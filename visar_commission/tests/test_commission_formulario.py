# -*- coding: utf-8 -*-
"""El plan se guarda desde el FORMULARIO, no solo desde el ORM.

Las pruebas de estructura crean el plan con `create()`, donde los periodos los
genera el servidor y nunca pasan por la vista. En pantalla es otro camino: el
onchange genera los periodos en el navegador y, al guardar, el cliente **no
envía** los campos de solo lectura salvo que la vista los marque `force_save`.
`name`, `date_from` y `date_to` del periodo son de solo lectura, así que el plan
nuevo no se podía guardar ("Falta el valor requerido para el campo 'Periodo'",
18-sep-2026). `Form` reproduce esa regla del cliente.
"""
from datetime import date

from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPlanDesdeElFormulario(TransactionCase):

    def _guardar(self, periodicidad):
        with Form(self.env['visar.commission.plan']) as form:
            form.name = 'Plan desde pantalla'
            form.date_from = date(2026, 1, 1)
            form.date_to = date(2026, 12, 31)
            form.periodicity = periodicidad
        return form.record

    def test_un_plan_nuevo_se_guarda_con_sus_periodos(self):
        esperados = {'quincena': 24, 'mes': 12, 'trimestre': 4, 'anio': 1}
        for periodicidad, cuantos in esperados.items():
            with self.subTest(periodicidad=periodicidad), self.env.cr.savepoint():
                plan = self._guardar(periodicidad)
                self.assertEqual(len(plan.period_ids), cuantos)
                self.assertTrue(all(plan.period_ids.mapped('name')))
                self.assertEqual(plan.period_ids[0].date_from, date(2026, 1, 1))
                self.assertEqual(plan.period_ids[-1].date_to, date(2026, 12, 31))

    def test_cambiar_la_periodicidad_en_pantalla_rehace_los_periodos(self):
        plan = self._guardar('mes')
        with Form(plan) as form:
            form.periodicity = 'trimestre'
        self.assertEqual(len(plan.period_ids), 4)
        self.assertEqual(plan.period_ids.mapped('name')[0], '2026 T1')
