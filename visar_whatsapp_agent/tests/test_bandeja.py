from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_base.models.wa_outbox_mixin import TOKEN_PARAM
from odoo.addons.visar_whatsapp_agent.models import visar_bandeja as vb

ENVIAR = 'odoo.addons.whatsapp.tools.whatsapp_api.WhatsAppApi._send_whatsapp'


@tagged('post_install', '-at_install')
class TestBandeja(TransactionCase):
    """Bandeja de WhatsApp en Odoo (23-oct-2026): ver `models/visar_bandeja.py`."""

    WA = '5219990007712'
    NUMERO = '529990007712'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_param(vb.BANDEJA_PARAM, 'True')
        cls.params.set_param(TOKEN_PARAM, 'secreto-de-prueba')
        cls.cuenta = cls.Tools._visar_bandeja_cuenta()
        if not cls.cuenta:
            cls.cuenta = cls.env['whatsapp.account'].sudo().create({
                'name': "Prueba", 'app_uid': 'a', 'app_secret': 's',
                'account_uid': 'c', 'phone_uid': 'p', 'token': 't',
                'notify_user_ids': [(6, 0, cls.env.ref('base.user_admin').ids)],
            })

    def _lote(self, *lineas):
        return {'mensajes': [
            {'uid': uid, 'phone': self.WA, 'quien': quien, 'texto': texto,
             'hora': 1760900000 + n}
            for n, (uid, quien, texto) in enumerate(lineas)]}

    def _canal(self):
        return self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'whatsapp'), ('whatsapp_number', '=', self.NUMERO)])

    def _wa(self, canal):
        return self.env['whatsapp.message'].sudo().search(
            [('mail_message_id', 'in', canal.message_ids.ids)], order='id')

    def test_numero_whatsapp(self):
        self.assertEqual(vb._numero_whatsapp('5218112345678'), '528112345678')
        self.assertEqual(vb._numero_whatsapp('+52 81 1234 5678'), '528112345678')
        self.assertEqual(vb._numero_whatsapp(None), '')

    def test_copia_los_tres_lados_sin_mandar_nada(self):
        with patch(ENVIAR, side_effect=AssertionError("no debe mandar")) as enviar:
            result = self.Tools.agent_log_messages(self._lote(
                ('b1', 'cliente', 'Hola, ¿cuánto cuesta fumigar?'),
                ('b2', 'agente', 'Te ayudo. ¿Cuántos m² tiene tu casa?'),
                ('b3', 'persona', 'Hola, soy Laura de Visar'),
            ))
        enviar.assert_not_called()
        self.assertEqual(result['bandeja'], 3)
        canal = self._canal()
        self.assertEqual(len(canal), 1)
        wa = self._wa(canal)
        self.assertEqual(wa.mapped('message_type'), ['inbound', 'outbound', 'outbound'])
        self.assertEqual(wa.mapped('state'), ['received', 'sent', 'sent'])
        self.assertTrue(all(u.startswith(vb.UID_COPIA) for u in wa.mapped('msg_uid')))
        self.assertEqual(wa[0].mail_message_id.author_id, canal.whatsapp_partner_id)
        self.assertEqual(wa[1].mail_message_id.author_id, self.env.user.partner_id)
        self.assertEqual(wa[2].mail_message_id.author_id, self.env.company.partner_id)
        # La ventana de 24 h sale del entrante copiado.
        self.assertEqual(canal.last_wa_mail_message_id, wa[0].mail_message_id)
        self.assertTrue(canal.whatsapp_channel_active)
        # Ni una copia queda para el cron de envío.
        self.assertFalse(self.env['whatsapp.message'].sudo().search_count(
            [('state', '=', 'outgoing'), ('id', 'in', wa.ids)]))

    def test_un_lote_repetido_no_duplica(self):
        lote = self._lote(('c1', 'cliente', 'Hola'), ('c2', 'agente', '¡Hola!'))
        with patch(ENVIAR, side_effect=AssertionError("no debe mandar")):
            self.Tools.agent_log_messages(lote)
            result = self.Tools.agent_log_messages(lote)
        self.assertEqual(result['bandeja'], 0)
        self.assertEqual(len(self._wa(self._canal())), 2)

    def test_apagada_no_copia(self):
        self.params.set_param(vb.BANDEJA_PARAM, 'False')
        result = self.Tools.agent_log_messages(self._lote(('d1', 'cliente', 'Hola')))
        self.assertNotIn('bandeja', result)
        self.assertFalse(self._canal())

    def test_sin_guardar_conversaciones_no_copia(self):
        self.params.set_param('visar.conversaciones.guardar', 'False')
        self.Tools.agent_log_messages(self._lote(('e1', 'cliente', 'Hola')))
        self.assertFalse(self._canal())

    def test_lo_escrito_en_odoo_no_se_copia_otra_vez(self):
        result = self.Tools.agent_log_messages(self._lote(
            ('f1', 'cliente', 'Hola'),
            (vb.UID_DESDE_ODOO + '77', 'persona', 'Ya te atiendo'),
        ))
        self.assertEqual(result['bandeja'], 1)
        self.assertEqual(self.env['visar.agent.mensaje'].search_count(
            [('uid', '=', vb.UID_DESDE_ODOO + '77')]), 1,
            "la conversación sí lo guarda")

    def test_persona_contesta_desde_odoo_avisa_al_runtime(self):
        self.Tools.agent_log_messages(self._lote(('g1', 'cliente', 'Hola')))
        canal = self._canal()
        self.env.cr.postcommit.clear()
        with patch(ENVIAR, return_value='wamid.prueba') as enviar:
            canal.message_post(body="Hola, soy Laura", message_type='whatsapp_message',
                               subtype_xmlid='mail.mt_comment')
        enviar.assert_called_once()
        with patch.object(vb.requests, 'post') as post:
            self.env.cr.postcommit.run()
        post.assert_called_once()
        url = post.call_args.args[0]
        self.assertTrue(url.endswith('/internal/persona-odoo'))
        aviso = post.call_args.kwargs['json']
        self.assertEqual(aviso['phone'], self.NUMERO)
        self.assertEqual(aviso['texto'], "Hola, soy Laura")
        self.assertTrue(aviso['uid'].startswith(vb.UID_DESDE_ODOO))
        self.assertEqual(post.call_args.kwargs['headers'],
                         {'X-Visar-Token': 'secreto-de-prueba'})

    def test_un_envio_fallido_no_avisa(self):
        self.Tools.agent_log_messages(self._lote(('h1', 'cliente', 'Hola')))
        self.env.cr.postcommit.clear()
        with patch(ENVIAR, return_value=False):
            self._canal().message_post(body="Hola", message_type='whatsapp_message',
                                       subtype_xmlid='mail.mt_comment')
        with patch.object(vb.requests, 'post') as post:
            self.env.cr.postcommit.run()
        post.assert_not_called()


