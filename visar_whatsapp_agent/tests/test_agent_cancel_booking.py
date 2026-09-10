# -*- coding: utf-8 -*-
"""`agent_cancel_pending_booking`: la liga vieja muere cuando el cliente cambia.

Lo que se fija aqui son los limites, porque es un metodo que ESCRIBE y cancela
ventas: solo lo del cliente que pide, solo lo que no esta pagado ni a medio
pagar, y que suelta el apartado para que el hueco vuelva a la agenda.
"""
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentCancelPendingBooking(TransactionCase):

    # Sinteticos: no existen en la copia de produccion.
    WA = '5219990771122'
    OTRO = '5219990773344'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.partner = cls.env['res.partner'].create(
            {'name': 'Cliente Cancela', 'phone': cls.WA})
        cls.product = cls.env['product.product'].create(
            {'name': 'Servicio Cancela', 'type': 'service', 'list_price': 600.0})
        cls.resource = cls.env['appointment.resource'].create(
            {'name': 'Tecnico Cancela', 'capacity': 1})
        cls.apt_type = cls.env['appointment.type'].create({
            'name': 'Tipo Cancela', 'schedule_based_on': 'resources',
            'resource_ids': [(6, 0, cls.resource.ids)],
        })
        cls.start = fields.Datetime.add(fields.Datetime.now(), days=4)
        cls.stop = fields.Datetime.add(cls.start, hours=1)

    def _reserva(self):
        """Orden en borrador + reserva pendiente + apartado, como la deja el agente."""
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})],
        })
        booking = self.env['calendar.booking'].create({
            'appointment_type_id': self.apt_type.id, 'name': 'Reserva Cancela',
            'partner_id': self.partner.id, 'product_id': self.product.id,
            'start': self.start, 'stop': self.stop,
            'order_line_id': order.order_line[:1].id,
        })
        hold = self.env['visar.slot.hold'].create({
            'appointment_resource_id': self.resource.id,
            'start': self.start, 'stop': self.stop, 'capacity': 1,
            'owner_key': self.partner.visar_phone_nat10,
            'expire_at': fields.Datetime.add(fields.Datetime.now(), minutes=10),
            'calendar_booking_id': booking.id,
        })
        return order, hold

    def test_anula_la_orden_y_suelta_el_apartado(self):
        order, hold = self._reserva()
        r = self.Tools.agent_cancel_pending_booking(
            {'phone': self.WA, 'order_id': order.id})
        self.assertEqual(r, {'cancelled': True, 'reason': None})
        self.assertEqual(order.state, 'cancel')
        self.assertFalse(hold.exists(), "el hueco vuelve a la agenda en el acto")

    def test_no_anula_lo_de_otro_telefono(self):
        order, hold = self._reserva()
        r = self.Tools.agent_cancel_pending_booking(
            {'phone': self.OTRO, 'order_id': order.id})
        self.assertEqual(r['reason'], 'not_owner')
        self.assertNotEqual(order.state, 'cancel')
        self.assertTrue(hold.exists())

    def test_no_toca_lo_pagado(self):
        order, _hold = self._reserva()
        order.state = 'sale'
        r = self.Tools.agent_cancel_pending_booking(
            {'phone': self.WA, 'order_id': order.id})
        self.assertEqual(r, {'cancelled': False, 'reason': 'paid'})
        self.assertEqual(order.state, 'sale')

    def test_dos_veces_es_lo_mismo_que_una(self):
        """Idempotente: la liga ya estaba muerta, que es lo que se pedia."""
        order, _hold = self._reserva()
        self.Tools.agent_cancel_pending_booking({'phone': self.WA, 'order_id': order.id})
        r = self.Tools.agent_cancel_pending_booking({'phone': self.WA, 'order_id': order.id})
        self.assertTrue(r['cancelled'])
        self.assertEqual(r['reason'], 'already_cancelled')

    def test_nunca_lanza(self):
        for payload in (None, {}, {'phone': 'x'}, {'phone': self.WA},
                        {'phone': self.WA, 'order_id': 'abc'},
                        {'phone': self.WA, 'order_id': 999999999}):
            r = self.Tools.agent_cancel_pending_booking(payload)
            self.assertFalse(r['cancelled'], payload)
