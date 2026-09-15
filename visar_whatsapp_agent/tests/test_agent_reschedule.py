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
        # La unica excepcion es `agent_cancel_pending_booking` (10-sep-2026), y
        # entra porque NO toca dinero: solo anula reservas sin pagar ni a medio
        # pagar, para que la liga vieja muera cuando el cliente cambia de fecha.
        # Que no toque lo pagado lo fija `test_agent_cancel_booking.
        # test_no_toca_lo_pagado`. Cualquier otro `agent_cancel_*` sigue
        # rompiendo aqui, que es para lo que existe esta prueba.
        permitidos = {'agent_cancel_pending_booking'}
        metodos = [m for m in dir(self.Tools)
                   if m.startswith('agent_') and 'cancel' in m
                   and m not in permitidos]
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

    def test_la_lista_nombra_el_servicio_sin_sus_atributos(self):
        """El nombre de la variante lleva la zona y el tramo ("(B, 1-250)"): el
        cliente no debe ver la zona, y esos caracteres de mas dejaban la lista
        fuera del tope de WhatsApp (7 al 9-sep-2026)."""
        _evento, pedido = self._cita(dentro_de_horas=72)
        linea = pedido.order_line.filtered(lambda l: l.product_id.visar_is_service)[:1]
        salida = self.Tools.agent_customer_services(
            {'phone': self.WA, 'scope': 'upcoming'})
        nombres = {s['service'] for s in salida['services']}
        self.assertIn(linea.product_id.product_tmpl_id.name, nombres)
        if linea.product_id.product_template_attribute_value_ids:
            self.assertNotIn(linea.product_id.display_name, nombres)


