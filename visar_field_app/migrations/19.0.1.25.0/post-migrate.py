# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.25.0, cose los upsells YA VENDIDOS al servicio que los
originó.

Hasta ahora el pedido de adicionales solo se enlazaba de cabecera a cabecera
(`project.task.visar_upsell_order_id` / `sale.order.visar_upsell_task_id`), así que
en el servicio externo no aparecía por ningún lado su contenido y desde la venta
original no había vuelta atrás. Los pedidos nuevos ya nacen cosidos; esta migración
cubre los que existen (QA y el servidor de pruebas):

  * `sale.order.line.task_id` — enlace línea→servicio, el mismo campo con el que el
    FSM nativo cuelga los materiales de una tarea;
  * `sale.order.visar_upsell_source_order_id` — pedido (o póliza) del que salió el
    servicio.

Idempotente: solo escribe donde falta el dato, así que repetir la actualización no
toca nada. No reasigna un `task_id` ya puesto — si alguien lo movió a mano, esa
decisión gana.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env['sale.order'].search([('visar_upsell_task_id', '!=', False)])
    if not orders:
        return

    lines_fixed = 0
    for order in orders:
        task = order.visar_upsell_task_id
        if not order.visar_upsell_source_order_id and task.sale_order_id:
            order.visar_upsell_source_order_id = task.sale_order_id.id
        pending = order.order_line.filtered(
            lambda line: not line.display_type and not line.task_id)
        if pending:
            pending.task_id = task.id
            lines_fixed += len(pending)

    _logger.info(
        "Visar upsell: %s pedidos revisados, %s líneas enlazadas a su servicio.",
        len(orders), lines_fixed)
