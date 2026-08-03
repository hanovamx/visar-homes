# -*- coding: utf-8 -*-
"""Superficie RPC de solo lectura para el agente de WhatsApp.

Tres metodos, parametros tipados, sin nombres de modelo ni dominios.

La cotizacion NO se reimplementa: se construyen los mismos `items` que arma el
wizard web y se pasan al motor de precios que ya existe,
appointment.type._visar_quote_booking(). Asi el precio del agente es, por
construccion, identico al de la web -incluida la variante combinada de
fumigacion interior+exterior (la rejilla zona x m2 interior x m2 exterior),
los descuentos de combo entre servicios, los add-ons obligatorios y los
tramos incluidos sin cargo.

Los metodos NO usan sudo: corren como el usuario RPC, asi que las ACLs del
grupo de solo lectura son el limite efectivo.
"""
import logging

from odoo import api, fields, models
from odoo.tools import format_datetime

_logger = logging.getLogger(__name__)

# Notas de negocio para el prompt, editables sin tocar codigo desde
# Ajustes > Tecnico > Parametros del sistema.
NOTES_PARAM = 'visar.agent.catalog_notes'

# Zona horaria para las fechas que se muestran al cliente. Visar opera en
# Monterrey; editable sin tocar codigo desde Parametros del sistema.
TZ_PARAM = 'visar.agent.timezone'
DEFAULT_TZ = 'America/Monterrey'

# Idioma en que se redactan las fechas ("5 de agosto, 10:00 h").
SERVICES_LANG = 'es_MX'

# Estados de orden de venta que cuentan como "servicio real" (no un presupuesto
# a medias ni una cancelacion). draft/sent = presupuesto; cancel = cancelada.
CONFIRMED_ORDER_STATES = ('sale', 'done')

# Tope de servicios que se devuelven, para no armar un mensaje kilometrico.
MAX_SERVICES = 10

# Alcance de agent_customer_services: 'upcoming' = proximos o pendientes de
# agendar; 'history' = ya realizados, pasados o cancelados; 'all' = ambos.
SERVICE_SCOPES = ('upcoming', 'history', 'all')
DEFAULT_SERVICE_SCOPE = 'upcoming'


