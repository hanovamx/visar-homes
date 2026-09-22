# -*- coding: utf-8 -*-
"""Tratamientos que se cotizan a mano, pedidos desde la hoja de trabajo (22-sep-2026).

Lo que se protege: que la hoja pida UNA cotización por servicio marcado y la retire
si se desmarca antes de cotizar; que el descuento de la valoración se aplique al
cotizar —también si el servicio es otro día— y nunca dos veces entre la visita y
sus cotizaciones; y los dos caminos: hacerlo en la misma visita o agendarlo aparte.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_field_app.models.upsell_servicio import PARAM_PRODUCTO_CREDITO

CP = '99902'


@tagged('post_install', '-at_install')
class TestCotizacionManual(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company = env.company
        cls.lista = env['product.pricelist'].create({
            'name': 'Lista cotizacion', 'company_id': company.id})
        cls.zona = env['visar.zone'].create({
            'name': 'Zona cotizacion', 'code': 'ZCQ', 'pricelist_id': cls.lista.id})
        env['visar.zone.cp'].create({'name': CP, 'zone_id': cls.zona.id})
        cls.cliente = env['res.partner'].create({'name': 'Cliente termitas', 'zip': CP})
        cls.tecnico = env['hr.employee'].create({'name': 'Tecnico que cotiza'})
        cls.proyecto = env['project.project'].create({
            'name': 'FSM valoraciones cotiza', 'is_fsm': True, 'allow_billable': True,
            'company_id': company.id})
        cls.valoracion = env['product.product'].create({
            'name': 'Valoracion cotiza', 'type': 'service', 'invoice_policy': 'order',
            'list_price': 500.0, 'taxes_id': [(6, 0, [])], 'visar_is_valuation': True})
        cls.termitas = env['product.template'].create({
            'name': 'Tratamiento antitermita prueba', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 0.0, 'taxes_id': [(6, 0, [])],
            'visar_quote_trigger': 'Termitas'})
        cls.descuento = env['product.product'].create({
            'name': 'Descuento cotiza', 'type': 'service', 'invoice_policy': 'order',
            'list_price': 0.0, 'taxes_id': [(6, 0, [])]})
        env['ir.config_parameter'].sudo().set_param(PARAM_PRODUCTO_CREDITO, cls.descuento.id)

    def _visita(self, pagada=True):
        pedido = self.env['sale.order'].create({
            'partner_id': self.cliente.id, 'pricelist_id': self.lista.id,
            'order_line': [(0, 0, {'product_id': self.valoracion.id, 'product_uom_qty': 1})]})
        pedido.action_confirm()
        if pagada:
            factura = pedido._create_invoices()
            factura.action_post()
            self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=factura.ids).create({})._create_payments()
        inicio = datetime.now().replace(microsecond=0)
        tarea = self.env['project.task'].create({
            'name': 'Valoracion con termitas', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id, 'sale_line_id': pedido.order_line[0].id,
            'planned_date_begin': inicio, 'date_deadline': inicio + timedelta(hours=1)})
        tarea._visar_set_stage(2)
        tarea.write({'visar_arrived_at': inicio, 'visar_service_start': inicio})
        return tarea

    def _hoja_marca(self, tarea, *nombres):
        """La hoja de la visita con esos servicios marcados."""
        with patch.object(type(tarea), '_visar_quote_identified_names',
                          lambda self: {n.lower() for n in nombres}):
            return tarea._visar_quote_requests_sync(self.tecnico)

    def _cotizar(self, cotizacion, precio):
        linea = cotizacion._visar_quote_service_lines()
        cotizacion.write({'order_line': [(1, linea.id, {'price_unit': precio})]})

    def _credito(self, pedido):
        return pedido.order_line.filtered('visar_valuation_credit')

    # ------------------------------------------------------------------
    def test_la_hoja_pide_una_cotizacion_por_servicio_marcado(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')

        self.assertEqual(len(cot), 1)
        self.assertEqual(cot.state, 'draft')
        self.assertEqual(cot.order_line.product_id.product_tmpl_id, self.termitas)
        self.assertEqual(cot.visar_quote_origin_task_id, tarea)
        self.assertEqual(cot.visar_quote_origin_order_id, tarea.sale_order_id)
        self.assertNotEqual(cot, tarea.sale_order_id, "cotización aparte")
        self.assertTrue(cot.activity_ids, "actividad para quien cotiza")

        self.assertFalse(self._hoja_marca(tarea, 'Termitas'), "guardar otra vez no duplica")
        self.assertEqual(len(tarea.visar_quote_order_ids), 1)

    def test_un_servicio_sin_producto_de_cotizacion_no_pide_nada(self):
        tarea = self._visita()
        self.assertFalse(self._hoja_marca(tarea, 'Riego'))

    def test_desmarcar_antes_de_cotizar_la_cancela(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._hoja_marca(tarea)
        self.assertEqual(cot.state, 'cancel')

    def test_desmarcar_ya_cotizada_no_la_toca(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        self._hoja_marca(tarea)
        self.assertEqual(cot.state, 'draft')

    def test_al_cotizar_entra_el_descuento_de_la_valoracion(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self.assertFalse(self._credito(cot), "en $0 no hay nada que descontar")

        self._cotizar(cot, 3000.0)
        self.assertEqual(self._credito(cot).price_unit, -500.0)
        self.assertEqual(cot.amount_total, 2500.0)

        self._cotizar(cot, 300.0)
        self.assertEqual(self._credito(cot).price_unit, -300.0, "nunca más que el servicio")

    def test_sin_valoracion_pagada_no_hay_descuento(self):
        tarea = self._visita(pagada=False)
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        self.assertFalse(self._credito(cot))

    def test_el_descuento_es_uno_entre_la_cotizacion_y_la_visita(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        self.assertTrue(self._credito(cot))

        # Un servicio vendido en la visita ya no lleva descuento: lo tiene la cotización.
        credito, _linea = tarea._visar_valuation_credit_for(
            cot._visar_quote_service_lines(), propios=self.env['sale.order.line'])
        self.assertEqual(credito, 0.0)

    def test_agendar_despues(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        with self.assertRaises(UserError):
            cot.action_visar_quote_schedule_later()  # todavía en $0

        self._cotizar(cot, 3000.0)
        cot.action_visar_quote_schedule_later()

        self.assertEqual(cot.visar_quote_path, 'agendar')
        self.assertEqual(cot.state, 'sent')
        self.assertEqual(cot.amount_total, 2500.0)
        self.assertFalse(cot.activity_ids, "la actividad de cotizar se cerró")
        self.assertEqual(tarea.sale_order_id.order_line.product_id, self.valoracion,
                         "el pedido de la valoración no se toca")

    def test_hacer_en_la_misma_visita(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)

        cot.action_visar_quote_in_visit()

        self.assertEqual(cot.state, 'cancel')
        self.assertEqual(cot.visar_quote_path, 'en_visita')
        self.assertEqual(tarea._visar_upsell_state(), 'borrador',
                         "el técnico lo ve en su carrito para generar el cobro")
        abiertas = tarea._visar_upsell_open_lines()
        self.assertIn(self.termitas, abiertas.product_id.product_tmpl_id)
        self.assertEqual(self._credito(tarea.sale_order_id).price_unit, -500.0,
                         "el descuento pasa al pedido de la visita, una sola vez")

        tarea._visar_upsell_confirm(self.tecnico)
        self.assertEqual(tarea._visar_upsell_invoice().amount_total, 2500.0)

    def test_en_la_misma_visita_solo_si_el_tecnico_sigue_ahi(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        tarea._visar_set_stage(3)  # ya se cerró la visita
        with self.assertRaises(UserError):
            cot.action_visar_quote_in_visit()
        self.assertEqual(cot.state, 'draft', "sigue disponible para agendar después")

    def test_lee_la_hoja_real_de_valoracion(self):
        """Sin simulación: el campo "Servicios identificados" de la hoja de valoración."""
        Plantilla = self.env['worksheet.template']
        plantilla = Plantilla.search([], limit=50).filtered(
            lambda t: t.model_id.model in self.env
            and 'x_servicios_identificados' in self.env[t.model_id.model]._fields)[:1]
        if not plantilla:
            self.skipTest("Esta BD no tiene la hoja de valoración con Servicios identificados")
        Hoja = self.env[plantilla.model_id.model]
        Servicio = self.env[Hoja._fields['x_servicios_identificados'].comodel_name]
        termitas = Servicio.search([('x_name', '=ilike', 'termitas')], limit=1) \
            or Servicio.create({'x_name': 'Termitas'})
        tarea = self._visita()
        tarea.worksheet_template_id = plantilla
        Hoja.create({'x_project_task_id': tarea.id,
                     'x_servicios_identificados': [(6, 0, termitas.ids)]})

        cot = tarea._visar_quote_requests_sync(self.tecnico)

        self.assertEqual(cot.order_line.product_id.product_tmpl_id, self.termitas)

    def test_agendar_despues_avisa_al_cliente_con_el_monto(self):
        self.cliente.phone = '5218190005566'
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        cot.action_visar_quote_schedule_later()

        aviso = self.env['visar.wa.message'].search([
            ('quote_order_id', '=', cot.id), ('template_key', '=', 'quote_ready')])
        self.assertEqual(len(aviso), 1)
        self.assertEqual(aviso.task_id, tarea, "cuelga de la visita que la pidió")
        self.assertIn('2,500.00', aviso.params_json, "el monto ya con el descuento")
        self.assertEqual(aviso._visar_wa_context().get('quote_id'), cot.id)
