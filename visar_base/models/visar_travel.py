# -*- coding: utf-8 -*-
"""Transporte a Mapbox y caché de tiempos de viaje, compartidos.

Vive en `visar_base` por una razón de dependencias, no de gusto: la integración
con Mapbox nació en `visar_field_app` (geocodificar direcciones y estimar el ETA
del técnico que va en camino), pero quien la necesita ahora es
`visar_appointment`, para decidir qué horarios se pueden ofrecer. Y esos dos
módulos **no se conocen**: son hermanos, los dos cuelgan de `visar_fsm`. El único
antepasado común es este módulo.

No hace falta añadir `base_geolocalize` al manifiesto: aquí solo se piden y se
devuelven coordenadas ya resueltas. El token es un `ir.config_parameter`
(`web_map.token_map_box`, el MISMO que usa el mapa nativo y la app de campo), y
leerlo no arrastra ninguna dependencia.

**Nada de aquí lanza nunca.** Una falla de red, un token caducado o un destino sin
geocodificar devuelven `None`, y quien llama decide qué hacer con eso. En el caso
del agendado la decisión ya está tomada y es "ofrecer el horario igual": una falla
de geocodificación no puede costar una reserva (diseño 33 §5.4), y con el 77.6%
de direcciones geocodificadas esa rama no es excepcional — corre en 1 de cada 4.
"""
import logging
import math

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# El token del mapa nativo de Odoo. Si el staff configura uno en Ajustes, todo
# —mapa, app de campo y esto— lo reutiliza.
MAPBOX_TOKEN_PARAM = 'web_map.token_map_box'

_MATRIX_URL = ('https://api.mapbox.com/directions-matrix/v1/mapbox/'
               '%(profile)s/%(coords)s')
_GEOCODE_URL = 'https://api.mapbox.com/geocoding/v5/mapbox.places/%s.json'

# Cuántas coordenadas admite una petición de Matrix. El perfil `driving` admite
# 25; `driving-traffic` solo 10, y el pico medido de paradas de un técnico es **10**
# (Pedro Martínez, 11-ago-2026) — que con el destino son 11, POR ENCIMA de su tope.
# Ver `_visar_travel_profile`.
MATRIX_MAX_COORDS = 25

# Parámetros de configuración (todos opcionales, con default sensato).
TRAVEL_PROFILE_PARAM = 'visar.travel.profile'
TRAVEL_TIMEOUT_PARAM = 'visar.travel.timeout'
TRAVEL_PRECISION_PARAM = 'visar.travel.coord_precision'
TRAVEL_CACHE_DAYS_PARAM = 'visar.travel.cache_days'
# Ancho de la franja horaria con la que se agrupa el tráfico histórico.
TRAVEL_BUCKET_HOURS_PARAM = 'visar.travel.depart_bucket_hours'
# Si se manda la hora de salida a Matrix. `depart_at` es BETA y exige alta previa
# por formulario; sin ella Mapbox devuelve 422 y tira la petición ENTERA, no solo
# el parámetro. Ver `_visar_depart_at_mode`.
DEPART_AT_PARAM = 'visar.travel.depart_at'
DEPART_AT_AUTO = 'auto'
# APAGADO por defecto: la cuenta no tiene la beta, y encendido cada llamada paga
# un 422 antes de reintentar. Ver `_visar_depart_at_mode`.
DEPART_AT_DEFAULT = '0'

DEFAULT_PROFILE = 'driving'
DEFAULT_TIMEOUT = 10
DEFAULT_PRECISION = 4
DEFAULT_CACHE_DAYS = 30
# TRES horas, y el número está medido, no elegido por gusto. La franja es la
# unidad de cobro: una llamada a Matrix por franja con paradas.
#
#   ancho    día mediano (2.5 paradas)    día pico (10 paradas)
#     1 h              3 llamadas              9 llamadas
#     3 h              2 llamadas              4 llamadas
#     6 h              2 llamadas              2 llamadas
#
# Con una hora, dos citas seguidas (9-10 y 10-11) caen en franjas distintas y se
# pagan por separado, que es el caso MÁS común y el que menos lo merece: el
# tráfico no cambia entre las 9:30 y las 10:30. Con seis se pierde justo lo que
# se vino a buscar, porque la mañana entera se cotiza a una sola hora.
#
# Tres deja los bordes donde de verdad están —6-9 pico de mañana, 9-12, 12-15,
# 15-18 pico de tarde— y colapsa las citas consecutivas. El ancho es parámetro
# porque el número bueno sale de medir en servidor (V7), no de este comentario.
DEFAULT_BUCKET_HOURS = 3
# Los puntos geocodificados caducan mucho más tarde: una dirección no se mueve.
DEFAULT_POINT_CACHE_DAYS = 180


