# -*- coding: utf-8 -*-
"""Fumigación "solo exterior": los tramos de jardín, a su variante sin interior.

El sitio cobraba interior + jardín a quien pedía solo jardín (1,840 en vez de
1,150 en zona A, 1,610 en vez de 920), porque los tramos de exterior apuntaban a
variantes con el interior fijo en "1-250". El porqué completo, y por qué no bastó
con cambiar el tabulador a mano, está en
`product.template._visar_wire_exterior_only_variants` (visar_base).

Va como migración y no como dato XML porque lo que se toca lo dio de alta Visar a
mano (variantes, lista de precios, tabulador): aquí solo se cosen entre sí, por
atributos y nunca por id. Idempotente; en una base sin variante "0" no hace nada.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    templates = env['visar.service.dimension'].sudo().search([]).mapped('product_tmpl_id')
    cambios = templates._visar_wire_exterior_only_variants()
    _logger.info("Solo exterior: %s cambio(s) en el tabulador", len(cambios))
