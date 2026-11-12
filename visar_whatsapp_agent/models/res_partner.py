# -*- coding: utf-8 -*-
"""Clientes y direcciones que nacieron en el chat antes de reservar nada.

Desde el 14-oct-2026 el agente guarda la direccion en cuanto el cliente la
confirma (`visar.agent.tools.agent_save_address`), y a quien escribe por primera
vez le crea la ficha en ese momento. Antes la ficha y la direccion nacian al
generar la liga de pago, y quien se quedaba a medias tenia que repetirlo todo la
siguiente vez ("ya te habia dicho").

El precio es que Odoo recibe fichas de gente que nunca compro. Por eso llevan
marca, se pueden filtrar en Contactos y se archivan solas pasado el plazo de
Ajustes -> Visar si siguen sin ningun pedido.
"""
import logging
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.visar_base.models.res_config_settings import (
    visar_chat_address_days,
)

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    visar_chat_sin_reserva = fields.Boolean(
        string="Del chat, sin reserva", copy=False, index=True,
        help="La ficha (o la direccion) la creo el agente de WhatsApp cuando el "
             "cliente confirmo su direccion, antes de reservar. Se desmarca al "
             "reservar por WhatsApp; si pasa el plazo de Ajustes -> Visar sin "
             "ningun pedido, se archiva sola.")

    def _visar_chat_tiene_pedidos(self):
        """¿Hay algun pedido (del estado que sea) a nombre de estas fichas?"""
        if not self:
            return False
        ids = self.ids
        return bool(self.env['sale.order'].sudo().search_count([
            '|', '|', '|',
            ('partner_id', 'in', ids),
            ('partner_shipping_id', 'in', ids),
            ('partner_invoice_id', 'in', ids),
            ('visar_service_partner_id', 'in', ids),
        ], limit=1))

    @api.model
    def _visar_cron_archivar_chat_sin_reserva(self):
        """Archiva lo que nacio en el chat y no reservo nada en el plazo.

        Una ficha de cliente se mira con sus direcciones: si alguna tiene pedido,
        o es mas nueva que el plazo, o el cliente tiene usuario del portal, no
        se toca nada. Una direccion suelta de un cliente de verdad se archiva
        ella sola.
        """
        limite = fields.Datetime.now() - timedelta(
            days=visar_chat_address_days(self.env))
        marcadas = self.sudo().search([
            ('visar_chat_sin_reserva', '=', True),
            ('create_date', '<', limite),
        ])
        archivadas = self.browse()
        for ficha in marcadas:
            if ficha in archivadas:
                continue
            familia = ficha
            if not ficha.parent_id:
                hijos = self.sudo().search([('parent_id', '=', ficha.id)])
                if ficha.user_ids or any(h.create_date >= limite for h in hijos):
                    continue
                familia |= hijos
            if familia._visar_chat_tiene_pedidos():
                continue
            familia.write({'active': False})
            archivadas |= familia
        if archivadas:
            _logger.info(
                "Chat sin reserva: %d fichas archivadas (ids %s)",
                len(archivadas), archivadas.ids)
        return len(archivadas)

    # ------------------------------------------------------------------
    # Un número, un cliente (12-nov-2026)
    # ------------------------------------------------------------------
    @api.model
    def _visar_cliente_por_telefono(self, phone):
        """(cliente, ambiguo) para un teléfono. Regla ÚNICA del agente.

        Oficina suele crear la dirección de entrega con el mismo teléfono que el
        cliente, y la regla de «varios contactos con ese número = no se revela
        nada» dejaba al agente ciego para ese cliente: «No encontré servicios
        asociados a este número» (Luis Ángel, 12-nov-2026). El cliente y sus
        direcciones son UNA persona: si todas las coincidencias cuelgan del mismo
        cliente (`commercial_partner_id`), se devuelve el cliente. La ambigüedad
        sigue siendo real —y no se revela nada— cuando el número está en dos
        clientes distintos.
        """
        Partner = self.sudo()
        vacio = Partner.browse()
        clave = Partner._visar_phone_nat10_value(phone)
        if not clave:
            return vacio, False
        encontrados = Partner.search([('visar_phone_nat10', '=', clave)])
        if not encontrados:
            return vacio, False
        if len(encontrados) == 1:
            return encontrados, False
        clientes = encontrados.mapped('commercial_partner_id')
        if len(clientes) == 1 and clientes.active:
            return clientes, False
        return vacio, True
