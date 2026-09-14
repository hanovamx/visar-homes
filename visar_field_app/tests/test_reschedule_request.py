# -*- coding: utf-8 -*-
"""Pedirle al cliente que elija otro horario: quien lo dispara y quien no.

El aviso al cliente cuelga de una **accion explicita** y nunca del valor de la
etapa. La distincion no es teorica: la etapa "Incidencia — Reprogramar" se usa
tambien para reflejar el estado en el tablero, y alguien reorganizando tarjetas en
el Kanban no debe mandarle un WhatsApp a un cliente que no lo pidio. De ahi que la
prueba central de este fichero sea una **negativa**.

Lo otro que se prueba es que los dos origenes —el boton del tecnico en la app de
campo y el del coordinador en el backend— pasan por el MISMO metodo. Dos copias de
esta transicion divergen en cuanto alguien toca una.
"""
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestSolicitudDeReagenda(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Outbox = cls.env['visar.wa.message']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Incidencia', 'phone': '8112345678',
            'street': 'Calle Falsa 123', 'zip': '64000', 'city': 'Monterrey'})
        cls.employee = cls.env['hr.employee'].create({'name': 'Tecnico Incidencia'})
        # El proyecto FSM no es decoracion: una tarea SIN proyecto es privada, y
        # en una tarea privada `stage_id` es la etapa PERSONAL del usuario, asi
        # que escribirla revienta con "You can only set a personal stage on a
        # private task". Sin proyecto, esta prueba probaria otra cosa.
        cls.project = cls.env['project.project'].create({
            'name': 'FSM Incidencia Test', 'is_fsm': True,
            'company_id': cls.env.company.id})

    def _tarea_con_cita(self, con_cita=True, partner_pedido=None):
        """Una tarea de campo, con (o sin) la cita pagada detras.

        `partner_pedido` separa el contacto de SERVICIO (el de la tarea, a quien
        se le escribe) del cliente del PEDIDO (contra quien el reagendado
        comprueba la pertenencia). En produccion son distintos en 79 de 80 casos.
        """
        inicio = fields.Datetime.add(fields.Datetime.now(), hours=-2)
        evento = self.env['calendar.event']
        if con_cita:
            evento = self.env['calendar.event'].create({
                'name': 'Cita con incidencia',
                'start': inicio,
                'stop': fields.Datetime.add(inicio, hours=1),
            })
        producto = self.env['product.product'].create({
            'name': 'Servicio incidencia', 'type': 'service',
            'visar_is_service': True})
        tarea = self.env['project.task'].create({
            'name': 'Servicio con incidencia',
            'project_id': self.project.id,
            'partner_id': self.partner.id,
            'planned_date_begin': inicio,
        })
        pedido = self.env['sale.order'].create({
            'partner_id': (partner_pedido or self.partner).id,
            'order_line': [(0, 0, {
                'product_id': producto.id,
                'calendar_event_id': evento.id if evento else False,
                'task_id': tarea.id})],
        })
        pedido.write({'state': 'sale'})
        return tarea, evento

    def _avisos(self, tarea, clave=None):
        dominio = [('task_id', '=', tarea.id)]
        if clave:
            dominio.append(('template_key', '=', clave))
        return self.Outbox.sudo().search(dominio)

    # --- La negativa: el Kanban no manda nada --------------------------

    def test_mover_la_tarjeta_en_el_kanban_NO_avisa_al_cliente(self):
        """Criterio de aceptacion, como prueba de regresion.

        Si algun dia alguien cuelga el envio de un `base.automation` sobre
        `stage_id`, esta prueba es la que lo caza. Reorganizar el tablero no puede
        escribirle a un cliente.
        """
        tarea, evento = self._tarea_con_cita()
        etapa = self.env.ref('industry_fsm.planning_project_stage_4',
                             raise_if_not_found=False)
        if not etapa:
            self.skipTest("la BD no tiene las etapas de Field Service")

        tarea.write({'stage_id': etapa.id, 'state': '1_canceled'})

        self.assertFalse(self._avisos(tarea), "ningun aviso encolado")
        self.assertFalse(tarea.visar_reschedule_requested_at,
                         "y no cuenta como solicitud de reagenda")
        self.assertFalse(evento.visar_reschedule_granted_at,
                         "ni autoriza al cliente a mover nada")

    # --- Los dos botones, una sola logica ------------------------------

    def test_el_boton_del_backend_reusa_la_misma_transicion(self):
        """No es una copia: llama a `_visar_flag_reschedule`, igual que la app."""
        tarea, evento = self._tarea_con_cita()

        tarea.visar_action_request_reschedule()

        self.assertTrue(tarea.visar_reschedule_requested_at)
        self.assertEqual(tarea.state, '1_canceled')
        etapa = self.env.ref('industry_fsm.planning_project_stage_4',
                             raise_if_not_found=False)
        if etapa:
            self.assertEqual(tarea.stage_id, etapa)
        self.assertTrue(evento.visar_reschedule_granted_at,
                        "y autoriza al cliente a elegir horario")

    def test_pedir_la_reagenda_encola_la_INVITACION_no_el_aviso_pasivo(self):
        """El cliente tiene que poder contestar, no solo enterarse."""
        tarea, _evento = self._tarea_con_cita()
        tarea._visar_flag_reschedule(self.employee)

        avisos = self._avisos(tarea)
        self.assertEqual(len(avisos), 1)
        self.assertEqual(avisos.template_key, 'reschedule_offer')
        # Y va por la ruta que toca la conversacion, no por la de texto suelto.
        self.assertEqual(
            avisos._visar_wa_endpoint(avisos.template_key),
            '/internal/booking-event')
        # El runtime necesita saber QUE cita se invita a mover.
        self.assertEqual(avisos._visar_wa_context().get('event_id'),
                         tarea._visar_calendar_event().id)

    def test_el_aviso_lleva_la_politica_de_no_devolucion(self):
        tarea, _evento = self._tarea_con_cita()
        tarea._visar_flag_reschedule(self.employee)
        aviso = self._avisos(tarea)
        texto = aviso.fallback_text
        self.assertIn('no son cancelables', texto)
        self.assertIn('pueden ser reprogramadas', texto)
        self.assertIn('24', texto, "con las horas que de verdad estan puestas")
        # Contrato con la plantilla de Meta: {{1}}=tecnico, {{2}}=HORAS. Si alguien
        # vuelve a mandar la frase entera, Meta rechaza el envio.
        import json
        params = json.loads(aviso.params_json)
        self.assertEqual(len(params), 2)
        self.assertEqual(params[1], '24')

    # --- Sin cita no se promete un boton que no existe -----------------

    def test_sin_cita_ligada_sale_el_aviso_pasivo(self):
        """Una tarea que nunca nacio de una reserva no tiene horarios que
        ofrecer. Prometerlos seria peor que decir "te contactamos"."""
        tarea, _evento = self._tarea_con_cita(con_cita=False)
        tarea._visar_flag_reschedule(self.employee)

        avisos = self._avisos(tarea)
        self.assertEqual(len(avisos), 1)
        self.assertEqual(avisos.template_key, 'reschedule')

    # --- Doble pulsacion ------------------------------------------------

    def test_el_tecnico_pulsando_dos_veces_no_avisa_dos_veces(self):
        tarea, _evento = self._tarea_con_cita()
        tarea._visar_flag_reschedule(self.employee)
        tarea._visar_flag_reschedule(self.employee)
        self.assertEqual(len(self._avisos(tarea)), 1)

    def test_el_coordinador_SI_puede_reenviar_a_proposito(self):
        """El guardia protege al tecnico que toca dos veces la pantalla; un
        coordinador que pulsa "solicitar reagenda" espera que el mensaje salga."""
        tarea, _evento = self._tarea_con_cita()
        tarea._visar_flag_reschedule(self.employee)
        tarea.visar_action_request_reschedule()
        self.assertEqual(len(self._avisos(tarea)), 2)

    # --- El puente tarea -> cita ---------------------------------------

    def test_la_tarea_encuentra_su_cita(self):
        """No existe `project.task.visar_event_id`: el puente es por la linea del
        pedido, y si alguien lo cambia esta prueba lo dice."""
        tarea, evento = self._tarea_con_cita()
        self.assertEqual(tarea._visar_calendar_event(), evento)

    def test_una_tarea_sin_pedido_no_revienta(self):
        suelta = self.env['project.task'].create(
            {'name': 'Tarea suelta', 'project_id': self.project.id})
        self.assertFalse(suelta._visar_calendar_event())

    # --- Atribucion: quien PIDE no es quien acudio ----------------------

    def test_desde_el_backend_no_se_atribuye_al_tecnico(self):
        """El tecnico es quien ACUDIO; quien pide la reagenda es el coordinador.

        Confundirlos hace mentir a la ficha: "Reagenda solicitada por <tecnico>"
        cuando el tecnico no la pidio.
        """
        tarea, _evento = self._tarea_con_cita()
        tarea.visar_technician_ids = [(6, 0, self.employee.ids)]

        tarea.visar_action_request_reschedule()

        self.assertNotEqual(tarea.visar_reschedule_requested_by_id, self.employee)

    def test_el_mensaje_nombra_al_tecnico_que_acudio(self):
        """Aunque lo pida el coordinador: al cliente se le habla del tecnico."""
        tarea, _evento = self._tarea_con_cita()
        tarea.visar_technician_ids = [(6, 0, self.employee.ids)]

        tarea.visar_action_request_reschedule()

        self.assertIn(self.employee.name, self._avisos(tarea).fallback_text)

    # --- Un SEGUNDO no-show de la misma tarea ---------------------------

    def test_un_segundo_no_show_vuelve_a_avisar(self):
        """El fallo mas silencioso posible: el tecnico pulsa y no pasa nada.

        El guardia de doble pulsacion se mira en `visar_reschedule_requested_at`.
        Si al volver la tarea a "Programado" no se limpiara, un segundo no-show de
        la misma tarea no le mandaria nada al cliente.
        """
        tarea, evento = self._tarea_con_cita()
        tarea._visar_flag_reschedule(self.employee)
        self.assertEqual(len(self._avisos(tarea)), 1)

        # El cliente eligio horario: la tarea vuelve a su sitio.
        tarea._visar_back_to_scheduled()
        self.assertFalse(tarea.visar_reschedule_requested_at,
                         "la solicitud quedo atendida")

        # Y el dia de la cita nueva tampoco atiende.
        tarea._visar_flag_reschedule(self.employee)
        self.assertEqual(len(self._avisos(tarea)), 2,
                         "el segundo no-show tambien avisa")


    # --- Pertenencia: a quien se le escribe vs de quien es la cita -------

    def test_no_se_ofrece_autoservicio_a_un_numero_que_la_reagenda_no_reconoce(self):
        """El fallo que solo aparece con datos reales.

        Se le escribe al contacto de SERVICIO (`task.partner_id`), pero el
        reagendado comprueba la pertenencia contra el cliente del PEDIDO. Cuando
        no coinciden y el de servicio tiene telefono propio, el cliente recibia
        "¿elegimos otro horario?" y al contestar "si" se llevaba un *"no encontre
        esa cita a tu nombre"*.

        Prometer y luego negar es peor que no prometer, y a este cliente ya le
        fallamos hoy una vez.
        """
        otro_cliente = self.env['res.partner'].create({
            'name': 'Cliente del pedido', 'phone': '8129998888'})
        tarea, evento = self._tarea_con_cita(partner_pedido=otro_cliente)
        # El contacto de servicio tiene telefono PROPIO: es el caso que rompe.
        self.assertTrue(self.partner.phone)
        self.assertEqual(tarea._visar_client_partner(), self.partner)
        self.assertNotIn(self.partner, evento._visar_appointment_partners())

        tarea._visar_flag_reschedule(self.employee)

        avisos = self._avisos(tarea)
        self.assertEqual(avisos.template_key, 'reschedule',
                         "se cae al aviso pasivo en vez de prometer un boton")
        self.assertFalse(evento.visar_reschedule_granted_at,
                         "y no se autoriza una reagenda que nadie podria usar")

    def test_cuando_coinciden_si_se_ofrece_autoservicio(self):
        """La linea base del caso anterior: el camino normal no se estropea."""
        tarea, evento = self._tarea_con_cita()
        self.assertIn(tarea._visar_client_partner(),
                      evento._visar_appointment_partners())

        tarea._visar_flag_reschedule(self.employee)

        self.assertEqual(self._avisos(tarea).template_key, 'reschedule_offer')
        self.assertTrue(evento.visar_reschedule_granted_at)

    def test_la_regla_de_pertenencia_es_la_MISMA_que_usa_la_reagenda(self):
        """Si alguien cambia una de las dos, esta prueba lo dice.

        La invitacion y el flujo tienen que estar de acuerdo sobre de quien es la
        cita, o se vuelve a prometer lo que luego se niega.
        """
        otro_cliente = self.env['res.partner'].create({
            'name': 'Cliente del pedido 2', 'phone': '8129997777'})
        _tarea, evento = self._tarea_con_cita(partner_pedido=otro_cliente)
        # `visar_field_app` NO depende de `visar_whatsapp_agent`: si el agente no
        # esta instalado no hay contrato que comprobar, y no es un fallo.
        if 'visar.agent.tools' not in self.env:
            self.skipTest("el agente de WhatsApp no esta instalado")
        Tools = self.env['visar.agent.tools']

        # El dueno de verdad entra.
        _ev, error = Tools._agent_reschedule_event(
            {'phone': '528129997777', 'event_id': evento.id})
        self.assertIsNone(error)
        # El contacto de servicio, no. Y es exactamente lo que mira el guardia.
        _ev2, error2 = Tools._agent_reschedule_event(
            {'phone': '52' + self.partner.phone, 'event_id': evento.id})
        self.assertEqual(error2, 'not_found')
