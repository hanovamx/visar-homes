from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_whatsapp_agent.models import visar_agent_mensaje as vam


@tagged('post_install', '-at_install')
class TestConversaciones(TransactionCase):
    """Conversaciones completas (20-oct-2026): ver `models/visar_agent_mensaje.py`."""

    RAW = '9990007711'
    WA = '5219990007711'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Mensaje = cls.env['visar.agent.mensaje']
        cls.params = cls.env['ir.config_parameter'].sudo()

    def _lote(self, *lineas):
        return {'mensajes': [
            {'uid': uid, 'phone': self.WA, 'quien': quien, 'texto': texto,
             'hora': 1760900000 + n}
            for n, (uid, quien, texto) in enumerate(lineas)]}

    def _mensajes(self):
        return self.Mensaje.search([('contacto_id.name', '=', self.RAW)])

    def test_encendido_por_omision(self):
        self.assertTrue(vam.visar_guardar_conversaciones(self.env))
        self.assertEqual(
            self.Tools.agent_runtime_config()['conversaciones'], {'guardar': True})

    def test_guarda_los_tres_lados_en_el_contacto(self):
        result = self.Tools.agent_log_messages(self._lote(
            ('a1', 'cliente', 'Hola, ¿cuánto cuesta fumigar?'),
            ('a2', 'agente', 'Te ayudo. ¿Cuántos m² tiene tu casa?'),
            ('a3', 'persona', 'Hola, soy Laura de Visar'),
        ))
        self.assertEqual(result['guardados'], 3)
        mensajes = self._mensajes()
        self.assertEqual(mensajes.mapped('quien'), ['cliente', 'agente', 'persona'])
        self.assertEqual(mensajes[0].contacto_id.mensajes, 0,
                         "guardar no suma al contador de mensajes recibidos")
        self.assertEqual(mensajes[0].fecha, fields.Datetime.to_datetime(
            '2025-10-19 18:53:20'))

    def test_un_lote_repetido_no_duplica(self):
        lote = self._lote(('b1', 'cliente', 'Hola'))
        self.Tools.agent_log_messages(lote)
        self.assertEqual(self.Tools.agent_log_messages(lote)['guardados'], 0)
        self.assertEqual(len(self._mensajes()), 1)

    def test_basura_no_revienta(self):
        result = self.Tools.agent_log_messages({'mensajes': [
            'texto suelto', {'uid': 'c1', 'phone': self.WA, 'quien': 'robot',
                             'texto': 'x', 'hora': 1},
            {'uid': 'c2', 'phone': self.WA, 'quien': 'cliente', 'texto': '  ',
             'hora': 1},
            {'uid': 'c3', 'phone': '123', 'quien': 'cliente', 'texto': 'hola',
             'hora': 1},
            {'uid': 'c4', 'phone': self.WA, 'quien': 'cliente', 'texto': 'ok',
             'hora': 'ayer'},
        ]})
        self.assertEqual(result['guardados'], 1, "solo el ultimo sirve")

    def test_apagado_no_guarda(self):
        settings = self.env['res.config.settings'].create(
            {'visar_conversaciones_guardar': False})
        settings.execute()
        self.assertEqual(self.params.get_param(vam.GUARDAR_PARAM), 'False')
        self.assertFalse(vam.visar_guardar_conversaciones(self.env))
        self.assertTrue(self.Tools.agent_log_messages(
            self._lote(('d1', 'cliente', 'Hola')))['apagado'])
        self.assertFalse(self._mensajes())
        self.assertFalse(self.env['res.config.settings'].create(
            {}).visar_conversaciones_guardar, "y la pantalla lo muestra apagado")

    def test_el_cron_borra_lo_vencido(self):
        self.Tools.agent_log_messages(self._lote(
            ('e1', 'cliente', 'viejo'), ('e2', 'cliente', 'nuevo')))
        viejo, nuevo = self._mensajes()
        viejo.fecha = fields.Datetime.now() - timedelta(days=400)
        nuevo.fecha = fields.Datetime.now() - timedelta(days=10)
        self.Mensaje._visar_cron_borrar_vencidos()
        self.assertEqual(self._mensajes(), nuevo)

    def test_al_menos_un_mes(self):
        with self.assertRaises(ValidationError):
            self.env['res.config.settings'].create({'visar_conversaciones_meses': 0})