class VisarMapboxService(models.AbstractModel):
    """Las tres llamadas a Mapbox que necesita el negocio, en un solo sitio."""

    _name = 'visar.mapbox.service'
    _description = 'Visar: transporte compartido a Mapbox'

    @api.model
    def _visar_mapbox_token(self):
        return self.env['ir.config_parameter'].sudo().get_param(MAPBOX_TOKEN_PARAM)

    @api.model
    def _visar_travel_profile(self):
        """Perfil de ruteo. `driving`, y no `driving-traffic`, a propósito.

        Dos razones, las dos cargan peso:

        1. **Tope de coordenadas.** `driving-traffic` admite 10 por petición de
           Matrix; `driving`, 25. El pico medido de paradas de un técnico en un
           día es **10** (Pedro Martínez, 11-ago-2026), que con el destino son 11:
           `driving-traffic` ya no daría, no es que quedara justo. El §5.3 asumía
           9 y se quedó corto.
        2. **El tráfico que queremos es el HISTÓRICO, no el de ahora.** Con
           `min_schedule_hours = 24` no se reserva a menos de un día vista, así
           que las condiciones actuales son ruido. `driving-traffic` mezclaría
           tráfico en vivo cuando la salida está cerca — justo lo que no
           queremos.

        Ojo: renunciar a `driving-traffic` **no** es renunciar al tráfico. Con
        `depart_at`, el perfil `driving` responde con las condiciones previstas
        para esa fecha y hora, a partir de 90 días de histórico. Es exactamente lo
        que hace falta aquí, y sin pagar el tope de 10 coordenadas.

        `visar_field_app._visar_enroute_eta_minutes` sí usa `driving-traffic`, y
        no es una incoherencia: allí el técnico sale **ahora mismo**.
        """
        return self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_PROFILE_PARAM, DEFAULT_PROFILE) or DEFAULT_PROFILE

    @api.model
    def _visar_travel_timeout(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_TIMEOUT_PARAM, DEFAULT_TIMEOUT)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT

    @api.model
    def _visar_depart_at_mode(self):
        """`0` | `auto` | `1` — si se manda la hora de salida a Matrix.

        **Nace APAGADO (`0`), y eso es deliberado.** `depart_at` en Matrix es una
        BETA con alta previa; esta cuenta no la tiene y Mapbox no lo ignora:
        **rechaza la petición entera** con 422 (verificado en servidor el
        21-ago-2026 con una matriz de 2 puntos — sin el parámetro 200, con él
        422). Encendido por defecto, cada llamada pagaba un 422 antes de
        reintentar, y una sola escritura fallida del interruptor lo dejaba
        pagándolo para siempre.

        Apagado, el comportamiento es **exactamente** el de `825d536`, que es el
        único que se ha visto podar horarios de verdad contra la base real.

        - `0` (por defecto): no se manda. Sin franjas, una llamada por (día,
          técnico), clave de caché sin franja.
        - `1`: **lo que hay que poner el día que Mapbox conceda la beta.**
        - `auto`: se intenta y al primer 422 se apaga solo. Útil para probar el
          alta sin tocar código; no es el default porque el 422 se paga.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            DEPART_AT_PARAM, DEPART_AT_DEFAULT)
        return str(raw or '').strip().lower() or DEPART_AT_DEFAULT

    @api.model
    def _visar_depart_at_active(self):
        """¿Se manda la hora de salida?

        Manda **más** que el parámetro de la petición: si no hay hora de salida,
        tampoco tiene sentido partir el día en franjas, porque todas devolverían
        la MISMA respuesta y se pagarían por separado. Un solo interruptor para
        las dos cosas, o se quedan desincronizadas.
        """
        return self._visar_depart_at_mode() not in ('0', 'false', 'off', 'no')

    @api.model
    def _visar_depart_at_disable(self, detalle=''):
        """Apaga `depart_at` tras un rechazo, y deja dicho cómo encenderlo.

        La escritura va en `try`: esto corre al pintar horarios, y una petición
        web de solo lectura no puede escribir. Si no se persiste no se rompe
        nada — se vuelve a intentar en la siguiente corrida y se vuelve a caer a
        la respuesta sin hora, que es la degradación correcta.
        """
        _logger.warning(
            "Mapbox rechazó `depart_at` en Matrix (%s). Es una BETA que exige "
            "alta previa: https://www.mapbox.com/contact/matrix-api-depart-at . "
            "Se apaga (%s=0) y se sigue con velocidades típicas, sin hora del "
            "día. Cuando concedan la beta hay que poner %s=1 A MANO: `auto` ya "
            "no vuelve a probar.",
            (detalle or '')[:200], DEPART_AT_PARAM, DEPART_AT_PARAM)
        try:
            self.env['ir.config_parameter'].sudo().set_param(DEPART_AT_PARAM, '0')
        except Exception as err:  # noqa: BLE001 - cursor de solo lectura, p. ej.
            _logger.info("No se pudo persistir %s=0: %s", DEPART_AT_PARAM, err)

    @api.model
    def _visar_mapbox_depart_at(self, when_utc):
        """UTC naive → cadena ISO 8601 para `depart_at`, o None.

        Devuelve None en dos casos, y los dos degradan a velocidades típicas sin
        hora del día: peor, pero no falso.

        1. **La cuenta no tiene la beta** (`_visar_depart_at_mode()` en `0`).
        2. **La salida no está en el futuro**, porque Mapbox rechaza `depart_at`
           en el pasado. Pasa de verdad: la ventana de paradas lleva un día de
           margen a cada lado, así que una parada de esta mañana entra en el
           barrido de esta tarde.

        Degradar, nunca bloquear — la alternativa (empujarlo a "ahora") sería
        cotizar el tráfico de una hora que no es la de la cita.
        """
        if not self._visar_depart_at_active():
            return None
        if not when_utc or when_utc <= fields.Datetime.now():
            return None
        # Con la Z explícita: sin ella Mapbox lo interpreta como hora local de la
        # primera coordenada, y aquí los datetime ya vienen en UTC.
        return when_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

    @api.model
    def _visar_mapbox_matrix(self, coords, depart_at=None):
        """Matriz de duraciones en SEGUNDOS entre N puntos, o None.

        `coords` es una lista de `(lat, lng)`. Devuelve una lista de listas donde
        `[i][j]` es el viaje de `i` a `j`, o `None` si no se pudo (sin token, más
        de `MATRIX_MAX_COORDS` puntos, red caída, respuesta rara).

        `depart_at` es un datetime **UTC naive** de cuándo se sale. Con él, Mapbox
        responde con el tráfico previsto para esa fecha y hora según 90 días de
        histórico, que es lo que hace falta para una cita de mañana o de dentro de
        tres semanas. Sin él, velocidades típicas sin hora del día — y la
        diferencia entre las 8:00 y las 11:00 en Monterrey es del mismo tamaño que
        el presupuesto de 20 min que estamos midiendo.

        `None` **no** significa "no se puede llegar": significa "no lo sé". Quien
        llama tiene que tratarlo como "no impongas restricción", nunca como
        "descarta el horario".
        """
        coords = [c for c in (coords or []) if c and c[0] is not None
                  and c[1] is not None]
        if len(coords) < 2:
            return None
        if len(coords) > MATRIX_MAX_COORDS:
            _logger.info(
                "Mapbox Matrix: %s coordenadas exceden el tope de %s; se omite "
                "el filtro de traslado para esta parada.",
                len(coords), MATRIX_MAX_COORDS)
            return None
        token = self._visar_mapbox_token()
        if not token:
            return None
        # Mapbox recibe lon,lat — al revés de como se guarda en el partner.
        path = ';'.join('%s,%s' % (lng, lat) for lat, lng in coords)
        url = _MATRIX_URL % {'profile': self._visar_travel_profile(),
                             'coords': path}
        stamp = self._visar_mapbox_depart_at(depart_at)
        # Como mucho dos vueltas: la segunda solo existe para reintentar SIN hora
        # de salida cuando la cuenta no tiene la beta. El bucle está acotado por
        # construcción —`stamp` se pone a None antes de reintentar— y no depende
        # de que la desactivación se haya llegado a persistir.
        payload = None
        for _intento in (1, 2):
            params = {'access_token': token, 'annotations': 'duration'}
            if stamp:
                params['depart_at'] = stamp
            try:
                resp = requests.get(
                    url,
                    params=params,
                    timeout=self._visar_travel_timeout())
                if (stamp and resp.status_code == 422
                        and 'depart_at' in (resp.text or '')):
                    # NO es un fallo de red ni una matriz demasiado grande: es una
                    # capacidad que esta cuenta no tiene. Tratarlo como caída
                    # dejaría el filtro inerte en silencio, que es justo lo que
                    # pasó el 21-ago-2026.
                    self._visar_depart_at_disable(resp.text)
                    stamp = None
                    continue
                resp.raise_for_status()
                payload = resp.json()
            except Exception as err:  # noqa: BLE001 - red/API: degradar, nunca bloquear
                _logger.warning("Mapbox Matrix falló (%s coords): %s",
                                len(coords), err)
                return None
            break
        if payload is None:
            return None
        durations = payload.get('durations')
        if not durations or len(durations) != len(coords):
            _logger.warning("Mapbox Matrix devolvió una matriz inesperada.")
            return None
        return durations

    @api.model
    def _visar_mapbox_geocode(self, query, country='mx'):
        """(lat, lng, 'exact'|'approx') para un texto, o None.

        Mismo criterio de exactitud que `visar_field_app`: `address` y `poi` son
        nivel calle; lo demás es centroide de algo más grande.
        """
        query = (query or '').strip()
        if not query:
            return None
        token = self._visar_mapbox_token()
        if not token:
            return None
        try:
            resp = requests.get(
                _GEOCODE_URL % requests.utils.quote(query, safe=''),
                params={'access_token': token, 'limit': 1, 'country': country},
                timeout=self._visar_travel_timeout())
            resp.raise_for_status()
            features = resp.json().get('features') or []
        except Exception as err:  # noqa: BLE001 - red/API: degradar
            _logger.warning("Mapbox geocode falló para %r: %s", query, err)
            return None
        if not features:
            return None
        center = features[0].get('center') or []
        if len(center) != 2:
            return None
        lng, lat = center[0], center[1]
        place_types = set(features[0].get('place_type') or [])
        kind = 'exact' if place_types & {'address', 'poi'} else 'approx'
        return float(lat), float(lng), kind


class VisarTravelCache(models.Model):
    """Lo que ya le preguntamos a Mapbox, para no volver a pagarlo.

    Es un modelo y no un `ir.config_parameter` por tres razones prácticas: son
    miles de filas, hacen falta búsquedas indexadas, y los parámetros del sistema
    son un sitio que **leen personas** — volcar ahí miles de pares de coordenadas
    convierte Ajustes → Técnico → Parámetros en un vertedero.

    Un solo modelo con `kind` en vez de dos tablas: el ciclo de vida es idéntico
    (una consulta remota cara, cacheada por una clave de texto, con caducidad y un
    cron que barre), así que es un ACL, un cron y un método de limpieza.
    """

    _name = 'visar.travel.cache'
    _description = 'Visar: caché de tiempos de viaje y geocodificación'

    kind = fields.Selection(
        [('travel', 'Tiempo de viaje'), ('geocode', 'Punto geocodificado')],
        required=True, index=True)
    key = fields.Char(required=True, index=True)
    duration_seconds = fields.Integer(
        help="Solo para kind=travel: segundos de trayecto.")
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    fetched_at = fields.Datetime(default=fields.Datetime.now, required=True)

    _unique_key = models.Constraint(
        'unique(kind, key)',
        "Ya existe una entrada de caché con esa clave.",
    )

    # ------------------------------------------------------------------
    # Claves
    # ------------------------------------------------------------------

    @api.model
    def _visar_precision(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_PRECISION_PARAM, DEFAULT_PRECISION)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_PRECISION

    @api.model
    def _visar_travel_bucket_hours(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_BUCKET_HOURS_PARAM, DEFAULT_BUCKET_HOURS)
        try:
            return min(max(int(raw), 1), 24)
        except (TypeError, ValueError):
            return DEFAULT_BUCKET_HOURS

    @api.model
    def _visar_travel_bucket(self, when_local):
        """Franja de tráfico de una salida: `'D<día>H<hora>'`, o None.

        **Día de la semana y hora, nunca la fecha.** Es lo que modela el tráfico
        histórico —un martes a las 9 se parece a cualquier otro martes a las 9— y
        es lo que hace que la caché sirva de algo: con la fecha dentro, cada
        entrada se usaría una sola vez y la caché sería un log.

        `None` (sin hora conocida) es una franja legítima y distinta: significa
        "medido sin hora del día", y no debe mezclarse con las que sí la tienen.
        """
        if not when_local:
            return None
        width = self._visar_travel_bucket_hours()
        return 'D%dH%02d' % (when_local.weekday(),
                             (when_local.hour // width) * width)

    @api.model
    def _visar_travel_key(self, origin, destination, bucket=None):
        """Clave DIRECCIONAL de un par de coordenadas redondeadas y una franja.

        Direccional a propósito: en una ciudad con sentidos únicos, A→B y B→A no
        duran lo mismo.

        Se redondea porque entre dos direcciones fijas el tiempo casi no cambia y
        no vale la pena volver a pagarlo. Cuatro decimales son ~11 m: suficiente
        para que la misma casa siempre acierte, sin que dos casas distintas
        colapsen en la misma entrada.

        La franja entra en la clave desde que existe `depart_at`: el mismo par de
        puntos **no** tarda lo mismo a las 8:00 que a las 11:00, así que cachear
        sin ella sería servir la respuesta de una hora ajena. Entradas viejas sin
        franja simplemente no se encuentran, y el cron las barre.
        """
        digits = self._visar_precision()
        fmt = '%%.%df' % digits
        base = '%s,%s>%s,%s' % (
            fmt % origin[0], fmt % origin[1],
            fmt % destination[0], fmt % destination[1])
        return '%s|%s' % (base, bucket) if bucket else base

    # ------------------------------------------------------------------
    # Lectura / escritura
    # ------------------------------------------------------------------

    @api.model
    def _visar_travel_get(self, keys):
        """{clave: segundos} para las claves que estén cacheadas y vivas.

        Recibe claves ya construidas, no pares: desde que la franja horaria entra
        en la clave, armarla es decisión de quien llama —él es el que sabe a qué
        hora sale el técnico— y devolverle un dict con las mismas claves que él
        pasó le evita construirlas dos veces.

        Una sola consulta para todas: el predicado pregunta por la jornada entera
        de un técnico, y una consulta por clave sería justo el patrón que
        `visar.slot.hold._visar_snapshot` ya tuvo que corregir.
        """
        keys = list(keys or [])
        if not keys:
            return {}
        cutoff = self._visar_cutoff('travel')
        rows = self.sudo().search([
            ('kind', '=', 'travel'),
            ('key', 'in', keys),
            ('fetched_at', '>=', cutoff),
        ])
        return {row.key: row.duration_seconds for row in rows}

    @api.model
    def _visar_travel_store(self, entries):
        """Guarda {clave: segundos}. Sobrescribe lo caducado, no lo duplica."""
        if not entries:
            return
        existing = {
            row.key: row
            for row in self.sudo().search([('kind', '=', 'travel'),
                                           ('key', 'in', list(entries))])
        }
        now = fields.Datetime.now()
        to_create = []
        for key, seconds in entries.items():
            row = existing.get(key)
            if row:
                row.write({'duration_seconds': seconds, 'fetched_at': now})
            else:
                to_create.append({'kind': 'travel', 'key': key,
                                  'duration_seconds': seconds, 'fetched_at': now})
        if to_create:
            self.sudo().create(to_create)

    @api.model
    def _visar_point_get(self, key):
        """(lat, lng) de un punto cacheado y vivo, o None."""
        row = self.sudo().search([
            ('kind', '=', 'geocode'),
            ('key', '=', key),
            ('fetched_at', '>=', self._visar_cutoff('geocode')),
        ], limit=1)
        if not row or not (row.latitude or row.longitude):
            return None
        return row.latitude, row.longitude

    @api.model
    def _visar_point_store(self, key, latitude, longitude):
        row = self.sudo().search([('kind', '=', 'geocode'), ('key', '=', key)],
                                 limit=1)
        values = {'latitude': latitude, 'longitude': longitude,
                  'fetched_at': fields.Datetime.now()}
        if row:
            row.write(values)
        else:
            self.sudo().create(dict(values, kind='geocode', key=key))

    # ------------------------------------------------------------------
    # Caducidad
    # ------------------------------------------------------------------

    @api.model
    def _visar_cache_days(self, kind):
        if kind == 'geocode':
            return DEFAULT_POINT_CACHE_DAYS
        raw = self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_CACHE_DAYS_PARAM, DEFAULT_CACHE_DAYS)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_CACHE_DAYS

    @api.model
    def _visar_cutoff(self, kind):
        from datetime import timedelta
        return fields.Datetime.now() - timedelta(days=self._visar_cache_days(kind))

    @api.model
    def _visar_cron_gc(self):
        """Barre lo caducado. Solo evita que la tabla crezca.

        La corrección no depende de que esto haya corrido: las lecturas ya filtran
        por `fetched_at`, igual que `visar.slot.hold` ignora lo vencido sin esperar
        a su cron.
        """
        for kind in ('travel', 'geocode'):
            self.sudo().search([
                ('kind', '=', kind),
                ('fetched_at', '<', self._visar_cutoff(kind)),
            ]).unlink()


def visar_round_up_minutes(seconds):
    """Segundos → minutos, redondeando HACIA ARRIBA.

    Hacia arriba porque el redondeo tiene que ir en contra de ofrecer un horario
    que no cabe: prometer y no llegar es peor que no ofrecer.
    """
    if not seconds:
        return 0
    return max(int(math.ceil(float(seconds) / 60.0)), 0)
