# -*- coding: utf-8 -*-
"""El aviso por WhatsApp de las visitas de suscripción pre-agendadas (Fase B).

Lo que se fija:

* **Apagado no manda nada**, y encendido manda UN aviso por hito: el cron es
  horario y la idempotencia vive en la visita.
* **Avisada = el cliente la puede mover.** Antes del aviso el bloqueo `poliza`
  sigue; después, no.
* «Sí, confirmo» marca la visita confirmada, y solo si la cita es del teléfono.
* La escalera: `ventana` deja solo los días a esa distancia de la cita.
* Sin teléfono no se encola nada: actividad para oficina.
"""
from unittest.mock import patch

import pytz
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.visar_poliza_aviso import dias_de_aviso


@tagged('post_install', '-at_install')
class TestPolizaAviso(TransactionCase):

    WA = '5219990775566'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env['project.task']
        cls.Outbox = cls.env['visar.wa.poliza.message'].sudo()
        cls.maestro = cls.env['appointment.type'].sudo(
        )._visar_get_master_appointment_type()
        cp = cls.env['visar.zone.cp'].sudo().search(
            [('zone_id', '!=', False)], limit=1)
        cls.zona = cp.zone_id
        cls.skip_todo = not (cls.maestro and cls.zona)
        if cls.skip_todo:
            return
        cls.proyecto = cls.env['project.project'].create({
            'name': 'Campo (aviso poliza)', 'is_fsm': True,
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Laura Aviso', 'phone': '9990775566', 'zip': cp.name,
            'partner_latitude': 25.67, 'partner_longitude': -100.33})
        empleado = cls.env['hr.employee'].create({'name': 'Tecnico aviso'})
        cls.recurso = cls.env['appointment.resource'].create({
            'name': 'Tecnico aviso', 'visar_employee_id': empleado.id,
            'capacity': 1, 'visar_zone_ids': [(6, 0, cls.zona.ids)],
            'visar_service_ids': [(6, 0, cls.maestro.ids)],
        })
        cls.maestro.sudo().resource_ids = [(4, cls.recurso.id)]
        cls.tz = pytz.timezone(cls.maestro.appointment_tz or 'America/Monterrey')
        params = cls.env['ir.config_parameter'].sudo()
        params.set_param('visar.poliza.aviso_enabled', 'True')
        params.set_param('visar.poliza.aviso_dias', '14,7,2')
        # Todo el día: la prueba no puede depender de la hora a la que corre.
        params.set_param('visar.poliza.aviso_hora_desde', '0')
        params.set_param('visar.poliza.aviso_hora_hasta', '24')

    def setUp(self):
        super().setUp()
        if self.skip_todo:
            self.skipTest("la BD no tiene tipo de cita maestro o catalogo de CP")

    # --- Helpers (los de visar_appointment/tests/test_poliza_preagenda.py) ----

    def _poliza(self, cliente=None):
        plan = self.env['sale.subscription.plan'].sudo().search(
            [('visar_visit_interval_months', '>', 0)], limit=1)
        if not plan:
            self.skipTest("la BD no tiene planes con intervalo de visitas")
        producto = self.env['product.product'].sudo().search(
            [('recurring_invoice', '=', True)], limit=1) or \
            self.env['product.product'].sudo().create({
                'name': 'Poliza de prueba (recurrente)', 'type': 'service',
                'recurring_invoice': True})
        orden = self.env['sale.order'].sudo().create({
            'partner_id': (cliente or self.cliente).id, 'plan_id': plan.id,
            'order_line': [(0, 0, {'product_id': producto.id, 'product_uom_qty': 1})],
        })
        orden.write({'state': 'sale', 'subscription_state': '3_progress'})
        return orden

    def _en_dias(self, dias, hora_local=15):
        dia = (fields.Datetime.now() + relativedelta(days=dias)).date()
        local = self.tz.localize(
            fields.Datetime.to_datetime('%s %02d:00:00' % (dia, hora_local)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def _visita_preagendada(self, dias=5, cliente=None):
        orden = self._poliza(cliente)
        ctx = dict(visar_poliza_due_sync=True, visar_sin_aviso_ruta=True)
        inicio = self._en_dias(-30)
        self.Task.sudo().with_context(**ctx).create({
            'name': 'Visita 1', 'project_id': self.proyecto.id,
            'partner_id': orden.partner_id.id,
            'visar_subscription_order_id': orden.id,
            'visar_visit_kind': 'preventiva', 'visar_visit_seq': 1,
            'visar_visit_total': 12, 'planned_date_begin': inicio,
            'date_deadline': inicio + relativedelta(hours=1),
            'visar_technician_ids': [(6, 0, self.recurso.visar_employee_id.ids)],
        })
        visita = self.Task.sudo().with_context(**ctx).create({
            'name': 'Visita 2', 'project_id': self.proyecto.id,
            'partner_id': orden.partner_id.id,
            'visar_subscription_order_id': orden.id,
            'visar_visit_kind': 'preventiva', 'visar_visit_seq': 2,
            'visar_visit_total': 12,
        })
        visita.with_context(visar_poliza_due_sync=True).write(
            {'visar_visit_due_date': (fields.Datetime.now()
                                      + relativedelta(days=dias)).date()})
        cita = visita._visar_preagendar()
        if not cita:
            self.skipTest("no se pudo pre-agendar en esta BD")
        return visita, cita

    def _mensajes(self, visita):
        return self.Outbox.search([('task_id', '=', visita.id)])

    # --- Cuándo toca ----------------------------------------------------------

    def test_los_dias_se_leen_de_la_configuracion(self):
        self.assertEqual(dias_de_aviso('14, 7,2,7'), [14, 7, 2])
        with self.assertRaises(ValueError):
            dias_de_aviso('14,siete')

    def test_el_hito_es_el_mas_chico_que_ya_llego(self):
        hito = self.Task._visar_poliza_hito
        self.assertEqual(hito(10, [14, 7, 2]), 14)
        self.assertEqual(hito(5, [14, 7, 2]), 7)
        self.assertEqual(hito(1, [14, 7, 2]), 2)
        self.assertIsNone(hito(20, [14, 7, 2]), "aun no toca")
        self.assertIsNone(hito(0, [14, 7, 2]), "es hoy: ya no se avisa")

    # --- El cron --------------------------------------------------------------

    def test_apagado_no_manda_nada(self):
        visita, _cita = self._visita_preagendada()
        self.env['ir.config_parameter'].sudo().set_param(
            'visar.poliza.aviso_enabled', 'False')
        self.Task._visar_cron_poliza_avisos()
        self.assertFalse(self._mensajes(visita))

    def test_un_aviso_por_hito_aunque_el_cron_corra_cuatro_veces(self):
        visita, cita = self._visita_preagendada(dias=5)
        for _ in range(4):
            self.Task._visar_cron_poliza_avisos()
        mensajes = self._mensajes(visita)
        self.assertEqual(len(mensajes), 1)
        self.assertEqual(mensajes.hito, 7, "a 5 dias toca el de 7")
        self.assertEqual(mensajes.event_id, cita)
        self.assertEqual(mensajes.phone, '529990775566')
        self.assertEqual(mensajes._visar_wa_endpoint('poliza_confirmar'),
                         '/internal/booking-event')
        self.assertEqual(mensajes._visar_wa_context()['event_id'], cita.id)

    def test_una_confirmada_no_se_avisa(self):
        visita, _cita = self._visita_preagendada()
        visita.visar_visit_preagenda_confirmada = True
        self.Task._visar_cron_poliza_avisos()
        self.assertFalse(self._mensajes(visita))

    def test_sin_telefono_actividad_para_oficina(self):
        sin_tel = self.env['res.partner'].create({
            'name': 'Sin Telefono', 'zip': self.cliente.zip,
            'partner_latitude': 25.67, 'partner_longitude': -100.33})
        visita, _cita = self._visita_preagendada(cliente=sin_tel)
        self.Task._visar_cron_poliza_avisos()
        self.assertFalse(self._mensajes(visita))
        self.assertTrue(visita.activity_ids.filtered(
            lambda a: 'avisar por teléfono' in (a.summary or '')))

    # --- Avisada = la puede mover -------------------------------------------

    def test_avisada_y_entregada_ya_se_puede_mover(self):
        visita, cita = self._visita_preagendada(dias=5)
        self.assertEqual(cita._visar_reschedule_blocked(), 'poliza')
        self.Task._visar_cron_poliza_avisos()
        mensaje = self._mensajes(visita)
        # Encolado no basta: tiene que haberle llegado.
        self.assertEqual(cita._visar_reschedule_blocked(), 'poliza')
        self.env['ir.config_parameter'].sudo().set_param(
            'visar_field.agent_token', 'token-prueba')

        class Respuesta:
            status_code = 200

            @staticmethod
            def json():
                return {'sent': True, 'mode': 'template'}

        with patch('odoo.addons.visar_base.models.wa_outbox_mixin.requests.post',
                   return_value=Respuesta()) as post:
            mensaje._visar_attempt_send()
        self.assertEqual(post.call_args.kwargs['json']['event_id'], cita.id)
        self.assertTrue(visita.visar_visit_aviso_at)
        self.assertIsNone(cita._visar_reschedule_blocked())

    # --- Las respuestas -------------------------------------------------------

    def test_si_confirmo_la_confirma(self):
        visita, cita = self._visita_preagendada()
        Tools = self.env['visar.agent.tools']
        ajena = Tools.agent_poliza_confirm(
            {'phone': '5219990779999', 'event_id': cita.id})
        self.assertFalse(ajena['ok'])
        self.assertFalse(visita.visar_visit_preagenda_confirmada)
        res = Tools.agent_poliza_confirm({'phone': self.WA, 'event_id': cita.id})
        self.assertTrue(res['ok'], res)
        self.assertTrue(visita.visar_visit_preagenda_confirmada)
        self.assertEqual(visita.visar_visit_respuesta, 'confirmo')
        self.assertTrue(res['when_label'])

    def test_quiero_cambiarla_se_apunta_sin_mover_nada(self):
        visita, cita = self._visita_preagendada()
        inicio = cita.start
        res = self.env['visar.agent.tools'].agent_poliza_change(
            {'phone': self.WA, 'event_id': cita.id})
        self.assertTrue(res['ok'])
        self.assertEqual(visita.visar_visit_respuesta, 'cambio')
        self.assertEqual(cita.start, inicio)

    def test_la_ventana_deja_solo_los_dias_cercanos(self):
        visita, cita = self._visita_preagendada(dias=10)
        visita.visar_visit_aviso_at = fields.Datetime.now()
        res = self.env['visar.agent.tools'].agent_reschedule_days(
            {'phone': self.WA, 'event_id': cita.id, 'ventana': 3})
        self.assertFalse(res.get('blocked'), res)
        ancla = pytz.utc.localize(cita.start).astimezone(self.tz).date()
        for dia in res['days']:
            distancia = abs((fields.Date.to_date(dia['date']) - ancla).days)
            self.assertLessEqual(distancia, 3, dia)

    def test_la_lista_de_servicios_marca_la_escalera(self):
        visita, cita = self._visita_preagendada()
        res = self.env['visar.agent.tools'].agent_customer_services(
            {'phone': self.WA, 'scope': 'upcoming'})
        fila = next(s for s in res['services'] if s['event_id'] == cita.id)
        self.assertTrue(fila['escalera'])
