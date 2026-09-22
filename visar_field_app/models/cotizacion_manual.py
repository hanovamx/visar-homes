# -*- coding: utf-8 -*-
"""Tratamientos que se cotizan a mano: termitas, chinches (22-sep-2026).

No tienen precio de lista —cada casa es distinta— y el precio no lo decide el
técnico sino un administrador en Odoo. Por eso no se venden desde el catálogo de
campo (su casilla "Vendible en campo" está apagada). Lo que pasa en cambio:

1. **La hoja de trabajo es la que pide la cotización.** Si el técnico marca el
   servicio en "Servicios identificados" (hoy, en la hoja de la visita de
   valoración), al guardar la hoja nace una COTIZACIÓN aparte —un `sale.order` en
   borrador—, ligada a la visita y a su pedido, con una actividad para quien
   cotiza. Qué servicio dispara qué producto se configura en el producto ("Se cotiza
   cuando la hoja marca").
2. **Cotización aparte y no una línea en el pedido de la visita**: el precio no
   existe todavía y el cliente puede decir que no. El pedido de la valoración ya está
   pagado y facturado; una línea en $0 esperando precio lo ensuciaría, y una
   cotización rechazada se queda como cotización sin tocar nada.
3. **Dos caminos una vez con precio**, porque no se sabe si quien cotiza estará en
   línea mientras el técnico sigue en la casa:
   * *Hacer en esta visita* — el técnico sigue en ejecución: el tratamiento entra
     como una ronda más de adicionales de la visita (mismo pedido, su factura, su
     liga), igual que un servicio vendido en sitio. La cotización se cancela: su
     contenido ya vive en el pedido de la visita.
   * *Agendar después* — lo normal: la cotización queda lista para que el agente le
     ofrezca al cliente fecha y liga de pago (paso 3, pendiente de la plantilla de
     Meta). Al confirmarse nace su propio servicio, con su propia hoja.
4. **El descuento de la valoración ($500) aplica también si el servicio se hace otro
   día** (Visar, 22-sep-2026), con las mismas reglas: una vez por pedido —contando
   la visita, sus adicionales y sus cotizaciones—, nunca por más que el servicio, y
   solo con la valoración pagada.
"""
from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

