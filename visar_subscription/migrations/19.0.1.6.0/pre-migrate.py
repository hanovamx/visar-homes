# -*- coding: utf-8 -*-
"""El tipo de visita se rellena ANTES de que el ORM cargue la nueva definición.

`visar_is_warranty` pasa a derivarse de `visar_visit_kind`. Si el tipo llegara vacío a
la carga del registro, el cómputo escribiría `False` en todas las visitas y Visar
perdería de golpe qué visitas fueron de garantía — que es justo lo que alimenta
`visar_warranty_rate`, el indicador con el que se ajusta el precio en la renovación.

Por eso la columna se crea y se llena aquí, en pre-migrate: cuando el ORM llegue, el
cómputo devuelve exactamente lo que ya había y no se mueve ni una fila.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE project_task
          ADD COLUMN IF NOT EXISTS visar_visit_kind varchar
    """)
    cr.execute("""
        UPDATE project_task
           SET visar_visit_kind = CASE WHEN visar_is_warranty THEN 'garantia'
                                       ELSE 'preventiva' END
         WHERE visar_visit_kind IS NULL
    """)
    cr.execute("""
        SELECT visar_visit_kind, count(*)
          FROM project_task
         WHERE visar_subscription_order_id IS NOT NULL
         GROUP BY visar_visit_kind
    """)
    for kind, total in cr.fetchall():
        _logger.info("Visitas de póliza marcadas como %s: %s", kind, total)
