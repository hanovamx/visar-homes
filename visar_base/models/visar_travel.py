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
# 25; `driving-traffic` solo 10, y el pico medido de paradas de un técnico es 9
# (+1 el destino = 10, justo en el límite). Ver `_visar_travel_profile`.
MATRIX_MAX_COORDS = 25

# Parámetros de configuración (todos opcionales, con default sensato).
TRAVEL_PROFILE_PARAM = 'visar.travel.profile'
TRAVEL_TIMEOUT_PARAM = 'visar.travel.timeout'
TRAVEL_PRECISION_PARAM = 'visar.travel.coord_precision'
TRAVEL_CACHE_DAYS_PARAM = 'visar.travel.cache_days'

DEFAULT_PROFILE = 'driving'
DEFAULT_TIMEOUT = 10
DEFAULT_PRECISION = 4
DEFAULT_CACHE_DAYS = 30
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
           día es 9, que con el destino son exactamente 10: quedaríamos justo en
           el límite, y un día atípico rompería la llamada entera.
        2. **El tráfico de ahora no dice nada del de mañana.** Con
           `min_schedule_hours = 24` no se puede reservar a menos de un día vista,
           así que las condiciones actuales son ruido.

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
    def _visar_mapbox_matrix(self, coords):
        """Matriz de duraciones en SEGUNDOS entre N puntos, o None.

        `coords` es una lista de `(lat, lng)`. Devuelve una lista de listas donde
        `[i][j]` es el viaje de `i` a `j`, o `None` si no se pudo (sin token, más
        de `MATRIX_MAX_COORDS` puntos, red caída, respuesta rara).

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
        try:
            resp = requests.get(
                url,
                params={'access_token': token, 'annotations': 'duration'},
                timeout=self._visar_travel_timeout())
            resp.raise_for_status()
            payload = resp.json()
        except Exception as err:  # noqa: BLE001 - red/API: degradar, nunca bloquear
            _logger.warning("Mapbox Matrix falló (%s coords): %s", len(coords), err)
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
    def _visar_travel_key(self, origin, destination):
        """Clave DIRECCIONAL de un par de coordenadas redondeadas.

        Direccional a propósito: en una ciudad con sentidos únicos, A→B y B→A no
        duran lo mismo.

        Se redondea porque entre dos direcciones fijas el tiempo casi no cambia y
        no vale la pena volver a pagarlo. Cuatro decimales son ~11 m: suficiente
        para que la misma casa siempre acierte, sin que dos casas distintas
        colapsen en la misma entrada.
        """
        digits = self._visar_precision()
        fmt = '%%.%df' % digits
        return '%s,%s>%s,%s' % (
            fmt % origin[0], fmt % origin[1],
            fmt % destination[0], fmt % destination[1])

    # ------------------------------------------------------------------
    # Lectura / escritura
    # ------------------------------------------------------------------

    @api.model
    def _visar_travel_get(self, pairs):
        """{clave: segundos} para los pares que estén cacheados y vivos.

        Una sola consulta para todos los pares: el predicado pregunta por la
        jornada entera de un técnico, y una consulta por par sería justo el patrón
        que `visar.slot.hold._visar_snapshot` ya tuvo que corregir.
        """
        keys = [self._visar_travel_key(o, d) for o, d in (pairs or [])]
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
