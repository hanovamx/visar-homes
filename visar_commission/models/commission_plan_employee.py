# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class VisarCommissionPlanEmployee(models.Model):
    _name = 'visar.commission.plan.employee'
    _description = "Empleado dentro de un plan de comisión"
    _rec_name = 'employee_id'
    _order = 'id'

    plan_id = fields.Many2one(
        'visar.commission.plan', required=True, index=True, ondelete='cascade')
    # AQUÍ está la diferencia con el nativo: `hr.employee`, no `res.users`. El
    # empleado no necesita cuenta de Odoo —igual que en la app de técnicos, que
    # entra con PIN— así que no consume licencia.
    employee_id = fields.Many2one(
        'hr.employee', string="Empleado", required=True, index=True,
        help="Quien cobra la comisión. No necesita usuario de Odoo.")
    # Vigencia propia: alguien que entra a mitad del plan solo comisiona desde
    # que entró, y quien sale deja de comisionar sin borrarle lo ya ganado.
    date_from = fields.Date(
        "Desde", compute='_compute_date_from', store=True, readonly=False)
    date_to = fields.Date("Hasta")

    _employee_uniq = models.Constraint(
        'unique (plan_id, employee_id)',
        "El empleado ya está en el plan.",
    )

    @api.depends('plan_id.date_from')
    def _compute_date_from(self):
        for linea in self:
            if not linea.date_from:
                linea.date_from = linea.plan_id.date_from

    @api.constrains('date_from', 'date_to')
    def _check_fechas(self):
        for linea in self:
            if linea.date_to and linea.date_from and linea.date_to < linea.date_from:
                raise ValidationError(_("«Desde» debe ser anterior a «Hasta»."))
            plan = linea.plan_id
            if linea.date_from and plan.date_from and linea.date_from < plan.date_from:
                raise ValidationError(_(
                    "El empleado no puede empezar antes que el plan."))
            if linea.date_to and plan.date_to and linea.date_to > plan.date_to:
                raise ValidationError(_(
                    "El empleado no puede terminar después que el plan."))

    def _compute_display_name(self):
        for linea in self:
            linea.display_name = "%s — %s" % (
                linea.employee_id.name or '', linea.plan_id.name or '')

    def _visar_ventana(self, date_from, date_to):
        """Intersección entre el periodo y la vigencia del empleado en el plan.

        Devuelve (desde, hasta) o None si el empleado no estaba en el plan
        durante ese periodo.
        """
        self.ensure_one()
        desde = max(date_from, self.date_from) if self.date_from else date_from
        hasta = min(date_to, self.date_to) if self.date_to else date_to
        if desde > hasta:
            return None
        return desde, hasta
