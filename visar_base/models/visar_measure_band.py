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
    # Los limites REALES de la banda. `m2_ref` no sirve para esto: es el punto
    # medio, y existe para resolver el tramo de cada tabulador, no para decidir
    # si un numero cae dentro. Se separan porque son dos preguntas distintas:
    # "con que m² cotizo esta banda" y "que m² pertenecen a esta banda".
    #
    # Existen para que el paso se pueda contestar ESCRIBIENDO los metros. La
    # etiqueta ("101 – 150 m²") lleva el rango, pero leerselo al vuelo seria
    # volver a parsear un nombre que un consultor puede reescribir — el mismo
    # error que ya se corrigio con `is_valuation` (§10.11).
    #
    # Vacios = la banda solo se puede elegir por su numero de fila. Es lo que
    # pasaba antes de esto, asi que ninguna banda empeora por no tenerlos.
    m2_min = fields.Float(
        "m² desde",
        help="Limite inferior de la banda, inclusive. Vacio = la banda no se "
             "puede elegir escribiendo metros, solo por su numero.")
    m2_max = fields.Float(
        "m² hasta",
        help="Limite superior de la banda, inclusive. Vacio en la ULTIMA banda "
             "significa 'de aqui en adelante'.")
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

    def _visar_tiene_limites(self):
        """True si la banda tiene limites utiles para ubicar unos metros.

        Un `Float` vacio en Odoo vale **0.0**, no None, asi que "sin configurar"
        y "empieza en cero" se ven igual desde fuera. La banda de 0–50 m² es
        real y su minimo ES cero, asi que la pregunta no puede ser "¿m2_min?"
        sino "¿alguno de los dos limites dice algo?".

        Sin esta distincion, una banda sin sembrar (0.0 y 0.0) pasaria por
        "de cero en adelante" y se llevaria cualquier numero — en un paso que
        decide el precio.
        """
        self.ensure_one()
        return bool(self.m2_min) or bool(self.m2_max)

    @api.model
    def _visar_exterior_bands(self):
        """Bandas activas para el paso de exterior, en orden."""
        return self.search([('active', '=', True)], order='sequence, m2_ref')
