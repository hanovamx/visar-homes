# -*- coding: utf-8 -*-
"""Relleno de `project.task.visar_source_line_ids` desde el m2o histórico.

Las visitas creadas antes de la consolidación llevan su línea solo en
`visar_source_line_id`. Desde ahora el m2m es la fuente que usan la idempotencia por
(orden, factura, grupo) y el etiquetado de grupos de servicio: sin este relleno, la
siguiente factura pagada de una póliza vieja no encontraría las visitas ya creadas y
las volvería a generar.

Las parejas de visitas que YA existen no se fusionan. Hojas capturadas, firmas y PDFs
no tienen fusión limpia — mismo criterio que la consolidación de la venta puntual
(`.context/40-decisions.md`, 13-ago-2026): la regla nueva aplica a lo que se genere
de aquí en adelante.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        INSERT INTO visar_task_poliza_line_rel (task_id, line_id)
        SELECT t.id, t.visar_source_line_id
          FROM project_task t
         WHERE t.visar_source_line_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM visar_task_poliza_line_rel r
                WHERE r.task_id = t.id AND r.line_id = t.visar_source_line_id)
    """)
    _logger.info("visar_subscription: %s visitas enlazadas a su línea de póliza",
                 cr.rowcount)
