# -*- coding: utf-8 -*-
"""El botón «Próxima reunión» de la ficha abre la cita donde de verdad está."""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestCrmReunion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tipo = cls.env['appointment.type']
        cls.tecnico = cls.env['appointment.resource'].create({'name': 'Técnico reunión'})
        cls.tipo = Tipo.create({
            'name': 'Tipo reunión', 'schedule_based_on': 'resources',
            'resource_ids': [(4, cls.tecnico.id)]})
        cls.otro_tipo = Tipo.create({
            'name': 'Otro tipo reunión', 'schedule_based_on': 'resources',
            'resource_ids': [(4, cls.tecnico.id)]})
        cls.cliente = cls.env['res.partner'].create({'name': 'Cliente reunión'})
        cls.ficha = cls.env['crm.lead'].create({
            'name': 'Ficha reunión', 'type': 'opportunity',
            'partner_id': cls.cliente.id})

    def _cita(self, dias, tipo=None, con_tecnico=True):
        inicio = fields.Datetime.now() + timedelta(days=dias)
        valores = {
            'name': 'Cita reunión', 'start': inicio,
            'stop': inicio + timedelta(hours=1),
            'opportunity_id': self.ficha.id,
            'partner_ids': [(4, self.cliente.id)],
        }
        if con_tecnico:
            valores.update({
                'appointment_type_id': (tipo or self.tipo).id,
                'booking_line_ids': [(0, 0, {
                    'appointment_resource_id': self.tecnico.id,
                    'capacity_reserved': 1, 'capacity_used': 1})],
            })
        return self.env['calendar.event'].create(valores)

    def test_abre_las_reservas_del_tipo_real_y_solo_las_de_la_ficha(self):
        cita = self._cita(3)
        action = self.ficha.action_schedule_meeting()
        self.assertEqual(action['res_model'], 'calendar.event')
        self.assertEqual(action['domain'], [('opportunity_id', '=', self.ficha.id)])
        self.assertEqual(
            action['context']['search_default_appointment_type_id'], self.tipo.id)
        self.assertEqual(action['context']['initial_date'], cita.start)
        self.assertEqual(self.env['calendar.event'].search(action['domain']), cita)
        nativa = self.env.ref('appointment.calendar_event_action_view_bookings_resources')
        self.assertEqual(action['id'], nativa.id)

    def test_apunta_a_la_proxima_y_no_a_una_pasada(self):
        self._cita(-5)
        proxima = self._cita(2)
        self._cita(9)
        action = self.ficha.action_schedule_meeting()
        self.assertEqual(action['context']['initial_date'], proxima.start)

    def test_con_varios_tipos_no_filtra_por_uno(self):
        self._cita(2)
        self._cita(4, tipo=self.otro_tipo)
        action = self.ficha.action_schedule_meeting()
        self.assertNotIn('search_default_appointment_type_id', action['context'])

    def test_sin_citas_con_tecnico_sigue_el_nativo(self):
        self._cita(2, con_tecnico=False)
        action = self.ficha.action_schedule_meeting()
        self.assertEqual(
            action['id'], self.env.ref('calendar.action_calendar_event').id)
        self.assertEqual(
            action['context']['search_default_opportunity_id'], self.ficha.id)
