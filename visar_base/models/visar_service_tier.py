# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class VisarServiceTier(models.Model):
    """ Tramo de metros cuadrados de un servicio (producto) que mapea a una
    variante concreta del producto. Es la base del cálculo de variante/precio (D-04). """
    _name = 'visar.service.tier'
    _description = "Tramo de servicio Visar (m² → variante)"
    _order = 'product_tmpl_id, m2_min'

    product_tmpl_id = fields.Many2one(
        'product.template', string="Servicio (producto)",
        required=True, ondelete='cascade', index=True)
    name = fields.Char(
        "Etiqueta del rango",
        translate=True,
        help="Texto mostrado como opción en el wizard (p. ej. '1 – 250 m²').")
    m2_min = fields.Float("m² desde", required=True, default=0)
    m2_max = fields.Float(
        "m² hasta", required=True, default=0,
        help="Límite superior del tramo (inclusivo). Usa un valor alto (p. ej. 99999) "
             "para el último tramo / valoración técnica.")
    product_id = fields.Many2one(
        'product.product', string="Variante (fallback)",
        domain="[('product_tmpl_id', '=', product_tmpl_id)]",
        help="(Deprecated) Fallback si no se resuelve variante por zona. "
             "La variante correcta se resuelve automáticamente según la zona del cliente.")
    is_valuation = fields.Boolean(
        "Visita de valoración",
        help="Marca este tramo como 'Visita de valoración técnica' (fuera de rango de servicio directo).")
    is_free = fields.Boolean(
        "Sin cargo",
        help="Si está marcado, el tramo aparece en el pedido con precio tachado al 100% "
             "(p. ej. fumigación exterior 0–50 m² incluida).")
    combo_discount_eligible = fields.Boolean(
        "Aplica descuento combo",
        help="Si una regla de combo aplica, esta línea puede recibir el descuento configurado.")
    sequence = fields.Integer("Secuencia", default=10)

    # Valida que m2_max no sea menor que m2_min en el tramo definido.
    @api.constrains('m2_min', 'm2_max')
    def _check_range(self):
        for tier in self:
            if tier.m2_max < tier.m2_min:
                raise ValidationError(
                    "En el tramo del servicio '%s', 'm² hasta' no puede ser menor que 'm² desde'."
                    % (tier.product_tmpl_id.display_name or ''))

    # Construye el nombre visible del tramo usando la etiqueta o el rango de m².
    @api.depends('product_tmpl_id', 'name', 'm2_min', 'm2_max')
    def _compute_display_name(self):
        for tier in self:
            if tier.name:
                tier.display_name = tier.name
            else:
                tier.display_name = "%s [%g - %g]" % (
                    tier.product_tmpl_id.display_name or '', tier.m2_min, tier.m2_max)

    def _visar_get_variant_for_zone(self, zone):
        """Resuelve la variante correcta para esta tier según la zona del cliente.

        El tier conserva product_id apuntando a la variante de Zona B (referencia).
        Para otras zonas, busca la variante que comparte los mismos atributos de rango
        (atributos no-zona) con la variante de referencia.
        """
        self.ensure_one()
        if not self.product_tmpl_id:
            return self.env['product.product']

        ref_variant = self.product_id

        # Sin zona o sin referencia: usar product_id directo
        if not zone or not ref_variant:
            return ref_variant or self.product_tmpl_id.product_variant_ids[:1]

        # Si la referencia ya pertenece a la zona pedida, usarla directamente
        if ref_variant.visar_zone_id == zone:
            return ref_variant

        # Buscar variantes de la zona pedida en este mismo template
        zone_variants = self.product_tmpl_id.product_variant_ids.filtered(
            lambda v: v.visar_zone_id == zone)

        if not zone_variants:
            return ref_variant  # fallback: usar la de Zona B

        if len(zone_variants) == 1:
            return zone_variants

        # Hay múltiples variantes en la zona: encontrar la análoga por atributos de rango.
        #
        # "Atributo de zona" = aquel donde cada zona tiene exactamente UN valor del atributo.
        # Ej: atributo "Zona" → Zone A siempre tiene ptav "A", Zone B siempre tiene "B".
        #
        # "Atributo de rango" = aquel donde una misma zona puede tener MÚLTIPLES valores.
        # Ej: atributo "Tamaño inmueble" → Zone B tiene "0-50", "51-100", "101-500", etc.
        #
        # La variante análoga en otra zona = misma zona pedida + mismos valores de rango que ref.
        from collections import defaultdict

        all_tmpl_variants = self.product_tmpl_id.product_variant_ids.filtered('visar_zone_id')

        # Calcular cuántos ptav distintos tiene cada (attribute_id, zone) para este template
        attr_ptav_per_zone = defaultdict(lambda: defaultdict(set))
        for v in all_tmpl_variants:
            zone_id = v.visar_zone_id.id
            for ptav in v.product_template_attribute_value_ids:
                attr_ptav_per_zone[ptav.attribute_id.id][zone_id].add(ptav.id)

        # Zona attr = cada zona tiene exactamente 1 ptav para ese atributo
        zone_attr_ids = set()
        for attr_id, zone_ptavs in attr_ptav_per_zone.items():
            if all(len(ptavs) == 1 for ptavs in zone_ptavs.values()):
                zone_attr_ids.add(attr_id)

        # Rango attr = los demás
        all_attr_ids = set(ref_variant.product_template_attribute_value_ids.mapped('attribute_id.id'))
        range_attr_ids = all_attr_ids - zone_attr_ids

        # Valores de rango del ref_variant (los ptav que identifican el tier específico)
        ref_range_ptavs = frozenset(
            ref_variant.product_template_attribute_value_ids
            .filtered(lambda t: t.attribute_id.id in range_attr_ids)
            .ids
        )

        # Buscar variante de la zona pedida con exactamente los mismos ptav de rango
        for v in zone_variants:
            v_range_ptavs = frozenset(
                v.product_template_attribute_value_ids
                .filtered(lambda t: t.attribute_id.id in range_attr_ids)
                .ids
            )
            if v_range_ptavs == ref_range_ptavs:
                return v

        # Fallback: primera de la zona
        return zone_variants[:1]
