# -*- coding: utf-8 -*-
"""El menú del agente en cuatro secciones (pedido del usuario, 21-oct-2026)."""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

ARBOL = {
    'Clientes': ['Contactos'],
    'Comportamiento del agente': [
        'Rutas', 'Correcciones', 'Vocabulario', 'Prompt base'],
    'Actividad y seguimiento': [
        'Conversaciones', 'Avisos de agendado',
        'Avisos de visitas de suscripción', 'Recontactos enviados',
        'Códigos postales', 'Interesados por CP'],
    'Configuración': [
        'LLM', 'WhatsApp', 'Recontactos', 'Etapas de servicio',
        'Plantillas de avisos'],
}


@tagged('post_install', '-at_install')
class TestMenusAgente(TransactionCase):

    def _hijos(self, menu):
        return self.env['ir.ui.menu'].with_context(lang='es_MX').search(
            [('parent_id', '=', menu.id)], order='sequence, id')

    def test_cuatro_secciones_en_orden(self):
        raiz = self.env.ref('visar_whatsapp_agent.menu_agent_root')
        secciones = self._hijos(raiz)
        self.assertEqual(secciones.mapped('name'), list(ARBOL))
        for seccion in secciones:
            self.assertEqual(self._hijos(seccion).mapped('name'),
                             ARBOL[seccion.name], seccion.name)

    def test_icono_propio(self):
        """El icono elegido el 22-oct-2026, no el generico de Odoo."""
        raiz = self.env.ref('visar_whatsapp_agent.menu_agent_root')
        self.assertEqual(raiz.web_icon,
                         'visar_whatsapp_agent,static/description/icon.svg')
        self.assertTrue(raiz.web_icon_data)
        adjunto = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'ir.ui.menu'), ('res_id', '=', raiz.id),
            ('res_field', '=', 'web_icon_data')])
        self.assertEqual(adjunto.mimetype, 'image/svg+xml')
        modulo = self.env['ir.module.module'].search(
            [('name', '=', 'visar_whatsapp_agent')])
        self.assertEqual(modulo.icon,
                         '/visar_whatsapp_agent/static/description/icon.svg')

    def test_codigos_postales_se_abre(self):
        """Con hijos, Odoo lo volvía encabezado y su lista no se abría."""
        menu = self.env.ref('visar_whatsapp_agent.menu_agent_cp_interes')
        self.assertFalse(self._hijos(menu))
        self.assertTrue(menu.action)
