# -*- coding: utf-8 -*-
"""Qué venta le toca a quién: el motor de medición.

Nada de aquí decide CUÁNTO se paga —eso son la tasa y la base, que se capturan en
el plan—. Lo que se prueba es la atribución: de quién es cada venta, en qué
periodo cae y con qué importe entra.
"""
from datetime import date

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestMedicion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendedora = cls.env['hr.employee'].create({'name': 'Vendedora de mostrador'})
        cls.tecnico = cls.env['hr.employee'].create({'name': 'Tecnico de campo'})
        cls.cliente = cls.env['res.partner'].create({'name': 'Cliente comisiones'})
        cls.lista = cls.env['product.pricelist'].create({'name': 'Lista comisiones'})
        cls.core = cls.env['product.category'].create({'name': 'Core de prueba'})
        cls.otra_categ = cls.env['product.category'].create({'name': 'Otra de prueba'})
        cls.servicio = cls.env['product.product'].create({
            'name': 'Fumigacion', 'type': 'service', 'list_price': 1000.0,
            'invoice_policy': 'order', 'categ_id': cls.core.id,
            'taxes_id': [(5, 0, 0)]})
        cls.extra = cls.env['product.product'].create({
            'name': 'Estacion', 'type': 'consu', 'list_price': 500.0,
            'invoice_policy': 'order', 'categ_id': cls.otra_categ.id,
            'taxes_id': [(5, 0, 0)]})
        cls.proyecto = cls.env['project.project'].create({
            'name': 'Proyecto comisiones', 'company_id': cls.env.company.id})

    # ------------------------------------------------------------------
    def _plan(self, **vals):
        plan = self.env['visar.commission.plan'].create(dict({
            'name': 'Plan medicion',
            'date_from': date(2026, 1, 1),
            'date_to': date(2026, 12, 31),
            'periodicity': 'mes',
        }, **vals))
        plan.employee_ids = [
            (0, 0, {'employee_id': self.vendedora.id}),
            (0, 0, {'employee_id': self.tecnico.id}),
        ]
        return plan

    def _regla(self, plan, **vals):
        return self.env['visar.commission.rule'].create(dict({
            'plan_id': plan.id,
            'base': 'importe_vendido',
            'origen': 'venta_directa',
            'rate': 0.10,
        }, **vals))

    def _pedido(self, vendedor=None, dia=15, confirmar=True, producto=None, qty=1):
        pedido = self.env['sale.order'].create({
            'partner_id': self.cliente.id,
            'pricelist_id': self.lista.id,
            'date_order': '2026-06-%02d 12:00:00' % dia,
            'visar_salesperson_employee_id': vendedor.id if vendedor else False,
            'order_line': [(0, 0, {
                'product_id': (producto or self.servicio).id,
                'product_uom_qty': qty})],
        })
        if confirmar:
            pedido.action_confirm()
            # `action_confirm` mueve date_order a hoy: se regresa al dia de la
            # prueba, que es lo que decide en que periodo cae la venta.
            pedido.date_order = '2026-06-%02d 12:00:00' % dia
        return pedido

    def _junio(self, plan):
        return plan.period_ids.filtered(lambda p: p.date_from == date(2026, 6, 1))

    def _medir(self, regla, empleado, periodo):
        return regla._visar_medir(empleado, periodo.date_from, periodo.date_to)

    # ------------------------------------------------------------------
    def test_la_venta_es_del_vendedor_empleado_de_la_cotizacion(self):
        plan = self._plan()
        regla = self._regla(plan)
        self._pedido(vendedor=self.vendedora)

        base, lineas = self._medir(regla, self.vendedora, self._junio(plan))

        self.assertEqual(base, 1000.0)
        self.assertEqual(len(lineas), 1)
        self.assertFalse(self._medir(regla, self.tecnico, self._junio(plan))[0],
                         "la venta de una no puede aparecerle a otro")

    def test_una_venta_sin_vendedor_empleado_no_le_toca_a_nadie(self):
        plan = self._plan()
        regla = self._regla(plan)
        self._pedido(vendedor=None)
        self.assertEqual(self._medir(regla, self.vendedora, self._junio(plan))[0], 0.0)

    def test_fuera_del_periodo_no_cuenta(self):
        plan = self._plan()
        regla = self._regla(plan)
        self._pedido(vendedor=self.vendedora)
        julio = plan.period_ids.filtered(lambda p: p.date_from == date(2026, 7, 1))
        self.assertEqual(self._medir(regla, self.vendedora, julio)[0], 0.0)

    def test_un_pedido_sin_confirmar_no_es_venta(self):
        plan = self._plan()
        regla = self._regla(plan)
        self._pedido(vendedor=self.vendedora, confirmar=False)
        self.assertEqual(self._medir(regla, self.vendedora, self._junio(plan))[0], 0.0)

    # --- upsell de campo ---------------------------------------------
    def _con_upsell(self):
        """Pedido del vendedor con un adicional que vendio el tecnico dentro."""
        pedido = self._pedido(vendedor=self.vendedora)
        tarea = self.env['project.task'].create({
            'name': 'Servicio', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id})
        self.env['sale.order.line'].create({
            'order_id': pedido.id,
            'product_id': self.extra.id,
            'product_uom_qty': 1,
            'visar_upsell_task_id': tarea.id,
            'visar_upsell_employee_id': self.tecnico.id,
        })
        return pedido

    def test_el_adicional_es_del_tecnico_y_no_del_vendedor(self):
        """Los dos comisionan sobre el MISMO documento, cada uno por su linea.
        Esto el nativo no lo sabe hacer: alla la venta es del vendedor de cabecera."""
        plan = self._plan()
        directa = self._regla(plan, origen='venta_directa')
        campo = self._regla(plan, origen='upsell_campo')
        self._con_upsell()
        junio = self._junio(plan)

        self.assertEqual(self._medir(directa, self.vendedora, junio)[0], 1000.0,
                         "el vendedor cobra su servicio, no el extra del tecnico")
        self.assertEqual(self._medir(campo, self.tecnico, junio)[0], 500.0)
        self.assertEqual(self._medir(directa, self.tecnico, junio)[0], 0.0)
        self.assertEqual(self._medir(campo, self.vendedora, junio)[0], 0.0)

    def test_las_dos_juntas_suman_el_documento_completo(self):
        plan = self._plan()
        ambas = self._regla(plan, origen='ambas')
        self._con_upsell()
        junio = self._junio(plan)
        self.assertEqual(self._medir(ambas, self.vendedora, junio)[0], 1000.0)
        self.assertEqual(self._medir(ambas, self.tecnico, junio)[0], 500.0)

    def test_el_upsell_historico_tambien_se_atribuye(self):
        """Los 10 adicionales vendidos antes del 17-sep-2026 son un pedido aparte
        con el tecnico en la CABECERA, y sus lineas no llevan marcador."""
        plan = self._plan()
        campo = self._regla(plan, origen='upsell_campo')
        tarea = self.env['project.task'].create({
            'name': 'Servicio viejo', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id})
        pedido = self._pedido(vendedor=None, producto=self.extra)
        pedido.write({
            'visar_upsell_task_id': tarea.id,
            'visar_upsell_employee_id': self.tecnico.id,
        })

        self.assertEqual(self._medir(campo, self.tecnico, self._junio(plan))[0], 500.0)

    # --- filtros y base ----------------------------------------------
    def test_la_categoria_filtra_lo_que_entra(self):
        plan = self._plan()
        solo_core = self._regla(plan, origen='ambas', product_categ_id=self.core.id)
        self._con_upsell()
        self.assertEqual(self._medir(solo_core, self.vendedora, self._junio(plan))[0], 1000.0)
        self.assertEqual(self._medir(solo_core, self.tecnico, self._junio(plan))[0], 0.0,
                         "el extra es de otra categoria")

    def test_la_cantidad_tambien_es_una_base(self):
        plan = self._plan()
        regla = self._regla(plan, base='cantidad_vendida')
        self._pedido(vendedor=self.vendedora, qty=3)
        self.assertEqual(self._medir(regla, self.vendedora, self._junio(plan))[0], 3.0)

    def test_el_iva_incluido_cambia_la_base(self):
        """Los precios de Visar llevan IVA dentro: el 10% de una venta de $1,160
        son dos cifras distintas segun lo que decida el negocio."""
        impuesto = self.env['account.tax'].create({
            'name': 'IVA incluido 16%', 'amount': 16.0, 'amount_type': 'percent',
            'price_include_override': 'tax_included', 'type_tax_use': 'sale'})
        self.servicio.taxes_id = impuesto
        plan = self._plan()
        sin_iva = self._regla(plan)
        self._pedido(vendedor=self.vendedora)
        junio = self._junio(plan)

        base_sin = self._medir(sin_iva, self.vendedora, junio)[0]
        plan.tax_base = 'con_iva'
        base_con = self._medir(sin_iva, self.vendedora, junio)[0]

        self.assertAlmostEqual(base_sin, 862.07, places=2)
        self.assertAlmostEqual(base_con, 1000.0, places=2)

    # --- facturado y cobrado -----------------------------------------
    def test_facturado_cuenta_el_dia_de_la_factura(self):
        plan = self._plan()
        regla = self._regla(plan, base='importe_facturado')
        pedido = self._pedido(vendedor=self.vendedora)
        factura = pedido._create_invoices()
        factura.write({'invoice_date': date(2026, 7, 5)})
        factura.action_post()

        junio = self._junio(plan)
        julio = plan.period_ids.filtered(lambda p: p.date_from == date(2026, 7, 1))
        self.assertEqual(self._medir(regla, self.vendedora, junio)[0], 0.0,
                         "se vendio en junio pero se facturo en julio")
        self.assertEqual(self._medir(regla, self.vendedora, julio)[0], 1000.0)

    def test_una_factura_en_borrador_no_es_facturado(self):
        plan = self._plan()
        regla = self._regla(plan, base='importe_facturado')
        pedido = self._pedido(vendedor=self.vendedora)
        pedido._create_invoices()
        self.assertEqual(self._medir(regla, self.vendedora, self._junio(plan))[0], 0.0)

    def test_cobrado_incluye_el_pedido_pagado_que_nadie_facturo(self):
        """El caso de REQ-002, medido en produccion: 7 de 9 pedidos con venta en
        campo estaban pagados en linea y sin factura. Sin esta rama el vendedor no
        cobraria comision de una venta que si entro."""
        plan = self._plan()
        regla = self._regla(plan, base='importe_cobrado')
        pedido = self._pedido(vendedor=self.vendedora)
        proveedor = self.env['payment.provider'].search([], limit=1)
        self.env['payment.transaction'].create({
            'provider_id': proveedor.id,
            'payment_method_id': proveedor.payment_method_ids[:1].id,
            'amount': pedido.amount_total,
            'currency_id': pedido.currency_id.id,
            'partner_id': self.cliente.id,
            'reference': 'PRUEBA-COMISION',
            'state': 'done',
            'sale_order_ids': [(6, 0, pedido.ids)],
        })

        self.assertFalse(pedido.invoice_ids, "a proposito: pagado y sin facturar")
        hoy = self.env['visar.commission.period'].search([
            ('plan_id', '=', plan.id),
            ('date_from', '<=', date.today()), ('date_to', '>=', date.today())])
        if not hoy:
            self.skipTest("el plan de prueba no cubre la fecha de hoy")
        self.assertEqual(self._medir(regla, self.vendedora, hoy)[0], 1000.0)

    def test_cobrado_no_cuenta_una_factura_sin_pagar(self):
        plan = self._plan()
        regla = self._regla(plan, base='importe_cobrado')
        pedido = self._pedido(vendedor=self.vendedora)
        factura = pedido._create_invoices()
        factura.write({'invoice_date': date(2026, 6, 20)})
        factura.action_post()
        self.assertEqual(factura.payment_state, 'not_paid')
        self.assertEqual(self._medir(regla, self.vendedora, self._junio(plan))[0], 0.0)

    # --- del logro a la comision -------------------------------------
    def test_la_tasa_convierte_lo_vendido_en_comision(self):
        plan = self._plan()
        self._regla(plan, rate=0.10)
        self._pedido(vendedor=self.vendedora)
        plan.action_approve()

        plan.action_calcular()

        linea = plan.line_ids.filtered(
            lambda l: l.employee_id == self.vendedora and l.date_from == date(2026, 6, 1))
        self.assertEqual(linea.base_amount, 1000.0)
        self.assertEqual(linea.commission_amount, 100.0)
        self.assertEqual(linea.detail_ids.sale_line_count, 1,
                         "el detalle guarda de que ventas salio")

    def test_un_ajuste_manual_entra_al_periodo_de_su_fecha(self):
        plan = self._plan()
        self._regla(plan, rate=0.10)
        self._pedido(vendedor=self.vendedora)
        self.env['visar.commission.adjustment'].create({
            'plan_employee_id': plan.employee_ids.filtered(
                lambda e: e.employee_id == self.vendedora).id,
            'date': date(2026, 6, 20),
            'amount': 250.0,
            'note': 'Bono acordado',
        })
        plan.action_approve()

        plan.action_calcular()

        linea = plan.line_ids.filtered(
            lambda l: l.employee_id == self.vendedora and l.date_from == date(2026, 6, 1))
        self.assertEqual(linea.adjustment_amount, 250.0)
        self.assertEqual(linea.commission_amount, 350.0)

    def test_el_empleado_que_entra_a_medio_plan_no_cobra_lo_anterior(self):
        plan = self._plan()
        self._regla(plan, rate=0.10)
        self._pedido(vendedor=self.vendedora, dia=10)
        plan.employee_ids.filtered(
            lambda e: e.employee_id == self.vendedora).date_from = date(2026, 6, 15)
        plan.action_approve()

        plan.action_calcular()

        linea = plan.line_ids.filtered(
            lambda l: l.employee_id == self.vendedora and l.date_from == date(2026, 6, 1))
        self.assertEqual(linea.commission_amount, 0.0,
                         "la venta del dia 10 quedo antes de que entrara al plan")

    def test_la_tabla_de_metas_interpola(self):
        plan = self._plan(mode='meta', commission_amount=0.0)
        self._regla(plan, rate=1.0)
        plan.curve_ids.filtered(lambda c: c.target_rate == 0.5).amount = 500.0
        plan.curve_ids.filtered(lambda c: c.target_rate == 1.0).amount = 1000.0
        self._junio(plan).target_amount = 2000.0
        self._pedido(vendedor=self.vendedora)  # vende 1000 de una meta de 2000
        plan.action_approve()

        plan.action_calcular()

        linea = plan.line_ids.filtered(
            lambda l: l.employee_id == self.vendedora and l.date_from == date(2026, 6, 1))
        self.assertEqual(linea.target_rate, 0.5)
        self.assertEqual(linea.commission_amount, 500.0)

    def _junio_cerrado_con_una_venta(self):
        plan = self._plan()
        self._regla(plan, rate=0.10)
        self._pedido(vendedor=self.vendedora)
        plan.action_approve()
        plan.action_calcular()
        junio = self._junio(plan)
        junio.action_cerrar()
        self._pedido(vendedor=self.vendedora, dia=20)  # otra venta del mismo mes
        return plan, junio

    def _comision_de_junio(self, plan):
        return plan.line_ids.filtered(
            lambda l: l.employee_id == self.vendedora
            and l.date_from == date(2026, 6, 1)).commission_amount

    def test_cerrar_un_periodo_congela_la_comision(self):
        """Lo que el reporte nativo no puede hacer: su vista SQL siempre recalcula."""
        plan, _junio = self._junio_cerrado_con_una_venta()

        plan.action_calcular()

        self.assertEqual(self._comision_de_junio(plan), 100.0,
                         "el plan no vuelve a tocar un periodo cerrado")

    def test_ni_recalculando_el_periodo_cerrado_a_mano(self):
        """El boton de recalcular vive en el renglon del periodo, asi que la
        guarda tiene que estar tambien ahi y no solo en el plan."""
        plan, junio = self._junio_cerrado_con_una_venta()

        junio.action_calcular()

        self.assertEqual(self._comision_de_junio(plan), 100.0)
        # Un renglon por empleado del plan (aunque venda cero, como en el nativo),
        # y ni uno mas: reintentar no duplica.
        self.assertEqual(len(junio.line_ids), 2)
        self.assertEqual(len(junio.line_ids.filtered(
            lambda l: l.employee_id == self.vendedora)), 1)


