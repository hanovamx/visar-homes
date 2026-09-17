# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # --- Venta en campo (upsell del técnico) ---
    # Desde el 17-sep-2026 el adicional se agrega como LÍNEA del pedido original del
    # servicio (antes se creaba un pedido aparte). Eso obliga a marcar la línea: el
    # `task_id` nativo no sirve para reconocerla porque las líneas del servicio
    # contratado también lo llevan (lo pone `industry_fsm_sale`). Este marcador es lo
    # que distingue "esto lo vendió el técnico en la puerta" de "esto es lo que el
    # cliente ya había comprado", y es lo que permite facturar SOLO el adicional.
    visar_upsell_task_id = fields.Many2one(
        'project.task', string="Vendido en el servicio", readonly=True, copy=False,
        index='btree_not_null',
        help="Servicio en el que el técnico vendió esta línea durante la visita. "
             "Vacío = línea del servicio contratado, no vendida en campo.")
    # Base de la comisión del técnico. Vive en la LÍNEA y no en la cabecera porque
    # el pedido original puede acumular adicionales de varias visitas (una póliza
    # con varias tareas), cada una vendida por un técnico distinto.
    visar_upsell_employee_id = fields.Many2one(
        'hr.employee', string="Vendido por (técnico)", readonly=True, copy=False,
        index='btree_not_null',
        help="Técnico que vendió esta línea en sitio. Base para su comisión.")
    visar_upsell_at = fields.Datetime(
        string="Vendido en sitio el", readonly=True, copy=False,
        help="Momento en que el técnico agregó esta línea desde la app de campo.")
