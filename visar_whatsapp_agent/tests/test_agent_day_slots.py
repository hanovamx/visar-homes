# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentDaySlots(TransactionCase):
    """`agent_day_slots` viaja con DOS relojes, y no dan lo mismo.

    `start`/`stop` van en UTC naive porque es lo que `agent_hold_slot` y
    `agent_prepare_booking` esperan de vuelta. `start_local`/`stop_local` van en
    la zona de Visar y son los unicos que se le pueden ensenar a una persona.

    La regresion que esto fija: el chat pintaba el UTC tal cual, asi que un
    servicio de las 4 de la tarde se ofrecia como "entre 22:00 y 23:00" —casi
    medianoche— y el cliente reservaba a ciegas.

    Se prueba la conversion sola: montar un arbol de disponibilidad real pide
    tipo de cita, recursos, zona y calendario, y lo que se puede invertir en
    silencio es el sentido de la conversion, no el arbol.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.agent.timezone', 'America/Monterrey')

    def _tz(self):
        import pytz
        return pytz.timezone(self.env['ir.config_parameter'].sudo().get_param(
            'visar.agent.timezone'))

    def test_el_local_resta_el_desfase_no_lo_suma(self):
        """Monterrey es UTC-6: las 22:00 UTC son las 16:00 de aqui, no las 04:00."""
        utc = fields.Datetime.to_datetime('2026-08-21 22:00:00')
        self.assertEqual(
            self.Tools._agent_to_local(utc, self._tz()), '2026-08-21 16:00:00')

    def test_un_slot_sin_hora_no_revienta(self):
        """Nunca lanza: el agente tiene que poder seguir la conversacion."""
        self.assertIsNone(self.Tools._agent_to_local(False, self._tz()))
        self.assertIsNone(self.Tools._agent_to_local(
            fields.Datetime.to_datetime('2026-08-21 22:00:00'), None))
