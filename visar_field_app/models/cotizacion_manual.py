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
import logging

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM_RESPONSABLE = 'visar_field.cotizacion_responsable_id'
CAMPO_SERVICIOS_IDENTIFICADOS = 'x_servicios_identificados'
# El texto libre de "Especifica qué otro". NO dispara cotización: el enlace
# producto <-> servicio es por etiqueta, y una frase escrita a mano no es una
# etiqueta. Se lee solo para poder AVISAR de que se escribió algo que nadie va a
# cotizar (ver `_visar_quote_revisar`).
CAMPO_SERVICIOS_OTRO = 'x_servicios_identificados_otro'
WORKSHEET_LINK = 'x_project_task_id'

# Prefijo del resumen de los avisos de hueco. Es la clave de idempotencia: la
# hoja se guarda muchas veces (hay borrador, y se reabre), y sin esto cada
# guardado dejaría otra actividad idéntica hasta enterrar la bandeja.
AVISO_RESUMEN = "Revisar la hoja"



class ProductTemplate(models.Model):
    _inherit = 'product.template'

    visar_quote_trigger = fields.Char(
        string="Se cotiza cuando la hoja marca",
        help="Nombre del servicio en \"Servicios identificados\" de la hoja de trabajo "
             "(p. ej. Termitas) que pide una cotización manual de este producto. Al "
             "guardar la hoja con ese servicio marcado nace una cotización en borrador "
             "para que un administrador le ponga precio. Vacío = no se cotiza así.")


