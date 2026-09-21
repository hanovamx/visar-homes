# -*- coding: utf-8 -*-
"""Venta de SERVICIOS en campo: el trabajo extra se hace en la misma visita.

El caso que lo motiva (Visar, 18-sep-2026): el técnico va a una **visita de
valoración** y ahí mismo puede dejar hecha la fumigación o el mantenimiento de áreas
verdes, en vez de volver otro día. Hasta ahora el catálogo de campo solo ofrecía
productos sueltos (una estación antirroedores); un servicio no aparecía aunque
tuviera marcado "Vendible en campo", porque sus reglas lo dejaban fuera por buenas
razones (ver `product.template._visar_upsell_domain`).

Lo que pasa ahora, y por qué cada pieza es así:

1. **El precio sale del mismo motor que el agendado web y el agente de WhatsApp.**
   El técnico captura metros cuadrados por dimensión (interior, exterior, jardín) y
   se cotiza con `appointment.type._visar_build_sale_lines`: la misma casa cuesta lo
   mismo por los tres canales, con el descuento de combo incluido. La traducción de
   m² a tramo es `visar.service.dimension._visar_quote_item`, compartida con el
   agente, porque el tramo equivocado cobra de menos sin avisar.
2. **La línea entra al pedido de la visita y Odoo crea el servicio nuevo**, igual que
   cuando alguien agrega el servicio a mano en el backend (S00300, S00284 y S00017,
   17 y 18-sep-2026: "S00300 - Fumigación interior o exterior (A, 1-250, 0 - 50)",
   con su hoja de fumigación). Mientras el técnico arma el carrito la línea cuelga
   de la visita de origen —un pedido confirmado genera la tarea en cuanto nace la
   línea, y un carrito que se edita dejaría servicios fantasma—; al **generar el
   cobro** se suelta y se deja que `visar_fsm` genere la tarea con su regla de
   siempre (incluida la consolidación: fumigación + áreas verdes es UNA visita).
3. **El servicio nuevo arranca ya "En ejecución", en el paso de la hoja**, con los
   mismos técnicos y la fecha de hoy. El técnico ya está en el domicilio: pasar por
   "Voy en camino" y "Confirmar llegada" le mandaría al cliente otra vez los avisos
   de WhatsApp de algo que está pasando delante de él.
4. **La visita de valoración se descuenta del servicio contratado a partir de ella**
   (política de Visar). Antes se hacía a mano con una línea "Descuento" de −$500
   (S00138, S00144, S00147). Reglas, confirmadas con Visar el 18-sep-2026: el monto
   sale de la línea de valoración del pedido (no de una constante), UNA vez por
   pedido, SOLO contra servicios, nunca por más que el servicio, y solo si la
   valoración ya está pagada.
5. **La liga de pago sale del número de Visar** (aviso `upsell_payment`), además del
   botón con el que el técnico la puede mandar desde su teléfono.
6. **Pago de prueba bajo un ajuste explícito.** El proveedor Demo marca la factura
   pagada sin que entre un peso; por eso la app lo esconde. Con el ajuste encendido
   se ofrece —con una marca PRUEBA visible— para poder probar el flujo completo.
"""
from datetime import timedelta

from markupsafe import Markup

from odoo import fields, models

