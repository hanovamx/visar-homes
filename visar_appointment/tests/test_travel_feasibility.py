# -*- coding: utf-8 -*-
"""El predicado de factibilidad de ruta (diseño 33 §5, decisiones 7/9/14).

Lo que se fija aquí es la aritmética, que es donde está el riesgo real: los 20
minutos son un **presupuesto entre paradas**, no un radio de servicio, y quien
lea el código sin el diseño delante tiene todas las papeletas de convertirlo en
un radio — que es la versión que parece más segura y en realidad rechaza
horarios perfectamente ofrecibles.

Ninguna prueba toca la red: `_visar_mapbox_matrix` se parchea. Una prueba que
depende de Mapbox no es una prueba del predicado, es una prueba de Mapbox.

Hoy hay **un** técnico usable, así que `require='all'` y `require='any'` dan lo
mismo en producción. Por eso cada uno lleva su prueba: la realidad todavía no
distingue las dos reglas, y el día que entre el segundo técnico ya será tarde
para descubrir que estaban confundidas.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

_TZ = 'America/Monterrey'
_SERVICE = 'odoo.addons.visar_base.models.visar_travel.VisarMapboxService'

# Destino y dos paradas cualesquiera: las coordenadas solo tienen que ser
# distintas entre sí, porque los tiempos los pone el mock.
_DEST = (25.6866, -100.3161)
_STOP_A = (25.7000, -100.3000)
_STOP_B = (25.6500, -100.4000)


def _codigo_sin_docstring(source):
    """El fuente de una funcion, sin su docstring. Para las pruebas de fuente.

    Devuelve codigo re-generado desde el AST: sin docstring y sin comentarios,
    que es justo lo que estas pruebas quieren mirar. Prohibir una cadena en el
    fuente ENTERO prohibe tambien nombrarla para advertir de ella.
    """
    import ast
    import textwrap

    arbol = ast.parse(textwrap.dedent(source))
    funcion = arbol.body[0]
    cuerpo = funcion.body
    if (cuerpo and isinstance(cuerpo[0], ast.Expr)
            and isinstance(cuerpo[0].value, ast.Constant)
            and isinstance(cuerpo[0].value.value, str)):
        funcion.body = cuerpo[1:]
    return ast.unparse(funcion)


@tagged('post_install', '-at_install')
class TestTravelFeasibility(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AptType = cls.env['appointment.type'].sudo()
        cls.tz = pytz.timezone(_TZ)
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.travel.enabled', '1')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stops(self, *tramos):
        """[(inicio, fin, coords)] en UTC naive, desde horas locales."""
        base = datetime(2026, 9, 1)
        salida = []
        for hora_ini, hora_fin, coords in tramos:
            start = self.tz.localize(
                base.replace(hour=hora_ini)).astimezone(pytz.utc).replace(tzinfo=None)
            stop = self.tz.localize(
                base.replace(hour=hora_fin)).astimezone(pytz.utc).replace(tzinfo=None)
            salida.append((start, stop, coords))
        return salida

    def _slot(self, hora_ini, hora_fin):
        base = datetime(2026, 9, 1)
        start = self.tz.localize(
            base.replace(hour=hora_ini)).astimezone(pytz.utc).replace(tzinfo=None)
        stop = self.tz.localize(
            base.replace(hour=hora_fin)).astimezone(pytz.utc).replace(tzinfo=None)
        return start, stop

    def _fits(self, stops, slot, durations, budget=20):
        start, stop = slot
        return self.AptType._visar_travel_slot_fits(
            stops, start, stop, durations, budget)

    # ------------------------------------------------------------------
    # La aritmética: 20 + (T − E)
    # ------------------------------------------------------------------

    def test_pegados_el_presupuesto_es_de_veinte_minutos(self):
        """`T = E`: es el caso que aprieta, y son 20 justos."""
        stops = self._stops((9, 10, _STOP_A))
        slot = self._slot(10, 11)          # empieza justo al acabar la anterior

        self.assertTrue(self._fits(stops, slot, {0: (15, 15)}),
                        "15 min de trayecto caben en un presupuesto de 20")
        self.assertFalse(self._fits(stops, slot, {0: (25, 25)}),
                         "25 min NO caben: se comería el traslado de la otra cita")

    def test_el_hueco_por_delante_se_suma_al_presupuesto(self):
        """La decisión 14 en una línea: con la mañana libre, 40 min sí se ofrecen.

        Es lo contrario de un radio de servicio, y lo que más fácil se pierde si
        alguien "simplifica" esto a un tope duro.
        """
        stops = self._stops((9, 10, _STOP_A))
        slot = self._slot(11, 12)          # una hora de hueco por delante

        self.assertTrue(
            self._fits(stops, slot, {0: (40, 40)}),
            "20 de presupuesto + 60 de hueco: un trayecto de 40 min cabe de sobra")

    def test_un_trayecto_mayor_que_presupuesto_mas_hueco_no_cabe(self):
        stops = self._stops((9, 10, _STOP_A))
        slot = self._slot(10, 11)
        self.assertFalse(self._fits(stops, slot, {0: (21, 21)}))

    # ------------------------------------------------------------------
    # Los bordes del día (decisión 9)
    # ------------------------------------------------------------------

    def test_el_borde_anterior_del_dia_no_se_restringe(self):
        """Antes de la primera parada no hay traslado ajeno que proteger.

        El técnico sale de donde salga; la primera parada del día no le quita el
        traslado a nadie. Por eso NO hace falta geocodificar "Visar Home".
        """
        stops = self._stops((14, 15, _STOP_A))
        slot = self._slot(8, 9)            # mucho antes de todo

        self.assertTrue(self._fits(stops, slot, {0: (90, 90)}),
                        "Hora y media de viaje y aun así se ofrece: no hay cita "
                        "anterior a la que quitarle nada")

    def test_el_borde_posterior_tampoco_se_restringe(self):
        stops = self._stops((9, 10, _STOP_A))
        slot = self._slot(16, 17)
        self.assertTrue(self._fits(stops, slot, {0: (90, 90)}))

    def test_en_medio_se_validan_LAS_DOS_direcciones(self):
        stops = self._stops((9, 10, _STOP_A), (13, 14, _STOP_B))
        slot = self._slot(11, 12)

        # Llegar bien pero no poder salir a tiempo tampoco vale.
        self.assertTrue(self._fits(stops, slot, {0: (15, 15), 1: (15, 15)}))
        self.assertFalse(
            self._fits(stops, slot, {0: (15, 15), 1: (200, 200)}),
            "Se llega, pero se rompe el traslado de la cita siguiente")

    # ------------------------------------------------------------------
    # Degradar, nunca bloquear (§5.4)
    # ------------------------------------------------------------------

    def test_una_parada_sin_coordenadas_no_impone_restriccion(self):
        """1 de cada 4 direcciones no está geocodificada: es la rama normal."""
        stops = self._stops((9, 10, None))
        slot = self._slot(10, 11)
        self.assertTrue(self._fits(stops, slot, {}))

    def test_un_dia_sin_paradas_pasa_trivialmente(self):
        self.assertTrue(self._fits([], self._slot(10, 11), {}))

    def test_solaparse_con_un_compromiso_no_cabe(self):
        """No debería llegar aquí (la capacidad ya lo excluye), pero no se adivina."""
        stops = self._stops((10, 11, _STOP_A))
        self.assertFalse(self._fits(stops, self._slot(10, 11), {0: (5, 5)}))

    # ------------------------------------------------------------------
    # Coste: una llamada por (día, técnico)
    # ------------------------------------------------------------------

    def test_un_dia_sin_paradas_no_gasta_ni_una_llamada(self):
        """El ahorro que el diseño no menciona y que hace esto barato.

        Con mediana de 2.5 paradas/día sobre 24 días con trabajo de cada 60, la
        mayoría de los días candidatos cuestan CERO.
        """
        with patch('%s._visar_mapbox_matrix' % _SERVICE) as matrix:
            durations = self.AptType._visar_travel_durations(
                [], _DEST, {'calls': 0, 'max_calls': 12}, self.tz)
            self.assertEqual(durations, {})
            matrix.assert_not_called()

    def test_mapbox_caido_no_pierde_horarios_y_corta_la_corrida(self):
        """Un token muerto no puede volverse 30 timeouts al pintar un mes."""
        stops = self._stops((9, 10, _STOP_A))
        budget = {'calls': 0, 'max_calls': 12}
        with patch('%s._visar_mapbox_matrix' % _SERVICE, return_value=None) as matrix:
            self.assertEqual(
                self.AptType._visar_travel_durations(stops, _DEST, budget, self.tz), {})
            self.assertTrue(budget['degraded'], "interruptor de circuito")
            # Segunda llamada: ya no se intenta.
            self.AptType._visar_travel_durations(stops, _DEST, budget, self.tz)
            self.assertEqual(matrix.call_count, 1)

    def test_el_tope_de_llamadas_degrada_sin_bloquear(self):
        stops = self._stops((9, 10, _STOP_A))
        budget = {'calls': 5, 'max_calls': 5}
        with patch('%s._visar_mapbox_matrix' % _SERVICE) as matrix:
            self.assertEqual(
                self.AptType._visar_travel_durations(stops, _DEST, budget, self.tz), {})
            matrix.assert_not_called()
            self.assertTrue(budget['capped'])

    def test_la_cache_evita_la_segunda_llamada(self):
        stops = self._stops((9, 10, _STOP_A))
        fake_matrix = [[0, 600], [600, 0]]      # 10 min en los dos sentidos
        with patch('%s._visar_mapbox_matrix' % _SERVICE,
                   return_value=fake_matrix) as matrix:
            primera = self.AptType._visar_travel_durations(
                stops, _DEST, {'calls': 0, 'max_calls': 12}, self.tz)
            self.assertEqual(primera[0], (10, 10))
            segunda = self.AptType._visar_travel_durations(
                stops, _DEST, {'calls': 0, 'max_calls': 12}, self.tz)
            self.assertEqual(segunda[0], (10, 10))
            self.assertEqual(matrix.call_count, 1,
                             "la segunda sale de la caché")

    def test_los_segundos_se_redondean_hacia_arriba(self):
        """El redondeo va en contra de ofrecer: prometer y no llegar es peor."""
        stops = self._stops((9, 10, _STOP_A))
        with patch('%s._visar_mapbox_matrix' % _SERVICE,
                   return_value=[[0, 601], [601, 0]]):
            durations = self.AptType._visar_travel_durations(
                stops, _DEST, {'calls': 0, 'max_calls': 12}, self.tz)
        self.assertEqual(durations[0], (11, 11), "601 s son 11 min, no 10")

    # ------------------------------------------------------------------
    # Tráfico histórico: `depart_at` y la franja horaria
    # ------------------------------------------------------------------

    def _depart_at(self, matrix_mock, call=0):
        return matrix_mock.call_args_list[call].kwargs.get('depart_at')

    def test_se_pregunta_por_la_hora_de_la_parada_no_por_ahora(self):
        """Sin `depart_at` cotizaríamos velocidades típicas sin hora del día.

        Y la diferencia entre las 8:00 y las 11:00 en Monterrey es del mismo
        tamaño que el presupuesto de 20 min que estamos midiendo.
        """
        stops = self._stops((9, 10, _STOP_A))
        with patch('%s._visar_mapbox_matrix' % _SERVICE,
                   return_value=[[0, 600], [600, 0]]) as matrix:
            self.AptType._visar_travel_durations(
                stops, _DEST, {'calls': 0, 'max_calls': 12}, self.tz)

        esperado = self.tz.localize(
            datetime(2026, 9, 1, 9, 30)).astimezone(pytz.utc).replace(tzinfo=None)
        self.assertEqual(
            self._depart_at(matrix), esperado,
            "El punto MEDIO de la parada: los dos trayectos salen a media hora "
            "de él, y así una sola franja sirve para los dos sentidos")

    def test_dos_franjas_de_trafico_en_un_dia_cuestan_dos_llamadas(self):
        """`depart_at` es de la PETICIÓN, no de la coordenada.

        Un técnico con trabajo a las 9:00 y a las 17:00 vive dos realidades de
        tráfico. Meterlas en la misma llamada sería cotizar una de las dos con la
        hora de la otra — barato y equivocado.
        """
        stops = self._stops((9, 10, _STOP_A), (17, 18, _STOP_B))
        with patch('%s._visar_mapbox_matrix' % _SERVICE,
                   return_value=[[0, 600], [600, 0]]) as matrix:
            durations = self.AptType._visar_travel_durations(
                stops, _DEST, {'calls': 0, 'max_calls': 12}, self.tz)

        self.assertEqual(matrix.call_count, 2, "una llamada por franja")
        self.assertEqual(durations[0], (10, 10))
        self.assertEqual(durations[1], (10, 10))
        horas = sorted(d.hour for d in (self._depart_at(matrix, 0),
                                        self._depart_at(matrix, 1)))
        self.assertNotEqual(horas[0], horas[1],
                            "cada llamada pregunta por SU hora")

    def test_un_dia_en_una_sola_franja_sigue_costando_una_llamada(self):
        """El costo no se multiplica por slot: la hora sale de la PARADA.

        Es lo que mantiene vivo el control de costo del §5.3. Con mediana de 2.5
        paradas al día, la mayoría de los días siguen siendo una sola llamada.
        """
        stops = self._stops((9, 10, _STOP_A), (10, 11, _STOP_B))
        with patch('%s._visar_mapbox_matrix' % _SERVICE,
                   return_value=[[0, 600, 600], [600, 0, 0], [600, 0, 0]]) as matrix:
            self.AptType._visar_travel_durations(
                stops, _DEST, {'calls': 0, 'max_calls': 12}, self.tz)
        self.assertEqual(matrix.call_count, 1)

    def test_la_cache_no_sirve_una_hora_por_otra(self):
        """La franja va en la clave, o cachear sería servir tráfico ajeno."""
        Cache = self.env['visar.travel.cache'].sudo()
        manana = Cache._visar_travel_key(_DEST, _STOP_A, 'D1H09')
        tarde = Cache._visar_travel_key(_DEST, _STOP_A, 'D1H17')
        self.assertNotEqual(manana, tarde)
        self.assertNotEqual(manana, Cache._visar_travel_key(_DEST, _STOP_A))
        self.assertNotEqual(
            manana, Cache._visar_travel_key(_STOP_A, _DEST, 'D1H09'),
            "y sigue siendo direccional: A→B y B→A no duran lo mismo")

    def test_el_tope_a_mitad_de_dia_conserva_lo_ya_cacheado(self):
        """Topar no tira lo que ya se sabía: filtra con la mitad buena.

        Antes el tope devolvía `{}` para el día entero aunque una de las franjas
        estuviera cacheada y no costara nada. Una parada sin resolver no impone
        restricción, así que conservarla es estrictamente mejor.
        """
        manana = self._stops((9, 10, _STOP_A))
        with patch('%s._visar_mapbox_matrix' % _SERVICE,
                   return_value=[[0, 600], [600, 0]]):
            self.AptType._visar_travel_durations(
                manana, _DEST, {'calls': 0, 'max_calls': 12}, self.tz)

        # Ahora un día con esa parada Y otra de la tarde, ya sin presupuesto.
        completo = self._stops((9, 10, _STOP_A), (17, 18, _STOP_B))
        budget = {'calls': 12, 'max_calls': 12}
        with patch('%s._visar_mapbox_matrix' % _SERVICE) as matrix:
            durations = self.AptType._visar_travel_durations(
                completo, _DEST, budget, self.tz)
            matrix.assert_not_called()

        self.assertEqual(durations, {0: (10, 10)},
                         "la franja cacheada sobrevive; la otra no impone nada")
        self.assertTrue(budget['capped'])

    def test_una_salida_pasada_no_lleva_depart_at(self):
        """Mapbox rechaza `depart_at` en el pasado, y pasa de verdad.

        La ventana de paradas lleva un día de margen a cada lado, así que una
        parada de esta mañana entra en el barrido de esta tarde. Se degrada a
        velocidades típicas: peor, pero no falso. Empujarlo a "ahora" sería
        cotizar el tráfico de una hora que no es la de la cita.
        """
        Mapbox = self.env['visar.mapbox.service']
        ayer = datetime.now() - timedelta(days=1)
        manana = datetime.now() + timedelta(days=1)
        self.assertIsNone(Mapbox._visar_mapbox_depart_at(ayer))
        self.assertIsNone(Mapbox._visar_mapbox_depart_at(None))
        self.assertTrue(
            Mapbox._visar_mapbox_depart_at(manana).endswith('Z'),
            "con la Z explícita: sin ella Mapbox lo lee como hora local")

    # ------------------------------------------------------------------
    # El flag y el árbol completo
    # ------------------------------------------------------------------

    def test_el_flag_apagado_devuelve_el_arbol_intacto(self):
        months = [{'id': 1, 'weeks': [[{'slots': [{'datetime': '2026-09-01 10:00:00'}]}]]}]
        self.env['ir.config_parameter'].sudo().set_param('visar.travel.enabled', '0')
        try:
            salida = self.AptType._visar_filter_slots_travel(
                self.AptType.browse(), months, _TZ, _DEST)
            self.assertIs(salida, months)
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'visar.travel.enabled', '1')

    def test_sin_destino_el_arbol_sale_intacto(self):
        months = [{'id': 1, 'weeks': [[{'slots': [{'datetime': '2026-09-01 10:00:00'}]}]]}]
        self.assertIs(
            self.AptType._visar_filter_slots_travel(
                self.AptType.browse(), months, _TZ, None),
            months)

    # ------------------------------------------------------------------
    # La carga sale de booking.line, NUNCA de project.task
    # ------------------------------------------------------------------

    def test_la_carga_sale_de_booking_line_no_de_project_task(self):
        """§5.3.2: `project.task.user_ids` no es dato fiable.

        En la base real hay 83 tareas activas asignadas a *admin*, 4 a
        `__system__` y 61 a nadie. Si el predicado leyera de ahí, diría que el
        técnico tiene el día libre y ofrecería horarios imposibles.
        """
        import inspect

        from odoo.addons.visar_appointment.models import visar_travel_feasibility

        source = inspect.getsource(
            visar_travel_feasibility.AppointmentType._visar_travel_stops_by_day)
        self.assertIn('appointment.booking.line', source)

        # Se mira el CODIGO, no la prosa. El docstring de ese metodo advierte por
        # escrito de que NO se lea `project.task.user_ids`, asi que buscar la
        # cadena en el fuente entero hacia saltar la guarda con su propio aviso:
        # la prueba salia roja teniendo delante el codigo correcto. Se quita el
        # docstring por AST y no por texto, porque `inspect.getdoc` normaliza la
        # sangria y ya no casa con el fuente crudo.
        self.assertNotIn('user_ids', _codigo_sin_docstring(source))

    def test_el_bloque_sale_de_appointment_duration_no_del_codigo(self):
        """Decisión 7: los 60 min no están horneados.

        La parte de servicio se deriva del tipo de cita; lo único configurado es
        el traslado. Si alguien hornea 40 en el código, esta prueba se cae.
        """
        import inspect

        from odoo.addons.visar_appointment.models import visar_travel_feasibility

        source = inspect.getsource(visar_travel_feasibility)
        self.assertIn('appointment_duration', source)
        self.assertEqual(
            self.AptType._visar_travel_minutes(), 20,
            "el traslado por defecto son 20 min (decisión 7)")
