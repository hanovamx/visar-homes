# -*- coding: utf-8 -*-
"""El día de la cita, tal y como lo nombra el cliente.

La lista de servicios manda la fecha en UTC (`date`) y redactada en la zona del
cliente (`date_label`). Cuando el cliente tiene más de una cita futura, el
runtime pregunta cuál quiere mover y la respuesta es una fecha: *"la del 2 de
octubre"*. Para resolverla necesita el DÍA LOCAL, no el UTC — y recortar la
cadena UTC acierta en la cita de la mañana y falla en la de la tarde, que es la
que movería la cita equivocada sin avisar (24-sep-2026).
"""
from odoo import fields
from odoo.tests import tagged

from .test_agent_reschedule import TestAgentReschedule


@tagged('post_install', '-at_install')
class TestFechaLocalDelServicio(TestAgentReschedule):

    def _servicio(self, evento):
        salida = self.Tools.agent_customer_services(
            {'phone': self.WA, 'scope': 'upcoming'})
        return next(s for s in salida['services']
                    if s.get('event_id') == evento.id)

    def test_el_dia_local_viaja_con_cada_servicio(self):
        evento, _pedido = self._cita(dentro_de_horas=72)
        servicio = self._servicio(evento)
        # El día que dice la etiqueta es el que el cliente va a nombrar.
        dia = servicio['date_local']
        self.assertTrue(dia, "sin día local no se puede resolver 'la del 2'")
        self.assertEqual(len(dia), 10)
        self.assertIn(str(int(dia[8:10])), servicio['date_label'])

    def test_la_cita_de_la_tarde_no_se_va_al_dia_siguiente(self):
        """00:00 UTC son las 18:00 del día ANTERIOR en Monterrey."""
        evento, _pedido = self._cita(dentro_de_horas=72)
        manana = fields.Datetime.add(
            fields.Datetime.now().replace(hour=0, minute=0, second=0),
            days=4)
        evento.write({'start': manana,
                      'stop': fields.Datetime.add(manana, hours=1)})
        servicio = self._servicio(evento)
        self.assertNotEqual(
            servicio['date_local'], servicio['date'][:10],
            "la fecha UTC y la del cliente son días distintos, y manda la suya")
        self.assertIn(str(int(servicio['date_local'][8:10])),
                      servicio['date_label'])
