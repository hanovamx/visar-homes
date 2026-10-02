# -*- coding: utf-8 -*-
"""Enlaza las ordenes que ya existian con su ficha de CRM.

Hasta la 19.0.1.4.0 **nadie escribia `sale.order.opportunity_id`**: 0 de 379
ordenes en produccion, y por eso la pestana "Cotizaciones" de toda ficha estaba
vacia en todos los canales. El camino nuevo lo escribe en borrador; esto es solo
para el historico.

**Se enlaza solo donde se puede DEMOSTRAR de quien es la orden.** La base trae
mucho dato de prueba y rellenar por aproximacion seria peor que no rellenar:
`7774501440` lo comparten siete `res.partner` (uno es Administrator, con 171
ordenes) y tiene fichas duplicadas por grupo. Medido antes de escribir esto:
**197 de 220 pares (orden, grupo) casan con mas de una ficha.** Las dos puertas
(un solo contacto por telefono, una sola ficha por grupo) viven en
`crm.lead._visar_crm_backfill_order_links`, con su razonamiento.

> ⚠️ **Se esperan POCOS enlaces — del orden de 10, no de 379.** No es que la
> migracion falle: es que el resto no se puede demostrar. El log dice, orden por
> orden, cual se dejo intacta y por que. Si algun dia alguien limpia los
> telefonos de prueba y las fichas duplicadas, volver a correr
> `_visar_crm_backfill_order_links()` desde un shell enlaza lo que ya se pueda.

**No reparte `expected_revenue` del historico**: solo enlaza. Repartirlo pisaria
cifras que alguien pudo ajustar a mano hace meses.

Los 18 leads que `appointment_crm` creo en octubre quedan **fuera a proposito**:
16 son del partner Administrator y solo 2 son clientes reales. Eso es limpieza de
datos a mano (runbook), no una migracion: mutar registros segun "el nombre
contiene Administrator" es exactamente la adivinanza que este repo evita.
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
            "visar_crm: fallo el relleno de enlaces orden<->ficha. El modulo "
            "queda actualizado y el camino nuevo funciona; el historico se "
            "puede rellenar luego desde un shell con "
            "env['crm.lead']._visar_crm_backfill_order_links().")
