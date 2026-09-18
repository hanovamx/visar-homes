# -*- coding: utf-8 -*-
"""La estructura del plan: periodos, estados y el candado del cierre.

Copia deliberada de la forma del módulo Enterprise `sale_commission` (vigencia,
estado aprobable, periodos autogenerados, tabla de metas) con empleados en vez de
usuarios. Lo que NO copia es que el resultado sea una vista SQL que siempre
recalcula: aquí se guarda y se puede cerrar.
"""
from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestEstructuraDelPlan(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.empleado = cls.env['hr.employee'].create({'name': 'Vendedora sin usuario'})

    def _plan(self, **vals):
        return self.env['visar.commission.plan'].create(dict({
            'name': 'Plan de prueba',
            'date_from': date(2026, 1, 1),
            'date_to': date(2026, 12, 31),
        }, **vals))

    # ------------------------------------------------------------------
    def test_los_periodos_se_generan_solos(self):
        self.assertEqual(len(self._plan(periodicity='mes').period_ids), 12)
        self.assertEqual(len(self._plan(periodicity='trimestre').period_ids), 4)
        self.assertEqual(len(self._plan(periodicity='anio').period_ids), 1)
        self.assertEqual(len(self._plan(periodicity='quincena').period_ids), 24,
                         "quincenal es 1-15 y 16-fin de mes, no cada 14 dias")

    def test_la_quincena_corta_como_la_nomina(self):
        plan = self._plan(periodicity='quincena')
        primera, segunda = plan.period_ids[0], plan.period_ids[1]
        self.assertEqual((primera.date_from, primera.date_to),
                         (date(2026, 1, 1), date(2026, 1, 15)))
        self.assertEqual((segunda.date_from, segunda.date_to),
                         (date(2026, 1, 16), date(2026, 1, 31)))
        self.assertIn('ene 2026', primera.name)

    def test_un_plan_que_empieza_a_media_quincena_no_desalinea_las_demas(self):
        """Nomina corta por calendario: el primer tramo se recorta, no se corre."""
        plan = self._plan(periodicity='mes', date_from=date(2026, 3, 10),
                          date_to=date(2026, 5, 31))
        tramos = [(p.date_from, p.date_to) for p in plan.period_ids]
        self.assertEqual(tramos, [
            (date(2026, 3, 10), date(2026, 3, 31)),
            (date(2026, 4, 1), date(2026, 4, 30)),
            (date(2026, 5, 1), date(2026, 5, 31)),
        ])

    def test_cambiar_la_periodicidad_regenera_los_periodos(self):
        plan = self._plan(periodicity='mes')
        plan.periodicity = 'trimestre'
        self.assertEqual(len(plan.period_ids), 4)

    def test_un_periodo_cerrado_sobrevive_al_cambio_de_periodicidad(self):
        """Un periodo cerrado es dinero autorizado: no se borra ni se mueve."""
        plan = self._plan(periodicity='mes')
        enero = plan.period_ids[0]
        enero.state = 'cerrado'

        plan.periodicity = 'trimestre'

        self.assertTrue(enero.exists(), "el periodo cerrado no se puede borrar")
        self.assertEqual(enero.state, 'cerrado')
        self.assertNotIn((date(2026, 1, 1), date(2026, 3, 31)),
                         [(p.date_from, p.date_to) for p in plan.period_ids],
                         "el trimestre que lo pisaria no se genera")

    # ------------------------------------------------------------------
    def test_no_se_aprueba_un_plan_sin_regla_definida(self):
        """Es el estado real de hoy: negocio todavia no define la regla."""
        plan = self._plan()
        self.assertTrue(plan.falta_configurar)
        with self.assertRaises(UserError):
            plan.action_approve()

    def test_con_empleado_y_regla_ya_se_aprueba(self):
        plan = self._plan()
        plan.write({
            'employee_ids': [(0, 0, {'employee_id': self.empleado.id})],
            'rule_ids': [(0, 0, {'base': 'importe_vendido', 'rate': 0.05})],
        })
        plan.action_approve()
        self.assertEqual(plan.state, 'approved')
        self.assertFalse(plan.falta_configurar)

    def test_un_plan_por_metas_exige_su_tabla(self):
        plan = self._plan(mode='meta')
        plan.write({
            'employee_ids': [(0, 0, {'employee_id': self.empleado.id})],
            'rule_ids': [(0, 0, {'base': 'importe_vendido', 'rate': 1.0})],
        })
        self.assertEqual(len(plan.curve_ids), 3,
                         "la FORMA de la tabla se siembra; los importes los pone negocio")
        self.assertTrue(plan.falta_configurar, "sin importes en la tabla no paga nada")
        with self.assertRaises(UserError):
            plan.action_approve()

    def test_un_plan_en_borrador_no_calcula(self):
        plan = self._plan()
        with self.assertRaises(UserError):
            plan.action_calcular()

    def test_no_se_marca_pagado_sin_cerrar_primero(self):
        plan = self._plan()
        with self.assertRaises(UserError):
            plan.period_ids[0].action_marcar_pagado()

    def test_lo_pagado_no_se_reabre_de_un_clic(self):
        plan = self._plan()
        periodo = plan.period_ids[0]
        periodo.state = 'pagado'
        with self.assertRaises(UserError):
            periodo.action_reabrir()
