# -*- coding: utf-8 -*-
"""Una cita no compite consigo misma al reagendarse.

Es el mismo error que `_filter_unavailable_bookings` ya tuvo que corregir con
los apartados, y salió caro: el nativo declaraba sin cupo el horario que el
propio cliente ocupaba, y **todas** las reservas por WhatsApp acababan cobradas
y sin cita. Medido en servidor: `order.state = sale`, `tx = done`,
`calendar_event_id = None`.

Al reagendar reaparece con dos caras, y las dos se cierran con el mismo contexto
`visar_ignore_event_id`:

  * **capacidad** — sin excluirla, el cliente no vería libre ni el horario que ya
    tiene, así que "confírmame el mismo día una hora más tarde" sería imposible;
  * **traslado** — sin excluirla, la cita es una parada del día que se come el
    presupuesto de las franjas vecinas: mover algo de las 10:00 a las 11:00
    parecería inviable por un viaje contra sí mismo que dura cero minutos.

Sin estas pruebas los dos fallos son **silenciosos**: no hay excepción, solo
horarios que no se ofrecen. El cliente ve "no hay disponibilidad" y nadie se
entera de que la había.
"""
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestReagendaCapacidad(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.resource = cls.env['appointment.resource'].create({
            'name': 'Tecnico Test Reagenda', 'capacity': 1})
        cls.apt_type = cls.env['appointment.type'].create({
            'name': 'Tipo Test Reagenda',
            'schedule_based_on': 'resources',
            'resource_ids': [(6, 0, cls.resource.ids)],
        })
        cls.start = fields.Datetime.add(fields.Datetime.now(), days=3)
        cls.stop = fields.Datetime.add(cls.start, hours=1)

    def _cita_ocupando_el_hueco(self):
        """Una cita confirmada del recurso, con su línea de reserva."""
        evento = self.env['calendar.event'].create({
            'name': 'Cita que ocupa',
            'start': self.start,
            'stop': self.stop,
            'appointment_type_id': self.apt_type.id,
        })
        self.env['appointment.booking.line'].create({
            'calendar_event_id': evento.id,
            'appointment_resource_id': self.resource.id,
            'capacity_reserved': 1,
        })
        return evento

    def _libre(self, **context):
        capacidad = self.apt_type.with_context(
            **context)._get_resources_remaining_capacity(
                self.resource, self.start, self.stop,
                with_linked_resources=False)
        return capacidad['total_remaining_capacity']

    def test_sin_citas_el_hueco_esta_libre(self):
        self.assertEqual(self._libre(), 1)

    def test_una_cita_ocupa_el_hueco(self):
        """La linea base: sin esto, el resto de la prueba no significa nada."""
        self._cita_ocupando_el_hueco()
        self.assertEqual(self._libre(), 0)

    def test_la_cita_que_se_reagenda_no_se_bloquea_a_si_misma(self):
        """El corazon del asunto.

        Con la cita excluida, su propio hueco vuelve a contarse libre — que es lo
        que permite ofrecerle al cliente el horario que ya tiene, y los de
        alrededor.
        """
        evento = self._cita_ocupando_el_hueco()
        self.assertEqual(self._libre(), 0, "ocupado para todos los demas")
        self.assertEqual(
            self._libre(visar_ignore_event_id=evento.id), 1,
            "y libre para ella misma")

    def test_excluir_una_cita_no_libera_las_de_otros(self):
        """Excluir la propia no puede vaciar la agenda del vecino.

        Si el filtro estuviera mal escrito —ignorando la clave, o comparando mal—
        el sintoma seria sobreventa: dos clientes en el mismo hueco.
        """
        mia = self._cita_ocupando_el_hueco()
        ajena = self._cita_ocupando_el_hueco()
        self.assertEqual(
            self._libre(visar_ignore_event_id=mia.id), 0,
            "la cita de otro sigue ocupando")
        self.assertEqual(self._libre(visar_ignore_event_id=ajena.id), 0)

    def test_sin_contexto_no_cuesta_ni_una_consulta(self):
        """El camino normal no paga por una funcion que no usa.

        `_get_resources_remaining_capacity` corre en el camino caliente de la
        generacion del calendario: una consulta de mas por franja se nota.
        """
        self._cita_ocupando_el_hueco()
        propia = self.apt_type._visar_own_capacity(
            self.resource, self.start, self.stop)
        self.assertEqual(propia, {})

    def test_el_traslado_no_cuenta_la_cita_que_se_mueve_como_parada(self):
        """La segunda cara, y la menos obvia.

        Como parada del dia, la cita se come el presupuesto de traslado de las
        franjas vecinas — contra si misma, y el viaje dura cero minutos.
        """
        evento = self._cita_ocupando_el_hueco()
        ventana = (fields.Datetime.add(self.start, hours=-4),
                   fields.Datetime.add(self.stop, hours=4))
        import pytz
        tz = pytz.timezone('America/Monterrey')

        con = self.apt_type._visar_travel_stops_by_day(self.resource, tz, ventana)
        self.assertTrue(any(paradas for paradas in con.values()),
                        "sin excluir, la cita es una parada del dia")

        sin = self.apt_type.with_context(
            visar_ignore_event_id=evento.id)._visar_travel_stops_by_day(
                self.resource, tz, ventana)
        self.assertFalse(any(paradas for paradas in sin.values()),
                         "excluida, deja de estorbarse a si misma")