@tagged('post_install', '-at_install')
class TestReagendaPorIncidencia(TestAgentReschedule):
    """La reagenda que autoriza VISAR, no la que pide el cliente por gusto.

    El tecnico acudio al domicilio y no se pudo prestar el servicio. Es un caso
    aparte porque **las reglas de la reagenda normal lo hacen imposible**: tras el
    no-show la cita ya esta en el pasado, asi que `_visar_reschedule_blocked`
    contesta 'ya_paso' a un cliente al que Visar acaba de invitar a elegir
    horario. Lo que se prueba aqui es que la autorizacion abre exactamente esa
    puerta y **ninguna otra**.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Una tarea SIN proyecto es privada, y ahi `stage_id` es la etapa
        # PERSONAL del usuario: escribirla revienta. Devolver la tarea a
        # "Programado" es media prueba de esta clase, asi que hace falta proyecto.
        cls.project = cls.env['project.project'].create({
            'name': 'FSM Incidencia Reagenda', 'is_fsm': True,
            'company_id': cls.env.company.id})

    def _autorizada(self, dentro_de_horas=72):
        evento, pedido = self._cita(dentro_de_horas=dentro_de_horas)
        evento.write({'visar_reschedule_granted_at': fields.Datetime.now()})
        return evento, pedido

    # --- Lo que la autorizacion SI abre --------------------------------

    def test_sin_autorizacion_una_cita_pasada_no_se_puede_mover(self):
        """La linea base: sin esto el resto de la clase no significa nada."""
        evento, _pedido = self._cita(dentro_de_horas=-2)
        self.assertEqual(evento._visar_reschedule_blocked(), 'ya_paso')

    def test_autorizada_una_cita_pasada_si_se_puede_mover(self):
        """Es la razon de existir de todo esto: tras un no-show la cita SIEMPRE
        esta en el pasado."""
        evento, _pedido = self._cita(dentro_de_horas=-2)
        evento.write({'visar_reschedule_granted_at': fields.Datetime.now()})
        self.assertIsNone(evento._visar_reschedule_blocked())

    def test_autorizada_se_salta_la_antelacion_de_la_cita_actual(self):
        """La punta 1 existe para que el cliente no deshaga la ruta del dia
        avisando tarde. Aqui la ruta ya se deshizo."""
        evento, _pedido = self._cita(dentro_de_horas=3)
        self.assertEqual(evento._visar_reschedule_blocked(), 'muy_proxima')
        evento.write({'visar_reschedule_granted_at': fields.Datetime.now()})
        self.assertIsNone(evento._visar_reschedule_blocked())

    # --- Lo que la autorizacion NO abre --------------------------------

    def test_el_horario_NUEVO_sigue_exigiendo_la_antelacion(self):
        """Decision de negocio: elegir para dentro de dos horas desordena la ruta
        del tecnico venga de una incidencia o de un capricho."""
        evento, _pedido = self._autorizada(dentro_de_horas=-2)
        pronto = fields.Datetime.add(fields.Datetime.now(), hours=3)
        self.assertEqual(
            evento._visar_reschedule_blocked(nuevo_inicio=pronto), 'muy_proxima')
        ok, motivo = evento._visar_reschedule(
            pronto, fields.Datetime.add(pronto, hours=1))
        self.assertFalse(ok)
        self.assertEqual(motivo, 'muy_proxima')

    def test_el_tope_de_cambios_sigue_aplicando(self):
        """Decision explicita del 12-sep-2026: la incidencia consume uno de los
        cambios del cliente. Agotados, lo atiende un asesor."""
        evento, _pedido = self._autorizada(dentro_de_horas=-2)
        evento.visar_reschedule_count = 2
        self.assertEqual(evento._visar_reschedule_blocked(), 'limite')

    def test_una_poliza_sigue_fuera_de_alcance(self):
        """Una visita de poliza no tiene cita que mover: es *agendar* lo que
        nunca tuvo fecha, y eso es otro requerimiento."""
        evento, pedido = self._autorizada(dentro_de_horas=-2)
        plan = self.env['sale.subscription.plan'].search([], limit=1)
        if not plan:
            self.skipTest("la BD no tiene planes de suscripcion configurados")
        # Odoo rechaza un plan sin producto recurrente ("Please add a recurring
        # product in the subscription or remove the recurring plan"), asi que el
        # producto de la linea tiene que serlo.
        pedido.order_line[0].product_id.product_tmpl_id.write(
            {'recurring_invoice': True})
        pedido.write({'plan_id': plan.id})
        self.assertEqual(evento._visar_reschedule_blocked(), 'poliza')

    # --- La autorizacion se consume ------------------------------------

    def test_mover_consume_la_autorizacion(self):
        """Si no se borrara, una cita con incidencia quedaria movible para
        siempre sin antelacion y sin tope."""
        evento, _pedido = self._autorizada(dentro_de_horas=-2)
        nuevo = fields.Datetime.add(fields.Datetime.now(), hours=96)
        ok, motivo = evento._visar_reschedule(
            nuevo, fields.Datetime.add(nuevo, hours=1))
        self.assertTrue(ok, motivo)
        self.assertFalse(evento.visar_reschedule_granted_at)

    # --- La tarea vuelve a su sitio ------------------------------------

    def test_la_MISMA_tarea_vuelve_a_programado(self):
        """No se crea ninguna tarea nueva: la de siempre recupera fecha y etapa.

        `_visar_sync_fsm_tasks` solo escribe fechas y tecnicos, asi que sin esto
        el cliente ya tiene horario nuevo y el tecnico no ve el servicio: se
        queda cancelado en "Incidencia — Reprogramar" con una fecha futura.
        """
        evento, pedido = self._autorizada(dentro_de_horas=-2)
        tarea = self.env['project.task'].create({
            'name': 'Tarea con incidencia',
            'project_id': self.project.id,
            'planned_date_begin': evento.start,
            'date_deadline': evento.stop,
            'state': '1_canceled',
        })
        pedido.order_line[0].task_id = tarea.id
        etapa_incidencia = self.env.ref(
            'industry_fsm.planning_project_stage_4', raise_if_not_found=False)
        if etapa_incidencia:
            tarea.stage_id = etapa_incidencia.id

        nuevo = fields.Datetime.add(fields.Datetime.now(), hours=96)
        ok, motivo = evento._visar_reschedule(
            nuevo, fields.Datetime.add(nuevo, hours=1))
        self.assertTrue(ok, motivo)

        self.assertEqual(tarea.planned_date_begin, nuevo, "fecha nueva")
        self.assertEqual(tarea.state, '01_in_progress', "ya no esta cancelada")
        etapa_programado = self.env.ref(
            'industry_fsm.planning_project_stage_0', raise_if_not_found=False)
        if etapa_programado:
            self.assertEqual(tarea.stage_id, etapa_programado)
        # Y sigue siendo UNA tarea, no dos.
        self.assertEqual(len(pedido.order_line[0].task_id), 1)

    def test_el_servicio_externo_dice_cuando_reagendo_el_cliente(self):
        """Visar, 15-sep: en la tarea solo constaba que el tecnico pidio
        reagendar, no cuando el cliente eligio el horario nuevo. La nota iba a la
        cita del calendario, y en UTC crudo."""
        evento, pedido = self._autorizada(dentro_de_horas=-2)
        tarea = self.env['project.task'].create({
            'name': 'Tarea con incidencia', 'project_id': self.project.id,
            'planned_date_begin': evento.start, 'date_deadline': evento.stop,
        })
        pedido.order_line[0].task_id = tarea.id

        nuevo = fields.Datetime.add(fields.Datetime.now(), hours=96)
        ok, motivo = evento._visar_reschedule(
            nuevo, fields.Datetime.add(nuevo, hours=1))
        self.assertTrue(ok, motivo)

        notas = tarea.message_ids.filtered(
            lambda m: 'reagendó el servicio' in (m.body or ''))
        self.assertEqual(len(notas), 1, "una nota en el servicio externo")
        cuerpo = notas.body
        self.assertIn('desde WhatsApp', cuerpo)
        self.assertIn('incidencia', cuerpo)
        self.assertNotIn('&lt;br', cuerpo, "el HTML no sale escapado")
        self.assertNotIn(fields.Datetime.to_string(nuevo), cuerpo,
                         "la hora va en la zona del cliente, no en UTC crudo")
        self.assertTrue(notas.subtype_id == self.env.ref('mail.mt_note'),
                        "nota interna: no avisa a los seguidores")
        self.assertTrue(evento.message_ids.filtered(
            lambda m: 'reagendó el servicio' in (m.body or '')),
            "y la cita la sigue teniendo")

    def test_la_lista_dice_cual_cita_autorizo_visar(self):
        """El boton "Elegir nuevo horario" encuentra la cita por aqui cuando la
        conversacion ya caduco (la invitacion vale 24 h; la conversacion, 3)."""
        autorizada, _pedido = self._autorizada(dentro_de_horas=-2)
        normal, _otro = self._cita(dentro_de_horas=72)
        servicios = self.Tools._agent_partner_services(self.partner, 'all')
        por_cita = {s['event_id']: s for s in servicios}
        self.assertTrue(por_cita[autorizada.id]['reschedule_granted'])
        self.assertFalse(por_cita[normal.id]['reschedule_granted'])

    def test_una_tarea_completada_no_se_reabre(self):
        """Mover la cita no puede resucitar un servicio que ya se presto."""
        evento, pedido = self._autorizada(dentro_de_horas=-2)
        tarea = self.env['project.task'].create({
            'name': 'Tarea ya cerrada', 'project_id': self.project.id,
            'state': '1_done'})
        pedido.order_line[0].task_id = tarea.id
        nuevo = fields.Datetime.add(fields.Datetime.now(), hours=96)
        evento._visar_reschedule(nuevo, fields.Datetime.add(nuevo, hours=1))
        self.assertEqual(tarea.state, '1_done')

    # --- Una reagenda normal no toca la etapa --------------------------

    def test_sin_incidencia_no_se_reabre_ninguna_etapa(self):
        """Un cliente que mueve su cita por gusto no tiene nada que reabrir."""
        evento, pedido = self._cita(dentro_de_horas=72)
        tarea = self.env['project.task'].create({
            'name': 'Tarea normal', 'project_id': self.project.id,
            'state': '1_canceled'})
        pedido.order_line[0].task_id = tarea.id
        nuevo = fields.Datetime.add(fields.Datetime.now(), hours=96)
        ok, motivo = evento._visar_reschedule(
            nuevo, fields.Datetime.add(nuevo, hours=1))
        self.assertTrue(ok, motivo)
        self.assertEqual(tarea.state, '1_canceled',
                         "solo la incidencia devuelve la tarea a Programado")

    # --- La politica, en un solo sitio ---------------------------------

    def test_la_politica_dice_las_horas_configuradas(self):
        """Estaba escrita a mano ("24 horas") en la confirmacion por WhatsApp, asi
        que cambiar el ajuste dejaba la frase mintiendole al cliente."""
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('visar.reschedule.min_hours', '48')
        try:
            texto = self.Event._visar_reschedule_policy_text()
            self.assertIn('48', texto)
            self.assertNotIn('24 horas', texto)
            self.assertIn('no son cancelables', texto)
        finally:
            Param.set_param('visar.reschedule.min_hours', '24')