@tagged('post_install', '-at_install')
class TestBandejaTrabajo(TestBandeja):
    """La bandeja para trabajar (23-oct-2026): estados, Tomar/Devolver y el
    enlace en la actividad. Hereda el montaje de `TestBandeja`."""

    def _contacto(self):
        return self.env['visar.agent.contacto'].sudo()._visar_buscar_o_crear(self.WA)

    def _respuesta(self, codigo=200, cuerpo=None):
        from unittest.mock import MagicMock
        r = MagicMock(status_code=codigo)
        r.json.return_value = cuerpo or {}
        return r

    def test_estados_y_colores(self):
        from datetime import timedelta
        from odoo import fields
        contacto = self._contacto()
        contacto.ultimo_mensaje = fields.Datetime.now()
        self.assertEqual(contacto.visar_bandeja_estado, 'agente')
        contacto.visar_atencion = 'escalado'
        self.assertEqual(contacto.visar_bandeja_estado, 'escalado')
        self.assertEqual(contacto.visar_bandeja_orden, 0, "lo urgente arriba")
        contacto.visar_atencion = 'persona'
        self.assertEqual(contacto.visar_bandeja_estado, 'persona')
        contacto.write({'visar_atencion': False,
                        'ultimo_mensaje': fields.Datetime.now() - timedelta(hours=25)})
        self.assertEqual(contacto.visar_bandeja_estado, 'cerrada')

    def test_el_cron_cierra_las_que_vencieron(self):
        from datetime import timedelta
        from odoo import fields
        contacto = self._contacto()
        contacto.ultimo_mensaje = fields.Datetime.now() - timedelta(hours=23)
        self.assertEqual(contacto.visar_bandeja_estado, 'agente')
        # Pasa el tiempo sin que nadie escriba: el campo no cambia, el cron sí.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE visar_agent_contacto SET ultimo_mensaje = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=30), contacto.id))
        contacto.invalidate_recordset(['ultimo_mensaje'])
        contacto._visar_bandeja_cerrar_vencidas()
        contacto.invalidate_recordset()
        self.assertEqual(contacto.visar_bandeja_estado, 'cerrada')

    def test_la_copia_liga_el_chat_al_contacto(self):
        self.Tools.agent_log_messages(self._lote(('t1', 'cliente', 'Hola')))
        self.assertEqual(self._contacto().visar_canal_id, self._canal())

    def test_tomar_desde_el_chat(self):
        self.Tools.agent_log_messages(self._lote(('t2', 'cliente', 'Hola')))
        canal = self._canal()
        with patch.object(vb.requests, 'post', return_value=self._respuesta()) as post:
            accion = canal.action_visar_tomar()
        self.assertTrue(post.call_args.args[0].endswith('/internal/handoff-take'))
        self.assertEqual(accion['tag'], 'display_notification')
        contacto = self._contacto()
        self.assertEqual(contacto.visar_atencion, 'persona')
        self.assertEqual(contacto.visar_bandeja_estado, 'persona')
        self.assertIn(self.env.user.partner_id, canal.channel_member_ids.partner_id)
        nota = canal.message_ids.filtered(lambda m: 'tomó la conversación' in (m.body or ''))
        self.assertTrue(nota)
        self.assertFalse(nota.wa_message_ids, "la nota no se le manda al cliente")

    def test_tomar_con_el_silencioso_apagado_explica(self):
        self.Tools.agent_log_messages(self._lote(('t3', 'cliente', 'Hola')))
        r = self._respuesta(409, {'detail': "Para tomar una conversación hay que encender"})
        with patch.object(vb.requests, 'post', return_value=r), \
                self.assertRaisesRegex(UserError, "Para tomar"):
            self._canal().action_visar_tomar()
        self.assertFalse(self._contacto().visar_atencion)

    def test_devolver_desde_el_chat(self):
        self.Tools.agent_log_messages(self._lote(('t4', 'cliente', 'Hola')))
        canal = self._canal()
        self._contacto().write({'visar_atencion': 'persona'})
        with patch('requests.post', return_value=self._respuesta()):
            canal.action_visar_devolver()
        self.assertFalse(self._contacto().visar_atencion)
        self.assertTrue(canal.message_ids.filtered(
            lambda m: 'devolvió la conversación' in (m.body or '')))

    def test_abrir_chat_me_hace_miembro(self):
        self.Tools.agent_log_messages(self._lote(('t5', 'cliente', 'Hola')))
        accion = self._contacto().action_visar_abrir_chat()
        self.assertEqual(accion['tag'], 'mail.action_discuss')
        self.assertEqual(accion['context']['active_id'], self._canal().id)

    def test_la_actividad_enlaza_el_chat(self):
        nota = self.Tools._agent_handoff_activity_note(
            {'phone': self.WA, 'silencioso': True})
        canal = self._canal()
        self.assertTrue(canal, "el chat se crea al escalar")
        self.assertIn(f'active_id={canal.id}', nota)
        self.params.set_param(vb.BANDEJA_PARAM, 'False')
        nota = self.Tools._agent_handoff_activity_note(
            {'phone': self.WA, 'silencioso': True})
        self.assertIn('app WhatsApp Business', nota)
        self.assertNotIn('active_id', nota)


