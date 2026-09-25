# -*- coding: utf-8 -*-
"""La lista de precios de la póliza no se pierde al fijar el cliente (25-sep-2026).

Visar reportó que el wizard decía "$655.50 al mes" y el Resumen de la orden "$690".
Medido en un clon: el pedido se armaba bien y **tocar el cliente después lo
repreciaba** — Odoo recalcula `pricelist_id` desde el partner, así que el servicio
volvía al precio de contado (690) mientras la mensualidad adelantada conservaba el
del plan (655.50). Dos precios para lo mismo en el mismo documento, y el segundo
es el que iba a la liga de pago. Pasó en producción: S00348.

El arreglo de fondo es el ORDEN (cliente antes de cotizar); esto prueba la red de
seguridad, que es lo que impide que un recálculo futuro vuelva a llegar callado.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestListaDePreciosDePoliza(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.zone = cls.env['visar.zone'].search([], limit=1)
        cls.plan = cls.env['sale.subscription.plan'].search(
            [('name', 'like', 'Suscripción Mensual')], limit=1)
        cls.cliente = cls.env['res.partner'].create({'name': "Cliente de prueba"})

    def _orden(self):
        return self.env['sale.order'].create({'partner_id': self.cliente.id})

    def test_la_lista_del_plan_es_distinta_de_la_de_contado(self):
        """Si estas dos coincidieran, la prueba de abajo no probaría nada."""
        self.assertTrue(self.zone and self.plan, "hacen falta zona y plan configurados")
        con_plan = self.zone._visar_poliza_pricelist(self.plan)
        contado = self.zone._visar_poliza_pricelist()

        self.assertTrue(con_plan.visar_plan_id, "la lista del plan está configurada")
        self.assertNotEqual(con_plan, contado)

    def test_se_reimpone_la_lista_si_algo_la_cambio(self):
        orden = self._orden()
        orden._visar_apply_zone_pricelist(self.zone, plan=self.plan)
        esperada = orden.pricelist_id
        # Lo que hace `_update_address` por dentro: dejarla en otra.
        orden.pricelist_id = self.zone._visar_poliza_pricelist()

        corregido = orden._visar_reassert_zone_pricelist(self.zone, plan=self.plan)

        self.assertTrue(corregido, "tenía que detectar el cambio")
        self.assertEqual(orden.pricelist_id, esperada)

    def test_si_la_lista_ya_es_la_correcta_no_toca_nada(self):
        orden = self._orden()
        orden._visar_apply_zone_pricelist(self.zone, plan=self.plan)

        self.assertFalse(orden._visar_reassert_zone_pricelist(self.zone, plan=self.plan))

    def test_sin_plan_la_red_vigila_la_lista_de_la_zona(self):
        """El flujo de valoración no lleva plan y sufría lo mismo."""
        orden = self._orden()
        orden._visar_apply_zone_pricelist(self.zone)
        esperada = orden.pricelist_id
        otra = self.env['product.pricelist'].search([('id', '!=', esperada.id)], limit=1)
        if not otra:
            self.skipTest("solo hay una lista de precios en esta base")
        orden.pricelist_id = otra

        self.assertTrue(orden._visar_reassert_zone_pricelist(self.zone))
        self.assertEqual(orden.pricelist_id, esperada)

    def test_sin_zona_no_hace_nada(self):
        """Una orden fuera del flujo Visar no se toca."""
        orden = self._orden()
        lista = orden.pricelist_id

        self.assertFalse(orden._visar_reassert_zone_pricelist(self.env['visar.zone']))
        self.assertEqual(orden.pricelist_id, lista)
