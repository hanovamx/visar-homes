# -*- coding: utf-8 -*-
import logging
import re

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Resolver el código postal desde calle + número + colonia (7-oct-2026).
#
# APAGADO por defecto, y no por timidez: medido contra 600 direcciones reales de
# Nuevo León, Mapbox ubica bien el punto (mediana de 16 m) pero el CP que
# devuelve coincide solo ~52% de las veces, y la zona ~86%. Sirve como
# SUGERENCIA que la persona confirma o corrige; no sirve como dato.
ADDRESS_CP_ENABLED_PARAM = 'visar.address_cp.enabled'
# Caja 'minLng,minLat,maxLng,maxLat' donde se busca. El default envuelve los CP
# del catálogo (de Linares a Sabinas Hidalgo) con ~15 km de margen.
ADDRESS_CP_BBOX_PARAM = 'visar.address_cp.bbox'
ADDRESS_CP_BBOX_DEFAULT = '-100.786,23.273,-99.030,27.403'
# Por debajo de esta relevancia el candidato se descarta. Con menos de 0.5 el CP
# acertó 18% de las veces en la medición: peor que no contestar.
ADDRESS_CP_MIN_RELEVANCE_PARAM = 'visar.address_cp.min_relevance'
ADDRESS_CP_MIN_RELEVANCE_DEFAULT = 0.5

# "Palo Blanco No. 8707" → "Palo Blanco 8707"; "Col. La Cima" → "La Cima".
_NUMBER_NOISE = re.compile(r'\b(?:no\.?|n[uú]m\.?|#)\s*(?=\d)', re.IGNORECASE)
_COLONIA_NOISE = re.compile(r'^\s*(?:col\.|colonia|fracc\.?|fraccionamiento)\s+',
                            re.IGNORECASE)


