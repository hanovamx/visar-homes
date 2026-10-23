# -*- coding: utf-8 -*-
"""Versiones del prompt base: las que ya existian se marcan como usadas.

Sin esto, el prompt viejo que se archivo el 29-sep saldria como «Borrador» y se
podria borrar, cuando en realidad estuvo en uso. El activo toma su fecha de
creacion como «en uso desde»; los archivados, igual, y su ultima edicion como
«dejo de usarse».
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE visar_agent_prompt
           SET usado_desde = create_date,
               retirado_el = CASE WHEN active THEN NULL ELSE write_date END
         WHERE ruta IS NULL AND usado_desde IS NULL
     RETURNING id
    """)
    _logger.info("Versiones del prompt base fechadas: %s", [r[0] for r in cr.fetchall()])
