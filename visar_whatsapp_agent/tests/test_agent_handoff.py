from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentHandoff(TransactionCase):
    """`agent_request_handoff`: escalar a un humano deja rastro accionable.

    Antes el agente decia "en seguida te contacta un asesor" y no pasaba nada
    mas: nada en Odoo, nadie enterado. Es la perdida de contexto que el proyecto
    existe para eliminar, reproducida dentro del sistema nuevo y con una promesa
    explicita al cliente encima.

    Lo que se fija aqui:
      * aterriza en el MISMO lead que `agent_track_lead` (un cliente, una ficha);
      * la nota lleva el contexto ya recogido, para que el asesor no vuelva a
        preguntar lo mismo;
      * se agenda una actividad, para que caiga en la bandeja de alguien.
    """

    RAW = '9990007788'
    WA = '5219990007788'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Lead = cls.env['crm.lead']
        cls.team = cls.env.ref('visar_crm.crm_team_whatsapp')
        cls.stage_nuevo = cls.env.ref('visar_crm.crm_stage_wa_nuevo')
        cls.group = cls.env['visar.service.group'].create({
            'name': 'Test Handoff Fumigacion', 'code': 'TSTH_FUM'})
        cls.dimension = cls.env['visar.service.dimension'].create({
            'name': 'Interior', 'code': 'TSTH_INT', 'group_id': cls.group.id})

    def _leads(self, group=None):
        return self.Lead.search([
            ('visar_wa_phone_norm', '=', self.RAW),
            ('team_id', '=', self.team.id),
            ('visar_service_group_id', '=', group.id if group else False),
        ])

    def test_crea_lead_con_nota_y_actividad(self):
        result = self.Tools.agent_request_handoff({
            'phone': self.WA,
            'reason': 'no_slot_fits',
            'summary': 'Ninguna fecha le acomoda, viaja toda la semana',
            'context': {'servicio': 'Fumigacion interior', 'cp': '64000',
                        'm2': 120},
        })
        self.assertTrue(result['lead_id'])
        self.assertTrue(result['created'])
        self.assertIsNone(result['skipped_reason'])

        lead = self.Lead.browse(result['lead_id'])
        self.assertEqual(lead.stage_id, self.stage_nuevo)

        body = "".join(lead.message_ids.mapped('body'))
        self.assertIn('escalo', body)
        # El contexto ya recogido tiene que viajar: es la diferencia entre que el
        # asesor retome la conversacion y que la empiece de cero.
        self.assertIn('64000', body)
        self.assertIn('120', body)
        self.assertIn('viaja toda la semana', body)

    def test_reusa_el_lead_de_una_cotizacion_previa(self):
        # Primero el agente cotiza (crea lead), luego escala: debe ser EL MISMO
        # lead, o el asesor acabaria con dos fichas y medio contexto en cada una.
        tracked = self.Tools.agent_track_lead({
            'phone': self.WA,
            'service_code': self.dimension.code,
            'quote': {'cp': '64000', 'm2': 120, 'total': 690, 'currency': 'MXN'},
        })
        self.assertTrue(tracked['lead_id'])

        result = self.Tools.agent_request_handoff({
            'phone': self.WA,
            'reason': 'customer_request',
            'service_code': self.dimension.code,
        })
        self.assertEqual(result['lead_id'], tracked['lead_id'])
        self.assertFalse(result['created'])
        self.assertEqual(len(self._leads(self.group)), 1)

    def test_lead_nuevo_registra_el_origen_escalado(self):
        """REGRESION T3h: `agent_request_handoff` lanzaba con lead NUEVO.

        `_agent_open_lead` escribia `visar_source='whatsapp_handoff'` y la
        Selection de `crm.lead` solo aceptaba 'whatsapp': ValueError hasta el
        runtime. Solo sobrevivia el caso que REUSA un lead existente, que es
        justo el que las pruebas cubrian — por eso paso desapercibido.
        """
        result = self.Tools.agent_request_handoff({
            'phone': self.WA, 'reason': 'payment_failed'})
        self.assertTrue(result['created'], "este caso tiene que crear el lead")
        lead = self.Lead.browse(result['lead_id'])
        self.assertEqual(lead.visar_source, 'whatsapp_handoff')

    def test_sin_servicio_tambien_escala(self):
        # Al escalar no siempre se sabe todavia que servicio queria el cliente.
        result = self.Tools.agent_request_handoff({
            'phone': self.WA, 'reason': 'not_understood'})
        self.assertTrue(result['lead_id'])
        self.assertIsNone(result['skipped_reason'])

    def test_la_actividad_no_se_asigna_al_propio_bot(self):
        """El hand-off tiene que convocar a un HUMANO.

        CRM auto-asigna el lead a quien lo crea, y aqui quien lo crea es el
        usuario RPC del agente. Sin filtro, la actividad quedaba a nombre de
        "Agente WhatsApp (RPC)": rastro perfecto que no llega a la bandeja de
        nadie — exactamente lo que este metodo existe para evitar.
        """
        result = self.Tools.agent_request_handoff({
            'phone': self.WA, 'reason': 'complaint'})
        lead = self.Lead.browse(result['lead_id'])
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'crm.lead'), ('res_id', '=', lead.id)])
        for activity in activities:
            self.assertNotEqual(
                activity.user_id, self.env.user,
                "la actividad del hand-off no puede quedar asignada al propio "
                "agente que la creo")
            self.assertFalse(activity.user_id.share)

    def test_telefono_invalido_no_revienta(self):
        result = self.Tools.agent_request_handoff({'phone': '123'})
        self.assertIsNone(result['lead_id'])
        self.assertEqual(result['skipped_reason'], 'invalid_phone')

    def test_motivo_desconocido_cae_en_otro(self):
        # El runtime elige de un catalogo cerrado; si manda basura, no se pierde
        # el hand-off (que es lo importante), solo se etiqueta como 'otro'.
        result = self.Tools.agent_request_handoff({
            'phone': self.WA, 'reason': 'motivo_inventado'})
        self.assertTrue(result['lead_id'])
        lead = self.Lead.browse(result['lead_id'])
        self.assertIn('Otro motivo', "".join(lead.message_ids.mapped('body')))
