# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

# Sobre QUÉ se comisiona. Las cuatro primeras son las del nativo, con su mismo
# significado; "cobrado" no existe allá y en Visar puede ser la que importa:
# hay pedidos pagados en línea que no llegan a facturarse (REQ-002), así que
# "facturado" y "cobrado" no son la misma cifra en este ambiente.
BASES = [
    ('importe_vendido', "Importe vendido (pedido confirmado)"),
    ('importe_facturado', "Importe facturado"),
    ('importe_cobrado', "Importe cobrado"),
    ('cantidad_vendida', "Cantidad vendida"),
    ('cantidad_facturada', "Cantidad facturada"),
]

# De qué venta se trata. El nativo no tiene este eje: allá la venta es del
# vendedor de la cabecera del documento y punto. En Visar el adicional que el
# técnico vende en campo vive DENTRO del pedido del servicio, con su propio
# empleado en la línea, así que hay dos cosas distintas que comisionar sobre el
# mismo documento.
ORIGENES = [
    ('venta_directa', "Venta del vendedor (cotización)"),
    ('upsell_campo', "Venta en campo del técnico (adicionales)"),
    ('ambas', "Las dos"),
]


class VisarCommissionRule(models.Model):
    _name = 'visar.commission.rule'
    _description = "Regla de comisión (logro)"
    _order = 'id'

    plan_id = fields.Many2one(
        'visar.commission.plan', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='plan_id.company_id', readonly=True)

    base = fields.Selection(
        BASES, string="Se comisiona sobre", required=True,
        default='importe_vendido',
        help="Qué se mide. «Vendido» cuenta al confirmarse el pedido, "
             "«facturado» al emitirse la factura y «cobrado» cuando el dinero "
             "entró (pago en línea, factura pagada o efectivo recibido en sitio).")
    origen = fields.Selection(
        ORIGENES, string="Tipo de venta", required=True, default='venta_directa',
        help="Si la venta directa y el adicional de campo se pagan distinto, se "
             "hace una regla para cada uno.")

    # Mismos dos filtros del nativo. En Visar las categorías ya están armadas con
    # el vocabulario del negocio —Servicios Core, Servicios Especializados,
    # Upsell, Pólizas y Suscripciones—, así que "5% en core y 8% en
    # especializados" se expresa con dos reglas y sin tocar código.
    product_id = fields.Many2one('product.product', string="Producto")
    product_categ_id = fields.Many2one(
        'product.category', string="Categoría de producto",
        help="Incluye las subcategorías.")

    rate = fields.Float(
        "Tasa", default=0.0, required=True, digits=(16, 4),
        help="Porcentaje que se paga sobre lo medido: 0.05 es 5%. "
             "En el modo de metas suele ser 1.0 (la venta cuenta completa "
             "para alcanzar la meta).")

    def _compute_display_name(self):
        bases = dict(self._fields['base']._description_selection(self.env))
        for regla in self:
            filtro = (regla.product_id.display_name
                      or regla.product_categ_id.complete_name or _("todo"))
            regla.display_name = "%s · %s (%s)" % (
                bases.get(regla.base, ''), filtro, regla.plan_id.name or '')

    # ------------------------------------------------------------------
    # Motor de medición
    # ------------------------------------------------------------------
    # Nada de lo que hay aquí abajo decide CUÁNTO se paga: eso son la tasa y la
    # base, que se capturan arriba. Esto solo contesta "¿cuánto de esto le toca a
    # este empleado en este periodo?".
    def _visar_dominio_atribucion(self, employee):
        """Qué líneas de venta son de ESTE empleado, según el tipo de venta.

        La venta directa excluye a propósito las líneas del upsell: si un
        vendedor cerró la cotización y luego el técnico le agregó un adicional en
        la visita, ese adicional es del técnico, no del vendedor.

        El upsell tiene dos formas porque el histórico no se migró: hasta el
        17-sep-2026 el adicional era un pedido aparte con el técnico en la
        cabecera; desde entonces es una línea marcada dentro del pedido original.
        """
        self.ensure_one()
        directa = [
            ('order_id.visar_salesperson_employee_id', '=', employee.id),
            ('visar_upsell_task_id', '=', False),
        ]
        upsell = [
            '|',
            ('visar_upsell_employee_id', '=', employee.id),
            '&', '&',
            ('visar_upsell_employee_id', '=', False),
            ('order_id.visar_upsell_task_id', '!=', False),
            ('order_id.visar_upsell_employee_id', '=', employee.id),
        ]
        if self.origen == 'venta_directa':
            return directa
        if self.origen == 'upsell_campo':
            return upsell
        # Notacion polaca: el '|' toma DOS expresiones, y `directa` son dos hojas
        # que antes hay que unir con '&'. Sin ese '&' el OR se come solo la
        # primera hoja y la regla "las dos" mide cualquier cosa.
        return ['|', '&'] + directa + upsell

    def _visar_dominio_lineas(self, employee):
        """Candidatas: líneas reales de venta de este empleado, ya filtradas por
        producto. Todavía sin filtro de fecha: cada base mide su propia fecha."""
        self.ensure_one()
        SOL = self.env['sale.order.line']
        dominio = [('display_type', '=', False)]
        # Anticipos y gastos refacturados no son venta del empleado; el nativo
        # los descarta igual. `is_expense` solo existe con sale_expense puesto.
        if 'is_downpayment' in SOL._fields:
            dominio.append(('is_downpayment', '=', False))
        if 'is_expense' in SOL._fields:
            dominio.append(('is_expense', '=', False))
        if self.product_id:
            dominio.append(('product_id', '=', self.product_id.id))
        if self.product_categ_id:
            dominio.append(('product_id.categ_id', 'child_of', self.product_categ_id.id))
        return dominio + self._visar_dominio_atribucion(employee)

    def _visar_importe(self, registro):
        """Importe de una línea (de venta o de factura), con o sin IVA.

        Los precios de Visar están capturados IVA incluido: el subtotal de una
        venta de $350 es $301.72. Cuál de las dos es la base la decide el plan.
        """
        self.ensure_one()
        if self.plan_id.tax_base == 'con_iva':
            return registro.price_total
        return registro.price_subtotal

    def _visar_medir(self, employee, date_from, date_to):
        """Cuánto logró este empleado bajo esta regla, en esta ventana.

        Devuelve (base, líneas de venta que la sostienen). La base viene con
        signo: una nota de crédito resta, igual que en el nativo.
        """
        self.ensure_one()
        SOL = self.env['sale.order.line'].sudo()
        candidatas = SOL.search(self._visar_dominio_lineas(employee))
        if not candidatas:
            return 0.0, SOL.browse()
        if self.base in ('importe_vendido', 'cantidad_vendida'):
            return self._visar_medir_vendido(candidatas, date_from, date_to)
        if self.base in ('importe_facturado', 'cantidad_facturada'):
            return self._visar_medir_facturado(candidatas, date_from, date_to)
        return self._visar_medir_cobrado(candidatas, date_from, date_to)

    def _visar_medir_vendido(self, candidatas, date_from, date_to):
        """Lo vendido cuenta el día que se CONFIRMÓ el pedido.

        Se compara `date_order` (que Odoo guarda en UTC) contra el periodo tal
        cual, igual que el nativo. Una venta cerrada a las 19:00 del último día
        del mes cae en el mes siguiente por el desfase de 6 horas; es el
        comportamiento estándar de Odoo y conviene saberlo antes que descubrirlo.
        """
        self.ensure_one()
        lineas = candidatas.filtered(
            lambda l: l.order_id.state == 'sale'
            and l.order_id.date_order
            and date_from <= l.order_id.date_order.date() <= date_to)
        if self.base == 'cantidad_vendida':
            return sum(lineas.mapped('product_uom_qty')), lineas
        return sum(self._visar_importe(l) for l in lineas), lineas

    def _visar_facturas_de(self, linea, solo_pagadas=False):
        """Apuntes de factura publicados que vienen de esta línea de venta."""
        apuntes = linea.invoice_lines.filtered(
            lambda aml: aml.move_id.state == 'posted'
            and aml.move_id.move_type in ('out_invoice', 'out_refund'))
        if solo_pagadas:
            apuntes = apuntes.filtered(
                lambda aml: aml.move_id.payment_state in ('paid', 'in_payment'))
        return apuntes

    def _visar_medir_facturado(self, candidatas, date_from, date_to):
        """Lo facturado cuenta el día de la factura, y la nota de crédito resta."""
        self.ensure_one()
        base = 0.0
        lineas = self.env['sale.order.line'].sudo().browse()
        for linea in candidatas:
            for aml in self._visar_facturas_de(linea):
                fecha = aml.move_id.invoice_date or aml.move_id.date
                if not fecha or not (date_from <= fecha <= date_to):
                    continue
                signo = -1 if aml.move_id.move_type == 'out_refund' else 1
                if self.base == 'cantidad_facturada':
                    base += signo * aml.quantity
                else:
                    base += signo * self._visar_importe(aml)
                lineas |= linea
        return base, lineas

    def _visar_medir_cobrado(self, candidatas, date_from, date_to):
        """Lo cobrado: cuándo entró el dinero, por los tres caminos que existen.

        Por orden de certeza contable:

          1. La factura de esa línea está pagada (o en proceso de pago). Es el
             caso limpio y el que usaría cualquiera.
          2. No hay factura pero el pedido tiene una transacción de pago en línea
             liquidada. Este caso existe de verdad: 7 de los 9 pedidos con venta
             en campo estaban pagados y sin facturar (17-sep-2026). Sin esta rama
             el vendedor no cobraría comisión de una venta que sí entró.
          3. Es un adicional de campo y el técnico selló que recibió el efectivo.
             Administración concilia después; el dinero ya está en la calle.

        Se toma el primer camino que aplique, nunca dos: si la factura está
        pagada, la transacción del pedido es ese mismo dinero.
        """
        self.ensure_one()
        base = 0.0
        lineas = self.env['sale.order.line'].sudo().browse()
        for linea in candidatas:
            pagadas = self._visar_facturas_de(linea, solo_pagadas=True)
            if pagadas:
                for aml in pagadas:
                    fecha = aml.move_id.invoice_date or aml.move_id.date
                    if not fecha or not (date_from <= fecha <= date_to):
                        continue
                    signo = -1 if aml.move_id.move_type == 'out_refund' else 1
                    base += signo * self._visar_importe(aml)
                    lineas |= linea
                continue
            fecha = self._visar_fecha_de_pago(linea)
            if fecha and date_from <= fecha <= date_to:
                base += self._visar_importe(linea)
                lineas |= linea
        return base, lineas

    @api.model
    def _visar_fecha_de_pago(self, linea):
        """Día en que entró el dinero de una línea SIN factura pagada, o False."""
        pedido = linea.order_id
        transacciones = pedido.transaction_ids.filtered(
            lambda t: t.state in ('done', 'authorized'))
        if transacciones:
            tx = transacciones.sorted('id')[0]
            fecha = tx.last_state_change or tx.create_date
            return fecha.date() if fecha else False
        tarea = linea.visar_upsell_task_id or pedido.visar_upsell_task_id
        if tarea and tarea.visar_upsell_cash_at:
            return tarea.visar_upsell_cash_at.date()
        if tarea and pedido.visar_upsell_cash_at:
            return pedido.visar_upsell_cash_at.date()
        return False
