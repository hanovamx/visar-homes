# -*- coding: utf-8 -*-
"""Relleno del historico: enlazar solo donde se puede DEMOSTRAR (2-oct-2026).

La tentacion es rellenar "a ver que casa", y en esta base eso seria peor que no
rellenar: el telefono `7774501440` lo comparten SIETE `res.partner` (uno es
Administrator, con 171 ordenes) y tiene fichas duplicadas por grupo. Medido antes
de escribir el relleno: **197 de 220 pares (orden, grupo) casan con mas de una
ficha**. Sin puertas se enlazarian 183 ordenes, de las que 156 son ese telefono.

Lo que se fija aqui son las dos puertas, y que lo que no las pasa **se quede
intacto** en vez de recibir un enlace inventado.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestCrmBackfill(TransactionCase):

    NAT = '9990005544'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Lead = cls.env['crm.lead']
        cls.team = cls.env.ref('visar_crm.crm_team_whatsapp')
        cls.s_nuevo = cls.env.ref('visar_crm.crm_stage_wa_nuevo')

        cls.group = cls.env['visar.service.group'].create({
            'name': 'Backfill Fumigacion', 'code': 'BKF_FUM'})
        cls.dim = cls.env['visar.service.dimension'].create({
            'name': 'Interior', 'code': 'BKF_INT', 'group_id': cls.group.id})
        cls.prod = cls.env['product.template'].create({
            'name': 'Backfill Fum', 'visar_is_service': True,
            'list_price': 690.0,
            'visar_dimension_id': cls.dim.id}).product_variant_id

    def _lead(self, nat=None):
        return self.Lead.create({
            'name': 'Ficha backfill', 'type': 'opportunity',
            'team_id': self.team.id, 'stage_id': self.s_nuevo.id,
            'visar_service_group_id': self.group.id,
            'visar_wa_phone_norm': nat or self.NAT,
        })

    def _order(self, nat=None, partner=None):
        partner = partner or self.env['res.partner'].create({
            'name': 'Cliente backfill', 'phone': nat or self.NAT})
        return self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': self.prod.id,
                                   'product_uom_qty': 1})],
        })

    def test_enlaza_cuando_el_match_es_unico(self):
        lead = self._lead()
        order = self._order()
        self.assertFalse(order.opportunity_id)

        self.Lead._visar_crm_backfill_order_links()

        self.assertEqual(order.opportunity_id, lead)

    def test_no_enlaza_si_el_telefono_es_de_varios_contactos(self):
        """Primera puerta. Es el caso del telefono de pruebas de produccion."""
        lead = self._lead()
        order = self._order()
        # Un segundo contacto con el MISMO numero: ya no se puede demostrar de
        # quien es la orden.
        self.env['res.partner'].create({
            'name': 'Otro con el mismo numero', 'phone': self.NAT})

        self.Lead._visar_crm_backfill_order_links()

        self.assertFalse(order.opportunity_id)
        self.assertEqual(lead.stage_id, self.s_nuevo)

    def test_no_enlaza_si_hay_fichas_duplicadas_del_mismo_grupo(self):
        """Segunda puerta. Es el otro caso real: el mismo telefono con dos
        fichas del mismo grupo, y senalar una seria adivinar."""
        self._lead()
        self._lead()
        order = self._order()

        self.Lead._visar_crm_backfill_order_links()

        self.assertFalse(order.opportunity_id)

    def test_no_pisa_un_enlace_que_ya_existe(self):
        lead = self._lead()
        otra = self._lead(nat='9990006666')
        order = self._order()
        order.sudo().write({'opportunity_id': otra.id})

        self.Lead._visar_crm_backfill_order_links()

        self.assertEqual(order.opportunity_id, otra)
        self.assertNotEqual(order.opportunity_id, lead)

    def test_no_reparte_el_importe_del_historico(self):
        """Solo enlaza: repartir pisaria cifras que alguien ajusto a mano."""
        lead = self._lead()
        lead.expected_revenue = 12345.0
        order = self._order()

        self.Lead._visar_crm_backfill_order_links()

        self.assertEqual(order.opportunity_id, lead)
        self.assertAlmostEqual(lead.expected_revenue, 12345.0, 2)

    def test_devuelve_contadores_para_poder_leer_el_resultado(self):
        self._lead()
        self._order()
        cuenta = self.Lead._visar_crm_backfill_order_links()
        self.assertIn('enlazadas', cuenta)
        self.assertGreaterEqual(cuenta['enlazadas'], 1)
