# -*- coding: utf-8 -*-
"""El ORDEN de los dias que se le ofrecen al cliente (§5.7 del diseno 33).

Hasta el 4-sep-2026 no habia orden: `agent_available_days` recorria el arbol en
orden de calendario y cortaba a los 10. La agrupacion por zona del dia anade una
preferencia —antes el dia que ya tiene trabajo en la zona del cliente que el dia
vacio, porque rellenar una ruta que existe cabe mas servicios que abrir una
nueva— y esa preferencia solo sirve si se aplica **antes** del corte.

Las dos trampas que estas pruebas existen para atrapar:

  * **Ordenar despues de cortar.** Es lo que sale si uno anade un `sort()` al
    final del metodo: reordena los 10 primeros dias del calendario, que es
    exactamente no hacer nada. `test_el_corte_ocurre_DESPUES_de_ordenar` falla.
  * **Ordenar por tier a secas.** Un dia agrupado dentro de tres semanas le gana
    a un dia vacio manana, y al cliente con prisa se le empuja lejos EN SILENCIO
    —no se le explica nada, esta decidido— asi que no tiene forma de pedir algo
    antes. De ahi la guarda de los dos dias mas proximos.

No se toca la red ni el catalogo: se le da a `_agent_rank_days` un arbol de meses
armado a mano, que es exactamente lo que le llega del filtro de traslado.
"""
from datetime import date

from odoo.addons.visar_appointment.models.visar_travel_feasibility import (
    TIER_CON_TRABAJO, TIER_VACIO,
)
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentDayRanking(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools'].sudo()

    def _arbol(self, *dias):
        """Un arbol de meses con los dias dados: (dia_del_mes, tier|None)."""
        semana = []
        for dia_mes, tier in dias:
            entrada = {
                'day': date(2026, 9, dia_mes),
                'slots': [{'datetime': '2026-09-%02d 15:00:00' % dia_mes}],
            }
            if tier is not None:
                entrada['visar_travel_tier'] = tier
            semana.append(entrada)
        return [{'weeks': [semana]}]

    def _fechas(self, dias):
        return [fila['date'] for fila in dias]

    # ------------------------------------------------------------------

    def test_sin_tier_el_orden_es_el_de_siempre(self):
        """Filtro apagado, sin destino o sin paradas: cronologico, como antes."""
        arbol = self._arbol((10, None), (11, None), (12, None))
        self.assertEqual(
            self._fechas(self.Tools._agent_rank_days(arbol)),
            ['2026-09-10', '2026-09-11', '2026-09-12'])

    def test_el_dia_con_trabajo_en_la_zona_sube(self):
        """Es la preferencia entera: rellenar ruta antes que abrir dia."""
        arbol = self._arbol((10, TIER_VACIO), (11, TIER_VACIO),
                            (12, TIER_VACIO), (25, TIER_CON_TRABAJO))
        fechas = self._fechas(self.Tools._agent_rank_days(arbol))
        self.assertEqual(
            fechas[0], '2026-09-25',
            "el dia que ya tiene trabajo en la zona va primero")

    def test_los_DOS_dias_mas_proximos_entran_siempre(self):
        """La guarda. Sin ella, al cliente con prisa se le empuja tres semanas.

        Doce dias agrupados lejanos contra tres vacios cercanos: si mandara solo
        el tier, los tres cercanos se caerian de una lista de 10.
        """
        lejanos = [(dia, TIER_CON_TRABAJO) for dia in range(14, 26)]
        arbol = self._arbol((1, TIER_VACIO), (2, TIER_VACIO), (3, TIER_VACIO),
                            *lejanos)
        fechas = self._fechas(self.Tools._agent_rank_days(arbol))

        self.assertIn('2026-09-01', fechas, "el dia mas proximo no puede faltar")
        self.assertIn('2026-09-02', fechas, "ni el segundo")
        self.assertLessEqual(len(fechas), self.Tools.MAX_AVAILABLE_DAYS)

    def test_el_corte_ocurre_DESPUES_de_ordenar(self):
        """Un dia agrupado fuera del top-10 cronologico tiene que entrar igual.

        Si alguien ordena despues de cortar, el 28 no aparece nunca: quedaba en
        la posicion 13 del calendario.
        """
        vacios = [(dia, TIER_VACIO) for dia in range(1, 13)]
        arbol = self._arbol(*vacios, (28, TIER_CON_TRABAJO))
        fechas = self._fechas(self.Tools._agent_rank_days(arbol))

        self.assertIn(
            '2026-09-28', fechas,
            "el dia agrupado estaba en la posicion 13 del calendario: si no "
            "aparece, el orden se aplico despues del corte y no sirve de nada")

    def test_nunca_se_pasa_del_tope(self):
        arbol = self._arbol(*[(dia, TIER_VACIO) for dia in range(1, 26)])
        self.assertEqual(
            len(self.Tools._agent_rank_days(arbol)),
            self.Tools.MAX_AVAILABLE_DAYS)

    def test_un_arbol_vacio_no_revienta(self):
        self.assertEqual(self.Tools._agent_rank_days([]), [])
        self.assertEqual(self.Tools._agent_rank_days(None), [])

    def test_el_slot_count_sobrevive_al_orden(self):
        arbol = self._arbol((10, TIER_VACIO))
        arbol[0]['weeks'][0][0]['slots'].append({'datetime': '2026-09-10 16:00:00'})
        dias = self.Tools._agent_rank_days(arbol)
        self.assertEqual(dias[0]['slot_count'], 2)