@tagged('post_install', '-at_install')
class TestBandejaContacto(TestBandeja):
    """El contacto que crea el módulo nativo para abrir el chat no puede volver
    «conocido» a un cliente nuevo (arreglo del 23-oct-2026)."""

    def test_numero_nuevo_sigue_siendo_nuevo(self):
        self.assertTrue(self.Tools._agent_booking_needs_name(self.WA))
        self.Tools.agent_log_messages(self._lote(('p1', 'cliente', 'Hola')))
        canal = self._canal()
        self.assertTrue(canal.whatsapp_partner_id)
        self.assertFalse(canal.whatsapp_partner_id.active, "provisional, archivado")
        self.assertTrue(self.Tools._agent_booking_needs_name(self.WA),
                        "el agente le sigue pidiendo el nombre")

    def test_el_chat_pasa_a_la_ficha_real(self):
        self.Tools.agent_log_messages(self._lote(('p2', 'cliente', 'Hola')))
        canal = self._canal()
        real = self.env['res.partner'].sudo().create(
            {'name': "Ana López Ruiz", 'phone': self.WA})
        self.Tools.agent_log_messages(self._lote(('p3', 'cliente', 'Ya les di mi nombre')))
        self.assertEqual(canal.whatsapp_partner_id, real)
        self.assertIn("Ana López Ruiz", canal.name)
        self.assertIn(real, canal.channel_member_ids.partner_id)

    def test_cliente_que_ya_existe_usa_su_ficha(self):
        real = self.env['res.partner'].sudo().create(
            {'name': "Beto Garza Treviño", 'phone': self.WA})
        self.Tools.agent_log_messages(self._lote(('p4', 'cliente', 'Hola')))
        canal = self._canal()
        self.assertEqual(canal.whatsapp_partner_id, real)
        self.assertTrue(real.active)

    def test_el_enlace_de_la_actividad_tampoco_crea_conocidos(self):
        self.Tools._agent_handoff_activity_note({'phone': self.WA, 'silencioso': True})
        self.assertTrue(self._canal())
        self.assertTrue(self.Tools._agent_booking_needs_name(self.WA))
