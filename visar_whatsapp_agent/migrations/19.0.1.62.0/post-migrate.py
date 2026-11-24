# -*- coding: utf-8 -*-
"""La base de conocimiento deja de decir «sin cupones» (REQ-7600).

Desde ahora el cliente captura su código de descuento o tarjeta de regalo en la
liga de pago (`visar_appointment`). La ficha es `noupdate` —Visar la edita a
mano—, así que el XML nuevo no la alcanza en una base existente. Se reemplaza
SOLO la frase original: si Visar ya la reescribió, se respeta lo suyo y se avisa
en el log para revisarlo a mano.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

VIEJA = "- Precios con IVA. Sin cupones, promociones, meses sin intereses ni transferencia."
NUEVA = ("- Precios con IVA. Sin meses sin intereses ni transferencia.\n"
         "- Código de descuento o tarjeta de regalo: el cliente lo captura en la "
         "liga de pago, en el campo «Tarjeta de regalo o código de descuento», "
         "antes de pagar. No aplica a pólizas. El agente no lo valida ni lo "
         "aplica.")


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False})
    ficha = env.ref('visar_whatsapp_agent.conocimiento_politicas_cita',
                    raise_if_not_found=False)
    if not ficha:
        return
    if VIEJA in (ficha.body or ''):
        ficha.body = ficha.body.replace(VIEJA, NUEVA)
        _logger.info("Conocimiento «%s»: ya no dice «sin cupones».", ficha.name)
    elif any(p in (ficha.body or '').lower() for p in ('cupon', 'cupón')):
        _logger.warning(
            "Conocimiento «%s» (id %s) menciona cupones con un texto que Visar "
            "editó: revísalo a mano contra REQ-7600.", ficha.name, ficha.id)
