import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    visar_visit_ids = fields.One2many(
        'project.task', 'visar_subscription_order_id',
        string="Visitas de la póliza", copy=False,
    )
    visar_visit_count = fields.Integer(
        string="Nº de visitas", compute='_compute_visar_visit_count',
    )
    visar_is_poliza = fields.Boolean(
        string="Es póliza (genera visitas)", compute='_compute_visar_is_poliza',
    )
    visar_included_visits = fields.Integer(
        string="Visitas incluidas",
        compute='_compute_visar_included_visits', store=True, readonly=False,
        copy=True,
        help="Visitas que genera cada factura de esta póliza. Se hereda del plan y "
             "se puede ajustar aquí sin tocar el plan. No afecta el precio ni el "
             "calendario de facturación.\n\n"
             "0 = derivar el nº de visitas de los periodos cobrados por adelantado.",
    )
    # Siniestralidad (Fase 5): consumo de garantía para ajustar renovación.
    visar_service_visit_count = fields.Integer(
        string="Servicios ejecutados", compute='_compute_visar_siniestralidad',
    )
    visar_warranty_count = fields.Integer(
        string="Visitas de garantía", compute='_compute_visar_siniestralidad',
    )
    visar_warranty_rate = fields.Float(
        string="Tasa de garantía (%)", compute='_compute_visar_siniestralidad',
        help="Visitas de garantía / servicios ejecutados. Indicador de siniestralidad "
             "para ajustar el precio en la renovación.",
    )
    visar_last_warranty_date = fields.Date(
        string="Última reincidencia", compute='_compute_visar_siniestralidad',
    )

    @api.depends('visar_visit_ids')
    def _compute_visar_visit_count(self):
        data = self.env['project.task']._read_group(
            [('visar_subscription_order_id', 'in', self.ids)],
            ['visar_subscription_order_id'], ['__count'],
        )
        counts = {order.id: count for order, count in data}
        for order in self:
            order.visar_visit_count = counts.get(order.id, 0)

    @api.depends('is_subscription', 'order_line.product_id')
    def _compute_visar_is_poliza(self):
        for order in self:
            order.visar_is_poliza = order._visar_is_poliza()

    def _visar_is_poliza(self):
        self.ensure_one()
        return bool(self.is_subscription and any(
            l.product_id.product_tmpl_id.visar_generates_visit
            for l in self.order_line))

    @api.depends('plan_id')
    def _compute_visar_included_visits(self):
        """Hereda las visitas incluidas del plan, dejándolas editables en la orden.

        Es compute y no onchange a propósito: en el flujo web la orden es el carrito y
        el plan se asigna con `write` desde el controlador y desde `_cart_add`, donde
        los onchange no corren. Al ser `readonly=False`, un valor puesto a mano en la
        póliza se respeta hasta que alguien cambie el plan.
        """
        for order in self:
            order.visar_included_visits = (
                order.plan_id.visar_included_visits if order.plan_id else 0)

    @api.depends('visar_visit_ids', 'visar_visit_ids.visar_is_warranty')
    def _compute_visar_siniestralidad(self):
        for order in self:
            visits = order.visar_visit_ids
            warranty = visits.filtered('visar_is_warranty')
            service = visits - warranty
            order.visar_service_visit_count = len(service)
            order.visar_warranty_count = len(warranty)
            order.visar_warranty_rate = (
                100.0 * len(warranty) / len(service)) if service else 0.0
            wdates = [d for d in (order._visar_task_date(t) for t in warranty) if d]
            order.visar_last_warranty_date = max(wdates) if wdates else False

    @api.model
    def _visar_task_date(self, task):
        """Fecha 'de servicio' de una visita: cierre de campo si existe, si no el
        deadline o la fecha de escritura."""
        d = getattr(task, 'visar_field_closed_at', False) or task.date_deadline or task.write_date
        return fields.Date.to_date(d) if d else False

    def _visar_last_service_date(self):
        """Fecha del último servicio (no garantía) ejecutado; fallback a la última
        factura de la póliza."""
        self.ensure_one()
        service = self.visar_visit_ids.filtered(lambda t: not t.visar_is_warranty)
        dates = [d for d in (self._visar_task_date(t) for t in service) if d]
        if dates:
            return max(dates)
        inv = self.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice' and m.invoice_date).sorted('invoice_date')
        return inv[-1].invoice_date if inv else False

    # ------------------------------------------------------------------
    # Fecha "hasta" (fin) automática según la duración del plan/póliza
    # ------------------------------------------------------------------
    def _visar_compute_end_date(self):
        """Devuelve la fecha de fin = inicio + duración del plan (o None)."""
        self.ensure_one()
        months = self.plan_id.visar_commitment_months if self.plan_id else 0
        if not months:
            return None
        base = self.start_date or fields.Date.context_today(self)
        return base + relativedelta(months=months)

    @api.onchange('plan_id', 'start_date')
    def _onchange_visar_end_date(self):
        for order in self:
            if order.plan_id and not order.end_date:
                end = order._visar_compute_end_date()
                if end:
                    order.end_date = end

    def action_confirm(self):
        for order in self.filtered(lambda o: o.is_subscription):
            order._visar_apply_combo_discount()
            # Re-espejar DESPUÉS del descuento de combo: en el alta desde backend el
            # descuento se aplica aquí, así que un anticipo creado antes traería el
            # precio sin descontar. Es idempotente, así que en el flujo web (donde el
            # carrito ya dejó las líneas listas) no mueve el total ya autorizado.
            order._visar_sync_anticipo_lines()
        res = super().action_confirm()
        for order in self.filtered(lambda o: o.is_subscription and not o.end_date):
            end = order._visar_compute_end_date()
            if end:
                order.end_date = end
        return res

    # ------------------------------------------------------------------
    # Descuento de combo para pólizas (Fase 4) — reusa visar.combo.rule
    # ------------------------------------------------------------------
    def _visar_apply_combo_discount(self):
        """Aplica el descuento de combo a las líneas de una póliza que incluye varios
        servicios base cuyas dimensiones cumplen una regla de visar.combo.rule. Así
        una 'póliza combo' (p.ej. Fumigación + Corte) recibe el mismo descuento que
        en el flujo de booking. Idempotente: fija el mismo % si se re-ejecuta."""
        self.ensure_one()
        if not self._visar_is_poliza():
            return
        lines = self.order_line.filtered(
            lambda l: l.product_id.product_tmpl_id.visar_generates_visit
            and l.product_id.product_tmpl_id.visar_dimension_id)
        dim_ids = lines.mapped('product_id.product_tmpl_id.visar_dimension_id').ids
        if len(set(dim_ids)) < 2:
            return
        rules = self.env['visar.combo.rule'].sudo().search(
            [('active', '=', True)], order='sequence')
        for rule in rules:
            if not rule._visar_applies_to_items(dim_ids):
                continue
            pct = rule._visar_discount_percent()
            for line in lines:
                if line.product_id.product_tmpl_id.visar_dimension_id.id in rule.discount_dimension_ids.ids:
                    line.discount = pct
            break  # primera regla aplicable por secuencia

    def action_visar_apply_combo_discount(self):
        """Botón para previsualizar el descuento de combo antes de confirmar."""
        for order in self:
            order._visar_apply_combo_discount()
        return True

    # ------------------------------------------------------------------
    # Bloqueo de cambio de dirección de servicio en pólizas (Fase 3)
    # ------------------------------------------------------------------
    def write(self, vals):
        if 'partner_shipping_id' in vals:
            new_id = vals.get('partner_shipping_id')
            for order in self:
                if (order.state == 'sale' and order._visar_is_poliza()
                        and order.partner_shipping_id.id != new_id):
                    raise UserError(_(
                        "No se puede cambiar la dirección de servicio de una "
                        "póliza confirmada (%s).", order.name))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Cobro inicial de N periodos — líneas de anticipo (Fase 1)
    # ------------------------------------------------------------------
    def _visar_first_invoice_periods(self):
        """Nº de periodos que el carrito cobra por adelantado, según el plan.

        1 = comportamiento normal. La Póliza Mensual usa 2 (cobra el mes 1 y el 2 de
        entrada). Los planes bimestral/trimestral usan 1: su propio periodo ya cubre
        dos meses o más, así que no necesitan línea de anticipo.
        """
        self.ensure_one()
        n = self.plan_id.visar_first_invoice_periods if self.plan_id else 1
        return n if n and n > 0 else 1

    def _visar_prepaid_periods_for_line(self, line):
        """Periodos que esta línea de servicio tiene realmente pagados de entrada.

        Se calcula desde las líneas de anticipo existentes —lo que de verdad se
        vendió y se cobró— y no desde la configuración del plan, que pudo cambiar
        después de firmar la póliza.
        """
        self.ensure_one()
        anticipo = self.order_line.filtered(
            lambda l: l.visar_anticipo_for_line_id == line)
        return 1 + sum(anticipo.mapped('visar_anticipo_periods'))

    def _visar_sync_anticipo_lines(self):
        """Crea/actualiza una línea de anticipo por cada línea de servicio recurrente.

        Una línea por servicio (no una sola sumada) para que el IVA y el descuento de
        combo se reproduzcan exactos por línea, para poder contar los periodos pagados
        de CADA servicio en una póliza combo, y para que al quitar un servicio se vaya
        su anticipo con él (ondelete cascade).

        Debe llamarse DESPUÉS de fijar los descuentos de todas las líneas: espeja el
        estado final de cada línea de servicio.
        """
        self.ensure_one()
        Line = self.env['sale.order.line'].with_context(
            visar_skip_mandatory_addons=True)
        extra = self._visar_first_invoice_periods() - 1
        existing = self.order_line.filtered('visar_anticipo_for_line_id')

        if extra <= 0 or not self.plan_id:
            existing.unlink()
            return self.env['sale.order.line']

        services = self.order_line.filtered(
            lambda l: l.recurring_invoice and not l.visar_anticipo_for_line_id
            and not l.display_type)
        # Anticipos huérfanos (su servicio ya no está en la orden).
        existing.filtered(lambda l: l.visar_anticipo_for_line_id not in services).unlink()

        result = self.env['sale.order.line']
        for service in services:
            vals = {
                'product_id': self.env['product.template']._visar_get_anticipo_template(
                    service.product_id).product_variant_id.id,
                'name': _("%(service)s — mensualidad adelantada", service=service.name),
                'product_uom_qty': service.product_uom_qty * extra,
                'price_unit': service.price_unit,
                'discount': service.discount,
                # Los impuestos se copian de la línea de servicio, no se dejan al
                # producto: el anticipo tiene que cobrar exactamente lo mismo que el
                # servicio que espeja, y un único producto de anticipo no puede
                # cuadrar con servicios de tasas distintas.
                'tax_ids': [(6, 0, service.tax_ids.ids)],
                'visar_anticipo_for_line_id': service.id,
                'visar_anticipo_periods': extra,
            }
            anticipo = existing.filtered(
                lambda l: l.visar_anticipo_for_line_id == service)[:1]
            if anticipo:
                anticipo.write(vals)
            else:
                anticipo = Line.create(dict(vals, order_id=self.id))
            result |= anticipo
        return result

    def _get_update_prices_lines(self):
        """Excluye las líneas de anticipo del recálculo masivo de precios.

        `_recompute_prices()` reasigna `price_unit` desde la lista de precios Y pone
        `discount` a 0. Se dispara solo al escribir la dirección del cliente en el
        checkout (`res.partner.write` sobre country/vat/zip mueve la posición fiscal),
        así que sin este filtro el anticipo caería al `list_price` del producto (0) y
        el cliente pagaría de menos sin que nada avise.
        """
        return super()._get_update_prices_lines().filtered(
            lambda l: not l.visar_anticipo_for_line_id)

    # ------------------------------------------------------------------
    # Generación de visitas — gatada al PAGO de la factura (Fase 1)
    # (disparada desde account.move._invoice_paid_hook)
    # ------------------------------------------------------------------
    def _visar_visits_for_line(self, line, is_first):
        """Nº de visitas que esta factura genera para esta línea de servicio.

        Con visitas incluidas > 0 el número lo fija la póliza y es el mismo en cada
        factura: es lo que permite vender un plan anual de un solo pago con 12 visitas
        sin cobrar 12 años. Con 0 se conserva el comportamiento histórico, donde las
        visitas del primer ciclo se derivan de los periodos realmente cobrados por
        adelantado.

        Se lee de la ORDEN y no del plan para respetar el ajuste manual por póliza.
        """
        self.ensure_one()
        if self.visar_included_visits > 0:
            return self.visar_included_visits
        return self._visar_prepaid_periods_for_line(line) if is_first else 1

    def _visar_visit_lines(self):
        """Líneas de la póliza que generan visita y tienen proyecto FSM configurado."""
        self.ensure_one()
        return self.order_line.filtered(
            lambda l: l.product_id.product_tmpl_id.visar_generates_visit
            and l.product_id.product_tmpl_id.visar_fsm_project_id)

    def _visar_visit_groups(self, is_first):
        """[(líneas, proyecto, nº de visitas)] — el trabajo del periodo, ya consolidado.

        Una póliza combo (fumigación + áreas verdes) es UNA visita, no dos: el técnico
        va una vez, llena una hoja, firma una vez y el cliente recibe UN aviso de "voy
        en camino". Se agrupa por proyecto EFECTIVO con la MISMA regla de configuración
        que la venta puntual (`project.project._visar_effective_projects`), así que dar
        de alta un tercer servicio combinable sigue siendo marcar un campo en su
        proyecto y no tocar código, y el resultado no depende de por dónde se contrató
        (wizard web, agente de WhatsApp o alta manual en Ventas).

        GUARDIA — nº de visitas distinto: si dos líneas del mismo grupo generan
        distinto número de visitas por factura (p. ej. 12 podas y 6 fumigaciones al
        año), no se consolidan. Consolidar solo la parte que coincide dejaría al
        cliente sin las visitas de la diferencia, y consolidarlo todo le regalaría o
        le quitaría servicios: mejor dos visitas separadas, como hasta ahora.
        """
        self.ensure_one()
        lines = self._visar_visit_lines()
        if not lines:
            return []
        Project = self.env['project.project']
        line_project = {
            line.id: line.product_id.product_tmpl_id.visar_fsm_project_id
            for line in lines}
        projects = Project.browse(
            sorted({project.id for project in line_project.values()}))
        effective = Project._visar_effective_projects(projects)

        grouped = {}
        for line in lines:
            project = effective[line_project[line.id].id]
            grouped.setdefault(project.id, self.env['sale.order.line'])
            grouped[project.id] |= line

        groups = []
        for project_id, group_lines in grouped.items():
            counts = {self._visar_visits_for_line(line, is_first)
                      for line in group_lines}
            if len(group_lines) > 1 and len(counts) > 1:
                _logger.info(
                    "Póliza %s: %s líneas comparten proyecto pero piden %s visitas "
                    "distintas; se generan por separado.",
                    self.name, len(group_lines), sorted(counts))
                for line in group_lines:
                    groups.append((line, line_project[line.id],
                                   self._visar_visits_for_line(line, is_first)))
                continue
            groups.append((group_lines, Project.browse(project_id), counts.pop()))
        return groups

    def _visar_generate_period_visit(self, invoice):
        self.ensure_one()
        if not self.is_subscription or self.subscription_state != '3_progress':
            return
        if not invoice or invoice.move_type != 'out_invoice':
            return
        Task = self.env['project.task']
        first_invoice = self.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice').sorted('id')[:1]
        is_first = invoice == first_invoice
        for lines, project, n in self._visar_visit_groups(is_first):
            # Idempotencia por (orden, factura, grupo de líneas): crear las que falten.
            existing = Task.search_count([
                ('visar_subscription_order_id', '=', self.id),
                ('visar_source_invoice_id', '=', invoice.id),
                ('visar_source_line_ids', 'in', lines.ids),
                ('visar_is_warranty', '=', False),
            ])
            # Se arranca el rango en las que ya existen (y no en 0) para que el
            # consecutivo del título siga la numeración cuando una corrida anterior
            # dejó el lote a medias.
            for seq in range(existing, n):
                Task.create(self._visar_visit_vals(
                    lines, project, invoice, seq=seq + 1, total=n))
            if is_first:
                self._visar_enrich_first_visit(lines)

    def _visar_enrich_first_visit(self, lines):
        """La PRIMERA visita del grupo hereda fecha y técnicos de la cita reservada.

        Al confirmar una póliza, `_timesheet_service_generation` saca las líneas de
        póliza antes de que visar_fsm cree su tarea, así que el horario y el técnico
        que el cliente eligió en el wizard no quedan en ninguna tarea. Esta es la que
        los recoge; las demás visitas del ciclo nacen sin agendar.

        Se relee la visita más antigua por id en vez de usar el índice del bucle: si
        una corrida anterior creó solo parte de las visitas, el índice 0 le tocaría a
        la segunda. El guardia sobre `planned_date_begin` la hace idempotente frente
        a los dos disparadores distintos de `_invoice_paid_hook`.
        """
        self.ensure_one()
        first = self.env['project.task'].search([
            ('visar_subscription_order_id', '=', self.id),
            ('visar_source_line_ids', 'in', lines.ids),
            ('visar_is_warranty', '=', False),
        ], order='id', limit=1)
        if first and not first.planned_date_begin:
            self._visar_enrich_fsm_tasks(first)

    @api.model
    def _visar_visit_service_label(self, lines):
        """Qué servicios cubre la visita, para su título.

        Una visita consolidada se nombra con sus GRUPOS de servicio ("Fumigación +
        Mantenimiento de áreas verdes"), igual que la tarea consolidada de la venta
        puntual (`project.task._visar_rename_from_services`): el nombre del producto de
        una sola línea nombraría media visita, y es lo que el técnico lee en su tarjeta.
        Un producto de póliza sin dimensión configurada no tiene grupo, así que se cae a
        los nombres de producto (sin repetir).
        """
        if len(lines) == 1:
            return lines.product_id.name
        groups = lines.mapped('product_id.product_tmpl_id')._visar_service_groups()
        if len(groups) > 1:
            return ' + '.join(groups.mapped('name'))
        return ' + '.join(dict.fromkeys(lines.mapped('product_id.name')))

    def _visar_visit_vals(self, lines, project, invoice, warranty=False, seq=0, total=0):
        """Valores de UNA visita. `lines` es el grupo que atiende (una o varias)."""
        self.ensure_one()
        period = invoice.invoice_date if invoice else fields.Date.context_today(self)
        label = _("Garantía") if warranty else _("Visita")
        name = _("%(label)s póliza %(period)s — %(product)s",
                 label=label, period=period,
                 product=self._visar_visit_service_label(lines))
        # Un lote de N visitas nace con la misma fecha y el mismo servicio, así que sin
        # consecutivo quedarían N tareas de título idéntico en el tablero.
        if total > 1:
            name = _("%(name)s (%(seq)s/%(total)s)", name=name, seq=seq, total=total)
        return {
            'name': name,
            'project_id': project.id,
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'visar_subscription_order_id': self.id,
            'visar_source_invoice_id': False if warranty else (invoice.id if invoice else False),
            # El m2o guarda la línea REPRESENTANTE (compatibilidad con vistas y datos
            # anteriores); el conjunto real de líneas atendidas va en el m2m.
            'visar_source_line_id': False if warranty else lines[:1].id,
            'visar_source_line_ids': False if warranty else [(6, 0, lines.ids)],
            'visar_is_warranty': warranty,
        }

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    # Ventana de garantía (días desde el último servicio). Configurable a futuro.
    VISAR_WARRANTY_DAYS = 30

    def _visar_check_warranty_eligibility(self):
        """Valida elegibilidad de garantía: póliza activa + reincidencia dentro de la
        ventana de N días desde el último servicio."""
        self.ensure_one()
        if self.subscription_state != '3_progress':
            raise UserError(_(
                "La póliza no está activa (%s); la garantía no aplica.",
                self.subscription_state or '—'))
        last = self._visar_last_service_date()
        if not last:
            raise UserError(_(
                "No hay un servicio previo registrado para validar la garantía."))
        days = (fields.Date.context_today(self) - last).days
        if days > self.VISAR_WARRANTY_DAYS:
            raise UserError(_(
                "La garantía cubre reincidencias dentro de %(win)s días del último "
                "servicio; han pasado %(days)s días (último servicio: %(date)s).",
                win=self.VISAR_WARRANTY_DAYS, days=days, date=last))

    def action_visar_add_warranty_visit(self):
        """Crea una visita de garantía (sin costo) ligada a la póliza, validando
        elegibilidad (póliza activa + reincidencia <30 días).

        La garantía NO se consolida aunque la póliza sea combo: se regresa a repetir el
        servicio que falló, no a prestar los dos otra vez. Va al proyecto de la primera
        línea de servicio; si el que reincidió es el otro, se cambia a mano.
        """
        self.ensure_one()
        self._visar_check_warranty_eligibility()
        line = self._visar_visit_lines()[:1]
        if not line:
            return False
        tmpl = line.product_id.product_tmpl_id
        task = self.env['project.task'].create(
            self._visar_visit_vals(line, tmpl.visar_fsm_project_id, invoice=False, warranty=True)
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'project.task',
            'res_id': task.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_visar_view_visits(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Visitas de la póliza"),
            'res_model': 'project.task',
            'domain': [('visar_subscription_order_id', '=', self.id)],
            'view_mode': 'list,form',
            'context': {'default_visar_subscription_order_id': self.id},
        }
