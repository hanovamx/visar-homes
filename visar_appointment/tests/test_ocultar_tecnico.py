# -*- coding: utf-8 -*-
"""El sitio web no le enseña al cliente el nombre del técnico asignado."""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestOcultarTecnico(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tipo = cls.env['appointment.type'].create({
            'name': 'Cita prueba técnico', 'schedule_based_on': 'resources'})
        cls.tecnico = cls.env['appointment.resource'].create({
            'name': 'Técnico Secreto Prueba',
            'appointment_type_ids': [(4, cls.tipo.id)]})

    def _panel(self, **valores):
        return str(self.env['ir.qweb']._render(
            'appointment.appointment_meeting_user',
            dict({'appointment_type': self.tipo, 'isDetails': True,
                  'isDate': False, 'selectionPossible': False,
                  'staff_user': False, 'resource': False}, **valores)))

    def test_el_panel_no_ensena_al_tecnico(self):
        html = self._panel(resource=self.tecnico)
        self.assertNotIn('Técnico Secreto Prueba', html)
        self.assertNotIn('o_appointment_user_short_card', html)

    def test_una_cita_por_usuario_se_sigue_viendo(self):
        html = self._panel(staff_user=self.env.user)
        self.assertIn(self.env.user.name, html)

    def test_la_confirmacion_tampoco_lo_lista(self):
        for xmlid, ruta in (
            ('appointment.appointment_validated',
             "//div[@t-foreach='event.appointment_resource_ids']/../.."),
            ('appointment.appointment_validated_card',
             "//h5[@t-out='resource.name']/../../.."),
        ):
            arch = self.env.ref(xmlid)._get_combined_arch()
            nodo = arch.xpath(ruta)[0]
            self.assertEqual(nodo.get('t-if') or nodo.get('t-elif'), 'False', xmlid)
