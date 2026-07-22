# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    visar_is_service = fields.Boolean(
        "Servicio agendable (Visar)",
        help="Marca este producto como un servicio reservable vía citas web con preguntas previas.")
    visar_is_valuation = fields.Boolean(
        "Producto de valoración técnica",
        help="Producto usado en el flujo de valoración técnica (solo zona).")
    visar_is_roedores = fields.Boolean(
        "Producto control de roedores",
        help="Producto que se agrega cuando el cliente declara problema de roedores. "
             "Sus add-ons obligatorios (estaciones antirroedores) se inyectan automáticamente.")
    visar_dimension_id = fields.Many2one(
        'visar.service.dimension', string="Dimensión Visar",
        help="Dimensión del wizard asociada a este producto (configuración alternativa).")
    visar_service_group = fields.Selection(
        selection=[
            ('fumigacion', 'Fumigación'),
            ('corte', 'Corte y poda'),
        ],
        string="Grupo (legacy)",
        help="Obsoleto: usar Grupos de servicio / dimensiones Visar.")
    visar_dimension_kind = fields.Selection(
        selection=[
            ('interior', 'Interior'),
            ('exterior', 'Exterior'),
            ('poda', 'Poda / jardín'),
        ],
        string="Dimensión (legacy)",
        help="Obsoleto: usar Grupos de servicio / dimensiones Visar.")
    visar_tier_ids = fields.One2many(
        'visar.service.tier', 'product_tmpl_id', string="Tabulador (tramos m²)")
    visar_optional_line_ids = fields.One2many(
        'visar.product.optional.line', 'product_tmpl_id',
        string="Add-ons Visar (obligatorio / cantidad)")

    @api.model
    def _visar_sanitize_optional_line_commands(self, commands):
        """Descarta creates de líneas add-on sin optional_product_id (artefacto del cliente web)."""
        sanitized = []
        for command in commands:
            if command[0] == 0 and not (command[2] or {}).get('optional_product_id'):
                continue
            sanitized.append(command)
        return sanitized

    @api.model_create_multi
    def create(self, vals_list):
        sanitized_list = []
        for vals in vals_list:
            vals = dict(vals)
            if vals.get('visar_optional_line_ids'):
                vals['visar_optional_line_ids'] = self._visar_sanitize_optional_line_commands(
                    vals['visar_optional_line_ids']
                )
            sanitized_list.append(vals)
        records = super().create(sanitized_list)
        records._visar_reconcile_optional_lines()
        return records

    def write(self, vals):
        if vals.get('visar_optional_line_ids'):
            vals = dict(vals)
            vals['visar_optional_line_ids'] = self._visar_sanitize_optional_line_commands(
                vals['visar_optional_line_ids']
            )
        res = super().write(vals)
        if 'optional_product_ids' in vals or 'visar_optional_line_ids' in vals:
            self._visar_reconcile_optional_lines()
        return res

    @api.onchange('optional_product_ids')
    def _onchange_optional_product_ids_visar(self):
        OptionalLine = self.env['visar.product.optional.line']
        for tmpl in self:
            current_ids = set(tmpl.optional_product_ids.ids)
            lines_to_keep = tmpl.visar_optional_line_ids.filtered(
                lambda line: line.optional_product_id
                and line.optional_product_id.id in current_ids
            )
            existing_ids = set(lines_to_keep.mapped('optional_product_id').ids)
            tmpl.visar_optional_line_ids = lines_to_keep
            for addon_id in current_ids - existing_ids:
                tmpl.visar_optional_line_ids = tmpl.visar_optional_line_ids | OptionalLine.new({
                    'optional_product_id': addon_id,
                    'is_mandatory': False,
                    'quantity': 1,
                })

    def _visar_reconcile_optional_lines(self):
        """Sincroniza visar_optional_line_ids con optional_product_ids (Opción A)."""
        OptionalLine = self.env['visar.product.optional.line']
        for tmpl in self.filtered('id'):
            current_ids = set(tmpl.optional_product_ids.ids)
            existing_by_addon = {
                line.optional_product_id.id: line
                for line in tmpl.visar_optional_line_ids
                if line.optional_product_id
            }
            stale_lines = tmpl.visar_optional_line_ids.filtered(
                lambda line: not line.optional_product_id
                or line.optional_product_id.id not in current_ids
            )
            if stale_lines:
                stale_lines.unlink()
            for addon_id in current_ids - set(existing_by_addon.keys()):
                OptionalLine.create({
                    'product_tmpl_id': tmpl.id,
                    'optional_product_id': addon_id,
                    'is_mandatory': False,
                    'quantity': 1,
                })

    def _visar_get_mandatory_addon_map(self, zone=None):
        """Retorna {product_id: qty} para add-ons obligatorios de este servicio."""
        self.ensure_one()
        addon_qty = {}
        for line in self.visar_optional_line_ids.filtered('is_mandatory'):
            variant = line.optional_product_id.product_variant_id
            if not variant:
                continue
            if zone:
                variant = self._visar_variant_for_zone(variant, zone)
            addon_qty[variant.id] = addon_qty.get(variant.id, 0) + line.quantity
        return addon_qty

    @api.model
    def _visar_get_service_template_for_dimension(self, dimension):
        """Producto servicio ligado a una dimensión del wizard."""
        if not dimension:
            return self.browse()
        if dimension.product_tmpl_id:
            return dimension.product_tmpl_id
        return self.search([
            ('visar_dimension_id', '=', dimension.id),
            ('visar_is_service', '=', True),
            ('active', '=', True),
        ], limit=1)

    def _visar_tiers_for_dimension(self, dimension):
        """Tramos del producto aplicables a una dimensión, acotados por measure_scope.

        Un mismo producto puede servir a interior y exterior; el alcance del tramo
        ('all' / 'interior' / 'exterior') evita mezclar tabuladores. 'all' siempre aplica.
        """
        self.ensure_one()
        scope = dimension.measure_type if dimension else False
        return self.visar_tier_ids.filtered(
            lambda t: t.measure_scope == 'all' or t.measure_scope == scope
        ).sorted('sequence')

    def _visar_tier_for_dimension_m2(self, dimension, m2):
        """Tramo cuyo rango contiene m2 para la dimensión dada.

        Ante solapes, gana el rango más angosto (más específico); desempata la
        secuencia. Requiere tramos acotados por measure_scope para no mezclar
        tabuladores de interior y exterior.
        """
        self.ensure_one()
        matches = self._visar_tiers_for_dimension(dimension).filtered(
            lambda t: t.m2_min <= m2 <= t.m2_max)
        return matches.sorted(
            key=lambda t: (t.m2_max - t.m2_min, t.sequence))[:1]

    @api.model
    def _visar_get_valuation_template(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'visar.valuation_product_tmpl_id')
        if param and param.isdigit():
            tmpl = self.browse(int(param)).exists()
            if tmpl and tmpl.visar_is_valuation:
                return tmpl
        return self.search([
            ('visar_is_valuation', '=', True),
            ('active', '=', True),
        ], limit=1)

    @api.model
    def _visar_get_roedores_template(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'visar.roedores_product_tmpl_id')
        if param and param.isdigit():
            tmpl = self.browse(int(param)).exists()
            if tmpl and tmpl.visar_is_roedores:
                return tmpl
        return self.search([
            ('visar_is_roedores', '=', True),
            ('active', '=', True),
        ], limit=1)

    @api.model
    def _visar_variant_for_zone(self, variant, zone):
        """Si la variante usa atributo de zona, devuelve la variante equivalente en la zona elegida."""
        if not variant or not zone or not zone.code:
            return variant
        zone_codes = set(
            self.env['visar.zone'].sudo().search([]).mapped('code')
        ) - {False}
        attrs = variant.product_template_attribute_value_ids.mapped('name')
        if not (zone_codes & set(attrs)):
            return variant
        range_names = [a for a in attrs if a not in zone_codes]
        if not range_names:
            return variant
        range_name = range_names[0]
        for candidate in variant.product_tmpl_id.product_variant_ids:
            cand_attrs = candidate.product_template_attribute_value_ids.mapped('name')
            if zone.code in cand_attrs and range_name in cand_attrs:
                return candidate
        return variant

    def _visar_axis_attribute(self, scope):
        """Atributo (product.attribute) cuyo valor varía entre las variantes de los
        tramos de este alcance ('interior'/'exterior').

        Un mismo producto puede llevar dos atributos de tamaño (p. ej. tamaño inmueble
        interior y tamaño jardín exterior). El eje de una medición es el atributo que
        cambia entre sus propios tramos mientras el otro queda fijo en su valor base.
        Así se identifica qué atributo corresponde a interior y cuál a exterior sin
        IDs hardcodeados. Devuelve product.attribute vacío si no es unívoco."""
        self.ensure_one()
        tiers = self.visar_tier_ids.filtered(
            lambda t: t.measure_scope == scope and not t.is_valuation
            and t.product_id.product_tmpl_id == self)
        variants = tiers.mapped('product_id')
        per_attr = {}
        for variant in variants:
            for ptav in variant.product_template_attribute_value_ids:
                per_attr.setdefault(ptav.attribute_id, set()).add(ptav.id)
        varying = [attr for attr, values in per_attr.items() if len(values) > 1]
        if len(varying) == 1:
            return varying[0]
        return self.env['product.attribute']

    def _visar_combined_variant_for_tiers(self, interior_tier, exterior_tier, zone):
        """Variante que combina ambos ejes de tamaño (interior + exterior) para una
        selección de fumigación interior + exterior del mismo producto.

        Resuelve la variante por zona de cada tramo, identifica el eje interior y el
        exterior (`_visar_axis_attribute`) y busca la variante de la zona que lleva a la
        vez el valor de tamaño interior elegido y el de tamaño exterior elegido.

        Devuelve product.product vacío si no se puede resolver de forma unívoca; el
        llamador debe entonces caer al comportamiento anterior de dos líneas."""
        self.ensure_one()
        empty = self.env['product.product']
        if not interior_tier or not exterior_tier:
            return empty
        int_var = interior_tier._visar_get_variant_for_zone(zone)
        ext_var = exterior_tier._visar_get_variant_for_zone(zone)
        if not int_var or not ext_var:
            return empty
        interior_attr = self._visar_axis_attribute('interior')
        exterior_attr = self._visar_axis_attribute('exterior')
        if not interior_attr or not exterior_attr or interior_attr == exterior_attr:
            return empty
        int_ptav = int_var.product_template_attribute_value_ids.filtered(
            lambda p: p.attribute_id == interior_attr)[:1]
        ext_ptav = ext_var.product_template_attribute_value_ids.filtered(
            lambda p: p.attribute_id == exterior_attr)[:1]
        if not int_ptav or not ext_ptav:
            return empty
        # La zona (y cualquier otro atributo de configuración) se toma de la variante
        # interior por VALOR de atributo, no por `visar_zone_id` (que solo está poblado en
        # las variantes "carrier", no en las combinadas). La variante combinada difiere de
        # la interior solo en el eje exterior, así que fija el resto de valores + int + ext.
        other_ptavs = int_var.product_template_attribute_value_ids.filtered(
            lambda p: p.attribute_id != interior_attr and p.attribute_id != exterior_attr)
        target = int_ptav | ext_ptav | other_ptavs
        for variant in self.product_variant_ids:
            values = variant.product_template_attribute_value_ids
            if all(ptav in values for ptav in target):
                return variant
        return empty
