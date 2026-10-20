from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_whatsapp_agent.models import visar_handoff


@tagged('post_install', '-at_install')
class TestHandoffSilencioso(TransactionCase):
    """Escalamiento silencioso (20-oct-2026): ver `models/visar_handoff.py`.

    Lo que se fija aqui:
      * los motivos del runtime caen en la lista de Odoo (antes, "Otro motivo");
      * la actividad va a la persona de Ajustes → Visar y lleva la conversación;
      * el contacto dice quién lo atiende, y el botón lo devuelve al agente;
      * el runtime recibe la config, y nace apagada.
    """

    RAW = '9990007799'
    WA = '5219990007799'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Contacto = cls.env['visar.agent.contacto']
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.laura = cls.env['res.users'].create({
            'name': 'Laura Escalamientos', 'login': 'laura.handoff.test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def _lead(self, result):
        return self.env['crm.lead'].browse(result['lead_id'])

    def _contacto(self):
        return self.Contacto.with_context(active_test=False).search(
            [('name', '=', self.RAW)], limit=1)

    # --- config ----------------------------------------------------------

    def test_nace_apagado(self):
        config = self.Tools.agent_runtime_config()['handoff']
        self.assertFalse(config['silencioso'])
        self.assertEqual(config['dias'], [0, 1, 2, 3, 4, 5])
        self.assertEqual((config['desde'], config['hasta']), (9, 19))
        self.assertEqual((config['espera_min'], config['devolver_h']), (15, 12))
        self.assertTrue(config['persona_primero'])
        self.assertEqual(config['texto_espera'], "Permíteme un momento.")

    def test_los_ajustes_llegan_al_runtime(self):
        settings = self.env['res.config.settings'].create({
            'visar_handoff_silencioso': True,
            'visar_handoff_user_id': self.laura.id,
            'visar_handoff_dias': 'lv',
            'visar_handoff_hora_desde': 8,
            'visar_handoff_hora_hasta': 18,
            'visar_handoff_persona_no_aparta': True,
            'visar_handoff_texto_espera': "Un momento, por favor.",
        })
        settings.execute()
        config = self.Tools.agent_runtime_config()['handoff']
        self.assertTrue(config['silencioso'])
        self.assertEqual(config['dias'], [0, 1, 2, 3, 4])
        self.assertEqual((config['desde'], config['hasta']), (8, 18))
        self.assertFalse(config['persona_primero'])
        self.assertEqual(config['texto_espera'], "Un momento, por favor.")
        self.assertEqual(visar_handoff.visar_handoff_user(self.env), self.laura)

    def test_horario_al_reves_no_se_guarda(self):
        with self.assertRaises(ValidationError):
            self.env['res.config.settings'].create({
                'visar_handoff_hora_desde': 19, 'visar_handoff_hora_hasta': 9})

    def test_texto_fuera_solo_acepta_cuando(self):
        with self.assertRaises(ValidationError):
            self.env['res.config.settings'].create({
                'visar_handoff_texto_fuera': "Te escribimos {dia}"})

    # --- motivos ---------------------------------------------------------

    def test_los_motivos_del_runtime_se_traducen(self):
        casos = {
            'asesor': "El cliente pidio hablar con un asesor",
            'no_se_entiende': "El agente no logro entender al cliente",
            'reschedule_blocked': "No se pudo reagendar desde el agente",
            'reschedule_sin_cita': "Quiere reagendar y no se encontro su cita",
        }
        for crudo, texto in casos.items():
            with self.subTest(crudo=crudo):
                result = self.Tools.agent_request_handoff(
                    {'phone': self.WA, 'reason': crudo})
                body = "".join(self._lead(result).message_ids.mapped('body'))
                self.assertIn(texto, body)

    def test_un_motivo_libre_se_guarda_en_la_nota(self):
        result = self.Tools.agent_request_handoff(
            {'phone': self.WA, 'reason': 'necesita su factura de septiembre'})
        body = "".join(self._lead(result).message_ids.mapped('body'))
        self.assertIn("Otro motivo", body)
        self.assertIn("necesita su factura de septiembre", body)

    # --- actividad -------------------------------------------------------

    def test_la_actividad_va_a_la_persona_a_cargo_con_la_conversacion(self):
        self.params.set_param(visar_handoff.USER_PARAM, str(self.laura.id))
        result = self.Tools.agent_request_handoff({
            'phone': self.WA, 'reason': 'asesor', 'silencioso': True,
            'transcript': [
                {'quien': 'cliente', 'texto': 'Quiero hablar con alguien',
                 'hora': 1760900000},
                {'quien': 'agente', 'texto': 'Permíteme un momento.',
                 'hora': 1760900005},
            ],
        })
        lead = self._lead(result)
        activity = self.env['mail.activity'].search([
            ('res_model', '=', 'crm.lead'), ('res_id', '=', lead.id)], limit=1)
        self.assertEqual(activity.user_id, self.laura)
        self.assertIn("el cliente espera", activity.summary)
        self.assertIn("Quiero hablar con alguien", activity.note)
        self.assertIn("WhatsApp Business", activity.note)
        body = "".join(lead.message_ids.mapped('body'))
        self.assertIn("La conversación", body)
        self.assertIn("Permíteme un momento.", body)

    def test_un_runtime_viejo_sin_conversacion_no_revienta(self):
        result = self.Tools.agent_request_handoff(
            {'phone': self.WA, 'reason': 'asesor', 'transcript': 'basura'})
        self.assertTrue(result['lead_id'])

    def test_una_persona_archivada_no_recibe_nada(self):
        self.laura.active = False
        self.params.set_param(visar_handoff.USER_PARAM, str(self.laura.id))
        self.assertFalse(visar_handoff.visar_handoff_user(self.env))

    # --- quién atiende ---------------------------------------------------

    def test_silencioso_deja_al_contacto_esperando(self):
        self.Tools.agent_request_handoff(
            {'phone': self.WA, 'reason': 'no_slot_fits', 'silencioso': True})
        contacto = self._contacto()
        self.assertEqual(contacto.visar_atencion, 'escalado')
        self.assertTrue(contacto.visar_atencion_desde)
        self.assertIn("fecha", contacto.visar_atencion_motivo)

    def test_sin_silencioso_el_contacto_no_cambia(self):
        self.Tools.agent_request_handoff({'phone': self.WA, 'reason': 'asesor'})
        self.assertFalse(self._contacto().visar_atencion)

    def test_el_runtime_avisa_que_contesto_una_persona_y_que_volvio(self):
        self.Tools.agent_request_handoff(
            {'phone': self.WA, 'reason': 'asesor', 'silencioso': True})
        desde = self._contacto().visar_atencion_desde
        self.assertTrue(self.Tools.agent_handoff_state(
            {'phone': self.WA, 'estado': 'persona', 'motivo': 'asesor'})['ok'])
        self.assertEqual(self._contacto().visar_atencion, 'persona')
        self.assertEqual(self._contacto().visar_atencion_desde, desde,
                         "el 'desde' es del escalamiento, no del primer mensaje")
        self.Tools.agent_handoff_state({'phone': self.WA, 'estado': 'devuelto'})
        self.assertFalse(self._contacto().visar_atencion)

    def test_la_persona_escribe_primero_y_el_contacto_se_crea(self):
        self.Tools.agent_handoff_state(
            {'phone': self.WA, 'estado': 'persona', 'motivo': 'persona'})
        contacto = self._contacto()
        self.assertEqual(contacto.visar_atencion, 'persona')
        self.assertEqual(contacto.visar_atencion_motivo,
                         "Una persona escribio primero desde la app")

    def test_un_estado_raro_no_hace_nada(self):
        self.assertFalse(self.Tools.agent_handoff_state(
            {'phone': self.WA, 'estado': 'quien_sabe'})['ok'])

    # --- botón «Devolver al agente» ---------------------------------------

    def _esperando(self):
        self.params.set_param('visar_field.agent_token', 'secreto')
        self.Tools.agent_request_handoff(
            {'phone': self.WA, 'reason': 'asesor', 'silencioso': True})
        return self._contacto()

    def test_devolver_al_agente_llama_al_runtime(self):
        contacto = self._esperando()
        respuesta = type('R', (), {'status_code': 200})()
        with patch.object(visar_handoff.requests, 'post',
                          return_value=respuesta) as post:
            contacto.action_visar_devolver_al_agente()
        url = post.call_args.args[0]
        self.assertTrue(url.endswith('/internal/handoff-release'))
        self.assertEqual(post.call_args.kwargs['headers'],
                         {'X-Visar-Token': 'secreto'})
        self.assertFalse(contacto.visar_atencion)

    def test_si_el_runtime_falla_se_dice_y_no_se_suelta(self):
        contacto = self._esperando()
        respuesta = type('R', (), {'status_code': 503})()
        with patch.object(visar_handoff.requests, 'post', return_value=respuesta):
            with self.assertRaises(UserError):
                contacto.action_visar_devolver_al_agente()
        self.assertEqual(contacto.visar_atencion, 'escalado')