class VisarAgentTools(models.AbstractModel):
    _name = 'visar.agent.tools'
    _description = "API de solo lectura para el agente de WhatsApp"

    # ------------------------------------------------------------------
    # Helpers de catalogo
    # ------------------------------------------------------------------

    @api.model
    def _agent_tier_label(self, tier):
        if tier.name:
            return tier.name
        return "%g - %g m2" % (tier.m2_min, tier.m2_max)

    @api.model
    def _agent_tier_payload(self, tier):
        return {
            'label': self._agent_tier_label(tier),
            'm2_min': tier.m2_min,
            'm2_max': tier.m2_max,
            'is_free': tier.is_free,
            'is_valuation': tier.is_valuation,
        }

    @api.model
    def _agent_resolve_dimension(self, service_code):
        """Codigo -> dimension. Acepta codigo de dimension o de grupo.

        Devuelve (dimension, opciones). `opciones` solo viene lleno cuando el
        codigo era de un grupo con mas de una dimension: en ese caso NO se
        puede cotizar sin saber cual, porque cada dimension tiene su tabulador.
        """
        Dimension = self.env['visar.service.dimension']
        Group = self.env['visar.service.group']
        code = (service_code or '').strip()
        empty = Dimension.browse()
        if not code:
            return empty, []

        dimension = Dimension.search([('code', '=ilike', code)], limit=1)
        if dimension:
            return dimension, []

        group = Group.search([('code', '=ilike', code)], limit=1)
        if not group:
            return empty, []

        dimensions = group.dimension_ids.filtered('active')
        if len(dimensions) == 1:
            return dimensions, []
        return empty, [
            {'code': d.code, 'name': d._visar_wizard_label()} for d in dimensions
        ]

    # ------------------------------------------------------------------
    # 1. Catalogo
    # ------------------------------------------------------------------

    @api.model
    def agent_catalog_snapshot(self):
        """Estructura del catalogo para el system prompt (sin precios ni CPs)."""
        Template = self.env['product.template']
        groups_payload = []

        for group in self.env['visar.service.group'].search([]):
            dimensions_payload = []
            for dimension in group.dimension_ids.filtered('active'):
                template = Template._visar_get_service_template_for_dimension(dimension)
                tiers = (
                    template._visar_tiers_for_dimension(dimension)
                    if template else self.env['visar.service.tier'].browse()
                )
                dimensions_payload.append({
                    'code': dimension.code,
                    'name': dimension._visar_wizard_label(),
                    'measure_type': dimension.measure_type,
                    'tiers': [self._agent_tier_payload(t) for t in tiers],
                })
            groups_payload.append({
                'code': group.code,
                'name': group._visar_wizard_label(),
                'description': group.wizard_help or '',
                'dimensions': dimensions_payload,
            })

        zones_payload = [
            {'code': zone.code, 'name': zone.name}
            for zone in self.env['visar.zone'].search([])
        ]

        notes = self.env['ir.config_parameter'].sudo().get_param(NOTES_PARAM, '')

        return {
            'generated_at': fields.Datetime.now().isoformat(),
            'groups': groups_payload,
            'zones': zones_payload,
            'notes': notes,
        }

    # ------------------------------------------------------------------
    # 1b. Configuracion de runtime (prompt editable + knobs del LLM)
    # ------------------------------------------------------------------

    @api.model
    def agent_runtime_config(self):
        """Config editable del runtime: prompt del sistema + knobs del LLM.

        NO devuelve secretos: las credenciales del LLM y de WhatsApp siguen en el
        `.env` del runtime. `prompt` es None si no hay ninguno configurado -> el
        runtime cae a su BASE_PROMPT de respaldo. Las notas del negocio NO van
        aqui: ya viajan en `agent_catalog_snapshot` y se renderizan una sola vez.
        """
        return {
            'generated_at': fields.Datetime.now().isoformat(),
            'prompt': self.env['visar.agent.prompt']._agent_active_body(),
            'llm': self.env['visar.llm.config']._agent_active_payload(),
        }

    # ------------------------------------------------------------------
    # 2. Cobertura por codigo postal
    # ------------------------------------------------------------------

    @api.model
    def agent_resolve_zone(self, cp):
        """Codigo postal -> zona Visar. No revela la lista completa de CPs."""
        ZoneCp = self.env['visar.zone.cp']
        normalized = ZoneCp._normalize_cp(cp)

        if len(normalized) != 5:
            return {
                'cp': normalized, 'served': False, 'zone_code': None,
                'zone_name': None, 'message': "El codigo postal debe tener 5 digitos.",
            }

        record = ZoneCp._get_cp_record(normalized)
        zone = record.zone_id
        if not zone:
            return {
                'cp': normalized, 'served': False, 'zone_code': None,
                'zone_name': None, 'municipality': record.municipality or None,
                'message': (
                    "El CP %s no esta dentro de la cobertura actual. "
                    "Conviene canalizarlo con un asesor." % normalized
                ),
            }
        return {
            'cp': normalized, 'served': True, 'zone_code': zone.code,
            'zone_name': zone.name, 'municipality': record.municipality or None,
            'message': "El CP %s pertenece a %s." % (normalized, zone.name),
        }

    # ------------------------------------------------------------------
    # 3. Cotizacion (reutiliza el motor del wizard)
    # ------------------------------------------------------------------

    @api.model
    def _agent_normalize_segments(self, payload):
        """Acepta {service_code, m2} o {items:[{service_code, m2}, ...]}."""
        payload = payload or {}
        if payload.get('items'):
            return list(payload['items'])
        if payload.get('service_code'):
            return [{'service_code': payload.get('service_code'), 'm2': payload.get('m2')}]
        return []

    @api.model
    def _agent_build_items(self, segments):
        """Construye los `items` del wizard a partir de (dimension, m2).

        Devuelve (items, error). `error` es un dict de respuesta listo para
        devolver (clarificacion, servicio inexistente, m2 faltantes o fuera de
        tabulador); en ese caso items viene vacio.
        """
        Template = self.env['product.template']
        items = []
        for seg in segments:
            code = seg.get('service_code')
            try:
                m2 = float(seg.get('m2') or 0.0)
            except (TypeError, ValueError):
                m2 = 0.0

            dimension, options = self._agent_resolve_dimension(code)
            if options:
                names = ", ".join(o['name'] for o in options)
                return [], {
                    'needs_clarification': True,
                    'options': options,
                    'message': (
                        "'%s' abarca varias opciones con tabulador distinto: %s. "
                        "Pregunta al cliente cual antes de cotizar." % (code, names)
                    ),
                }
            if not dimension:
                return [], {'message': "No existe el servicio '%s'." % code}
            if m2 <= 0:
                return [], {
                    'message': "Faltan los metros cuadrados de %s."
                    % dimension._visar_wizard_label()
                }

            template = Template._visar_get_service_template_for_dimension(dimension)
            if not template:
                return [], {
                    'message': "El servicio '%s' no tiene producto configurado."
                    % dimension._visar_wizard_label()
                }
            tier = template._visar_tier_for_dimension_m2(dimension, m2)
            if not tier:
                return [], {
                    'message': (
                        "Con %g m2 no aplica ningun tramo de %s; hace falta una "
                        "visita de valoracion." % (m2, dimension._visar_wizard_label())
                    )
                }

            items.append({
                'dimension_id': dimension.id,
                'tier_id': tier.id,
                'tier_name': tier.name or self._agent_tier_label(tier),
                'variant_id': None,   # lo resuelve por zona el motor de precios
                'product_tmpl_id': template.id,
                'is_valuation': tier.is_valuation,
                'is_free': tier.is_free,
            })
        return items, None

    @api.model
    def agent_quote_service(self, payload):
        """(servicios, CP, m2) -> lineas y total, con el motor del wizard.

        `payload` = {"service_code": str, "cp": str, "m2": float}
                    o {"cp": str, "items": [{"service_code": str, "m2": float}, ...],
                       "include_roedores": bool}

        Un solo servicio de fumigacion interior + exterior se cotiza como UNA
        variante combinada (no la suma de dos). Varios servicios distintos
        aplican los descuentos de combo y los add-ons obligatorios que
        correspondan. Nunca devuelve un total a medias: si falta zona, si el
        codigo es ambiguo, o si algo exige valoracion, lo dice en `message`.
        """
        base = {
            'served': False,
            'zone_code': None,
            'currency': self.env.company.currency_id.name,
            'is_valuation': False,
            'needs_clarification': False,
            'options': [],
            'lines': [],
            'total': None,
        }

        payload = payload or {}
        segments = self._agent_normalize_segments(payload)
        if not segments:
            return {**base, 'message': "No se indico ningun servicio a cotizar."}

        # Zona (una sola para todo el basket).
        zone_info = self.agent_resolve_zone(payload.get('cp'))
        if not zone_info['served']:
            return {**base, 'message': zone_info['message']}
        zone = self.env['visar.zone'].search(
            [('code', '=', zone_info['zone_code'])], limit=1)
        base['zone_code'] = zone.code

        # Items del wizard.
        items, error = self._agent_build_items(segments)
        if error:
            return {**base, **error}

        include_roedores = bool(payload.get('include_roedores'))
        quote = self.env['appointment.type']._visar_quote_booking(
            items, zone, include_roedores=include_roedores)

        if not quote:
            return {**base, 'message': "No se pudo calcular el precio con esos datos."}

        currency = self.env['res.currency'].browse(quote['currency_id']).exists()
        currency_name = currency.name if currency else base['currency']
        is_valuation = any(it['is_valuation'] for it in items)

        lines = [
            {
                'name': line['name'],
                'quantity': line['quantity'],
                'unit_price': line['unit_price'],
                'price': line['price'],
                'is_free': line['is_free'],
                'is_addon': line['is_addon'],
                'discount': line['discount'],
            }
            for line in quote['lines']
        ]

        if is_valuation:
            message = (
                "Alguno de los servicios requiere visita de valoracion tecnica "
                "para poder cotizar."
            )
        else:
            message = "Total estimado en %s: %s %.2f." % (
                zone.name, currency_name, quote['total'])

        return {
            **base,
            'served': True,
            'currency': currency_name,
            'is_valuation': is_valuation,
            'lines': lines,
            'total': quote['total'],
            'message': message,
        }

    # ------------------------------------------------------------------
    # 4. Servicios agendados de un cliente (identidad por telefono)
    # ------------------------------------------------------------------
    #
    # A diferencia de los metodos de catalogo/precio, este cruza datos de
    # cliente (res.partner, sale.order, calendar.event, project.task) que el
    # grupo de solo lectura del agente NO ve por ACL, a proposito. Por eso las
    # lecturas van con sudo() ACOTADO: solo este metodo, y devuelve un dict
    # tipado y minimo (nombre + lista de servicios). El usuario RPC no gana
    # acceso a esos modelos por ACL; no puede leerlos de forma arbitraria. Es
    # el cruce deliberado y contenido de la regla "sin datos de cliente".
    # Ver `29-whatsapp-agent-routing-design.md` §"Servicio existente".

    @api.model
    def _agent_normalize_phone(self, phone):
        """Deja los ultimos 10 digitos: el numero nacional MX.

        WhatsApp entrega algo como `5218112345678` (52 de pais + el `1` de movil
        + 10 digitos); los campos de telefono en Odoo son texto libre. Comparar
        por los ultimos 10 digitos esquiva el prefijo de pais y el `1` de movil.
        Se usa para el numero ENTRANTE; el lado guardado se normaliza en SQL
        dentro de `_agent_find_partner` (asi cubre cualquier formato).
        """
        digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
        return digits[-10:] if len(digits) >= 10 else digits

    @api.model
    def _agent_find_partner(self, phone):
        """Resuelve telefono -> res.partner por numero nacional (ultimos 10 digitos).

        - `res.partner.mobile` NO existe en Odoo 19 (se elimino): solo se usa
          `phone`.
        - El lado guardado se normaliza EN SQL (`regexp_replace` quita todo lo que
          no sea digito), asi el match funciona sin importar el formato guardado
          (espacios, guiones, `+52`, el `1` de movil). Es lo unico que cubre todos
          los formatos; el ORM con `ilike` se perderia los que traen separadores.
        - Politica ante AMBIGUEDAD (privacidad): si mas de un partner comparte el
          numero, NO se devuelve ninguno. Este metodo usa sudo() para saltar las
          ACL y leer ventas/citas/tareas, asi que un match equivocado seria
          mostrarle a un cliente los servicios de OTRA persona. Ante duda, no
          revelar (se canaliza a un asesor). Se registra en el log para el staff.
        """
        nat = self._agent_normalize_phone(phone)
        if len(nat) != 10:
            return self.env['res.partner'].browse()

        # Numero nacional guardado, en cualquier formato, que termine en `nat`
        # (equivale a comparar los ultimos 10 digitos: cubre +52 y el `1` de movil).
        self.env.cr.execute(
            """
            SELECT id FROM res_partner
            WHERE regexp_replace(COALESCE(phone, ''), '[^0-9]', '', 'g') LIKE %s
            """,
            ['%' + nat],
        )
        ids = [row[0] for row in self.env.cr.fetchall()]
        if len(ids) != 1:
            if len(ids) > 1:
                _logger.info(
                    "agent_customer_services: el telefono terminado en %s coincide "
                    "con %d partners; se omite por ambiguedad (posible dato "
                    "duplicado). Ante duda no se revelan servicios.",
                    nat[-4:], len(ids))
            return self.env['res.partner'].browse()
        return self.env['res.partner'].sudo().browse(ids[0])

    @api.model
    def _agent_service_date(self, line):
        """Fecha del servicio: la de la cita, o la planeada de la tarea FSM."""
        event = line.calendar_event_id
        if event and event.start:
            return event.start
        task = line.task_id
        if task and task.planned_date_begin:
            return task.planned_date_begin
        return False

    @api.model
    def _agent_service_status(self, line, date, now):
        """Estado legible del servicio.

        Si hay tarea FSM con etapa, se usa su nombre (es el estado real que ve el
        staff). Si no, se deriva de la fecha: futura = Programada, pasada =
        Realizada, sin fecha = Pendiente de agendar.
        """
        task = line.task_id
        if task and task.stage_id:
            return task.stage_id.name
        if not date:
            return "Pendiente de agendar"
        return "Programada" if date >= now else "Realizada"

    @api.model
    def _agent_format_date(self, date, tz):
        """Fecha UTC -> texto en espanol y zona horaria local, o None."""
        if not date:
            return None
        return format_datetime(
            self.env, date, tz=tz, dt_format="d 'de' MMMM y, HH:mm 'h'",
            lang_code=SERVICES_LANG)

    @api.model
    def _agent_service_bucket(self, line, date, today_start):
        """Clasifica un servicio en 'upcoming' o 'history'.

        history = cerrado (etapa FSM con fold=True: Completado / Cancelado) o de
        fecha pasada. upcoming = lo demas (proximo, en curso o sin fecha).
        """
        task = line.task_id
        if task and task.stage_id and task.stage_id.fold:
            return 'history'
        if date and date < today_start:
            return 'history'
        return 'upcoming'

    @api.model
    def _agent_partner_services(self, partner, scope=DEFAULT_SERVICE_SCOPE):
        """Servicios del cliente segun `scope`, ordenados por fecha.

        Recorre las ordenes CONFIRMADAS del cliente -> lineas de servicio Visar ->
        cita (calendar.event) y tarea FSM (project.task).

          - 'upcoming' (default): proximos o pendientes de agendar.
          - 'history': ya realizados, pasados o cancelados.
          - 'all': ambos.
        """
        now = fields.Datetime.now()
        today_start = fields.Datetime.start_of(now, 'day')
        tz = self.env['ir.config_parameter'].sudo().get_param(TZ_PARAM, DEFAULT_TZ)

        # El usuario RPC puede estar en en_US; se leen las ordenes (y todo lo que
        # cuelga: nombre del servicio, etapa FSM) en es_MX para que el cliente no
        # reciba mensajes mezclados. La fecha ya fija su idioma aparte.
        orders = self.env['sale.order'].with_context(lang=SERVICES_LANG).sudo().search([
            ('partner_id', '=', partner.id),
            ('state', 'in', CONFIRMED_ORDER_STATES),
        ])

        entries = []
        for line in orders.mapped('order_line'):
            if not line.product_id.visar_is_service:
                continue
            date = self._agent_service_date(line)
            bucket = self._agent_service_bucket(line, date, today_start)
            if scope != 'all' and bucket != scope:
                continue
            event = line.calendar_event_id
            entries.append({
                'service': line.product_id.display_name,
                'date': date.isoformat() if date else None,
                'date_label': self._agent_format_date(date, tz),
                'status': self._agent_service_status(line, date, now),
                'zone': event.visar_zone_id.name if event and event.visar_zone_id else None,
                '_sort': date or fields.Datetime.end_of(now, 'year'),
            })

        # Historial: mas reciente primero. Proximos: mas cercano primero, con los
        # pendientes sin fecha al final (su clave de orden es un futuro lejano).
        entries.sort(key=lambda e: e['_sort'], reverse=(scope == 'history'))
        for entry in entries:
            del entry['_sort']
        return entries[:MAX_SERVICES]

    @api.model
    def agent_customer_services(self, payload):
        """Servicios de un cliente, identificado por su telefono.

        `payload` = {"phone": "5218112345678", "scope": "upcoming"|"history"|"all"}
                    `scope` es opcional (default "upcoming").

        Devuelve:
            {
              "found": bool,             # se encontro al cliente por telefono
              "partner_name": str|None,
              "scope": str,              # el alcance efectivamente aplicado
              "services": [{
                  "service": str,        # nombre del servicio
                  "date": str|None,      # ISO 8601 UTC, o None si sin agendar
                  "date_label": str|None,# fecha ya redactada en tz/idioma local
                  "status": str,         # Programada / Realizada / etapa FSM / ...
                  "zone": str|None,      # zona Visar de la cita
              }, ...],
              "message": str,            # resumen/fallback listo para mostrar
            }

        Con scope="upcoming" trae proximos/pendientes; con "history" el historico
        (realizados, pasados o cancelados); con "all" ambos. Solo lectura, con
        sudo() acotado (ver nota de seccion). No revela datos de otros clientes:
        se limita a los del telefono dado.
        """
        payload = payload or {}
        scope = payload.get('scope') or DEFAULT_SERVICE_SCOPE
        if scope not in SERVICE_SCOPES:
            scope = DEFAULT_SERVICE_SCOPE

        partner = self._agent_find_partner(payload.get('phone'))
        if not partner:
            return {
                'found': False,
                'partner_name': None,
                'scope': scope,
                'services': [],
                'message': (
                    "No encontre servicios asociados a este numero. "
                    "Conviene canalizarlo con un asesor."
                ),
            }

        services = self._agent_partner_services(partner, scope)
        name = partner.name or "El cliente"
        if not services:
            if scope == 'history':
                message = "%s no tiene servicios anteriores registrados." % name
            else:
                message = "%s no tiene servicios agendados por ahora." % name
        else:
            message = "%d servicio(s) para %s." % (len(services), partner.name)

        return {
            'found': True,
            'partner_name': partner.name or None,
            'scope': scope,
            'services': services,
            'message': message,
        }
