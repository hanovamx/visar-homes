# -*- coding: utf-8 -*-
import base64
import logging
import math

import pytz
import requests
from lxml import etree
from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools import formatLang, html2plaintext
from odoo.tools.image import image_process

from ..hooks import FUMIGACION_NAME

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
# Fotos embebidas en el PDF: JPEG re-escalado. Tamaño/calidad contenidos para que un
# data-URI grande no corrompa el render de wkhtmltopdf (primera página en blanco).
_WS_REPORT_IMG_PX = 640
_WS_REPORT_IMG_QUALITY = 70
# Tope de fotos por galería en el PDF del cliente (evita documentos gigantes si
# una galería de evidencia trae decenas de fotos). Se muestran las más antiguas.
_WS_REPORT_GALLERY_MAX = 12


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
    visar_worksheet_saved_at = fields.Datetime(
        string="Hoja de trabajo guardada en", readonly=True,
        help="Primera vez que el técnico guardó la hoja de trabajo desde la app. "
             "Hasta entonces la app NO muestra la sección de firma ni el cierre.")
    visar_worksheet_saved_by_id = fields.Many2one(
        'hr.employee', string="Hoja de trabajo guardada por", readonly=True,
        help="Técnico que guardó la hoja de trabajo por primera vez.")
    visar_worksheet_last_saved_at = fields.Datetime(
        string="Hoja de trabajo — última guarda", readonly=True,
        help="ÚLTIMA vez que se guardó la hoja de trabajo (se actualiza en cada "
             "guardado). Con 'Llegada del técnico' define el tiempo en sitio.")
    visar_onsite_minutes = fields.Float(
        string="Tiempo en sitio (min)", readonly=True,
        compute='_compute_visar_onsite_minutes', store=True,
        help="Minutos desde 'Confirmar llegada' hasta la ÚLTIMA guarda de la hoja "
             "de trabajo. Es el tiempo que el técnico dedicó en el domicilio a "
             "ejecutar y documentar el servicio. Aparece en el PDF del reporte.")
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

    @api.depends('visar_arrived_at', 'visar_worksheet_last_saved_at')
    def _compute_visar_onsite_minutes(self):
        """Tiempo en sitio = 'Confirmar llegada' → última guarda de la hoja de
        trabajo. 0 mientras falte alguno de los dos sellos."""
        for task in self:
            start, end = task.visar_arrived_at, task.visar_worksheet_last_saved_at
            if start and end and end > start:
                task.visar_onsite_minutes = (end - start).total_seconds() / 60.0
            else:
                task.visar_onsite_minutes = 0.0

    @staticmethod
    def _visar_format_duration(minutes):
        """'1 h 23 min' / '45 min' / '—' (0 o None). Para el PDF y la app."""
        if not minutes or minutes <= 0:
            return "—"
        total = int(round(minutes))
        hours, mins = divmod(total, 60)
        if hours and mins:
            return "%d h %d min" % (hours, mins)
        if hours:
            return "%d h" % hours
        return "%d min" % mins

    def _visar_report_tz(self):
        """Huso para los sellos del reporte: el del técnico que documentó (guardó
        o cerró), luego el primer técnico, luego la compañía. Naive-UTC → local."""
        self.ensure_one()
        emp = (self.visar_worksheet_saved_by_id or self.visar_field_closed_by_id
               or self.visar_technician_ids[:1])
        tzname = (emp.tz or self.company_id.resource_calendar_id.tz
                  or self.env.company.resource_calendar_id.tz or 'UTC')
        try:
            return pytz.timezone(tzname)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _visar_onsite_report(self):
        """Datos del bloque 'Tiempo en sitio' del PDF, o None si no aplica.

        `duration` = llegada → última guarda de la hoja (el dato pedido). Se
        incluyen los dos extremos (en huso del técnico) para que sea auditable.
        """
        self.ensure_one()
        start, end = self.visar_arrived_at, self.visar_worksheet_last_saved_at
        if not (start and end):
            return None
        tz = self._visar_report_tz()

        def fmt(dt):
            return pytz.utc.localize(dt).astimezone(tz).strftime('%d/%m/%Y %H:%M')

        return {
            'arrived': fmt(start),
            'saved': fmt(end),
            'duration': self._visar_format_duration(self.visar_onsite_minutes),
        }

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

    def _visar_stage_pending_signature(self):
        """Etapa **Pendiente de firma** (archivada; ya no se usa en el flujo).

        Se conserva el lookup por xmlid solo para reconciliar tareas viejas que
        aún la tengan: la app las trata como 'en_ejecucion' y no borra sellos.
        El gate de firma usa `visar_worksheet_saved_at`, no esta etapa."""
        return self.env.ref(
            'visar_field_app.visar_stage_pending_signature', raise_if_not_found=False)

    def _visar_set_stage_pending_signature(self):
        """No-op. La etapa se retiró del flujo; la firma se habilita por sello."""
        self.ensure_one()
        return

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
        en Pendiente de firma / Completado como historial). Si gestión mueve la etapa
        a Programado / En camino / Incidencia, los sellos se limpian para que la app
        muestre la fase correcta (sin timer/¡Tiempo!/reagenda fantasma). El `write`
        resultante no toca `stage_id`, así que no reentra en el override."""
        self.ensure_one()
        s1 = self._visar_fsm_stage(1)  # En camino
        s2 = self._visar_fsm_stage(2)  # En ejecución
        s3 = self._visar_fsm_stage(3)  # Completado
        sign = self._visar_stage_pending_signature()
        stage = self.stage_id
        # 'Pendiente de firma' está archivada, pero si queda alguna tarea vieja
        # ahí hay que tratarla como servicio (si no, se borra `visar_service_start`
        # y la app oculta la hoja / firma).
        in_service = ((s2 and stage == s2) or (s3 and stage == s3)
                      or (sign and stage == sign))
        # "En ruta" abarca En camino + En ejecución + Completado: mientras la tarea
        # esté en (o haya pasado por) el trayecto, se conserva el sello de salida
        # para poder calcular el traslado al confirmar la llegada.
        on_the_way = (s1 and stage == s1) or in_service
        vals = {}
        if not in_service:
            # Antes de "Confirmar llegada" no hay llegada/espera/servicio. Se borra
            # también el sello de la hoja de trabajo: al reiniciar el flujo, la firma
            # vuelve a exigir que se guarde la hoja (el dato capturado NO se toca).
            vals.update({
                'visar_arrived_at': False,
                'visar_waiting_start': False,
                'visar_waiting_minutes': 0,
                'visar_service_start': False,
                'visar_client_wait_minutes': 0.0,
                'visar_worksheet_saved_at': False,
                'visar_worksheet_saved_by_id': False,
                'visar_worksheet_last_saved_at': False,
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
    # Traza de acciones del técnico (Llamar / WhatsApp / Google Maps)
    # ==================================================================
    # Qué botón se pulsó → (etiqueta para el chatter, emoji). El destino real
    # (teléfono / dirección) lo resuelve el servidor al registrar, para que la
    # nota diga a QUIÉN se llamó y no solo "pulsó Llamar".
    VISAR_TRACK_ACTIONS = {
        'call': ("Llamar", "📞"),
        'whatsapp': ("WhatsApp", "💬"),
        'maps': ("Abrir en Google Maps", "📍"),
    }

    def _visar_log_field_action(self, employee, action):
        """Deja en el chatter que el técnico pulsó Llamar / WhatsApp / Maps.

        Es una **nota interna** (`mail.mt_note`): traza para gestión, no se envía
        a nadie. Un toque = una nota; el JS ya descarta el doble-toque accidental.
        """
        self.ensure_one()
        label, icon = self.VISAR_TRACK_ACTIONS.get(action, (None, None))
        if not label:
            return False
        if action == 'maps':
            target = self.partner_id.contact_address_complete or ''
        else:
            target, _e164 = self._visar_client_phone()
        detail = (" → %s" % target) if target else ""
        self.message_post(
            body=Markup("%s <b>%s</b> pulsó <b>%s</b> desde la app de campo.%s") % (
                icon, employee.name or '', label, detail),
            subtype_xmlid='mail.mt_note')
        return True

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
        # Fumigación: maqueta dedicada, optimizada para la legibilidad del cliente
        # (Servicios agregados → Horario → Áreas tratadas → Evidencia). El resto de
        # plantillas siguen el recorrido genérico de la vista (de aquí para abajo).
        if template.sudo().name == FUMIGACION_NAME:
            return self._visar_fumigacion_report_sections(record)
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
        arriesgar un PDF roto.

        ⚠️ **Encoding (la causa del "reporte sin fotos"):** `image_process` trabaja
        con bytes **CRUDOS** en ambos extremos, pero el campo binary de Odoo
        (`record[name]`) llega en **base64** y la plantilla usa `image_data_uri`, que
        espera **base64**. Hay que **decodificar** antes y **re-codificar** después:
          base64 (campo) → b64decode → image_process (crudo→crudo) → b64encode → base64.
        Si se salta cualquiera de las dos, PIL o `image_data_uri` fallan y la imagen se
        descartaba en silencio (PIL) o rompía el render (`image_data_uri`).

        ⚠️ **Formato JPEG, no PNG.** Las fotos van EMBEBIDAS como data-URI en el HTML;
        un PNG grande (una captura/mapa comprime malísimo en PNG: ~270 KB aun a 900 px)
        **corrompe el render de wkhtmltopdf** — el síntoma real observado fue la PRIMERA
        página **en blanco** (el texto desaparece) aunque las fotos salieran en las
        siguientes. Recomprimir a **JPEG** baja ese caso a ~70-120 KB y estabiliza el
        documento. `WORKSHEET_REPORT_IMG_*` acota tamaño/calidad."""
        if not value:
            return False
        try:
            processed = image_process(
                base64.b64decode(value),
                size=(_WS_REPORT_IMG_PX, _WS_REPORT_IMG_PX),
                quality=_WS_REPORT_IMG_QUALITY, output_format='JPEG')
            return base64.b64encode(processed) if processed else False
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

    # ==================================================================
    # Reporte PDF para el CLIENTE — datos compartidos + maqueta Fumigación
    # ==================================================================
    # El cascarón del reporte (external_layout con el logo/datos de Visar, la ficha
    # del Cliente y la Firma) lo pinta la plantilla nativa; aquí se preparan los
    # bloques que faltaban o que Visar reordena para el cliente.

    def _visar_report_technicians(self):
        """[{'name', 'phone'}] de los técnicos que realizaron el servicio.

        Usa los técnicos ASIGNADOS como empleados (`visar_technician_ids`): el campo
        nativo `user_ids` —del que tira el bloque "Técnico" del reporte nativo— va
        vacío porque los técnicos de campo no tienen usuario interno. Cae a quien
        cerró en campo si no hubiera asignación."""
        self.ensure_one()
        employees = self.visar_technician_ids or self.visar_field_closed_by_id
        return [{
            'name': emp.name or '',
            'phone': emp.work_phone or emp.mobile_phone or '',
        } for emp in employees]

    def _visar_report_arrival_finish(self):
        """{'arrived', 'finished'} formateados en el huso del técnico, o None si no
        hay ninguno de los dos sellos.

        Son los tiempos que le importan al cliente: llegada al domicilio y cierre
        del servicio. NO se incluyen los registros internos (traslado, tiempo en
        sitio, timesheets) — esos se retiran del reporte del cliente."""
        self.ensure_one()
        start, end = self.visar_arrived_at, self.visar_field_closed_at
        if not (start or end):
            return None
        tz = self._visar_report_tz()

        def fmt(dt):
            if not dt:
                return "—"
            return pytz.utc.localize(dt).astimezone(tz).strftime('%d/%m/%Y %H:%M')

        return {'arrived': fmt(start), 'finished': fmt(end)}

    def _visar_report_services(self):
        """Datos de la tabla "Servicios agregados" (SOLO las líneas de ESTA tarea),
        o None si no hay.

        Las líneas de la orden se reparten por tarea (`sale.order.line.task_id`, que
        `visar_fsm` asigna a la línea de servicio y a sus add-ons), así que se filtra
        por la tarea para no listar los servicios de otras cuadrillas de la misma
        cita. Importes formateados en la moneda de la orden."""
        self.ensure_one()
        order = self.visar_sale_order_id
        if not order:
            return None
        lines = order.order_line.filtered(
            lambda sol: sol.task_id.id == self.id and not sol.display_type)
        if not lines:
            return None
        currency = order.currency_id
        rows, total = [], 0.0
        for line in lines:
            rows.append({
                'name': line.product_id.display_name or line.name or '',
                'qty': '%g' % line.product_uom_qty,
                'price': formatLang(self.env, line.price_unit, currency_obj=currency),
                'subtotal': formatLang(self.env, line.price_subtotal, currency_obj=currency),
            })
            total += line.price_subtotal
        return {'rows': rows, 'total': formatLang(self.env, total, currency_obj=currency)}

    def _visar_report_gallery(self, res_model, res_id, key, fallback=None):
        """Lista de imágenes (base64 JPEG, listas para embeber) de una galería de
        fotos — los adjuntos etiquetados con `visar_photo_key` de la App de Campo.

        Cae al binary del campo (`fallback`) si la galería está vacía, para no perder
        la foto representativa en datos antiguos. Cada foto se reescala/recomprime
        (mismo tratamiento que el resto del reporte) para no inflar el PDF."""
        self.ensure_one()
        atts = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', res_model),
            ('res_id', '=', res_id),
            ('res_field', '=', False),
            ('visar_photo_key', '=', key),
            ('mimetype', 'like', 'image/'),
        ], order='id asc', limit=_WS_REPORT_GALLERY_MAX)
        images = []
        for att in atts:
            img = self._visar_ws_report_image(att.datas)
            if img:
                images.append(img)
        if not images and fallback:
            img = self._visar_ws_report_image(fallback)
            if img:
                images.append(img)
        return images

    def _visar_report_evidence_section(self, record):
        """Sección "Evidencia" con subsecciones inicial / durante / final, o None si
        no hay ninguna foto.

        - Inicial  = galería `x_foto_inicial` (estado antes de iniciar).
        - Durante  = galería `x_foto_ejecucion` (tratamiento aplicándose).
        - Final    = fotos de evidencia POR ÁREA tratada (`x_foto_evidencia` de cada
                     línea): la plantilla de Fumigación no tiene una foto de estado
                     final propia, así que "evidencia final" se arma con la evidencia
                     capturada en cada área.
        Cada subsección se representa como un campo kind='gallery'; las vacías se
        omiten para no dejar títulos huérfanos en el PDF."""
        self.ensure_one()
        fields_out = []
        inicial = self._visar_report_gallery(
            record._name, record.id, 'x_foto_inicial', record.x_foto_inicial)
        if inicial:
            fields_out.append(
                {'kind': 'gallery', 'label': "Evidencia inicial", 'images': inicial})
        durante = self._visar_report_gallery(
            record._name, record.id, 'x_foto_ejecucion', record.x_foto_ejecucion)
        if durante:
            fields_out.append(
                {'kind': 'gallery', 'label': "Durante el tratamiento", 'images': durante})
        final = []
        for line in record.x_areas_tratadas:
            final += self._visar_report_gallery(
                line._name, line.id, 'x_foto_evidencia', line.x_foto_evidencia)
            if len(final) >= _WS_REPORT_GALLERY_MAX:
                break
        if final:
            fields_out.append({
                'kind': 'gallery', 'label': "Evidencia final (por área tratada)",
                'images': final[:_WS_REPORT_GALLERY_MAX],
            })
        if not fields_out:
            return None
        return {'title': "Evidencia", 'fields': fields_out}

    def _visar_report_plaguicidas_section(self, record):
        """PENDIENTE (Req 7): tabla de plaguicidas utilizados con una breve
        explicación de qué es cada uno. Requiere un modelo de plaguicidas (ficha
        con nombre, principio activo y descripción) que hoy no existe: en la hoja
        solo se captura el nombre por área (`x_plaguicida_nombre`), sin catálogo.
        Devuelve None hasta que se defina ese modelo."""
        return None

    def _visar_fumigacion_report_sections(self, record):
        """Secciones del reporte de Fumigación, en el orden pedido por el cliente:
        (3) Servicios agregados → (4) Horario del servicio → (5) Áreas tratadas →
        (6) Evidencia → (7) Plaguicidas [pendiente].

        Cliente (1), Técnico (2) y Firma (8) los pinta el cascarón compartido del
        reporte (fila superior y bloque de firma), no esta lista."""
        self.ensure_one()
        sections = []

        # (3) Servicios agregados — tabla de servicios prestados con importes.
        services = self._visar_report_services()
        if services:
            rows = [[
                {'kind': 'scalar', 'text': r['name']},
                {'kind': 'scalar', 'text': r['qty']},
                {'kind': 'scalar', 'text': r['price']},
                {'kind': 'scalar', 'text': r['subtotal']},
            ] for r in services['rows']]
            rows.append([
                {'kind': 'scalar', 'text': "Total"},
                {'kind': 'scalar', 'text': ''},
                {'kind': 'scalar', 'text': ''},
                {'kind': 'scalar', 'text': services['total']},
            ])
            sections.append({'title': "Servicios y productos agregados", 'fields': [{
                'kind': 'table', 'label': '',
                'columns': ["Servicio", "Cantidad", "Precio unitario", "Subtotal"],
                'rows': rows,
            }]})

        # (4) Horario del servicio — llegada y finalización (sin tiempos internos).
        times = self._visar_report_arrival_finish()
        if times:
            sections.append({'title': "Horario del servicio", 'fields': [
                {'kind': 'scalar', 'label': "Hora de llegada", 'text': times['arrived']},
                {'kind': 'scalar', 'label': "Hora de finalización", 'text': times['finished']},
            ]})

        # (5) Áreas tratadas — subficha one2many como tabla (columnas de la vista).
        info = self.env[record._name].sudo().fields_get(['x_areas_tratadas']).get('x_areas_tratadas')
        if info:
            table = self._visar_ws_table_descriptor(
                'x_areas_tratadas', info, '', '', record)
            if table:
                sections.append({'title': "Áreas tratadas", 'fields': [table]})

        # (6) Evidencia — galerías inicial / durante / final.
        evidence = self._visar_report_evidence_section(record)
        if evidence:
            sections.append(evidence)

        # (7) Plaguicidas utilizados — PENDIENTE (ver método stub).
        plaguicidas = self._visar_report_plaguicidas_section(record)
        if plaguicidas:
            sections.append(plaguicidas)

        return sections
