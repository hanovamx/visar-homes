from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_appointment.controllers.appointment import (
    VisarAppointmentController,
)


@tagged('post_install', '-at_install')
class TestBookingPartner(TransactionCase):
    """El wizard debe dejar en la orden al cliente RESERVADO, no al usuario
    logueado que corre el flujo (bug: órdenes quedaban en 'Administrator' y el
    cliente real, huérfano sin órdenes). Ver controllers/appointment.py:
    _visar_should_reassign / _visar_booking_customer.

    La decisión pura (_visar_should_reassign) se prueba directamente: el flujo
    completo depende del contexto HTTP (_is_anonymous_cart, request), que se
    verifica a mano reservando por el wizard. Aquí se fija la lógica del arreglo.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({
            'name': 'Jonathan Velazquez Test', 'phone': '8123415696'})
        # Partner con usuario INTERNO: staff/admin corriendo el wizard.
        cls.staff_partner = cls.env.ref('base.user_admin').partner_id
        # Partner del usuario PÚBLICO del sitio: carrito anónimo.
        cls.public_partner = cls.env.ref('base.public_user').partner_id
        # Cliente de portal reservando para sí mismo: su usuario es 'share'.
        cls.portal_user = cls.env['res.users'].create({
            'name': 'Cliente Portal Test',
            'login': 'portal_cli_test',
            'email': 'portal_cli_test@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })
        cls.portal_partner = cls.portal_user.partner_id

    # --- decisión: a quién debe quedar la orden -------------------------------

    def test_staff_on_behalf_reassigns(self):
        # Staff/admin reserva por un cliente -> la orden debe quedar del cliente.
        self.assertTrue(VisarAppointmentController._visar_should_reassign(
            self.staff_partner, self.customer, is_anonymous=False))

    def test_anonymous_reassigns(self):
        # Carrito anónimo (usuario público) -> se aplica el cliente. Es el flujo
        # real de siempre; su resultado no cambia.
        self.assertTrue(VisarAppointmentController._visar_should_reassign(
            self.public_partner, self.customer, is_anonymous=True))

    def test_portal_self_untouched(self):
        # Cliente de portal reservando para SÍ MISMO -> NO se reasigna (no se le
        # crea un duplicado ni se le secuestra el carrito). Regresión #1.
        self.assertFalse(VisarAppointmentController._visar_should_reassign(
            self.portal_partner, self.customer, is_anonymous=False))

    def test_same_partner_untouched(self):
        # Si el cliente ya es el de la orden, no hay nada que hacer.
        self.assertFalse(VisarAppointmentController._visar_should_reassign(
            self.customer, self.customer, is_anonymous=False))

    def test_no_booking_partner_untouched(self):
        # Sin cliente reservado no se toca la orden (ni siquiera en anónimo).
        empty = self.env['res.partner'].browse()
        self.assertFalse(VisarAppointmentController._visar_should_reassign(
            self.staff_partner, empty, is_anonymous=True))

    # --- regresión #4: candado de la dirección de servicio --------------------

    def test_service_shipping_lock_holds(self):
        # Una vez fijada la dirección de servicio Visar, el checkout no puede
        # cambiar partner_shipping_id (bug histórico "Santos Cantú").
        service_addr = self.env['res.partner'].create({
            'name': 'Dir Servicio Test', 'type': 'delivery',
            'parent_id': self.customer.id})
        other_addr = self.env['res.partner'].create({
            'name': 'Otra Dir Test', 'type': 'delivery',
            'parent_id': self.customer.id})
        order = self.env['sale.order'].create({'partner_id': self.customer.id})

        order._visar_set_service_shipping(service_addr)
        self.assertEqual(order.visar_service_partner_id, service_addr)
        self.assertEqual(order.partner_shipping_id, service_addr)

        # Intento de cambiarla SIN el contexto permitido: queda fijada.
        order.partner_shipping_id = other_addr.id
        self.assertEqual(
            order.partner_shipping_id, service_addr,
            "la dirección de servicio Visar no debe poder cambiarse en el checkout")
