# -*- coding: utf-8 -*-
from odoo import api, fields, models


class VisarFieldSession(models.Model):
    _name = 'visar.field.session'
    _description = "Sesión de trabajo de técnico (App de Campo Visar)"
    _order = 'date_start desc'

    name = fields.Char(
        string="Referencia", compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string="Técnico", required=True, ondelete='cascade',
        index=True)
    date_start = fields.Datetime(
        string="Inicio", required=True, default=fields.Datetime.now)
    date_end = fields.Datetime(string="Fin")
    state = fields.Selection(
        [('open', "Abierta"), ('closed', "Cerrada")],
        string="Estado", default='open', required=True, index=True)
    note = fields.Char(string="Dispositivo / nota")

    @api.depends('employee_id', 'date_start')
    def _compute_name(self):
        for session in self:
            emp = session.employee_id.name or ""
            day = fields.Date.to_string(session.date_start) if session.date_start else ""
            session.name = ("%s — %s" % (emp, day)).strip(" —") or "Sesión"

    def action_close(self):
        self.write({'state': 'closed', 'date_end': fields.Datetime.now()})
