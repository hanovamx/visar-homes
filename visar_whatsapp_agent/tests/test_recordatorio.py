# -*- coding: utf-8 -*-
"""Recordatorio de servicio a clientes sin suscripción (26-oct-2026).

Ver `models/visar_recordatorio.py`. Lo que se fija:

* pedir el recordatorio pone la fecha, y cada servicio terminado la reinicia;
* el cron crea UNA oportunidad con actividad y encola UN WhatsApp, y corre la
  fecha otros 3/6 meses; apagado no hace nada;
* quien ya tiene un servicio por venir no recibe recordatorio;
* si el cliente contesta días después, el agente lo retoma con su contexto.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.visar_recordatorio import TEMPLATE_KEY


@tagged('post_install', '-at_install')
class TestRecordatorio(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env['ir.config_parameter'].sudo()
        params.set_param('visar.recordatorio.enabled', 'True')
        params.set_param('visar.recordatorio.dias_antes', '7')
        params.set_param('visar.recordatorio.user_id', str(cls.env.ref('base.user_admin').id))
        # Todo el día, todos los días: la prueba no depende de la hora.
        params.set_param('visar.handoff.dias', 'ld')
        params.set_param('visar.handoff.hora_desde', '0')
        params.set_param('visar.handoff.hora_hasta', '24')
        cls.Task = cls.env['project.task']
        cls.Outbox = cls.env['visar.wa.recordatorio.message'].sudo()
        cls.proyecto = cls.env['project.project'].create({
            'name': 'Campo (recordatorio)', 'is_fsm': True,
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Marta Recordatorio', 'phone': '9990776611'})
        cls.hoy = fields.Date.context_today(cls.cliente)

    def _tarea(self, **vals):
        return self.Task.sudo().create(dict({
            'name': 'Fumigación', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id}, **vals))

    def _vence_pronto(self):
        self.cliente.write({'visar_recordatorio_meses': '3',
                            'visar_recordatorio_fecha': self.hoy + relativedelta(days=5),
                            'visar_recordatorio_servicio': 'Fumigación'})

    def test_pedirlo_pone_la_fecha(self):
        self.cliente.visar_recordatorio_meses = '6'
        self.assertEqual(self.cliente.visar_recordatorio_fecha,
                         self.hoy + relativedelta(months=6))

    def test_un_servicio_terminado_reinicia_la_cuenta(self):
        self._vence_pronto()
        self._tarea(name='Jardinería').write({'state': '1_done'})
        self.assertEqual(self.cliente.visar_recordatorio_fecha,
                         self.hoy + relativedelta(months=3))
        self.assertEqual(self.cliente.visar_recordatorio_servicio, 'Jardinería')

    def test_sin_pedirlo_un_servicio_no_pone_nada(self):
        self._tarea().write({'state': '1_done'})
        self.assertFalse(self.cliente.visar_recordatorio_fecha)

    def test_el_cron_crea_la_oportunidad_y_encola_el_whatsapp(self):
        self._vence_pronto()
        fecha = self.cliente.visar_recordatorio_fecha
        self.assertEqual(self.Task._visar_cron_recordatorios(), 1)
        lead = self.env['crm.lead'].search([('partner_id', '=', self.cliente.id)])
        self.assertEqual(len(lead), 1)
        self.assertEqual(lead.type, 'opportunity')
        actividad = lead.activity_ids
        self.assertEqual(len(actividad), 1)
        self.assertEqual(actividad.user_id, self.env.ref('base.user_admin'))
        self.assertEqual(actividad.date_deadline, fields.Date.context_today(lead))
        mensaje = self.Outbox.search([('partner_id', '=', self.cliente.id)])
        self.assertEqual(len(mensaje), 1)
        self.assertEqual(mensaje.template_key, TEMPLATE_KEY)
        self.assertEqual(mensaje.phone, '529990776611')
        self.assertIn("ya casi toca tu siguiente servicio de Fumigación",
                      mensaje.fallback_text)
        self.assertEqual(mensaje._visar_wa_endpoint(TEMPLATE_KEY), '/internal/booking-event')
        self.assertEqual(mensaje._visar_wa_context()['contexto']['servicio'], 'Fumigación')
        # La siguiente vuelta, otros 3 meses; y una segunda corrida no repite.
        self.assertEqual(self.cliente.visar_recordatorio_fecha,
                         fecha + relativedelta(months=3))
        self.assertEqual(self.Task._visar_cron_recordatorios(), 0)
        self.assertEqual(len(self.Outbox.search([('partner_id', '=', self.cliente.id)])), 1)

    def test_apagado_no_hace_nada(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'visar.recordatorio.enabled', 'False')
        self._vence_pronto()
        self.assertEqual(self.Task._visar_cron_recordatorios(), 0)
        self.assertFalse(self.Outbox.search([('partner_id', '=', self.cliente.id)]))

    def test_con_un_servicio_por_venir_no_se_le_recuerda(self):
        self._vence_pronto()
        self._tarea(planned_date_begin=fields.Datetime.now() + relativedelta(days=2))
        self.assertEqual(self.Task._visar_cron_recordatorios(), 0)
        self.assertFalse(self.env['crm.lead'].search([('partner_id', '=', self.cliente.id)]))

    def test_si_contesta_dias_despues_el_agente_lo_retoma(self):
        self._vence_pronto()
        self.Task._visar_cron_recordatorios()
        mensaje = self.Outbox.search([('partner_id', '=', self.cliente.id)])
        mensaje.write({'state': 'sent', 'sent_at': fields.Datetime.now()})
        datos = self.env['visar.agent.tools'].agent_partner_facts(
            {'phone': '5219990776611'})
        recontacto = datos.get('recontacto')
        self.assertTrue(recontacto)
        self.assertEqual(recontacto['contexto']['motivo'], TEMPLATE_KEY)
        self.assertEqual(recontacto['contexto']['servicio'], 'Fumigación')
        self.assertIn("¿Te lo agendamos?", recontacto['texto'])

    def test_la_plantilla_tiene_su_fila(self):
        fila = self.env['visar.wa.template.route'].search([('key', '=', TEMPLATE_KEY)])
        self.assertEqual(len(fila), 1)
