# -*- coding: utf-8 -*-
from odoo import api, fields, models


class VisarMeasureBand(models.Model):
    """ Banda unificada de medición de exterior (jardín / área verde).

    Una sola pregunta de tamaño de exterior alimenta varias tablas de precio
    (fumigación exterior y corte de pasto). Cada banda define un m² representativo
    (`m2_ref`) que se resuelve contra los tramos (`visar.service.tier`) de cada
    dimensión con medición de tipo 'exterior', usando la lógica existente
    `m2_min <= m2_ref <= m2_max`. Así una banda puede caer en un tramo con precio
    para un servicio y en un tramo de valoración para otro, sin alinear las tablas.
    """
    _name = 'visar.measure.band'
    _description = "Banda de medición de exterior Visar"
    _order = 'sequence, m2_ref'

    name = fields.Char(
        "Etiqueta del rango", required=True, translate=True,
        help="Texto mostrado como opción en el wizard (p. ej. '101 – 150 m²').")
    m2_ref = fields.Float(
        "m² representativo", required=True,
        help="Valor de m² usado para resolver el tramo de cada servicio de exterior. "
             "Usa un punto interior de la banda (p. ej. el punto medio) para que caiga "
             "en el tramo correcto de cada tabulador.")
    comparative_label = fields.Char(
        "Comparativo visual", translate=True,
        help="Referencia cotidiana para clientes que no conocen sus m² "
             "(p. ej. 'Como una cancha de básquetbol'). Vacío = no se ofrece como comparativo.")
    is_valuation = fields.Boolean(
        "Requiere valoración",
        help="Marca la banda como fuera de rango de servicio directo: al elegirla, "
             "la reserva pasa a Visita de Valoración Técnica.")
    sequence = fields.Integer("Secuencia", default=10)
    active = fields.Boolean(default=True)

    @api.model
    def _visar_exterior_bands(self):
        """Bandas activas para el paso de exterior, en orden."""
        return self.search([('active', '=', True)], order='sequence, m2_ref')
