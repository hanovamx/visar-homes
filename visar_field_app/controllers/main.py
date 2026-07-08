# -*- coding: utf-8 -*-
import base64
import json
import logging
from urllib.parse import urlencode

from lxml import etree

from odoo import fields, http
from odoo.http import request
from odoo.tools import html2plaintext, plaintext2html

_logger = logging.getLogger(__name__)

# Claves de sesión HTTP que identifican al técnico en el dispositivo.
SESSION_EMPLOYEE = 'visar_field_employee_id'
SESSION_SHIFT = 'visar_field_session_id'

# Estados de tarea considerados "cerrados" (no se muestran como pendientes).
CLOSED_STATES = ('1_done', '1_canceled')

# Campo de enlace de la worksheet dinámica hacia la tarea (res_model = project.task).
WORKSHEET_LINK = 'x_project_task_id'
# Campos de la worksheet que NO se editan en el formulario (igual que el reporte nativo).
WORKSHEET_OMIT = {WORKSHEET_LINK, 'x_name'}
# Tipos relacionales/complejos que el formulario de campo v1 no edita.
WORKSHEET_SKIP_TYPES = ('one2many', 'many2many')
# Widgets cuyos campos NO se renderizan en la app de campo:
#   - statusbar: barra de etapas de Studio (no es dato de captura, confunde al técnico).
#   - signature: la firma se captura en la sección nativa "Firma del cliente" (canvas).
WORKSHEET_SKIP_WIDGETS = ('statusbar', 'signature')
# Subcadenas de nombres de campo que se omiten por ser redundantes con la sección
# nativa de firma (nombre de quien firma). La firma en sí ya se salta por widget.
WORKSHEET_SKIP_NAME_HINTS = ('nombre_de_quien_firma',)
# Tipos de campo de LÍNEA (subficha one2many) que la app no captura en tarjetas.
# Binary (foto por tarjeta) y many2many (grupo de casillas) SÍ se soportan.
LINE_SKIP_TYPES = ('one2many',)
# Prefijos de los inputs de las tarjetas one2many (una sola submission).
#   o2mline~{campo_o2m}~{fila}~{campo_linea} = valor
#   o2mline~{campo_o2m}~{fila}~id            = id de la línea existente (o vacío)
#   o2mpresent~{campo_o2m}                   = marcador de que la subficha se renderizó
O2M_LINE_PREFIX = 'o2mline'
O2M_PRESENT_PREFIX = 'o2mpresent'
O2M_SEP = '~'
# Reporte nativo de la worksheet FSM.
FSM_REPORT = 'industry_fsm.worksheet_custom'