PARAM_PAGO_PRUEBA = 'visar_field.upsell_permitir_pago_prueba'
PARAM_PRODUCTO_CREDITO = 'visar_field.upsell_producto_credito_valoracion'


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    visar_valuation_credit = fields.Boolean(
        string="Descuento de la visita de valoración", readonly=True, copy=False,
        help="Línea que descuenta lo pagado por la visita de valoración del servicio "
             "contratado a partir de ella. La crea la venta en campo; hay una sola por "
             "pedido.")


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_upsell_allow_test_payment = fields.Boolean(
        string="Permitir pagos de prueba en campo",
        config_parameter=PARAM_PAGO_PRUEBA,
        help="Ofrece en la app de campo los proveedores de pago en modo PRUEBA (hoy, "
             "Demo). Un pago de prueba marca la factura como pagada SIN que entre "
             "dinero: la app lo señala con una marca PRUEBA. Apagar antes de salir "
             "en vivo.")
    visar_upsell_credit_product_id = fields.Many2one(
        'product.product',
        string="Producto del descuento por valoración",
        config_parameter=PARAM_PRODUCTO_CREDITO,
        help="Producto con el que se registra el descuento de la visita de valoración "
             "cuando el técnico vende un servicio en esa misma visita. Sin producto, "
             "no se descuenta nada.")


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_upsell_origin_task_id = fields.Many2one(
        'project.task', string="Vendido en la visita", readonly=True, copy=False,
        index='btree_not_null',
        help="Visita en la que el técnico vendió este servicio y lo hizo ahí mismo.")
    visar_upsell_service_task_ids = fields.One2many(
        'project.task', 'visar_upsell_origin_task_id',
        string="Servicios vendidos en esta visita", readonly=True)
    visar_upsell_link_sent_at = fields.Datetime(
        string="Liga de pago enviada", readonly=True, copy=False,
        help="Cuándo se le mandó al cliente, desde el número de Visar, la liga de pago "
             "de lo vendido en esta visita.")

    # ------------------------------------------------------------------
    # Qué servicios se pueden vender aquí
    # ------------------------------------------------------------------
    def _visar_upsell_service_offers(self):
        """Dimensiones de servicio vendibles en esta visita, para pintar el catálogo.

        Una dimensión (fumigación interior, exterior, áreas verdes) y no un producto,
        porque el precio depende de los m² y de la zona: el técnico captura metros y el
        motor elige la variante. Sin zona resoluble no se ofrece nada — cotizar sin
        zona es cobrar la lista equivocada.
        """
        self.ensure_one()
        if not self._visar_upsell_zone():
            return []
        Template = self.env['product.template'].sudo()
        offers = []
        for dimension in self.env['visar.service.dimension'].sudo().search(
                [('active', '=', True)]):
            template = Template._visar_get_service_template_for_dimension(dimension)
            if (not template or not template.sale_ok or not template.visar_upsell_ok
                    or template.visar_is_valuation or not template.visar_is_service):
                continue
            offers.append({
                'id': dimension.id,
                'label': dimension._visar_wizard_label(),
                'product': template.name,
            })
        return offers

    def _visar_upsell_add_service(self, employee, m2_by_dimension):
        """Cotiza con el motor del agendado y agrega el servicio al carrito.

        Devuelve (ok, error). `error` ∈ {'cerrado', 'sin_zona', 'sin_m2',
        'sin_producto', 'sin_tramo', 'valoracion', 'sin_precio', 'sin_pedido'}.

        `valoracion`: los m² caen en el tramo que en el agendado manda a visita de
        valoración (casa demasiado grande para el tabulador). En campo no tiene
        sentido venderle una valoración a quien ya se está valorando; ese precio lo
        cotiza oficina.
        """
        self.ensure_one()
        if self._visar_upsell_state() not in ('vacio', 'borrador'):
            return False, 'cerrado'
        zone = self._visar_upsell_zone()
        if not zone:
            return False, 'sin_zona'
        ofrecibles = {offer['id'] for offer in self._visar_upsell_service_offers()}
        Dimension = self.env['visar.service.dimension'].sudo()
        items = []
        for dimension_id, m2 in m2_by_dimension.items():
            if dimension_id not in ofrecibles or not m2 or m2 <= 0:
                continue
            item, error = Dimension.browse(dimension_id)._visar_quote_item(m2)
            if error:
                return False, error
            if item['is_valuation']:
                return False, 'valoracion'
            items.append(item)
        if not items:
            return False, 'sin_m2'

        Motor = self.env['appointment.type'].sudo()
        sale_lines = Motor._visar_build_sale_lines(items, zone)
        sale_lines = [vals for vals in sale_lines or []
                      if not Motor._visar_skip_cart_line(vals, zone)]
        if not sale_lines:
            return False, 'sin_precio'
        order = self._visar_upsell_order(employee=employee, create=True)
        if not order:
            return False, 'sin_pedido'
        Product = self.env['product.product'].sudo()
        for vals in sale_lines:
            product = Product.browse(vals['product_id']).exists()
            if not product:
                continue
            quantity = vals.get('quantity') or 1
            # Mismo precio que el agendado: lista de la ZONA y el descuento que el
            # motor haya decidido (combo). Se fija explícito porque el pedido de la
            # visita puede traer otra lista.
            self.env['sale.order.line'].sudo().with_context(
                **self._VISAR_UPSELL_LINE_CTX).create({
                    'order_id': order.id,
                    'product_id': product.id,
                    'product_uom_qty': quantity,
                    'price_unit': Motor._visar_list_unit_price(product, zone),
                    'discount': vals.get('discount') or 0.0,
                    # Cuelga de la visita mientras el carrito se edita; al generar
                    # el cobro se suelta para que nazca su propio servicio (ver
                    # `_visar_upsell_spawn_service_tasks`).
                    'task_id': self.id,
                    'visar_upsell_task_id': self.id,
                    'visar_upsell_employee_id': employee.id if employee else False,
                    'visar_upsell_at': fields.Datetime.now(),
                })
            self._visar_upsell_log(order, employee, product.id, quantity)
        self._visar_upsell_sync_valuation_credit()
        return True, None

    # ------------------------------------------------------------------
    # Descuento de la visita de valoración
    # ------------------------------------------------------------------
    def _visar_upsell_credit_product(self):
        param = self.env['ir.config_parameter'].sudo().get_param(PARAM_PRODUCTO_CREDITO)
        if not param or not str(param).isdigit():
            return self.env['product.product'].sudo().browse()
        return self.env['product.product'].sudo().browse(int(param)).exists()

    @staticmethod
    def _visar_line_amount(line):
        """Importe de una línea en la misma base que `price_unit` (antes de impuesto
        si el impuesto no va incluido, con él si va incluido): la del descuento
        copia los impuestos de la valoración, así que comparar en esta base es
        comparar lo mismo."""
        return line.price_unit * (1.0 - (line.discount or 0.0) / 100.0) * line.product_uom_qty

    def _visar_upsell_valuation_lines(self):
        """Líneas de visita de valoración del pedido que originó esta visita."""
        self.ensure_one()
        source = self.sale_order_id.sudo()
        return source.order_line.filtered(
            lambda l: not l.display_type and l.product_uom_qty > 0
            and l.product_id.product_tmpl_id.visar_is_valuation)

    def _visar_valuation_is_paid(self, valuation_lines):
        """¿Ya se cobró la valoración? Por factura pagada o por pago en línea.

        Los dos caminos existen en producción: la mayoría de los pedidos llega a la
        visita pagado en línea pero todavía SIN factura (7 de 9 medidos el
        17-sep-2026), así que exigir factura dejaría sin descuento a casi todos.
        """
        invoices = valuation_lines.invoice_lines.move_id.filtered(
            lambda m: m.move_type == 'out_invoice' and m.state == 'posted')
        if invoices and all(m.payment_state in ('paid', 'in_payment') for m in invoices):
            return True
        source = valuation_lines.order_id[:1]
        pagado = sum(source.transaction_ids.filtered(
            lambda t: t.state in ('done', 'authorized')).mapped('amount'))
        return pagado > 0 and pagado + 0.01 >= sum(valuation_lines.mapped('price_total'))

    def _visar_upsell_valuation_credit(self):
        """(importe, línea de valoración) del descuento que toca, o (0, vacío)."""
        self.ensure_one()
        vacio = (0.0, self.env['sale.order.line'].sudo().browse())
        propios = self.visar_upsell_line_ids.sudo()
        servicios = self._visar_upsell_lines().filtered(
            lambda l: l.product_id.visar_is_service and not l.visar_valuation_credit)
        if not servicios:
            return vacio  # solo contra servicios: una estación no se descuenta
        valoracion = self._visar_upsell_valuation_lines()
        producto = self._visar_upsell_credit_product()
        if not valoracion or not producto:
            return vacio
        # UNA vez por pedido: cuenta un descuento de otra visita y también el que se
        # capturó a mano antes de que esto existiera (línea negativa del producto de
        # descuento, como en S00138).
        pedidos = valoracion.order_id | self._visar_upsell_order()
        otros = pedidos.order_line.filtered(
            lambda l: l not in propios and l.product_uom_qty > 0 and (
                l.visar_valuation_credit
                or (l.product_id == producto and l.price_unit < 0)))
        if otros:
            return vacio
        if not self._visar_valuation_is_paid(valoracion):
            return vacio
        importe = min(sum(self._visar_line_amount(l) for l in valoracion),
                      sum(self._visar_line_amount(l) for l in servicios))
        return (importe, valoracion[:1]) if importe > 0 else vacio

    def _visar_upsell_sync_valuation_credit(self):
        """Deja la línea de descuento como toca según el carrito. Idempotente.

        Se recalcula en cada cambio del carrito (y antes de cobrar) para que el total
        que el técnico le enseña al cliente ya sea el que va a pagar.
        """
        self.ensure_one()
        order = self._visar_upsell_order()
        if not order:
            return
        credito = self.visar_upsell_line_ids.sudo().filtered('visar_valuation_credit')
        importe, valoracion = self._visar_upsell_valuation_credit()
        ctx = dict(self._VISAR_UPSELL_LINE_CTX)
        if importe <= 0:
            vivos = credito.filtered(lambda l: l.product_uom_qty > 0)
            if vivos and order.state == 'draft':
                vivos.unlink()
            elif vivos:
                vivos.with_context(**ctx).write({'product_uom_qty': 0.0})
            return
        vals = {
            'product_uom_qty': 1.0,
            'price_unit': -importe,
            'discount': 0.0,
            'tax_ids': [(6, 0, valoracion.tax_ids.ids)],
        }
        if credito:
            credito[:1].with_context(**ctx).write(vals)
            return
        empleado = self._visar_upsell_lines().filtered(
            lambda l: l.product_id.visar_is_service).visar_upsell_employee_id[:1]
        self.env['sale.order.line'].sudo().with_context(**ctx).create(dict(
            vals,
            order_id=order.id,
            product_id=self._visar_upsell_credit_product().id,
            name="Descuento por visita de valoración (%s)" % (
                valoracion.order_id.name or ''),
            task_id=self.id,
            visar_upsell_task_id=self.id,
            # Con el técnico, para que su comisión se mida sobre lo que el cliente
            # paga de más hoy y no sobre el precio de lista del servicio.
            visar_upsell_employee_id=empleado.id or False,
            visar_upsell_at=fields.Datetime.now(),
            visar_valuation_credit=True,
        ))

    # ------------------------------------------------------------------
    # El servicio vendido nace como su propia visita, ya en ejecución
    # ------------------------------------------------------------------
    def _visar_upsell_spawn_service_tasks(self, employee=None):
        """Suelta las líneas de servicio de la visita y deja que nazca su tarea.

        Se usa el generador de siempre (`_timesheet_service_generation`, con la
        consolidación de `visar_fsm`) y no un `create` a mano: el nombre, el proyecto,
        la hoja de trabajo y la regla de combo tienen que ser los mismos que si el
        servicio se hubiera agendado. Idempotente: una línea que ya tiene su propia
        tarea no se vuelve a soltar.
        """
        self.ensure_one()
        lineas = self._visar_upsell_lines().filtered(
            lambda l: l.task_id == self and not l.visar_valuation_credit
            and l.product_id.visar_is_service
            and l.product_id.service_tracking != 'no').sudo()
        if not lineas:
            return self.browse()
        ctx = dict(self._VISAR_UPSELL_LINE_CTX)
        lineas.with_context(**ctx).write({'task_id': False})
        lineas.with_context(**ctx)._timesheet_service_generation()
        huerfanas = lineas.filtered(lambda l: not l.task_id)
        if huerfanas:
            # El producto no tiene proyecto: no hay dónde nacer. Se devuelve a la
            # visita para no dejar una venta colgando de nada.
            huerfanas.with_context(**ctx).write({'task_id': self.id})
        nuevas = (lineas.mapped('task_id') - self).sudo()
        for tarea in nuevas:
            self._visar_upsell_prepare_service_task(tarea, employee)
        return nuevas

    def _visar_upsell_prepare_service_task(self, tarea, employee=None):
        """Deja el servicio nuevo listo para trabajarse ahora mismo.

        Mismos técnicos, fecha de hoy y directo a "En ejecución" en el paso de la
        hoja: los botones de "Voy en camino" y "Confirmar llegada" mandan avisos al
        cliente, y el técnico ya está en su casa.
        """
        self.ensure_one()
        ahora = fields.Datetime.now()
        duracion = timedelta(hours=1)
        if self.planned_date_begin and self.date_deadline \
                and self.date_deadline > self.planned_date_begin:
            duracion = self.date_deadline - self.planned_date_begin
        vals = {
            'visar_upsell_origin_task_id': self.id,
            # Odoo DESCARTA un `planned_date_begin` que llega sin su fecha de fin.
            'planned_date_begin': ahora,
            'date_deadline': ahora + duracion,
        }
        if self.visar_technician_ids:
            vals['visar_technician_ids'] = [(6, 0, self.visar_technician_ids.ids)]
        if self.user_ids:
            vals['user_ids'] = [(6, 0, self.user_ids.ids)]
        tarea.write(vals)
        tarea._visar_set_stage(2)  # En ejecución
        tarea.write({
            'visar_arrived_at': self.visar_arrived_at or ahora,
            'visar_service_start': ahora,
        })
        quien = employee.name if employee else "el técnico"
        tarea.message_post(body=Markup(
            "<p>Servicio vendido y hecho en la visita <b>%s</b> por <b>%s</b>. "
            "Arranca en ejecución: el técnico ya está en el domicilio, así que no se "
            "le mandaron al cliente los avisos de camino ni de llegada.</p>"
        ) % (self.name or '', quien), subtype_xmlid='mail.mt_note')
        self.message_post(body=Markup(
            "<p>Se vendió en sitio y se creó el servicio <b>%s</b>, con su propia hoja "
            "de trabajo.</p>") % (tarea.name or ''), subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # Liga de pago desde el número de Visar
    # ------------------------------------------------------------------
    def _visar_upsell_send_payment_link(self):
        """Manda la liga de pago por WhatsApp desde el número de Visar. Una vez.

        El botón con el que el técnico la manda desde su teléfono se queda como
        respaldo: sirve cuando el cliente no tiene la ventana de 24 h abierta y la
        plantilla todavía no está aprobada.
        """
        self.ensure_one()
        if self.visar_upsell_link_sent_at:
            return self.env['visar.wa.message'].browse()
        link = self._visar_upsell_payment_link()
        if not link:
            return self.env['visar.wa.message'].browse()
        invoice = self._visar_upsell_invoice()
        currency = self._visar_upsell_currency()
        importe = invoice.amount_residual if invoice else self.visar_upsell_amount_total
        monto = currency.format(importe) if currency else str(importe)
        text, params = self._visar_msg_upsell_payment(monto, link)
        queued = self._visar_notify_client(text, event='upsell_payment', params=params)
        if queued:
            self.sudo().visar_upsell_link_sent_at = fields.Datetime.now()
        return queued

    def _visar_msg_upsell_payment(self, monto, link):
        """Texto y parámetros del aviso `upsell_payment` ({{1}} monto, {{2}} liga)."""
        text = ("Hola, le saluda Visar Homes. Le compartimos la liga para pagar el "
                "servicio adicional de hoy (%s): %s" % (monto, link))
        return text, [monto, link]

    # ------------------------------------------------------------------
    # Pago de prueba
    # ------------------------------------------------------------------
    def _visar_upsell_provider_states(self):
        if self.env['ir.config_parameter'].sudo().get_param(PARAM_PAGO_PRUEBA) in (
                'True', 'true', '1'):
            return ('enabled', 'test')
        return super()._visar_upsell_provider_states()

    def _visar_upsell_is_test_payment(self):
        """¿El cobro en línea que se ofrece es de PRUEBA (no entra dinero)?"""
        self.ensure_one()
        providers = self._visar_upsell_providers()
        return bool(providers) and all(p.state == 'test' for p in providers)

    # ------------------------------------------------------------------
    # Enganches con el carrito existente
    # ------------------------------------------------------------------
    def _visar_upsell_add(self, employee, product_id, quantity=1):
        res = super()._visar_upsell_add(employee, product_id, quantity=quantity)
        if res:
            self._visar_upsell_sync_valuation_credit()
        return res

    def _visar_upsell_remove(self, line_id):
        # El descuento no se quita a mano: lo decide la regla, y quitarlo aquí solo
        # haría que el siguiente cambio del carrito lo volviera a poner.
        if self.visar_upsell_line_ids.filtered(
                lambda l: l.id == int(line_id) and l.visar_valuation_credit):
            return False
        res = super()._visar_upsell_remove(line_id)
        if res:
            self._visar_upsell_sync_valuation_credit()
        return res

    def _visar_upsell_confirm(self, employee):
        self.ensure_one()
        if self._visar_upsell_state() in ('vacio', 'borrador'):
            self._visar_upsell_sync_valuation_credit()
        order = super()._visar_upsell_confirm(employee)
        if order:
            self._visar_upsell_spawn_service_tasks(employee)
            self._visar_upsell_send_payment_link()
        return order
