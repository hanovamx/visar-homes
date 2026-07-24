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
from odoo import api, fields, models

# Notas de negocio para el prompt, editables sin tocar codigo desde
# Ajustes > Tecnico > Parametros del sistema.
NOTES_PARAM = 'visar.agent.catalog_notes'


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
