# -*- coding: utf-8 -*-
import base64
import json
import logging
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

import pytz
import requests
from lxml import etree

from odoo import fields, http
from odoo.http import request
from odoo.tools import html2plaintext, plaintext2html

from ..hooks import AREAS_FIJAS, FUM_LINE

_logger = logging.getLogger(__name__)

# Claves de sesión HTTP que identifican al técnico en el dispositivo.
SESSION_EMPLOYEE = 'visar_field_employee_id'
SESSION_SHIFT = 'visar_field_session_id'

# Estados de tarea considerados "cerrados" (servicio terminado o cancelado).
CLOSED_STATES = ('1_done', '1_canceled')

# Alcances de la lista de servicios (`?scope=`):
#   today = solo los agendados para HOY (por defecto), en cualquier estado —
#           los ya cerrados se muestran al final con su etiqueta.
#   all   = todos los asignados al técnico, de cualquier fecha y estado.
# El MAPA siempre usa `today` sin importar el alcance de la lista.
SCOPE_TODAY = 'today'
SCOPE_ALL = 'all'
SCOPES = (SCOPE_TODAY, SCOPE_ALL)

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
# Línea "fija": tarjeta que la hoja trae de nacimiento y el técnico no puede
# eliminar (áreas de inspección obligatoria). El campo no va en el arch — es
# contabilidad interna — así que se lee del modelo, no del descriptor.
LINE_FIXED_FLAG = 'x_fija'
# Campo que da título a una tarjeta fija (su valor ya está decidido al sembrarla).
LINE_FIXED_TITLE_FIELD = 'x_area'
# Áreas de inspección obligatoria por modelo de línea. El catálogo vive en
# `hooks.py` (fuente de verdad de la plantilla) y aquí solo se mapea al modelo.
LINE_FIXED_AREAS = {FUM_LINE: AREAS_FIJAS}
# Prefijos de los inputs de las tarjetas one2many (una sola submission).
#   o2mline~{campo_o2m}~{fila}~{campo_linea} = valor
#   o2mline~{campo_o2m}~{fila}~id            = id de la línea existente (o vacío)
#   o2mpresent~{campo_o2m}                   = marcador de que la subficha se renderizó
O2M_LINE_PREFIX = 'o2mline'
O2M_PRESENT_PREFIX = 'o2mpresent'
O2M_SEP = '~'
# Reporte nativo de la worksheet FSM.
FSM_REPORT = 'industry_fsm.worksheet_custom'
# Lado del QR de cobro (px). Suficiente para que una cámara lo lea desde ~30 cm
# sobre la pantalla del técnico, sin que el PNG pese de más.
UPSELL_QR_PX = 320

# ------------------------------------------------------------------
# Obligatoriedad de la hoja de trabajo (Req 7)
# ------------------------------------------------------------------
# Los campos SIEMPRE obligatorios se declaran con `required="1"` en el arch de
# cada plantilla (fuente de verdad; el reporte nativo también los honra) y la app
# los lee del nodo de la vista. Aquí van solo las reglas que el arch no expresa:
#
# 1. Obligatoriedad CONDICIONAL por un campo disparador (`{campo: (controlador,
#    kind, valor)}`). `kind='truthy'` = el controlador (casilla booleana) marcado.
#    Los companion "Especifique cuál otro" NO van aquí: son obligatorios cuando se
#    muestran, y eso se deriva de su propia condición de visibilidad (`conditional`).
WORKSHEET_REQUIRED_IF = {
    # Áreas verdes: foto y número de bolsas si se embolsaron residuos.
    'x_foto_bolsas': ('x_residuos_embolsados', 'truthy', ''),
    'x_num_bolsas': ('x_residuos_embolsados', 'truthy', ''),
}
# 2. Subfichas one2many que exigen AL MENOS UNA línea (el arch no tiene forma de
#    declarar "min 1" en un o2m). Nombres únicos entre las tres plantillas.
WORKSHEET_MIN_ONE = {'x_areas_tratadas', 'x_labores', 'x_zonas_evidencia'}

# 3. Campos que dejan de ser obligatorios cuando un disparador está puesto, POR
#    LÍNEA COMPLETA: `{modelo_de_línea: campo_disparador}`. Todo campo de esa
#    tarjeta (salvo el disparador mismo) deja de exigirse. Se declara por modelo y
#    no campo-por-campo para que agregar un campo a la tarjeta quede cubierto solo.
WORKSHEET_REQUIRED_UNLESS_LINE = {
    # "Cliente NO permitió que se fumigara en esta área": el área es obligatoria,
    # pero si no se dejó tratar no se le puede exigir foto, plaga ni plaguicida.
    'x_visar_area_tratada_v2': 'x_cliente_no_permitio',
}