class VisarFieldApp(http.Controller):
    """App de campo para técnicos (patrón POS): un dispositivo, identificación por PIN,
    sin usuario interno. Escribe en la worksheet NATIVA y campos de firma nativos para
    que los reportes nativos de Field Service funcionen sin cambios.

    Todas las rutas son públicas y operan en sudo, pero acotadas estrictamente al
    empleado identificado en la sesión del dispositivo.
    """

    # ==================================================================
    # Helpers de sesión / técnico
    # ==================================================================
    def _current_employee(self):
        emp_id = request.session.get(SESSION_EMPLOYEE)
        if not emp_id:
            return request.env['hr.employee'].sudo().browse()
        return request.env['hr.employee'].sudo().browse(emp_id).exists()

    def _employee_tasks(self, employee, include_closed=False):
        domain = [('visar_technician_ids', 'in', employee.ids)]
        if not include_closed:
            domain.append(('state', 'not in', list(CLOSED_STATES)))
        return request.env['project.task'].sudo().search(
            domain, order='planned_date_begin asc, priority desc, id desc')

    def _task_for_employee(self, task_id, employee):
        task = request.env['project.task'].sudo().browse(int(task_id)).exists()
        if task and employee and employee in task.visar_technician_ids:
            return task
        return request.env['project.task'].sudo().browse()

    # ==================================================================
    # Ubicación / mapa
    # ==================================================================
    @staticmethod
    def _task_address(task):
        """Dirección legible del cliente del servicio (o cadena vacía)."""
        partner = task.partner_id
        if not partner:
            return ''
        return partner.contact_address_complete or partner.name or ''

    def _google_maps_url(self, task):
        """URL de búsqueda en Google Maps con la dirección del servicio.

        Usa el endpoint universal (`api=1`); en móvil abre la app de Maps.
        Devuelve '' si no hay dirección que buscar.
        """
        address = self._task_address(task)
        if not address:
            return ''
        return 'https://www.google.com/maps/search/?' + urlencode(
            {'api': 1, 'query': address})

    def _task_map_payload(self, tasks):
        """Lista serializable (JSON) de servicios para plotear en el mapa.

        Las coordenadas viven en el cliente (`partner_id.partner_latitude/longitude`,
        campos base poblados por base_geolocalize). Se consideran "sin ubicación"
        las coordenadas nulas o 0.0/0.0 (valor por defecto sin geocodificar).
        """
        payload = []
        for task in tasks:
            partner = task.partner_id
            lat = partner.partner_latitude if partner else 0.0
            lng = partner.partner_longitude if partner else 0.0
            has_coords = bool(partner) and bool(lat) and bool(lng)
            payload.append({
                'id': task.id,
                'name': task.name or '',
                'client': partner.name if partner else '',
                'address': self._task_address(task),
                'url': '/visar/field/task/%s' % task.id,
                'lat': lat,
                'lng': lng,
                'has_coords': has_coords,
            })
        return payload

    # ==================================================================
    # Contacto del cliente / flujo en sitio (Req 2)
    # ==================================================================
    @staticmethod
    def _task_contact(task):
        """Datos de contacto para llamar/WhatsApp. En Odoo 19 res.partner solo tiene
        `phone` (no `mobile`). El contacto de servicio (entrega) suele NO tener
        teléfono; vive en el cliente (`commercial_partner_id`). Devuelve {} si no hay.
        """
        partner = task.partner_id
        if not partner:
            return {}
        source = partner if partner.phone else partner.commercial_partner_id
        phone = source.phone or ''
        if not phone:
            return {}
        # wa.me exige solo dígitos con lada internacional; `phone_sanitized` viene en
        # formato E.164 (+52…) → se quitan símbolos. Si falta, se usan los dígitos crudos.
        intl = ''.join(ch for ch in (source.phone_sanitized or phone) if ch.isdigit())
        return {
            'phone_display': phone,
            'tel_href': 'tel:' + ''.join(ch for ch in phone if ch.isdigit() or ch == '+'),
            'wa_href': ('https://wa.me/' + intl) if intl else '',
        }

    @staticmethod
    def _default_waiting_minutes():
        """Minutos de espera por defecto (parámetro global, 10 si no está)."""
        raw = request.env['ir.config_parameter'].sudo().get_param(
            'visar_field.waiting_minutes', 10)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return 10

    def _coerce_waiting_minutes(self, raw):
        """Coacciona los minutos elegidos por el técnico; si no es válido o < 1,
        usa el valor por defecto."""
        try:
            val = int(float(raw))
        except (TypeError, ValueError):
            return self._default_waiting_minutes()
        return val if val >= 1 else self._default_waiting_minutes()

    def _task_flow_state(self, task):
        """Sub-fase del flujo en sitio para elegir qué botón mostrar.

        La **etapa nativa `stage_id` manda**: si gestión la cambia en el backend, la
        app refleja ese cambio. Los sellos de tiempo solo REFINAN dentro de "En
        camino" (llegada/espera no tienen etapa propia); el `write` de project.task
        los deja consistentes con la etapa, así que aquí no pueden "ganarle".

        Devuelve: 'programado' | 'en_camino' | 'llego' | 'esperando' |
                  'en_ejecucion' | 'cerrado' | 'reagenda'.
        """
        stage = task.stage_id
        s1 = task._visar_fsm_stage(1)  # En camino
        s2 = task._visar_fsm_stage(2)  # En ejecución
        s3 = task._visar_fsm_stage(3)  # Completado
        s4 = task._visar_fsm_stage(4)  # Incidencia—Reprogramar
        if s3 and stage == s3:
            return 'cerrado'
        if s4 and stage == s4:
            return 'reagenda'
        if s2 and stage == s2:
            # 'Confirmar llegada' ya movió la etapa a En ejecución; las sub-fases
            # (llegada → espera → servicio) se distinguen por los sellos de tiempo.
            if task.visar_service_start:
                return 'en_ejecucion'
            if task.visar_waiting_start:
                return 'esperando'
            if task.visar_arrived_at:
                return 'llego'
            return 'en_ejecucion'  # etapa puesta a mano sin sellos: servicio en curso
        if s1 and stage == s1:
            return 'en_camino'
        return 'programado'  # Programado o cualquier otra etapa

    # ==================================================================
    # Worksheet nativa (modelo dinámico x_...)
    # ==================================================================
    def _worksheet_model(self, task):
        """Recordset sudo del modelo dinámico de la worksheet de la tarea, o None."""
        template = task.worksheet_template_id
        if not template or not template.sudo().model_id:
            return None
        model_name = template.sudo().model_id.model
        if model_name not in request.env:
            return None
        return request.env[model_name].sudo()

    def _worksheet_record(self, task, create=False):
        """Registro worksheet de la tarea (uno por tarea); lo crea si hace falta."""
        Model = self._worksheet_model(task)
        if Model is None:
            return None
        record = Model.search(
            [(WORKSHEET_LINK, '=', task.id)], limit=1, order='create_date desc')
        if not record and create:
            record = Model.create({WORKSHEET_LINK: task.id})
        return record

    # ==================================================================
    # Galerías de fotos por campo (adjuntos etiquetados con visar_photo_key)
    # ==================================================================
    def _field_photo_atts(self, res_model, res_id, key):
        """Adjuntos-imagen de UNA galería (campo-foto), ordenados por antigüedad."""
        return request.env['ir.attachment'].sudo().search([
            ('res_model', '=', res_model),
            ('res_id', '=', res_id),
            ('res_field', '=', False),
            ('visar_photo_key', '=', key),
            ('mimetype', 'like', 'image/'),
        ], order='id asc')

    def _field_photo_ids(self, res_model, res_id, key):
        return self._field_photo_atts(res_model, res_id, key).ids

    @staticmethod
    def _is_ajax():
        """True si la petición viene por fetch (galería sin recargar la página)."""
        return (request.httprequest.headers.get('X-Requested-With')
                == 'XMLHttpRequest')

    @staticmethod
    def _json_ok(**payload):
        """Respuesta JSON para las galerías AJAX (upload/delete sin recarga)."""
        payload.setdefault('ok', True)
        return request.make_response(
            json.dumps(payload), [('Content-Type', 'application/json')])

    def _sync_binary_from_photos(self, record, att_res_model, att_res_id, field):
        """Copia la PRIMERA foto de la galería al campo binary de `record` (o lo limpia).

        Mantiene una foto representativa en el campo del modelo para que el reporte
        nativo (PDF) siga mostrando imagen; las demás viven solo como adjuntos. La
        galería puede colgar de otro modelo que el que se escribe (campos-foto
        principales: adjuntos en la TAREA, binary en el registro de la worksheet).
        """
        if record is None:
            return
        atts = self._field_photo_atts(att_res_model, att_res_id, field)
        record.write({field: atts[0].datas if atts else False})

    def _worksheet_binary_fields(self, task):
        """Nombres de los campos-foto (binary) principales de la worksheet."""
        record = self._worksheet_record(task)
        return {d['name'] for d in self._worksheet_descriptors(task, record)
                if d['type'] == 'binary'}

    def _line_photo_line(self, task, record, line_id, field):
        """Devuelve la línea o2m dueña de un campo-foto de línea, o None.

        Valida que `field` sea un binary declarado en ALGUNA subficha de la
        worksheet y que `line_id` pertenezca a esa subficha de ESTA tarea.
        """
        for d in self._worksheet_descriptors(task, record):
            if d['type'] != 'one2many':
                continue
            if field not in {n for n, t in d['line_specs'] if t == 'binary'}:
                continue
            line = record[d['name']].filtered(lambda l: l.id == line_id)
            if line:
                return line
        return None

    @staticmethod
    def _node_is_invisible(node):
        """True si el nodo está oculto por un `invisible` constante ('1'/'True').

        Las expresiones dinámicas (p. ej. invisible="context.get('studio')") no se
        pueden evaluar aquí; se tratan como visibles, igual que el formulario nativo.
        """
        return node.get('invisible') in ('1', 'True', 'true')

    def _collect_field_nodes(self, node, out, ancestor_hidden):
        """Recolecta los nodos <field> visibles del arch, en orden de documento.

        A diferencia de un `iter('field')` plano, esto:
          - salta el subárbol <header> (barra de estado, botones: no son captura);
          - oculta los campos cuyo <page>/<group> ancestro esté invisible;
          - NO desciende dentro de un <field> (los subcampos de list/form de un
            one2many pertenecen a otro modelo; se procesan aparte por subficha).
        El filtrado por widget/nombre/tipo se aplica luego en el constructor de
        descriptores, que ya tiene la metadata del modelo.
        """
        if node.tag == 'header':
            return
        hidden = ancestor_hidden or self._node_is_invisible(node)
        if node.tag == 'field':
            if not hidden:
                out.append(node)
            return
        for child in node:
            self._collect_field_nodes(child, out, hidden)

    def _worksheet_field_nodes(self, Model):
        """Nodos <field> visibles del formulario nativo, en orden."""
        nodes = []
        try:
            arch = etree.fromstring(Model.get_view(view_type='form')['arch'])
            self._collect_field_nodes(arch, nodes, ancestor_hidden=False)
        except Exception:  # noqa: BLE001 - vista dinámica; degradar a fields_get
            _logger.warning("Worksheet form view ilegible para %s", Model._name)
        return nodes

    @staticmethod
    def _scalar_descriptor(info, name, value, help_text=''):
        """Descriptor de un campo escalar (o de línea) para renderizar un control.

        `help_text` proviene del nodo de la vista (donde Studio guarda el "Help
        Tooltip"); el help a nivel de modelo casi siempre está vacío.
        """
        ftype = info['type']
        desc = {
            'name': name,
            'type': ftype,
            'string': info.get('string') or name,
            'help': help_text or info.get('help') or '',
            'selection': info.get('selection') or [],
            'required': bool(info.get('required')),
            'value': value,
            'options': [],
            'value_id': False,
            'value_ids': [],
            'has_file': False,
            'photos': [],
            'conditional': None,
        }
        if ftype == 'many2one' and info.get('relation'):
            comodel = request.env[info['relation']].sudo()
            desc['options'] = comodel.search_read([], ['display_name'], limit=200)
            desc['value_id'] = value.id if value else False
        elif ftype == 'many2many' and info.get('relation'):
            comodel = request.env[info['relation']].sudo()
            desc['options'] = comodel.search_read([], ['display_name'], limit=200)
            desc['value_ids'] = value.ids if value else []
        elif ftype == 'binary':
            desc['has_file'] = bool(value)
            desc['value'] = False  # no arrastrar el binario al contexto de plantilla
        elif ftype == 'html':
            # El campo se edita en un <textarea> plano; se muestra el texto sin las
            # etiquetas HTML (p. ej. <p>…</p>) con que Odoo envuelve el valor.
            desc['value'] = html2plaintext(value) if value else ''
        return desc

    def _otro_conditional(self, name, meta):
        """Si `name` es un companion `{base}_otro`, devuelve la condición que lo
        muestra (cuando el campo base tiene 'Otro'/'Otros' elegido); si no, None.

        Funciona con selección simple (valor = opción 'Otro') y múltiple/m2m
        (trigger = id de la etiqueta 'Otro'). Convención: el companion va justo
        después de su campo base en la plantilla y se llama `{base}_otro`.
        """
        if not name.endswith('_otro'):
            return None
        base = name[:-5]
        info = meta.get(base)
        if not info:
            return None
        ftype = info.get('type')
        if ftype == 'selection':
            for value, label in (info.get('selection') or []):
                if (str(value).strip().lower().startswith('otro')
                        or str(label).strip().lower().startswith('otro')):
                    return {'controller': base, 'kind': 'selection', 'trigger': str(value)}
        elif ftype == 'many2many' and info.get('relation'):
            for rec in request.env[info['relation']].sudo().search([], limit=200):
                if (rec.display_name or '').strip().lower().startswith('otro'):
                    return {'controller': base, 'kind': 'many2many', 'trigger': str(rec.id)}
        return None

    def _o2m_fk_name(self, LineModel, Model):
        """Nombre del many2one de la línea que apunta de vuelta a la worksheet."""
        for name, info in LineModel.fields_get().items():
            if info.get('type') == 'many2one' and info.get('relation') == Model._name:
                return name
        return None

    def _o2m_line_rows(self, Model, o2m_name, LineModel, fk_name):
        """Filas de campos de línea, respetando los `<group>` anidados de la subficha.

        Devuelve una lista de filas; cada fila es una lista de (nombre, help):
          - un `<field>` suelto → fila de 1 (ancho completo);
          - un `<group>` anidado con 2 campos → fila de 2 (se renderiza en 2 columnas).
        """
        try:
            arch = etree.fromstring(Model.get_view(view_type='form')['arch'])
        except Exception:  # noqa: BLE001
            return []
        node = next((f for f in arch.iter('field') if f.get('name') == o2m_name), None)
        if node is None:
            return []
        sub = node.find('form') or node.find('list') or node.find('tree')
        if sub is None:
            return []
        line_meta = LineModel.fields_get()
        seen = set()

        def item(fnode):
            name = fnode.get('name')
            if not name or name in seen:
                return None
            if fnode.get('widget') in WORKSHEET_SKIP_WIDGETS:
                return None
            if self._node_is_invisible(fnode):
                return None
            if fnode.get('column_invisible') in ('1', 'True', 'true'):
                return None
            info = line_meta.get(name)
            if not info or info['type'] in LINE_SKIP_TYPES:
                return None
            if name in WORKSHEET_OMIT or name == fk_name or name.endswith('_sequence'):
                return None
            seen.add(name)
            return (name, fnode.get('help') or '')

        container = sub.find('group') or sub
        rows = []
        for child in container:
            if child.tag == 'field':
                it = item(child)
                if it:
                    rows.append([it])
            elif child.tag == 'group':
                pair = [x for x in (item(f) for f in child if f.tag == 'field') if x]
                if len(pair) == 2:
                    rows.append(pair)
                else:
                    rows.extend([x] for x in pair)
        return rows

    def _o2m_descriptor(self, Model, info, name, record, o2m_help=''):
        """Descriptor de una subficha one2many (tarjetas dinámicas)."""
        relation = info.get('relation')
        if not relation or relation not in request.env:
            return None
        LineModel = request.env[relation].sudo()
        line_meta = LineModel.fields_get()
        fk = self._o2m_fk_name(LineModel, Model)
        line_rows = self._o2m_line_rows(Model, name, LineModel, fk)
        if not line_rows:
            return None
        flat = [it for row in line_rows for it in row]  # [(name, help), ...]
        line_names = [n for n, _h in flat]
        seq_field = next(
            (n for n in line_meta if n.endswith('_sequence')
             and line_meta[n]['type'] == 'integer'), None)

        def mk(n, h, rec_line):
            d = self._scalar_descriptor(
                line_meta[n], n, (rec_line[n] if rec_line else False), h)
            d['conditional'] = self._otro_conditional(n, line_meta)
            if d['type'] == 'binary':
                d['photos'] = (self._field_photo_ids(relation, rec_line.id, n)
                               if rec_line else [])
            return d

        def build_rows(rec_line):
            """Filas de descriptores (con valores de `rec_line` o en blanco)."""
            return [[mk(n, h, rec_line) for (n, h) in row] for row in line_rows]

        lines = []
        if record:
            existing = record[name]
            if seq_field:
                existing = existing.sorted(lambda l: l[seq_field])
            for line in existing:
                lines.append({'id': line.id, 'rows': build_rows(line)})
        return {
            'name': name,
            'type': 'one2many',
            'string': info.get('string') or name,
            'help': o2m_help or info.get('help') or '',
            'relation': relation,
            'fk': fk,
            'sequence_field': seq_field,
            'line_specs': [(n, line_meta[n]['type']) for n in line_names],
            'blank_rows': build_rows(None),
            'lines': lines,
            'conditional': None,
        }

    def _worksheet_descriptors(self, task, record):
        """Lista ordenada de descriptores (escalares y subfichas one2many)."""
        Model = self._worksheet_model(task)
        if Model is None:
            return []
        meta = Model.fields_get()
        nodes = self._worksheet_field_nodes(Model)
        descriptors = []
        seen = set()
        for node in nodes:
            name = node.get('name')
            widget = node.get('widget')
            if (not name or name in WORKSHEET_OMIT or name in seen
                    or widget in WORKSHEET_SKIP_WIDGETS
                    or any(h in name for h in WORKSHEET_SKIP_NAME_HINTS)):
                continue
            info = meta.get(name)
            if not info:
                continue
            ftype = info['type']
            seen.add(name)
            help_text = node.get('help') or ''
            if ftype == 'one2many':
                desc = self._o2m_descriptor(Model, info, name, record, help_text)
                if desc:
                    descriptors.append(desc)
            else:
                sdesc = self._scalar_descriptor(
                    info, name, record[name] if record else False, help_text)
                sdesc['conditional'] = self._otro_conditional(name, meta)
                if sdesc['type'] == 'binary':
                    # Galería viva sobre la tarea (existe siempre), etiquetada por campo.
                    sdesc['photos'] = self._field_photo_ids(
                        'project.task', task.id, name)
                descriptors.append(sdesc)
        if not descriptors:  # vista ilegible: degradar a campos x_ escalares
            for name, info in meta.items():
                if (name.startswith('x_') and name not in WORKSHEET_OMIT
                        and info['type'] not in WORKSHEET_SKIP_TYPES):
                    descriptors.append(self._scalar_descriptor(
                        info, name, record[name] if record else False))
        return descriptors

    @staticmethod
    def _coerce_scalar(ftype, raw):
        """Convierte un valor crudo (string de form) al tipo del campo."""
        if ftype == 'boolean':
            return bool(raw)
        if ftype == 'integer':
            return int(raw or 0)
        if ftype in ('float', 'monetary'):
            return float(raw or 0)
        if ftype == 'many2one':
            return int(raw) if raw else False
        if ftype == 'html':
            # El técnico escribe texto plano; se guarda como HTML limpio (<p>…</p>
            # con saltos de línea) para que el reporte nativo lo renderice bien.
            return plaintext2html(raw) if raw else False
        return raw or False  # char, text, selection, date, datetime

    def _worksheet_write_values(self, task, record, post, files):
        """Coacciona los valores ESCALARES a escribir en la worksheet."""
        vals = {}
        for d in self._worksheet_descriptors(task, record):
            name, ftype = d['name'], d['type']
            if ftype == 'one2many':
                continue  # las subfichas se sincronizan aparte
            if ftype == 'binary':
                upload = files.get(name)
                if upload:
                    data = upload.read()
                    if data:
                        vals[name] = base64.b64encode(data)
            elif ftype == 'boolean':
                vals[name] = bool(post.get(name))
            elif ftype == 'many2many':
                # Checkboxes con el mismo name → varios valores; siempre presente en
                # el form, así que [] significa "desmarcó todo".
                ids = request.httprequest.form.getlist(name)
                vals[name] = [(6, 0, [int(i) for i in ids if i.isdigit()])]
            elif name in post:
                vals[name] = self._coerce_scalar(ftype, post.get(name))
        for protected in WORKSHEET_OMIT:
            vals.pop(protected, None)
        return vals

    def _sync_worksheet_lines(self, task, record, post, files=None):
        """Sincroniza las subfichas one2many desde los inputs de tarjetas.

        Regla de conjunto: las filas enviadas son el estado deseado. Las líneas
        existentes cuyo id no vuelve se eliminan; las filas nuevas no vacías se
        crean. El marcador `o2mpresent~{campo}` distingue "subficha vaciada por el
        técnico" de "subficha no incluida en este formulario". Las fotos por
        tarjeta (binary) llegan en `files` (multipart), no en `post`.
        """
        Model = self._worksheet_model(task)
        if Model is None or record is None:
            return
        present = {k.split(O2M_SEP, 1)[1] for k in post
                   if k.startswith(O2M_PRESENT_PREFIX + O2M_SEP)}
        if not present:
            return
        # Agrupa inputs de línea: o2m -> fila -> {campo: valor}
        rows_by_o2m = {}
        for key, value in post.items():
            if not key.startswith(O2M_LINE_PREFIX + O2M_SEP):
                continue
            parts = key.split(O2M_SEP)
            if len(parts) != 4:
                continue
            _prefix, o2m, row, field = parts
            rows_by_o2m.setdefault(o2m, {}).setdefault(row, {})[field] = value

        for d in self._worksheet_descriptors(task, record):
            if d['type'] != 'one2many' or d['name'] not in present:
                continue
            self._sync_one_o2m(record, d, rows_by_o2m.get(d['name'], {}), files)

    def _sync_one_o2m(self, record, d, rows, files=None):
        """Crea/actualiza/elimina las líneas de UNA subficha one2many.

        Las fotos de línea (binary) YA NO se escriben en el campo del modelo: se
        adjuntan como galería (varias por campo). Al guardar se agregan las nuevas
        fotos seleccionadas en la tarjeta; el borrado por-foto es aparte (ruta
        line-photo). Se mantiene la primera foto en el campo binary para el reporte.
        """
        LineModel = request.env[d['relation']].sudo()
        fk, seq_field = d['fk'], d['sequence_field']
        # Los modelos de línea de Studio suelen tener `x_name` requerido; se rellena
        # al crear con una etiqueta derivada para no violar el NOT NULL.
        has_x_name = 'x_name' in LineModel._fields
        binary_fields = [n for n, t in d['line_specs'] if t == 'binary']
        valid_ids = set(record[d['name']].ids)
        submitted_ids = set()
        for seq, row_key in enumerate(sorted(rows, key=self._row_sort_key)):
            row = rows[row_key]
            raw_id = row.get('id')
            line_id = int(raw_id) if (raw_id and raw_id.isdigit()) else False
            # Fotos nuevas seleccionadas en esta tarjeta (multi-archivo por campo).
            row_files = {}
            for name in binary_fields:
                key = O2M_SEP.join((O2M_LINE_PREFIX, d['name'], row_key, name))
                # Un input file sin selección igual llega como FileStorage vacío
                # (filename ''); solo cuentan los que traen archivo real.
                uploads = [u for u in (files.getlist(key) if files else [])
                           if u and u.filename]
                if uploads:
                    row_files[name] = uploads
            vals = {}
            for name, ftype in d['line_specs']:
                if ftype == 'binary':
                    continue  # se maneja como adjunto abajo, no como campo
                elif ftype == 'boolean':
                    vals[name] = bool(row.get(name))
                elif ftype == 'many2many':
                    # Casillas con el mismo name → getlist (el dict `row` colapsa repetidos).
                    key = O2M_SEP.join((O2M_LINE_PREFIX, d['name'], row_key, name))
                    ids = request.httprequest.form.getlist(key)
                    vals[name] = [(6, 0, [int(i) for i in ids if i.isdigit()])]
                elif name in row:
                    vals[name] = self._coerce_scalar(ftype, row.get(name))
            if seq_field:
                vals[seq_field] = seq
            target_line = None
            if line_id and line_id in valid_ids:
                target_line = LineModel.browse(line_id)
                target_line.write(vals)
                submitted_ids.add(line_id)
            # Se crea si hay contenido escalar O fotos nuevas (tarjeta solo-foto).
            elif self._line_vals_has_content(vals, seq_field) or row_files:
                vals[fk] = record.id
                if has_x_name and not vals.get('x_name'):
                    label = next(
                        (str(vals[n]) for n, t in d['line_specs']
                         if t in ('char', 'text', 'selection') and vals.get(n)),
                        '%s %d' % (d['string'], seq + 1))
                    vals['x_name'] = label[:200]
                target_line = LineModel.create(vals)
            if target_line and row_files:
                self._attach_line_photos(target_line, row_files)
        # Solo se eliminan líneas ORIGINALES no reenviadas (las recién creadas no
        # están en valid_ids, así que nunca se borran por error). Se limpian también
        # sus adjuntos-foto (no hay cascade automático de ir.attachment).
        stale = LineModel.browse(list(valid_ids - submitted_ids)).exists()
        if stale:
            self._unlink_line_photos(stale, binary_fields)
            stale.unlink()

    def _attach_line_photos(self, line, row_files):
        """Crea adjuntos-foto para una línea y sincroniza su campo binary (reporte)."""
        Attachment = request.env['ir.attachment'].sudo()
        for field, uploads in row_files.items():
            created = False
            for upload in uploads:
                data = upload.read()
                if not data:
                    continue
                Attachment.create({
                    'name': upload.filename or 'foto.jpg',
                    'datas': base64.b64encode(data),
                    'res_model': line._name,
                    'res_id': line.id,
                    'mimetype': upload.mimetype or 'image/jpeg',
                    'visar_photo_key': field,
                })
                created = True
            if created:
                self._sync_binary_from_photos(line, line._name, line.id, field)

    def _unlink_line_photos(self, lines, binary_fields):
        """Elimina los adjuntos-foto de un conjunto de líneas que se van a borrar."""
        if not binary_fields or not lines:
            return
        atts = request.env['ir.attachment'].sudo().search([
            ('res_model', '=', lines._name),
            ('res_id', 'in', lines.ids),
            ('visar_photo_key', 'in', binary_fields),
        ])
        if atts:
            atts.unlink()

    @staticmethod
    def _line_vals_has_content(vals, seq_field):
        """True si la tarjeta nueva tiene algún valor real (para no crear vacías).

        Trata un comando m2m vacío `[(6, 0, [])]` como SIN contenido.
        """
        for key, value in vals.items():
            if key == seq_field:
                continue
            if isinstance(value, list):  # comando m2m [(6, 0, [ids])]
                if value and value[0][2]:
                    return True
            elif value:
                return True
        return False

    @staticmethod
    def _row_sort_key(row_key):
        """Ordena filas existentes (índices numéricos) antes que las nuevas ('nN')."""
        return (0, int(row_key)) if row_key.isdigit() else (1, row_key)

    # ==================================================================
    # Login por PIN
    # ==================================================================
    @http.route('/visar/field', type='http', auth='public', website=True, sitemap=False)
    def field_login(self, **kw):
        if self._current_employee():
            return request.redirect('/visar/field/tasks')
        return request.render('visar_field_app.field_login', {'error': kw.get('error')})

    @http.route('/visar/field/login', type='http', auth='public', website=True,
                methods=['POST'], csrf=True)
    def field_login_submit(self, **post):
        employee = request.env['hr.employee']._visar_field_find_by_pin(post.get('pin'))
        if not employee:
            return request.redirect('/visar/field?error=1')

        shift = request.env['visar.field.session'].sudo().create({
            'employee_id': employee.id,
            'note': request.httprequest.user_agent.string[:120]
            if request.httprequest.user_agent else False,
        })
        request.session[SESSION_EMPLOYEE] = employee.id
        request.session[SESSION_SHIFT] = shift.id
        return request.redirect('/visar/field/tasks')

    @http.route('/visar/field/logout', type='http', auth='public', website=True,
                methods=['POST'], csrf=True)
    def field_logout(self, **post):
        shift_id = request.session.get(SESSION_SHIFT)
        if shift_id:
            shift = request.env['visar.field.session'].sudo().browse(shift_id).exists()
            if shift and shift.state == 'open':
                shift.action_close()
        request.session.pop(SESSION_EMPLOYEE, None)
        request.session.pop(SESSION_SHIFT, None)
        return request.redirect('/visar/field')

    # ==================================================================
    # Lista de servicios del técnico
    # ==================================================================
    @http.route('/visar/field/tasks', type='http', auth='public', website=True,
                sitemap=False)
    def field_tasks(self, **kw):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        tasks = self._employee_tasks(employee)
        payload = self._task_map_payload(tasks)
        return request.render('visar_field_app.field_tasks', {
            'employee': employee,
            'tasks': tasks,
            'map_tasks_json': json.dumps(payload),
            'geocoded_count': sum(1 for t in payload if t['has_coords']),
        })

    # ==================================================================
    # Detalle de un servicio
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>', type='http', auth='public',
                website=True, sitemap=False)
    def field_task_detail(self, task_id, **kw):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        # Las fotos ya no son una sección general: cada campo-foto de la hoja de
        # trabajo trae su propia galería (descriptores con clave 'photos').
        worksheet = self._worksheet_record(task)
        # Minutos de espera: el valor elegido por el técnico en la tarea; si no ha
        # elegido (0), el parámetro global.
        waiting_minutes = task.visar_waiting_minutes or self._default_waiting_minutes()
        return request.render('visar_field_app.field_task_detail', {
            'employee': employee,
            'task': task,
            'has_worksheet_template': bool(task.worksheet_template_id),
            'worksheet_fields': self._worksheet_descriptors(task, worksheet),
            'is_signed': bool(task.worksheet_signature),
            'saved': kw.get('saved'),
            'maps_url': self._google_maps_url(task),
            'contact': self._task_contact(task),
            'flow_state': self._task_flow_state(task),
            'waiting_minutes': waiting_minutes,
            # 'Z' marca UTC: Odoo guarda datetimes naive en UTC; sin el marcador el
            # navegador los interpreta como hora LOCAL y desfasa la cuenta regresiva
            # por el offset del huso (p. ej. +6 h en Monterrey → arranca en 360:xx).
            'waiting_start_iso': (task.visar_waiting_start.isoformat() + 'Z'
                                  if task.visar_waiting_start else ''),
            'close_error': kw.get('close_error'),
        })

    # ==================================================================
    # Captura: fotos por campo (galería viva sobre campos-foto principales)
    # ==================================================================
    # Sube N fotos a la galería de UN campo-foto principal de la worksheet. Los
    # adjuntos cuelgan de la tarea (existe siempre) etiquetados con visar_photo_key
    # = nombre del campo, y se sincroniza la primera al campo binary (reporte).
    @http.route('/visar/field/task/<int:task_id>/ws-photo/<field>', type='http',
                auth='public', website=True, methods=['POST'], csrf=True)
    def field_task_ws_photo(self, task_id, field, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        if field in self._worksheet_binary_fields(task):
            Attachment = request.env['ir.attachment'].sudo()
            for upload in request.httprequest.files.getlist('photos'):
                data = upload.read()
                if not data:
                    continue
                Attachment.create({
                    'name': upload.filename or 'foto.jpg',
                    'datas': base64.b64encode(data),
                    'res_model': 'project.task',
                    'res_id': task.id,
                    'mimetype': upload.mimetype or 'image/jpeg',
                    'visar_photo_key': field,
                })
            record = self._worksheet_record(task, create=True)
            self._sync_binary_from_photos(record, 'project.task', task.id, field)
        if self._is_ajax():
            return self._json_ok(
                photos=self._field_photo_ids('project.task', task.id, field))
        return request.redirect('/visar/field/task/%s' % task.id)

    # Borra UNA foto de la galería de un campo-foto principal (adjunto etiquetado),
    # acotada a la tarea del técnico. Re-sincroniza la primera foto al binary.
    @http.route('/visar/field/task/<int:task_id>/ws-photo/<field>/<int:attachment_id>/delete',
                type='http', auth='public', website=True, methods=['POST'], csrf=True)
    def field_task_ws_photo_delete(self, task_id, field, attachment_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        attachment = request.env['ir.attachment'].sudo().browse(attachment_id).exists()
        if (attachment and attachment.res_model == 'project.task'
                and attachment.res_id == task.id
                and attachment.visar_photo_key == field
                and not attachment.res_field
                and (attachment.mimetype or '').startswith('image/')):
            attachment.unlink()
            self._sync_binary_from_photos(
                self._worksheet_record(task), 'project.task', task.id, field)
        if self._is_ajax():
            return self._json_ok(
                photos=self._field_photo_ids('project.task', task.id, field))
        return request.redirect('/visar/field/task/%s' % task.id)

    # Sirve una imagen (adjunto de la tarea) acotada al técnico de la sesión.
    @http.route('/visar/field/task/<int:task_id>/image/<int:attachment_id>',
                type='http', auth='public', website=True, sitemap=False)
    def field_task_image(self, task_id, attachment_id, **kw):
        employee = self._current_employee()
        task = self._task_for_employee(task_id, employee) if employee else None
        if not task:
            return request.not_found()
        attachment = request.env['ir.attachment'].sudo().browse(attachment_id).exists()
        if (not attachment or attachment.res_model != 'project.task'
                or attachment.res_id != task.id):
            return request.not_found()
        return request.make_response(
            base64.b64decode(attachment.datas or b''),
            [('Content-Type', attachment.mimetype or 'image/jpeg')])

    # Sirve UNA foto de la galería de un campo-foto de LÍNEA (adjunto sobre el
    # modelo de línea), acotada al técnico, a la worksheet de la tarea y a la línea.
    @http.route('/visar/field/task/<int:task_id>/line-photo/<int:line_id>/<field>/<int:attachment_id>',
                type='http', auth='public', website=True, sitemap=False)
    def field_task_line_photo(self, task_id, line_id, field, attachment_id, **kw):
        employee = self._current_employee()
        task = self._task_for_employee(task_id, employee) if employee else None
        if not task:
            return request.not_found()
        record = self._worksheet_record(task)
        line = self._line_photo_line(task, record, line_id, field) if record else None
        if line is None:
            return request.not_found()
        att = request.env['ir.attachment'].sudo().browse(attachment_id).exists()
        if (not att or att.res_model != line._name or att.res_id != line.id
                or att.visar_photo_key != field):
            return request.not_found()
        return request.make_response(
            base64.b64decode(att.datas or b''),
            [('Content-Type', att.mimetype or 'image/jpeg')])

    # Borra UNA foto de la galería de un campo-foto de línea. Re-sincroniza la
    # primera foto restante al campo binary de la línea (reporte).
    @http.route('/visar/field/task/<int:task_id>/line-photo/<int:line_id>/<field>/<int:attachment_id>/delete',
                type='http', auth='public', website=True, methods=['POST'], csrf=True)
    def field_task_line_photo_delete(self, task_id, line_id, field, attachment_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')
        record = self._worksheet_record(task)
        line = self._line_photo_line(task, record, line_id, field) if record else None
        if line is not None:
            att = request.env['ir.attachment'].sudo().browse(attachment_id).exists()
            if (att and att.res_model == line._name and att.res_id == line.id
                    and att.visar_photo_key == field and not att.res_field
                    and (att.mimetype or '').startswith('image/')):
                att.unlink()
                self._sync_binary_from_photos(line, line._name, line.id, field)
        if self._is_ajax():
            return self._json_ok()
        return request.redirect('/visar/field/task/%s' % task.id)

    # ==================================================================
    # Captura: worksheet nativa
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/worksheet', type='http', auth='public',
                website=True, methods=['POST'], csrf=True)
    def field_task_worksheet(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        record = self._worksheet_record(task, create=True)
        if record is not None:
            vals = self._worksheet_write_values(
                task, record, post, request.httprequest.files)
            if vals:
                record.write(vals)
            self._sync_worksheet_lines(
                task, record, post, request.httprequest.files)
        return request.redirect('/visar/field/task/%s?saved=1' % task.id)

    # ==================================================================
    # Transiciones en sitio (etapas nativas + sellos de tiempo) — Req 2
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/status', type='http', auth='public',
                website=True, methods=['POST'], csrf=True)
    def field_task_status(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        action = post.get('action')
        if action == 'enroute':
            task._visar_set_stage(1)  # En camino
        elif action == 'arrived':
            if not task.visar_arrived_at:
                task.visar_arrived_at = fields.Datetime.now()
            task._visar_set_stage(2)  # llegada → directo a En ejecución (etapa FSM)
        elif action == 'waiting':
            # Guarda los minutos elegidos y (re)sella el inicio de espera.
            task.visar_waiting_minutes = self._coerce_waiting_minutes(post.get('minutes'))
            task.visar_waiting_start = fields.Datetime.now()
        elif action == 'start':
            # Registra cuánto se esperó al cliente (de 'Esperar al cliente' hasta ahora).
            if task.visar_waiting_start and not task.visar_service_start:
                waited = fields.Datetime.now() - task.visar_waiting_start
                task.visar_client_wait_minutes = max(waited.total_seconds() / 60.0, 0.0)
            if not task.visar_service_start:
                task.visar_service_start = fields.Datetime.now()
            task._visar_set_stage(2)  # ya está En ejecución; no cambia de etapa
        elif action == 'reschedule':
            task._visar_flag_reschedule(employee)
            return request.redirect('/visar/field/tasks')
        return request.redirect('/visar/field/task/%s' % task.id)

    # ==================================================================
    # Cierre del servicio (firma nativa + atribución + etapa + timesheet)
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/close', type='http', auth='public',
                website=True, methods=['POST'], csrf=True)
    def field_task_close(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')

        # Validación de cierre: firma Y nombre obligatorios (defensa en profundidad;
        # el JS también bloquea el submit).
        signature = self._decode_data_url(post.get('signature'))
        signed_by = (post.get('signature_name') or '').strip()
        if not signature or not signed_by:
            return request.redirect('/visar/field/task/%s?close_error=1' % task.id)

        # Cronómetro oculto → timesheet nativo (mientras visar_service_start sigue puesto).
        task._visar_write_service_timesheet(employee)
        task._visar_set_stage(3)  # Completado
        task.write({
            'visar_field_closed_by_id': employee.id,
            'visar_field_closed_at': fields.Datetime.now(),
            'state': '1_done',
            'worksheet_signature': signature,       # campos NATIVOS (reporte)
            'worksheet_signed_by': signed_by,
        })
        return request.redirect('/visar/field/task/%s?saved=1' % task.id)

    # ==================================================================
    # Reporte nativo (PDF) — preview/descarga para el técnico
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/report', type='http', auth='public',
                website=True, sitemap=False)
    def field_task_report(self, task_id, **kw):
        employee = self._current_employee()
        task = self._task_for_employee(task_id, employee) if employee else None
        if not task:
            return request.redirect('/visar/field')
        pdf, _ctype = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            FSM_REPORT, [task.id])
        return request.make_response(pdf, [
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', 'inline; filename="reporte-servicio.pdf"'),
        ])

    # ==================================================================
    # Utilidades
    # ==================================================================
    @staticmethod
    def _decode_data_url(value):
        """Convierte un data-URL (canvas de firma) a base64 puro para Binary."""
        if not value:
            return False
        if ',' in value:
            value = value.split(',', 1)[1]
        return value or False
