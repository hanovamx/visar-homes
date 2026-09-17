# -*- coding: utf-8 -*-
"""El adicional se agrega al pedido ORIGINAL del servicio, no a uno nuevo.

Decisión de negocio del 17-sep-2026, tomada sobre datos: los 10 adicionales
vendidos hasta entonces tenían el pedido original confirmado y pagado en línea, y
aun así Odoo admite la línea nueva (pasó en producción en S00264, ya facturado).
Lo que NO se puede es facturar el pedido completo delante del cliente: casi
siempre está pagado pero sin facturar, y le llegaría de nuevo el servicio.

Las pólizas conservan el pedido aparte: la línea no se repetiría cada ciclo
(medido), pero se cobraría en la siguiente factura —puede ser dentro de un año— y
el técnico necesita cobrar en la puerta.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


class CasoAdicional(TransactionCase):
    """Fixture comun: un servicio de campo nacido de un pedido de venta."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.proyecto = cls.env['project.project'].create({
            'name': 'FSM adicionales', 'is_fsm': True, 'allow_billable': True,
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Cliente con adicionales', 'zip': '64000'})
        cls.empleado = cls.env['hr.employee'].create({'name': 'Tecnico de campo'})
        # Lista explícita: REQ-004 exige lista de precios para confirmar.
        cls.lista = cls.env['product.pricelist'].create({
            'name': 'Lista de la zona', 'company_id': cls.env.company.id})
        cls.servicio = cls.env['product.product'].create({
            'name': 'Fumigacion contratada', 'type': 'service',
            'list_price': 700.0, 'invoice_policy': 'order'})
        cls.extra = cls.env['product.product'].create({
            'name': 'Estacion antirroedores', 'type': 'consu',
            'list_price': 450.0, 'invoice_policy': 'order',
            'sale_ok': True, 'visar_upsell_ok': True})

    # ------------------------------------------------------------------
    def _pedido(self, confirmar=True, **vals):
        """Pedido del servicio contratado.

        Se confirma a mano salvo que se pida lo contrario, aunque en la practica
        crear la tarea de campo ya lo confirma: es lo que pasa en produccion (92 de
        96 pedidos con servicio estaban confirmados el 17-sep-2026).
        """
        pedido = self.env['sale.order'].create(dict({
            'partner_id': self.cliente.id,
            'pricelist_id': self.lista.id,
            'order_line': [(0, 0, {
                'product_id': self.servicio.id, 'product_uom_qty': 1})],
        }, **vals))
        if confirmar:
            pedido.action_confirm()
        return pedido

    def _tarea(self, pedido=None):
        vals = {'name': 'Servicio en sitio', 'project_id': self.proyecto.id,
                'partner_id': self.cliente.id}
        if pedido:
            vals['sale_line_id'] = pedido.order_line[0].id
        return self.env['project.task'].create(vals)

    def _vender(self, tarea, cantidad=1):
        return tarea._visar_upsell_add(self.empleado, self.extra.id, cantidad)