# ------------------------------------------------------------------
# Visibilidad condicional DECLARADA
# ------------------------------------------------------------------
# Además de la convención `{base}_otro` (ver `_otro_conditional`), un campo puede
# declarar aquí su condición de visibilidad: `{campo: (controlador, kind, trigger)}`.
#   - kind='truthy'     → el controlador (casilla) marcado. `trigger` se ignora.
#   - kind='many2many'  → `trigger` es el NOMBRE de la etiqueta que debe estar
#                         marcada (se resuelve a id al renderizar, porque el id
#                         cambia entre BDs y no se puede hardcodear).
#   - kind='selection'  → `trigger` es el valor de la opción.
# Un campo oculto por su condición NO es obligatorio (ni en cliente ni en
# servidor), aunque el arch lo marque `required="1"`.
WORKSHEET_CONDITIONAL = {
    # Fumigación (línea): el bloque de plaga cuelga de "¿presencia activa?".
    'x_foto_evidencia': ('x_infestacion_activa', 'truthy', ''),
    'x_plaga_ids': ('x_infestacion_activa', 'truthy', ''),
    # Taxonomía de 2 niveles: cada lista de especies cuelga de su categoría.
    'x_plaga_rastreros_ids': ('x_plaga_ids', 'many2many', "Rastreros"),
    'x_plaga_voladores_ids': ('x_plaga_ids', 'many2many', "Voladores"),
    'x_plaga_roedores_ids': ('x_plaga_ids', 'many2many', "Roedores"),
    'x_plaga_otras_ids': ('x_plaga_ids', 'many2many', "Otras plagas"),
    # Se declara EXPLÍCITAMENTE (no por convención `_otro`): la convención busca la
    # etiqueta que empieza con "otro" y aquí hay dos candidatas ("Otras plagas" y
    # "Otra plaga no en las opciones") — elegiría la equivocada.
    'x_plaga_ids_otro': ('x_plaga_ids', 'many2many', "Otra plaga no en las opciones"),
}
# Campos que se pintan indentados bajo el campo que los controla (jerarquía
# visual de la taxonomía). Puramente cosmético.
WORKSHEET_NESTED = set(WORKSHEET_CONDITIONAL) & {
    'x_plaga_rastreros_ids', 'x_plaga_voladores_ids',
    'x_plaga_roedores_ids', 'x_plaga_otras_ids',
}


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

    # ==================================================================
    # Huso horario / día del técnico
    # ==================================================================
    @staticmethod
    def _employee_tz(employee):
        """Huso del técnico. "Hoy" es el día del TÉCNICO, no el del servidor
        (que corre en UTC): sin esto, entre las 18:00 y la medianoche local el
        servidor ya estaría en el día siguiente y la lista se vaciaría.
        """
        tzname = (
            employee.tz
            or employee.resource_calendar_id.tz
            or request.env.company.resource_calendar_id.tz
            or 'UTC'
        )
        try:
            return pytz.timezone(tzname)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _local_day(self, employee, dt):
        """Fecha LOCAL del técnico de un datetime naive-UTC de Odoo (o None)."""
        if not dt:
            return None
        return pytz.utc.localize(dt).astimezone(self._employee_tz(employee)).date()

    def _today_bounds(self, employee):
        """`(inicio, fin)` naive-UTC del día de hoy del técnico, para el dominio.

        Se localiza la medianoche con `tz.localize` (no `replace(tzinfo=...)`)
        para que el offset sea el correcto en cambios de horario de verano.
        """
        tz = self._employee_tz(employee)
        today = pytz.utc.localize(fields.Datetime.now()).astimezone(tz).date()
        start_local = tz.localize(datetime.combine(today, time.min))
        end_local = start_local + timedelta(days=1)
        return (
            start_local.astimezone(pytz.utc).replace(tzinfo=None),
            end_local.astimezone(pytz.utc).replace(tzinfo=None),
        )

    # ==================================================================
    # Servicios del técnico
    # ==================================================================
    def _employee_tasks(self, employee, scope=SCOPE_TODAY):
        """Servicios del técnico en el alcance pedido, en el orden de la app.

        `scope=today` (por defecto) limita a los agendados para hoy — en el huso
        del técnico — en CUALQUIER estado: los ya cerrados siguen visibles al
        final para que el técnico vea el avance de su día. `scope=all` devuelve
        todo lo asignado, de cualquier fecha.

        Orden (ver `_task_sort_key`): día, pendientes antes que cerrados y,
        dentro de eso, el orden manual que el técnico dejó arrastrando las
        tarjetas (`visar.field.route.order`), con la hora agendada de desempate.
        """
        domain = [('visar_technician_ids', 'in', employee.ids)]
        if scope == SCOPE_TODAY:
            start, end = self._today_bounds(employee)
            domain += [
                ('planned_date_begin', '>=', start),
                ('planned_date_begin', '<', end),
            ]
        tasks = request.env['project.task'].sudo().search(domain)
        return self._sort_tasks(employee, tasks, scope)

    def _sort_tasks(self, employee, tasks, scope=SCOPE_TODAY):
        """Aplica el orden de la app (incluido el manual) a un recordset."""
        if not tasks:
            return tasks
        order_map = request.env['visar.field.route.order']._visar_order_map(
            employee, tasks)
        return tasks.sorted(
            key=lambda task: self._task_sort_key(employee, task, order_map, scope))

    def _task_sort_key(self, employee, task, order_map, scope):
        """Clave de orden de una tarjeta.

        1. **Día** — en `all`, los días más recientes primero (la lista completa
           se usa para consultar lo hecho); en `today` todos comparten día.
        2. **Cerrado al final** — dentro del día, primero lo que falta por hacer.
        3. **Orden manual** — el número de parada que el técnico arrastró; los
           que no ha ordenado (`UNORDERED_SEQUENCE`) caen después.
        4. **Hora agendada / id** — desempate estable.
        """
        Order = request.env['visar.field.route.order']
        day = self._local_day(employee, task.planned_date_begin)
        # Sin fecha agendada = lo más antiguo (no compite con el día en curso).
        day_key = day.toordinal() if day else 0
        if scope == SCOPE_ALL:
            day_key = -day_key  # más reciente primero
        return (
            day_key,
            1 if task.state in CLOSED_STATES else 0,
            order_map.get(task.id, Order.UNORDERED_SEQUENCE),
            task.planned_date_begin or fields.Datetime.now(),
            task.id,
        )

    @staticmethod
    def _scope(value):
        """Normaliza el `?scope=` recibido (cualquier basura → `today`)."""
        return value if value in SCOPES else SCOPE_TODAY

    # Etiqueta de la tarjeta para los servicios ya cerrados (los pendientes no
    # llevan etiqueta: su estado se ve al abrirlos).
    STATE_LABELS = {
        '1_done': ('Completado', 'text-bg-success'),
        '1_canceled': ('Reprogramar', 'text-bg-danger'),
    }

    def _task_state_label(self, task):
        """`{'text': ..., 'css': ...}` para un servicio cerrado, o False."""
        label = self.STATE_LABELS.get(task.state)
        if not label:
            return False
        return {'text': label[0], 'css': label[1]}

    def _task_times(self, employee, tasks, scope):
        """`{task_id: 'HH:MM'}` en el huso del TÉCNICO para pintar la tarjeta.

        Los datetimes de Odoo son naive-UTC: pintarlos con `t-esc`/`t-field` en
        una página pública los muestra en UTC (el usuario público no tiene huso),
        p. ej. 14:00 local se veía como "20:00". Se formatean aquí. En `all` se
        antepone el día, porque la lista mezcla fechas.
        """
        tz = self._employee_tz(employee)
        times = {}
        for task in tasks:
            if not task.planned_date_begin:
                continue
            local = pytz.utc.localize(task.planned_date_begin).astimezone(tz)
            times[task.id] = (local.strftime('%H:%M') if scope == SCOPE_TODAY
                              else local.strftime('%d/%m/%Y %H:%M'))
        return times

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

        `tasks` llega ya en el orden de la app (ver `_employee_tasks`): el orden
        manual que el técnico arrastró, u hora agendada si no ha arrastrado. Ese
        es el orden de la RUTA. A cada servicio **pendiente** se le asigna un
        número de parada consecutivo (`order`) en ese mismo orden, que el mapa
        dibuja sobre el pin (como los waypoints numerados del mapa nativo de
        Servicio externo) y la lista repite en la tarjeta.

        **Los servicios ya cerrados no se numeran ni entran a la ruta** (`done`):
        el número es "lo que falta por recorrer", no un historial — se plotean con
        un pin apagado de "✓" para ubicar lo ya atendido.

        El número se asigna aunque el servicio **no** esté geolocalizado, para que
        la tarjeta y el pin muestren SIEMPRE el mismo número (un servicio sin
        coordenadas simplemente no se plotea: el mapa salta ese número).
        """
        payload = []
        stop_number = 0
        for task in tasks:
            partner = task.partner_id
            lat = partner.partner_latitude if partner else 0.0
            lng = partner.partner_longitude if partner else 0.0
            has_coords = bool(partner) and bool(lat) and bool(lng)
            done = task.state in CLOSED_STATES
            order = None
            if not done:
                stop_number += 1
                order = stop_number
            payload.append({
                'id': task.id,
                'name': task.name or '',
                'client': partner.name if partner else '',
                'address': self._task_address(task),
                'url': '/visar/field/task/%s' % task.id,
                'lat': lat,
                'lng': lng,
                'has_coords': has_coords,
                'done': done,
                'order': order,
            })
        return payload

    # Directions API de Mapbox (misma cuenta/token que el mapa nativo web_map y
    # que la geocodificación de res_partner). Límite duro: 25 coordenadas por
    # petición. `overview=full` devuelve la geometría completa de la ruta.
    MAPBOX_DIRECTIONS_URL = (
        'https://api.mapbox.com/directions/v5/mapbox/driving/%s')
    MAPBOX_DIRECTIONS_MAX_POINTS = 25

    def _task_route_geometry(self, payload):
        """Geometría de la ruta que sigue las calles entre los servicios geolocalizados,
        en el orden de agenda. Se calcula **en el servidor** llamando a la Directions
        API de Mapbox, de modo que el token NO se expone en la página pública del
        técnico (mismo principio que la geocodificación server-side de res_partner).

        Devuelve una lista de puntos `[[lat, lng], ...]` (ya en orden Leaflet) lista
        para dibujar como polilínea, o `None` si no hay token, hay <2 paradas, o la
        API falla (el JS cae a líneas rectas entre paradas como respaldo visual).
        """
        # Solo las paradas que el técnico tiene por delante (las cerradas no se
        # rutean, igual que no se numeran en `_task_map_payload`).
        located = [p for p in payload if p.get('has_coords') and not p.get('done')]
        if len(located) < 2:
            return None
        token = request.env['ir.config_parameter'].sudo().get_param(
            'web_map.token_map_box')
        if not token:
            return None
        # La Directions API topa en 25 coordenadas; si hay más, se rutea hasta ahí
        # (las paradas restantes siguen numeradas y con pin, solo sin línea trazada).
        if len(located) > self.MAPBOX_DIRECTIONS_MAX_POINTS:
            _logger.info(
                "Ruta Mapbox: %s paradas > %s; se rutean las primeras %s.",
                len(located), self.MAPBOX_DIRECTIONS_MAX_POINTS,
                self.MAPBOX_DIRECTIONS_MAX_POINTS)
            located = located[:self.MAPBOX_DIRECTIONS_MAX_POINTS]
        coords = ';'.join('%s,%s' % (p['lng'], p['lat']) for p in located)
        try:
            resp = requests.get(
                self.MAPBOX_DIRECTIONS_URL % coords,
                params={
                    'access_token': token,
                    'geometries': 'geojson',
                    'overview': 'full',
                },
                timeout=10)
            resp.raise_for_status()
            routes = resp.json().get('routes') or []
        except Exception as err:  # noqa: BLE001 - red/API: degradar a líneas rectas
            _logger.warning("Directions Mapbox falló: %s", err)
            return None
        if not routes:
            return None
        # GeoJSON entrega [lon, lat]; Leaflet dibuja [lat, lng].
        line = routes[0].get('geometry', {}).get('coordinates') or []
        return [[pt[1], pt[0]] for pt in line] or None

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

    def _task_flow_state(self, task):
        """Sub-fase del flujo en sitio para elegir qué botón mostrar.

        La **etapa nativa `stage_id` manda**: si gestión la cambia en el backend, la
        app refleja ese cambio. Los sellos de tiempo solo REFINAN dentro de "En
        camino" (llegada/espera no tienen etapa propia); el `write` de project.task
        los deja consistentes con la etapa, así que aquí no pueden "ganarle".

        Devuelve: 'programado' | 'en_camino' | 'esperando' | 'en_ejecucion' |
                  'cerrado' | 'reagenda'.
        """
        stage = task.stage_id
        s1 = task._visar_fsm_stage(1)  # En camino
        s2 = task._visar_fsm_stage(2)  # En ejecución
        s3 = task._visar_fsm_stage(3)  # Completado
        s4 = task._visar_fsm_stage(4)  # Incidencia—Reprogramar
        sign = task._visar_stage_pending_signature()  # Pendiente de firma
        if s3 and stage == s3:
            return 'cerrado'
        if s4 and stage == s4:
            return 'reagenda'
        # Etapa archivada (retirada del flujo). Si queda alguna tarea vieja ahí,
        # se trata como servicio en curso (firma gated por visar_worksheet_saved_at).
        if sign and stage == sign:
            return 'en_ejecucion'
        if s2 and stage == s2:
            # 'Confirmar llegada' ya movió la etapa a En ejecución Y arrancó la espera
            # automáticamente; las sub-fases (espera → servicio) se distinguen por los
            # sellos de tiempo.
            if task.visar_service_start:
                return 'en_ejecucion'
            if task.visar_waiting_start:
                return 'esperando'
            # Sin sellos de espera (etapa puesta a mano, o llegada heredada previa a la
            # espera automática): se trata como servicio en curso para no dejar la
            # pantalla sin acción.
            return 'en_ejecucion'
        if s1 and stage == s1:
            return 'en_camino'
        return 'programado'  # Programado o cualquier otra etapa

    # Fases en las que la hoja de trabajo se muestra y se puede guardar: desde que
    # el técnico pulsa "Comenzar servicio" (Req 5). Se gatea por la FASE y no por el
    # sello `visar_service_start` porque la fase es la que manda (si gestión pone la
    # etapa a mano sin sellos, la app igual debe dejar capturar). 'cerrado' sigue
    # mostrándola: el técnico consulta lo que capturó.
    WORKSHEET_STATES = ('en_ejecucion', 'cerrado')

    def _worksheet_available(self, flow_state):
        """¿Se muestra/guarda la hoja de trabajo en esta fase? (Req 5)"""
        return flow_state in self.WORKSHEET_STATES

    def _worksheet_locked(self, task, flow_state):
        """¿La hoja de trabajo está en SOLO LECTURA?

        Un servicio cerrado (Completado) muestra su hoja para consulta, pero NO se
        edita por defecto: hay que pulsar "Habilitar edición" (`_visar_worksheet_reopen`),
        que sella `visar_worksheet_reopened_at` y la desbloquea hasta el próximo
        guardado. En ejecución la hoja siempre es editable. Este candado se aplica en
        el servidor (no solo en la plantilla): un POST directo a `/worksheet` sobre un
        servicio cerrado sin desbloquear se rechaza."""
        if flow_state != 'cerrado':
            return False
        return not task.visar_worksheet_reopened_at

    def _signature_available(self, task, flow_state):
        """¿Se muestra la sección de firma + "Cerrar servicio"? (Req 6)

        Solo con el servicio en ejecución **y** la hoja de trabajo guardada al menos
        una vez. Si el servicio **no tiene plantilla** de hoja de trabajo no hay nada
        que guardar: se muestra en cuanto arranca el servicio (si no, el técnico no
        podría cerrar nunca).
        """
        if flow_state != 'en_ejecucion':
            return False
        if not task.worksheet_template_id:
            return True
        return bool(task.visar_worksheet_saved_at)

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
        # Solo se siembra cuando el llamador va a ESCRIBIR: las rutas de lectura
        # (servir una foto, pintar una hoja cerrada) no deben mutar la hoja.
        if record and create:
            self._seed_fixed_lines(record)
        return record

    def _seed_fixed_lines(self, record):
        """Siembra las tarjetas de área OBLIGATORIA que falten en esta hoja.

        Idempotente y por VALOR de área, no por conteo: si el técnico ya llenó
        "Cocina" no se duplica, y si el catálogo gana un área obligatoria mañana,
        las hojas ya abiertas la reciben al siguiente render. Solo se siembra sobre
        subfichas cuyo modelo de línea tenga la marca `x_fija` (hoy, Fumigación).
        """
        for name, field in record._fields.items():
            if field.type != 'one2many':
                continue
            fixed_areas = LINE_FIXED_AREAS.get(field.comodel_name)
            if not fixed_areas:
                continue
            LineModel = request.env[field.comodel_name].sudo()
            if LINE_FIXED_FLAG not in LineModel._fields:
                continue  # BD sin re-sembrar todavía: se degrada a solo dinámicas
            present = set(record[name].filtered(
                lambda l: l[LINE_FIXED_FLAG]).mapped(LINE_FIXED_TITLE_FIELD))
            missing = [a for a in fixed_areas if a not in present]
            if not missing:
                continue
            fk = self._o2m_fk_name(LineModel, record)
            if not fk:
                continue
            has_x_name = 'x_name' in LineModel._fields
            for index, area in enumerate(fixed_areas):
                if area not in missing:
                    continue
                vals = {fk: record.id, LINE_FIXED_TITLE_FIELD: area,
                        LINE_FIXED_FLAG: True}
                # Secuencia negativa: las fijas quedan antes de cualquier área
                # que el técnico agregue (y el orden entre ellas es el del catálogo).
                if 'x_sequence' in LineModel._fields:
                    vals['x_sequence'] = index - len(fixed_areas)
                if has_x_name:
                    vals['x_name'] = area
                LineModel.create(vals)

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
    def _scalar_descriptor(info, name, value, help_text='', required=False):
        """Descriptor de un campo escalar (o de línea) para renderizar un control.

        `help_text` proviene del nodo de la vista (donde Studio guarda el "Help
        Tooltip"); el help a nivel de modelo casi siempre está vacío. `required`
        (obligatorio SIEMPRE) viene del nodo de la vista (`required="1"`), no del
        modelo (los campos `x_` son manual y casi nunca traen required de modelo).
        """
        ftype = info['type']
        desc = {
            'name': name,
            'type': ftype,
            'string': info.get('string') or name,
            'help': help_text or info.get('help') or '',
            'selection': info.get('selection') or [],
            'required': bool(required),
            # Obligatoriedad condicional: None, o {'controller','kind','value'}.
            # El campo es obligatorio solo cuando la condición se cumple.
            'required_if': None,
            # Dispensa: si la condición se cumple, el campo NO es obligatorio
            # aunque `required`/`required_if` digan que sí.
            'required_unless': None,
            # Cosmético: se pinta indentado bajo el campo que lo controla.
            'nested': name in WORKSHEET_NESTED,
            'value': value,
            'options': [],
            'value_id': False,
            'value_ids': [],
            'has_file': False,
            'photos': [],
            'conditional': None,
            # Condición propia + las de los ancestros (ver `_conditional_chain`).
            'conditional_chain': [],
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

    def _declared_conditional(self, name, meta):
        """Condición de visibilidad DECLARADA en `WORKSHEET_CONDITIONAL`, o None.

        Tiene prioridad sobre la convención `{base}_otro`. Para `many2many` el
        trigger se declara por NOMBRE de etiqueta y se resuelve al id vigente en
        esta BD (los ids de los catálogos no son estables entre BDs).
        """
        rule = WORKSHEET_CONDITIONAL.get(name)
        if not rule:
            return None
        controller, kind, trigger = rule
        info = meta.get(controller)
        if not info:
            return None
        if kind == 'many2many':
            if not info.get('relation'):
                return None
            tag = request.env[info['relation']].sudo().search(
                [('x_name', '=', trigger)], limit=1)
            if not tag:
                _logger.warning(
                    "Condicional de %s: no existe la etiqueta %r en %s",
                    name, trigger, info['relation'])
                return None
            trigger = str(tag.id)
        return {'controller': controller, 'kind': kind, 'trigger': trigger}

    def _conditional_chain(self, name, meta, _depth=0):
        """Condiciones que deben cumplirse TODAS para que el campo se vea: la suya
        y las de sus ancestros.

        La taxonomía de plagas anida dos niveles (especies → categoría → "¿presencia
        activa?"), así que mirar solo la condición propia no basta: un POST viejo
        podría traer la categoría marcada con la casilla de presencia apagada y el
        servidor exigiría un campo que el técnico no vio. El cliente resuelve lo
        mismo por la cascada del DOM (ver `evalCondFields`).
        """
        cond = self._otro_conditional(name, meta)
        if not cond or _depth >= 5:  # tope: protege de una declaración circular
            return [cond] if cond else []
        return [cond] + self._conditional_chain(
            cond['controller'], meta, _depth + 1)

    def _otro_conditional(self, name, meta):
        """Condición de visibilidad de un campo, o None si siempre se ve.

        Primero la declaración explícita (`WORKSHEET_CONDITIONAL`); si no hay,
        la convención `{base}_otro`: el companion se muestra cuando el campo base
        tiene 'Otro'/'Otros' elegido. Funciona con selección simple (valor =
        opción 'Otro') y múltiple/m2m (trigger = id de la etiqueta 'Otro').
        """
        declared = self._declared_conditional(name, meta)
        if declared:
            return declared
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

    @staticmethod
    def _required_if(name, conditional):
        """Condición de obligatoriedad de un campo, o None (obligatorio siempre/nunca
        lo resuelve `required`). Dos fuentes:
          - companion "…_otro": obligatorio cuando se MUESTRA (misma condición de
            visibilidad que ya trae `conditional`);
          - `WORKSHEET_REQUIRED_IF`: obligatorio cuando el disparador está puesto.
        """
        if name.endswith('_otro') and conditional:
            return dict(conditional)
        rule = WORKSHEET_REQUIRED_IF.get(name)
        if rule:
            controller, kind, value = rule
            return {'controller': controller, 'kind': kind, 'trigger': value}
        return None

    def _o2m_fk_name(self, LineModel, Model):
        """Nombre del many2one de la línea que apunta de vuelta a la worksheet."""
        for name, info in LineModel.fields_get().items():
            if info.get('type') == 'many2one' and info.get('relation') == Model._name:
                return name
        return None

    def _o2m_line_rows(self, Model, o2m_name, LineModel, fk_name):
        """Filas de campos de línea, respetando los `<group>` anidados de la subficha.

        Devuelve una lista de filas; cada fila es una lista de (nombre, help, required):
          - un `<field>` suelto → fila de 1 (ancho completo);
          - un `<group>` anidado con 2 campos → fila de 2 (se renderiza en 2 columnas).
        `required` sale del nodo (`required="1"`), igual que en los campos principales.
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
            node_req = fnode.get('required') in ('1', 'True', 'true')
            return (name, fnode.get('help') or '', node_req)

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

    @staticmethod
    def _line_fixed_title(line, line_meta):
        """Etiqueta legible del área de una tarjeta fija (para el encabezado)."""
        info = line_meta.get(LINE_FIXED_TITLE_FIELD)
        if not info:
            return ''
        value = line[LINE_FIXED_TITLE_FIELD]
        if info['type'] == 'selection':
            return dict(info.get('selection') or []).get(value, value or '')
        return value or ''

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
        flat = [it for row in line_rows for it in row]  # [(name, help, req), ...]
        line_names = [n for n, _h, _r in flat]
        seq_field = next(
            (n for n in line_meta if n.endswith('_sequence')
             and line_meta[n]['type'] == 'integer'), None)

        # Disparador de dispensa de esta tarjeta ("cliente no permitió…"): exime a
        # TODOS los campos de la línea menos a sí mismo.
        unless_field = WORKSHEET_REQUIRED_UNLESS_LINE.get(relation)
        if unless_field and unless_field not in line_meta:
            unless_field = None

        def mk(n, h, req, rec_line):
            d = self._scalar_descriptor(
                line_meta[n], n, (rec_line[n] if rec_line else False), h,
                required=req)
            d['conditional_chain'] = self._conditional_chain(n, line_meta)
            d['conditional'] = d['conditional_chain'][0] if d['conditional_chain'] else None
            d['required_if'] = self._required_if(n, d['conditional'])
            # El campo que IDENTIFICA la tarjeta (el área) nunca se dispensa: sin él
            # quedaría una tarjeta anónima. Se exime todo lo demás.
            if unless_field and n not in (unless_field, LINE_FIXED_TITLE_FIELD):
                d['required_unless'] = {
                    'controller': unless_field, 'kind': 'truthy', 'trigger': ''}
            if d['type'] == 'binary':
                d['photos'] = (self._field_photo_ids(relation, rec_line.id, n)
                               if rec_line else [])
            return d

        def build_rows(rec_line):
            """Filas de descriptores (con valores de `rec_line` o en blanco)."""
            return [[mk(n, h, req, rec_line) for (n, h, req) in row]
                    for row in line_rows]

        # Áreas obligatorias: tarjetas que la hoja trae de nacimiento, no se pueden
        # eliminar y van SIEMPRE primero (el técnico las recorre en orden).
        has_fixed = LINE_FIXED_FLAG in line_meta
        lines = []
        if record:
            existing = record[name]
            if seq_field:
                existing = existing.sorted(lambda l: l[seq_field])
            if has_fixed:
                existing = existing.sorted(
                    lambda l: not l[LINE_FIXED_FLAG])  # fijas primero (estable)
            for line in existing:
                fixed = bool(has_fixed and line[LINE_FIXED_FLAG])
                lines.append({
                    'id': line.id,
                    'rows': build_rows(line),
                    'fixed': fixed,
                    # Título de la tarjeta fija: el área ya está decidida, así que se
                    # muestra como encabezado y su campo se emite oculto (ver plantilla).
                    'title': self._line_fixed_title(line, line_meta) if fixed else '',
                })
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
            'conditional_chain': [],
            'required': False,
            'required_if': None,
            'required_unless': None,
            'nested': False,
            # Exige al menos una línea (Req 7). El arch no puede declararlo.
            'min_one': name in WORKSHEET_MIN_ONE,
            # Campo que en las tarjetas FIJAS se pinta como título en vez de como
            # control editable (su valor ya está decidido; va en un hidden).
            'fixed_title_field': LINE_FIXED_TITLE_FIELD if has_fixed else '',
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
            node_req = node.get('required') in ('1', 'True', 'true')
            if ftype == 'one2many':
                desc = self._o2m_descriptor(Model, info, name, record, help_text)
                if desc:
                    descriptors.append(desc)
            else:
                sdesc = self._scalar_descriptor(
                    info, name, record[name] if record else False, help_text,
                    required=node_req)
                sdesc['conditional_chain'] = self._conditional_chain(name, meta)
                sdesc['conditional'] = (sdesc['conditional_chain'][0]
                                        if sdesc['conditional_chain'] else None)
                sdesc['required_if'] = self._required_if(name, sdesc['conditional'])
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

    # ==================================================================
    # Validación de obligatoriedad (Req 7) — misma lógica que el cliente
    # ==================================================================
    @staticmethod
    def _cond_met(cond, get_value, get_list):
        """¿Se cumple la condición de un `required_if`/`conditional`?

        `get_value(field)` devuelve el valor escalar enviado del controlador;
        `get_list(field)` la lista de valores (checkboxes). Sirve tanto para el
        ámbito principal como para una línea (cambian solo esas dos funciones).
        """
        ctrl = cond['controller']
        trigger = str(cond.get('trigger', ''))
        if cond['kind'] == 'truthy':
            return bool(get_value(ctrl))
        if cond['kind'] == 'many2many':
            return trigger in [str(v) for v in get_list(ctrl)]
        return str(get_value(ctrl) or '') == trigger  # selection

    def _field_is_required_now(self, desc, get_value, get_list):
        """¿El campo es obligatorio en el estado ACTUAL del formulario?

        Orden de resolución (el mismo que aplica el cliente):
          1. **Oculto por su condición → nunca obligatorio.** Un campo condicional
             puede traer `required="1"` del arch (así se exige cuando SÍ se muestra),
             así que sin este corte el servidor exigiría campos que el técnico no
             llegó a ver.
          2. **Dispensa por línea** (`required_unless`): p. ej. el cliente no dejó
             tratar el área.
          3. Obligatorio siempre (`required`) o por disparador (`required_if`).
        """
        chain = desc.get('conditional_chain') or (
            [desc['conditional']] if desc.get('conditional') else [])
        if any(not self._cond_met(c, get_value, get_list) for c in chain):
            return False
        unless = desc.get('required_unless')
        if unless and self._cond_met(unless, get_value, get_list):
            return False
        if desc.get('required'):
            return True
        cond_if = desc.get('required_if')
        return bool(cond_if) and self._cond_met(cond_if, get_value, get_list)

    def _main_field_empty(self, task, desc, post, form, files):
        """¿Un campo principal obligatorio quedó vacío?"""
        name, ftype = desc['name'], desc['type']
        if ftype == 'boolean':
            return not post.get(name)              # casilla de confirmación: marcada
        if ftype == 'many2many':
            return not [i for i in form.getlist(name) if str(i).isdigit()]
        if ftype == 'binary':
            # Foto: cuenta si hay ya una en la galería de la tarjeta o llega archivo.
            if files and files.get(name) and files.get(name).filename:
                return False
            return not self._field_photo_ids('project.task', task.id, name)
        return not str(post.get(name) or '').strip()

    def _line_field_empty(self, relation, desc, row, o2m, row_key, form, files):
        """¿Un campo de línea obligatorio quedó vacío en esa tarjeta?"""
        name, ftype = desc['name'], desc['type']
        key = O2M_SEP.join((O2M_LINE_PREFIX, o2m, row_key, name))
        if ftype == 'binary':
            if files and any(u and u.filename for u in files.getlist(key)):
                return False
            raw_id = row.get('id')
            if raw_id and raw_id.isdigit():
                return not self._field_photo_ids(relation, int(raw_id), name)
            return True
        if ftype == 'many2many':
            return not [i for i in form.getlist(key) if str(i).isdigit()]
        if ftype == 'boolean':
            return not row.get(name)
        return not str(row.get(name) or '').strip()

    def _row_has_content(self, d, row, o2m, row_key, files):
        """¿La tarjeta tiene algo (para exigirle sus obligatorios)?

        Una tarjeta EXISTENTE (con id) siempre cuenta. Una tarjeta nueva cuenta si
        el técnico escribió algo o adjuntó una foto; una completamente vacía se
        ignora (al guardar se descarta, ver `_line_vals_has_content`)."""
        raw_id = row.get('id')
        if raw_id and raw_id.isdigit():
            return True
        for name, ftype in d['line_specs']:
            key = O2M_SEP.join((O2M_LINE_PREFIX, o2m, row_key, name))
            if ftype == 'binary':
                if files and any(u and u.filename for u in files.getlist(key)):
                    return True
            elif ftype == 'many2many':
                if [i for i in request.httprequest.form.getlist(key) if str(i).isdigit()]:
                    return True
            elif row.get(name):
                return True
        return False

    def _worksheet_validation_errors(self, task, record, post, files):
        """Lista de etiquetas de campos obligatorios que faltan. Vacía = OK.

        Es la MISMA lógica que valida el cliente (misma fuente: los descriptores);
        aquí es defensa en profundidad ante un POST que se salta el JS.
        """
        form = request.httprequest.form
        rows_by_o2m = self._parse_o2m_rows(post)
        present = self._o2m_present(post)
        errors = []
        for d in self._worksheet_descriptors(task, record):
            if d['type'] == 'one2many':
                if d['name'] not in present:
                    continue
                rows = rows_by_o2m.get(d['name'], {})
                real = {rk: r for rk, r in rows.items()
                        if self._row_has_content(d, r, d['name'], rk, files)}
                if d.get('min_one') and not real:
                    errors.append("%s: agregue al menos uno" % d['string'])
                    continue
                line_descs = [desc for brow in d['blank_rows'] for desc in brow]
                for rk, row in real.items():
                    getv = lambda f, _r=row: _r.get(f)
                    getl = lambda f, _rk=rk: form.getlist(
                        O2M_SEP.join((O2M_LINE_PREFIX, d['name'], _rk, f)))
                    for desc in line_descs:
                        if (self._field_is_required_now(desc, getv, getl)
                                and self._line_field_empty(
                                    d['relation'], desc, row, d['name'], rk, form, files)):
                            errors.append("%s — %s" % (d['string'], desc['string']))
            else:
                getv = post.get
                getl = form.getlist
                if (self._field_is_required_now(d, getv, getl)
                        and self._main_field_empty(task, d, post, form, files)):
                    errors.append(d['string'])
        return errors

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

    @staticmethod
    def _o2m_present(post):
        """Subfichas que SÍ se renderizaron en este formulario (marcador
        `o2mpresent~{campo}`) — distingue "vaciada" de "no incluida"."""
        return {k.split(O2M_SEP, 1)[1] for k in post
                if k.startswith(O2M_PRESENT_PREFIX + O2M_SEP)}

    @staticmethod
    def _parse_o2m_rows(post):
        """Agrupa los inputs de tarjeta: `o2m -> fila -> {campo: valor}`."""
        rows_by_o2m = {}
        for key, value in post.items():
            if not key.startswith(O2M_LINE_PREFIX + O2M_SEP):
                continue
            parts = key.split(O2M_SEP)
            if len(parts) != 4:
                continue
            _prefix, o2m, row, field = parts
            rows_by_o2m.setdefault(o2m, {}).setdefault(row, {})[field] = value
        return rows_by_o2m

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
        present = self._o2m_present(post)
        if not present:
            return
        rows_by_o2m = self._parse_o2m_rows(post)
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
        # Las áreas OBLIGATORIAS no se borran nunca, ni por un POST manipulado que
        # omita su fila: la plantilla ni siquiera les pinta "Eliminar".
        if stale and LINE_FIXED_FLAG in LineModel._fields:
            stale = stale.filtered(lambda l: not l[LINE_FIXED_FLAG])
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
        scope = self._scope(kw.get('scope'))
        tasks = self._employee_tasks(employee, scope=scope)
        # El MAPA siempre es el día de hoy, sea cual sea el alcance de la lista.
        # Con `scope=today` es el mismo recordset (no se vuelve a buscar).
        today_tasks = (tasks if scope == SCOPE_TODAY
                       else self._employee_tasks(employee, scope=SCOPE_TODAY))
        payload = self._task_map_payload(today_tasks)
        route = self._task_route_geometry(payload)
        return request.render('visar_field_app.field_tasks', {
            'employee': employee,
            'tasks': tasks,
            'scope': scope,
            # Número de parada por tarjeta: sale del MISMO cálculo que el mapa,
            # así la tarjeta "3" es el pin "3". Solo en `today`: el número es la
            # posición en la ruta del día, y en `all` (historial, sin arrastre)
            # numerar unas tarjetas sí y otras no solo confunde.
            'stop_numbers': ({p['id']: p['order'] for p in payload if p['order']}
                             if scope == SCOPE_TODAY else {}),
            'task_times': self._task_times(employee, tasks, scope),
            'task_labels': {
                task.id: self._task_state_label(task) for task in tasks},
            # Solo se arrastra la ruta de HOY: en `all` las tarjetas mezclan días
            # (y el mapa no refleja días pasados), así que el orden manual no
            # tendría a qué aplicarse.
            'can_reorder': scope == SCOPE_TODAY,
            'closed_states': CLOSED_STATES,
            'reorder_action': '/visar/field/tasks/reorder',
            'map_tasks_json': json.dumps(payload),
            'map_route_json': json.dumps(route) if route else '',
            'today_count': len(today_tasks),
            'geocoded_count': sum(1 for t in payload if t['has_coords']),
        })

    @http.route('/visar/field/tasks/reorder', type='http', auth='public',
                website=True, methods=['POST'], csrf=True)
    def field_tasks_reorder(self, **post):
        """Guarda el orden de la ruta de hoy tras arrastrar y soltar (fetch).

        Recibe `task_ids` = ids separados por coma, en el orden nuevo, y responde
        JSON con el mapa recalculado (numeración de paradas + ruta Mapbox en el
        orden nuevo) para que el JS repinte el mapa sin recargar la página.

        Solo se aceptan servicios PENDIENTES de HOY del propio técnico: los ids
        que no estén en ese conjunto se ignoran (no se ordena lo ajeno).
        """
        employee = self._current_employee()
        if not employee:
            return request.make_json_response({'error': 'no_session'}, status=403)

        today_tasks = self._employee_tasks(employee, scope=SCOPE_TODAY)
        # Las cerradas no se arrastran (van al final de la lista por su cuenta).
        draggable = {
            task.id for task in today_tasks if task.state not in CLOSED_STATES
        }
        submitted = []
        for raw in (post.get('task_ids') or '').split(','):
            raw = raw.strip()
            if not raw.isdigit():
                continue
            task_id = int(raw)
            if task_id in draggable and task_id not in submitted:
                submitted.append(task_id)
        if not submitted:
            return request.make_json_response({'error': 'empty'}, status=400)

        request.env['visar.field.route.order']._visar_set_order(employee, submitted)

        # Se relee para que el mapa salga del MISMO orden que verá la lista al
        # recargar (y no del orden que asumió el navegador).
        tasks = self._employee_tasks(employee, scope=SCOPE_TODAY)
        payload = self._task_map_payload(tasks)
        return request.make_json_response({
            'tasks': payload,
            'route': self._task_route_geometry(payload) or [],
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

        # Minutos de espera: el valor elegido por el técnico en la tarea; si no ha
        # elegido (0), el parámetro global.
        waiting_minutes = task.visar_waiting_minutes or self._default_waiting_minutes()
        flow_state = self._task_flow_state(task)
        worksheet_available = self._worksheet_available(flow_state)
        # Las fotos ya no son una sección general: cada campo-foto de la hoja de
        # trabajo trae su propia galería (descriptores con clave 'photos').
        #
        # `create` solo cuando la hoja se va a poder CAPTURAR: crear el registro es
        # lo que permite sembrar las tarjetas de área obligatoria con id propio (y
        # así ordenarlas y protegerlas de borrado). En una hoja de solo lectura
        # —servicio cerrado, o aún no comenzado— no se toca nada.
        worksheet_editable = (worksheet_available
                              and not self._worksheet_locked(task, flow_state))
        worksheet = self._worksheet_record(task, create=worksheet_editable)
        values = self._upsell_render_values(task)
        values.update({
            'employee': employee,
            'task': task,
            'has_worksheet_template': bool(task.worksheet_template_id),
            # Los descriptores solo se calculan si la hoja se va a pintar: leen la
            # vista del modelo dinámico y no son gratis.
            'worksheet_fields': (self._worksheet_descriptors(task, worksheet)
                                 if worksheet_available else []),
            'worksheet_available': worksheet_available,
            'worksheet_saved': bool(task.visar_worksheet_saved_at),
            # Servicio cerrado → hoja de solo lectura hasta pulsar "Habilitar edición".
            'worksheet_locked': self._worksheet_locked(task, flow_state),
            'reopen_action': '/visar/field/task/%s/worksheet/reopen' % task.id,
            'ws_error': kw.get('ws_error'),
            'signature_available': self._signature_available(task, flow_state),
            'track_action': '/visar/field/task/%s/track' % task.id,
            'is_signed': bool(task.worksheet_signature),
            'saved': kw.get('saved'),
            'maps_url': self._google_maps_url(task),
            'contact': self._task_contact(task),
            'flow_state': flow_state,
            'waiting_minutes': waiting_minutes,
            # 'Z' marca UTC: Odoo guarda datetimes naive en UTC; sin el marcador el
            # navegador los interpreta como hora LOCAL y desfasa la cuenta regresiva
            # por el offset del huso (p. ej. +6 h en Monterrey → arranca en 360:xx).
            'waiting_start_iso': (task.visar_waiting_start.isoformat() + 'Z'
                                  if task.visar_waiting_start else ''),
            'close_error': kw.get('close_error'),
            'upsell_msg': kw.get('upsell'),
        })
        return request.render('visar_field_app.field_task_detail', values)

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

        # Req 5: no se captura antes de "Comenzar servicio". Defensa en profundidad —
        # la plantilla ni siquiera pinta el formulario, esto ataja un POST directo.
        flow_state = self._task_flow_state(task)
        if not self._worksheet_available(flow_state):
            return request.redirect('/visar/field/task/%s' % task.id)

        # Servicio cerrado sin "Habilitar edición": la hoja es de solo lectura. La
        # plantilla oculta el botón de guardar; esto ataja un POST directo / pestaña
        # vieja (el flag de desbloqueo vive en el servidor, no en el formulario).
        if self._worksheet_locked(task, flow_state):
            return request.redirect('/visar/field/task/%s' % task.id)

        record = self._worksheet_record(task, create=True)
        if record is not None:
            # Req 7: no se guarda una hoja incompleta. El cliente ya valida campo a
            # campo (misma lógica); aquí es defensa en profundidad. Si algo falta NO
            # se escribe nada (ni se avanza la etapa) y se vuelve con ?ws_error=1.
            if self._worksheet_validation_errors(
                    task, record, post, request.httprequest.files):
                return request.redirect(
                    '/visar/field/task/%s?ws_error=1' % task.id)
            vals = self._worksheet_write_values(
                task, record, post, request.httprequest.files)
            if vals:
                record.write(vals)
            self._sync_worksheet_lines(
                task, record, post, request.httprequest.files)
            # Req 6: guardar la hoja habilita la firma (sello _saved_at). La etapa
            # FSM se queda en En ejecución — "Pendiente de firma" se retiró del
            # flujo (el tramo hoja→firma es de segundos/minutos y no aporta a
            # gestión). El sello _saved_at/_by_id es de la PRIMERA vez (auditoría);
            # _last_saved_at se actualiza en CADA guardado (Req 8: con la llegada
            # define el tiempo en sitio del PDF).
            now = fields.Datetime.now()
            ws_vals = {'visar_worksheet_last_saved_at': now}
            if not task.visar_worksheet_saved_at:
                ws_vals['visar_worksheet_saved_at'] = now
                ws_vals['visar_worksheet_saved_by_id'] = employee.id
            task.write(ws_vals)
            # Edición de un servicio cerrado: se re-bloquea (vuelve a solo lectura) y
            # se registra en el chatter. Editar de nuevo exige "Habilitar edición".
            if flow_state == 'cerrado':
                task._visar_worksheet_relock(employee)
        return request.redirect('/visar/field/task/%s?saved=1' % task.id)

    @http.route('/visar/field/task/<int:task_id>/worksheet/reopen', type='http',
                auth='public', website=True, methods=['POST'], csrf=True)
    def field_task_worksheet_reopen(self, task_id, **post):
        """Habilita la edición de la hoja de un servicio cerrado ("Habilitar edición").

        Cualquier técnico asignado puede hacerlo (mismo modelo de acceso que el resto
        de la app); la acción queda en el chatter. Solo tiene efecto si la tarea está
        cerrada — en ejecución la hoja ya es editable."""
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.redirect('/visar/field/tasks')
        task._visar_worksheet_reopen(employee)
        return request.redirect('/visar/field/task/%s' % task.id)

    # ==================================================================
    # Traza de acciones del técnico (Llamar / WhatsApp / Google Maps)
    # ==================================================================
    @http.route('/visar/field/task/<int:task_id>/track', type='http', auth='public',
                methods=['POST'], csrf=True)
    def field_task_track(self, task_id, **post):
        """Registra en el chatter que el técnico pulsó un botón de contacto/mapa.

        La llama `navigator.sendBeacon` (fire-and-forget): el navegador la manda
        aunque la página se esté yendo a `tel:` / `wa.me`, y no bloquea el toque.
        Por eso responde vacío (nadie lee la respuesta) y nunca redirige.
        """
        employee = self._current_employee()
        if not employee:
            return request.make_response('', status=403)
        task = self._task_for_employee(task_id, employee)
        if not task:
            return request.make_response('', status=404)
        task._visar_log_field_action(employee, post.get('action'))
        return request.make_response('', [('Content-Type', 'text/plain')])

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
            # Sella la salida (solo la primera vez, para preservar el momento real
            # de partida si se vuelve a pulsar), calcula la ETA con la ubicación del
            # técnico (Mapbox) si el navegador la envió —si no, valor fijo— y avisa al
            # cliente. Alimenta `visar_travel_minutes`.
            if not task.visar_enroute_at:
                task.visar_enroute_at = fields.Datetime.now()
                eta = task._visar_enroute_eta_minutes(post.get('lat'), post.get('lng'))
                task.visar_enroute_eta_minutes = eta
                task._visar_notify_client(
                    task._visar_msg_enroute(eta, employee), event='enroute')
            task._visar_set_stage(1)  # En camino
        elif action == 'arrived':
            # La espera arranca AUTOMÁTICamente al llegar (antes era un botón manual):
            # se sella el inicio y los minutos por defecto, y se avisa al cliente que
            # tiene esa ventana para recibir. El flujo cae directo en 'esperando'.
            if not task.visar_arrived_at:
                task.visar_arrived_at = fields.Datetime.now()
                minutes = self._default_waiting_minutes()
                task.visar_waiting_minutes = minutes
                task.visar_waiting_start = fields.Datetime.now()
                task._visar_notify_client(
                    task._visar_msg_arrived(minutes, employee), event='arrived')
            task._visar_set_stage(2)  # llegada → directo a En ejecución (etapa FSM)
        elif action == 'start':
            # Registra cuánto se esperó al cliente (de la llegada/espera hasta ahora).
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

        # Req 6: no se cierra sin haber guardado la hoja de trabajo (el formulario
        # de firma ni se pinta antes; esto ataja un POST directo o una pestaña vieja).
        if not self._signature_available(task, self._task_flow_state(task)):
            return request.redirect('/visar/field/task/%s' % task.id)

        # Un carrito de adicionales en BORRADOR es plata en la mesa: productos que
        # el técnico agregó y nadie cobró. Se bloquea el cierre hasta que genere el
        # cobro o los quite. Un pedido ya confirmado y pendiente de pago SÍ deja
        # cerrar: ahí el cobro existe y administración le da seguimiento.
        if task._visar_upsell_state() == 'borrador':
            return request.redirect('/visar/field/task/%s?upsell=pending' % task.id)

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
    # Upsell en campo: catálogo, carrito y cobro
    # ==================================================================
    # Las cinco rutas comparten la misma guarda: técnico identificado + servicio
    # SUYO + servicio EN EJECUCIÓN. La última importa: vender antes de llegar (o
    # después de cerrar) no es upsell en sitio, y la app pública tiene que
    # rechazarlo en el servidor, no solo escondiendo el botón.
    def _upsell_task(self, task_id, employee, require_running=True):
        task = self._task_for_employee(task_id, employee)
        if not task:
            return None
        if require_running and not self._upsell_available(task):
            return None
        return task

    def _upsell_available(self, task):
        """¿Se puede vender en este momento? Misma fase que la hoja de trabajo,
        pero sin incluir 'cerrado': un servicio ya firmado no admite venta nueva
        (el cobro tendría que hacerlo administración)."""
        return self._task_flow_state(task) == 'en_ejecucion'

    def _upsell_render_values(self, task):
        """Valores comunes de la tarjeta de adicionales (detalle y pantalla de cobro)."""
        order = task._visar_upsell_order()
        zone = task._visar_upsell_zone()
        return {
            'upsell_state': task._visar_upsell_state(),
            'upsell_order': order,
            'upsell_lines': (order.order_line.filtered(lambda l: not l.display_type)
                             if order else []),
            'upsell_zone': zone.name if zone else '',
            'upsell_available': self._upsell_available(task),
        }

    @http.route('/visar/field/task/<int:task_id>/upsell', type='http', auth='public',
                website=True, sitemap=False)
    def field_upsell_catalog(self, task_id, **kw):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._upsell_task(task_id, employee)
        if not task:
            return request.redirect('/visar/field/task/%s' % task_id)
        zone = task._visar_upsell_zone()
        return request.render('visar_field_app.field_upsell_catalog', {
            'employee': employee,
            'task': task,
            'catalog': task._visar_upsell_catalog(),
            'upsell_zone': zone.name if zone else '',
            'currency': task._visar_upsell_order().currency_id or request.env.company.currency_id,
        })

    @http.route('/visar/field/task/<int:task_id>/upsell/add', type='http',
                auth='public', website=True, methods=['POST'], csrf=True)
    def field_upsell_add(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._upsell_task(task_id, employee)
        if not task:
            return request.redirect('/visar/field/task/%s' % task_id)
        # El formulario manda `qty~<product_id>` por producto; solo cuentan los > 0,
        # así el técnico puede llenar varios renglones y agregar todo de un tirón.
        added = 0
        for key, value in post.items():
            if not key.startswith('qty~'):
                continue
            raw_id = key.split('~', 1)[1]
            if not raw_id.isdigit():
                continue
            try:
                quantity = int(float(value or 0))
            except (TypeError, ValueError):
                continue
            if quantity <= 0:
                continue
            if task._visar_upsell_add(employee, int(raw_id), quantity):
                added += 1
        if not added:
            return request.redirect('/visar/field/task/%s/upsell?empty=1' % task.id)
        return request.redirect('/visar/field/task/%s?upsell=added' % task.id)

    @http.route('/visar/field/task/<int:task_id>/upsell/remove', type='http',
                auth='public', website=True, methods=['POST'], csrf=True)
    def field_upsell_remove(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._upsell_task(task_id, employee)
        if not task:
            return request.redirect('/visar/field/task/%s' % task_id)
        line_id = (post.get('line_id') or '').strip()
        if line_id.isdigit():
            task._visar_upsell_remove(int(line_id))
        return request.redirect('/visar/field/task/%s' % task.id)

    @http.route('/visar/field/task/<int:task_id>/upsell/charge', type='http',
                auth='public', website=True, methods=['POST'], csrf=True)
    def field_upsell_charge(self, task_id, **post):
        """Confirma el carrito (pedido + factura) y lleva a la pantalla de cobro."""
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._upsell_task(task_id, employee)
        if not task:
            return request.redirect('/visar/field/task/%s' % task_id)
        if not task._visar_upsell_confirm(employee):
            return request.redirect('/visar/field/task/%s' % task.id)
        return request.redirect('/visar/field/task/%s/upsell/pay' % task.id)

    @http.route('/visar/field/task/<int:task_id>/upsell/pay', type='http',
                auth='public', website=True, sitemap=False)
    def field_upsell_pay(self, task_id, **kw):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        # `require_running=False`: si el cobro quedó pendiente y gestión movió la
        # etapa, el técnico todavía debe poder abrir la pantalla para consultarlo.
        task = self._upsell_task(task_id, employee, require_running=False)
        if not task or task._visar_upsell_state() == 'vacio':
            return request.redirect('/visar/field/task/%s' % task_id)
        link = task._visar_upsell_payment_link()
        order = task._visar_upsell_order()
        values = self._upsell_render_values(task)
        values.update({
            'employee': employee,
            'task': task,
            'payment_link': link,
            'has_providers': bool(task._visar_upsell_providers()),
            'invoice': task._visar_upsell_invoice(),
            'wa_link': self._upsell_whatsapp_url(task, link),
            'currency': order.currency_id,
            'status_url': '/visar/field/task/%s/upsell/status' % task.id,
            'sent': kw.get('sent'),
        })
        return request.render('visar_field_app.field_upsell_pay', values)

    @http.route('/visar/field/task/<int:task_id>/upsell/cash', type='http',
                auth='public', website=True, methods=['POST'], csrf=True)
    def field_upsell_cash(self, task_id, **post):
        employee = self._current_employee()
        if not employee:
            return request.redirect('/visar/field')
        task = self._upsell_task(task_id, employee, require_running=False)
        if not task:
            return request.redirect('/visar/field/task/%s' % task_id)
        task._visar_upsell_register_cash(employee)
        return request.redirect('/visar/field/task/%s/upsell/pay' % task.id)

    @http.route('/visar/field/task/<int:task_id>/upsell/status', type='http',
                auth='public', website=True, sitemap=False)
    def field_upsell_status(self, task_id, **kw):
        """Sondeo del pago: la pantalla del QR se refresca sola cuando el cliente
        termina de pagar en su propio teléfono."""
        employee = self._current_employee()
        task = self._upsell_task(task_id, employee, require_running=False) if employee else None
        if not task:
            return request.make_json_response({'error': 'no_session'}, status=403)
        return request.make_json_response({'state': task._visar_upsell_state()})

    @http.route('/visar/field/task/<int:task_id>/upsell/qr', type='http',
                auth='public', website=True, sitemap=False)
    def field_upsell_qr(self, task_id, **kw):
        """PNG del QR con el enlace de pago (generado por el motor nativo de
        códigos de barras, sin depender de un servicio externo)."""
        employee = self._current_employee()
        task = self._upsell_task(task_id, employee, require_running=False) if employee else None
        if not task:
            return request.not_found()
        link = task._visar_upsell_payment_link()
        if not link:
            return request.not_found()
        png = request.env['ir.actions.report'].sudo().barcode(
            'QR', link, width=UPSELL_QR_PX, height=UPSELL_QR_PX, barLevel='M')
        return request.make_response(png, [
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'no-store'),
        ])

    @staticmethod
    def _upsell_whatsapp_url(task, link):
        """Enlace wa.me con el mensaje de cobro ya escrito. El técnico solo pulsa
        enviar: en sitio no hay tiempo de redactar."""
        if not link:
            return ''
        phone = task._visar_client_phone()
        if not phone:
            return ''
        digits = ''.join(ch for ch in phone if ch.isdigit())
        if not digits:
            return ''
        if len(digits) == 10:  # nacional sin lada de país
            digits = '52' + digits
        order = task._visar_upsell_order()
        text = (
            "Hola %s, le comparto el enlace para pagar los productos adicionales "
            "de su servicio de hoy (%s): %s" % (
                task.partner_id.name or '',
                order.currency_id.format(order.amount_total) if order else '',
                link)
        )
        return 'https://wa.me/%s?%s' % (digits, urlencode({'text': text}))

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
    # Reporte nativo (PDF) — previsualización EN LÍNEA desde el backend
    # ==================================================================
    # Sirve el MISMO reporte que la ruta de la app de campo, pero para usuarios
    # internos (botón "Reporte (PDF)" de la vista de tarea). En línea (no descarga
    # directa): el navegador lo abre en su visor y desde ahí se puede descargar.
    @http.route('/visar/report/task/<int:task_id>/worksheet', type='http',
                auth='user', website=False, sitemap=False)
    def backend_task_worksheet_report(self, task_id, **kw):
        task = request.env['project.task'].browse(task_id).exists()
        # ACL: el usuario debe poder leer la tarea (no basta con estar logueado).
        # El render se hace en sudo por la maqueta enriquecida, pero solo tras
        # comprobar el acceso del usuario a ESTA tarea.
        if not task:
            raise request.not_found()
        task.check_access('read')
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
