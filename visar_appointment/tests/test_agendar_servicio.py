# -*- coding: utf-8 -*-
"""La guía para agendar a mano (29-oct-2026).

Lo que se fija aquí:

* **La guía solo ofrece lo que el web ofrecería**, más las ausencias del
  empleado, que el web todavía no mira.
* **Elegir crea la cita de verdad**: ocupa la agenda (el mismo hueco deja de
  ofrecerse) y la tarea toma fecha y técnico de ella. Es la razón de ser: una
  fecha escrita a mano en la tarea no ocupaba nada.
* **Callada**: sin recordatorio ni invitación al cliente.
"""
import pytz
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgendarServicio(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.maestro = cls.env['appointment.type'].sudo(
        )._visar_get_master_appointment_type()
        cp = cls.env['visar.zone.cp'].sudo().search(
            [('zone_id', '!=', False)], limit=1)
        cls.zona = cp.zone_id
        cls.skip_todo = not (cls.maestro and cls.zona)
        if cls.skip_todo:
            return
        cls.proyecto = cls.env['project.project'].create({
            'name': 'Campo (guía)', 'is_fsm': True,
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Cliente de venta manual (prueba)', 'zip': cp.name})
        cls.empleado = cls.env['hr.employee'].create({'name': 'Tecnico guía'})
        cls.recurso = cls.env['appointment.resource'].create({
            'name': 'Tecnico guía',
            'visar_employee_id': cls.empleado.id,
            'capacity': 1,
            'visar_zone_ids': [(6, 0, cls.zona.ids)],
            'visar_service_ids': [(6, 0, cls.maestro.ids)],
        })
        cls.maestro.sudo().resource_ids = [(4, cls.recurso.id)]
        cls.tz = pytz.timezone(cls.maestro.appointment_tz or 'America/Monterrey')

    def setUp(self):
        super().setUp()
        if self.skip_todo:
            self.skipTest("la BD no tiene tipo de cita maestro o catálogo de CP")

    def _tarea(self, nombre='Servicio manual'):
        return self.env['project.task'].create({
            'name': nombre, 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id})

    def _guia(self, tarea):
        accion = tarea.action_visar_agendar()
        guia = self.env['visar.agendar.servicio'].browse(accion['res_id'])
        # Solo el técnico de la prueba: la zona real tiene técnicos de producción.
        guia.recurso_id = self.recurso
        guia.desde = fields.Date.context_today(guia) + relativedelta(days=2)
        guia.action_buscar()
        return guia

    def test_guia_lista_y_elegir_crea_la_cita(self):
        tarea = self._tarea()
        guia = self._guia(tarea)
        self.assertEqual(guia.zona_id, self.zona, "la zona sale del CP del cliente")
        self.assertTrue(guia.hueco_ids, guia.nota)
        hueco = guia.hueco_ids[0]
        inicio = hueco.inicio
        hueco.action_elegir()

        cita = tarea.visar_visit_event_id
        self.assertTrue(cita, "la tarea queda con cita propia")
        self.assertEqual(cita.start, inicio)
        self.assertEqual(cita.booking_line_ids.appointment_resource_id, self.recurso,
                         "la cita ocupa la agenda del técnico")
        self.assertFalse(cita.alarm_ids, "sin recordatorio al cliente")
        self.assertEqual(tarea.planned_date_begin, inicio)
        self.assertEqual(tarea.visar_technician_ids, self.empleado)
        self.assertFalse(tarea.visar_agendable, "ya no se ofrece el botón")
        with self.assertRaises(UserError):
            tarea.action_visar_agendar()

        # El mismo hueco ya no se le ofrece a otro servicio.
        otra = self._guia(self._tarea('Otro servicio manual'))
        self.assertNotIn(inicio, otra.hueco_ids.filtered(
            lambda h: h.recurso_id == self.recurso).mapped('inicio'))

    def test_ausencia_del_empleado_quita_el_dia(self):
        tarea = self._tarea()
        guia = self._guia(tarea)
        self.assertTrue(guia.hueco_ids, guia.nota)
        dia = fields.Datetime.context_timestamp(
            guia.with_context(tz=str(self.tz)), guia.hueco_ids[0].inicio).date()
        desde = self.tz.localize(fields.Datetime.to_datetime(dia)).astimezone(
            pytz.utc).replace(tzinfo=None)
        self.env['resource.calendar.leaves'].create({
            'name': 'Vacaciones (prueba)',
            'resource_id': self.empleado.resource_id.id,
            'calendar_id': self.empleado.resource_calendar_id.id,
            'date_from': desde,
            'date_to': desde + relativedelta(days=1),
        })
        guia.action_buscar()
        dias = {fields.Datetime.context_timestamp(
            guia.with_context(tz=str(self.tz)), h.inicio).date()
            for h in guia.hueco_ids}
        self.assertNotIn(dia, dias)

    def test_hueco_ocupado_entre_tanto_no_sobrevende(self):
        guia = self._guia(self._tarea())
        hueco = guia.hueco_ids[0]
        inicio = hueco.inicio
        # Otro servicio se lleva ese horario mientras la guía estaba abierta.
        otra = self._guia(self._tarea('Se adelantó'))
        otra.hueco_ids.filtered(lambda h: h.inicio == inicio)[0].action_elegir()
        with self.assertRaises(UserError):
            hueco.action_elegir()
        self.assertFalse(guia.task_id.visar_visit_event_id)

    def test_orden_de_poliza_no_lleva_boton(self):
        plan = self.env['sale.subscription.plan'].sudo().search([], limit=1)
        if not plan:
            self.skipTest("sin planes")
        orden = self.env['sale.order'].create({
            'partner_id': self.cliente.id, 'plan_id': plan.id})
        self.assertEqual(orden.visar_agendables_count, 0)
