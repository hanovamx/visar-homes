# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class VisarCommissionLine(models.Model):
    _name = 'visar.commission.line'
    _description = "Comisión de un empleado en un periodo"
    _order = 'period_id, id'
    _rec_name = 'employee_id'

    plan_id = fields.Many2one(
        'visar.commission.plan', required=True, index=True, ondelete='cascade')
    period_id = fields.Many2one(
        'visar.commission.period', required=True, index=True, ondelete='cascade')
    plan_employee_id = fields.Many2one(
        'visar.commission.plan.employee', required=True, ondelete='cascade')
    employee_id = fields.Many2one(
        related='plan_employee_id.employee_id', store=True, index=True, readonly=True)
    currency_id = fields.Many2one(related='plan_id.currency_id', readonly=True)
    state = fields.Selection(related='period_id.state', store=True, readonly=True)
    date_from = fields.Date(related='period_id.date_from', store=True, readonly=True)
    date_to = fields.Date(related='period_id.date_to', store=True, readonly=True)
    payment_date = fields.Date(related='period_id.payment_date', store=True, readonly=True)

    # Lo medido en crudo (lo que vendió), antes de aplicarle tasa alguna. Es la
    # cifra con la que el empleado va a discutir, así que se guarda aparte.
    base_amount = fields.Monetary(
        "Vendido", currency_field='currency_id', readonly=True)
    adjustment_amount = fields.Monetary(
        "Ajustes", currency_field='currency_id', readonly=True)
    # "Logrado" = lo medido ya ponderado por la tasa de cada regla, más ajustes.
    # En el modo de tasa directa esto YA es dinero de comisión (5% de lo vendido);
    # en el modo de metas es el avance contra la meta. Es el mismo doble sentido
    # que tiene "achieved" en el nativo.
    achieved_amount = fields.Monetary(
        "Logrado", currency_field='currency_id', readonly=True)
    target_amount = fields.Monetary(
        "Meta", currency_field='currency_id', readonly=True)
    target_rate = fields.Float(
        "% de la meta", readonly=True, aggregator='avg', digits=(16, 4))
    commission_amount = fields.Monetary(
        "Comisión", currency_field='currency_id', readonly=True)

    detail_ids = fields.One2many(
        'visar.commission.line.rule', 'line_id', string="Detalle por regla",
        readonly=True)

    def _compute_display_name(self):
        for linea in self:
            linea.display_name = "%s — %s" % (
                linea.employee_id.name or '', linea.period_id.name or '')

    # ------------------------------------------------------------------
    @api.model
    def _visar_generar(self, periodo, plan_emp, desde, hasta):
        """Mide, aplica la tasa y guarda la comisión de un empleado en un periodo."""
        plan = periodo.plan_id
        empleado = plan_emp.employee_id
        detalles = []
        base_total = 0.0
        logrado = 0.0
        for regla in plan.rule_ids:
            base, lineas = regla._visar_medir(empleado, desde, hasta)
            if not base and not lineas:
                continue
            base_total += base
            logrado += base * regla.rate
            detalles.append((0, 0, {
                'rule_id': regla.id,
                'base_amount': base,
                'rate': regla.rate,
                'commission_amount': base * regla.rate,
                'sale_line_ids': [(6, 0, lineas.ids)],
            }))
        ajustes = sum(self.env['visar.commission.adjustment'].sudo().search([
            ('plan_employee_id', '=', plan_emp.id),
            ('date', '>=', desde),
            ('date', '<=', hasta),
        ]).mapped('amount'))
        logrado += ajustes
        linea = self.create({
            'plan_id': plan.id,
            'period_id': periodo.id,
            'plan_employee_id': plan_emp.id,
            'base_amount': base_total,
            'adjustment_amount': ajustes,
            'achieved_amount': logrado,
            'target_amount': periodo.target_amount,
            'detail_ids': detalles,
        })
        linea._visar_aplicar_modo()
        return linea

    def _visar_aplicar_modo(self):
        """Convierte lo logrado en comisión según el modo del plan."""
        for linea in self:
            plan = linea.plan_id
            if plan.mode == 'tasa':
                # La tasa de cada regla ya es el porcentaje de comisión, así que
                # lo logrado ES la comisión. Igual que el nativo en modo "logros".
                linea.write({
                    'target_rate': 0.0,
                    'commission_amount': linea.achieved_amount,
                })
                continue
            meta = linea.target_amount
            avance = (linea.achieved_amount / meta) if meta else 0.0
            linea.write({
                'target_rate': avance,
                'commission_amount': linea._visar_interpolar(avance),
            })

    def _visar_interpolar(self, avance):
        """Comisión de la tabla para un % de meta alcanzado.

        Entre dos escalones se interpola en línea recta; por debajo del primero se
        paga el primero y por encima del último se mantiene el último (plano), que
        es exactamente lo que hace el nativo cuando no hay escalón siguiente.
        """
        self.ensure_one()
        puntos = self.plan_id.curve_ids.sorted('target_rate')
        if not puntos:
            return self.achieved_amount
        if avance <= puntos[0].target_rate:
            return puntos[0].amount
        for bajo, alto in zip(puntos, puntos[1:]):
            if avance <= alto.target_rate:
                ancho = alto.target_rate - bajo.target_rate
                if not ancho:
                    return alto.amount
                proporcion = (avance - bajo.target_rate) / ancho
                return bajo.amount + (alto.amount - bajo.amount) * proporcion
        return puntos[-1].amount

    def action_ver_ventas(self):
        """Las ventas que sostienen esta comisión. Sin esto nadie le cree al número."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Ventas de %s en %s",
                      self.employee_id.name or '', self.period_id.name or ''),
            'res_model': 'sale.order.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.detail_ids.sale_line_ids.ids)],
            'context': {'create': False},
        }


class VisarCommissionLineRule(models.Model):
    _name = 'visar.commission.line.rule'
    _description = "Detalle de comisión por regla"
    _order = 'line_id, id'

    line_id = fields.Many2one(
        'visar.commission.line', required=True, index=True, ondelete='cascade')
    rule_id = fields.Many2one('visar.commission.rule', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='line_id.currency_id', readonly=True)
    base = fields.Selection(related='rule_id.base', readonly=True)
    origen = fields.Selection(related='rule_id.origen', readonly=True)
    base_amount = fields.Monetary(
        "Medido", currency_field='currency_id', readonly=True)
    rate = fields.Float("Tasa", readonly=True, digits=(16, 4))
    commission_amount = fields.Monetary(
        "Aporta", currency_field='currency_id', readonly=True)
    # La trazabilidad completa: exactamente qué líneas de venta entraron. Es lo
    # que se revisa cuando un vendedor reclama que le falta una venta.
    sale_line_ids = fields.Many2many(
        'sale.order.line', string="Líneas de venta incluidas", readonly=True)
    sale_line_count = fields.Integer(
        compute='_compute_sale_line_count', export_string_translation=False)

    @api.depends('sale_line_ids')
    def _compute_sale_line_count(self):
        for detalle in self:
            detalle.sale_line_count = len(detalle.sale_line_ids)
