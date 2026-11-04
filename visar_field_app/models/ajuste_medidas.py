# -*- coding: utf-8 -*-
"""Ajuste de medidas en campo: el cliente pagó un tamaño y la casa es otro (3-nov-2026).

## El caso

El cliente agenda una fumigación para "1 – 250 m²", la paga, y el técnico llega y la
casa tiene 320. Hasta ahora eso se arreglaba por teléfono con oficina, o no se
arreglaba. Con el botón «Las medidas no coinciden» el técnico elige el tramo real en
la app y el cliente paga **solo la diferencia**, ahí mismo.

## Cómo se cobra, y por qué así

Igual que el descuento de la visita de valoración (`upsell_servicio.py`): el precio
nuevo entero, menos lo ya pagado. En el pedido quedan dos líneas nuevas, marcadas con
`visar_ajuste_medidas`:

* el servicio con la variante CORRECTA, al precio del mismo motor que el web y el
  agente (`appointment.type._visar_build_sale_lines`, con la zona del pedido y el
  combo incluido);
* una línea negativa «Ya pagado por medidas anteriores» por el importe de las líneas
  que el cliente ya pagó.

La línea original no se toca: ya está facturada y cobrada. Las dos nuevas entran a la
ronda de cobro de los adicionales (factura aparte, liga o efectivo) y cuelgan de esta
misma visita, así que no nace otro servicio.

## Lo que decidió Visar (3-nov-2026)

* **Casa más chica**: no se devuelve nada; oficina recibe una actividad.
* **El cliente no acepta pagar**: se hace solo lo pagado. El servicio queda marcado
  como incompleto por medidas y oficina recibe una actividad.
* **El pago no bloquea la hoja.** Si el servicio se cierra con la diferencia sin
  pagar, oficina recibe una actividad.
* **Comisión**: la diferencia cuenta para la del técnico solo si el ajuste está
  encendido en Ajustes → Visar → Venta en campo (lo aplica `visar_commission`).
* **Pólizas fuera**: cambiar el tamaño de una póliza cambia todos sus cobros futuros.
* **Evidencia obligatoria**: al menos una foto y el motivo por escrito.
"""
import base64

from markupsafe import Markup

from odoo import _, fields, models
from odoo.tools import float_compare, float_round

PARAM_AJUSTE_COMISION = 'visar_field.ajuste_medidas_comisiona'
FOTO_KEY = 'visar_ajuste_medidas'

ESTADOS_AJUSTE = [
    ('mayor', "Más grande: se cobra la diferencia"),
    ('menor', "Más chica: sin devolución"),
    ('igual', "Mismo tramo"),
    ('valoracion', "Fuera de tabulador: a cotización"),
    ('rechazado', "Incompleto: el cliente no pagó la diferencia"),
]

