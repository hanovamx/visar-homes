# -*- coding: utf-8 -*-
"""El contrato de los dos ganchos de CRM que `visar_base` declara (2-oct-2026).

Son no-op aquí y los implementa `visar_crm`. Viven en el ancestro común porque
quien los LLAMA son módulos que **no pueden depender de `visar_crm`**:
`visar_field_app` depende de `visar_fsm`, y `visar_appointment` no declara `crm`.

Lo que se protege: que llamarlos sea siempre seguro. Si alguien los convierte en
`raise NotImplementedError` o les cambia la firma, lo que se rompe no es esto —
es una reserva que el cliente estaba a punto de pagar, y en silencio. Por eso
hay prueba de algo que "no hace nada".
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestCrmHooks(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente ganchos'})
        cls.order = cls.env['sale.order'].create({'partner_id': cls.partner.id})

    def test_los_dos_ganchos_existen_con_su_firma(self):
        self.assertTrue(hasattr(self.order, '_visar_inherit_crm_from'))
        self.assertTrue(hasattr(self.order, '_visar_crm_after_fill'))
        self.assertTrue(hasattr(self.order, '_visar_is_formal_quote'))

    def test_heredar_de_una_orden_vacia_no_explota(self):
        otra = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.order._visar_inherit_crm_from(otra)
        self.order._visar_inherit_crm_from(self.env['sale.order'].browse())

    def test_el_gancho_del_wizard_acepta_payload_vacio_y_sin_canal(self):
        self.order._visar_crm_after_fill({})
        self.order._visar_crm_after_fill({}, canal='web')
        self.order._visar_crm_after_fill(None, canal=None)

    def test_una_orden_normal_no_es_cotizacion_formal(self):
        """La etapa «Cotización enviada» es la cotización formal que arma una
        persona tras la visita, no cualquier carrito. Quien dice sí es
        `visar_field_app`; aquí el default tiene que ser NO."""
        self.assertFalse(
            self.env['sale.order'].create({
                'partner_id': self.partner.id,
            })._visar_is_formal_quote())
