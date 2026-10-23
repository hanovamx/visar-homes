from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_base.models.wa_outbox_mixin import TOKEN_PARAM
from odoo.addons.visar_whatsapp_agent.models import visar_whatsapp_numero as vwn


@tagged('post_install', '-at_install')
class TestWhatsappNumero(TransactionCase):
    """El número del agente sale de la cuenta de WhatsApp de Odoo (23-oct-2026)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.cuenta = cls.Tools._visar_bandeja_cuenta()
        if not cls.cuenta:
            cls.cuenta = cls.env['whatsapp.account'].sudo().create({
                'name': "Prueba", 'app_uid': 'a', 'app_secret': 's',
                'account_uid': 'c', 'phone_uid': 'p', 'token': 't',
                'notify_user_ids': [(6, 0, cls.env.ref('base.user_admin').ids)],
            })
        cls.runtime = cls.env.ref(vwn.USUARIO_RUNTIME)
        cls.env['ir.config_parameter'].sudo().set_param(TOKEN_PARAM, 'secreto')

    def test_el_runtime_lee_la_cuenta(self):
        cred = self.Tools.with_user(self.runtime).agent_whatsapp_credentials()
        self.assertEqual(cred['phone_id'], self.cuenta.phone_uid)
        self.assertEqual(cred['token'], self.cuenta.sudo().token)
        self.assertEqual(cred['app_id'], self.cuenta.app_uid)
        self.assertEqual(cred['app_secret'], self.cuenta.sudo().app_secret)
        self.assertEqual(set(cred), {'account', 'phone_id', 'token', 'app_id',
                                     'app_secret', 'verify_token'})

    def test_nadie_mas_lee_las_llaves(self):
        with self.assertRaises(AccessError):
            self.Tools.with_user(self.env.ref('base.user_admin')).agent_whatsapp_credentials()

    def test_las_llaves_no_viajan_en_la_config(self):
        config = repr(self.Tools.agent_runtime_config())
        self.assertNotIn(self.cuenta.sudo().token, config)

    def test_boton_avisa_al_runtime(self):
        respuesta = MagicMock(status_code=200)
        respuesta.json.return_value = {'changed': ['número']}
        with patch.object(vwn.requests, 'post', return_value=respuesta) as post:
            accion = self.cuenta.action_visar_aplicar_en_agente()
        self.assertTrue(post.call_args.args[0].endswith('/internal/whatsapp-reload'))
        self.assertEqual(post.call_args.kwargs['headers'], {'X-Visar-Token': 'secreto'})
        self.assertIn('número', accion['params']['message'])

    def test_boton_con_error_del_runtime(self):
        respuesta = MagicMock(status_code=409)
        respuesta.json.return_value = {'detail': "Falta el token"}
        with patch.object(vwn.requests, 'post', return_value=respuesta), \
                self.assertRaises(UserError):
            self.cuenta.action_visar_aplicar_en_agente()
