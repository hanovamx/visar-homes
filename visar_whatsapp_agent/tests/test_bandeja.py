from unittest.mock import patch

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
