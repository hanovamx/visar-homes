# -*- coding: utf-8 -*-
"""Pone Nuevo León en las direcciones que se guardaron sin estado.

El wizard buscaba el estado por código 'NL' (en Odoo es 'NLE') y no lo
encontraba: toda dirección de servicio quedó sin estado, y las fichas de cliente
a las que se les prestó esa dirección (desde el 6-oct-2026) también. México lo
exige para facturar.

Solo se tocan contactos de México, sin estado, con calle, y cuyo CP está en la
tabla de cobertura de Visar, que es toda de Nuevo León.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    estado = env['sale.order']._visar_service_state()
    pais = env.ref('base.mx', raise_if_not_found=False)
    if not estado or not pais:
        return
    cps = env['visar.zone.cp'].search([]).mapped('name')
    contactos = env['res.partner'].with_context(active_test=False).search([
        ('country_id', '=', pais.id), ('state_id', '=', False),
        ('street', '!=', False), ('zip', 'in', cps)])
    contactos.write({'state_id': estado.id})
    _logger.info("Direcciones sin estado puestas en Nuevo León: %s (ids %s)",
                 len(contactos), contactos.ids)