class VisarZoneCp(models.Model):
    """ Mapeo Código Postal → Zona Visar.

    Sembrado desde SEPOMEX (noupdate=1) y editable en Odoo para afinar
    los CP ambiguos (una misma zona postal con colonias de varias zonas).
    El booking web resuelve la zona a partir del CP capturado. """
    _name = 'visar.zone.cp'
    _description = "Código Postal → Zona Visar"
    _order = 'name'

    name = fields.Char(
        "Código Postal", required=True, index=True,
        help="Código postal de 5 dígitos (SEPOMEX).")
    zone_code = fields.Char(
        "Código de zona", help="Código de zona sembrado (A, B, C). "
        "Determina la zona por defecto; puede sobrescribirse en 'Zona'.")
    zone_id = fields.Many2one(
        'visar.zone', string="Zona", index=True,
        compute='_compute_zone_id', store=True, readonly=False,
        help="Zona Visar asignada a este código postal.")
    municipality = fields.Char("Municipio")
    colonia_count = fields.Integer(
        "# Colonias", help="Número de colonias del CP (referencia SEPOMEX).")
    criterion = fields.Char(
        "Criterio", help="Cómo se asignó la zona (único, mayoría, mixto).")
    needs_review = fields.Boolean(
        "Revisar", compute='_compute_needs_review', store=True,
        help="El CP abarca colonias de varias zonas; conviene revisarlo.")
    visar_centroid_lat = fields.Float(
        "Latitud (centroide)", digits=(10, 7),
        help="Centro aproximado del CP. Respaldo para estimar traslados cuando "
             "la dirección exacta no geocodifica.")
    visar_centroid_lng = fields.Float(
        "Longitud (centroide)", digits=(10, 7))

    _sql_constraints = [
        ('cp_uniq', 'unique(name)', "El código postal debe ser único."),
    ]

    @api.depends('zone_code')
    def _compute_zone_id(self):
        """Resuelve zone_id desde zone_code (match por código de zona).

        Solo recalcula cuando cambia zone_code, de modo que las
        sobrescrituras manuales de zona persisten. """
        Zone = self.env['visar.zone']
        by_code = {
            z.code: z for z in Zone.search([]) if z.code
        }
        for record in self:
            code = (record.zone_code or '').strip()
            record.zone_id = by_code.get(code, Zone)

    @api.depends('criterion')
    def _compute_needs_review(self):
        for record in self:
            criterion = (record.criterion or '').strip().lower()
            record.needs_review = bool(criterion) and criterion != 'único'

    @api.model
    def _normalize_cp(self, cp):
        """Deja solo el código postal de 5 dígitos, sin espacios ni guiones."""
        if not cp:
            return ''
        return ''.join(ch for ch in str(cp) if ch.isdigit())[:5]

    @api.model
    def _get_cp_record(self, cp):
        """Devuelve el registro visar.zone.cp del CP dado, o un recordset vacío."""
        normalized = self._normalize_cp(cp)
        if not normalized:
            return self.browse()
        return self.search([('name', '=', normalized)], limit=1)

    @api.model
    def _get_zone_for_cp(self, cp):
        """Devuelve la visar.zone del CP dado, o un recordset vacío."""
        return self._get_cp_record(cp).zone_id

    def _visar_centroid(self, geocode=True):
        """(lat, lng) del centro del CP, geocodificando la primera vez.

        `geocode=False` significa **"solo lo que ya está guardado"**: si el CP no
        tiene centroide todavía, devuelve `None` en vez de salir a la red. No es
        una optimización, es una regla de dónde puede costar latencia una
        petición:

        - el **destino** de una reserva es UNO por consulta, así que geocodificar
          en caliente es un precio que se puede pagar;
        - las **paradas** de un día son varias, y las de un mes son muchas. Pedir
          el centroide de cada una al pintar horarios es exactamente el error que
          el §5.3 del diseño 33 pasó tres revisiones evitando.

        Quien necesite los centroides poblados que use `_visar_prewarm_centroids`
        desde el cron, fuera del camino de la petición.

        Es el respaldo del §5.4 del diseño 33: **1 de cada 4 direcciones no está
        geocodificada** (77.6% de cobertura medida en servidor), y sin ninguna
        coordenada el predicado de traslado se rinde y ofrece cualquier horario.
        El centroide del CP no es la casa del cliente, pero está muchísimo más
        cerca que rendirse.

        Cuesta **una** llamada a Mapbox por CP en toda la vida del sistema: se
        guarda en el propio registro, que es el sitio natural (un CP no se mueve)
        y evita depender de la caché general para un dato tan estable.

        Nunca lanza: sin token o con Mapbox caído devuelve None y quien llama
        degrada.
        """
        self.ensure_one()
        if self.visar_centroid_lat or self.visar_centroid_lng:
            return self.visar_centroid_lat, self.visar_centroid_lng
        if not self.name:
            return None
        partes = ['CP %s' % self.name]
        if self.municipality:
            partes.append(self.municipality)
        partes += ['Nuevo León', 'México']
        if not geocode:
            return None
        found = self.env['visar.mapbox.service']._visar_mapbox_geocode(
            ', '.join(partes))
        if not found:
            return None
        lat, lng, _kind = found
        self.sudo().write({'visar_centroid_lat': lat, 'visar_centroid_lng': lng})
        return lat, lng

    @api.model
    def _visar_prewarm_centroids(self, limit=None):
        """Geocodifica CPs sin centroide, en lote y FUERA de la petición.

        El respaldo de centroide lleva construido desde el 21-ago y nunca se ha
        estrenado: al 4-sep-2026 la tabla tenía **1080 filas y 0 centroides**,
        porque la geocodificación de la dirección exacta venía resolviendo y esa
        rama no se llegaba a pisar. No estaba roto — estaba sin pisar.

        Deja de dar igual con la agrupación por zona del día (§5.7): ahí las
        **paradas** también necesitan punto, y una parada sin coordenadas no
        restringe, así que un día ciego se lee como día vacío y lo acepta todo.
        Un respaldo que solo funciona cuando alguien lo llama en caliente no
        sirve para eso; tiene que estar ya poblado.

        Devuelve cuántos se resolvieron. No levanta: un CP que no geocodifica se
        queda sin centroide y se reintentará en la corrida siguiente.
        """
        limit = limit or self._visar_prewarm_batch()
        pendientes = self.search(
            ['|', ('visar_centroid_lat', '=', 0.0),
                  ('visar_centroid_lat', '=', False)], limit=limit)
        if not pendientes:
            return 0
        resueltos = 0
        for record in pendientes:
            try:
                if record._visar_centroid(geocode=True):
                    resueltos += 1
            except Exception:  # noqa: BLE001 - un CP no puede tumbar la corrida
                _logger.exception(
                    "visar.zone.cp: no se pudo geocodificar el CP %s", record.name)
        _logger.info(
            "Precalentado de centroides: %s de %s resueltos; quedan %s sin centroide.",
            resueltos, len(pendientes), self.search_count(
                ['|', ('visar_centroid_lat', '=', 0.0),
                      ('visar_centroid_lat', '=', False)]))
        return resueltos

    @api.model
    def _visar_prewarm_batch(self):
        """Cuántos CPs se geocodifican por corrida del cron.

        Mapbox admite ~600 peticiones/min en geocoding, así que 200 no roza el
        límite; el tope existe para que una corrida no se eternice ni se coma el
        worker, no porque la API se queje.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'visar.travel.prewarm_batch', 200)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return 200

    # ------------------------------------------------------------------
    # Dirección → código postal
    # ------------------------------------------------------------------

    @api.model
    def _visar_address_cp_enabled(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            ADDRESS_CP_ENABLED_PARAM, '0')
        return str(raw).strip().lower() in ('1', 'true', 'yes', 'si', 'sí')

    @api.model
    def _visar_address_cp_min_relevance(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            ADDRESS_CP_MIN_RELEVANCE_PARAM, ADDRESS_CP_MIN_RELEVANCE_DEFAULT)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return ADDRESS_CP_MIN_RELEVANCE_DEFAULT

    @api.model
    def _visar_address_to_cp(self, street, ext_num, neighborhood, municipality=None):
        """Calle + número + colonia → el CP cubierto que mejor cuadra.

        Devuelve siempre un dict con `status`:

        - `'disabled'`: la función está apagada (`visar.address_cp.enabled`).
        - `'incomplete'`: falta calle, número o colonia.
        - `'not_found'`: Mapbox no devolvió nada utilizable DENTRO de la
          cobertura. No distingue "fuera de cobertura" de "mal escrita": desde
          aquí no se puede saber, y quien llama debe pedir el CP en vez de
          decirle a alguien que no se le atiende.
        - `'found'`: con `zip`, `municipality`, `street` (como la escribe
          Mapbox), `ext_num`, `neighborhood` (como llegó), `exact` (¿encontró
          ese número, o solo la calle?) y `relevance`.

        Se queda con el PRIMER candidato cuyo CP está en el catálogo con zona.
        Es una sugerencia: hay que enseñársela a la persona antes de usarla
        (ver la medición junto a `ADDRESS_CP_ENABLED_PARAM`).

        Nunca lanza.
        """
        if not self._visar_address_cp_enabled():
            return {'status': 'disabled'}
        street = _NUMBER_NOISE.sub('', (street or '')).strip()
        ext_num = _NUMBER_NOISE.sub('', (ext_num or '')).strip()
        neighborhood = (neighborhood or '').strip()
        if not (street and ext_num and neighborhood):
            return {'status': 'incomplete'}

        partes = ['%s %s' % (street, ext_num), _COLONIA_NOISE.sub('', neighborhood)]
        if (municipality or '').strip():
            partes.append(municipality.strip())
        query = ', '.join(partes)
        bbox = (self.env['ir.config_parameter'].sudo().get_param(
            ADDRESS_CP_BBOX_PARAM) or ADDRESS_CP_BBOX_DEFAULT).strip()
        features = self.env['visar.mapbox.service']._visar_mapbox_geocode_features(
            query, types='address', language='es', limit=5, bbox=bbox)

        minimo = self._visar_address_cp_min_relevance()
        for feature in features:
            try:
                relevance = float(feature.get('relevance') or 0.0)
            except (TypeError, ValueError):
                relevance = 0.0
            if relevance < minimo:
                continue
            postcode = ''
            for entry in feature.get('context') or []:
                if str((entry or {}).get('id') or '').startswith('postcode'):
                    postcode = self._normalize_cp(entry.get('text'))
                    break
            record = self._get_cp_record(postcode) if postcode else self.browse()
            if not record or not record.zone_id:
                continue
            result = {
                'status': 'found',
                'zip': record.name,
                'municipality': record.municipality or '',
                'street': (feature.get('text') or street).strip(),
                'ext_num': ext_num,
                'neighborhood': neighborhood,
                'exact': feature.get('address') is not None,
                'relevance': relevance,
            }
            _logger.info(
                "Dirección → CP: %r resolvió a %s (%s), relevancia %.2f, número %s.",
                query, result['zip'], feature.get('place_name'), relevance,
                'encontrado' if result['exact'] else 'NO encontrado')
            return result
        _logger.info("Dirección → CP: %r sin candidato en cobertura (%s resultados).",
                     query, len(features))
        return {'status': 'not_found'}