class ProductTemplateService(models.Model):
    _inherit = 'product.template'

    def _visar_counts_as_service(self):
        """Un tratamiento que cotiza oficina es un servicio para el cliente aunque no
        sea agendable por la web: recibe el descuento de la valoración, nace como su
        propia visita y sale en "Mis servicios" cuando el agente los lista."""
        self.ensure_one()
        return super()._visar_counts_as_service() or bool(self.visar_quote_trigger)


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
        enviado = self._visar_quote_notify_client()
        nota = (_("Cotizada para agendarse aparte. Total %s. Se le mandó al cliente por "
                  "WhatsApp con el botón \"Elegir fecha\": el agente le ofrece horarios "
                  "y la liga de pago.", self.currency_id.format(self.amount_total))
                if enviado else
                _("Cotizada para agendarse aparte. Total %s. NO se pudo avisar al "
                  "cliente por WhatsApp (sin teléfono): hay que llamarle.",
                  self.currency_id.format(self.amount_total)))
        self.message_post(body=nota)
        self._visar_quote_close_activity(nota)
        return True

    def _visar_quote_notify_client(self):
        """Encola el aviso `quote_ready` al cliente de la visita. Devuelve el aviso.

        Cuelga de la VISITA que pidió la cotización: ahí está el teléfono del
        cliente al que se le habló, y ahí queda la nota del envío. El monto va en el
        mensaje porque es lo primero que el cliente quiere saber, y porque la liga
        que le llega después tiene que decir la misma cifra.
        """
        self.ensure_one()
        task = self.visar_quote_origin_task_id.sudo()
        _display, e164 = task._visar_client_phone()
        servicio = ", ".join(
            self._visar_quote_service_lines().product_id.mapped('name')).lower() \
            or "su servicio"
        monto = self.currency_id.format(self.amount_total)
        con_descuento = bool(self.order_line.filtered('visar_valuation_credit'))
        texto = (
            "Hola, le saluda Visar Homes. La cotización de su servicio de *%s* ya está "
            "lista: *%s*%s.\n\nToque *Elegir fecha* para agendarlo; al confirmar le "
            "enviamos su liga de pago." % (
                servicio, monto,
                ", con el descuento de su visita de valoración ya aplicado"
                if con_descuento else ""))
        aviso = self.env['visar.wa.message'].sudo()._visar_wa_enqueue(
            'quote_ready', e164, texto, params=[servicio, monto],
            values={'task_id': task.id, 'quote_order_id': self.id}) if e164 else False
        task.message_post(body=Markup("📱 <b>[WhatsApp %s]</b><br/>%s") % (
            ("→ %s (en cola)" % _display) if aviso else "— sin número en el contacto",
            texto), subtype_xmlid='mail.mt_note')
        return aviso


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
    def _visar_quote_hoja(self):
        """La hoja de trabajo más reciente de esta visita, o `None`.

        Se saca a un método porque ahora la leen tres sitios (los nombres
        marcados, el texto libre y el aviso de huecos) y tres búsquedas con la
        misma intención divergen en cuanto alguien toque una.

        Devuelve `None` y no un recordset vacío a propósito: cuando no hay
        plantilla no se sabe de QUÉ modelo sería el recordset.
        """
        self.ensure_one()
        template = self.sudo().worksheet_template_id
        model = template.model_id.model if template else False
        if not model or model not in self.env:
            return None
        Hoja = self.env[model].sudo()
        if CAMPO_SERVICIOS_IDENTIFICADOS not in Hoja._fields:
            return None
        return Hoja.search([(WORKSHEET_LINK, '=', self.id)], limit=1,
                           order='create_date desc')

    def _visar_quote_identified_display(self):
        """Los nombres marcados TAL CUAL se escribieron. Para los avisos.

        El aviso de "esto no coincide con ningún producto" tiene que enseñar el
        nombre con sus mayúsculas y sus acentos: es lo que alguien va a comparar
        a ojo contra el campo del producto, y en minúsculas parece otro texto.
        """
        hoja = self._visar_quote_hoja()
        if not hoja:
            return []
        return [n for n in hoja[CAMPO_SERVICIOS_IDENTIFICADOS].mapped('display_name')
                if (n or '').strip()]

    def _visar_quote_identified_names(self):
        """Nombres marcados en "Servicios identificados" de la hoja (minúsculas)."""
        self.ensure_one()
        return {(n or '').strip().lower()
                for n in self._visar_quote_identified_display()}

    def _visar_quote_identified_otro(self):
        """Lo que el técnico escribió a mano en "Especifica qué otro"."""
        hoja = self._visar_quote_hoja()
        if not hoja or CAMPO_SERVICIOS_OTRO not in hoja._fields:
            return ''
        return (hoja[CAMPO_SERVICIOS_OTRO] or '').strip()

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
        marcados = self._visar_quote_identified_names()
        productos = self._visar_quote_products()
        if not order:
            # La hoja pidió algo y el circuito no puede ni empezar. ANTES esto
            # era un `return` mudo: el técnico marcaba el servicio, la app decía
            # "guardado", y nadie se enteraba de que no había cotización.
            self._visar_quote_revisar(productos, sin_pedido=True)
            return self.env['sale.order'].sudo().browse()
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
        self._visar_quote_revisar(productos)
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
        responsable = self._visar_quote_responsable(origen)
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

    def _visar_quote_responsable(self, origen=None):
        """A quién se le encarga cotizar. Nunca vacío.

        Si el parámetro no está puesto cae al comercial del pedido y, en último
        término, al administrador: una actividad sin dueño no la ve nadie, y aquí
        el punto entero es que alguien se entere.
        """
        param = self.env['ir.config_parameter'].sudo().get_param(PARAM_RESPONSABLE)
        responsable = (self.env['res.users'].sudo().browse(int(param)).exists()
                       if param and str(param).isdigit() else self.env['res.users'])
        return (responsable
                or (origen.user_id if origen else self.env['res.users'])
                or self.env.ref('base.user_admin'))

    # ------------------------------------------------------------------
    # Cuando la hoja pide algo y NO nace cotización
    # ------------------------------------------------------------------
    #
    # El circuito tenía tres salidas mudas, y las tres acaban igual: el técnico
    # marca el servicio, la app dice "guardado", y nadie descubre que no hay
    # cotización hasta que el cliente pregunta —o nunca—.
    #
    #   ① La visita no tiene pedido detrás. Pasó en producción con la visita 678:
    #      al cambiarle el cliente, Odoo limpió `sale_line_id` (su dominio lo
    #      restringe a los pedidos de ESE cliente) y el `if not order: return` se
    #      tragó todo sin una línea de log.
    #   ② El nombre marcado no coincide con ningún producto. El enlace es por
    #      nombre exacto (strip + lower), así que "Alacranes (prueba)" contra un
    #      producto que dice "Alacranes" no casa.
    #   ③ El técnico escribió el servicio a mano en "Especifica qué otro". Solo se
    #      leen las etiquetas; ese texto únicamente sale en el PDF firmado.
    #
    # Avisa, no bloquea: igual que el aviso de ruta. Una hoja mal configurada no
    # puede impedirle al técnico guardar su trabajo en la puerta del cliente.

    def _visar_quote_revisar(self, productos, sin_pedido=False):
        """Deja una actividad por cada hueco del circuito. NUNCA levanta.

        Va colgado del guardado de la hoja, que ocurre con el técnico de pie en
        casa del cliente: si esto fallara, lo que no puede pasar es que se caiga
        el guardado. Un aviso perdido vale muchísimo menos que el parte de un
        servicio.
        """
        self.ensure_one()
        try:
            self._visar_quote_revisar_ahora(productos, sin_pedido)
        except Exception:  # noqa: BLE001 - un aviso no tumba un guardado
            _logger.exception(
                "No se pudo revisar los huecos de cotización de la tarea %s", self.id)

    def _visar_quote_revisar_ahora(self, productos, sin_pedido=False):
        """El trabajo de `_visar_quote_revisar`, sin la red de seguridad."""
        self.ensure_one()
        marcados = self._visar_quote_identified_display()
        otro = self._visar_quote_identified_otro()

        if sin_pedido:
            # Sin pedido no se puede cotizar NADA, así que basta un aviso para
            # toda la hoja: desglosar por servicio repetiría la misma causa.
            if marcados or otro:
                pedidos = ", ".join(marcados + ([otro] if otro else []))
                self._visar_quote_aviso(
                    _("sin pedido"),
                    _("La hoja pidió cotización de %(pedidos)s y esta visita no "
                      "tiene ningún pedido de venta detrás, así que no se pudo "
                      "crear.\n\n"
                      "La causa más común es que se le haya cambiado el cliente a "
                      "la visita: al hacerlo Odoo borra su línea de pedido, porque "
                      "solo admite pedidos de ese cliente. Revisa el pedido de la "
                      "visita; en cuanto lo tenga, basta con volver a guardar la "
                      "hoja para que la cotización nazca.",
                      pedidos=pedidos))
            return

        for nombre in marcados:
            if (nombre or '').strip().lower() in productos:
                continue
            configurados = sorted(
                t.visar_quote_trigger for t in productos.values()
                if t.visar_quote_trigger)
            self._visar_quote_aviso(
                _("«%s» sin producto", nombre),
                _("El técnico marcó «%(nombre)s» en Servicios identificados y "
                  "ningún producto se cotiza con ese nombre, así que no nació "
                  "cotización.\n\n"
                  "El enlace es por nombre EXACTO (no distingue mayúsculas ni "
                  "espacios de los extremos, pero nada más). Hoy están "
                  "configurados: %(configurados)s.\n\n"
                  "Arréglalo en el producto, campo «Se cotiza cuando la hoja "
                  "marca», o renombrando la etiqueta. Después vuelve a guardar la "
                  "hoja y la cotización nace.",
                  nombre=nombre,
                  configurados=", ".join(configurados) or _("ninguno")))

        if otro:
            self._visar_quote_aviso(
                _("servicio escrito a mano"),
                _("El técnico escribió «%(otro)s» en «Especifica qué otro». Ese "
                  "texto NO pide cotización: el circuito solo lee las etiquetas "
                  "marcadas, y ese campo únicamente sale en el reporte firmado.\n\n"
                  "Si es un servicio que Visar cotiza, hace falta su etiqueta en "
                  "«Servicios identificados» y un producto con ese nombre en «Se "
                  "cotiza cuando la hoja marca». Si no, no hay nada que hacer: "
                  "este aviso es para que no se quede en el PDF y se olvide.",
                  otro=otro))

    def _visar_quote_aviso(self, motivo, nota):
        """Una actividad para quien cotiza, UNA sola vez por motivo.

        **La idempotencia es la mitad que importa.** La hoja se guarda muchas
        veces —hay guardado de borrador, y se puede reabrir—, así que sin esto
        cada guardado dejaría otra actividad idéntica hasta enterrar la bandeja
        de quien cotiza, que es la forma más rápida de que un aviso útil deje de
        leerse. La clave es el resumen.
        """
        self.ensure_one()
        resumen = "%s: %s" % (AVISO_RESUMEN, motivo)
        modelo = self.env['ir.model']._get_id('project.task')
        existe = self.env['mail.activity'].sudo().search_count([
            ('res_model_id', '=', modelo),
            ('res_id', '=', self.id),
            ('summary', '=', resumen),
        ])
        if existe:
            return False
        responsable = self._visar_quote_responsable(self.sale_order_id.sudo())
        cuerpo = self._visar_quote_parrafos(nota)
        self.sudo().activity_schedule(
            'mail.mail_activity_data_todo',
            summary=resumen,
            note=cuerpo,
            user_id=responsable.id)
        self.sudo().message_post(
            body=Markup("<p><b>%s</b></p>") % resumen + cuerpo,
            subtype_xmlid='mail.mt_note')
        return True

    @staticmethod
    def _visar_quote_parrafos(texto):
        """Texto plano -> párrafos HTML, con el contenido ESCAPADO.

        No es cosmético: el aviso de "servicio escrito a mano" repite un texto que
        teclea el técnico en su teléfono. Metido crudo en el `note` de una
        actividad —que Odoo trata como HTML— sería inyección de etiquetas en el
        backend. El `%` de `Markup` escapa el argumento; la estructura se añade
        por fuera, que es la única forma de tener las dos cosas.
        """
        return Markup("").join(
            Markup("<p>%s</p>") % parrafo.strip()
            for parrafo in (texto or '').split('\n\n') if parrafo.strip())

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