@tagged('post_install', '-at_install')
class TestAdicionalEnPedidoOriginal(CasoAdicional):

    def test_el_adicional_entra_como_linea_del_pedido_original(self):
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self.assertEqual(tarea.sale_order_id, pedido, "fixture: tarea ligada al pedido")
        pedidos_antes = self.env['sale.order'].search_count([])

        self.assertTrue(self._vender(tarea))

        self.assertEqual(self.env['sale.order'].search_count([]), pedidos_antes,
                         "no se crea un pedido nuevo: el adicional va en el original")
        self.assertEqual(tarea._visar_upsell_order(), pedido)
        linea = tarea._visar_upsell_lines()
        self.assertEqual(len(linea), 1)
        self.assertEqual(linea.order_id, pedido)
        self.assertEqual(linea.product_id, self.extra)
        self.assertEqual(linea.visar_upsell_employee_id, self.empleado,
                         "quien vendio la linea es la base de su comision")
        self.assertEqual(linea.task_id, tarea,
                         "el FSM nativo lista la linea por `task_id`")
        self.assertEqual(pedido.amount_total, 1150.0)

    def test_la_factura_del_cobro_solo_lleva_el_adicional(self):
        """El pedido original llega pagado pero SIN facturar: facturarlo completo
        le cobraria al cliente, en la puerta, el servicio que ya pago."""
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._vender(tarea)

        tarea._visar_upsell_confirm(self.empleado)

        factura = tarea._visar_upsell_invoice()
        self.assertTrue(factura, "sin factura no hay liga de pago")
        self.assertEqual(factura.amount_total, 450.0,
                         "solo lo vendido hoy, no los 1150 del pedido")
        self.assertEqual(factura.invoice_line_ids.product_id, self.extra)
        servicio = pedido.order_line.filtered(
            lambda l: l.product_id == self.servicio)
        self.assertEqual(servicio.qty_to_invoice, 1.0,
                         "el servicio contratado sigue pendiente para administracion")

    def test_un_pedido_ya_facturado_recibe_solo_la_complementaria(self):
        pedido = self._pedido()
        factura_servicio = pedido._create_invoices()
        factura_servicio.action_post()
        tarea = self._tarea(pedido)

        self._vender(tarea)
        tarea._visar_upsell_confirm(self.empleado)

        self.assertEqual(len(pedido.invoice_ids), 2)
        self.assertEqual(tarea._visar_upsell_invoice().amount_total, 450.0)
        self.assertNotEqual(tarea._visar_upsell_invoice(), factura_servicio,
                            "la factura del servicio no se toca")

    def test_el_precio_es_el_del_catalogo_y_no_el_de_la_lista_del_pedido(self):
        """El catalogo cotiza con la lista de la ZONA; el pedido puede traer otra.
        Lo que el tecnico le dijo al cliente es lo que se cobra."""
        otra = self.env['product.pricelist'].create({
            'name': 'Lista con 50%', 'company_id': self.env.company.id,
            'item_ids': [(0, 0, {
                'compute_price': 'percentage', 'percent_price': 50.0,
                'applied_on': '3_global'})]})
        pedido = self._pedido(pricelist_id=otra.id)
        tarea = self._tarea(pedido)
        precio = {p['id']: p['price'] for p in tarea._visar_upsell_catalog()}[self.extra.id]

        self._vender(tarea)

        self.assertEqual(tarea._visar_upsell_lines().price_total, precio)
        self.assertEqual(tarea.visar_upsell_amount_total, precio)

    def test_no_se_le_suma_cantidad_a_la_linea_del_servicio(self):
        """Si el mismo producto ya estaba vendido en el pedido (paso en S00264),
        el adicional es una linea NUEVA: subirle la cantidad a la otra seria
        cobrarle al cliente algo que hoy no se vendio."""
        pedido = self._pedido(confirmar=False)
        pedido.write({'order_line': [(0, 0, {
            'product_id': self.extra.id, 'product_uom_qty': 2})]})
        pedido.action_confirm()
        tarea = self._tarea(pedido)
        original = pedido.order_line.filtered(lambda l: l.product_id == self.extra)

        self._vender(tarea)

        self.assertEqual(original.product_uom_qty, 2.0, "la linea vendida antes no se toca")
        adicional = tarea._visar_upsell_lines()
        self.assertEqual(len(adicional), 1)
        self.assertNotEqual(adicional, original)
        self.assertEqual(adicional.product_uom_qty, 1.0)

    def test_quitar_un_adicional_del_pedido_confirmado_lo_deja_en_cero(self):
        """Odoo prohibe borrar lineas de un pedido confirmado; se dejan en 0 (es
        lo que hace tambien el catalogo de materiales del FSM nativo)."""
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._vender(tarea)
        linea = tarea._visar_upsell_lines()

        self.assertTrue(tarea._visar_upsell_remove(linea.id))

        self.assertTrue(linea.exists(), "la linea se queda como constancia")
        self.assertEqual(linea.product_uom_qty, 0.0)
        self.assertEqual(tarea._visar_upsell_state(), 'vacio')
        self.assertEqual(pedido.amount_total, 700.0, "vuelve a valer lo contratado")

    def test_el_efectivo_se_sella_en_la_visita_y_no_en_el_pedido(self):
        """El pedido original puede acumular adicionales de varias visitas: 'quien
        recibio el efectivo' solo tiene sentido por visita."""
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._vender(tarea)

        self.assertTrue(tarea._visar_upsell_register_cash(self.empleado))

        self.assertTrue(tarea.visar_upsell_cash_at)
        self.assertEqual(tarea.visar_upsell_cash_by_id, self.empleado)
        self.assertFalse(pedido.visar_upsell_cash_at,
                         "el sello de cabecera es solo para el pedido aparte")
        self.assertEqual(tarea._visar_upsell_state(), 'pagado')

    def test_el_reporte_firmado_totaliza_solo_lo_vendido_hoy(self):
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._vender(tarea)
        tarea._visar_upsell_confirm(self.empleado)

        seccion = tarea._visar_upsell_report_section()

        total = seccion['fields'][0]['rows'][-1][-1]['text']
        self.assertIn('450', total, "el total del PDF no puede traer el servicio")


