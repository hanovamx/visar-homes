# -*- coding: utf-8 -*-
"""Corrige la puerta del telefono del relleno de la 19.0.1.4.0.

El relleno contaba los contactos que comparten un telefono con un `search`
normal, y **el ORM inyecta `active = True`**: no veia los archivados. Medido en
produccion justo despues de desplegarlo, el telefono `8112772622` esta en **15
fichas de contacto con seis nombres distintos** y todas archivadas, asi que el
`search` devolvia **cero** y el relleno concluia "no se puede demostrar de quien
es la orden" por el motivo equivocado — con el numero correcto de casualidad.

Peor, en el otro sentido: un telefono con un contacto activo y catorce
archivados **pasaba** la puerta. Eso enlazo **4 ordenes** de "Luis Angel rios
jasso", cuyo numero esta en siete fichas.

Dos faenas:

1. **Deshacer** los enlaces que no pasan la puerta corregida. Son los 4 de
   arriba. El principio de este trabajo es enlazar solo donde se pueda
   demostrar, y dejarlos seria dejar justo lo que el principio prohibe. Una
   linea de log por caso, con los nombres que comparten el numero.
2. **Volver a rellenar** con la puerta buena, por si algun enlace legitimo se
   habia perdido.

Si algun dia alguien deduplica los contactos de produccion, volver a correr
`env['crm.lead']._visar_crm_backfill_order_links()` desde un shell enlaza lo que
ya se pueda demostrar.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    Lead = env['crm.lead']

    try:
        Lead._visar_crm_unlink_unprovable_links()
        Lead._visar_crm_backfill_order_links()
    except Exception:  # noqa: BLE001 - la correccion no tumba el -u
        _logger.exception(
            "visar_crm: fallo la correccion de la puerta del telefono. El "
            "modulo queda actualizado; se puede repetir desde un shell con "
            "env['crm.lead']._visar_crm_unlink_unprovable_links() y luego "
            "_visar_crm_backfill_order_links().")
