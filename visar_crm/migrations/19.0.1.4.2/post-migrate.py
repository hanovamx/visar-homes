# -*- coding: utf-8 -*-
"""Rescata las fichas que `appointment_crm` creo sin identidad Visar.

Visar pregunto por que el lead **604** —de una reserva web de hoy— seguia con la
pestana "Cotizaciones" vacia despues del arreglo. La respuesta: lo creo
`appointment_crm` tres horas ANTES del despliegue, con solo
`{name, partner_id, type, user_id, description}`. Sin `visar_wa_phone_norm` y sin
`visar_service_group_id`, que son la pareja por la que busca el emparejador: su
orden no tenia forma de encontrarlo.

El plan dejaba esas 17 fichas "fuera, para un runbook manual", porque 16 son
ruido de prueba del partner Administrator. Pero el runbook no hacia falta: la
ficha **apunta a ese cliente**, y eso es prueba de propiedad **mas fuerte** que
el telefono. Asi que el relleno gana un respaldo por cliente —solo cuando la via
del grupo viene VACIA, nunca cuando viene ambigua— y no hay que mutar nada a
mano ni adivinar por el nombre.

Es aditivo: solo mira ordenes con `opportunity_id` vacio, asi que no toca ningun
enlace existente. Idempotente.

A partir de ahora el camino vivo tambien las rescata
(`_visar_crm_lead_sin_identidad`): la siguiente reserva de ese cliente le pone
telefono y grupo a la ficha vieja en vez de crear una segunda al lado, que es
como el cliente habria acabado con dos fichas en el mismo tablero.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    try:
        env['crm.lead']._visar_crm_backfill_order_links()
    except Exception:  # noqa: BLE001 - el relleno no tumba el -u
        _logger.exception(
            "visar_crm: fallo el relleno con respaldo por cliente. Se puede "
            "repetir desde un shell con "
            "env['crm.lead']._visar_crm_backfill_order_links().")
