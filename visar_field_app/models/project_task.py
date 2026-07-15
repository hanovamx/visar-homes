# -*- coding: utf-8 -*-
import logging
import math

import requests
from lxml import etree
from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools import html2plaintext
from odoo.tools.image import image_process

_logger = logging.getLogger(__name__)

# Estados de tarea "cerrados": sus clientes no se geolocalizan.
_CLOSED_STATES = ('1_done', '1_canceled')

# --- ETA de traslado (Mapbox Directions) ---
# Mismo token que geocodificación/mapa (web_map.token_map_box). El perfil
# 'driving-traffic' factoriza tráfico típico. Coordenadas en orden lon,lat.
_MAPBOX_TOKEN_PARAM = 'web_map.token_map_box'
_MAPBOX_DIRECTIONS_URL = (
    'https://api.mapbox.com/directions/v5/mapbox/driving-traffic/'
    '%(lon1)s,%(lat1)s;%(lon2)s,%(lat2)s')
# ETA fija (min) cuando no hay coords del técnico / destino sin geocodificar / falla Mapbox.
_ENROUTE_ETA_PARAM = 'visar_field.enroute_eta_minutes'
_ENROUTE_ETA_DEFAULT = 30

# Reglas para leer la worksheet dinámica al construir el reporte PDF (espejo de
# las del controlador de la app, pero orientadas a MOSTRAR, no a editar):
#   - campos técnicos que nunca se muestran (enlace a la tarea, nombre interno);
#   - widgets que no son captura visible (barra de etapas, firma nativa —esta
#     última ya la pinta la sección "Signature" del reporte);
#   - nombres redundantes con la firma.
_WS_OMIT_NAMES = {'x_project_task_id', 'x_name'}
_WS_SKIP_WIDGETS = ('statusbar', 'signature')
_WS_SKIP_NAME_HINTS = ('nombre_de_quien_firma',)


