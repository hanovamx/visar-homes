# -*- coding: utf-8 -*-
"""Servicio vendido y hecho en la misma visita (18-sep-2026).

El técnico va a una visita de valoración y ahí mismo deja hecho el servicio. Lo
que se protege aquí, en orden de lo que cuesta si falla:

* que el cliente pague lo justo: el mismo precio que por web y WhatsApp, menos la
  valoración que ya pagó, una sola vez y nunca de más;
* que la factura del cobro no vuelva a cobrar la valoración;
* que el servicio nazca como su propia visita, con su hoja, sin mandarle al
  cliente otra vez los avisos de "voy en camino" y "ya llegué";
* que la liga salga del número de Visar, y que un pago de prueba nunca se ofrezca
  sin que alguien lo haya encendido a propósito.
"""
from datetime import datetime, timedelta

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_field_app.models.upsell_servicio import (
    PARAM_PAGO_PRUEBA, PARAM_PRODUCTO_CREDITO)

CP = '99901'


@tagged('post_install', '-at_install')
class TestServicioEnVisita(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company = env.company
        cls.lista = env['product.pricelist'].create({
            'name': 'Lista zona prueba campo', 'company_id': company.id})
        cls.zona = env['visar.zone'].create({
            'name': 'Zona prueba campo', 'code': 'ZPC', 'pricelist_id': cls.lista.id})
        env['visar.zone.cp'].create({'name': CP, 'zone_id': cls.zona.id})
        cls.cliente = env['res.partner'].create({
            'name': 'Cliente valoracion', 'zip': CP, 'phone': '8181234567'})
        cls.tecnico = env['hr.employee'].create({'name': 'Tecnico que valora'})

        cls.proyecto_visita = env['project.project'].create({
            'name': 'FSM valoraciones', 'is_fsm': True, 'allow_billable': True,
            'company_id': company.id})
        cls.proyecto_servicio = env['project.project'].create({
            'name': 'FSM poda en sitio', 'is_fsm': True, 'allow_billable': True,
            'company_id': company.id})

        # Servicio con tabulador: 0-100 m² se vende; más de 100 es "valoración".
        cls.poda = env['product.template'].create({
            'name': 'Poda en sitio', 'type': 'service', 'invoice_policy': 'order',
            'service_tracking': 'task_global_project',
            'project_id': cls.proyecto_servicio.id,
            'list_price': 690.0, 'sale_ok': True, 'taxes_id': [(6, 0, [])],
            'visar_is_service': True, 'visar_upsell_ok': True})
        cls.poda_variante = cls.poda.product_variant_id
        cls.poda_variante.visar_zone_id = cls.zona
        grupo = env['visar.service.group'].create({
            'name': 'Grupo poda prueba', 'code': 'poda_prueba_campo'})
        cls.dimension = env['visar.service.dimension'].create({
            'group_id': grupo.id, 'name': 'Poda prueba', 'code': 'poda_prueba_campo',
            'measure_type': 'direct', 'product_tmpl_id': cls.poda.id})
        Tier = env['visar.service.tier']
        Tier.create({'product_tmpl_id': cls.poda.id, 'name': '0 - 100',
                     'm2_min': 0, 'm2_max': 100, 'product_id': cls.poda_variante.id})
        Tier.create({'product_tmpl_id': cls.poda.id, 'name': 'Más de 100',
                     'm2_min': 101, 'm2_max': 99999, 'is_valuation': True,
                     'product_id': cls.poda_variante.id})

        cls.valoracion = env['product.product'].create({
            'name': 'Visita de valoracion prueba', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 500.0,
            'taxes_id': [(6, 0, [])], 'visar_is_valuation': True})
        cls.estacion = env['product.product'].create({
            'name': 'Estacion prueba', 'type': 'consu', 'invoice_policy': 'order',
            'list_price': 100.0, 'sale_ok': True, 'visar_upsell_ok': True,
            'taxes_id': [(6, 0, [])]})
        cls.descuento = env['product.product'].create({
            'name': 'Descuento prueba', 'type': 'service', 'invoice_policy': 'order',
            'list_price': 0.0, 'taxes_id': [(6, 0, [])]})
        env['ir.config_parameter'].sudo().set_param(
            PARAM_PRODUCTO_CREDITO, cls.descuento.id)
        env['ir.config_parameter'].sudo().set_param(PARAM_PAGO_PRUEBA, False)

    # ------------------------------------------------------------------
    def _visita(self, pagada=True, cliente=None):
        """Visita de valoración en ejecución, nacida de un pedido confirmado."""
        pedido = self.env['sale.order'].create({
            'partner_id': (cliente or self.cliente).id,
            'pricelist_id': self.lista.id,
            'order_line': [(0, 0, {
                'product_id': self.valoracion.id, 'product_uom_qty': 1})]})
        pedido.action_confirm()
        if pagada:
            factura = pedido._create_invoices()
            factura.action_post()
            self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=factura.ids
            ).create({})._create_payments()
        inicio = datetime.now().replace(microsecond=0)
        tarea = self.env['project.task'].create({
            'name': 'Visita de valoracion', 'project_id': self.proyecto_visita.id,
            'partner_id': (cliente or self.cliente).id,
            'sale_line_id': pedido.order_line[0].id,
            'visar_technician_ids': [(6, 0, self.tecnico.ids)],
            'planned_date_begin': inicio,
            'date_deadline': inicio + timedelta(hours=2)})
        tarea._visar_set_stage(2)
        tarea.write({'visar_arrived_at': inicio, 'visar_service_start': inicio})
        return tarea

    def _vender_poda(self, tarea, m2=80):
        return tarea._visar_upsell_add_service(self.tecnico, {self.dimension.id: m2})

    def _credito(self, tarea):
        return tarea._visar_upsell_lines().filtered('visar_valuation_credit')

    # ------------------------------------------------------------------
    # Catálogo y precio
    # ------------------------------------------------------------------
    def test_01_el_servicio_se_ofrece_por_dimension_con_zona(self):
        tarea = self._visita()
        ofertas = tarea._visar_upsell_service_offers()
        self.assertIn(self.dimension.id, [o['id'] for o in ofertas])

        sin_zona = self.env['res.partner'].create({'name': 'Sin CP', 'zip': '00000'})
        otra = self._visita(cliente=sin_zona)
        self.assertEqual(otra._visar_upsell_service_offers(), [],
                         "sin zona no se cotiza: sería cobrar la lista equivocada")
        ok, error = otra._visar_upsell_add_service(self.tecnico, {self.dimension.id: 80})
        self.assertFalse(ok)
        self.assertEqual(error, 'sin_zona')

    def test_02_el_precio_es_el_del_motor_del_agendado(self):
        """La misma casa cuesta lo mismo por web, WhatsApp y en la puerta."""
        tarea = self._visita()
        item, _error = self.dimension._visar_quote_item(80)
        cotizacion = self.env['appointment.type']._visar_quote_booking([item], self.zona)

        ok, error = self._vender_poda(tarea)
        self.assertTrue(ok, error)
        linea = tarea._visar_upsell_lines().filtered(
            lambda l: l.product_id == self.poda_variante)
        self.assertEqual(linea.price_total, cotizacion['total'])
        self.assertEqual(linea.order_id, tarea.sale_order_id,
                         "entra al pedido de la visita, no a uno nuevo")

    def test_02b_la_direccion_de_servicio_es_el_mismo_cliente(self):
        """El contacto de la visita es la DIRECCIÓN DE SERVICIO, hijo del cliente del
        pedido (73 de 80 visitas abiertas en producción, 18-sep-2026). Sigue siendo
        el mismo pedido."""
        direccion = self.env['res.partner'].create({
            'name': 'Casa del cliente', 'type': 'delivery',
            'parent_id': self.cliente.id, 'zip': CP, 'phone': '8181234567'})
        tarea = self._visita()
        tarea.partner_id = direccion
        pedidos_antes = self.env['sale.order'].search_count([])
        ok, error = self._vender_poda(tarea)
        self.assertTrue(ok, error)
        self.assertEqual(tarea._visar_upsell_order(), tarea.sale_order_id)
        self.assertEqual(self.env['sale.order'].search_count([]), pedidos_antes)

    def test_02c_otro_cliente_sigue_yendo_aparte(self):
        """Lo que la regla protege: el extra no se le factura a otra persona."""
        otro = self.env['res.partner'].create({'name': 'Otro cliente', 'zip': CP})
        tarea = self._visita()
        tarea.partner_id = otro
        self._vender_poda(tarea)
        self.assertNotEqual(tarea._visar_upsell_order(), tarea.sale_order_id)

    def test_03_fuera_de_tabulador_no_se_vende_en_sitio(self):
        tarea = self._visita()
        ok, error = self._vender_poda(tarea, m2=500)
        self.assertFalse(ok)
        self.assertEqual(error, 'valoracion')
        self.assertFalse(tarea._visar_upsell_lines())

    def test_04_mientras_se_arma_el_carrito_no_nace_ninguna_visita(self):
        """Un pedido confirmado genera la tarea en cuanto nace la línea; un
        carrito que se edita dejaría servicios fantasma."""
        tarea = self._visita()
        antes = self.env['project.task'].search_count(
            [('sale_order_id', '=', tarea.sale_order_id.id)])
        self._vender_poda(tarea)
        self.assertEqual(self.env['project.task'].search_count(
            [('sale_order_id', '=', tarea.sale_order_id.id)]), antes)

    # ------------------------------------------------------------------
    # Descuento de la visita de valoración
    # ------------------------------------------------------------------
    def test_05_la_valoracion_se_descuenta_del_servicio(self):
        tarea = self._visita()
        self._vender_poda(tarea)
        credito = self._credito(tarea)
        self.assertEqual(len(credito), 1)
        self.assertEqual(credito.price_total, -500.0,
                         "el monto sale de la línea de valoración del pedido")
        self.assertEqual(tarea.visar_upsell_amount_total, 190.0)

    def test_06_sin_valoracion_pagada_no_hay_descuento(self):
        tarea = self._visita(pagada=False)
        self._vender_poda(tarea)
        self.assertFalse(self._credito(tarea))
        self.assertEqual(tarea.visar_upsell_amount_total, 690.0)

    def test_07_solo_contra_servicios(self):
        tarea = self._visita()
        tarea._visar_upsell_add(self.tecnico, self.estacion.id, 1)
        self.assertFalse(self._credito(tarea), "una estación no se descuenta")
        self._vender_poda(tarea)
        self.assertEqual(self._credito(tarea).price_total, -500.0)
        self.assertEqual(tarea.visar_upsell_amount_total, 290.0)

    def test_08_nunca_por_mas_que_el_servicio(self):
        barata = self.env['product.pricelist.item'].create({
            'pricelist_id': self.lista.id, 'applied_on': '0_product_variant',
            'product_id': self.poda_variante.id, 'compute_price': 'fixed',
            'fixed_price': 300.0})
        self.assertTrue(barata)
        tarea = self._visita()
        self._vender_poda(tarea)
        self.assertEqual(self._credito(tarea).price_total, -300.0)
        self.assertEqual(tarea.visar_upsell_amount_total, 0.0,
                         "el cobro no puede quedar negativo")

    def test_09_una_sola_vez_por_pedido(self):
        tarea = self._visita()
        self._vender_poda(tarea)
        tarea._visar_upsell_confirm(self.tecnico)
        # Otra visita del MISMO pedido vende otro servicio.
        otra = self.env['project.task'].create({
            'name': 'Segunda visita', 'project_id': self.proyecto_visita.id,
            'partner_id': self.cliente.id,
            'sale_line_id': tarea.sale_order_id.order_line.filtered(
                lambda l: l.product_id == self.valoracion).id,
            'visar_technician_ids': [(6, 0, self.tecnico.ids)]})
        otra._visar_set_stage(2)
        otra._visar_upsell_add_service(self.tecnico, {self.dimension.id: 50})
        self.assertFalse(self._credito(otra), "el pedido ya tuvo su descuento")

    def test_10_un_descuento_capturado_a_mano_cuenta(self):
        """Antes de esto el descuento se ponía a mano (S00138, S00144, S00147)."""
        tarea = self._visita()
        self.env['sale.order.line'].create({
            'order_id': tarea.sale_order_id.id, 'product_id': self.descuento.id,
            'product_uom_qty': 1, 'price_unit': -500.0})
        self._vender_poda(tarea)
        self.assertFalse(self._credito(tarea))

    def test_11_quitar_el_servicio_quita_el_descuento(self):
        tarea = self._visita()
        self._vender_poda(tarea)
        credito = self._credito(tarea)
        self.assertFalse(tarea._visar_upsell_remove(credito.id),
                         "el descuento no se quita a mano")
        servicio = tarea._visar_upsell_lines().filtered(
            lambda l: l.product_id == self.poda_variante)
        self.assertTrue(tarea._visar_upsell_remove(servicio.id))
        self.assertFalse(tarea._visar_upsell_lines(), "sin servicio no queda descuento")

    # ------------------------------------------------------------------
    # Generar el cobro: servicio nuevo, factura, liga
    # ------------------------------------------------------------------
    def test_12_al_cobrar_nace_el_servicio_ya_en_ejecucion(self):
        tarea = self._visita()
        self._vender_poda(tarea)
        tarea._visar_upsell_confirm(self.tecnico)

        nueva = tarea.visar_upsell_service_task_ids
        self.assertEqual(len(nueva), 1)
        self.assertEqual(nueva.sale_order_id, tarea.sale_order_id,
                         "en la misma orden de venta")
        self.assertEqual(nueva.project_id, self.proyecto_servicio)
        self.assertIn(tarea.sale_order_id.name, nueva.name)
        self.assertIn('Poda en sitio', nueva.name)
        self.assertEqual(nueva.visar_technician_ids, self.tecnico)
        self.assertEqual(nueva.stage_id, nueva._visar_fsm_stage(2), "En ejecución")
        self.assertTrue(nueva.visar_service_start,
                        "entra directo al paso de la hoja de trabajo")
        self.assertTrue(nueva.planned_date_begin and nueva.date_deadline)
        avisos = self.env['visar.wa.message'].search([('task_id', '=', nueva.id)])
        self.assertFalse(avisos.filtered(
            lambda m: m.template_key in ('enroute', 'arrived')),
            "el técnico ya está ahí: nada de 'voy en camino' ni 'ya llegué'")

    def test_13_la_factura_es_solo_el_extra_con_el_descuento(self):
        tarea = self._visita()
        self._vender_poda(tarea)
        tarea._visar_upsell_confirm(self.tecnico)
        factura = tarea._visar_upsell_invoice()
        self.assertEqual(factura.amount_total, 190.0)
        self.assertNotIn(self.valoracion, factura.invoice_line_ids.product_id,
                         "la valoración ya pagada no se vuelve a facturar")
        self.assertEqual(len(tarea.sale_order_id.invoice_ids), 2,
                         "la de la valoración y una aparte para el extra")

    def test_13b_el_pago_de_la_cita_no_paga_el_adicional(self):
        """Pedido pagado en línea y sin facturar (lo normal en producción): la
        factura del adicional no puede quedarse con ese pago."""
        demo = self.env['payment.provider'].sudo().search(
            [('code', '=', 'demo')], limit=1)
        if not demo:
            self.skipTest("Sin proveedor Demo en esta BD")
        tarea = self._visita(pagada=False)
        pedido = tarea.sale_order_id
        tx = self.env['payment.transaction'].sudo().create({
            'provider_id': demo.id,
            'payment_method_id': demo.payment_method_ids[:1].id,
            'amount': pedido.amount_total, 'currency_id': pedido.currency_id.id,
            'partner_id': pedido.partner_id.id,
            'sale_order_ids': [(6, 0, pedido.ids)]})
        tx._set_done()
        self.assertTrue(pedido.transaction_ids, "fixture: pedido pagado en línea")

        tarea._visar_upsell_add(self.tecnico, self.estacion.id, 1)
        tarea._visar_upsell_confirm(self.tecnico)
        factura = tarea._visar_upsell_invoice()
        self.assertFalse(factura.transaction_ids,
                         "el pago de la cita no se liga a la factura del adicional")
        self.assertEqual(factura.amount_residual, 100.0, "el adicional sigue por cobrar")
        self.assertNotEqual(tarea._visar_upsell_state(), 'pagado')

    def test_14_cobrar_dos_veces_no_duplica_nada(self):
        tarea = self._visita()
        self._vender_poda(tarea)
        tarea._visar_upsell_confirm(self.tecnico)
        tarea._visar_upsell_confirm(self.tecnico)
        self.assertEqual(len(tarea.visar_upsell_service_task_ids), 1)
        self.assertEqual(len(tarea.sale_order_id.invoice_ids), 2)

    def test_15_la_liga_sale_del_numero_de_visar(self):
        tarea = self._visita()
        self.env['ir.config_parameter'].sudo().set_param(PARAM_PAGO_PRUEBA, True)
        self._vender_poda(tarea)
        tarea._visar_upsell_confirm(self.tecnico)
        if not tarea._visar_upsell_payment_link():
            self.skipTest("No hay proveedor de pago (ni de prueba) en esta BD")
        aviso = self.env['visar.wa.message'].search([
            ('task_id', '=', tarea.id), ('template_key', '=', 'upsell_payment')])
        self.assertEqual(len(aviso), 1)
        self.assertTrue(tarea.visar_upsell_link_sent_at)
        tarea._visar_upsell_send_payment_link()
        self.assertEqual(self.env['visar.wa.message'].search_count([
            ('task_id', '=', tarea.id), ('template_key', '=', 'upsell_payment')]), 1,
            "una sola vez")

    def test_16_el_pago_de_prueba_solo_con_el_ajuste(self):
        tarea = self._visita()
        self._vender_poda(tarea)
        tarea._visar_upsell_confirm(self.tecnico)
        Provider = self.env['payment.provider'].sudo()
        if not Provider.search([('state', '=', 'test')], limit=1):
            self.skipTest("No hay proveedor en modo prueba en esta BD")
        if Provider.search([('state', '=', 'enabled')], limit=1):
            self.skipTest("Hay un proveedor real habilitado: la prueba no aplica")
        self.assertFalse(tarea._visar_upsell_providers(),
                         "sin el ajuste, un proveedor de prueba no se ofrece")
        self.env['ir.config_parameter'].sudo().set_param(PARAM_PAGO_PRUEBA, True)
        self.assertTrue(tarea._visar_upsell_providers())
        self.assertTrue(tarea._visar_upsell_is_test_payment(),
                        "la app lo marca como PRUEBA")

    def test_17_el_qr_se_genera_sin_reportlab(self):
        """El QR de cobro salía 500: el renderizador PNG de reportlab no está
        instalado en el servidor. Ahora se genera con `qrcode` + PIL."""
        from odoo.addons.visar_field_app.controllers.main import VisarFieldApp
        png = VisarFieldApp._upsell_qr_png('https://visar.test/pay/1')
        self.assertTrue(png.startswith(b'\x89PNG'))
