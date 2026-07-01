# -*- coding: utf-8 -*-
from odoo import api, fields, models


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
