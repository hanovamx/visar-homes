# -*- coding: utf-8 -*-
"""Reagendar una cita ya pagada: politica, pertenencia y lo que cuelga.

Hasta agosto de 2026 **nada reagendaba nada** — el boton "el cliente no llego"
de la app de campo solo levanta una bandera, y su propio codigo dice que el
calendario lo rehace oficina a mano. Esto es el primer camino automatico, y
ademas es de cara al cliente, asi que lo que se prueba aqui es sobre todo lo que
NO debe poder pasar.

Tres familias, y las tres existen por un motivo concreto:

  * **Pertenencia.** El id de la cita viaja por el chat y un id es adivinable.
    Sin la comprobacion, escribir un numero movia la cita de otra persona.
  * **Politica.** Las 24 h en las DOS puntas y el tope de cambios. Un guardia que
    solo se comprueba al listar no es un guardia: entre listar y confirmar el
    cliente estuvo conversando.
  * **Lo que cuelga.** Mover el evento sin mover la tarea de campo deja al
    tecnico con la hora vieja en su app. Es la mitad que se olvida.

Lo que NO se prueba aqui es la oferta de horarios contra el catalogo real: eso
depende de tener zonas, tecnicos y tipos de cita configurados, y en una BD sin
catalogo daria un falso verde. Ver `test_agent_prepare_booking.py`, que aplica el
mismo criterio.
"""
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentReschedule(TransactionCase):

    WA = '5219990771122'
    WA_OTRO = '5219990773344'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Event = cls.env['calendar.event']
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.reschedule.min_hours', '24')
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.reschedule.max_times', '2')

        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Reagenda', 'phone': '9990771122',
            'street': 'Calle Falsa 123', 'zip': '64000', 'city': 'Monterrey'})
        cls.otro = cls.env['res.partner'].create({
            'name': 'Otro Cliente', 'phone': '9990773344'})

    def _cita(self, dentro_de_horas=72, partner=None):
        """Una cita futura del cliente, con su pedido confirmado detras."""
        partner = partner or self.partner
        inicio = fields.Datetime.add(fields.Datetime.now(), hours=dentro_de_horas)
        evento = self.Event.create({
            'name': 'Servicio de prueba',
            'start': inicio,
            'stop': fields.Datetime.add(inicio, hours=1),
        })
        # `visar_is_service` y el estado confirmado NO son decoracion: la lista
        # de servicios del cliente solo recorre lineas de servicio de pedidos
        # confirmados, asi que sin las dos cosas la cita no existe para el chat.
        producto = self.env['product.product'].create({
            'name': 'Servicio de prueba reagenda', 'type': 'service',
            'visar_is_service': True})
        pedido = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': producto.id,
                                   'calendar_event_id': evento.id})],
        })
        # Se escribe el estado en vez de confirmar: `action_confirm` arrastra
        # tareas de campo, facturacion y suscripciones, y nada de eso es lo que
        # esta prueba mira.
        pedido.write({'state': 'sale'})
        return evento, pedido

    # --- Pertenencia ---------------------------------------------------

    def test_no_se_puede_mover_la_cita_de_otro(self):
        """El id viaja por el chat: sin esto, adivinar un numero mueve tu cita."""
        evento, _pedido = self._cita()
        resultado = self.Tools.agent_reschedule_days({
            'phone': self.WA_OTRO, 'event_id': evento.id})
        self.assertEqual(resultado['blocked'], 'not_found')

    def test_una_cita_inexistente_no_delata_que_no_existe(self):
        """Mismo motivo para 'no existe' y 'no es tuya'.

        Distinguirlos convertiria el metodo en un oraculo para enumerar ids.
        """
        propia = self.Tools.agent_reschedule_days(
            {'phone': self.WA, 'event_id': 999999999})
        ajena, _p = self._cita(partner=self.otro)
        de_otro = self.Tools.agent_reschedule_days(
            {'phone': self.WA, 'event_id': ajena[0].id if isinstance(ajena, tuple) else ajena.id})
        self.assertEqual(propia['blocked'], de_otro['blocked'], 'not_found')

    def test_confirmar_tambien_comprueba_la_pertenencia(self):
        """No basta con comprobarlo al listar: confirmar es el que escribe."""
        evento, _pedido = self._cita()
        inicio = fields.Datetime.add(fields.Datetime.now(), hours=72)
        resultado = self.Tools.agent_reschedule_confirm({
            'phone': self.WA_OTRO, 'event_id': evento.id,
            'start': inicio, 'stop': fields.Datetime.add(inicio, hours=1)})
        self.assertFalse(resultado['ok'])
        self.assertEqual(resultado['reason'], 'not_found')

    # --- Politica ------------------------------------------------------

    def test_una_cita_lejana_se_puede_mover(self):
        evento, _pedido = self._cita(dentro_de_horas=72)
        self.assertIsNone(evento._visar_reschedule_blocked())

    def test_una_cita_a_menos_de_24h_no(self):
        evento, _pedido = self._cita(dentro_de_horas=10)
        self.assertEqual(evento._visar_reschedule_blocked(), 'muy_proxima')

    def test_el_horario_NUEVO_tambien_respeta_las_24h(self):
        """La segunda punta, y la que se olvida.

        La cita actual esta a tres dias, asi que se puede mover — pero no a
        dentro de dos horas.
        """
        evento, _pedido = self._cita(dentro_de_horas=72)
        pronto = fields.Datetime.add(fields.Datetime.now(), hours=2)
        self.assertEqual(
            evento._visar_reschedule_blocked(nuevo_inicio=pronto), 'muy_proxima')

    def test_al_tercer_cambio_hace_falta_un_asesor(self):
        evento, _pedido = self._cita(dentro_de_horas=72)
        evento.visar_reschedule_count = 2
        self.assertEqual(evento._visar_reschedule_blocked(), 'limite')

    def test_una_cita_que_ya_paso_no_se_mueve(self):
        evento, _pedido = self._cita(dentro_de_horas=-5)
        self.assertEqual(evento._visar_reschedule_blocked(), 'ya_paso')

    def test_no_existe_camino_de_cancelacion(self):
        """Decision de negocio: el servicio esta cobrado y no hay reembolso.

        Se fija como prueba para que anadir un `agent_cancel_*` sin resolver
        antes que pasa con el dinero rompa aqui y no en produccion.
        """
        metodos = [m for m in dir(self.Tools)
                   if m.startswith('agent_') and 'cancel' in m]
        self.assertEqual(metodos, [])

    # --- Lo que cuelga -------------------------------------------------

    def test_mover_la_cita_mueve_la_tarea_del_tecnico(self):
        """La mitad que se olvida.

        `_visar_enrich_fsm_tasks` copia las fechas UNA vez al confirmar el
        pedido; no es un enlace. Sin sincronizar, el tecnico abre su app, ve la
        hora vieja y se presenta cuando no toca.
        """
        evento, pedido = self._cita(dentro_de_horas=72)
        tarea = self.env['project.task'].create({
            'name': 'Tarea de prueba',
            'planned_date_begin': evento.start,
            'date_deadline': evento.stop,
        })
        pedido.order_line[0].task_id = tarea.id

        nuevo = fields.Datetime.add(fields.Datetime.now(), hours=96)
        ok, motivo = evento._visar_reschedule(
            nuevo, fields.Datetime.add(nuevo, hours=1))
        self.assertTrue(ok, motivo)
        self.assertEqual(tarea.planned_date_begin, nuevo)

    def test_mover_cuenta_el_cambio(self):
        evento, _pedido = self._cita(dentro_de_horas=72)
        nuevo = fields.Datetime.add(fields.Datetime.now(), hours=96)
        evento._visar_reschedule(nuevo, fields.Datetime.add(nuevo, hours=1))
        self.assertEqual(evento.visar_reschedule_count, 1)
        self.assertEqual(evento.start, nuevo)

    def test_el_tope_se_respeta_al_escribir_no_solo_al_listar(self):
        """Entre listar y confirmar el cliente estuvo conversando."""
        evento, _pedido = self._cita(dentro_de_horas=72)
        evento.visar_reschedule_count = 2
        nuevo = fields.Datetime.add(fields.Datetime.now(), hours=96)
        ok, motivo = evento._visar_reschedule(
            nuevo, fields.Datetime.add(nuevo, hours=1))
        self.assertFalse(ok)
        self.assertEqual(motivo, 'limite')

    # --- La lista de servicios -----------------------------------------

    def test_la_lista_dice_cual_se_puede_mover_y_por_que_no(self):
        """Sin `event_id` el cliente puede decir "muevela" y no hay cual."""
        evento, _pedido = self._cita(dentro_de_horas=72)
        salida = self.Tools.agent_customer_services(
            {'phone': self.WA, 'scope': 'upcoming'})
        self.assertTrue(salida['found'])
        mios = [s for s in salida['services'] if s.get('event_id') == evento.id]
        self.assertEqual(len(mios), 1)
        self.assertTrue(mios[0]['can_reschedule'])
        self.assertIsNone(mios[0]['reschedule_reason'])
