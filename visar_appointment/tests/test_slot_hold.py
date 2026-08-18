from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestSlotHold(TransactionCase):
    """`visar.slot.hold`: apartar un horario lo saca de la disponibilidad.

    El apartado existe porque una reserva pendiente de pago NO consume capacidad
    en Odoo (solo la consumen las citas ya confirmadas), asi que dos clientes
    pueden llegar al pago del mismo horario y el segundo paga y se queda sin
    cita. Ver `models/visar_slot_hold.py`.

    Lo que se fija aqui, y que es facil romper sin darse cuenta:
      * el apartado descuenta capacidad en `_get_resources_remaining_capacity`,
        que es el punto por el que pasan TODOS los caminos (calendario, submit,
        agente);
      * el DUENO del apartado no se bloquea a si mismo;
      * un apartado vencido deja de estorbar aunque el cron no haya corrido;
      * un apartado congelado (pago en vuelo) sobrevive a su vencimiento.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Hold = cls.env['visar.slot.hold']
        cls.resource = cls.env['appointment.resource'].create({
            'name': 'Tecnico Test Hold',
            'capacity': 1,
        })
        cls.apt_type = cls.env['appointment.type'].create({
            'name': 'Tipo Test Hold',
            'schedule_based_on': 'resources',
            'resource_ids': [(6, 0, cls.resource.ids)],
        })
        # Una hora cualquiera en el futuro, en UTC naive (misma convencion que
        # appointment.booking.line.event_start).
        cls.start = fields.Datetime.add(fields.Datetime.now(), days=3)
        cls.stop = fields.Datetime.add(cls.start, hours=1)
        cls.owner = '8112345678'

    def _remaining(self, **context):
        capacity = self.apt_type.with_context(**context)._get_resources_remaining_capacity(
            self.resource, self.start, self.stop, with_linked_resources=False)
        return capacity['total_remaining_capacity']

    def _hold(self, owner=None, **overrides):
        vals = {
            'appointment_resource_id': self.resource.id,
            'start': self.start,
            'stop': self.stop,
            'capacity': 1,
            'owner_key': owner or self.owner,
            'expire_at': fields.Datetime.add(fields.Datetime.now(), minutes=10),
        }
        vals.update(overrides)
        return self.Hold.create(vals)

    # --- lo esencial ---------------------------------------------------------

    def test_sin_apartado_hay_capacidad(self):
        self.assertEqual(self._remaining(), 1)

    def test_apartado_quita_capacidad(self):
        self._hold()
        self.assertEqual(
            self._remaining(), 0,
            "un horario apartado no puede seguir ofreciendose a otros clientes")

    def test_el_dueno_no_se_bloquea_a_si_mismo(self):
        self._hold()
        self.assertEqual(
            self._remaining(visar_hold_owner=self.owner), 1,
            "quien aparto el horario tiene que poder reservarlo; si no, el "
            "apartado seria un tiro en el pie")

    def test_otro_cliente_si_lo_ve_ocupado(self):
        self._hold()
        self.assertEqual(self._remaining(visar_hold_owner='9999999999'), 0)

    def test_ignorar_apartados_por_id(self):
        hold = self._hold()
        self.assertEqual(self._remaining(visar_ignore_hold_ids=[hold.id]), 1)

    # --- vencimiento ---------------------------------------------------------

    def test_apartado_vencido_no_estorba_aunque_no_corra_el_cron(self):
        self._hold(expire_at=fields.Datetime.subtract(
            fields.Datetime.now(), minutes=1))
        self.assertEqual(
            self._remaining(), 1,
            "la disponibilidad filtra por expire_at, asi que la correccion no "
            "depende de que el cron de limpieza haya corrido")

    def test_apartado_congelado_sobrevive_al_vencimiento(self):
        # Pago en vuelo: soltar el horario aqui dejaria al cliente pagado y sin
        # cita. Hoy no pasa (el pago es simulado), pero con Stripe si.
        self._hold(is_frozen=True, expire_at=fields.Datetime.subtract(
            fields.Datetime.now(), minutes=1))
        self.assertEqual(self._remaining(), 0)

    def test_el_cron_borra_lo_vencido_pero_no_lo_congelado(self):
        self._hold(expire_at=fields.Datetime.subtract(
            fields.Datetime.now(), minutes=1))
        frozen = self._hold(owner='9990001111', is_frozen=True,
                            expire_at=fields.Datetime.subtract(
                                fields.Datetime.now(), minutes=1))
        self.Hold._visar_cron_gc()
        self.assertTrue(frozen.exists())
        self.assertEqual(
            self.Hold.search_count([('owner_key', '=', self.owner)]), 0)

    # --- ciclo de vida -------------------------------------------------------

    def test_un_telefono_solo_aparta_un_horario(self):
        first = self.Hold._visar_hold(self.resource, self.start, self.stop,
                                      self.owner)
        other_start = fields.Datetime.add(self.start, hours=2)
        second = self.Hold._visar_hold(self.resource, other_start,
                                       fields.Datetime.add(other_start, hours=1),
                                       self.owner)
        self.assertFalse(
            first.exists(),
            "pedir un apartado nuevo libera el anterior: un cliente indeciso no "
            "puede bloquear la agenda saltando de horario en horario")
        self.assertTrue(second.exists())

    # --- regresiones encontradas al verificar en el servidor (18-ago) ---------

    def test_una_reserva_no_compite_contra_su_propio_apartado(self):
        """REGRESION T3f: se pagaba y NO se creaba la cita.

        El nativo `_filter_unavailable_bookings` consulta capacidad SIN contexto,
        asi que el override restaba el apartado del propio cliente, declaraba el
        horario sin cupo y descartaba la reserva — despues de cobrar. Medido en el
        servidor: order=sale, tx=done, calendar_event_id=None. Pasaba en TODAS las
        reservas por WhatsApp.
        """
        booking = self.env['calendar.booking'].create({
            'appointment_type_id': self.apt_type.id,
            'name': 'Reserva Test Hold',
            'partner_id': self.env['res.partner'].create(
                {'name': 'Cliente Test Hold'}).id,
            'start': self.start,
            'stop': self.stop,
            'booking_line_ids': [(0, 0, {
                'appointment_resource_id': self.resource.id,
                'capacity_reserved': 1,
                'capacity_used': 1,
            })],
        })
        self._hold(calendar_booking_id=booking.id)
        self.assertNotIn(
            booking, booking._filter_unavailable_bookings(),
            "su propio apartado no puede dejar a la reserva sin cupo: es "
            "exactamente el desastre que el apartado existe para evitar")

    def test_las_dos_rutas_de_capacidad_coinciden(self):
        """La foto precargada y la consulta a la base tienen que dar lo mismo.

        `_get_appointment_slots` siembra `visar_hold_cache` para no consultar una
        vez por slot (costaba ~1 s por calendario). Si las dos rutas divergen, la
        disponibilidad del calendario y la de la reserva dejarian de coincidir.
        """
        self._hold()
        Hold = self.Hold
        from_db = Hold._visar_used_capacity(self.resource, self.start, self.stop)
        snapshot = Hold._visar_snapshot(self.resource)
        from_cache = Hold.with_context(
            visar_hold_cache=snapshot)._visar_used_capacity(
                self.resource, self.start, self.stop)
        self.assertEqual(from_db, from_cache)

        # Y la exclusion del dueno tiene que respetarse igual por las dos rutas.
        self.assertEqual(
            Hold._visar_used_capacity(self.resource, self.start, self.stop,
                                      exclude_owner=self.owner),
            Hold.with_context(visar_hold_cache=snapshot)._visar_used_capacity(
                self.resource, self.start, self.stop, exclude_owner=self.owner))

    def test_liberar_devuelve_la_capacidad(self):
        self._hold()
        self.assertEqual(self._remaining(), 0)
        self.Hold._visar_release(owner_key=self.owner)
        self.assertEqual(self._remaining(), 1)