@tagged('post_install', '-at_install')
class TestEfectivoPorRonda(TransactionCase):
    """Una visita puede cobrar adicionales en efectivo en varias rondas (22-sep-2026):
    cada línea se fecha con el efectivo de SU factura, no con el último de la visita."""

    def test_cada_ronda_se_fecha_con_su_propio_efectivo(self):
        from datetime import datetime
        env = self.env
        tecnico = env['hr.employee'].create({'name': 'Tecnico rondas'})
        cliente = env['res.partner'].create({'name': 'Cliente rondas'})
        lista = env['product.pricelist'].create({'name': 'Lista rondas'})
        servicio = env['product.product'].create({
            'name': 'Servicio rondas', 'type': 'service', 'invoice_policy': 'order'})
        extra = env['product.product'].create({
            'name': 'Extra rondas', 'type': 'consu', 'list_price': 100.0,
            'invoice_policy': 'order', 'sale_ok': True, 'visar_upsell_ok': True})
        proyecto = env['project.project'].create({
            'name': 'FSM rondas', 'is_fsm': True, 'allow_billable': True,
            'company_id': env.company.id})
        pedido = env['sale.order'].create({
            'partner_id': cliente.id, 'pricelist_id': lista.id,
            'order_line': [(0, 0, {'product_id': servicio.id})]})
        pedido.action_confirm()
        tarea = env['project.task'].create({
            'name': 'Visita rondas', 'project_id': proyecto.id,
            'partner_id': cliente.id, 'sale_line_id': pedido.order_line[0].id})

        tarea._visar_upsell_add(tecnico, extra.id, 1)
        tarea._visar_upsell_register_cash(tecnico)
        primera = tarea._visar_upsell_lines()
        primera.invoice_lines.move_id.visar_upsell_cash_at = datetime(2026, 9, 10, 12)

        tarea._visar_upsell_add(tecnico, extra.id, 1)
        segunda = tarea._visar_upsell_open_lines()
        Regla = env['visar.commission.rule']
        self.assertFalse(Regla._visar_fecha_de_pago(segunda),
                         "sin cobrar todavía: el efectivo de la ronda 1 no cuenta")

        tarea._visar_upsell_confirm(tecnico)
        tarea._visar_upsell_register_cash(tecnico)

        self.assertEqual(Regla._visar_fecha_de_pago(primera), date(2026, 9, 10))
        self.assertEqual(Regla._visar_fecha_de_pago(segunda),
                         tarea.visar_upsell_cash_at.date())
