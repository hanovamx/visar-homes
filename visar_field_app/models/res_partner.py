# -*- coding: utf-8 -*-
import logging
import re
from urllib.parse import quote

import requests

from odoo import fields, models

_logger = logging.getLogger(__name__)

# "No. 8707", "núm 8707", "# 8707"… → el geocodificador resuelve mejor sin el prefijo.
_STREET_NUMBER_NOISE = re.compile(r'\b(?:no\.?|n[uú]m\.?|#)\s*(?=\d)', re.IGNORECASE)

# Config compartida con el mapa nativo de Field Service (web_map). Si Visar
# configura un token de Mapbox en Ajustes, la app de campo lo reutiliza para
# geocodificar con más precisión (mismo token, ambos mapas mejoran).
MAPBOX_TOKEN_PARAM = 'web_map.token_map_box'
# Geocodificación forward de Mapbox v5 (misma API que usa el mapa nativo).
MAPBOX_GEOCODE_URL = 'https://api.mapbox.com/geocoding/v5/mapbox.places/%s.json'
# place_type de Mapbox que consideramos "a nivel calle" (no centroide).
_MAPBOX_EXACT_TYPES = {'address', 'poi'}


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @staticmethod
    def _visar_clean_street(street):
        """Limpia ruido típico de direcciones MX que confunde al geocodificador
        (p. ej. "Palo Blanco No. 8707" → "Palo Blanco 8707")."""
        if not street:
            return ''
        return _STREET_NUMBER_NOISE.sub('', street).strip()

    def _visar_geo_localize(self):
        """Geolocaliza la dirección de este contacto y escribe lat/long/fecha.

        Estrategia por precisión:
          1. **Mapbox** (si hay token `web_map.token_map_box`, el MISMO que usa el
             mapa nativo de Field Service). Server-side, así que el token no se
             expone en la página pública del técnico.
          2. **OpenStreetMap** (base_geolocalize) con consulta enriquecida
             (colonia + estado, sin ruido de número), como respaldo gratuito.

        Devuelve 'exact' si resolvió a nivel calle, 'approx' si solo al centroide
        de CP/ciudad, o False si no resolvió nada.
        """
        self.ensure_one()
        token = self.env['ir.config_parameter'].sudo().get_param(MAPBOX_TOKEN_PARAM)
        if token:
            kind = self._visar_geo_localize_mapbox(token)
            if kind:
                return kind
            # Si Mapbox no devolvió nada, se intenta con OSM antes de rendirse.
        return self._visar_geo_localize_osm()

    def _visar_geo_localize_mapbox(self, token):
        """Geocodifica con la API de Mapbox (server-side). Devuelve 'exact'/'approx'
        o False. No escribe si no hay resultado (preserva coordenadas previas)."""
        self.ensure_one()
        address = (self.contact_address_complete or '').replace('/', ' ').strip()
        if not address:
            return False
        params = {'access_token': token, 'limit': 1, 'language': 'es'}
        if self.country_id.code:
            params['country'] = self.country_id.code.lower()
        try:
            resp = requests.get(
                MAPBOX_GEOCODE_URL % quote(address, safe=''),
                params=params, timeout=10)
            resp.raise_for_status()
            features = resp.json().get('features') or []
        except Exception as err:  # noqa: BLE001 - red/API: degradar a OSM
            _logger.warning("Mapbox geocode falló para %s: %s", self.id, err)
            return False
        if not features:
            return False
        feature = features[0]
        # Mapbox devuelve el centro como [lon, lat].
        lng, lat = feature['center']
        self.write({
            'partner_latitude': lat,
            'partner_longitude': lng,
            'date_localization': fields.Date.context_today(self),
        })
        place_types = set(feature.get('place_type') or [])
        return 'exact' if (place_types & _MAPBOX_EXACT_TYPES) else 'approx'

    def _visar_geo_localize_osm(self):
        """Respaldo gratuito: base_geolocalize (OSM) con consulta enriquecida
        (colonia + estado, sin ruido de número) y fallback al centroide de CP."""
        self.ensure_one()
        geo = self.env['base.geocoder']
        country = self.country_id.name or ''
        state = self.state_id.name or ''
        # En MX la colonia suele vivir en street2; se incluye en el componente calle.
        street = ', '.join(part for part in [
            self._visar_clean_street(self.street), self.street2] if part)

        # 1) Nivel calle (dirección completa).
        search = geo.geo_query_address(
            street=street, zip=self.zip, city=self.city, state=state, country=country)
        result = geo.geo_find(search, force_country=country)
        kind = 'exact'

        # 2) Fallback: centroide de CP / ciudad (sin calle).
        if not result:
            search = geo.geo_query_address(
                zip=self.zip, city=self.city, state=state, country=country)
            result = geo.geo_find(search, force_country=country)
            kind = 'approx'

        if not result:
            return False
        self.write({
            'partner_latitude': result[0],
            'partner_longitude': result[1],
            'date_localization': fields.Date.context_today(self),
        })
        return kind
