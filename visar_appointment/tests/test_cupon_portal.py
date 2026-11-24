import re

from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_appointment.models.sale_order_cupon import CUPON_MAX_FALLOS


@tagged('post_install', '-at_install')
class TestCuponPortal(TransactionCase):
    """REQ-7600: código de descuento / tarjeta de regalo en la liga de pago.

    La liga del agente abre el portal de la orden, que en Odoo no trae el campo
    del carrito. Se prueba la lógica del modelo; el controlador solo la expone.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Que ningún programa de la base se meta en las cuentas.
        cls.env['loyalty.program'].search([]).write({'active': False})

        cls.partner = cls.env['res.partner'].create({'name': 'Cliente Cupón Test'})
        # Visar no deja confirmar a mano sin lista (`visar_base`); el agente y el
        # wizard siempre ponen la de la zona.
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Lista Cupón Test'})
        cls.pricelist_poliza = cls.env['product.pricelist'].create(
            {'name': 'Lista Póliza Cupón Test'})
        cls.product = cls.env['product.product'].create({
            'name': 'Servicio Cupón Test', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 1000.0,
            'taxes_id': [Command.clear()],
        })

        def _promo(nombre, code, porcentaje):
            return cls.env['loyalty.program'].create({
                'name': nombre,
                'program_type': 'promo_code',
                'trigger': 'with_code',
                'applies_on': 'current',
                'rule_ids': [Command.create({'mode': 'with_code', 'code': code})],
                'reward_ids': [Command.create({
                    'reward_type': 'discount',
                    'discount': porcentaje,
                    'discount_mode': 'percent',
                    'discount_applicability': 'order',
                })],
            })

        cls.promo_100 = _promo('Amigos Test', 'AMIGOSTEST', 100)
        cls.promo_10 = _promo('Diez Test', 'DIEZTEST', 10)

        cls.gift_program = cls.env['loyalty.program'].create({
            'name': 'Tarjetas Test',
            'program_type': 'gift_card',
            'applies_on': 'future',
            'trigger': 'auto',
            'rule_ids': [Command.create({
                'reward_point_amount': 1,
                'reward_point_mode': 'money',
                'reward_point_split': True,
            })],
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount': 1,
                'discount_mode': 'per_point',
                'discount_applicability': 'order',
            })],
        })

        journal = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.env.company.id)],
            limit=1)
        cls.provider = cls.env['payment.provider'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        if cls.provider and journal:
            cls.provider.journal_id = journal

    def _orden(self, **vals):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'require_payment': True,
            'order_line': [Command.create({
                'product_id': self.product.id, 'product_uom_qty': 1})],
            **vals,
        })

    def _tarjeta(self, puntos, code):
        return self.env['loyalty.card'].create({
            'program_id': self.gift_program.id, 'points': puntos, 'code': code})

    def _tx(self, orden, monto):
        if not self.provider:
            self.skipTest("La base no tiene proveedor de pago.")
        return self.env['payment.transaction'].create({
            'provider_id': self.provider.id,
            'payment_method_id': self.provider.payment_method_ids[:1].id,
            'reference': self.env['payment.transaction']._compute_reference(
                self.provider.code, prefix=orden.name),
            'amount': monto,
            'currency_id': orden.currency_id.id,
            'partner_id': self.partner.id,
            'sale_order_ids': [Command.set(orden.ids)],
        })

    # ------------------------------------------------------------------
    # Dónde aparece el campo
    # ------------------------------------------------------------------
    def test_campo_solo_en_ordenes_que_esperan_pago(self):
        self.assertTrue(self._orden()._visar_cupon_aplica())
        self.assertFalse(self._orden(require_payment=False)._visar_cupon_aplica())

        confirmada = self._orden()
        confirmada.action_confirm()
        self.assertFalse(confirmada._visar_cupon_aplica())

    def test_polizas_no_aceptan_codigo(self):
        """Decisión de Luis: el descuento se repetiría cada periodo."""
        plan = self.env['sale.subscription.plan'].create({
            'name': 'Plan Cupón Test', 'billing_period_unit': 'month',
            'billing_period_value': 1})
        poliza = self._orden(plan_id=plan.id)
        self.assertTrue(poliza.is_subscription)
        self.assertFalse(poliza._visar_cupon_aplica())
        ok, _mensaje = poliza._visar_cupon_aplicar('DIEZTEST')
        self.assertFalse(ok)
        self.assertFalse(poliza.order_line.reward_id)

    def test_programa_restringido_por_lista_no_aplica_en_otra(self):
        """Lo que Luis configura en el programa: solo listas de compra única."""
        self.promo_10.pricelist_ids = self.pricelist_poliza
        orden = self._orden()
        ok, _mensaje = orden._visar_cupon_aplicar('DIEZTEST')
        self.assertFalse(ok)
        self.assertFalse(orden.order_line.reward_id)

        self.promo_10.pricelist_ids = self.pricelist
        ok, _mensaje = orden._visar_cupon_aplicar('DIEZTEST')
        self.assertTrue(ok)

    def test_cero_sin_lista_tambien_se_confirma(self):
        """Igual que un pago en línea: la regla de la lista es para oficina."""
        orden = self._orden(pricelist_id=False)
        orden._visar_cupon_aplicar('AMIGOSTEST')
        ok, mensaje = orden._visar_cupon_confirmar_sin_pago()
        self.assertTrue(ok, mensaje)
        self.assertEqual(orden.state, 'sale')

    # ------------------------------------------------------------------
    # Aplicar y quitar
    # ------------------------------------------------------------------
    def test_codigo_parcial_baja_el_total_y_deja_nota(self):
        orden = self._orden()
        ok, _mensaje = orden._visar_cupon_aplicar('  DIEZTEST ')
        self.assertTrue(ok)
        self.assertAlmostEqual(orden.amount_total, 900.0)
        self.assertEqual(orden._visar_cupon_lineas().reward_id.program_id,
                         self.promo_10)
        self.assertIn('DIEZTEST', orden.message_ids[:1].body)

    def test_quitar_regresa_el_total_y_el_codigo_se_puede_volver_a_usar(self):
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        linea = orden._visar_cupon_lineas()
        ok, _mensaje = orden._visar_cupon_quitar(linea.id)
        self.assertTrue(ok)
        self.assertAlmostEqual(orden.amount_total, 1000.0)
        self.assertFalse(orden.order_line.reward_id)
        ok, _mensaje = orden._visar_cupon_aplicar('DIEZTEST')
        self.assertTrue(ok, "Quitarlo debe soltar el código, no quemarlo.")

    def test_quitar_una_linea_ajena_no_hace_nada(self):
        orden = self._orden()
        ok, _mensaje = orden._visar_cupon_quitar(orden.order_line.id)
        self.assertFalse(ok)
        self.assertEqual(len(orden.order_line), 1)

    def test_tarjeta_de_regalo_parcial_y_nota_sin_el_codigo_completo(self):
        self._tarjeta(300, 'GIFT-TEST-0001')
        orden = self._orden()
        ok, _mensaje = orden._visar_cupon_aplicar('GIFT-TEST-0001')
        self.assertTrue(ok)
        self.assertAlmostEqual(orden.amount_total, 700.0)
        nota = orden.message_ids[:1].body
        self.assertIn('0001', nota)
        self.assertNotIn('GIFT-TEST-0001', nota,
                         "Una tarjeta es dinero: la nota no debe exponerla.")

    def test_un_codigo_mejor_reemplaza_al_anterior(self):
        """Dos descuentos globales: Odoo deja el mejor y reutiliza la línea."""
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        ok, mensaje = orden._visar_cupon_aplicar('AMIGOSTEST')
        self.assertTrue(ok, mensaje)
        self.assertTrue(orden.currency_id.is_zero(orden.amount_total))

    def test_dos_tarjetas_del_mismo_programa_se_suman(self):
        self._tarjeta(300, 'GIFT-TEST-0004')
        self._tarjeta(200, 'GIFT-TEST-0005')
        orden = self._orden()
        orden._visar_cupon_aplicar('GIFT-TEST-0004')
        ok, mensaje = orden._visar_cupon_aplicar('GIFT-TEST-0005')
        self.assertTrue(ok, mensaje)
        self.assertAlmostEqual(orden.amount_total, 500.0)

    def test_codigos_fallidos_bloquean_la_liga(self):
        orden = self._orden()
        for _i in range(CUPON_MAX_FALLOS):
            ok, _mensaje = orden._visar_cupon_aplicar('NO-EXISTE')
            self.assertFalse(ok)
        self.assertTrue(orden.visar_cupon_bloqueo_hasta)
        ok, mensaje = orden._visar_cupon_aplicar('DIEZTEST')
        self.assertFalse(ok, "Bloqueada, ni un código bueno entra.")
        self.assertIn('intentos', mensaje)
        self.assertFalse(orden.order_line.reward_id)

    def test_un_codigo_bueno_reinicia_los_fallos(self):
        orden = self._orden()
        orden._visar_cupon_aplicar('NO-EXISTE')
        self.assertEqual(orden.visar_cupon_fallos, 1)
        orden._visar_cupon_aplicar('DIEZTEST')
        self.assertEqual(orden.visar_cupon_fallos, 0)

    # ------------------------------------------------------------------
    # Total en cero
    # ------------------------------------------------------------------
    def test_codigo_al_100_confirma_sin_cobro(self):
        orden = self._orden()
        ok, _mensaje = orden._visar_cupon_aplicar('AMIGOSTEST')
        self.assertTrue(ok)
        self.assertTrue(orden.currency_id.is_zero(orden.amount_total))
        self.assertTrue(orden._visar_cupon_puede_confirmar_sin_pago())

        ok, mensaje = orden._visar_cupon_confirmar_sin_pago()
        self.assertTrue(ok, mensaje)
        self.assertEqual(orden.state, 'sale')
        self.assertFalse(orden.transaction_ids)

    def test_cero_sin_codigo_lo_confirma_oficina(self):
        """Un 100% puesto a mano (S00029) no habilita el botón del cliente."""
        orden = self._orden()
        orden.order_line.discount = 100
        self.assertTrue(orden.currency_id.is_zero(orden.amount_total))
        self.assertFalse(orden._visar_cupon_puede_confirmar_sin_pago())
        ok, _mensaje = orden._visar_cupon_confirmar_sin_pago()
        self.assertFalse(ok)
        self.assertEqual(orden.state, 'draft')

    def test_con_saldo_no_se_confirma_sin_cobro(self):
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        self.assertFalse(orden._visar_cupon_puede_confirmar_sin_pago())
        ok, _mensaje = orden._visar_cupon_confirmar_sin_pago()
        self.assertFalse(ok)
        self.assertEqual(orden.state, 'draft')

    # ------------------------------------------------------------------
    # Blindaje del cobro
    # ------------------------------------------------------------------
    def test_cobro_con_el_total_con_descuento_pasa(self):
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        self._tx(orden, 900.0)  # no lanza

    def test_cobro_con_el_monto_viejo_se_rechaza(self):
        """La página pintó 1000 y la orden ya vale 900 (u otra pestaña lo cambió)."""
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        with self.assertRaises(ValidationError):
            self._tx(orden, 1000.0)

    def test_tarjeta_gastada_en_otra_orden_no_se_cobra(self):
        """Sin la revalidación, la confirmación reventaría DESPUÉS del cargo."""
        self._tarjeta(300, 'GIFT-TEST-0002')
        primera, segunda = self._orden(), self._orden()
        primera._visar_cupon_aplicar('GIFT-TEST-0002')
        segunda._visar_cupon_aplicar('GIFT-TEST-0002')
        self.assertAlmostEqual(segunda.amount_total, 700.0)

        primera.action_confirm()  # aquí se descuentan los puntos de la tarjeta

        with self.assertRaises(ValidationError):
            self._tx(segunda, 700.0)

    def test_cobro_que_ya_no_confirma_avisa_a_oficina(self):
        """Las dos cobran antes de que alguna confirme: la segunda no revienta."""
        self._tarjeta(300, 'GIFT-TEST-0006')
        primera, segunda = self._orden(), self._orden()
        primera._visar_cupon_aplicar('GIFT-TEST-0006')
        segunda._visar_cupon_aplicar('GIFT-TEST-0006')
        tx = self._tx(segunda, 700.0)  # pasa: la tarjeta aún tiene saldo
        primera.action_confirm()
        tx.state = 'done'

        tx._check_amount_and_confirm_order()  # no lanza
        tx._check_amount_and_confirm_order()  # otra pasada del post-proceso

        self.assertEqual(segunda.state, 'draft')
        self.assertIn('NO se pudo confirmar', segunda.message_ids[:1].body)
        avisos = segunda.activity_ids.filtered(
            lambda a: 'Cobrado y sin confirmar' in (a.summary or ''))
        self.assertEqual(len(avisos), 1, "Un aviso por orden, no uno por pasada.")

    def test_cobro_sin_problema_de_saldo_confirma_normal(self):
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        tx = self._tx(orden, 900.0)
        tx.state = 'done'
        tx._check_amount_and_confirm_order()
        self.assertEqual(orden.state, 'sale')

    def test_al_abrir_la_liga_se_ve_el_descuento_vigente(self):
        self._tarjeta(300, 'GIFT-TEST-0003')
        primera, segunda = self._orden(), self._orden()
        primera._visar_cupon_aplicar('GIFT-TEST-0003')
        segunda._visar_cupon_aplicar('GIFT-TEST-0003')
        primera.action_confirm()

        segunda._visar_cupon_refrescar()
        self.assertAlmostEqual(segunda.amount_total, 1000.0)

    def test_la_liga_no_pinta_mas_que_el_total(self):
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        self.assertAlmostEqual(orden._visar_cupon_tope_liga('1000.0', True), 900.0)
        self.assertEqual(orden._visar_cupon_tope_liga(None, True), None)
        self.assertEqual(orden._visar_cupon_tope_liga('1000.0', False), '1000.0')

    def test_liga_vieja_bajo_el_anticipo_usa_el_total(self):
        """El total SUBIÓ (la tarjeta se gastó): sin esto el portal da 404."""
        orden = self._orden()
        self.assertAlmostEqual(orden._visar_cupon_tope_liga('700.0', True), 1000.0)

    def test_monto_de_regreso_a_la_liga(self):
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        # La liga cobraba el total: ahora cobra el total nuevo.
        self.assertAlmostEqual(orden._visar_cupon_monto_liga('1000.0', 1000.0), 900.0)
        # Al quitarlo, el total vuelve a subir y la liga lo sigue.
        orden._visar_cupon_quitar(orden._visar_cupon_lineas().id)
        self.assertAlmostEqual(orden._visar_cupon_monto_liga('900.0', 900.0), 1000.0)
        # Un anticipo de oficina se conserva.
        orden.prepayment_percent = 0.3
        self.assertAlmostEqual(orden._visar_cupon_monto_liga('500.0', 1000.0), 500.0)
        self.assertIsNone(orden._visar_cupon_monto_liga(None, 1000.0))

    def test_tarjeta_escrita_en_mayusculas_entra(self):
        """Las tarjetas de Odoo llevan hexadecimal en minúsculas."""
        self._tarjeta(300, '044f-1a2b-test')
        orden = self._orden()
        ok, mensaje = orden._visar_cupon_aplicar('044F-1A2B-TEST')
        self.assertTrue(ok, mensaje)
        self.assertAlmostEqual(orden.amount_total, 700.0)

    def test_cupon_escrito_en_minusculas_entra(self):
        orden = self._orden()
        ok, mensaje = orden._visar_cupon_aplicar('dieztest')
        self.assertTrue(ok, mensaje)

    def test_repetir_el_codigo_propio_no_bloquea(self):
        orden = self._orden()
        orden._visar_cupon_aplicar('DIEZTEST')
        for _i in range(CUPON_MAX_FALLOS + 1):
            ok, _mensaje = orden._visar_cupon_aplicar('DIEZTEST')
            self.assertFalse(ok)
        self.assertEqual(orden.visar_cupon_fallos, 0)
        self.assertFalse(orden.visar_cupon_bloqueo_hasta)

    def test_anticipo_de_oficina_se_puede_cobrar(self):
        orden = self._orden(prepayment_percent=0.3)
        orden._visar_cupon_aplicar('DIEZTEST')  # 900, anticipo 270
        self._tx(orden, 500.0)  # no lanza
        with self.assertRaises(ValidationError):
            self._tx(orden, 200.0)


@tagged('post_install', '-at_install')
class TestCuponPortalHttp(HttpCase):
    """La liga de verdad: abrirla sin sesión, aplicar el código y confirmar."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['loyalty.program'].search([]).write({'active': False})
        cls.env['loyalty.program'].create({
            'name': 'Amigos Http Test',
            'program_type': 'promo_code',
            'trigger': 'with_code',
            'applies_on': 'current',
            'rule_ids': [Command.create({'mode': 'with_code', 'code': 'AMIGOSHTTP'})],
            'reward_ids': [Command.create({
                'reward_type': 'discount', 'discount': 100,
                'discount_mode': 'percent', 'discount_applicability': 'order',
            })],
        })
        partner = cls.env['res.partner'].create({'name': 'Cliente Http Test'})
        product = cls.env['product.product'].create({
            'name': 'Servicio Http Test', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 1000.0,
            'taxes_id': [Command.clear()],
        })
        cls.order = cls.env['sale.order'].create({
            'partner_id': partner.id,
            # Como una cotización de oficina: el botón nativo de firma existe.
            'require_signature': True,
            'pricelist_id': cls.env['product.pricelist'].create(
                {'name': 'Lista Http Test'}).id,
            'require_payment': True,
            'order_line': [Command.create({
                'product_id': product.id, 'product_uom_qty': 1})],
        })

    def _csrf(self, html):
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertTrue(match, "La página debe traer el formulario del código.")
        return match.group(1)

    def test_liga_aplicar_y_confirmar(self):
        order = self.order
        liga = order.get_portal_url(query_string='&payment_amount=1000.0')
        page = self.url_open(liga)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Tarjeta de regalo o código de descuento', page.text)

        respuesta = self.url_open(
            f'/my/orders/{order.id}/visar/cupon',
            data={'csrf_token': self._csrf(page.text),
                  'access_token': order.access_token,
                  'code': 'AMIGOSHTTP', 'monto_liga': '1000.0'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('Código aplicado', respuesta.text)
        self.assertIn('Confirmar cita', respuesta.text)
        self.assertNotIn('Aceptar y firmar', respuesta.text)
        self.assertNotIn('Accept &amp; Sign', respuesta.text)
        self.env.invalidate_all()
        self.assertTrue(order.currency_id.is_zero(order.amount_total))

        respuesta = self.url_open(
            f'/my/orders/{order.id}/visar/confirmar',
            data={'csrf_token': self._csrf(respuesta.text),
                  'access_token': order.access_token})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('Tu cita quedó confirmada', respuesta.text)
        self.env.invalidate_all()
        self.assertEqual(order.state, 'sale')

    def test_sin_token_no_se_toca_la_orden(self):
        page = self.url_open(self.order.get_portal_url())
        self.url_open(
            f'/my/orders/{self.order.id}/visar/cupon',
            data={'csrf_token': self._csrf(page.text),
                  'access_token': 'token-falso', 'code': 'AMIGOSHTTP'})
        self.env.invalidate_all()
        self.assertFalse(self.order.order_line.reward_id)
