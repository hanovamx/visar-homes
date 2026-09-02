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

import pytz
from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.addons.visar_appointment.models.appointment_wizard_flow import (
    VISAR_STEP_ADDRESS,
)
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

# --- Seguimiento CRM (agent_track_lead) -----------------------------------
# XMLIDs del pipeline WhatsApp; viven en el modulo visar_crm (dependencia).
# El agente SOLO crea/refresca en 'Nuevo'; el avance lo hace Odoo por eventos
# reales (ver .context/32-whatsapp-crm-lead-implementation.md).
WA_TEAM_XMLID = 'visar_crm.crm_team_whatsapp'
WA_STAGE_NUEVO_XMLID = 'visar_crm.crm_stage_wa_nuevo'
WA_STAGE_CERRADO_XMLID = 'visar_crm.crm_stage_wa_cerrado'


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

        # Los combos, con sus condiciones. El catalogo no los mencionaba: la
        # unica aparicion de la palabra era dentro del nombre de un producto. Sin
        # esto el modelo no tiene forma de saber que conviene preguntar por la
        # cobertura antes de cotizar, y cotiza de menos sin equivocarse en nada.
        combos_payload = [
            {
                'name': rule.name,
                'requires': [{'service_code': dim.code, 'name': dim._visar_wizard_label()}
                             for dim in rule.required_dimension_ids],
                'discounts': [{'service_code': dim.code, 'name': dim._visar_wizard_label()}
                              for dim in rule.discount_dimension_ids],
                'discount_percent': rule._visar_discount_percent(),
            }
            for rule in self.env['visar.combo.rule'].sudo().search(
                [('active', '=', True)], order='sequence')
        ]

        return {
            'generated_at': fields.Datetime.now().isoformat(),
            'groups': groups_payload,
            'zones': zones_payload,
            'combos': combos_payload,
            'notes': notes,
        }

    # ------------------------------------------------------------------
    # 1b. Configuracion de runtime (prompt editable + knobs del LLM)
    # ------------------------------------------------------------------

    @api.model
    def agent_runtime_config(self):
        """Config editable del runtime: prompt del sistema + knobs del LLM.

        NO devuelve secretos: las credenciales del LLM y de WhatsApp siguen en el
        `.env` del runtime. Las notas del negocio NO van aqui: ya viajan en
        `agent_catalog_snapshot` y se renderizan una sola vez.

        Contrato, para que el runtime pueda apoyarse en el:

          `prompt`        str | None. Cuerpo del registro BASE (`ruta` vacia).
                          None = no hay ninguno -> el runtime cae a su
                          BASE_PROMPT de respaldo. Significado SIN CAMBIOS.
          `route_prompts` dict[str, str]. SIEMPRE presente, SIEMPRE dict, nunca
                          None. Claves entre {reception, info, schedule,
                          existing, other}; valores, cadenas no vacias. Una ruta
                          sin registro, archivada o en blanco esta AUSENTE, no
                          presente con None.
          `llm`           dict. Sin cambios ({} si no hay config).

        Las dos direcciones de compatibilidad:

          Odoo nuevo + runtime viejo -> `route_prompts` sobra y se ignora; el
            runtime solo mira `prompt` y `llm`. Inerte.
          Runtime nuevo + Odoo viejo -> `config.get("route_prompts") or {}`: sin
            memorias, se comporta como antes. Degrada, no falla.

        Por eso se despliega Odoo primero: el caso inerte es mas corto que el
        degradado. Y despues, `POST /debug/runtime/refresh` — si no, el runtime
        sirve la config anterior hasta que caduque su TTL (15 min).
        """
        Prompt = self.env['visar.agent.prompt']
        return {
            'generated_at': fields.Datetime.now().isoformat(),
            'prompt': Prompt._agent_active_body(),
            'route_prompts': Prompt._agent_route_memories(),
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
    # 2b. Estimacion de m2 de construccion
    # ------------------------------------------------------------------

    @api.model
    def agent_estimate_m2(self, payload):
        """Proxies de la casa -> m2 de construccion, con la cuenta del wizard.

        `payload` = {"rec": int, "ban": int, "niv": int, "gar": int,
                     "predio": float}

        Reutiliza visar.estimator.factor._visar_estimate_interior_m2(), la
        misma que usa el wizard web, para que la web y WhatsApp no den numeros
        distintos por la misma casa.

        El modelo NO debe hacer esta cuenta a mano. La formula lleva un factor
        por tamano de predio (0.72 a 1.70) y dos topes de coherencia contra el
        predio; transcrita en prosa se pierden, y sin el predio la estimacion
        no alcanza nunca el tramo de valoracion (harian falta ~24 recamaras).

        `predio` es opcional pero cambia mucho el resultado: conviene pedirlo.
        """
        payload = payload or {}

        def _num(key, default=0):
            try:
                return max(int(float(payload.get(key) or default)), 0)
            except (TypeError, ValueError):
                return default

        rec = _num('rec')
        ban = _num('ban')
        niv = max(_num('niv', 1), 1)
        gar = _num('gar')
        try:
            predio = max(float(payload.get('predio') or 0.0), 0.0)
        except (TypeError, ValueError):
            predio = 0.0

        if rec <= 0:
            return {
                'm2': None, 'is_valuation': False, 'tier_label': None,
                'predio_usado': False,
                'message': "Falta el numero de recamaras para poder estimar.",
            }

        m2 = self.env['visar.estimator.factor']._visar_estimate_interior_m2(
            rec, ban, niv, gar, predio)

        # Tramo de fumigacion interior para ese m2: es lo que decide si la
        # casa se cotiza o se va a valoracion tecnica.
        dimension = self.env['visar.service.dimension'].search(
            [('active', '=', True), ('measure_type', '=', 'interior')], limit=1)
        tier = self.env['visar.service.tier'].browse()
        if dimension:
            template = self.env['product.template'].\
                _visar_get_service_template_for_dimension(dimension)
            if template:
                tier = template._visar_tier_for_dimension_m2(dimension, m2)

        is_valuation = bool(tier and tier.is_valuation)
        if is_valuation:
            message = (
                "Alrededor de %d m2 de construccion. Con esa superficie hace "
                "falta una visita de valoracion tecnica; no hay precio de "
                "lista." % m2
            )
        else:
            message = (
                "Alrededor de %d m2 de construccion. Confirmalo con el cliente "
                "antes de cotizar." % m2
            )

        return {
            'm2': m2,
            'is_valuation': is_valuation,
            'tier_label': (self._agent_tier_label(tier) if tier else None),
            'predio_usado': predio > 0,
            'message': message,
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
                # Decirle al modelo cuales SI existen: sin esto no puede
                # corregirse solo y repite el codigo malo hasta agotar las
                # iteraciones. Ver hallazgo FUM_INT del 31-ago-2026.
                validos = self.env['visar.service.dimension'].search(
                    [('active', '=', True)]).mapped('code')
                return [], {'message': (
                    "No existe el servicio '%s'. Los codigos validos son: %s."
                    % (code, ", ".join(sorted(validos)) or "ninguno")
                )}
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

        `combos_disponibles` son los combos que esta canasta NO alcanza y que
        estan a una dimension de distancia, con lo que falta y lo que se
        ahorraria. El total devuelto es correcto para lo que se pidio; esto dice
        si habia una canasta mejor. Sin el, una fumigacion de solo interior mas
        areas verdes se cotiza sin el 50% del corte y nadie lo nota.

        Cada linea lleva `list_price` ademas de `unit_price`: el segundo ya viene
        NETO del descuento, asi que sin el primero no se puede enseniar el
        "antes/ahora" y recalcularlo a mano lo aplicaria dos veces.
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
            'combos_disponibles': [],
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
                'list_price': line['list_price'],
                'price': line['price'],
                'is_free': line['is_free'],
                'is_addon': line['is_addon'],
                'discount': line['discount'],
            }
            for line in quote['lines']
        ]

        # Combos que esta canasta se esta perdiendo. Los calcula Odoo, que es
        # donde vive `visar.combo.rule`: el modelo no puede saber que el descuento
        # del corte exige interior Y exterior, y el catalogo no se lo decia.
        combos = [] if is_valuation else self.env['appointment.type']._visar_combo_offers(
            items, zone)

        if is_valuation:
            message = (
                "Alguno de los servicios requiere visita de valoracion tecnica "
                "para poder cotizar."
            )
        else:
            message = "Total estimado en %s: %s %.2f." % (
                zone.name, currency_name, quote['total'])
            for combo in combos:
                falta = ", ".join(m['name'] for m in combo['missing'])
                if combo['discounts']:
                    detalle = "; ".join(
                        "%s pasa de %s %.2f a %s %.2f" % (
                            d['name'], currency_name, d['list_price'],
                            currency_name, d['price'])
                        for d in combo['discounts'])
                    # Solo HECHOS, y en un castellano que el cliente pueda leer
                    # tal cual. El prompt manda "usa solo lo que devuelve la
                    # herramienta", asi que todo lo que entre aqui puede acabar
                    # citado en el chat: una instruccion para el modelo ("vuelve
                    # a cotizar") se leeria como si el negocio hablara solo.
                    # "se cobra aparte" hace el mismo trabajo -impide concluir
                    # que el total baja- y delante de un cliente es verdad.
                    message += (
                        " Anadiendo %s se activa '%s' (%.0f%%): %s. %s se cobra"
                        " aparte." % (
                            falta, combo['name'], combo['discount_percent'],
                            detalle, falta))
                else:
                    sobre = ", ".join(d['name'] for d in combo['discount_services'])
                    message += (
                        " Anadiendo %s se activaria '%s': %.0f%% de descuento"
                        " sobre %s, y nada mas. %s se cobra aparte."
                        % (falta, combo['name'], combo['discount_percent'],
                           sobre or combo['name'], falta))

        return {
            **base,
            'served': True,
            'currency': currency_name,
            'is_valuation': is_valuation,
            'lines': lines,
            'total': quote['total'],
            'combos_disponibles': combos,
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
        """Deja los ultimos 10 digitos: el numero nacional MX, o '' si no llega.

        WhatsApp entrega algo como `5218112345678` (52 de pais + el `1` de movil
        + 10 digitos); comparar por los ultimos 10 digitos esquiva el prefijo de
        pais y el `1` de movil. Delega en `res.partner._visar_phone_nat10_value`
        -la regla ES la misma que usa el dedupe de reservas y el campo indexado
        `visar_phone_nat10`- para que las dos nociones de "mismo numero" no puedan
        divergir. Devuelve '' (no False) para conservar el contrato de str.
        """
        return self.env['res.partner']._visar_phone_nat10_value(phone) or ''

    @api.model
    def _agent_find_partner(self, phone):
        """Resuelve telefono -> res.partner por numero nacional (ultimos 10 digitos).

        - Busca por igualdad indexada sobre `visar_phone_nat10` (campo almacenado
          en `res.partner`, ultimos 10 digitos del telefono). Es la MISMA clave que
          usa el dedupe de reservas, normalizada en un solo sitio
          (`_visar_phone_nat10_value`), asi que el agente y las reservas no pueden
          tener nociones distintas de "mismo numero". Antes se hacia un scan con
          `regexp_replace` sobre toda la tabla; ahora es un lookup indexado.
        - `res.partner.mobile` NO existe en Odoo 19: solo se usa `phone`.
        - Politica ante AMBIGUEDAD (privacidad): si mas de un partner comparte el
          numero, NO se devuelve ninguno. Este metodo usa sudo() para saltar las
          ACL y leer ventas/citas/tareas, asi que un match equivocado seria
          mostrarle a un cliente los servicios de OTRA persona. Ante duda, no
          revelar (se canaliza a un asesor). Se registra en el log para el staff.
        """
        key = self.env['res.partner']._visar_phone_nat10_value(phone)
        if not key:
            return self.env['res.partner'].browse()

        partners = self.env['res.partner'].sudo().search(
            [('visar_phone_nat10', '=', key)])
        if len(partners) != 1:
            if len(partners) > 1:
                _logger.info(
                    "agent_customer_services: el telefono terminado en %s coincide "
                    "con %d partners; se omite por ambiguedad (posible dato "
                    "duplicado). Ante duda no se revelan servicios.",
                    key[-4:], len(partners))
            return self.env['res.partner'].browse()
        return partners

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
    def _agent_window_label(self, start, stop):
        """'21 de agosto de 2026, entre 15:00 y 16:00' — ventana, no hora exacta.

        Al cliente se le da una VENTANA de llegada (decision 15 del diseno 33): el
        bloque son 20 min de traslado + 40 de servicio, asi que prometer una hora
        en punto es prometer algo que la calle no respeta.

        En la zona de Visar y en espanol, pase lo que pase con el idioma del
        usuario que llame: esto lo lee un cliente.
        """
        if not start:
            return ""
        tz = self.env['ir.config_parameter'].sudo().get_param(TZ_PARAM, DEFAULT_TZ)
        dia = format_datetime(self.env, start, tz=tz, dt_format="d 'de' MMMM 'de' y",
                              lang_code=SERVICES_LANG)
        desde = format_datetime(self.env, start, tz=tz, dt_format="HH:mm",
                                lang_code=SERVICES_LANG)
        if not stop:
            return "%s, %s" % (dia, desde)
        hasta = format_datetime(self.env, stop, tz=tz, dt_format="HH:mm",
                                lang_code=SERVICES_LANG)
        return "%s, entre %s y %s" % (dia, desde, hasta)

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
            # `event_id` es lo que convierte una lista en algo accionable: sin el,
            # el cliente puede decir "muevela" y no hay forma de saber cual.
            # Viaja por el chat, asi que TODOS los metodos de reagenda vuelven a
            # comprobar que la cita es de este telefono.
            bloqueo = event._visar_reschedule_blocked() if event else 'sin_fecha'
            entries.append({
                'service': line.product_id.display_name,
                'date': date.isoformat() if date else None,
                'date_label': self._agent_format_date(date, tz),
                'status': self._agent_service_status(line, date, now),
                'zone': event.visar_zone_id.name if event and event.visar_zone_id else None,
                'event_id': event.id if event else None,
                'can_reschedule': bool(event) and bloqueo is None,
                'reschedule_reason': bloqueo,
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

    # ------------------------------------------------------------------
    # Seguimiento CRM (agent_track_lead) — UNICO metodo de ESCRITURA
    # ------------------------------------------------------------------
    #
    # Cruce deliberado y acotado de la regla "el runtime no escribe en Odoo"
    # (diseno 31). El agente SOLO crea/refresca leads en la etapa 'Nuevo'; el
    # avance a etapas posteriores lo hace Odoo por eventos reales (pago,
    # valoracion, tarea FSM), no el runtime. Corre con sudo() ACOTADO a este
    # metodo (cruza partner/ordenes/crm que el usuario share no ve por ACL,
    # igual que agent_customer_services). Superficie tipada y minima: solo
    # telefono + service_code + un quote opcional; nunca nombres de modelo,
    # dominios ni SQL.

    @api.model
    def _agent_lead_skip(self, reason):
        """Respuesta cuando NO se crea/toca lead (telefono/grupo/cliente existente)."""
        return {'lead_id': None, 'created': False, 'stage': None,
                'skipped_reason': reason}

    @api.model
    def _agent_partner_has_service_in_group(self, partner, group):
        """True si el partner ya es cliente del grupo (orden confirmada en el).

        Recorre las ordenes CONFIRMADAS del partner y busca una linea de servicio
        Visar cuyo grupo sea `group`. El grupo se resuelve con
        `product.template._visar_service_groups()`, que lee el enlace autoritativo
        dimension -> producto (un producto puede cubrir varias dimensiones, p. ej.
        "Fumigacion interior + exterior") y no solo el puntero inverso
        `visar_dimension_id`, que en catalogos reales suele venir vacio. Una
        poliza activa es tambien una orden confirmada en su grupo, asi que queda
        cubierta sin leer subscription_state. Es la exclusion "los clientes no son
        leads", pero POR GRUPO: un cliente de fumigacion que pregunta por jardineria
        SI genera lead de jardineria (diseno 31 seccion 4).
        """
        if not partner or not group:
            return False
        orders = self.env['sale.order'].sudo().search([
            ('partner_id', '=', partner.id),
            ('state', 'in', CONFIRMED_ORDER_STATES),
        ])
        templates = orders.mapped('order_line').filtered(
            lambda l: l.product_id.visar_is_service
        ).mapped('product_id.product_tmpl_id')
        return group in templates._visar_service_groups()

    @api.model
    def _agent_lead_quote_note(self, dimension, quote):
        """Nota de chatter con la cotizacion del agente (enriquecimiento, no avance)."""
        quote = quote or {}
        label = dimension._visar_wizard_label() if dimension else "servicio"
        parts = []
        total = quote.get('total')
        if total not in (None, False):
            try:
                parts.append("$%s %s" % (
                    '{:,.2f}'.format(float(total)), quote.get('currency') or ''))
            except (TypeError, ValueError):
                pass
        if quote.get('m2'):
            parts.append("%s m2" % quote['m2'])
        if quote.get('cp'):
            parts.append("CP %s" % quote['cp'])
        detail = (" - " + ", ".join(parts)) if parts else ""
        return "Cotizacion del agente: %s%s" % (label, detail)

    @api.model
    def _agent_open_lead(self, nat, group, partner=None, phone=None, source=None):
        """(lead, created, motivo). Lead ABIERTO del pipeline WhatsApp, o lo crea.

        Lo comparten `agent_track_lead` (cotizacion) y `agent_request_handoff`
        (escalar a un humano): los dos tienen que aterrizar en el MISMO lead, o el
        asesor acabaria con dos fichas del mismo cliente y la mitad del contexto
        en cada una.

        `group` puede venir vacio: al escalar no siempre se sabe todavia que
        servicio queria el cliente.
        """
        team = self.env.ref(WA_TEAM_XMLID, raise_if_not_found=False)
        nuevo = self.env.ref(WA_STAGE_NUEVO_XMLID, raise_if_not_found=False)
        cerrado = self.env.ref(WA_STAGE_CERRADO_XMLID, raise_if_not_found=False)
        if not team or not nuevo:
            _logger.warning(
                "pipeline WhatsApp ausente (equipo/etapa sin cargar). "
                "Instalar/actualizar el modulo visar_crm.")
            return self.env['crm.lead'].browse(), False, 'pipeline_missing'

        # sudo() acotado a crm.lead: el usuario RPC no tiene ACL de CRM.
        Lead = self.env['crm.lead'].sudo()
        domain = [
            ('visar_wa_phone_norm', '=', nat),
            ('visar_service_group_id', '=', group.id if group else False),
            ('team_id', '=', team.id),
        ]
        if cerrado:  # lead "abierto" = aun no Cerrado (won/lost ya archivado)
            domain.append(('stage_id', '!=', cerrado.id))
        lead = Lead.search(domain, order='id desc', limit=1)
        if lead:
            if partner and not lead.partner_id:
                # El partner aparecio despues de crear el lead: enlazarlo.
                lead.partner_id = partner.id
            return lead, False, None

        lead = Lead.create({
            'name': "WhatsApp %s" % ((partner.name if partner else None) or nat),
            'type': 'opportunity',
            'team_id': team.id,
            'stage_id': nuevo.id,
            'visar_service_group_id': group.id if group else False,
            'visar_wa_phone_norm': nat,
            'visar_source': source or 'whatsapp',
            'phone': phone or nat,
            'partner_id': partner.id if partner else False,
        })
        return lead, True, None

    @api.model
    def agent_track_lead(self, payload):
        """Registra una interaccion de WhatsApp como lead de CRM en 'Nuevo'.

        `payload` = {
          "phone":        "5218112345678",
          "service_code": "fumigacion_interior",  # DIMENSION; Odoo resuelve el grupo
          "quote":        {"cp","m2","total","currency"} | None,   # enriquecimiento
          "source":       "whatsapp"     # opcional
        }

        Idempotente y acotado a 'Nuevo': un lead por (telefono, grupo) en el
        pipeline WhatsApp. Devuelve:
            {"lead_id": int|None, "created": bool, "stage": str|None,
             "skipped_reason": None|"invalid_phone"|"no_group"|
                               "existing_customer"|"pipeline_missing"}
        No lanza por datos malos del runtime: el seguimiento es best-effort y no
        debe tumbar una respuesta al cliente.
        """
        payload = payload or {}

        nat = self._agent_normalize_phone(payload.get('phone'))
        if len(nat) != 10:
            return self._agent_lead_skip('invalid_phone')

        dimension, _options = self._agent_resolve_dimension(payload.get('service_code'))
        if not dimension:  # sin dimension o grupo ambiguo: no hay UN grupo
            return self._agent_lead_skip('no_group')
        group = dimension.group_id
        if not group:
            return self._agent_lead_skip('no_group')

        partner = self._agent_find_partner(payload.get('phone'))
        if self._agent_partner_has_service_in_group(partner, group):
            return self._agent_lead_skip('existing_customer')

        lead, created, reason = self._agent_open_lead(
            nat, group, partner=partner, phone=payload.get('phone'),
            source=payload.get('source'))
        if reason:
            return self._agent_lead_skip(reason)

        quote = payload.get('quote') or {}

        # Enriquecimiento (diseno 31 seccion 5.1): valor del pipeline + chatter.
        # NO avanza la etapa: la cotizacion del agente se queda en 'Nuevo'.
        total = quote.get('total')
        if total not in (None, False):
            try:
                lead.expected_revenue = float(total)
            except (TypeError, ValueError):
                pass
        lead.message_post(body=self._agent_lead_quote_note(dimension, quote))

        # Una cotizacion sin respuesta es el caso tipico de lead frio: se programa
        # el recontacto aqui mismo. Idempotente: si el cliente sigue escribiendo,
        # cada turno lo vuelve a empujar seis horas mas adelante.
        lead._visar_wa_schedule_followup(
            context=self._agent_followup_context(payload, dimension, quote))

        return {
            'lead_id': lead.id,
            'created': created,
            'stage': lead.stage_id.name,
            'skipped_reason': None,
        }

    # ------------------------------------------------------------------
    # Interes sin cotizacion (leads frios)
    # ------------------------------------------------------------------
    #
    # `agent_track_lead` solo existe cuando el modelo COTIZO, y esa no es la unica
    # forma de perder a un cliente: se pierde igual al que pregunto por cobertura
    # y no volvio, y al que contesto medio cuestionario y se fue. Esos no dejaban
    # rastro ninguno en el CRM -ni lead, ni nada que recontactar-.
    #
    # El disparador es la SENAL COMERCIAL, no el mensaje: nombrar un servicio,
    # preguntar un precio, preguntar si hay cobertura, entrar al cuestionario. Un
    # "hola", un numero equivocado o un audio que no se entendio NO abren lead, o
    # el pipeline se llena de fichas que nadie puede trabajar. Quien decide si
    # hubo senal es el runtime, que es quien ve el mensaje; Odoo decide si esa
    # senal merece ficha.

    @api.model
    def agent_track_interest(self, payload):
        """Abre (o refresca) el lead de un cliente interesado, sin cotizacion.

        `payload` = {
          "phone":        "5218112345678",
          "service_code": "fumigacion_interior" | None,  # puede no saberse aun
          "context":      {...},              # foto para redactar el recontacto
          "source":       "whatsapp"          # opcional
        }

        A diferencia de `agent_track_lead`, **el grupo puede quedar vacio**: es la
        misma forma que ya usa `agent_request_handoff` cuando se escala sin saber
        que queria el cliente. Devuelve la misma forma que `agent_track_lead`.

        Best-effort de punta a punta: ningun fallo de aqui puede tumbar la
        respuesta al cliente, que es lo unico que el cliente ve.
        """
        payload = payload or {}

        nat = self._agent_normalize_phone(payload.get('phone'))
        if len(nat) != 10:
            return self._agent_lead_skip('invalid_phone')

        # Sin service_code el grupo queda vacio y el lead se abre igual. Con uno
        # que no resuelve, tambien: no saber que servicio quiere no es razon para
        # perderle la pista a alguien que pregunto.
        group = self.env['visar.service.group']
        if payload.get('service_code'):
            dimension, _options = self._agent_resolve_dimension(
                payload.get('service_code'))
            group = dimension.group_id if dimension else group

        partner = self._agent_find_partner(payload.get('phone'))
        if group and self._agent_partner_has_service_in_group(partner, group):
            return self._agent_lead_skip('existing_customer')

        try:
            lead, created, reason = self._agent_open_lead(
                nat, group, partner=partner, phone=payload.get('phone'),
                source=payload.get('source'))
        except Exception:  # noqa: BLE001 - el seguimiento nunca tumba el turno
            _logger.exception(
                "agent_track_interest: no se pudo abrir el lead del telefono "
                "terminado en %s", nat[-4:])
            return self._agent_lead_skip('lead_failed')
        if reason:
            return self._agent_lead_skip(reason)

        contexto = payload.get('context')
        lead._visar_wa_schedule_followup(
            context=contexto if isinstance(contexto, dict) else None)

        return {
            'lead_id': lead.id,
            'created': created,
            'stage': lead.stage_id.name,
            'skipped_reason': None,
        }

    @api.model
    def agent_drop_followup(self, payload):
        """Cancela el recontacto de un cliente. `payload` = {phone, reason}.

        Existe porque hay dos exclusiones que Odoo **no puede ver**: que el
        cliente haya dicho que no, y que se haya quejado. Las dos viven en el
        texto del mensaje, y el texto solo lo lee el runtime. El resto de
        exclusiones (etapa, escalamiento, cliente existente) se comprueban al
        enviar y no necesitan que nadie avise.

        Cancela **todos** los leads abiertos del telefono: quien dice "ya no,
        gracias" no lo esta diciendo de un grupo de servicio en particular.
        """
        payload = payload or {}
        nat = self._agent_normalize_phone(payload.get('phone'))
        if len(nat) != 10:
            return {'dropped': 0}

        reason = payload.get('reason') or 'declino'
        leads = self.env['crm.lead'].sudo().search([
            ('visar_wa_phone_norm', '=', nat),
            ('visar_wa_followup_state', 'in', ('scheduled', 'queued')),
        ])
        if not leads:
            return {'dropped': 0}
        leads._visar_wa_drop_followup(reason)
        return {'dropped': len(leads)}

    @api.model
    def _agent_followup_context(self, payload, dimension, quote):
        """Foto minima con la que el modelo puede redactar un recontacto.

        Lo que el runtime mande en `context` manda; esto solo rellena lo que se
        sabe desde Odoo cuando la llamada vino por `agent_track_lead`, que no
        trae foto propia.
        """
        contexto = dict(payload.get('context') or {})
        contexto.setdefault('wa_id', payload.get('phone') or '')
        contexto.setdefault('etapa', 'cotizado')
        if dimension:
            contexto.setdefault('servicio', dimension.display_name)
        for clave in ('cp', 'm2', 'total', 'currency'):
            if quote.get(clave) not in (None, False, ''):
                contexto.setdefault(clave, quote.get(clave))
        return contexto

    # ------------------------------------------------------------------
    # Horarios disponibles (solo lectura)
    # ------------------------------------------------------------------
    #
    # No reimplementan la agenda: llaman al generador nativo
    # `appointment.type._get_appointment_slots` -que es un metodo de modelo y NO
    # necesita sesion web- y lo pasan por el mismo filtro multi-tecnico que usa el
    # wizard. Lo unico propio es APLANAR el resultado: el nativo devuelve un arbol
    # mes/semana/dia pensado para pintar un calendario, y por WhatsApp lo que sirve
    # es una lista corta de dias y horas.

    # Tope de dias que se ofrecen de golpe. WhatsApp corta las listas en 10 filas,
    # asi que pedir mas no cabria en un mensaje.
    MAX_AVAILABLE_DAYS = 10

    @api.model
    def _agent_slot_bounds(self, slot, tz_info, apt_type):
        """(inicio, fin) en UTC naive de un slot del arbol nativo."""
        dt_str = slot.get('datetime')
        if not dt_str:
            return None, None
        duration = float(
            slot.get('slot_duration') or apt_type.appointment_duration or 1.0)
        start_local = fields.Datetime.from_string(dt_str)
        start_utc = tz_info.localize(start_local).astimezone(
            pytz.utc).replace(tzinfo=None)
        return start_utc, start_utc + relativedelta(hours=duration)

    @api.model
    def _agent_slot_resource_ids(self, slot):
        """Tecnicos del slot, venga del filtro Visar o del arbol nativo.

        Delega en el modelo: el filtro de traslado necesita el mismo accesor, y
        dos copias de un "de cualquiera de las dos formas" es como divergen las
        formas.
        """
        return self.env['appointment.type'].sudo()._visar_slot_resource_ids(slot)

    @api.model
    def _agent_slot_tree(self, payload):
        """(apt_type, arbol de meses ya filtrado, tz_info) o (empty, [], None)."""
        AptType = self.env['appointment.type'].sudo()
        zone = self._agent_booking_zone(payload)
        if not zone:
            return AptType.browse(), [], None
        mode, apt_type, items = self._agent_booking_context(payload)
        if not apt_type or not items:
            return AptType.browse(), [], None

        tz_name = self.env['ir.config_parameter'].sudo().get_param(
            TZ_PARAM, DEFAULT_TZ)
        tz_info = pytz.timezone(tz_name)
        asked_capacity = int(payload.get('asked_capacity') or 1)

        # El cliente tiene que seguir viendo SU horario apartado. Sin esto el chat
        # queda absurdo: "aparta este horario" -> "muestrame otra vez ese dia" ->
        # su propio horario ya no aparece. Reservar si funcionaba (ahi el contexto
        # se ponia), pero el listado no, y el listado es lo que el cliente ve.
        owner_key = self.env['res.partner'].sudo()._visar_phone_nat10_value(
            payload.get('phone'))
        if owner_key:
            apt_type = apt_type.with_context(visar_hold_owner=owner_key)

        # A donde va el tecnico. Sale del mismo booking que trae el payload, y con
        # None el filtro de traslado no toca nada (diseno 33 §5.4).
        destination = AptType._visar_travel_destination(payload)

        if mode == 'valuation':
            resources = apt_type._visar_eligible_resources(zone)
            if not resources:
                return AptType.browse(), [], None
            months = apt_type._get_appointment_slots(
                tz_name, filter_resources=resources, asked_capacity=asked_capacity)
            # ESTE es el sitio que el §5.5 del diseno olvidaba. La rama de
            # valoracion NO pasa por `_visar_filter_slots_multi_service` -por eso
            # el apartado se descuenta en `_get_resources_remaining_capacity` y no
            # ahi, ver el docstring de `visar_slot_hold.py`-, asi que la
            # factibilidad hay que engancharla aparte o esta rama se quedaria sin
            # ella. `require='any'`: la lista son CANDIDATOS y basta uno que
            # llegue, pero los que no llegan se podan, porque el runtime toma
            # `resource_ids[0]`.
            months = AptType._visar_filter_slots_travel(
                apt_type, months, tz_name, destination, require='any')
            return apt_type, months, tz_info

        pools, _missing = AptType._visar_service_resource_pools(zone, items)
        if not pools:
            return AptType.browse(), [], None
        resource_ids = AptType._visar_filter_resource_ids_for_pools(pools)
        resources = self.env['appointment.resource'].sudo().browse(resource_ids)
        months = apt_type._get_appointment_slots(
            tz_name, filter_resources=resources, asked_capacity=asked_capacity)
        # Mismo filtro que el wizard: exige que los tecnicos de TODOS los
        # servicios esten libres a la vez, y de paso la factibilidad de ruta.
        months = AptType._visar_filter_slots_multi_service(
            apt_type, months, pools, tz_name, asked_capacity,
            destination=destination)
        return apt_type, months, tz_info

    @api.model
    def _agent_iter_days(self, months):
        """Recorre el arbol nativo y entrega (fecha, slots) de los dias con hueco."""
        for month in months or []:
            for week in month.get('weeks', []):
                for day in week:
                    if not isinstance(day, dict) or not day.get('slots'):
                        continue
                    yield day.get('day'), day['slots']

    @api.model
    def agent_available_days(self, payload):
        """Proximos dias con horarios disponibles para lo que el cliente pidio.

        `payload` = {"selections": {...}, "cp"|"zone_id", "mode", "asked_capacity"}

        Devuelve {"days": [{"date": "2026-08-20", "slot_count": 5}], "min_hours",
        "message"}. `min_hours` importa: con `min_schedule_hours` en 24 **no se
        puede reservar para hoy**, y el agente tiene que decirlo de frente en vez
        de ofrecer algo imposible.
        """
        payload = payload or {}
        apt_type, months, _tz = self._agent_slot_tree(payload)
        if not apt_type:
            return {'days': [], 'min_hours': 0,
                    'message': "No hay cobertura o servicio para esa consulta."}

        days = []
        for day, slots in self._agent_iter_days(months):
            if not day:
                continue
            days.append({
                'date': fields.Date.to_string(day),
                'slot_count': len(slots),
            })
            if len(days) >= self.MAX_AVAILABLE_DAYS:
                break

        return {
            'days': days,
            'min_hours': apt_type.min_schedule_hours,
            'message': ("%d dia(s) con disponibilidad." % len(days) if days
                        else "No hay horarios disponibles por ahora."),
        }

    @api.model
    def _agent_to_local(self, stamp, tz_info):
        """UTC naive -> texto naive en la zona de Visar ('2026-08-20 16:00:00')."""
        if not stamp or not tz_info:
            return None
        local = pytz.utc.localize(stamp).astimezone(tz_info).replace(tzinfo=None)
        return fields.Datetime.to_string(local)

    @api.model
    def agent_day_slots(self, payload):
        """Horarios disponibles de UN dia concreto.

        `payload` = el de `agent_available_days` + {"date": "2026-08-20"}.
        Devuelve {"date", "slots": [{"start","stop","start_local","stop_local",
        "resource_ids"}]}.

        **Dos relojes, y no da igual cual se usa para que.** `start`/`stop` van en
        UTC naive porque es lo que `agent_hold_slot` y `agent_prepare_booking`
        esperan de vuelta (la misma convencion que `appointment.booking.line`).
        `start_local`/`stop_local` van en la zona de Visar (`visar.agent.timezone`)
        y son los UNICOS que se le pueden ensenar a una persona.

        Sin los locales el runtime no tenia forma de saberlo y pintaba el UTC tal
        cual: un servicio de las 4 de la tarde se ofrecia como "entre 22:00 y
        23:00" -casi medianoche- y el cliente reservaba a ciegas. El runtime NO
        puede convertirlo por su cuenta: la zona es configuracion de Odoo y
        derivarla del otro lado seria otra regla duplicada.
        """
        payload = payload or {}
        wanted = fields.Date.to_date(payload.get('date'))
        if not wanted:
            return {'date': None, 'slots': [], 'message': "Falta la fecha."}

        apt_type, months, tz_info = self._agent_slot_tree(payload)
        if not apt_type:
            return {'date': payload.get('date'), 'slots': [],
                    'message': "No hay cobertura o servicio para esa consulta."}

        slots_payload = []
        for day, slots in self._agent_iter_days(months):
            if day != wanted:
                continue
            for slot in slots:
                start, stop = self._agent_slot_bounds(slot, tz_info, apt_type)
                if not start:
                    continue
                slots_payload.append({
                    'start': fields.Datetime.to_string(start),
                    'stop': fields.Datetime.to_string(stop),
                    'start_local': self._agent_to_local(start, tz_info),
                    'stop_local': self._agent_to_local(stop, tz_info),
                    'resource_ids': self._agent_slot_resource_ids(slot),
                })
            break

        return {
            'date': fields.Date.to_string(wanted),
            'slots': slots_payload,
            'message': ("%d horario(s) disponibles." % len(slots_payload)
                        if slots_payload else "No hay horarios ese dia."),
        }

    # ------------------------------------------------------------------
    # Reagendar una cita ya pagada
    # ------------------------------------------------------------------
    #
    # El cliente que ya pago y quiere mover su cita no tenia camino: la reagenda
    # era trabajo a mano de oficina. Estos tres metodos son ese camino.
    #
    # **No se puede cancelar, solo mover** (decision de negocio, ago-2026): el
    # servicio esta cobrado y no existe ningun flujo de reembolso en el sistema.
    # Por eso aqui no hay `agent_cancel_*` y no debe anadirse uno sin resolver
    # antes que pasa con el dinero.
    #
    # **La pertenencia se comprueba en los TRES.** El id de la cita viaja por el
    # chat y un id es adivinable: sin esta comprobacion, cualquiera podria mover
    # la cita de otro escribiendo un numero. Se verifica contra el telefono en
    # cada llamada, no solo al listar.
    #
    # ## De donde salen los horarios que se ofrecen
    #
    # NO se reconstruye lo que el cliente eligio en su dia. Se penso hacerlo
    # desde `calendar.event.visar_booking_items`, y **ese campo esta vacio en las
    # 90 citas de produccion**: lo escribe solo una rama del controlador web y
    # nunca llego a poblarse. Reconstruir desde ahi habria funcionado en pruebas
    # y con ninguna cita real.
    #
    # En su lugar se deriva de lo que SI existe: el tipo de cita del evento, la
    # zona del domicilio del cliente y los tecnicos elegibles de esa zona. Es el
    # mismo camino que la rama de valoracion, incluida la factibilidad de ruta.
    #
    # **Limitacion conocida:** una cita multi-servicio se listaba exigiendo que
    # los tecnicos de TODAS las dimensiones estuvieran libres a la vez
    # (`_visar_filter_slots_multi_service`). Aqui se exige el pool del tipo de
    # cita del evento con la capacidad que la cita ya tiene. Para mover una cita
    # es suficiente y es conservador —pide los mismos tecnicos simultaneos que ya
    # tenia—, pero no es identico. Si algun dia se puebla `visar_booking_items`,
    # este es el sitio donde conviene volver.

    @api.model
    def _agent_reschedule_event(self, payload):
        """(evento, motivo_de_error). Resuelve el evento Y comprueba que es suyo.

        Devolver el mismo motivo `not_found` cuando la cita no existe y cuando
        existe pero es de otro cliente es deliberado: distinguirlos convertiria
        este metodo en un oraculo para saber que ids de cita existen.
        """
        payload = payload or {}
        partner = self._agent_find_partner(payload.get('phone'))
        if not partner:
            return self.env['calendar.event'].browse(), 'not_found'
        try:
            event_id = int(payload.get('event_id') or 0)
        except (TypeError, ValueError):
            return self.env['calendar.event'].browse(), 'not_found'
        if not event_id:
            return self.env['calendar.event'].browse(), 'not_found'

        event = self.env['calendar.event'].sudo().browse(event_id).exists()
        if not event:
            return self.env['calendar.event'].browse(), 'not_found'

        lineas = self.env['sale.order.line'].sudo().search([
            ('calendar_event_id', '=', event.id),
        ])
        if partner not in lineas.mapped('order_id.partner_id'):
            return self.env['calendar.event'].browse(), 'not_found'
        return event, None

    @api.model
    def _agent_reschedule_tree(self, event):
        """(apt_type, arbol de meses, tz_info) para mover ESTA cita.

        Corre siempre con `visar_ignore_event_id` puesto: la cita que se mueve no
        cuenta ni como capacidad ocupada ni como parada del dia. Sin eso, el
        cliente no veria libre ni el horario que ya tiene, y las franjas vecinas
        pareceran inalcanzables por un viaje contra si mismo de cero minutos.
        """
        AptType = self.env['appointment.type'].sudo()
        vacio = (AptType.browse(), [], None)
        apt_type = event.appointment_type_id
        if not apt_type:
            return vacio

        zone = self._agent_reschedule_zone(event)
        if not zone:
            return vacio

        tz_name = self.env['ir.config_parameter'].sudo().get_param(
            TZ_PARAM, DEFAULT_TZ)
        tz_info = pytz.timezone(tz_name)

        apt_type = apt_type.sudo().with_context(visar_ignore_event_id=event.id)
        resources = apt_type._visar_eligible_resources(zone)
        if not resources:
            return vacio

        # La capacidad que ya tiene la cita: mover no es cambiar de tamano.
        capacidad = max(len(event.appointment_resource_ids), 1)
        months = apt_type._get_appointment_slots(
            tz_name, filter_resources=resources, asked_capacity=capacidad)
        destination = AptType.with_context(
            visar_ignore_event_id=event.id)._visar_travel_destination(
                {'delivery_address': self._agent_reschedule_address(event)})
        months = AptType.with_context(
            visar_ignore_event_id=event.id)._visar_filter_slots_travel(
                apt_type, months, tz_name, destination, require='any')
        return apt_type, months, tz_info

    @api.model
    def _agent_reschedule_partner(self, event):
        """El cliente de la cita, desde sus lineas de pedido."""
        lineas = self.env['sale.order.line'].sudo().search([
            ('calendar_event_id', '=', event.id),
        ])
        partners = lineas.mapped('order_id.partner_id')
        return partners[:1]

    @api.model
    def _agent_reschedule_address(self, event):
        """Domicilio del servicio, para el filtro de traslado."""
        partner = self._agent_reschedule_partner(event)
        if not partner:
            return {}
        destino = partner.child_ids.filtered(
            lambda p: p.type == 'delivery')[:1] or partner
        return {
            'street': destino.street or '',
            'zip': destino.zip or '',
            'city': destino.city or '',
        }

    @api.model
    def _agent_reschedule_zone(self, event):
        """Zona del servicio: la del evento si la tiene, si no la del CP."""
        if event.visar_zone_id:
            return event.visar_zone_id
        cp = (self._agent_reschedule_address(event) or {}).get('zip')
        record = self.env['visar.zone.cp'].sudo()._get_cp_record(cp)
        return record.zone_id if record else self.env['visar.zone'].sudo().browse()

    @api.model
    def agent_reschedule_days(self, payload):
        """Dias con hueco para mover una cita. `payload` = {phone, event_id}."""
        event, error = self._agent_reschedule_event(payload)
        if error:
            return {'days': [], 'min_hours': 0, 'blocked': error,
                    'message': "No encontre esa cita a tu nombre."}
        motivo = event._visar_reschedule_blocked()
        if motivo:
            return {'days': [], 'min_hours': 0, 'blocked': motivo,
                    'message': "Esa cita no se puede mover."}

        apt_type, months, _tz = self._agent_reschedule_tree(event)
        if not apt_type:
            return {'days': [], 'min_hours': 0, 'blocked': None,
                    'message': "No hay horarios disponibles por ahora."}

        days = []
        for day, slots in self._agent_iter_days(months):
            if not day:
                continue
            days.append({'date': fields.Date.to_string(day),
                         'slot_count': len(slots)})
            if len(days) >= self.MAX_AVAILABLE_DAYS:
                break
        return {
            'days': days,
            'min_hours': max(apt_type.min_schedule_hours or 0,
                             event._visar_reschedule_min_hours()),
            'blocked': None,
            'message': ("%d dia(s) con disponibilidad." % len(days) if days
                        else "No hay horarios disponibles por ahora."),
        }

    @api.model
    def agent_reschedule_slots(self, payload):
        """Horarios de un dia para mover una cita. `payload` = {phone, event_id, date}."""
        event, error = self._agent_reschedule_event(payload)
        if error:
            return {'date': None, 'slots': [], 'blocked': error,
                    'message': "No encontre esa cita a tu nombre."}
        motivo = event._visar_reschedule_blocked()
        if motivo:
            return {'date': None, 'slots': [], 'blocked': motivo,
                    'message': "Esa cita no se puede mover."}

        target = fields.Date.to_date(payload.get('date'))
        if not target:
            return {'date': None, 'slots': [], 'blocked': None,
                    'message': "Falta la fecha."}

        apt_type, months, tz_info = self._agent_reschedule_tree(event)
        if not apt_type:
            return {'date': payload.get('date'), 'slots': [], 'blocked': None,
                    'message': "No hay horarios disponibles."}

        # El nuevo horario tambien tiene que respetar la antelacion minima; se
        # podan aqui para no ofrecer lo que `_visar_reschedule` rechazaria.
        limite = fields.Datetime.add(fields.Datetime.now(),
                                     hours=event._visar_reschedule_min_hours())
        slots_payload = []
        for day, slots in self._agent_iter_days(months):
            if day != target:
                continue
            for slot in slots:
                start, stop = self._agent_slot_bounds(slot, tz_info, apt_type)
                if not start or start < limite:
                    continue
                slots_payload.append({
                    'start': fields.Datetime.to_string(start),
                    'stop': fields.Datetime.to_string(stop),
                    'start_local': self._agent_to_local(start, tz_info),
                    'stop_local': self._agent_to_local(stop, tz_info),
                    'resource_ids': self._agent_slot_resource_ids(slot),
                })
            break
        return {
            'date': fields.Date.to_string(target),
            'slots': slots_payload,
            'blocked': None,
            'message': ("%d horario(s) disponibles." % len(slots_payload)
                        if slots_payload else "No hay horarios ese dia."),
        }

    @api.model
    def agent_reschedule_confirm(self, payload):
        """Mueve la cita. `payload` = {phone, event_id, start, stop, resource_ids}.

        ESCRIBE, con sudo() acotado a mover la cita y lo que cuelga de ella. No
        toca el pedido ni el pago: el servicio ya esta cobrado y lo unico que
        cambia es cuando se presta.
        """
        event, error = self._agent_reschedule_event(payload)
        if error:
            return {'ok': False, 'reason': error,
                    'message': "No encontre esa cita a tu nombre."}

        start = fields.Datetime.to_datetime(payload.get('start'))
        stop = fields.Datetime.to_datetime(payload.get('stop'))
        if not start or not stop:
            return {'ok': False, 'reason': 'sin_horario',
                    'message': "Falta el horario nuevo."}

        ok, motivo = event._visar_reschedule(
            start, stop, resource_ids=payload.get('resource_ids') or [])
        if not ok:
            return {'ok': False, 'reason': motivo,
                    'message': "No se pudo mover la cita."}

        tz_name = self.env['ir.config_parameter'].sudo().get_param(
            TZ_PARAM, DEFAULT_TZ)
        return {
            'ok': True,
            'reason': None,
            'when_label': self._agent_window_label(event.start, event.stop),
            'remaining': max(event._visar_reschedule_max()
                             - event.visar_reschedule_count, 0),
            'message': "Cita movida.",
        }

    # ------------------------------------------------------------------
    # Hand-off a un humano (agent_request_handoff)
    # ------------------------------------------------------------------
    #
    # Hasta ahora el agente decia "en seguida te contacta un asesor" y NO pasaba
    # nada mas: no se creaba nada en Odoo y nadie se enteraba. Era la falla del
    # sistema manual -el contexto se pierde, nadie da seguimiento- reproducida
    # dentro del sistema nuevo, y encima con una promesa explicita al cliente.
    #
    # Aterriza en el lead de CRM, que es donde ya vive el rastro de este cliente:
    # nota en el chatter con TODO lo que ya se recogio (para que el asesor no
    # vuelva a preguntarlo) + actividad asignada, para que caiga en la bandeja de
    # alguien y no solo en un historial que nadie mira.
    # Ver `.context/33-whatsapp-agendado-design.md` §9.1.

    # Motivos por los que el agente escala. Catalogo CERRADO: el runtime elige de
    # esta lista, no manda texto libre que luego nadie pueda agrupar.
    HANDOFF_REASONS = {
        'customer_request': "El cliente pidio hablar con un asesor",
        'no_slot_fits': "Ninguna fecha ofrecida le acomoda al cliente",
        'payment_failed': "Fallo el pago o la liga",
        'hold_expired': "Se vencio el apartado del horario",
        'out_of_coverage': "Codigo postal fuera de cobertura",
        'phone_ambiguous': "El telefono coincide con varios clientes",
        'complaint': "Queja o inconformidad",
        'not_understood': "El agente no logro entender al cliente",
        'other': "Otro motivo",
    }

    # Dias para vencer la actividad. Corto a proposito: un hand-off que se atiende
    # en una semana ya no sirve de nada.
    HANDOFF_ACTIVITY_DAYS = 1

    @api.model
    def agent_request_handoff(self, payload):
        """Escala la conversacion a un humano dejando rastro accionable en Odoo.

        `payload` = {
          "phone":   "5218112345678",
          "reason":  "no_slot_fits",          # de HANDOFF_REASONS
          "summary": "texto libre corto",     # opcional, lo que dijo el cliente
          "context": {"servicio": "...", "cp": "64000", ...},  # opcional
          "service_code": "fumigacion_interior"  # opcional, para agrupar por grupo
        }

        Devuelve {"lead_id", "created", "activity_scheduled", "skipped_reason"}.
        Best-effort, como agent_track_lead: no lanza por datos malos del runtime.
        """
        payload = payload or {}

        nat = self._agent_normalize_phone(payload.get('phone'))
        if len(nat) != 10:
            return {'lead_id': None, 'created': False,
                    'activity_scheduled': False, 'skipped_reason': 'invalid_phone'}

        # El grupo es opcional: al escalar puede no saberse aun que queria.
        group = self.env['visar.service.group'].browse()
        if payload.get('service_code'):
            dimension, _options = self._agent_resolve_dimension(payload['service_code'])
            group = dimension.group_id if dimension else group

        partner = self._agent_find_partner(payload.get('phone'))
        # El docstring promete que no lanza, y hay que cumplirlo de verdad: si el
        # hand-off revienta, el cliente se queda esperando a un asesor que nadie
        # convoco. Mejor perder el rastro en Odoo (queda en el log) que perder la
        # respuesta al cliente. Se aprendio por las malas: `visar_source` no
        # aceptaba el valor que este metodo escribia, y la excepcion viajaba
        # entera hasta el runtime.
        try:
            lead, created, reason = self._agent_open_lead(
                nat, group, partner=partner, phone=payload.get('phone'),
                source='whatsapp_handoff')
        except Exception:  # noqa: BLE001 - el hand-off nunca tumba la respuesta
            _logger.exception(
                "agent_request_handoff: no se pudo abrir el lead del telefono "
                "terminado en %s", nat[-4:])
            return {'lead_id': None, 'created': False,
                    'activity_scheduled': False, 'skipped_reason': 'lead_failed'}
        if reason:
            return {'lead_id': None, 'created': False,
                    'activity_scheduled': False, 'skipped_reason': reason}

        # El chatter y la actividad tambien van dentro del try: una excepcion aqui
        # dejaria al cliente esperando a un asesor que nadie convoco, que es
        # justo lo que este metodo existe para evitar.
        scheduled = False
        try:
            lead.message_post(body=self._agent_handoff_note(payload))
            assignee = self._agent_handoff_assignee(lead)
            if assignee:
                lead.activity_schedule(
                    'mail.mail_activity_data_call',
                    date_deadline=fields.Date.add(
                        fields.Date.context_today(lead),
                        days=self.HANDOFF_ACTIVITY_DAYS),
                    summary="WhatsApp: %s" % self.HANDOFF_REASONS.get(
                        payload.get('reason'), self.HANDOFF_REASONS['other']),
                    user_id=assignee.id,
                )
                scheduled = True
            else:
                # Sin humano a quien asignar, la nota igual queda: mejor rastro
                # sin dueno que nada. Pero se avisa, porque un hand-off que no
                # llega a la bandeja de nadie es medio hand-off.
                _logger.warning(
                    "agent_request_handoff: lead %s sin asignatario humano. "
                    "Poner lider o miembros al equipo de WhatsApp.", lead.id)
        except Exception:  # noqa: BLE001 - el hand-off nunca tumba la respuesta
            _logger.exception(
                "agent_request_handoff: fallo al anotar/agendar el lead %s", lead.id)

        return {
            'lead_id': lead.id,
            'created': created,
            'activity_scheduled': scheduled,
            'skipped_reason': None,
        }

    @api.model
    def _agent_handoff_assignee(self, lead):
        """Humano al que se le asigna el hand-off, o vacio si no hay ninguno.

        **El bot no cuenta.** CRM auto-asigna el lead a quien lo crea, y quien lo
        crea aqui es el usuario RPC del agente: sin este filtro la actividad
        quedaba a nombre de "Agente WhatsApp (RPC)" — rastro perfecto que no
        convoca a nadie, exactamente lo que este metodo existe para evitar.
        Se descartan tambien los usuarios *share* (portal): no ven el CRM.
        """
        def usable(users):
            return users.filtered(
                lambda user: user and not user.share and user != self.env.user)[:1]

        return (usable(lead.user_id)
                or usable(lead.team_id.user_id)
                or usable(lead.team_id.member_ids)
                or self.env['res.users'].browse())

    @api.model
    def _agent_handoff_note(self, payload):
        """Nota de chatter del hand-off.

        Lleva TODO lo que el agente ya recogio. Es la diferencia entre que el
        asesor retome la conversacion y que la empiece de cero preguntando lo
        mismo — el problema que este proyecto existe para eliminar.
        """
        reason = self.HANDOFF_REASONS.get(
            payload.get('reason'), self.HANDOFF_REASONS['other'])
        lines = ["<p><b>El agente de WhatsApp escalo esta conversacion.</b></p>",
                 "<p><b>Motivo:</b> %s</p>" % escape(reason)]
        summary = (payload.get('summary') or '').strip()
        if summary:
            lines.append("<p><b>Lo que dijo el cliente:</b> %s</p>" % escape(summary))
        context = payload.get('context') or {}
        if isinstance(context, dict) and context:
            rows = "".join(
                "<li><b>%s:</b> %s</li>" % (escape(str(key)), escape(str(value)))
                for key, value in context.items() if value not in (None, '', False))
            if rows:
                lines.append("<p><b>Lo que ya se sabe:</b></p><ul>%s</ul>" % rows)
        return Markup("".join(lines))

    # ------------------------------------------------------------------
    # Agendado por WhatsApp — apartado + preparacion de la reserva
    # ------------------------------------------------------------------
    #
    # Segundo y tercer metodo de ESCRITURA, tan acotados como agent_track_lead:
    # sudo() dentro del metodo, payload tipado, sin nombres de modelo ni dominios.
    #
    # NO reimplementan nada del wizard web. Llaman a los MISMOS metodos de modelo
    # que el controlador (`sale.order._visar_fill_from_booking`,
    # `calendar.booking._visar_create_for_booking`), que se bajaron del
    # controlador justo para esto. Si una regla de precio cambia, cambia para los
    # dos canales a la vez. Ver `.context/33-whatsapp-agendado-design.md`.

    @api.model
    def _agent_booking_fail(self, reason, message):
        """Respuesta de fallo, tipada. Nunca lanza: el agente tiene que poder
        decirle algo util al cliente (o escalar) en vez de recibir un traceback."""
        return {
            'prepared': False,
            'reason': reason,
            'message': message,
            'payment_url': None,
        }

    @api.model
    def _agent_booking_zone(self, payload):
        """Zona desde `zone_id` o desde el CP. Recordset vacio si no hay cobertura."""
        zone_id = payload.get('zone_id')
        if zone_id:
            return self.env['visar.zone'].sudo().browse(int(zone_id)).exists()
        cp_record = self.env['visar.zone.cp'].sudo()._get_cp_record(payload.get('cp'))
        return cp_record.zone_id if cp_record else self.env['visar.zone'].sudo().browse()

    @api.model
    def _agent_booking_partner(self, phone, name=None):
        """(partner, motivo_de_error). Crea el cliente si el telefono es nuevo.

        La politica de AMBIGUEDAD es la misma que en agent_customer_services y por
        la misma razon: si dos partners comparten el numero no se adivina, porque
        equivocarse aqui significa colgarle una venta a otra persona. Se devuelve
        el motivo para que el agente escale a un humano (agent_request_handoff).
        """
        Partner = self.env['res.partner'].sudo()
        key = Partner._visar_phone_nat10_value(phone)
        if not key:
            return Partner.browse(), 'phone_invalid'
        matches = Partner.search([('visar_phone_nat10', '=', key)])
        if len(matches) > 1:
            _logger.info(
                "agent_prepare_booking: el telefono terminado en %s coincide con "
                "%d partners; no se reserva por ambiguedad.", key[-4:], len(matches))
            return Partner.browse(), 'phone_ambiguous'
        if matches:
            return matches, None
        if not (name or '').strip():
            return Partner.browse(), 'name_required'
        return Partner.create({'name': name.strip(), 'phone': phone}), None

    @api.model
    def _agent_booking_needs_name(self, phone):
        """¿Este telefono es de alguien a quien todavia no conocemos por nombre?

        Es LECTURA: no crea nada (`_agent_booking_partner` si crea, y solo al
        cerrar). Se usa para que el cuestionario incluya el paso del nombre en vez
        de descubrirlo al final, que es lo que pasaba antes: el cliente nuevo
        contestaba diez preguntas, elegia horario, y en vez de la liga de pago
        recibia *"Falta el nombre del cliente."* — un callejon sin salida del que
        ni siquiera se escalaba a un humano.

        Ambiguo (varios partners con el mismo numero) devuelve False a proposito:
        ahi el nombre no arregla nada, y `agent_prepare_booking` ya escala.
        """
        Partner = self.env['res.partner'].sudo()
        key = Partner._visar_phone_nat10_value(phone)
        if not key:
            return False
        return not Partner.search_count([('visar_phone_nat10', '=', key)])

    @api.model
    def _agent_booking_mode(self, payload):
        """Modo de venta: `wizard` o `valuation`.

        **Se DERIVA del cuestionario; no lo manda el runtime.** "Un corte a
        valoracion se vende como valoracion" es regla de negocio, y las reglas de
        negocio viven en el modelo (diseno 33 §11).

        El runtime ya recibe `requires_valuation` en cada estado; pedirle ademas
        que devuelva un `mode` serian dos representaciones del mismo hecho, justo
        en el sitio donde este proyecto ya se quemo dos veces con reglas
        duplicadas (I-11, y la regla de "elige al menos una" de `6999839`).

        Un `mode` explicito sirve para el caso contrario: el web resuelve el modo
        por su cuenta y entra por su propia URL de valoracion, sin selecciones que
        derivar. Pero **no puede contradecir al cuestionario**.

        Aqui el explicito ganaba SIEMPRE, y eso dejaba la rama de valoracion
        inalcanzable desde el chat: el runtime estampa `mode: "wizard"` fijo en
        cada peticion (`FlowState.from_state` arma el booking y no tiene como
        saber el modo), asi que la derivacion no llegaba a correr nunca. El
        cliente de termitas acusaba el aviso, daba su direccion, y al pedir dias
        se le resolvia como reserva normal: sin dimension no hay pools, sin pools
        no hay dias, y acababa en "no encontre fechas disponibles" — exactamente
        el sintoma de I-17 que esto venia a cerrar. Visto en `visar-db` el
        21-ago-2026, con la rama ya desplegada.
        """
        payload = payload or {}
        selections = payload.get('selections') or {}
        # El corte manda. Es una funcion de las selecciones, no una preferencia
        # del llamador, y un llamador que diga 'wizard' sobre un cuestionario ya
        # cortado esta equivocado, no eligiendo.
        if self.env['appointment.type'].sudo()._visar_wizard_requires_valuation(
                selections):
            return 'valuation'
        return payload.get('mode') or 'wizard'

    @api.model
    def _agent_booking_context(self, payload):
        """(mode, appointment_type, items) para la reserva, o (mode, empty, []).

        Dos modos, los mismos que el web: `wizard` (fumigacion / areas verdes) y
        `valuation` (visita de valoracion, precio fijo y sin medidas).
        """
        AptType = self.env['appointment.type'].sudo()
        mode = self._agent_booking_mode(payload)
        if mode == 'valuation':
            apt_type = AptType._visar_get_valuation_appointment_type()
            # La lista la arma el flujo, no este metodo: es la MISMA que usa el
            # paso de la direccion para resolver items, y dos definiciones del
            # "que se vende en una valoracion" acabarian divergiendo.
            items = AptType._visar_wizard_valuation_items()
            return mode, apt_type, items
        apt_type = AptType._visar_get_master_appointment_type()
        # SIEMPRE se resuelven desde `selections`. Armar `items` a mano puede
        # emparejar una dimension con un tramo del eje equivocado y devolver la
        # variante base -un tercio del precio- SIN error. Ver diseno 33 §7.1.
        items = AptType._visar_resolve_wizard_items(payload.get('selections') or {})
        return mode, apt_type, items

    @api.model
    def _agent_pick_resources(self, apt_type, zone, items, start, stop, mode,
                              asked_capacity=1):
        """Tecnico(s) libres para el horario pedido, o recordset vacio."""
        AptType = self.env['appointment.type'].sudo()
        if mode == 'valuation':
            eligible = apt_type._visar_eligible_resources(zone)
            free = AptType._visar_free_candidates(
                apt_type, eligible, start, stop, asked_capacity)
            if not free:
                return self.env['appointment.resource'].browse()
            return min(free, key=lambda r: AptType._visar_resource_load(r, start, stop))
        pools, _missing = AptType._visar_service_resource_pools(zone, items)
        if not pools:
            return self.env['appointment.resource'].browse()
        return AptType._visar_pick_resources_for_slot(
            apt_type, pools, start, stop, asked_capacity)

    @api.model
    def _agent_booking_line_values(self, apt_type, resources, start, stop,
                                   asked_capacity=1):
        """Lineas de reserva por recurso, con el mismo reparto de capacidad que el web.

        Espeja `appointment/controllers/appointment.py` (submit): reparte la
        capacidad pedida entre los recursos elegidos respetando lo que queda libre.
        """
        remaining = apt_type._get_resources_remaining_capacity(
            resources, start, stop, with_linked_resources=False)
        values = []
        to_assign = asked_capacity
        for resource in resources:
            resource_remaining = remaining.get(resource, 0)
            reserved = min(resource_remaining, to_assign, resource.capacity)
            to_assign -= reserved
            values.append({
                'appointment_resource_id': resource.id,
                'capacity_reserved': reserved,
                'capacity_used': (
                    reserved if resource.shareable and apt_type.manage_capacity
                    else resource.capacity if apt_type.manage_capacity else 1),
            })
        return values

    # ------------------------------------------------------------------
    # El cuestionario, paso a paso (agent_booking_step)
    # ------------------------------------------------------------------
    #
    # Es el metodo que permite que el runtime NO tenga logica de flujo. Le manda
    # el estado y la respuesta del cliente; Odoo poda lo que quedo invalido,
    # normaliza la respuesta, decide que sigue y devuelve las opciones validas
    # del paso nuevo.
    #
    # Sin esto el runtime tendria que derivar las opciones del catalogo y
    # reimplementar tres reglas: que se invalida al cambiar un paso, en que orden
    # van, y como se normaliza cada respuesta ("proteccion general" activa las
    # tres categorias; "termitas" corta a valoracion). Serian dos copias
    # divergiendo — el riesgo de "dos front-ends" del diseno 33 §11, que ya se
    # cobro una vez (I-11).
    #
    # Es LECTURA: no escribe nada en Odoo. El estado se lo queda el runtime.

    @api.model
    def _agent_flow_type(self):
        """`appointment.type` para conducir el cuestionario, EN ESPANOL.

        El idioma no es cosmetica aqui. Los titulos y las opciones fijas son
        literales en espanol en el codigo, asi que se veian bien; pero todo lo que
        sale del CATALOGO -nombres de grupo, de dimension, de tramo, de add-on, de
        plan de poliza- se lee en el idioma del usuario que hace la llamada, y el
        usuario RPC del agente esta en `en_US`.

        El caso mas visible era el paso de poliza: la periodicidad llegaba como
        *"per month"* en mitad de una conversacion en espanol, en el paso de mayor
        valor del flujo. Es la misma correccion que ya llevaba
        `_agent_partner_services`, por la misma razon.
        """
        return self.env['appointment.type'].sudo().with_context(lang=SERVICES_LANG)

    @api.model
    def _agent_booking_state(self, booking, step=None, error=None):
        """Respuesta tipada de agent_booking_step. Nunca lanza."""
        AptType = self._agent_flow_type()
        booking = booking or {}
        # `next_pending_step` y no `next_step`: el segundo solo llega hasta la
        # direccion, asi que para un cuestionario completo respondia "la
        # direccion" y devolvia al cliente a un paso que ya habia contestado.
        step = step or AptType._visar_wizard_next_pending_step(booking)
        return {
            'selections': booking.get('selections') or {},
            'zone_id': booking.get('zone_id') or None,
            'items': booking.get('items') or [],
            'delivery_address': booking.get('delivery_address') or {},
            'extras_accepted': booking.get('extras_accepted') or [],
            'step': step,
            'options': AptType._visar_wizard_step_options(booking, step),
            'sequence': AptType._visar_wizard_step_sequence(booking),
            # Lo mismo que `sequence`, pero con etiqueta: es el menu de "quiero
            # cambiar algo". El runtime solo tiene claves (`group_12`) y ponerles
            # nombre del otro lado seria otra regla duplicada.
            'steps': AptType._visar_wizard_editable_steps(booking),
            # Cadena opaca: si no cambia entre dos estados, el horario apartado
            # sigue valiendo. El runtime la compara, no la interpreta.
            'schedule_key': AptType._visar_wizard_schedule_key(booking),
            # Para la pantalla de revision: que lleva y cuanto cuesta, en texto.
            # El runtime no puede armarlo (`selections` trae ids, no nombres).
            'summary': AptType._visar_wizard_summary(booking),
            'requires_valuation': AptType._visar_wizard_requires_valuation(
                booking.get('selections') or {}),
            'done': step == 'schedule',
            'error': error,
        }

    @api.model
    def agent_booking_step(self, payload):
        """Avanza el cuestionario un paso y devuelve el estado + las opciones.

        `payload` = {
            "booking": {...},     # el estado que devolvio la llamada anterior;
                                  # vacio o ausente = empezar de cero
            "step":    "plagas",  # el paso que se esta contestando; ausente = solo
                                  # preguntar por el estado actual, sin aplicar nada
            "answer":  {...},     # la respuesta del cliente, tal cual la recogio
                                  # el runtime (ver `options.kind` del paso)
            "phone":   "5218112345678",  # opcional; si el numero no es de ningun
                                  # cliente, el cuestionario anade el paso del
                                  # nombre. Sin el, ese paso no aparece.
            "ask":     "cobertura",  # opcional; vuelve a PREGUNTAR ese paso sin
                                  # aplicar nada. Es como el cliente corrige algo
                                  # desde la pantalla de revision. Solo se admiten
                                  # pasos de `steps`: pedir uno cualquiera seria
                                  # dejar que el runtime invente secuencia.
        }

        Devuelve {"selections", "zone_id", "items", "delivery_address",
        "extras_accepted", "step", "options", "sequence", "requires_valuation",
        "done", "error"}.

        `error` es None o {"code", "message", ...}: el runtime se lo dice al
        cliente y vuelve a preguntar EL MISMO paso. No es una excepcion — el
        agente tiene que poder seguir la conversacion.

        El `booking` que se devuelve es el que hay que mandar en la llamada
        siguiente. Se pasa entero a `agent_prepare_booking` al cerrar: por eso
        `selections` viaja tal cual y nunca se arman `items` a mano (diseno 33
        §7.1 — emparejar mal un tramo cobra un tercio del precio SIN error).
        """
        payload = payload or {}
        AptType = self._agent_flow_type()
        # La bandera NO viaja de ida y vuelta: se recalcula en cada llamada. Es
        # un hecho del mundo (¿existe ya este cliente?) que puede cambiar entre
        # dos mensajes -alguien lo da de alta en Odoo a media conversacion- y un
        # estado guardado en el runtime lo dejaria congelado.
        needs_name = self._agent_booking_needs_name(payload.get('phone'))
        # Igual que `needs_name`: la pone el CANAL, no el runtime, y se recalcula
        # en cada llamada. Dice que en el chat el aviso de valoracion es un paso
        # mas -se acusa y se sigue a la direccion- en vez de un corte a un flujo
        # aparte, que es lo que hace el web. Sin esto la rama de valoracion no
        # llega a horarios (I-17); con esto puesta solo aqui, el web no se entera.
        booking = dict(payload.get('booking') or {},
                       needs_name=needs_name, valuation_inline=True)
        step = payload.get('step')

        # Volver a preguntar un paso ya contestado (el cliente quiere corregirlo).
        # No aplica nada: solo devuelve ESE paso con sus opciones. Lo que hace que
        # corregir funcione es la poda, y esa corre al CONTESTAR, no al preguntar.
        ask = payload.get('ask')
        if ask and not step:
            editables = {s['key'] for s
                         in AptType._visar_wizard_editable_steps(booking)}
            if ask in editables:
                return self._agent_booking_state(booking, step=ask)
            return self._agent_booking_state(booking)

        # Sin paso: solo se pregunta "¿en que voy?". Util para retomar una
        # conversacion estacionada sin tocar el estado.
        if not step:
            return self._agent_booking_state(booking)

        try:
            booking, error = AptType._visar_wizard_apply_answer(
                booking, step, payload.get('answer'))
        except Exception:  # noqa: BLE001 - el flujo nunca tumba la respuesta
            _logger.exception(
                "agent_booking_step: fallo al aplicar el paso %s", step)
            return self._agent_booking_state(
                booking, step=step,
                error={'code': 'step_failed',
                       'message': "No pude registrar esa respuesta."})

        # La bandera se vuelve a poner DESPUES de aplicar la respuesta, y no es
        # defensa gratuita: `_visar_wizard_answer_address` no muta el booking, lo
        # **rehace** desde cero (es el paso que resuelve zona e items), asi que
        # cualquier clave que no sea del contrato se pierde ahi. Sin esto el paso
        # del nombre desaparecia justo despues de la direccion, que es donde
        # tenia que aparecer. Encontrado recorriendo el cuestionario de verdad:
        # el Odoo falso del runtime SI conservaba la clave, y mentia en verde.
        booking = dict(booking or {}, needs_name=needs_name, valuation_inline=True)

        if error:
            # Se vuelve a preguntar EL MISMO paso, con su mensaje.
            return self._agent_booking_state(booking, step=step, error=error)

        # Corregir un paso de ARRIBA invalida los items, y el unico sitio donde se
        # recalculan es el paso de la direccion. Si ya la tenemos, se vuelve a
        # aplicar sola: hacerle escribir otra vez su direccion -la pregunta mas
        # cara del cuestionario, y la que ya habia contestado bien- por cambiar
        # "interior" a "ambos" es justo lo que hace que nadie corrija nada.
        if (step != VISAR_STEP_ADDRESS
                and AptType._visar_wizard_next_step(booking) == VISAR_STEP_ADDRESS
                and (booking.get('delivery_address') or {})):
            booking, error = AptType._visar_wizard_reapply_address(booking)
            booking = dict(booking or {}, needs_name=needs_name, valuation_inline=True)
            if error:
                # La direccion guardada ya no sirve para lo que ahora se pide
                # (p. ej. no hay tecnicos para ese servicio en esa zona): se
                # pregunta de nuevo, con el motivo delante.
                return self._agent_booking_state(
                    booking, step=VISAR_STEP_ADDRESS, error=error)

        # Un SOLO camino de salida: lo que QUEDE pendiente. Antes se avanzaba por
        # la cadena (`_visar_wizard_step_after`), que reanuda desde el paso
        # contestado y por tanto vuelve a ofrecer extras y poliza aunque no
        # dependieran de lo que se acaba de corregir.
        return self._agent_booking_state(booking)

    @api.model
    def agent_hold_slot(self, payload):
        """Aparta un horario unos minutos a nombre de un telefono.

        `payload` = {"phone": "5218112345678", "resource_id": 1,
                     "start": "2026-08-20 16:00:00", "stop": "2026-08-20 17:00:00",
                     "mode": "wizard"|"valuation"}   # mode opcional, default wizard

        Devuelve {"held": bool, "hold_id": int|None, "expire_at": str|None,
        "reason": str|None}.

        Un telefono solo puede tener UN apartado a la vez: pedir otro libera el
        anterior (si no, un cliente indeciso bloquearia la agenda saltando de
        horario en horario).
        """
        payload = payload or {}
        Partner = self.env['res.partner'].sudo()
        owner = Partner._visar_phone_nat10_value(payload.get('phone'))
        resource = self.env['appointment.resource'].sudo().browse(
            int(payload.get('resource_id') or 0)).exists()
        start = fields.Datetime.to_datetime(payload.get('start'))
        stop = fields.Datetime.to_datetime(payload.get('stop'))
        if not (owner and resource and start and stop):
            return {'held': False, 'hold_id': None, 'expire_at': None,
                    'reason': 'invalid_payload'}

        # Comprobar disponibilidad ANTES de apartar. Sin esto dos clientes podian
        # apartar el mismo horario y quedarse FUERA LOS DOS: la exclusion del
        # dueno solo ignora el apartado propio, asi que a cada uno le estorbaba el
        # del otro y ninguno volvia a ver el horario. `agent_prepare_booking` si
        # validaba, pero este RPC suelto no.
        #
        # El tipo de cita se resuelve por MODO, igual que en el resto del flujo, y
        # no tomando el primero que cuelgue del recurso: un tecnico puede estar en
        # varios tipos (validariamos contra uno al azar) o en ninguno (apartariamos
        # a ciegas). Si no se puede determinar, se rechaza: apartar sin comprobar
        # es justo el bug que este bloque vino a cerrar.
        AptType = self.env['appointment.type'].sudo()
        mode = self._agent_booking_mode(payload)
        apt_type = (AptType._visar_get_valuation_appointment_type()
                    if mode == 'valuation'
                    else AptType._visar_get_master_appointment_type())
        if not apt_type or resource not in apt_type.resource_ids:
            return {'held': False, 'hold_id': None, 'expire_at': None,
                    'reason': 'resource_unavailable'}
        remaining = apt_type.with_context(
            visar_hold_owner=owner)._get_resources_remaining_capacity(
                resource, start, stop, with_linked_resources=False)
        if remaining.get('total_remaining_capacity', 0) < 1:
            return {'held': False, 'hold_id': None, 'expire_at': None,
                    'reason': 'slot_taken'}

        hold = self.env['visar.slot.hold']._visar_hold(resource, start, stop, owner)
        # El wa_id EXACTO, no el nacional de 10 digitos: es la clave con la que el
        # runtime encuentra la conversacion para avisar si el apartado vence.
        if hold:
            hold.sudo().visar_wa_phone = payload.get('phone') or False
        return {
            'held': bool(hold),
            'hold_id': hold.id if hold else None,
            'expire_at': hold.expire_at.isoformat() if hold else None,
            'reason': None if hold else 'hold_failed',
        }

    @api.model
    def agent_prepare_booking(self, payload):
        """Deja la reserva lista para pagar y devuelve la liga de pago.

        `payload` = {
            "phone": "5218112345678",       # identidad; obligatorio
            "name": "Juan Perez",           # solo si el telefono es nuevo
            "mode": "wizard"|"valuation",   # default wizard
            "selections": {...},            # respuestas del cuestionario
            "cp": "64000" | "zone_id": 2,
            "delivery_address": {"street", "ext_num", "int_num",
                                 "neighborhood", "zip", "city"},
            "slot": {"start": "...", "stop": "..."},   # UTC naive
            "extras_accepted": [{"product_id": 1, "quantity": 3}],
            "asked_capacity": 1,
        }

        Devuelve {"prepared": True, "payment_url", "expire_at", "total",
        "currency", "booking_id", "order_id"} o, si algo falla,
        {"prepared": False, "reason", "message"} — nunca una excepcion: el agente
        tiene que poder responderle al cliente o escalar.

        El horario queda APARTADO mientras el cliente paga, y la liga vive y muere
        con ese apartado (diseno 33 §6.1): pagar tarde no puede terminar en
        "pagaste y tu lugar ya se lo dieron a otro".
        """
        payload = payload or {}
        AptType = self.env['appointment.type'].sudo()

        # 1. Identidad. Sin cliente no hay reserva, y la ambiguedad no se adivina.
        # El nombre puede venir suelto (`name`) o como una respuesta mas del
        # cuestionario (`selections['nombre']`), que es por donde llega desde
        # WhatsApp: asi el runtime no tiene que saber que esa clave existe.
        partner, error = self._agent_booking_partner(
            payload.get('phone'),
            payload.get('name') or (payload.get('selections') or {}).get('nombre'))
        if error:
            return self._agent_booking_fail(error, {
                'phone_invalid': "El telefono no es valido.",
                'phone_ambiguous': "Hay varios clientes con ese telefono; "
                                   "conviene canalizarlo con un asesor.",
                'name_required': "Falta el nombre del cliente.",
            }[error])

        # 2. Cobertura.
        zone = self._agent_booking_zone(payload)
        if not zone:
            return self._agent_booking_fail(
                'out_of_coverage', "Ese codigo postal esta fuera de cobertura.")

        # 3. Que se va a vender.
        mode, apt_type, items = self._agent_booking_context(payload)
        if not apt_type:
            return self._agent_booking_fail(
                'config_missing', "Falta configurar el tipo de cita.")
        if not items:
            return self._agent_booking_fail(
                'no_items', "No pude resolver el servicio a partir de las respuestas.")

        # 4. Horario.
        slot = payload.get('slot') or {}
        start = fields.Datetime.to_datetime(slot.get('start'))
        stop = fields.Datetime.to_datetime(slot.get('stop'))
        if not (start and stop) or stop <= start:
            return self._agent_booking_fail('slot_invalid', "El horario no es valido.")

        asked_capacity = int(payload.get('asked_capacity') or 1)
        owner_key = self.env['res.partner'].sudo()._visar_phone_nat10_value(
            payload.get('phone'))
        # El contexto tiene que viajar en `apt_type`, no en `self`: quien acaba
        # consultando la capacidad es `_visar_resource_free_at`, y lo hace sobre
        # el tipo de cita que se le pasa. Sin esto pasan DOS cosas, ambas malas:
        # el cliente se bloquearia con su propio apartado, y el reparto de
        # capacidad de las lineas saldria en cero (su hold se restaria a si mismo).
        apt_type = apt_type.with_context(visar_hold_owner=owner_key)
        resources = self._agent_pick_resources(
            apt_type, zone, items, start, stop, mode, asked_capacity)
        if not resources:
            return self._agent_booking_fail(
                'slot_taken', "Ese horario ya no esta disponible.")

        # 5. Apartar antes de cobrar.
        hold = self.env['visar.slot.hold']._visar_hold(
            resources[0], start, stop, owner_key, capacity=asked_capacity)
        wa_phone = payload.get('phone') or False
        if hold:
            hold.sudo().visar_wa_phone = wa_phone

        # 6. Reserva pendiente + pedido, por los mismos metodos que el web.
        booking_payload = {
            'mode': mode,
            'master_appointment_type_id': apt_type.id,
            'zone_id': zone.id,
            'items': items,
            'selections': payload.get('selections') or {},
            'delivery_address': payload.get('delivery_address') or {},
            'extras_accepted': payload.get('extras_accepted') or [],
        }
        description = AptType._visar_calification_notes(payload.get('selections'))
        booking_line_values = self._agent_booking_line_values(
            apt_type, resources, start, stop, asked_capacity)
        calendar_booking = self.env['calendar.booking']._visar_create_for_booking(
            apt_type, start, stop, description, False, [], partner.name, partner,
            asked_capacity=asked_capacity,
            booking_line_values=booking_line_values)
        hold.sudo().calendar_booking_id = calendar_booking.id
        # Marca la reserva como venida de WhatsApp y con QUE numero: es lo que
        # permite confirmarle el pago por donde escribio.
        calendar_booking.sudo().visar_wa_phone = wa_phone

        order = self.env['sale.order'].sudo().create({
            'partner_id': partner.id,
            'website_id': self.env['website'].sudo().search([], limit=1).id or False,
        })
        plan = self.env['sale.subscription.plan'].sudo().browse(
            int((payload.get('selections') or {}).get('poliza_plan_id') or 0)).exists()
        lines_added = order._visar_fill_from_booking(
            booking_payload, calendar_booking, zone, plan=plan,
            tz=apt_type.appointment_tz)
        if not lines_added:
            calendar_booking.sudo().unlink()  # arrastra el hold (ondelete cascade)
            order.sudo().unlink()
            return self._agent_booking_fail(
                'cart_failed', "No pude armar el pedido para ese servicio.")

        order._visar_apply_delivery_address(
            payload.get('delivery_address'), partner_name=partner.name)
        # Sin esto el portal puede mostrar la orden sin boton de pago.
        order.require_payment = True

        # Total en cero (p. ej. un tramo sin cargo): no hay nada que cobrar, asi
        # que no hay liga de pago que generar — y sin pago el flujo nativo nunca
        # convierte la reserva en cita. Se escala en vez de mandar una liga rota.
        if order.amount_total <= 0:
            return self._agent_booking_fail(
                'zero_total',
                "El servicio no tiene cargo; conviene cerrarlo con un asesor.")

        # 7. Liga de pago. ABSOLUTA a proposito: `get_portal_url()` devuelve una
        # ruta relativa, que en un chat no es tocable.
        link_wizard = self.env['payment.link.wizard'].sudo().create({
            'res_model': 'sale.order',
            'res_id': order.id,
            'amount': order.amount_total,
            'currency_id': order.currency_id.id,
            'partner_id': order.partner_id.id,
        })

        return {
            'prepared': True,
            'reason': None,
            'message': None,
            'payment_url': link_wizard.link,
            'expire_at': hold.expire_at.isoformat() if hold else None,
            # amount_TOTAL: los precios de Visar llevan IVA incluido, asi que el
            # subtotal NO es lo que el cliente ve ni lo que cotizo el agente.
            'total': order.amount_total,
            'currency': order.currency_id.name,
            'booking_id': calendar_booking.id,
            'order_id': order.id,
            'hold_id': hold.id if hold else None,
        }
