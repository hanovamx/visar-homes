# -*- coding: utf-8 -*-
"""Varias rondas de adicionales en la misma visita (22-sep-2026).

El cliente paga una estación y más tarde autoriza otra cosa (S00316). Cada "Generar
cobro" cierra una ronda con su propia factura, en el mismo pedido; lo que se agrega
después se cobra aparte. Lo que se protege: que una ronda nueva no toque lo ya
facturado, que el efectivo de una ronda no pague la siguiente, y que no haya dos
cobros pendientes a la vez.
"""
import importlib.util
import os

from odoo.tests import tagged

from .test_upsell_en_pedido_original import CasoAdicional


@tagged('post_install', '-at_install')
class TestRondasDeAdicionales(CasoAdicional):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.otro = cls.env['product.product'].create({
            'name': 'Guardapolvos', 'type': 'consu', 'list_price': 350.0,
            'invoice_policy': 'order', 'sale_ok': True, 'visar_upsell_ok': True})

    def _ronda_pagada_en_efectivo(self, tarea, producto=None, cantidad=1):
        producto = producto or self.extra
        self.assertTrue(tarea._visar_upsell_add(self.empleado, producto.id, cantidad))
        self.assertTrue(tarea._visar_upsell_register_cash(self.empleado))
        self.assertEqual(tarea._visar_upsell_state(), 'pagado')

    def test_pagada_la_ronda_se_puede_agregar_mas(self):
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._ronda_pagada_en_efectivo(tarea)
        primera = tarea._visar_upsell_invoice()

        self.assertTrue(tarea._visar_upsell_add(self.empleado, self.otro.id, 1))

        self.assertEqual(tarea._visar_upsell_state(), 'borrador')
        self.assertEqual(tarea._visar_upsell_round_lines().product_id, self.otro,
                         "la app enseña solo lo de la ronda nueva")
        self.assertEqual(tarea._visar_upsell_round_total(), 350.0)
        self.assertEqual(tarea.visar_upsell_amount_total, 800.0,
                         "en el backend sigue el total vendido en la visita")

        tarea._visar_upsell_confirm(self.empleado)

        segunda = tarea._visar_upsell_invoice()
        self.assertNotEqual(segunda, primera)
        self.assertEqual(segunda.amount_total, 350.0, "la factura nueva cobra solo lo nuevo")
        self.assertEqual(tarea._visar_upsell_invoices(), primera | segunda)
        self.assertEqual(segunda.invoice_line_ids.sale_line_ids.order_id, pedido,
                         "mismo pedido de venta")
        self.assertEqual(tarea._visar_upsell_state(), 'por_cobrar',
                         "el efectivo de la primera ronda no paga la segunda")

        tarea._visar_upsell_register_cash(self.empleado)
        self.assertEqual(tarea._visar_upsell_state(), 'pagado')
        self.assertEqual(tarea.visar_upsell_cash_move_id, segunda)

    def test_el_mismo_producto_en_otra_ronda_no_toca_la_factura_pagada(self):
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._ronda_pagada_en_efectivo(tarea)
        facturada = tarea._visar_upsell_lines()

        tarea._visar_upsell_add(self.empleado, self.extra.id, 2)

        self.assertEqual(facturada.product_uom_qty, 1, "la línea ya pagada no cambia")
        nueva = tarea._visar_upsell_open_lines()
        self.assertEqual(nueva.product_uom_qty, 2)
        self.assertNotEqual(nueva, facturada)

    def test_con_un_cobro_pendiente_no_se_abre_otra_ronda(self):
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._vender(tarea)
        tarea._visar_upsell_confirm(self.empleado)
        self.assertEqual(tarea._visar_upsell_state(), 'por_cobrar')

        self.assertFalse(tarea._visar_upsell_add(self.empleado, self.otro.id, 1))
        self.assertFalse(tarea._visar_upsell_open_lines())

    def test_cobrar_dos_veces_la_misma_ronda_no_duplica_la_factura(self):
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._ronda_pagada_en_efectivo(tarea)
        tarea._visar_upsell_add(self.empleado, self.otro.id, 1)
        tarea._visar_upsell_confirm(self.empleado)
        tarea._visar_upsell_confirm(self.empleado)
        self.assertEqual(len(tarea._visar_upsell_invoices()), 2)

    def test_el_reporte_lleva_lo_cobrado_aunque_haya_un_carrito_abierto(self):
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._ronda_pagada_en_efectivo(tarea)
        tarea._visar_upsell_add(self.empleado, self.otro.id, 1)
        tarea._visar_upsell_confirm(self.empleado)
        tarea._visar_upsell_register_cash(self.empleado)
        tarea._visar_upsell_add(self.empleado, self.otro.id, 1)  # tercera, sin cobrar

        seccion = tarea._visar_upsell_report_section()

        tabla = seccion['fields'][0]['rows']
        self.assertEqual(len(tabla), 3, "dos productos cobrados + fila de total")
        self.assertIn('800.00', tabla[-1][-1]['text'])
        self.assertIn("Cobros anteriores ya pagados", seccion['fields'][1]['text'])

    def test_un_sello_sin_factura_no_paga_una_ronda_nueva(self):
        """S00316: el pedido aparte con la factura del efectivo se borró a mano. El
        sello viejo se queda sin factura y no puede dar por pagada la siguiente."""
        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._ronda_pagada_en_efectivo(tarea)
        tarea.visar_upsell_cash_move_id = False  # sello huérfano

        tarea._visar_upsell_add(self.empleado, self.otro.id, 1)
        tarea._visar_upsell_confirm(self.empleado)
        self.assertEqual(tarea._visar_upsell_state(), 'por_cobrar')

    def test_la_migracion_apunta_los_sellos_viejos_a_su_factura(self):
        ruta = os.path.join(os.path.dirname(__file__), os.pardir, 'migrations',
                            '19.0.1.37.0', 'post-migrate.py')
        spec = importlib.util.spec_from_file_location('mig_rondas', ruta)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)

        pedido = self._pedido()
        tarea = self._tarea(pedido)
        self._ronda_pagada_en_efectivo(tarea)
        factura = tarea._visar_upsell_invoice()
        tarea.visar_upsell_cash_move_id = False  # como quedaron antes del 22-sep
        factura.visar_upsell_cash_at = False

        modulo.migrate(self.env.cr, '19.0.1.36.0')

        self.assertEqual(tarea.visar_upsell_cash_move_id, factura)
        self.assertEqual(factura.visar_upsell_cash_at, tarea.visar_upsell_cash_at,
                         "la comisión lee el efectivo de la factura")
        self.assertEqual(tarea._visar_upsell_state(), 'pagado')
