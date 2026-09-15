# -*- coding: utf-8 -*-
"""REQ-004: el upsell de campo se confirma aunque no tenga lista de precios.

Lo confirma el técnico en casa del cliente; bloquearlo ahí deja la venta a medias.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestUpsellSinLista(TransactionCase):

    def test_el_upsell_se_confirma_sin_lista(self):
        cliente = self.env['res.partner'].create({'name': 'Cliente upsell REQ-004'})
        proyecto = self.env['project.project'].create({
            'name': 'FSM REQ-004', 'is_fsm': True, 'company_id': self.env.company.id})
        tarea = self.env['project.task'].create({
            'name': 'Servicio REQ-004', 'project_id': proyecto.id,
            'partner_id': cliente.id})
        producto = self.env['product.product'].create({
            'name': 'Malla REQ-004', 'type': 'consu', 'list_price': 50.0})
        orden = self.env['sale.order'].create({
            'partner_id': cliente.id, 'visar_upsell_task_id': tarea.id,
            'order_line': [(0, 0, {'product_id': producto.id})]})
        orden.pricelist_id = False

        orden.action_confirm()

        self.assertEqual(orden.state, 'sale')
