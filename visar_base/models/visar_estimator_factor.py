# -*- coding: utf-8 -*-
import math

from odoo import api, fields, models


class VisarEstimatorFactor(models.Model):
    """ Tabla de factores de ajuste para la estimación de m² de construcción.

    Cuando el cliente no conoce sus m² interiores, se estiman por proxies
    (recámaras/baños/niveles/cochera). Si además indica el tamaño del terreno
    (predio), se aplica un factor de ajuste: una casa en un predio chico está
    más apretada que la misma casa en un predio grande. La tabla es configurable
    desde el backend; sin predio el factor es 1.0.

    La lógica replica la forma del cotizador interno `fumiquote.html`, pero los
    rangos y precios finales salen del tabulador oficial de Visar (tramos), no de
    los números del cotizador.
    """
    _name = 'visar.estimator.factor'
    _description = "Factor de estimación interior Visar (por tamaño de predio)"
    _order = 'sequence, predio_max'

    predio_max = fields.Float(
        "Predio hasta (m²)", required=True,
        help="Cota superior inclusiva del tamaño de terreno para este factor. "
             "Usa un valor alto (p. ej. 999999) en la última fila.")
    factor = fields.Float(
        "Factor", required=True, default=1.0,
        help="Multiplicador aplicado a la estimación de construcción.")
    sequence = fields.Integer("Secuencia", default=10)
    active = fields.Boolean(default=True)

    @api.model
    def _visar_estimator_factor(self, predio=0):
        """Factor de la primera fila cuyo `predio_max` cubre el predio dado.

        Sin predio (0 o falsy) → 1.0 (sin ajuste).
        """
        try:
            predio = float(predio or 0)
        except (TypeError, ValueError):
            predio = 0.0
        if predio <= 0:
            return 1.0
        rows = self.search([('active', '=', True)], order='sequence, predio_max')
        for row in rows:
            if predio <= row.predio_max:
                return row.factor or 1.0
        return rows[-1].factor if rows else 1.0

    @api.model
    def _visar_estimate_interior_m2(self, rec=0, ban=0, niv=1, gar=0, predio=0):
        """Estima m² de construcción a partir de proxies + factor de predio.

        Devuelve un entero de m² que luego se resuelve a un tramo del tabulador
        interior. Solo necesita acertar el rango (1–250 / 251–500 / 501–1000).
        """
        rec = max(int(rec or 0), 0)
        ban = max(int(ban or 0), 0)
        niv = max(int(niv or 1), 1)
        gar = max(int(gar or 0), 0)

        f = self._visar_estimator_factor(predio)
        m_rec, m_ban, m_gar, m_sala, m_circ = 12 * f, 5 * f, 14 * f, 22 * f, 8 * f

        pb = m_sala + (rec * m_rec) + (ban * m_ban) + (gar * m_gar)
        extras = 0.0
        if niv > 1:
            extras = (niv - 1) * (
                math.ceil(rec * 0.6) * m_rec
                + math.ceil(ban * 0.5) * m_ban
                + m_circ
            )
        total = round(pb + extras)

        # Clamp de coherencia contra el predio (constantes del cotizador interno).
        try:
            predio_f = float(predio or 0)
        except (TypeError, ValueError):
            predio_f = 0.0
        if predio_f > 0:
            total = min(total, round(predio_f * niv * 0.82))
            total = max(total, round(predio_f * 0.35))
        return int(total)
