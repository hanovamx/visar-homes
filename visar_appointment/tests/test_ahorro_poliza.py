# -*- coding: utf-8 -*-
"""El ahorro se compara contra las MISMAS visitas (25-sep-2026).

Visar reportó que la etiqueta "Ahorras $…" solo salía en la suscripción mensual.
La causa: se comparaba el total del PERIODO contra UNA visita de contado. Eso
solo cuadra cuando el periodo trae una visita; en la semestral (6 visitas) y la
anual (12) la resta daba negativo, el `max(0, …)` la dejaba en cero y la etiqueta
desaparecía — aunque el descuento por visita fuera el mismo 5% en las tres.

Lo que se protege aquí: que el ahorro se mida sobre la misma cantidad de servicio
en los dos lados de la comparación, que el porcentaje salga sobre esa misma base,
y que un plan sin descuento siga diciendo cero en vez de inventarse una cifra.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAhorroDePoliza(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Flow = cls.env['appointment.type']
        Plan = cls.env['sale.subscription.plan']
        # Un mes, seis meses y un año, todos con visita mensual: 1, 6 y 12 visitas
        # por periodo. Es la forma de los planes reales de producción.
        cls.mensual = Plan.create({
            'name': "Prueba mensual", 'billing_period_unit': 'month',
            'billing_period_value': 1, 'visar_visit_interval_months': 1})
        cls.semestral = Plan.create({
            'name': "Prueba semestral", 'billing_period_unit': 'month',
            'billing_period_value': 6, 'visar_visit_interval_months': 1})
        cls.anual = Plan.create({
            'name': "Prueba anual", 'billing_period_unit': 'year',
            'billing_period_value': 1, 'visar_visit_interval_months': 1})

    def test_el_plan_sabe_cuantas_visitas_cubre_su_periodo(self):
        self.assertEqual(self.mensual._visar_visits_per_period(), 1)
        self.assertEqual(self.semestral._visar_visits_per_period(), 6)
        self.assertEqual(self.anual._visar_visits_per_period(), 12)

    def _ofertas(self, contado, por_visita):
        """Ofertas con el precio de contado y el de póliza fijados.

        Se sustituye la cotización porque lo que se prueba es la ARITMÉTICA del
        ahorro, no el motor de precios por zona (que tiene sus propias pruebas).
        """
        planes = self.mensual + self.semestral + self.anual
        zone = self.env['visar.zone'].search([], limit=1)
        master = self.Flow._visar_get_master_appointment_type() or self.Flow.browse()
        moneda = self.env.company.currency_id.id

        def fake_quote(self_apt, items, zona, plan=None, **kw):
            visitas = plan._visar_visits_per_period() if plan else 1
            recurrente = (por_visita * visitas) if plan else contado
            return {'lines': [], 'total': recurrente, 'currency_id': moneda,
                    'zone_name': 'Z', 'plan': plan or False, 'periods': 1,
                    'recurring_total': recurrente, 'addons_total': 0.0,
                    'upfront_service_total': recurrente,
                    'upfront_total': recurrente}

        booking = {'mode': 'wizard', 'zone_id': zone.id, 'items': [{'x': 1}],
                   'master_appointment_type_id': master.id, 'selections': {}}
        with patch.object(type(self.Flow), '_visar_wizard_poliza_context',
                          return_value=(zone, master, planes)), \
             patch.object(type(self.Flow), '_visar_quote_booking', fake_quote), \
             patch.object(type(self.Flow), '_visar_wizard_has_roedores',
                          return_value=False):
            return {o['name']: o for o in self.Flow._visar_wizard_poliza_offers(booking)}

    def test_todos_los_planes_muestran_su_ahorro(self):
        """690 de contado contra 655.50 por visita: 5% en las tres, y en pesos
        proporcional a las visitas que cubre el periodo."""
        ofertas = self._ofertas(contado=690.0, por_visita=655.50)

        self.assertAlmostEqual(ofertas["Prueba mensual"]['saving'], 34.50, 2)
        self.assertAlmostEqual(ofertas["Prueba semestral"]['saving'], 207.0, 2)
        self.assertAlmostEqual(ofertas["Prueba anual"]['saving'], 414.0, 2,
                               "antes salía 0 y la etiqueta desaparecía")
        for oferta in ofertas.values():
            self.assertTrue(oferta['saving'], "%s sin ahorro" % oferta['name'])

    def test_el_porcentaje_es_el_mismo_en_todos(self):
        """El descuento por visita es el mismo, así que el porcentaje también:
        sacarlo sobre una sola visita daría cifras disparatadas en los largos."""
        ofertas = self._ofertas(contado=690.0, por_visita=655.50)

        for oferta in ofertas.values():
            self.assertAlmostEqual(oferta['saving_percent'], 5.0, 1, oferta['name'])

    def test_el_periodo_declara_cuantas_visitas_cubre(self):
        ofertas = self._ofertas(contado=690.0, por_visita=655.50)

        self.assertEqual(ofertas["Prueba anual"]['visits_per_period'], 12)
        self.assertEqual(ofertas["Prueba mensual"]['visits_per_period'], 1)

    def test_un_plan_sin_descuento_no_inventa_ahorro(self):
        ofertas = self._ofertas(contado=690.0, por_visita=690.0)

        for oferta in ofertas.values():
            self.assertEqual(oferta['saving'], 0.0, oferta['name'])
            self.assertEqual(oferta['saving_percent'], 0.0, oferta['name'])

    def test_un_plan_mas_caro_que_el_contado_no_sale_en_negativo(self):
        ofertas = self._ofertas(contado=690.0, por_visita=800.0)

        for oferta in ofertas.values():
            self.assertEqual(oferta['saving'], 0.0, oferta['name'])

    def test_el_agente_dice_el_ahorro_de_los_planes_largos(self):
        """Mismo cálculo para web y chat: la descripción del agente sale de estos
        mismos campos, así que arreglar el cálculo lo arregla en los dos sitios."""
        ofertas = self._ofertas(contado=690.0, por_visita=655.50)

        texto = self.Flow._visar_wizard_poliza_description(ofertas["Prueba anual"])
        self.assertTrue(texto.startswith("Ahorro del 5%"), texto)
