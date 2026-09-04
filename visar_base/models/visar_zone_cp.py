# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


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
