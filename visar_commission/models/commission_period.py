# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class VisarCommissionPeriod(models.Model):
    _name = 'visar.commission.period'
    _description = "Periodo de comisión"
    _order = 'date_from, id'

    plan_id = fields.Many2one(
        'visar.commission.plan', required=True, index=True, ondelete='cascade')
    currency_id = fields.Many2one(related='plan_id.currency_id', readonly=True)
    name = fields.Char("Periodo", required=True, readonly=True)
    date_from = fields.Date("Desde", required=True, readonly=True, index=True)
    date_to = fields.Date("Hasta", required=True, readonly=True, index=True)
    # Fecha en la que se paga lo de este periodo. Como en el nativo, es la que
    # usa nómina para juntar varios periodos en un mismo pago.
    payment_date = fields.Date(
        "Fecha de pago", compute='_compute_payment_date', store=True, readonly=False)
    target_amount = fields.Monetary(
        "Meta", currency_field='currency_id', default=0.0,
        help="Solo se usa en el modo de metas. Es lo que el empleado debe "
             "lograr en el periodo para cobrar la comisión de la tabla.")

    # El nativo no tiene este estado: su reporte SIEMPRE recalcula, así que
    # editar un pedido viejo cambia una comisión ya pagada. Cerrar congela.
    state = fields.Selection([
        ('abierto', "Abierto"),
        ('cerrado', "Cerrado"),
        ('pagado', "Pagado"),
    ], string="Estado", required=True, default='abierto',
        help="Abierto: se recalcula cada vez. Cerrado o pagado: las cifras ya "
             "no cambian aunque cambien los pedidos.")

    line_ids = fields.One2many(
        'visar.commission.line', 'period_id', string="Comisiones", readonly=True)
    commission_total = fields.Monetary(
        "Comisión del periodo", compute='_compute_totales',
        currency_field='currency_id', store=True)
    achieved_total = fields.Monetary(
        "Logrado", compute='_compute_totales',
        currency_field='currency_id', store=True)

    @api.depends('line_ids.commission_amount', 'line_ids.achieved_amount')
    def _compute_totales(self):
        for periodo in self:
            periodo.commission_total = sum(periodo.line_ids.mapped('commission_amount'))
            periodo.achieved_total = sum(periodo.line_ids.mapped('achieved_amount'))

    @api.depends('date_to')
    def _compute_payment_date(self):
        for periodo in self:
            if not periodo.payment_date:
                periodo.payment_date = periodo.date_to

    @api.constrains('plan_id', 'date_from', 'date_to')
    def _check_traslape(self):
        for periodo in self:
            if periodo.date_from > periodo.date_to:
                raise ValidationError(_(
                    "El periodo «%s» empieza después de terminar.", periodo.name))
            traslape = self.search([
                ('plan_id', '=', periodo.plan_id.id),
                ('id', '!=', periodo.id),
                ('date_from', '<=', periodo.date_to),
                ('date_to', '>=', periodo.date_from),
            ], limit=1)
            if traslape:
                raise ValidationError(_(
                    "Los periodos «%(uno)s» y «%(otro)s» del plan se traslapan.",
                    uno=periodo.name, otro=traslape.name))

    # ------------------------------------------------------------------
    def _visar_calcular(self):
        """Recalcula las comisiones del periodo y devuelve sus renglones.

        Un periodo cerrado o pagado se salta en silencio: es dinero autorizado.
        """
        Line = self.env['visar.commission.line']
        resultado = Line.browse()
        for periodo in self:
            if periodo.state != 'abierto':
                resultado |= periodo.line_ids
                continue
            periodo.line_ids.unlink()
            for plan_emp in periodo.plan_id.employee_ids:
                ventana = plan_emp._visar_ventana(periodo.date_from, periodo.date_to)
                if not ventana:
                    continue  # el empleado no estaba en el plan en este periodo
                resultado |= Line._visar_generar(periodo, plan_emp, *ventana)
        return resultado

    def action_calcular(self):
        self._visar_calcular()
        return True

    def action_cerrar(self):
        for periodo in self:
            if periodo.state != 'abierto':
                continue
            if not periodo.line_ids:
                periodo._visar_calcular()
            periodo.state = 'cerrado'
            periodo.plan_id.message_post(body=_(
                "Periodo <b>%(periodo)s</b> cerrado: %(total)s de comisión. "
                "Las cifras quedan congeladas.",
                periodo=periodo.name,
                total=periodo.currency_id.format(periodo.commission_total),
            ))

    def action_reabrir(self):
        for periodo in self:
            if periodo.state == 'pagado':
                raise UserError(_(
                    "El periodo «%s» ya se pagó. Reabrirlo cambiaría cifras que "
                    "ya salieron en nómina: si de verdad hace falta, primero "
                    "regrésalo a «Cerrado».", periodo.name))
            periodo.state = 'abierto'

    def action_marcar_pagado(self):
        for periodo in self:
            if periodo.state == 'abierto':
                raise UserError(_(
                    "Antes de marcar pagado el periodo «%s» hay que cerrarlo, "
                    "para que las cifras no se muevan después.", periodo.name))
            periodo.state = 'pagado'

    def action_ver_lineas(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Comisiones de %s", self.name),
            'res_model': 'visar.commission.line',
            'view_mode': 'list,form',
            'domain': [('period_id', '=', self.id)],
            'context': {'create': False},
        }
