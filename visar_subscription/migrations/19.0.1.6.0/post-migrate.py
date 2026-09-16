# -*- coding: utf-8 -*-
"""Numera las visitas ya creadas y les propone fecha a las que están sin agendar.

Dos rellenos, en este orden:

1. **Nº de visita.** El «3/12» solo existía dentro del TÍTULO, así que no se podía
   ordenar ni filtrar por él. Se reconstruye por lote —(póliza, factura, línea
   representante)— y por orden de creación, que es exactamente como se generó: leerlo
   del texto del título sería adivinar sobre un campo traducible que alguien puede
   haber editado a mano.
2. **Fecha propuesta.** Se recorre la serie preventiva de cada póliza con visitas
   pendientes. Las visitas ya agendadas no se tocan: lo único que se escribe es la
   propuesta de las que no tienen fecha.

Nada de esto cambia una sola fecha real de trabajo ni avisa a ningún cliente.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        WITH lote AS (
            SELECT id,
                   row_number() OVER (PARTITION BY visar_subscription_order_id,
                                                   visar_source_invoice_id,
                                                   visar_source_line_id
                                      ORDER BY id) AS seq,
                   count(*) OVER (PARTITION BY visar_subscription_order_id,
                                               visar_source_invoice_id,
                                               visar_source_line_id) AS total
              FROM project_task
             WHERE visar_subscription_order_id IS NOT NULL
               AND visar_visit_kind = 'preventiva'
        )
        UPDATE project_task t
           SET visar_visit_seq = lote.seq,
               visar_visit_total = lote.total
          FROM lote
         WHERE lote.id = t.id
           AND COALESCE(t.visar_visit_seq, 0) = 0
    """)
    _logger.info("Visitas de póliza numeradas: %s", cr.rowcount)

    cr.execute("""
        SELECT DISTINCT visar_subscription_order_id
          FROM project_task
         WHERE visar_subscription_order_id IS NOT NULL
           AND visar_visit_kind = 'preventiva'
           AND planned_date_begin IS NULL
           AND state NOT IN ('1_done', '1_canceled')
    """)
    order_ids = [row[0] for row in cr.fetchall()]
    if not order_ids:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    env['sale.order'].browse(order_ids).exists()._visar_schedule_visit_due_dates()
    # Sin esto el recuento de abajo se hace sobre lo que hay en la BD, que todavía no
    # incluye lo que el ORM tiene en memoria: la migración funcionaba y el log decía
    # cero, que es peor que no decir nada.
    env.flush_all()

    cr.execute("""
        -- COALESCE porque una visita cuya póliza no tiene ancla no se escribe (no hay
        -- nada que cambiar), así que su booleano se queda en NULL: sin esto el recuento
        -- se comía justo las que interesan.
        SELECT count(*) FILTER (WHERE visar_visit_due_date IS NOT NULL),
               count(*) FILTER (WHERE COALESCE(visar_visit_due_out_of_term, false)),
               count(*) FILTER (WHERE visar_visit_due_date IS NULL
                                  AND NOT COALESCE(visar_visit_due_out_of_term, false))
          FROM project_task
         WHERE visar_subscription_order_id IS NOT NULL
           AND visar_visit_kind = 'preventiva'
           AND planned_date_begin IS NULL
           AND state NOT IN ('1_done', '1_canceled')
    """)
    con_fecha, fuera, sin_ancla = cr.fetchone()
    _logger.info(
        "Visitas de póliza pendientes: %s con fecha propuesta, %s fuera de vigencia, "
        "%s sin ancla (ninguna visita de su póliza tiene fecha real).",
        con_fecha, fuera, sin_ancla)
