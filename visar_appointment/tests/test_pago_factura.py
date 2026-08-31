from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPagoFactura(TransactionCase):
    """REQ-3921: al completarse el pago del sitio, el pedido se confirma Y se factura.

    El defecto no estaba en la cadena de Odoo, que funciona: estaba en que
    `sale.automatic_invoice` venía apagado, y sin él el core confirma el pedido
    pero nunca crea la factura. El dinero quedaba cobrado y la venta sin
    reflejar (S00242, S00250 en producción).

    Importa más allá de la contabilidad: las visitas de póliza cuelgan de
    `_invoice_paid_hook`, que solo corre cuando hay una factura pagada. Con la
    facturación automática apagada, **una póliza contratada por web no generaba
    ninguna visita**.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente Pago Test'})
        cls.product = cls.env['product.template'].create({
            'name': 'Servicio Pago Test', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 1000.0,
            'taxes_id': [(6, 0, [])],
        })
        # El proveedor necesita diario: sin él, `_create_payment()` no puede
        # armar el account.payment y la cadena se corta antes de conciliar.
        journal = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.env.company.id)],
            limit=1)
        cls.provider = cls.env['payment.provider'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        if cls.provider and journal:
            cls.provider.journal_id = journal

    def _make_order(self):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.product_variant_id.id,
                'product_uom_qty': 1,
            })],
        })

    def _make_done_tx(self, order):
        """Transacción pagada y ligada al pedido, como la deja el checkout web."""
        tx = self.env['payment.transaction'].create({
            'provider_id': self.provider.id,
            'payment_method_id': self.provider.payment_method_ids[:1].id,
            'reference': order.name,
            'amount': order.amount_total,
            'currency_id': order.currency_id.id,
            'partner_id': self.partner.id,
            'sale_order_ids': [(6, 0, order.ids)],
        })
        tx.state = 'done'
        return tx

    def test_01_parametro_sembrado_por_el_modulo(self):
        """El módulo deja la facturación automática encendida.

        Es la corrección del REQ-3921 y va como dato del módulo justamente para
        que sobreviva a una reinstalación: cuando se reconstruyó el servidor el
        2026-08-27 se perdió, y nadie se enteró hasta que faltaron las facturas.
        """
        valor = self.env['ir.config_parameter'].sudo().get_param(
            'sale.automatic_invoice')
        self.assertEqual(
            str(valor), 'True',
            "sale.automatic_invoice debe quedar encendido: sin él un pedido "
            "pagado se confirma pero nunca se factura.")

    def test_02_pago_completo_confirma_y_factura(self):
        """Pago que cubre el total → pedido confirmado Y con factura.

        Se ejercita la capa de ventas (`_check_amount_and_confirm_order` +
        `_invoice_sale_orders`), que es exactamente lo que el parámetro
        controla, y no `_post_process()` entero: esa última pata crea el
        `account.payment` y exige un diario con línea de método de pago
        configurada, lo que ataría el test a la contabilidad de la base de
        pruebas sin decir nada más sobre el fix. La conciliación real se
        verifica contra el entorno.
        """
        if not self.provider:
            self.skipTest("no hay proveedor de pago configurado")
        order = self._make_order()
        tx = self._make_done_tx(order)

        self.assertEqual(order.state, 'draft', "arranca como cotización")
        self.assertFalse(order.invoice_ids, "y sin factura")

        self.assertEqual(tx._check_amount_and_confirm_order(), order,
                         "el pago completo debe confirmar el pedido")
        self.assertEqual(order.state, 'sale')

        tx._invoice_sale_orders()

        self.assertTrue(
            order.invoice_ids,
            "debe generar la factura: sin ella el cobro no se refleja y, en "
            "una póliza, no se dispara _invoice_paid_hook ni nacen las visitas.")

    def test_03_pago_parcial_no_confirma(self):
        """Pagar de menos NO confirma: es el caso de S00119, y está bien así.

        El pedido exige el 100% (`prepayment_percent`). Se fija aquí para que
        nadie “arregle” el REQ-3921 confirmando pedidos a medio pagar.
        """
        if not self.provider:
            self.skipTest("no hay proveedor de pago configurado")
        order = self._make_order()
        tx = self._make_done_tx(order)
        tx.amount = order.amount_total / 2

        self.assertFalse(tx._check_amount_and_confirm_order(),
                         "un pago parcial no debe confirmar el pedido")
        self.assertEqual(order.state, 'draft')