# Errores que la app traduce para el técnico. Un solo catálogo para el modelo y el
# controlador.
ERRORES_AJUSTE = {
    'no_disponible': "Este servicio no admite ajuste de medidas.",
    'sin_cambio': "Elija al menos una medida distinta de la que se pagó.",
    'sin_motivo': "Escriba por qué no coinciden las medidas.",
    'sin_foto': "Tome al menos una foto como evidencia.",
    'sin_tramo': "Esa medida no está en el tabulador.",
    'sin_precio': "No se pudo calcular el precio con esas medidas.",
    'sin_credito': "Falta configurar el producto de descuento en Ajustes → Visar → "
                   "Venta en campo. Avise a oficina.",
    'cobro_pendiente': "Hay un cobro de adicionales pendiente. Cóbrelo antes de "
                       "ajustar las medidas.",
    'sin_pedido': "No se encontró el pedido del servicio.",
    'ya_pagado': "La diferencia ya está pagada: ya no se puede marcar como rechazada.",
    'mixto': "El cobro de la diferencia va junto con otros adicionales. Pida a "
             "oficina que lo cancele.",
}


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    visar_ajuste_medidas = fields.Boolean(
        string="Ajuste de medidas", readonly=True, copy=False,
        help="Línea del ajuste de medidas en campo: el servicio con el tamaño real, o "
             "la línea negativa con lo que el cliente ya había pagado.")


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_ajuste_medidas_comisiona = fields.Boolean(
        string="La diferencia por medidas cuenta para la comisión",
        config_parameter=PARAM_AJUSTE_COMISION,
        help="Si está encendido, la diferencia que el técnico cobra al corregir las "
             "medidas cuenta como venta suya en campo, igual que un adicional.")


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_ajuste_estado = fields.Selection(
        ESTADOS_AJUSTE, string="Ajuste de medidas", readonly=True, copy=False,
        tracking=True)
    visar_ajuste_resumen = fields.Char(
        string="Medidas: antes → ahora", readonly=True, copy=False)
    visar_ajuste_motivo = fields.Text(
        string="Motivo del ajuste", readonly=True, copy=False)
    visar_ajuste_diferencia = fields.Float(
        string="Diferencia por medidas", readonly=True, copy=False,
        help="Precio con las medidas reales menos lo ya pagado, IVA incluido. "
             "Negativa si la casa resultó más chica.")
    visar_ajuste_at = fields.Datetime(string="Ajustado el", readonly=True, copy=False)
    visar_ajuste_by_id = fields.Many2one(
        'hr.employee', string="Ajustado por", readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Qué se pagó
    # ------------------------------------------------------------------
    def _visar_ajuste_lineas_pagadas(self):
        """Líneas de servicio por m² que este servicio atiende y que el cliente compró
        (no las vendidas en campo, no la valoración)."""
        self.ensure_one()
        lineas = (self.sudo().visar_sale_line_ids or self.sudo().sale_line_id)
        return lineas.filtered(
            lambda l: not l.display_type and l.product_uom_qty > 0
            and not l.visar_upsell_task_id
            and l.product_id.product_tmpl_id.visar_is_service
            and not l.product_id.product_tmpl_id.visar_is_valuation
            and l.product_id.product_tmpl_id.visar_tier_ids)

    def _visar_ajuste_disponible(self):
        """¿Se le ofrece el botón? Servicio con tramos pagados, sin póliza y sin un
        ajuste ya hecho."""
        self.ensure_one()
        if self.visar_ajuste_estado or self.visar_subscription_order_id:
            return False
        orden = self.sale_order_id.sudo()
        if orden and 'plan_id' in orden._fields and orden.plan_id:
            return False
        return bool(self._visar_ajuste_dimensiones())

    def _visar_ajuste_dimensiones(self, lineas=None):
        """[(dimensión, tramos ofrecibles, tramo pagado o vacío)] del servicio pagado.

        El tramo pagado se lee de la VARIANTE de la línea: cada eje de tamaño es un
        atributo (`_visar_axis_attribute`), y el tramo pagado es el que comparte su
        valor. Si la línea no lo dice (variante "0" = esa parte no se contrató), la
        dimensión sale con el tramo vacío y la app la ofrece como «No contratado».
        """
        self.ensure_one()
        pagadas = (lineas if lineas is not None else self._visar_ajuste_lineas_pagadas())
        plantillas = pagadas.mapped('product_id.product_tmpl_id')
        if not plantillas:
            return []
        Template = self.env['product.template'].sudo()
        resultado = []
        for dimension in self.env['visar.service.dimension'].sudo().search(
                [('active', '=', True)]):
            plantilla = Template._visar_get_service_template_for_dimension(dimension)
            if plantilla not in plantillas:
                continue
            tramos = plantilla._visar_tiers_for_dimension(dimension)
            if not tramos:
                continue
            pagado = self.env['visar.service.tier']
            eje = self._visar_ajuste_eje(plantilla, dimension, tramos)
            linea = pagadas.filtered(lambda l: l.product_id.product_tmpl_id == plantilla)[:1]
            if eje and linea:
                valor = linea.product_id.product_template_attribute_value_ids.filtered(
                    lambda v: v.attribute_id == eje)[:1].name
                for tramo in tramos:
                    ref = tramo.product_id.product_template_attribute_value_ids.filtered(
                        lambda v: v.attribute_id == eje)[:1].name
                    if valor and ref == valor:
                        pagado = tramo
                        break
            resultado.append((dimension, tramos, pagado))
        return resultado

    @staticmethod
    def _visar_ajuste_eje(plantilla, dimension, tramos):
        """Atributo de tamaño de esta dimensión en el producto.

        Con interior y exterior en el mismo producto (fumigación) lo dice el alcance
        de los tramos (`_visar_axis_attribute`). Un producto de un solo eje (corte de
        pasto, 4-nov-2026) tiene sus tramos con alcance «Todas», y ahí el eje es el
        único atributo que cambia entre las variantes de sus tramos; la zona no
        cambia, porque todos los tramos apuntan a la misma zona de referencia.
        """
        if dimension.measure_type in ('interior', 'exterior'):
            eje = plantilla._visar_axis_attribute(dimension.measure_type)
            if eje:
                return eje
        variantes = tramos.filtered(
            lambda t: not t.is_valuation and t.product_id.product_tmpl_id == plantilla
        ).mapped('product_id')
        por_atributo = {}
        for variante in variantes:
            for valor in variante.product_template_attribute_value_ids:
                por_atributo.setdefault(valor.attribute_id, set()).add(valor.id)
        cambian = [a for a, valores in por_atributo.items() if len(valores) > 1]
        return cambian[0] if len(cambian) == 1 else False

    def _visar_ajuste_pagado(self):
        """Lo que el cliente ya pagó por esas líneas, en la base de `price_unit`."""
        self.ensure_one()
        return sum(self._visar_line_amount(l) for l in self._visar_ajuste_lineas_pagadas())

    # ------------------------------------------------------------------
    # Cotizar con las medidas reales
    # ------------------------------------------------------------------
    def _visar_ajuste_cotizar(self, tramos_por_dimension):
        """Precio con los tramos elegidos. Devuelve un dict, o {'error': clave}.

        `tramos_por_dimension` = {dimension_id: tier_id o 0}; 0 = no contratado.
        Se cotizan TODAS las dimensiones del servicio juntas, para que el combo
        (fumigación + jardín) cuente igual que al agendar.
        """
        self.ensure_one()
        zona = self._visar_upsell_zone()
        dimensiones = self._visar_ajuste_dimensiones()
        if not zona or not dimensiones:
            return {'error': 'no_disponible'}
        items = []
        cambio = False
        partes = []
        for dimension, tramos, pagado in dimensiones:
            elegido_id = int(tramos_por_dimension.get(dimension.id, pagado.id or 0) or 0)
            elegido = tramos.filtered(lambda t: t.id == elegido_id)
            if elegido_id and not elegido:
                return {'error': 'sin_tramo'}
            if elegido != pagado:
                cambio = True
            partes.append("%s %s → %s" % (
                dimension._visar_wizard_label(),
                pagado.name or _("no contratado"),
                elegido.name or _("no contratado")))
            if not elegido:
                continue
            if elegido.is_valuation:
                return {'error': None, 'valoracion': True, 'cambio': True,
                        'resumen': "; ".join(partes)}
            # Mismo camino que el agente y la venta en campo: m² → tramo. Se usa el
            # inicio del tramo y se comprueba que vuelve al mismo, por si el
            # tabulador tiene solapes.
            item, error = dimension._visar_quote_item(max(elegido.m2_min, 1.0))
            if error or item['tier_id'] != elegido.id:
                return {'error': 'sin_tramo'}
            items.append(item)
        if not cambio:
            return {'error': 'sin_cambio'}
        if not items:
            return {'error': 'sin_precio'}
        Motor = self.env['appointment.type'].sudo()
        lineas = [v for v in (Motor._visar_build_sale_lines(items, zona) or [])
                  if not Motor._visar_skip_cart_line(v, zona)]
        if not lineas:
            return {'error': 'sin_precio'}
        Product = self.env['product.product'].sudo()
        nuevas = []
        for vals in lineas:
            producto = Product.browse(vals['product_id']).exists()
            if not producto:
                return {'error': 'sin_precio'}
            precio = Motor._visar_list_unit_price(producto, zona)
            nuevas.append({
                'product': producto,
                'quantity': vals.get('quantity') or 1,
                'price_unit': precio,
                'discount': vals.get('discount') or 0.0,
            })
        moneda = self._visar_upsell_currency()
        nuevo = sum(n['price_unit'] * (1 - n['discount'] / 100.0) * n['quantity']
                    for n in nuevas)
        pagado_total = self._visar_ajuste_pagado()
        diferencia = nuevo - pagado_total
        if moneda:
            nuevo, pagado_total, diferencia = (
                moneda.round(nuevo), moneda.round(pagado_total), moneda.round(diferencia))
        else:
            diferencia = float_round(diferencia, 2)
        return {
            'error': None,
            'valoracion': False,
            'cambio': True,
            'lineas': nuevas,
            'nuevo': nuevo,
            'pagado': pagado_total,
            'diferencia': diferencia,
            'resumen': "; ".join(partes),
        }

    # ------------------------------------------------------------------
    # Aplicar
    # ------------------------------------------------------------------
    def _visar_ajuste_aplicar(self, employee, tramos_por_dimension, motivo, fotos,
                              m2_por_dimension=None):
        """Registra el ajuste. Devuelve (estado, error).

        `fotos` = [(nombre, bytes, mimetype)]. Con la casa más grande deja las dos
        líneas en el carrito de adicionales; quien llama genera el cobro.
        """
        self.ensure_one()
        if not self._visar_ajuste_disponible():
            return None, 'no_disponible'
        motivo = (motivo or '').strip()
        if not motivo:
            return None, 'sin_motivo'
        fotos = [f for f in (fotos or []) if f and f[1]]
        if not fotos:
            return None, 'sin_foto'
        cotizacion = self._visar_ajuste_cotizar(tramos_por_dimension)
        if cotizacion.get('error'):
            return None, cotizacion['error']

        if cotizacion['valoracion']:
            estado = 'valoracion'
        else:
            comparacion = float_compare(cotizacion['diferencia'], 0.0, precision_digits=2)
            estado = 'mayor' if comparacion > 0 else 'menor' if comparacion < 0 else 'igual'

        if estado == 'mayor':
            error = self._visar_ajuste_crear_lineas(employee, cotizacion)
            if error:
                return None, error

        self.sudo().write({
            'visar_ajuste_estado': estado,
            'visar_ajuste_resumen': cotizacion['resumen'],
            'visar_ajuste_motivo': motivo,
            'visar_ajuste_diferencia': cotizacion.get('diferencia') or 0.0,
            'visar_ajuste_at': fields.Datetime.now(),
            'visar_ajuste_by_id': employee.id if employee else False,
        })
        adjuntos = self._visar_ajuste_guardar_fotos(fotos)
        self._visar_ajuste_guardar_m2(m2_por_dimension or {})
        self._visar_ajuste_nota(employee, cotizacion, estado, adjuntos)
        if estado in ('menor', 'valoracion'):
            self._visar_ajuste_avisar_oficina(estado)
        return estado, None

    def _visar_ajuste_crear_lineas(self, employee, cotizacion):
        """Servicio con la variante correcta + «ya pagado», en el carrito de la visita."""
        if not self._visar_upsell_can_start_round():
            return 'cobro_pendiente'
        credito = self._visar_upsell_credit_product()
        if not credito:
            return 'sin_credito'
        orden = self._visar_upsell_order(employee=employee, create=True)
        if not orden:
            return 'sin_pedido'
        pagadas = self._visar_ajuste_lineas_pagadas()
        ahora = fields.Datetime.now()
        comunes = {
            'order_id': orden.id,
            # Cuelga de ESTA visita y se queda ahí: `_visar_upsell_spawn_service_tasks`
            # deja fuera las líneas del ajuste, así que no nace otro servicio.
            'task_id': self.id,
            'visar_upsell_task_id': self.id,
            'visar_upsell_employee_id': employee.id if employee else False,
            'visar_upsell_at': ahora,
            'visar_ajuste_medidas': True,
        }
        Line = self.env['sale.order.line'].sudo().with_context(
            **self._VISAR_UPSELL_LINE_CTX)
        for nueva in cotizacion['lineas']:
            Line.create(dict(
                comunes,
                product_id=nueva['product'].id,
                name=_("Ajuste de medidas: %s", nueva['product'].display_name),
                product_uom_qty=nueva['quantity'],
                price_unit=nueva['price_unit'],
                discount=nueva['discount'],
            ))
        Line.create(dict(
            comunes,
            product_id=credito.id,
            name=_("Ya pagado por medidas anteriores: %s",
                   ", ".join(pagadas.mapped('product_id.display_name'))),
            product_uom_qty=1.0,
            price_unit=-cotizacion['pagado'],
            discount=0.0,
            tax_ids=[(6, 0, pagadas[:1].tax_ids.ids)],
        ))
        return None

    def _visar_ajuste_guardar_fotos(self, fotos):
        Attachment = self.env['ir.attachment'].sudo()
        adjuntos = Attachment.browse()
        for nombre, datos, mimetype in fotos:
            adjuntos |= Attachment.create({
                'name': nombre or 'medidas.jpg',
                'datas': base64.b64encode(datos),
                'res_model': 'project.task',
                'res_id': self.id,
                'mimetype': mimetype or 'image/jpeg',
                'visar_photo_key': FOTO_KEY,
            })
        return adjuntos

    def _visar_ajuste_guardar_m2(self, m2_por_dimension):
        """Los m² que el técnico midió, en el domicilio, para que el agente y el web
        no vuelvan a cotizar el tramo viejo. Solo lo que escribió: un tramo elegido
        no dice cuántos metros son."""
        domicilio = self.partner_id
        if not domicilio:
            return
        Dimension = self.env['visar.service.dimension'].sudo()
        Fact = self.env['visar.partner.fact'].sudo()
        for dimension_id, m2 in m2_por_dimension.items():
            dimension = Dimension.browse(int(dimension_id)).exists()
            if not dimension or not m2 or not 0 < m2 < 100000:
                continue
            slot = 'm2_construccion' if dimension.measure_type == 'interior' else 'm2_exterior'
            try:
                with self.env.cr.savepoint():
                    Fact._visar_fact_set(domicilio, slot, "%d" % int(m2), origen='hoja')
            except Exception:  # noqa: BLE001 - un dato de contexto no tumba el ajuste
                pass

    def _visar_ajuste_nota(self, employee, cotizacion, estado, adjuntos):
        moneda = self._visar_upsell_currency()

        def dinero(valor):
            return "%s%s" % (moneda.symbol or '$', "{:,.2f}".format(valor or 0.0))

        quien = employee.name if employee else _("El técnico")
        lineas = [Markup("<b>%s</b> ajustó las medidas: %s.") % (
            quien, cotizacion['resumen'])]
        if estado == 'valoracion':
            lineas.append(_("La medida real necesita valoración técnica: lo cotiza "
                            "oficina."))
        else:
            lineas.append(_("Precio con las medidas reales %(nuevo)s; ya pagado "
                            "%(pagado)s; diferencia %(dif)s.",
                            nuevo=dinero(cotizacion['nuevo']),
                            pagado=dinero(cotizacion['pagado']),
                            dif=dinero(cotizacion['diferencia'])))
        lineas.append(Markup("Motivo: %s") % self.visar_ajuste_motivo)
        cuerpo = Markup("<p>%s</p>") % Markup("<br/>").join(lineas)
        self.sudo().message_post(body=cuerpo, subtype_xmlid='mail.mt_note',
                                 attachment_ids=adjuntos.ids)
        orden = self.sale_order_id.sudo()
        if orden:
            orden.message_post(body=cuerpo, subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # El cliente no acepta
    # ------------------------------------------------------------------
    def _visar_ajuste_lineas(self):
        self.ensure_one()
        return self._visar_upsell_lines().filtered('visar_ajuste_medidas')

    def _visar_ajuste_pagada(self):
        """¿La diferencia ya se cobró? Por factura pagada, pago en línea o efectivo."""
        self.ensure_one()
        facturas = self._visar_ajuste_lineas().invoice_lines.move_id.filtered(
            lambda m: m.move_type == 'out_invoice' and m.state == 'posted')
        for factura in facturas:
            if factura.payment_state in ('paid', 'in_payment'):
                return True
            if any(t.state in ('done', 'authorized') for t in factura.sudo().transaction_ids):
                return True
            if self._visar_upsell_cash_covers(factura):
                return True
        return False

    def _visar_ajuste_rechazar(self, employee):
        """El cliente no paga la diferencia: se hace solo lo pagado. (ok, error)."""
        self.ensure_one()
        if self.visar_ajuste_estado != 'mayor':
            return False, 'no_disponible'
        if self._visar_ajuste_pagada():
            return False, 'ya_pagado'
        lineas = self._visar_ajuste_lineas()
        facturas = lineas.invoice_lines.move_id.filtered(lambda m: m.state != 'cancel')
        if facturas:
            otras = facturas.invoice_line_ids.sale_line_ids - lineas
            if otras.filtered(lambda l: not l.display_type):
                return False, 'mixto'
            facturas.sudo().button_draft()
            facturas.sudo().button_cancel()
        lineas.with_context(**self._VISAR_UPSELL_LINE_CTX).write({'product_uom_qty': 0.0})
        self._visar_upsell_purge(lineas)
        self.sudo().write({'visar_ajuste_estado': 'rechazado'})
        quien = employee.name if employee else _("El técnico")
        self.sudo().message_post(body=Markup(
            "<p><b>%s</b>: el cliente no aceptó pagar la diferencia por medidas. Se "
            "hace solo lo que se pagó; el servicio queda incompleto por medidas.</p>")
            % quien, subtype_xmlid='mail.mt_note')
        self._visar_ajuste_avisar_oficina('rechazado')
        return True, None

    # ------------------------------------------------------------------
    # Avisos a oficina
    # ------------------------------------------------------------------
    _AVISOS_AJUSTE = {
        'menor': ("Medidas menores a lo pagado",
                  "La casa resultó más chica que lo pagado. No se devolvió nada; "
                  "decidan si procede algo con el cliente."),
        'valoracion': ("Cotizar ajuste de medidas",
                       "La medida real cae en valoración técnica: no hay precio de "
                       "lista. Cotizar la diferencia con el cliente."),
        'rechazado': ("Servicio incompleto por medidas",
                      "El cliente no aceptó pagar la diferencia: se hizo solo lo "
                      "pagado. Dar seguimiento."),
        'sin_pagar': ("Cobrar diferencia por medidas",
                      "El servicio se cerró con la diferencia por medidas sin pagar."),
    }

    def _visar_ajuste_avisar_oficina(self, motivo):
        resumen, nota = self._AVISOS_AJUSTE[motivo]
        resumen = "%s: %s" % (resumen, self.name or '')
        modelo = self.env['ir.model']._get_id('project.task')
        if self.env['mail.activity'].sudo().search_count([
                ('res_model_id', '=', modelo), ('res_id', '=', self.id),
                ('summary', '=', resumen)]):
            return
        responsable = self._visar_quote_responsable(self.sale_order_id.sudo())
        self.sudo().activity_schedule(
            'mail.mail_activity_data_todo',
            summary=resumen,
            note=Markup("<p>%s</p><p>%s</p>") % (nota, self.visar_ajuste_resumen or ''),
            user_id=responsable.id)

    def _visar_ajuste_al_cerrar(self):
        """Al cerrar el servicio: si la diferencia sigue sin pagar, oficina se entera."""
        for task in self:
            if task.visar_ajuste_estado == 'mayor' and not task._visar_ajuste_pagada():
                task._visar_ajuste_avisar_oficina('sin_pagar')

    # ------------------------------------------------------------------
    # Para la pantalla
    # ------------------------------------------------------------------
    def _visar_ajuste_opciones(self):
        """Lo que pinta la ventana: una fila de tramos por dimensión."""
        self.ensure_one()
        return [{
            'id': dimension.id,
            'label': dimension._visar_wizard_label(),
            'pagado_id': pagado.id or 0,
            'pagado': pagado.name or _("No contratado"),
            'tramos': [{'id': 0, 'name': _("No contratado")}] + [
                {'id': t.id, 'name': t.name or t.display_name,
                 'valoracion': t.is_valuation,
                 'm2_min': t.m2_min, 'm2_max': t.m2_max} for t in tramos],
        } for dimension, tramos, pagado in self._visar_ajuste_dimensiones()]
