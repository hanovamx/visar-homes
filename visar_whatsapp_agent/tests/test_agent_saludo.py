# -*- coding: utf-8 -*-
"""El saludo según quién escribe (`agent_partner_facts` → `saludo`).

Lo que se fija aquí:

  * **apagado no cambia nada**: texto vacío para todos, que es "pinta tu saludo
    de siempre". Es el estado en que se despliega;
  * un desconocido que nunca escribió es **nuevo**; en cuanto hay constancia de
    un mensaje suyo, o es cliente, es **conocido**;
  * el suscriptor solo lo es **con una visita fechada que ya conoce**. Una
    pre-agenda sin confirmar no se anuncia en un saludo;
  * sin nombre no queda una coma colgando;
  * nunca levanta.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_whatsapp_agent.models.visar_agent_saludo import (
    SALUDO_CONOCIDO_PARAM,
    SALUDO_ENABLED_PARAM,
    SALUDO_SUSCRIPTOR_PARAM,
)

CLIENTE = '528119990771'
DESCONOCIDO = '528119990772'


@tagged('post_install', '-at_install')
class TestAgentSaludo(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.T = cls.env['visar.agent.tools']
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_param(SALUDO_ENABLED_PARAM, 'True')
        # Los textos de fabrica, pase lo que pase con lo que haya en la base.
        cls.params.search([('key', 'in', (
            SALUDO_CONOCIDO_PARAM, SALUDO_SUSCRIPTOR_PARAM))]).unlink()
        cls.cliente = cls.env['res.partner'].create({
            'name': 'ana maría lópez', 'phone': CLIENTE})
        cls.casa = cls.env['res.partner'].create({
            'name': 'Casa', 'type': 'delivery', 'parent_id': cls.cliente.id,
            'street': 'Calle 1 No. 10', 'street2': 'Contry', 'zip': '64860'})

    def _saludo(self, phone=CLIENTE):
        return self.T.agent_partner_facts({'phone': phone})['saludo']

    def _poliza(self):
        """Una suscripción en curso, mínima. Mismo apaño que la pre-agenda."""
        plan = self.env['sale.subscription.plan'].sudo().search([], limit=1)
        if not plan:
            self.skipTest("la BD no tiene planes de suscripción")
        producto = self.env['product.product'].sudo().search(
            [('recurring_invoice', '=', True)], limit=1)
        if not producto:
            producto = self.env['product.product'].sudo().create({
                'name': 'Suscripción de prueba', 'type': 'service',
                'recurring_invoice': True})
        orden = self.env['sale.order'].sudo().create({
            'partner_id': self.cliente.id, 'plan_id': plan.id,
            'order_line': [(0, 0, {'product_id': producto.id,
                                   'product_uom_qty': 1})],
        })
        orden.write({'state': 'sale', 'subscription_state': '3_progress'})
        if 'visar_service_partner_id' in orden._fields:
            orden.with_context(visar_allow_shipping_change=True).write(
                {'visar_service_partner_id': self.casa.id})
        return orden

    def _visita(self, orden, dias=5, **extra):
        proyecto = self.env['project.project'].sudo().search(
            [('is_fsm', '=', True)], limit=1)
        if not proyecto:
            self.skipTest("la BD no tiene proyecto de servicio de campo")
        cuando = fields.Datetime.now().replace(
            hour=18, minute=0, second=0, microsecond=0) + timedelta(days=dias)
        return self.env['project.task'].sudo().create(dict({
            'name': 'Visita de prueba', 'project_id': proyecto.id,
            'partner_id': self.cliente.id,
            'visar_subscription_order_id': orden.id,
            'planned_date_begin': cuando,
            'date_deadline': cuando + timedelta(hours=1),
        }, **extra))

    # ------------------------------------------------------------------
    # Apagado
    # ------------------------------------------------------------------

    def test_apagado_nadie_recibe_saludo_propio(self):
        self.params.set_param(SALUDO_ENABLED_PARAM, False)
        self.assertEqual(self._saludo(), {'nivel': 'nuevo', 'texto': ''})

    # ------------------------------------------------------------------
    # Nuevo / conocido
    # ------------------------------------------------------------------

    def test_un_desconocido_que_nunca_escribio_es_nuevo(self):
        self.assertEqual(self._saludo(DESCONOCIDO),
                         {'nivel': 'nuevo', 'texto': ''})

    def test_quien_ya_habia_escrito_es_conocido_y_sin_coma_colgando(self):
        self.T.agent_track_inbound({'phone': DESCONOCIDO})
        saludo = self._saludo(DESCONOCIDO)
        self.assertEqual(saludo['nivel'], 'conocido')
        self.assertEqual(saludo['texto'],
                         "¡Hola de nuevo! ¿Cómo te puedo ayudar?")

    def test_un_cliente_es_conocido_aunque_nunca_haya_escrito(self):
        """Decisión del usuario (13-oct-2026): quien reservó por la web ya
        tiene nombre y servicios con nosotros; no es un desconocido."""
        saludo = self._saludo()
        self.assertEqual(saludo['nivel'], 'conocido')
        self.assertEqual(saludo['texto'],
                         "¡Hola de nuevo, Ana! ¿Cómo te puedo ayudar?")

    def test_una_empresa_no_tiene_nombre_de_pila(self):
        self.cliente.write({'is_company': True, 'name': 'Restaurante El Rey'})
        self.assertEqual(self._saludo()['texto'],
                         "¡Hola de nuevo! ¿Cómo te puedo ayudar?")

    def test_un_telefono_guardado_como_nombre_no_se_dice(self):
        self.cliente.name = '8119990771'
        self.assertNotIn('8119990771', self._saludo()['texto'])

    def test_el_texto_se_edita_y_lleva_la_marca(self):
        self.params.set_param(
            SALUDO_CONOCIDO_PARAM, "Hola {{nombre}}, aquí {{marca}}.")
        texto = self._saludo()['texto']
        self.assertTrue(texto.startswith("Hola Ana, aquí "))
        self.assertNotIn('{{', texto)

    def test_un_marcador_de_suscriptor_en_el_de_conocido_no_llega(self):
        self.params.set_param(
            SALUDO_CONOCIDO_PARAM, "Hola {{nombre}}, te vemos el {{fecha}}.")
        self.assertNotIn('{{', self._saludo()['texto'])

    def test_dos_clientes_con_el_mismo_numero_no_se_nombran(self):
        self.env['res.partner'].create({'name': 'Otro', 'phone': CLIENTE})
        saludo = self._saludo()
        self.assertEqual(saludo['nivel'], 'nuevo')
        self.assertNotIn('Ana', saludo['texto'])

    # ------------------------------------------------------------------
    # Suscriptor
    # ------------------------------------------------------------------

    def test_el_suscriptor_con_visita_recibe_su_fecha_y_su_colonia(self):
        visita = self._visita(self._poliza())
        saludo = self._saludo()
        self.assertEqual(saludo['nivel'], 'suscriptor')
        self.assertIn('Ana', saludo['texto'])
        self.assertIn('Contry', saludo['texto'])
        self.assertNotIn('Calle 1', saludo['texto'])
        # El dia en Monterrey, no en UTC: las 18:00 UTC son las 12:00 de alla.
        dia = fields.Datetime.context_timestamp(
            self.T.with_context(tz='America/Monterrey'),
            visita.planned_date_begin).day
        self.assertIn("el %d de " % dia, saludo['texto'])
        self.assertNotIn('{{', saludo['texto'])

    def test_una_preagenda_sin_confirmar_NO_se_anuncia(self):
        """El cliente no sabe que existe: el saludo no es quien se lo dice."""
        self._visita(self._poliza(), visar_visit_preagendada=True)
        self.assertEqual(self._saludo()['nivel'], 'conocido')

    def test_una_preagenda_confirmada_si(self):
        self._visita(self._poliza(), visar_visit_preagendada=True,
                     visar_visit_preagenda_confirmada=True)
        self.assertEqual(self._saludo()['nivel'], 'suscriptor')

    def test_una_visita_pasada_no_cuenta(self):
        self._visita(self._poliza(), dias=-3)
        self.assertEqual(self._saludo()['nivel'], 'conocido')

    def test_una_suscripcion_dada_de_baja_no_cuenta(self):
        orden = self._poliza()
        self._visita(orden)
        orden.write({'subscription_state': '6_churn'})
        self.assertEqual(self._saludo()['nivel'], 'conocido')

    def test_suscriptor_sin_visita_fechada_es_conocido(self):
        self._poliza()
        self.assertEqual(self._saludo()['nivel'], 'conocido')

    def test_un_texto_de_suscriptor_sin_fecha_cae_al_de_fabrica(self):
        self._visita(self._poliza())
        self.params.set_param(SALUDO_SUSCRIPTOR_PARAM, "Hola {{nombre}}.")
        self.assertIn('próxima visita', self._saludo()['texto'])

    # ------------------------------------------------------------------
    # Red de seguridad
    # ------------------------------------------------------------------

    def test_nunca_levanta_y_no_se_lleva_el_bloque(self):
        with patch.object(type(self.T), '_agent_saludo_ahora',
                          side_effect=ValueError("boom")):
            datos = self.T.agent_partner_facts({'phone': CLIENTE})
        self.assertEqual(datos['saludo'], {'nivel': 'nuevo', 'texto': ''})
        self.assertEqual(datos['partner_id'], self.cliente.id)