class ProjectTask(models.Model):
    _inherit = 'project.task'

    # Nota: `visar_technician_ids` (técnicos asignados) ahora vive en visar_fsm,
    # porque la asignación es responsabilidad de FSM y la usa el Gantt de técnicos.
    # Esta app solo lo consume (lista de servicios del técnico).

    # --- Atribución del cierre en campo ---
    # La captura (worksheet, firma) usa los modelos/campos NATIVOS para que los
    # reportes nativos funcionen. Aquí solo guardamos QUIÉN cerró (empleado), que
    # el flujo nativo no registra y Visar necesita para comisiones de upsell.
    visar_field_closed_by_id = fields.Many2one(
        'hr.employee', string="Cerrado por (técnico)", readonly=True,
        help="Técnico que cerró el servicio desde la app de campo. "
             "Base para comisiones de upsell y auditoría.")
    visar_field_closed_at = fields.Datetime(
        string="Cerrado en campo", readonly=True)

    # --- Flujo en sitio (Req 2) ---
    # El ESTADO visible usa las etapas nativas de FSM (`stage_id`), no un campo
    # propio. Aquí solo se sellan los momentos de las sub-fases que NO tienen etapa
    # nativa (llegada, espera del cliente, inicio del servicio) para calcular
    # tiempos y disparar el cronómetro de espera.
    visar_enroute_at = fields.Datetime(
        string="Salida del técnico (en camino)", readonly=True,
        help="Momento en que el técnico pulsó 'Voy en camino' en la app. Junto con "
             "'Llegada del técnico' permite calcular el tiempo de traslado.")
    visar_arrived_at = fields.Datetime(
        string="Llegada del técnico", readonly=True,
        help="Momento en que el técnico pulsó 'Confirmar llegada' en la app.")
    visar_travel_minutes = fields.Float(
        string="Traslado del técnico (min)", readonly=True,
        compute='_compute_visar_travel_minutes', store=True,
        help="Minutos transcurridos desde 'Voy en camino' hasta 'Confirmar llegada' "
             "(cuánto tardó el técnico en llegar). 0 si falta alguno de los dos sellos.")
    visar_enroute_eta_minutes = fields.Integer(
        string="ETA de llegada estimada (min)", readonly=True,
        help="Minutos estimados de traslado al pulsar 'Voy en camino', comunicados "
             "al cliente. Mapbox (con la ubicación del técnico) o el valor fijo "
             "configurable si no hay ubicación. Es una estimación al momento de salir.")
    visar_waiting_start = fields.Datetime(
        string="Inicio de espera al cliente", readonly=True,
        help="Momento en que el técnico pulsó 'Esperar al cliente'. Dispara la "
             "cuenta regresiva en la app.")
    visar_waiting_minutes = fields.Integer(
        string="Minutos de espera al cliente", readonly=True,
        help="Duración (min) que el técnico eligió para la cuenta regresiva de "
             "espera. 0 = usar el valor por defecto (parámetro visar_field.waiting_minutes).")
    visar_service_start = fields.Datetime(
        string="Inicio del servicio", readonly=True,
        help="Momento en que el técnico pulsó 'Comenzar servicio'. Al cerrar se "
             "registra el tiempo trabajado como parte de horas (timesheet).")
    visar_client_wait_minutes = fields.Float(
        string="Espera al cliente (min)", readonly=True,
        help="Minutos que transcurrieron desde 'Esperar al cliente' hasta "
             "'Comenzar servicio' (cuánto se esperó a que el cliente abriera). "
             "0 si el técnico no inició el temporizador de espera.")
    visar_reschedule_requested_by_id = fields.Many2one(
        'hr.employee', string="Reagenda solicitada por", readonly=True,
        help="Técnico que marcó 'Cliente no llegó' (solicitud de reagenda).")
    visar_reschedule_requested_at = fields.Datetime(
        string="Reagenda solicitada en", readonly=True)

    # ==================================================================
    # Flujo en sitio: etapas nativas + timesheet + reagenda (Req 2)
    # ==================================================================
    @api.depends('visar_enroute_at', 'visar_arrived_at')
    def _compute_visar_travel_minutes(self):
        """Tiempo de traslado = 'Voy en camino' → 'Confirmar llegada'. Almacenado
        para poder agrupar/reportar por él. 0 mientras falte alguno de los sellos."""
        for task in self:
            if task.visar_enroute_at and task.visar_arrived_at:
                delta = task.visar_arrived_at - task.visar_enroute_at
                task.visar_travel_minutes = max(delta.total_seconds() / 60.0, 0.0)
            else:
                task.visar_travel_minutes = 0.0

    def _visar_fsm_stage(self, n):
        """Etapa nativa de Field Service por su xmlid estable (portable, sin ids
        cableados). n ∈ {0..4}: 0 Programado, 1 En camino, 2 En ejecución,
        3 Completado, 4 Incidencia—Reprogramar."""
        return self.env.ref(
            'industry_fsm.planning_project_stage_%s' % n, raise_if_not_found=False)

    def _visar_set_stage(self, n):
        """Mueve la tarea a la etapa nativa n (si existe)."""
        self.ensure_one()
        stage = self._visar_fsm_stage(n)
        if stage:
            self.stage_id = stage.id

    def write(self, vals):
        """Al cambiar de etapa (desde la app O desde el backend "Servicio externo"),
        reconcilia los sellos de sub-fase para que la app muestre los botones que
        corresponden a la etapa. Sin esto, mover la etapa a mano en Odoo dejaba
        sellos obsoletos (p. ej. `visar_service_start`) que "ganaban" y congelaban
        la app en una fase vieja (timer/¡Tiempo!/reagenda fantasma)."""
        changed = self.browse()
        if 'stage_id' in vals:
            changed = self.filtered(lambda t: t.stage_id.id != vals['stage_id'])
        res = super().write(vals)
        for task in changed:
            task._visar_reconcile_flow_markers()
        return res

    def _visar_reconcile_flow_markers(self):
        """Limpia los sellos de sub-fase cuando la etapa deja de ser "de servicio".

        Desde que 'Confirmar llegada' salta directo a **En ejecución**, todas las
        sub-fases (llegada → espera → servicio) viven en esa etapa (y se conservan
        en Completado como historial). Si gestión mueve la etapa a Programado / En
        camino / Incidencia, los sellos se limpian para que la app muestre la fase
        correcta (sin timer/¡Tiempo!/reagenda fantasma). El `write` resultante no
        toca `stage_id`, así que no reentra en el override."""
        self.ensure_one()
        s1 = self._visar_fsm_stage(1)  # En camino
        s2 = self._visar_fsm_stage(2)  # En ejecución
        s3 = self._visar_fsm_stage(3)  # Completado
        stage = self.stage_id
        in_service = (s2 and stage == s2) or (s3 and stage == s3)
        # "En ruta" abarca En camino + En ejecución + Completado: mientras la tarea
        # esté en (o haya pasado por) el trayecto, se conserva el sello de salida
        # para poder calcular el traslado al confirmar la llegada.
        on_the_way = (s1 and stage == s1) or in_service
        vals = {}
        if not in_service:
            # Antes de "Confirmar llegada" no hay llegada/espera/servicio.
            vals.update({
                'visar_arrived_at': False,
                'visar_waiting_start': False,
                'visar_waiting_minutes': 0,
                'visar_service_start': False,
                'visar_client_wait_minutes': 0.0,
            })
        if not on_the_way:
            # De vuelta a Programado / Incidencia: se cancela también la salida
            # (el traslado se recompute a 0 al limpiarse el sello) y la ETA.
            vals['visar_enroute_at'] = False
            vals['visar_enroute_eta_minutes'] = 0
        if vals:
            self.write(vals)

    def _visar_write_service_timesheet(self, employee):
        """Registra el tiempo trabajado como línea de timesheet NATIVA (oculta al
        técnico), atribuida a su empleado. Reutiliza `account.analytic.line` (lo
        mismo que produce el cronómetro nativo) sin usar el widget ligado a usuario.

        No hace nada si no hubo 'Comenzar servicio' o el proyecto no lleva horas.
        """
        self.ensure_one()
        if not self.visar_service_start or not self.project_id.allow_timesheets:
            return
        delta = fields.Datetime.now() - self.visar_service_start
        hours = max(delta.total_seconds() / 3600.0, 0.0)
        if not hours:
            return
        self.env['account.analytic.line'].sudo().create({
            'task_id': self.id,
            'project_id': self.project_id.id,
            'date': fields.Date.context_today(self),
            'name': "Servicio en campo (app técnicos)",
            'unit_amount': hours,
            'employee_id': employee.id,
        })

    def _visar_reschedule_assignee(self):
        """Usuario al que se asigna la actividad de reagenda. Los técnicos no tienen
        usuario, así que `user_ids` suele estar vacío: se cae al vendedor de la orden
        y luego al responsable del proyecto."""
        self.ensure_one()
        return (self.user_ids[:1]
                or self.visar_sale_order_id.user_id
                or self.project_id.user_id)

    def _visar_flag_reschedule(self, employee):
        """Marca 'Cliente no llegó': etapa Incidencia—Reprogramar + cancelación,
        actividad para gestión (si hay a quién) y SIEMPRE una nota en el chatter.
        No reagenda el calendario (eso lo hace gestión en el backend)."""
        self.ensure_one()
        self.visar_reschedule_requested_by_id = employee.id
        self.visar_reschedule_requested_at = fields.Datetime.now()
        self._visar_set_stage(4)
        self.state = '1_canceled'
        body = ("Reagenda solicitada desde la app de campo por <b>%s</b>: el cliente "
                "no atendió tras la espera." % (employee.name or ''))
        assignee = self._visar_reschedule_assignee()
        if assignee:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=assignee.id,
                summary="Reagendar servicio — cliente no llegó",
                note=body)
        self.message_post(body=body)

    # ==================================================================
    # Aviso al cliente (hoy: simulación en chatter; futuro: WhatsApp)
    # ==================================================================
    def _visar_client_phone(self):
        """Número del cliente al que se enviaría el aviso. El contacto de servicio
        (entrega) suele no tener teléfono → se cae al cliente comercial. Devuelve
        (display, e164) o ('', '') si no hay."""
        self.ensure_one()
        partner = self.partner_id
        if not partner:
            return '', ''
        source = partner if partner.phone else partner.commercial_partner_id
        phone = source.phone or ''
        if not phone:
            return '', ''
        e164 = ''.join(ch for ch in (source.phone_sanitized or phone) if ch.isdigit())
        return phone, e164

    def _visar_notify_client(self, body, event=''):
        """Único punto de envío de avisos al cliente.

        **Hoy es una SIMULACIÓN:** deja una nota interna en el chatter etiquetada con
        el número destino, para tener la lógica y el registro. **Cuando se conecte
        WhatsApp, solo cambia este método** (crear `whatsapp.message` desde una
        `whatsapp.template` aprobada); los disparadores y los textos no se tocan.
        """
        self.ensure_one()
        display, _e164 = self._visar_client_phone()
        target = ("→ %s" % display) if display else "— sin número en el contacto"
        label = "📱 <b>[Simulación WhatsApp %s]</b>" % target
        self.message_post(
            body=Markup("%s<br/>%s") % (Markup(label), body),
            subtype_xmlid='mail.mt_note')

    @staticmethod
    def _visar_msg_enroute(eta_minutes, employee=None):
        """Texto (cliente) del aviso 'técnico en camino'."""
        tech = (" %s" % employee.name) if employee and employee.name else ""
        return ("Hola, le saluda Visar. Su técnico%s va en camino y llegará en "
                "aproximadamente %s minutos. Por favor esté disponible para "
                "recibirlo." % (tech, eta_minutes))

    @staticmethod
    def _visar_msg_arrived(waiting_minutes, employee=None):
        """Texto (cliente) del aviso 'técnico llegó' + ventana de espera."""
        tech = (" %s" % employee.name) if employee and employee.name else ""
        return ("Su técnico%s ya llegó a su domicilio. Cuenta con %s minutos para "
                "recibirlo; de lo contrario la cita se cancelará y deberá "
                "reagendarse." % (tech, waiting_minutes))

    def _visar_enroute_eta_minutes(self, tech_lat=None, tech_lng=None):
        """Minutos estimados de traslado del técnico al domicilio de servicio.

        1. **Mapbox Directions** (perfil driving-traffic) si hay coordenadas del
           técnico, el destino está geocodificado y hay token.
        2. **Fallback fijo** (`visar_field.enroute_eta_minutes`, def 30) en cualquier
           otro caso (sin ubicación, destino sin geocodificar o falla la API).
        """
        self.ensure_one()
        fixed = self._visar_default_enroute_eta()
        try:
            tech_lat = float(tech_lat)
            tech_lng = float(tech_lng)
        except (TypeError, ValueError):
            return fixed
        dest = self.partner_id
        if not dest or not dest.partner_latitude or not dest.partner_longitude:
            return fixed
        token = self.env['ir.config_parameter'].sudo().get_param(_MAPBOX_TOKEN_PARAM)
        if not token:
            return fixed
        url = _MAPBOX_DIRECTIONS_URL % {
            'lon1': tech_lng, 'lat1': tech_lat,
            'lon2': dest.partner_longitude, 'lat2': dest.partner_latitude,
        }
        try:
            resp = requests.get(
                url,
                params={'access_token': token, 'overview': 'false'},
                timeout=10)
            resp.raise_for_status()
            routes = resp.json().get('routes') or []
        except Exception as err:  # noqa: BLE001 - red/API: degradar al valor fijo
            _logger.warning("Mapbox Directions falló para tarea %s: %s", self.id, err)
            return fixed
        if not routes:
            return fixed
        seconds = routes[0].get('duration')
        if not seconds:
            return fixed
        return max(int(math.ceil(seconds / 60.0)), 1)

    def _visar_default_enroute_eta(self):
        """ETA fija de traslado (parámetro global, 30 si no está o es inválido)."""
        raw = self.env['ir.config_parameter'].sudo().get_param(
            _ENROUTE_ETA_PARAM, _ENROUTE_ETA_DEFAULT)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return _ENROUTE_ETA_DEFAULT

    def _visar_geolocalize_service_partners(self, force=False):
        """Geolocaliza (lat/long) la **dirección de servicio** de los servicios
        abiertos, para que aparezcan en el mapa de la app de campo.

        La dirección de servicio es `task.partner_id` (contacto de entrega/obra,
        distinto del cliente de facturación). Los técnicos no pueden geolocalizar
        (no tienen usuario); esto lo dispara gestión desde el backend. Usa
        `res.partner._visar_geo_localize()` (consulta enriquecida con colonia +
        estado, con fallback al centroide de CP). Proveedor por defecto:
        OpenStreetMap, sin API key.

        Con `force=False` solo procesa los que no tienen coordenadas; con
        `force=True` re-geolocaliza todos (útil tras mejorar la consulta).
        Devuelve una notificación con cuántas direcciones resolvieron a nivel
        calle vs. solo al centroide.
        """
        tasks = self.search([('state', 'not in', list(_CLOSED_STATES))])
        partners = tasks.partner_id
        if not force:
            partners = partners.filtered(
                lambda p: not (p.partner_latitude and p.partner_longitude))
        exact = approx = failed = 0
        for partner in partners:
            try:
                kind = partner.with_context(force_geo_localize=True)._visar_geo_localize()
            except Exception as err:  # noqa: BLE001 - red/proveedor: no abortar el lote
                _logger.warning(
                    "Geolocalización fallida para el contacto %s: %s", partner.id, err)
                kind = False
            if kind == 'exact':
                exact += 1
            elif kind == 'approx':
                approx += 1
            else:
                failed += 1
        message = (
            "%d dirección(es) a nivel calle, %d solo aproximada(s) (centroide), "
            "%d sin resolver. Total: %d." % (exact, approx, failed, len(partners)))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Geolocalización de direcciones de servicio",
                'message': message,
                'type': 'success' if (exact or approx) else 'warning',
                'sticky': False,
            },
        }

    # ==================================================================
    # Reporte PDF — lectura "para mostrar" de la worksheet dinámica
    # ==================================================================
    # El reporte nativo (industry_fsm.worksheet_custom) pinta las respuestas con
    # una vista QWeb AUTOGENERADA (worksheet_template_id.report_view_id): sin
    # títulos de sección, con `page-break-inside: avoid` en cada fila (→ páginas
    # en blanco) y las subfichas o2m como una vista <form>/<list> cruda. Aquí
    # construimos una estructura limpia (secciones = pestañas del notebook, campos
    # con etiqueta + valor formateado) que la plantilla Visar dibuja con estilo.

    def _visar_worksheet_report_sections(self):
        """Estructura lista-para-mostrar de la worksheet de ESTA tarea.

        Devuelve `[{'title': str, 'fields': [descriptor, ...]}, ...]` o `[]` si
        la tarea no tiene worksheet o su vista es ilegible (en cuyo caso la
        plantilla cae al render nativo). Cada descriptor lleva `kind` ∈
        {scalar, bool, image, html, table} + los datos ya formateados.
        Los campos escalares SIN valor se omiten (evita etiquetas huérfanas, una
        de las causas del "no hay texto" en el PDF actual); los booleanos siempre
        se muestran (Sí/No es información).
        """
        self.ensure_one()
        template = self.worksheet_template_id
        if not template or not template.sudo().model_id:
            return []
        model_name = template.sudo().model_id.model
        if model_name not in self.env:
            return []
        Model = self.env[model_name].sudo()
        record = Model.search(
            [('x_project_task_id', '=', self.id)], limit=1, order='create_date desc')
        if not record:
            return []
        try:
            arch = etree.fromstring(Model.get_view(view_type='form')['arch'])
        except Exception:  # noqa: BLE001 - vista dinámica ilegible → fallback nativo
            _logger.warning("Worksheet form view ilegible para %s", model_name)
            return []
        meta = Model.fields_get()

        sections = []
        default_section = {'title': '', 'fields': []}
        sections.append(default_section)

        def walk(node, section):
            if node.tag == 'header' or self._visar_ws_node_hidden(node):
                return
            if node.tag == 'page':
                section = {'title': (node.get('string') or '').strip(), 'fields': []}
                sections.append(section)
                for child in node:
                    walk(child, section)
                return
            if node.tag == 'field':
                desc = self._visar_ws_field_descriptor(node, meta, record)
                if desc:
                    section['fields'].append(desc)
                return
            for child in node:
                walk(child, section)

        walk(arch, default_section)
        return [s for s in sections if s['fields']]

    @staticmethod
    def _visar_ws_node_hidden(node):
        """True si el nodo está oculto por un `invisible`/`column_invisible`
        constante. Las expresiones dinámicas se tratan como visibles (igual que
        el formulario nativo)."""
        const = ('1', 'True', 'true')
        return node.get('invisible') in const or node.get('column_invisible') in const

    def _visar_ws_field_descriptor(self, node, meta, record):
        """Descriptor de UN campo del formulario para el reporte, o None si se
        omite (técnico, oculto, o escalar sin valor)."""
        name = node.get('name')
        if (not name or name in _WS_OMIT_NAMES
                or node.get('widget') in _WS_SKIP_WIDGETS
                or any(h in name for h in _WS_SKIP_NAME_HINTS)):
            return None
        info = meta.get(name)
        if not info:
            return None
        ftype = info['type']
        label = info.get('string') or name
        help_text = (node.get('help') or '').strip()
        if ftype == 'one2many':
            return self._visar_ws_table_descriptor(name, info, label, help_text, record)
        value = record[name]
        base = {'label': label, 'help': help_text}
        if ftype == 'boolean':
            base.update(kind='bool', bool=bool(value))
            return base
        if ftype == 'binary':
            image = self._visar_ws_report_image(value)
            if not image:
                return None
            base.update(kind='image', image=image)
            return base
        if ftype == 'html':
            if not (value and html2plaintext(value).strip()):
                return None
            base.update(kind='html', html=Markup(value))
            return base
        text = self._visar_ws_format_scalar(ftype, info, value)
        if not text:
            return None
        base.update(kind='scalar', text=text)
        return base

    @staticmethod
    def _visar_ws_report_image(value):
        """Miniatura (base64) de una foto para EMBEBER en el PDF.

        Las fotos de campo llegan a resolución completa (varios MB). Embeberlas
        crudas en el HTML del reporte hincha el documento a megabytes y hace que
        wkhtmltopdf **falle de forma intermitente** (páginas en blanco / texto que
        desaparece — el síntoma "a veces no hay texto"). Se reescalan a un máximo
        de 900px y se recomprime, reduciendo el peso ~10-50× y estabilizando el
        render. Si el procesado falla (dato corrupto), se omite la imagen antes que
        arriesgar un PDF roto."""
        if not value:
            return False
        try:
            return image_process(value, size=(900, 900), quality=80)
        except Exception:  # noqa: BLE001 - imagen ilegible: mejor omitir que romper
            return False

    @staticmethod
    def _visar_ws_format_scalar(ftype, info, value):
        """Valor mostrable (str) de un campo escalar/relacional simple, o '' si
        está vacío."""
        if value in (False, None, ''):
            return ''
        if ftype == 'selection':
            return dict(info.get('selection') or []).get(value, value) or ''
        if ftype == 'many2one':
            return value.display_name or ''
        if ftype == 'many2many':
            return ', '.join(value.mapped('display_name'))
        if ftype in ('float', 'monetary'):
            return ('%g' % value) if value else ''
        return str(value)

    def _visar_ws_table_descriptor(self, name, info, label, help_text, record):
        """Descriptor de una subficha one2many como TABLA (columnas = campos de
        línea visibles en la vista lista; filas = líneas). None si no hay líneas
        (no se pinta una tabla vacía)."""
        relation = info.get('relation')
        if not relation or relation not in self.env:
            return None
        lines = record[name]
        if not lines:
            return None
        LineModel = self.env[relation].sudo()
        line_meta = LineModel.fields_get()
        # El many2one de la línea que apunta de vuelta a la worksheet no es dato.
        back_fk = next((n for n, i in line_meta.items()
                        if i.get('type') == 'many2one'
                        and i.get('relation') == record._name), None)

        # Columnas: los <field> de la sublista, en orden, saltando ocultos/técnicos.
        columns = []
        seen = set()
        sub_arch = self._visar_ws_line_list_arch(record, name)
        if sub_arch is not None:
            for fnode in sub_arch.iter('field'):
                cn = fnode.get('name')
                if (not cn or cn in seen or cn == back_fk or cn in _WS_OMIT_NAMES
                        or self._visar_ws_node_hidden(fnode)
                        or cn.endswith('_sequence')):
                    continue
                ci = line_meta.get(cn)
                if not ci or ci['type'] == 'one2many':
                    continue
                seen.add(cn)
                columns.append((cn, ci.get('string') or cn, ci['type'], ci))
        if not columns:
            return None

        rows = []
        for line in lines:
            cells = []
            for (cn, _lbl, ctype, ci) in columns:
                cells.append(self._visar_ws_cell(ctype, ci, line[cn]))
            rows.append(cells)
        return {
            'label': label, 'help': help_text, 'kind': 'table',
            'columns': [lbl for (_n, lbl, _t, _i) in columns],
            'rows': rows,
        }

    def _visar_ws_line_list_arch(self, record, o2m_name):
        """Nodo <list>/<tree> (o <form>) de la subficha `o2m_name` dentro de la
        vista formulario de la worksheet, para saber qué columnas mostrar."""
        Model = self.env[record._name].sudo()
        try:
            arch = etree.fromstring(Model.get_view(view_type='form')['arch'])
        except Exception:  # noqa: BLE001
            return None
        node = next((f for f in arch.iter('field') if f.get('name') == o2m_name), None)
        if node is None:
            return None
        for tag in ('list', 'tree', 'form'):
            sub = node.find(tag)
            if sub is not None:
                return sub
        return None

    def _visar_ws_cell(self, ftype, info, value):
        """Celda de tabla o2m: dict con `kind` (image/bool/scalar) + dato."""
        if ftype == 'boolean':
            return {'kind': 'bool', 'bool': bool(value)}
        if ftype == 'binary':
            return {'kind': 'image', 'image': self._visar_ws_report_image(value)}
        if ftype == 'html':
            return {'kind': 'scalar', 'text': html2plaintext(value) if value else ''}
        return {'kind': 'scalar', 'text': self._visar_ws_format_scalar(ftype, info, value)}
