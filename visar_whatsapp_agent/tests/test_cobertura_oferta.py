# -*- coding: utf-8 -*-
"""¿Dónde fumigamos?, cuando el cliente ya lo dijo por WhatsApp (21-oct-2026).

El paso de cobertura lleva `oferta`: la pregunta que hace el agente en vez de
«¿Dónde fumigamos?» si ya sabe que la plaga está solo adentro o solo afuera.
Los textos se editan en Ajustes → Visar.
"""
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_appointment.models.appointment_wizard_flow import (
    COBERTURA_OFERTA_DEFAULT,
)


@tagged('post_install', '-at_install')
class TestCoberturaOferta(TransactionCase):

    def _paso(self):
        return self.env['appointment.type']._visar_wizard_step_options(
            {}, 'cobertura')

    def test_el_paso_lleva_la_oferta_por_zona(self):
        oferta = self._paso()['oferta']
        self.assertEqual(oferta['interior']['pregunta'],
                         COBERTURA_OFERTA_DEFAULT['interior'])
        self.assertEqual(oferta['interior']['solo'], "Solo interior")
        self.assertEqual(oferta['interior']['tambien'], "También el exterior")
        self.assertEqual(oferta['exterior']['tambien'], "También el interior")
        # Las opciones de siempre no cambian: la web sigue igual.
        self.assertEqual([o['value'] for o in self._paso()['options']],
                         ['interior', 'exterior', 'ambos'])

    def test_los_textos_salen_de_ajustes(self):
        self.env['res.config.settings'].create({
            'visar_cobertura_oferta_interior': "¿Sumamos el jardín?",
        }).execute()
        oferta = self._paso()['oferta']
        self.assertEqual(oferta['interior']['pregunta'], "¿Sumamos el jardín?")
        self.assertEqual(oferta['exterior']['pregunta'],
                         COBERTURA_OFERTA_DEFAULT['exterior'])


    # --- Codigo postal sin servicio (21-oct-2026) -------------------------

    def test_el_texto_sin_cobertura_sale_de_ajustes(self):
        Flujo = self.env['appointment.type']
        self.assertIn("¿Tienes alguna otra dirección?",
                      Flujo._visar_wizard_texto_sin_cobertura('42084'))
        self.env['res.config.settings'].create({
            'visar_cobertura_sin_servicio': "Aún no llegamos al {cp}.",
        }).execute()
        self.assertEqual(Flujo._visar_wizard_texto_sin_cobertura('42084'),
                         "Aún no llegamos al 42084.\n\n¿Tienes alguna otra dirección?")
        with self.assertRaises(ValidationError):
            self.env['res.config.settings'].create({
                'visar_cobertura_sin_servicio': "No llegamos a {codigo}."})
