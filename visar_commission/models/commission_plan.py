# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Periodicidades, con el salto que hay que dar para generar el siguiente periodo.
# La QUINCENA no existe en el nativo y en México es la unidad de nómina más común:
# del 1 al 15 y del 16 al fin de mes, no "cada 14 días".
PERIODICIDADES = [
    ('quincena', "Quincenal"),
    ('mes', "Mensual"),
    ('trimestre', "Trimestral"),
    ('anio', "Anual"),
]


class VisarCommissionPlan(models.Model):
    _name = 'visar.commission.plan'
    _description = "Plan de comisión (por empleado)"
    _inherit = ['mail.thread']
    _order = 'date_from desc, id desc'

    name = fields.Char("Nombre", required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string="Compañía", required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', store=True, readonly=True)

    date_from = fields.Date(
        "Desde", required=True, tracking=True,
        default=lambda self: fields.Date.today() + relativedelta(day=1, month=1))
    date_to = fields.Date(
        "Hasta", required=True, tracking=True,
        default=lambda self: fields.Date.today() + relativedelta(
            years=1, day=1, month=1) - relativedelta(days=1))
    periodicity = fields.Selection(
        PERIODICIDADES, string="Periodicidad", required=True, default='mes',
        tracking=True,
        help="Cada cuánto se corta y se paga la comisión. Al cambiarla se "
             "regeneran los periodos que todavía no estén cerrados.")

    # --- Cómo se convierte lo vendido en dinero ---
    # Las dos formas que existen en el nativo, con los mismos nombres de negocio.
    mode = fields.Selection([
        ('tasa', "Tasa directa sobre lo logrado"),
        ('meta', "Meta por periodo con tabla de comisión"),
    ], string="Modo de cálculo", required=True, default='tasa', tracking=True,
        help="Tasa directa: cada regla paga su porcentaje de lo vendido "
             "(5% de $10,000 = $500).\n"
             "Meta: lo logrado se compara contra la meta del periodo y la "
             "comisión sale de la tabla (0% de la meta, 50%, 100%…), "
             "interpolando entre escalones.")
    commission_amount = fields.Monetary(
        "Comisión al 100% de la meta", currency_field='currency_id',
        help="Lo que se paga cuando el empleado llega justo a su meta. "
             "Solo aplica en el modo de metas.")

    # --- Sobre qué importe se comisiona ---
    # El nativo siempre usa el subtotal SIN impuesto. En Visar los precios están
    # capturados IVA INCLUIDO, así que "el 5% de lo que vendió" puede significar
    # dos cifras distintas y la diferencia es del 16%. Se deja como configuración
    # en vez de elegir por el negocio.
    tax_base = fields.Selection([
        ('sin_iva', "Sin IVA (subtotal)"),
        ('con_iva', "Con IVA (total que pagó el cliente)"),
    ], string="Importe a comisionar", required=True, default='sin_iva',
        tracking=True,
        help="Los precios de Visar están capturados con IVA incluido: el "
             "subtotal de una venta de $350 es $301.72. Aquí se decide cuál de "
             "las dos cifras es la base de la comisión.")

    state = fields.Selection([
        ('draft', "Borrador"),
        ('approved', "Aprobado"),
        ('done', "Terminado"),
        ('cancel', "Cancelado"),
    ], string="Estado", required=True, default='draft', tracking=True,
        help="Solo los planes APROBADOS calculan comisión.")

    employee_ids = fields.One2many(
        'visar.commission.plan.employee', 'plan_id', string="Empleados", copy=True)
    rule_ids = fields.One2many(
        'visar.commission.rule', 'plan_id', string="Reglas de comisión", copy=True)
    period_ids = fields.One2many(
        'visar.commission.period', 'plan_id', string="Periodos",
        compute='_compute_period_ids', store=True, readonly=False, copy=True)
    curve_ids = fields.One2many(
        'visar.commission.curve', 'plan_id', string="Tabla de comisión",
        compute='_compute_curve_ids', store=True, readonly=False, copy=True)
    line_ids = fields.One2many(
        'visar.commission.line', 'plan_id', string="Comisiones calculadas",
        readonly=True)

    employee_count = fields.Integer(compute='_compute_counts', export_string_translation=False)
    rule_count = fields.Integer(compute='_compute_counts', export_string_translation=False)
    line_count = fields.Integer(compute='_compute_counts', export_string_translation=False)
    # Semáforo para la ficha: mientras negocio no defina la regla, el plan no
    # puede calcular nada y conviene que se vea al abrirlo, no al fallar.
    falta_configurar = fields.Boolean(
        compute='_compute_falta_configurar', export_string_translation=False)

    @api.depends('employee_ids', 'rule_ids', 'line_ids')
    def _compute_counts(self):
        for plan in self:
            plan.employee_count = len(plan.employee_ids)
            plan.rule_count = len(plan.rule_ids)
            plan.line_count = len(plan.line_ids)

    @api.depends('rule_ids', 'employee_ids', 'mode', 'curve_ids.amount')
    def _compute_falta_configurar(self):
        for plan in self:
            falta = not plan.rule_ids or not plan.employee_ids
            if plan.mode == 'meta' and not any(
                    c.amount for c in plan.curve_ids):
                falta = True
            plan.falta_configurar = falta

    @api.constrains('date_from', 'date_to')
    def _check_fechas(self):
        for plan in self:
            if plan.date_from and plan.date_to and plan.date_to <= plan.date_from:
                raise ValidationError(_("La fecha inicial debe ser anterior a la final."))

    # ------------------------------------------------------------------
    # Periodos: se generan solos, como en el nativo
    # ------------------------------------------------------------------
    @api.model
    def _visar_nombre_periodo(self, inicio, fin, periodicidad):
        meses = ("ene", "feb", "mar", "abr", "may", "jun",
                 "jul", "ago", "sep", "oct", "nov", "dic")
        mes = meses[inicio.month - 1]
        if periodicidad == 'quincena':
            return "%s %s %s" % ("1ª q." if inicio.day == 1 else "2ª q.", mes, inicio.year)
        if periodicidad == 'mes':
            return "%s %s" % (mes, inicio.year)
        if periodicidad == 'trimestre':
            return "%s T%s" % (inicio.year, (inicio.month - 1) // 3 + 1)
        return str(inicio.year)

    @api.model
    def _visar_tramos(self, date_from, date_to, periodicidad):
        """Lista de (inicio, fin) que cubre la vigencia, alineada al calendario.

        Alineada importa: un plan que arranca el 10 de marzo no debe producir
        periodos "10-mar al 9-abr", porque la nómina corta por quincena y por mes
        naturales. El primer tramo se recorta al inicio del plan y el último al
        final, igual que haría cualquiera a mano.
        """
        tramos = []
        cursor = date_from
        while cursor <= date_to:
            if periodicidad == 'quincena':
                if cursor.day <= 15:
                    inicio = cursor.replace(day=1)
                    fin = cursor.replace(day=15)
                else:
                    inicio = cursor.replace(day=16)
                    fin = cursor + relativedelta(day=31)
            elif periodicidad == 'mes':
                inicio = cursor.replace(day=1)
                fin = cursor + relativedelta(day=31)
            elif periodicidad == 'trimestre':
                primer_mes = (cursor.month - 1) // 3 * 3 + 1
                inicio = cursor.replace(month=primer_mes, day=1)
                fin = inicio + relativedelta(months=3, day=1) - relativedelta(days=1)
            else:
                inicio = cursor.replace(month=1, day=1)
                fin = cursor.replace(month=12, day=31)
            tramos.append((max(inicio, date_from), min(fin, date_to)))
            cursor = fin + relativedelta(days=1)
        return tramos

    @api.depends('date_from', 'date_to', 'periodicity')
    def _compute_period_ids(self):
        """Regenera los periodos, RESPETANDO los que ya se cerraron o pagaron.

        Un periodo cerrado es dinero ya autorizado: si alguien alarga el plan o
        cambia la periodicidad, ese tramo no se puede borrar ni mover.
        """
        for plan in self:
            if not plan.date_from or not plan.date_to:
                continue
            intocables = plan.period_ids.filtered(lambda p: p.state != 'abierto')
            ocupado = [(p.date_from, p.date_to) for p in intocables]
            comandos = [
                (2, p.id) for p in plan.period_ids
                if p.state == 'abierto'
            ]
            for inicio, fin in plan._visar_tramos(
                    plan.date_from, plan.date_to, plan.periodicity):
                if any(ini <= fin and inicio <= f for ini, f in ocupado):
                    continue  # ya hay un periodo cerrado encima de este tramo
                comandos.append((0, 0, {
                    'name': plan._visar_nombre_periodo(inicio, fin, plan.periodicity),
                    'date_from': inicio,
                    'date_to': fin,
                }))
            plan.period_ids = comandos

    @api.depends('mode', 'commission_amount')
    def _compute_curve_ids(self):
        """Tabla meta→comisión con los tres escalones del nativo (0%, 50%, 100%).

        Los importes quedan en cero a propósito: son justamente la regla de
        negocio que falta. Lo que se siembra es la FORMA de la tabla.
        """
        for plan in self:
            if plan.mode != 'meta' or plan.curve_ids:
                continue
            plan.curve_ids = [
                (0, 0, {'target_rate': 0.0, 'amount': 0.0}),
                (0, 0, {'target_rate': 0.5, 'amount': 0.0}),
                (0, 0, {'target_rate': 1.0, 'amount': plan.commission_amount or 0.0}),
            ]

    # ------------------------------------------------------------------
    # Estados
    # ------------------------------------------------------------------
    def action_approve(self):
        for plan in self:
            if plan.falta_configurar:
                raise UserError(_(
                    "Antes de aprobar el plan «%s» falta definir su regla de "
                    "comisión: al menos un empleado y una regla (y la tabla de "
                    "comisión si el plan va por metas).", plan.name))
        self.state = 'approved'

    def action_draft(self):
        self.state = 'draft'

    def action_done(self):
        self.state = 'done'

    def action_cancel(self):
        self.state = 'cancel'

    # ------------------------------------------------------------------
    # Cálculo
    # ------------------------------------------------------------------
    def action_calcular(self):
        """Recalcula las comisiones de los periodos ABIERTOS del plan."""
        lineas = self.env['visar.commission.line']
        for plan in self:
            if plan.state != 'approved':
                raise UserError(_(
                    "El plan «%s» no está aprobado: un plan en borrador no "
                    "calcula comisiones.", plan.name))
            lineas |= plan.period_ids.filtered(
                lambda p: p.state == 'abierto')._visar_calcular()
        return lineas

    def action_ver_lineas(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Comisiones de %s", self.name),
            'res_model': 'visar.commission.line',
            'view_mode': 'list,form',
            'domain': [('plan_id', '=', self.id)],
            'context': {'create': False},
        }