@tagged('post_install', '-at_install')
class TestCuandoSigueSiendoPedidoAparte(CasoAdicional):
    """Los cuatro casos en que meter la linea en el pedido original seria peor."""

    def _es_aparte(self, tarea):
        pedido = tarea._visar_upsell_order()
        return bool(pedido) and pedido.visar_upsell_task_id == tarea

    def test_una_poliza_conserva_su_pedido_aparte(self):
        if 'subscription_state' not in self.env['sale.order']._fields:
            self.skipTest("sin sale_subscription instalado no hay polizas")
        plan = self.env['sale.subscription.plan'].create({'name': 'Mensual'})
        recurrente = self.env['product.product'].create({
            'name': 'Poliza mensual', 'type': 'service', 'list_price': 700.0,
            'invoice_policy': 'order', 'recurring_invoice': True})
        pedido = self.env['sale.order'].create({
            'partner_id': self.cliente.id, 'pricelist_id': self.lista.id,
            'plan_id': plan.id,
            'order_line': [(0, 0, {
                'product_id': recurrente.id, 'product_uom_qty': 1})]})
        tarea = self._tarea(pedido)
        self.assertEqual(pedido.state, 'sale', "fixture: la poliza queda confirmada")

        self.assertFalse(tarea._visar_upsell_destino())
        self._vender(tarea)

        self.assertTrue(self._es_aparte(tarea),
                        "en poliza el cobro tiene que poder cerrarse hoy")
        self.assertNotIn(self.extra, pedido.order_line.product_id)

    def test_un_pedido_bloqueado_no_se_toca(self):
        pedido = self._pedido()
        pedido.locked = True
        tarea = self._tarea(pedido)

        self._vender(tarea)

        self.assertTrue(self._es_aparte(tarea))

    def test_un_pedido_sin_confirmar_no_se_confirma_desde_la_puerta(self):
        """Confirmar el pedido del servicio desde la app seria cerrar la venta sin
        que nadie de oficina la autorice (y REQ-004 exige lista de precios)."""
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        # Crear la tarea de campo confirma el pedido (lo hace el FSM nativo), y
        # "Volver a cotizacion" solo aplica a pedidos cancelados: para probar la
        # guarda se devuelve el estado a mano.
        pedido.sudo().write({'state': 'draft'})

        self._vender(tarea)

        self.assertTrue(self._es_aparte(tarea))
        self.assertEqual(pedido.state, 'draft')

    def test_un_servicio_sin_pedido_sigue_abriendo_uno(self):
        tarea = self._tarea()

        self._vender(tarea)

        self.assertTrue(self._es_aparte(tarea))
        self.assertEqual(tarea._visar_upsell_order().state, 'draft',
                         "el carrito aparte sigue naciendo en borrador")
