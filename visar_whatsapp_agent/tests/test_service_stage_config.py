# -*- coding: utf-8 -*-
"""La etapa le DICE al agente que hacer con el servicio, y el agente obedece.

El fallo que cierra (Visar, 11-sep-2026): una cita con incidencia, pendiente de
reprogramar y con fecha FUTURA, le salia al cliente bajo "tus servicios
anteriores" — porque la etapa que Visar llama "Incidencia — Reprogramar" es la
`Cancelled` de Odoo renombrada, y viene marcada como cerrada. Visar no cancela
servicios: esa etapa significa pendiente.

Lo que se fija aqui:

  * el valor de fabrica (`auto`) NO cambia nada. Es la condicion para poder
    instalarlo sin revisar las cinco etapas: lo que no se configure se comporta
    como el 10-sep;
  * `upcoming` gana a la marca de cerrada de Odoo, que es el caso de Visar;
  * `history` gana a la fecha futura, que es el caso simetrico;
  * la etiqueta se la lleva el cliente, y el nombre interno de la etapa no.
"""
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestServiceStageConfig(TransactionCase):

    WA = '5219990882211'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Cliente Etapas', 'phone': '9990882211'})
        # La etapa del caso real: "Cancelled" de FSM, renombrada por Visar.
        cls.etapa = cls.env.ref('industry_fsm.planning_project_stage_4')
        cls.proyecto = cls.env['project.project'].create({'name': 'Proyecto etapas'})

    def setUp(self):
        super().setUp()
        # Cada prueba parte de la configuracion de fabrica.
        self.etapa.write({'visar_agent_bucket': 'auto', 'visar_agent_label': False})

    def _servicio(self, dentro_de_horas=72):
        """Un servicio confirmado del cliente, con su tarea en la etapa."""
        inicio = fields.Datetime.add(fields.Datetime.now(), hours=dentro_de_horas)
        evento = self.env['calendar.event'].create({
            'name': 'Servicio con incidencia',
            'start': inicio,
            'stop': fields.Datetime.add(inicio, hours=1)})
        producto = self.env['product.product'].create({
            'name': 'Fumigacion de prueba', 'type': 'service',
            'visar_is_service': True})
        pedido = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': producto.id,
                                   'calendar_event_id': evento.id})]})
        pedido.write({'state': 'sale'})
        linea = pedido.order_line[:1]
        tarea = self.env['project.task'].create({
            'name': 'Tarea con incidencia',
            'project_id': self.proyecto.id,
            'stage_id': self.etapa.id})
        linea.task_id = tarea.id
        return linea

    def _lista(self, scope):
        salida = self.Tools.agent_customer_services(
            {'phone': self.WA, 'scope': scope})
        return salida.get('services') or []

    def _nombres(self, scope):
        return [s.get('service') for s in self._lista(scope)]

    # --- El cubo -------------------------------------------------------

    def test_de_fabrica_se_comporta_como_antes(self):
        """`auto` = la deduccion de siempre: etapa cerrada -> anteriores.

        Si esto se rompe, instalar el modulo cambiaria la lista de TODOS los
        clientes sin que nadie lo pidiera.
        """
        self.assertTrue(self.etapa.fold, "la etapa del caso viene cerrada")
        self._servicio(dentro_de_horas=72)
        self.assertTrue(self._nombres('history'))
        self.assertFalse(self._nombres('upcoming'))

    def test_pendiente_le_gana_a_la_marca_de_cerrada(self):
        """El caso de Visar: incidencia por reprogramar es un servicio VIVO."""
        self.etapa.visar_agent_bucket = 'upcoming'
        self._servicio(dentro_de_horas=72)
        self.assertTrue(self._nombres('upcoming'),
                        "sale en proximos aunque Odoo marque la etapa cerrada")
        self.assertFalse(self._nombres('history'))

    def test_terminado_le_gana_a_la_fecha_futura(self):
        """El simetrico: si la etapa dice terminado, no importa la fecha."""
        self.etapa.visar_agent_bucket = 'history'
        self._servicio(dentro_de_horas=72)
        self.assertTrue(self._nombres('history'))
        self.assertFalse(self._nombres('upcoming'))

    def test_sin_tarea_manda_la_fecha(self):
        """Un servicio sin tarea de campo no tiene etapa que preguntar."""
        self.etapa.visar_agent_bucket = 'upcoming'
        inicio = fields.Datetime.add(fields.Datetime.now(), hours=-48)
        evento = self.env['calendar.event'].create({
            'name': 'Sin tarea', 'start': inicio,
            'stop': fields.Datetime.add(inicio, hours=1)})
        producto = self.env['product.product'].create({
            'name': 'Servicio sin tarea', 'type': 'service',
            'visar_is_service': True})
        pedido = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': producto.id,
                                   'calendar_event_id': evento.id})]})
        pedido.write({'state': 'sale'})
        self.assertTrue(self._nombres('history'), "la fecha pasada manda")

    # --- Lo que lee el cliente -----------------------------------------

    def test_sin_etiqueta_el_cliente_lee_el_nombre_de_la_etapa(self):
        # En ESPANOL: `agent_customer_services` lee el catalogo con
        # lang='es_MX' porque esto lo lee un cliente, y el usuario RPC del
        # agente esta en ingles. La etapa se llama "Cancelled" en el original
        # de Odoo y "Incidencia — Reprogramar" para Visar.
        nombre_es = self.etapa.with_context(lang='es_MX').name
        self._servicio()
        estados = [s.get('status') for s in self._lista('history')]
        self.assertEqual(estados, [nombre_es])

    def test_con_etiqueta_el_cliente_no_lee_el_nombre_interno(self):
        """"Incidencia — Reprogramar" es lenguaje de operaciones."""
        self.etapa.write({'visar_agent_bucket': 'upcoming',
                          'visar_agent_label': "Pendiente de reprogramar"})
        self._servicio()
        estados = [s.get('status') for s in self._lista('upcoming')]
        self.assertEqual(estados, ["Pendiente de reprogramar"])
        self.assertNotIn(self.etapa.with_context(lang='es_MX').name, estados,
                         "el nombre interno no llega al cliente")
