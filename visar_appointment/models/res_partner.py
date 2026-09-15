# -*- coding: utf-8 -*-
"""Identidad de cliente por teléfono (número nacional MX).

Una sola clave canónica de "mismo número" que comparten el dedupe de reservas
(controllers/appointment.py) y la búsqueda del agente de WhatsApp
(visar_whatsapp_agent). Dos nociones distintas de "mismo número" serían el bug
que estamos arreglando, así que la regla vive en un único sitio:
`_visar_phone_nat10_value`.
"""
from odoo import api, fields, models
from odoo.addons.website.models import ir_http


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Últimos 10 dígitos del teléfono = número nacional MX. Almacenado e indexado
    # para que el match sea una igualdad indexada (no un scan con regexp). SIN
    # constraint único: los duplicados existentes bloquearían el upgrade y un
    # número de hogar compartido es legítimo; el match se hace a nivel aplicación.
    visar_phone_nat10 = fields.Char(
        string="Teléfono nacional (10 díg.)",
        compute='_compute_visar_phone_nat10',
        store=True, index=True, compute_sudo=True,
        help="Últimos 10 dígitos del teléfono (número nacional MX). Clave de "
             "identidad por teléfono compartida por el dedupe de reservas y la "
             "búsqueda del agente de WhatsApp. False cuando no hay teléfono.")

    @api.depends('phone')
    def _compute_visar_phone_nat10(self):
        for partner in self:
            partner.visar_phone_nat10 = self._visar_phone_nat10_value(partner.phone)

    @staticmethod
    def _visar_phone_nat10_value(phone):
        """Últimos 10 dígitos de `phone`, o False si no llega a 10 dígitos.

        Regla ÚNICA de "mismo número" (número nacional MX): descarta separadores,
        el prefijo de país (+52) y el `1` de móvil. `8123415696`, `+528123415696`,
        `52 812 341 5696` y `5218123415696` dan todos `8123415696`.

        Devuelve **False** (no `''`) cuando no hay 10 dígitos, para que los ~75%
        de partners sin teléfono no colisionen todos en la misma clave vacía.
        """
        digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
        return digits[-10:] if len(digits) >= 10 else False

    # ------------------------------------------------------------------
    # Lista de precios: sin default genérico fuera del sitio web (REQ-004)
    # ------------------------------------------------------------------
    #
    # `property_product_pricelist` NO se guarda: Odoo la calcula, y a quien no
    # tiene lista propia le pone la primera lista activa sin grupo de países
    # (`product.pricelist._get_country_pricelist_multi`). En Visar esa es
    # "VISAR Zona A" (sequence 11), así que todos los clientes "tenían" Zona A y
    # toda cotización nacía con ella. Reordenar las listas no lo arregla: la que
    # quede primera se vuelve el default.
    #
    # Fuera del sitio web se muestra solo la lista ELEGIDA para el cliente (o la
    # de su grupo de países, si algún día se usan). En el sitio web no se toca:
    # la tienda y el wizard dependen de tener una lista, y ahí la zona del código
    # postal pone la correcta (`sale.order._visar_apply_zone_pricelist`).
    #
    # Se pregunta con `ir_http.get_request_website()`, igual que `website_sale`,
    # para que las pruebas lo puedan simular.

    @api.depends('country_id', 'specific_property_product_pricelist')
    @api.depends_context('company', 'country_code')
    def _compute_product_pricelist(self):
        super()._compute_product_pricelist()
        if ir_http.get_request_website():
            return
        for partner in self:
            lista = partner.property_product_pricelist
            if (lista and lista != partner.specific_property_product_pricelist
                    and not lista.country_group_ids):
                partner.property_product_pricelist = False

    def _inverse_product_pricelist(self):
        """Lo que se elige es lo que se guarda.

        El nativo NO guarda la lista si coincide con el default del país, porque
        "ya la tiene". Sin default, elegir "VISAR Zona A" a propósito se perdería
        al guardar.
        """
        if ir_http.get_request_website():
            return super()._inverse_product_pricelist()
        for partner in self:
            if partner.specific_property_product_pricelist != partner.property_product_pricelist:
                partner.specific_property_product_pricelist = partner.property_product_pricelist
