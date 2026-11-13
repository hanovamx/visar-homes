# -*- coding: utf-8 -*-
"""Días sin servicio (13-nov-2026): festivos y días que Visar no trabaja.

Lo que se fija:

* Los festivos oficiales (LFT art. 74) caen donde dice la ley, y sembrarlos es
  idempotente y respeta lo archivado (= ese día sí se trabaja).
* Un día sin servicio desaparece de la agenda; uno parcial, solo su horario.
* Borrar un festivo oficial lo archiva (si no, el cron lo revive).
"""
from datetime import date

import pytz

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.visar_dia_sin_servicio import festivos_oficiales


@tagged('post_install', '-at_install')
class TestDiaSinServicio(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dia = cls.env['visar.dia.sin.servicio']
        cls.maestro = cls.env['appointment.type'].sudo(
        )._visar_get_master_appointment_type()
        cls.tz = pytz.timezone((cls.maestro and cls.maestro.appointment_tz)
                               or 'America/Monterrey')

    def _slots(self):
        """{fecha 'YYYY-MM-DD': [hora local de inicio, ...]} de la agenda."""
        if not self.maestro:
            self.skipTest("la BD no tiene tipo de cita maestro")
        out = {}
        for mes in self.maestro._get_appointment_slots(self.tz.zone):
            for semana in mes['weeks']:
                for dia in semana:
                    if dia and dia.get('slots'):
                        out[str(dia['day'])] = [s['datetime'][11:16] for s in dia['slots']]
        if len(out) < 3:
            self.skipTest("la agenda no tiene huecos suficientes en esta BD")
        return out

    # --- La ley -------------------------------------------------------------

    def test_festivos_oficiales_2026_y_2030(self):
        dias = dict(festivos_oficiales(2026))
        self.assertIn(date(2026, 2, 2), dias, "primer lunes de febrero")
        self.assertIn(date(2026, 3, 16), dias, "tercer lunes de marzo")
        self.assertIn(date(2026, 11, 16), dias, "tercer lunes de noviembre")
        self.assertIn(date(2026, 12, 25), dias)
        self.assertNotIn(date(2026, 10, 1), dias)
        self.assertIn(date(2030, 10, 1), dict(festivos_oficiales(2030)),
                      "transmisión del Poder Ejecutivo, cada seis años")

    def test_sembrar_es_idempotente_y_respeta_lo_archivado(self):
        self.Dia._visar_cron_festivos()
        navidad = self.Dia.search([('oficial', '=', True),
                                   ('fecha', '=', date(date.today().year, 12, 25))])
        self.assertEqual(len(navidad), 1)
        navidad.unlink()  # «ese día sí trabajamos»
        self.assertTrue(navidad.exists(), "un festivo oficial se archiva, no se borra")
        self.assertFalse(navidad.active)
        self.assertEqual(self.Dia._visar_cron_festivos(), 0)
        self.assertFalse(self.Dia.search([('fecha', '=', navidad.fecha)]),
                         "archivado no se vuelve a sembrar")

    # --- La agenda -----------------------------------------------------------

    def test_un_dia_sin_servicio_desaparece_de_la_agenda(self):
        self.Dia.search([]).write({'active': False})
        antes = self._slots()
        fecha = sorted(antes)[1]
        self.Dia.create({'fecha': fecha, 'motivo': 'No abrimos'})
        despues = self._slots()
        self.assertNotIn(fecha, despues)
        self.assertIn(sorted(antes)[2], despues, "los demás días siguen")

    def test_un_horario_sin_servicio_deja_el_resto_del_dia(self):
        self.Dia.search([]).write({'active': False})
        antes = self._slots()
        fecha = next((f for f, horas in sorted(antes.items())
                      if any(h < '12:00' for h in horas)
                      and any(h >= '12:00' for h in horas)), None)
        if not fecha:
            self.skipTest("ningún día con huecos antes y después de las 12")
        self.Dia.create({'fecha': fecha, 'motivo': 'Medio día',
                         'todo_el_dia': False, 'hora_desde': 12.0, 'hora_hasta': 24.0})
        horas = self._slots().get(fecha, [])
        self.assertTrue(horas, "la mañana sigue")
        self.assertFalse([h for h in horas if h >= '12:00'], horas)

    def test_cuenta_las_citas_que_ya_estaban(self):
        dia = self.Dia.create({'fecha': date(2031, 3, 3), 'motivo': 'Prueba'})
        self.assertEqual(dia.citas_count, 0)
        self.assertEqual(dia.dia_semana, 'lunes')