PARAM_RESPONSABLE = 'visar_field.cotizacion_responsable_id'
CAMPO_SERVICIOS_IDENTIFICADOS = 'x_servicios_identificados'
WORKSHEET_LINK = 'x_project_task_id'


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    visar_quote_trigger = fields.Char(
        string="Se cotiza cuando la hoja marca",
        help="Nombre del servicio en \"Servicios identificados\" de la hoja de trabajo "
             "(p. ej. Termitas) que pide una cotización manual de este producto. Al "
             "guardar la hoja con ese servicio marcado nace una cotización en borrador "
             "para que un administrador le ponga precio. Vacío = no se cotiza así.")


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _visar_counts_as_service(self):
        """¿Recibe el descuento de la valoración y nace como su propia visita? Los
        servicios de catálogo y los tratamientos que se cotizan a mano."""
        self.ensure_one()
        return bool(self.visar_is_service or self.visar_quote_trigger)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_quote_user_id = fields.Many2one(
        'res.users', string="Responsable de cotizar",
        config_parameter=PARAM_RESPONSABLE,
        help="Quién recibe la actividad \"Cotizar\" cuando una hoja de trabajo pide un "
             "tratamiento con cotización manual. Vacío = el vendedor del pedido de la "
             "visita.")


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    visar_quote_origin_task_id = fields.Many2one(
        'project.task', string="Pedida en la visita", readonly=True, copy=False,
        index='btree_not_null',
        help="Visita cuya hoja de trabajo pidió esta cotización.")
    visar_quote_origin_order_id = fields.Many2one(
        'sale.order', string="Originada en el pedido", readonly=True, copy=False,
        index='btree_not_null',
        help="Pedido de la visita que originó la cotización (la valoración). Su "
             "descuento se cuenta una sola vez entre los dos.")
    visar_quote_trigger = fields.Char(
        string="Servicio identificado", readonly=True, copy=False)
    visar_quote_path = fields.Selection([
        ('en_visita', "Se hace en la misma visita"),
        ('agendar', "Se agenda aparte"),
    ], string="Camino", readonly=True, copy=False,
        help="Lo que decidió quien cotizó. Vacío = todavía sin precio.")

    def write(self, vals):
        res = super().write(vals)
        # El descuento sigue al precio: quien cotiza cambia la línea y el total ya
        # sale con los $500 descontados, sin tener que acordarse de nada.
        if 'order_line' in vals and not self.env.context.get('visar_quote_no_sync'):
            for order in self.filtered(
                    lambda o: o.visar_quote_origin_task_id and o.state in ('draft', 'sent')):
                order._visar_quote_sync_credit()
        return res

    # ------------------------------------------------------------------
    def _visar_quote_service_lines(self):
        self.ensure_one()
        return self.order_line.filtered(
            lambda l: not l.display_type and not l.visar_valuation_credit
            and l.product_uom_qty > 0)

    def _visar_quote_sync_credit(self):
        """Deja la línea de descuento de la valoración como toca. Idempotente."""
        self.ensure_one()
        task = self.visar_quote_origin_task_id.sudo()
        credito = self.order_line.filtered('visar_valuation_credit')
        importe, valoracion = task._visar_valuation_credit_for(
            self._visar_quote_service_lines(), propios=credito)
        ctx = dict(visar_quote_no_sync=True)
        if importe <= 0:
            if credito:
                credito.with_context(**ctx).unlink()
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
        self.env['sale.order.line'].sudo().with_context(**ctx).create(dict(
            vals,
            order_id=self.id,
            product_id=task._visar_upsell_credit_product().id,
            name="Descuento por visita de valoración (%s)" % (
                valoracion.order_id.name or ''),
            visar_valuation_credit=True,
        ))

    def _visar_quote_check_priced(self):
        self.ensure_one()
        if not self.visar_quote_origin_task_id:
            raise UserError(_("Esta cotización no viene de una visita."))
        if self.state not in ('draft', 'sent') or self.visar_quote_path:
            raise UserError(_("Esta cotización ya se resolvió."))
        lineas = self._visar_quote_service_lines()
        if not lineas or any(l.price_unit <= 0 for l in lineas):
            raise UserError(_(
                "Ponle precio al tratamiento antes de continuar: la cotización todavía "
                "tiene líneas en $0."))
        return lineas

    def _visar_quote_close_activity(self, nota):
        actividades = self.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref('mail.mail_activity_data_todo'))
        if actividades:
            actividades.action_feedback(feedback=nota)

    def action_visar_quote_in_visit(self):
        """El técnico sigue en la casa: se hace hoy, como una ronda más de adicionales."""
        self.ensure_one()
        lineas = self._visar_quote_check_priced()
        task = self.visar_quote_origin_task_id.sudo()
        if not task._visar_quote_visit_running():
            raise UserError(_(
                "La visita %s ya no está en ejecución: el técnico ya no está en la casa. "
                "Usa \"Agendar después\".", task.name))
        if not task._visar_upsell_can_start_round():
            raise UserError(_(
                "La visita %s tiene un cobro de adicionales pendiente. Cuando el cliente "
                "lo pague se puede agregar el tratamiento.", task.name))
        datos = [(l.product_id, l.product_uom_qty, l.price_unit, l.discount, l.name)
                 for l in lineas]
        # Se cancela ANTES de pasar las líneas: así su descuento deja de contar y el
        # de la visita puede nacer en el pedido de la visita (una vez por pedido).
        self.with_context(visar_quote_no_sync=True).write({'visar_quote_path': 'en_visita'})
        self._action_cancel()
        if not task._visar_upsell_add_quoted(datos):
            raise UserError(_("No se pudo agregar el tratamiento a la visita %s.", task.name))
        nota = _("Se hace en la misma visita (%s): pasó al pedido de la visita como "
                 "adicional; el técnico genera el cobro desde la app.", task.name)
        self.message_post(body=nota)
        self._visar_quote_close_activity(nota)
        task.message_post(body=Markup(
            "<p>Oficina cotizó <b>%s</b> y se hace en esta visita: ya está en los "
            "adicionales de la app para generar el cobro.</p>") % (
                ", ".join(p.display_name for p, *_r in datos)),
            subtype_xmlid='mail.mt_note')
        return True

    def action_visar_quote_schedule_later(self):
        """Lo normal: se agenda otro día. Queda lista para mandarse al cliente."""
        self.ensure_one()
        self._visar_quote_check_priced()
        self._visar_quote_sync_credit()
        self.with_context(visar_quote_no_sync=True).write({'visar_quote_path': 'agendar'})
        if self.state == 'draft':
            self.action_quotation_sent()
        nota = _("Cotizada para agendarse aparte. Total %s. Siguiente paso: el agente "
                 "le ofrece al cliente fecha y liga de pago.",
                 self.currency_id.format(self.amount_total))
        self.message_post(body=nota)
        self._visar_quote_close_activity(nota)
        return True


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_quote_order_ids = fields.One2many(
        'sale.order', 'visar_quote_origin_task_id',
        string="Cotizaciones pedidas", readonly=True)
    visar_quote_order_count = fields.Integer(compute='_compute_visar_quote_order_count')

    def _compute_visar_quote_order_count(self):
        for task in self:
            task.visar_quote_order_count = len(task.visar_quote_order_ids)

    def action_visar_view_quotes(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Cotizaciones pedidas"),
            'res_model': 'sale.order',
            'view_mode': 'list,form',
            'domain': [('visar_quote_origin_task_id', '=', self.id)],
            'context': {'create': False},
        }

    # ------------------------------------------------------------------
    def _visar_quote_identified_names(self):
        """Nombres marcados en "Servicios identificados" de la hoja (minúsculas)."""
        self.ensure_one()
        template = self.sudo().worksheet_template_id
        model = template.model_id.model if template else False
        if not model or model not in self.env:
            return set()
        Hoja = self.env[model].sudo()
        if CAMPO_SERVICIOS_IDENTIFICADOS not in Hoja._fields:
            return set()
        hoja = Hoja.search([(WORKSHEET_LINK, '=', self.id)], limit=1,
                           order='create_date desc')
        return {(n or '').strip().lower()
                for n in hoja[CAMPO_SERVICIOS_IDENTIFICADOS].mapped('display_name')}

    def _visar_quote_products(self):
        """{nombre del servicio (minúsculas): product.template} de los que se cotizan."""
        Template = self.env['product.template'].sudo()
        return {(t.visar_quote_trigger or '').strip().lower(): t
                for t in Template.search([('visar_quote_trigger', '!=', False)])}

    def _visar_quote_requests_sync(self, employee=None):
        """Crea (o retira) las cotizaciones que pide la hoja. Idempotente.

        Una por visita y servicio. Si el técnico desmarca el servicio, la cotización
        se cancela solo mientras nadie le haya puesto precio: con precio ya es trabajo
        de oficina y no se toca.
        """
        self.ensure_one()
        order = self.sale_order_id.sudo()
        if not order:
            return self.env['sale.order'].sudo().browse()
        marcados = self._visar_quote_identified_names()
        productos = self._visar_quote_products()
        existentes = self.sudo().visar_quote_order_ids
        nuevas = self.env['sale.order'].sudo().browse()
        for clave, template in productos.items():
            ya = existentes.filtered(lambda o: (o.visar_quote_trigger or '').lower() == clave)
            if clave in marcados and not ya:
                nuevas |= self._visar_quote_create(template, employee)
            elif clave not in marcados:
                for cot in ya.filtered(lambda o: o.state == 'draft' and not o.visar_quote_path):
                    if all(l.price_unit <= 0 for l in cot._visar_quote_service_lines()):
                        cot._action_cancel()
                        cot.message_post(body=_(
                            "Cancelada: el técnico desmarcó \"%s\" en la hoja antes de "
                            "que se cotizara.", template.visar_quote_trigger))
        return nuevas

    def _visar_quote_create(self, template, employee=None):
        self.ensure_one()
        origen = self.sale_order_id.sudo()
        producto = template.product_variant_id
        cot = self.env['sale.order'].sudo().with_context(visar_quote_no_sync=True).create({
            'partner_id': origen.partner_id.id,
            'partner_invoice_id': origen.partner_invoice_id.id,
            'partner_shipping_id': origen.partner_shipping_id.id,
            'pricelist_id': origen.pricelist_id.id,
            'company_id': origen.company_id.id,
            'user_id': origen.user_id.id,
            'origin': "%s — %s" % (origen.name, self.name or ''),
            'visar_quote_origin_task_id': self.id,
            'visar_quote_origin_order_id': origen.id,
            'visar_quote_trigger': template.visar_quote_trigger,
            'order_line': [(0, 0, {
                'product_id': producto.id,
                'product_uom_qty': 1.0,
                'price_unit': 0.0,
            })],
        })
        quien = employee.name if employee else _("el técnico")
        cot.message_post(body=Markup(
            "<p>Pedida desde la hoja de trabajo de <b>%s</b>: %s marcó <b>%s</b> en "
            "Servicios identificados. Ponle precio al tratamiento y elige si se hace "
            "en esa misma visita o se agenda aparte.</p>") % (
                self.name or '', quien, template.visar_quote_trigger))
        param = self.env['ir.config_parameter'].sudo().get_param(PARAM_RESPONSABLE)
        responsable = (self.env['res.users'].sudo().browse(int(param)).exists()
                       if param and str(param).isdigit() else self.env['res.users'])
        responsable = responsable or origen.user_id or self.env.ref('base.user_admin')
        cot.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_("Cotizar: %s (%s)", template.visar_quote_trigger, origen.name),
            note=_("Visita %s. Poner precio y elegir: hacer en esta visita o agendar "
                   "después.", self.name or ''),
            user_id=responsable.id)
        self.message_post(body=Markup(
            "<p>La hoja pidió cotización de <b>%s</b>: %s.</p>") % (
                template.name, cot.name), subtype_xmlid='mail.mt_note')
        return cot

    def _visar_quote_visit_running(self):
        """¿El técnico sigue en la casa? En ejecución y sin cerrar."""
        self.ensure_one()
        return bool(self.stage_id == self._visar_fsm_stage(2)
                    and self.visar_service_start and not self.visar_field_closed_at)

    def _visar_upsell_add_quoted(self, datos):
        """Agrega al carrito de la visita un tratamiento ya cotizado por oficina.

        `datos` = [(product.product, cantidad, precio, descuento, descripción)]. No
        pasa por el catálogo de campo: estos productos no se venden desde la app, el
        precio lo puso oficina.
        """
        self.ensure_one()
        order = self._visar_upsell_order(create=True)
        if not order:
            return False
        ahora = fields.Datetime.now()
        for producto, cantidad, precio, descuento, nombre in datos:
            self.env['sale.order.line'].sudo().with_context(
                **self._VISAR_UPSELL_LINE_CTX).create({
                    'order_id': order.id,
                    'product_id': producto.id,
                    'name': nombre,
                    'product_uom_qty': cantidad,
                    'price_unit': precio,
                    'discount': descuento,
                    'task_id': self.id,
                    'visar_upsell_task_id': self.id,
                    'visar_upsell_at': ahora,
                })
        self._visar_upsell_sync_valuation_credit()
        return True

    # ------------------------------------------------------------------
    # Descuento de la valoración compartido entre la visita y sus cotizaciones
    # ------------------------------------------------------------------
    def _visar_valuation_credit_orders(self, valoracion):
        """Pedidos donde puede vivir el descuento de esta valoración: el suyo, el de
        adicionales de la visita y las cotizaciones vivas que originó."""
        self.ensure_one()
        pedido = valoracion.order_id
        cotizaciones = self.env['sale.order'].sudo().search([
            ('visar_quote_origin_order_id', 'in', pedido.ids), ('state', '!=', 'cancel')])
        return pedido | self._visar_upsell_order() | cotizaciones

    def _visar_valuation_credit_for(self, servicios, propios):
        """(importe, línea de valoración) del descuento que les toca a `servicios`, o
        (0, vacío). `propios` = líneas de descuento que ya son de ellos (no cuentan
        como "ya usado")."""
        self.ensure_one()
        vacio = (0.0, self.env['sale.order.line'].sudo().browse())
        if not servicios:
            return vacio
        valoracion = self._visar_upsell_valuation_lines()
        producto = self._visar_upsell_credit_product()
        if not valoracion or not producto:
            return vacio
        otros = self._visar_valuation_credit_orders(valoracion).order_line.filtered(
            lambda l: l not in propios and l.product_uom_qty > 0 and (
                l.visar_valuation_credit
                or (l.product_id == producto and l.price_unit < 0)))
        if otros or not self._visar_valuation_is_paid(valoracion):
            return vacio
        importe = min(sum(self._visar_line_amount(l) for l in valoracion),
                      sum(self._visar_line_amount(l) for l in servicios))
        return (importe, valoracion[:1]) if importe > 0 else vacio
