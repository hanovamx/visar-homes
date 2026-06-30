# -*- coding: utf-8 -*-
from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    visar_field_pin = fields.Char(
        string="PIN App de Campo",
        groups="hr.group_hr_user",
        copy=False,
        help="PIN con el que el técnico se identifica en la app de campo de Visar. "
             "Permite atribuir el servicio al técnico correcto sin que tenga usuario "
             "interno de Odoo (mismo patrón que el PIN de POS).")

    @api.model
    def _visar_field_find_by_pin(self, pin):
        """Devuelve el empleado cuyo PIN de campo coincide, o un recordset vacío.

        Se ejecuta en sudo desde el controlador público; el PIN está protegido por
        el grupo hr.group_hr_user en la UI de backend.
        """
        pin = (pin or '').strip()
        if not pin:
            return self.browse()
        return self.sudo().search(
            [('visar_field_pin', '=', pin), ('active', '=', True)], limit=1)
