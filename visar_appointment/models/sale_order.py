# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Dirección de servicio capturada en el wizard/valoración. Si está definida,
    # el checkout de eCommerce no puede sustituirla por la dirección del usuario
    # logueado (causa del bug "Santos Cantú" vs dirección del flujo).
    visar_service_partner_id = fields.Many2one(
        'res.partner',
        string='Dirección de servicio Visar',
        index=True,
        copy=False,
        help='Contacto de entrega fijado por el flujo Visar (wizard/valoración).',
    )

    def _visar_apply_zone_pricelist(self, zone, plan=None):
        """Asigna al carrito/orden la lista de precios de la zona.

        Con `plan` usa la lista (zona × plan) de la póliza, que deriva sus precios de
        la lista de la zona: el servicio recurrente lleva el descuento del plan y todo
        lo demás (add-ons, extras, roedores) cotiza idéntico a una compra única.
        """
        self.ensure_one()
        if not zone:
            return
        pricelist = zone._visar_poliza_pricelist(plan)
        if pricelist:
            self.pricelist_id = pricelist

    def _visar_reassert_zone_pricelist(self, zone, plan=None):
        """Vuelve a imponer la lista de la zona (× plan) si algo la cambió, y
        reprecia. Devuelve True si hubo que corregir.

        Existe porque **tocar el cliente de la orden reprecia las líneas**: Odoo
        recalcula `pricelist_id` desde el partner. El 25-sep-2026 eso dejó pedidos
        de póliza con el servicio al precio de CONTADO (690 en vez de 655.50) y la
        mensualidad adelantada al del plan — dos precios distintos para lo mismo en
        el mismo documento, y el cliente veía 655.50 en el wizard y 690 al pagar
        (S00348 en producción).

        El arreglo de fondo es el ORDEN (fijar el cliente antes de cotizar), pero
        esto se queda como red: cualquier recálculo futuro se corrige en vez de
        llegar callado a una liga de pago. Avisa al log cuando actúa, para que no
        vuelva a ser invisible.
        """
        self.ensure_one()
        if not zone:
            return False
        esperada = zone._visar_poliza_pricelist(plan)
        if not esperada or self.pricelist_id == esperada:
            return False
        _logger.warning(
            "visar: la orden %s se quedo con la lista %s en vez de %s "
            "(plan=%s); se corrige y se reprecia.",
            self.name, self.pricelist_id.display_name, esperada.display_name,
            plan.display_name if plan else None)
        self.pricelist_id = esperada
        self._recompute_prices()
        return True

    # ------------------------------------------------------------------
    # Armado de la reserva (compartido por el wizard web y el agente WhatsApp)
    # ------------------------------------------------------------------
    #
    # Estos dos metodos vivian en el controlador del wizard. Se bajaron al modelo
    # porque el agente de WhatsApp necesita exactamente lo mismo SIN peticion HTTP
    # (no hay navegador ni sesion: la orden se arma por RPC y el cliente solo
    # recibe una liga de pago). Reimplementarlos alla habria creado dos
    # front-ends con dos verdades; en cuanto cambie una regla de precio, divergen.
    # El controlador ahora solo delega. Ver `.context/33-whatsapp-agendado-design.md` §11.

    def _visar_apply_delivery_address(self, address, partner_name=None):
        """Crea (o reutiliza) el contacto de entrega y lo fija como direccion de servicio.

        `address` = {street, ext_num, int_num, neighborhood, zip, city}, tal cual
        lo captura el wizard. Sin direccion o sin cliente no hace nada.
        """
        self.ensure_one()
        address = address or {}
        if not address or not self.partner_id:
            return self.env['res.partner'].browse()
        Partner = self.env['res.partner'].sudo()
        commercial = self.partner_id.commercial_partner_id
        country = self.env.ref('base.mx', raise_if_not_found=False)
        state = self._visar_service_state()

        street = (address.get('street') or '').strip()
        ext_num = (address.get('ext_num') or '').strip()
        int_num = (address.get('int_num') or '').strip()
        if ext_num:
            street = ('%s No. %s' % (street, ext_num)).strip()
        if int_num:
            street = ('%s Int. %s' % (street, int_num)).strip()

        vals = {
            'name': partner_name or self.partner_id.name or "Dirección de servicio",
            'type': 'delivery',
            'parent_id': commercial.id,
            'street': street,
            'street2': address.get('neighborhood') or '',
            'zip': address.get('zip') or '',
            'city': address.get('city') or '',
            'state_id': state.id if state else False,
            'country_id': country.id if country else False,
        }
        # Reutiliza un contacto de entrega idéntico si ya existe.
        existing = Partner.search([
            ('parent_id', '=', commercial.id),
            ('type', '=', 'delivery'),
            ('street', '=', vals['street']),
            ('zip', '=', vals['zip']),
        ], limit=1)
        delivery_partner = existing or Partner.create(vals)
        if existing:
            # Keep name/details fresh when reusing (e.g. new booking contact name).
            existing.write({
                k: vals[k] for k in ('name', 'street2', 'city', 'state_id', 'country_id')
                if vals.get(k)
            })
        self._visar_set_service_shipping(delivery_partner)
        return delivery_partner

    # Campos que el checkout exige en la dirección de FACTURACIÓN
    # (`portal._get_mandatory_address_fields`) y que el wizard ya capturó.
    @api.model
    def _visar_service_state(self):
        """Nuevo León: el estado de toda dirección de servicio de Visar.

        Por xmlid. Antes se buscaba por código 'NL' y en Odoo el código es 'NLE':
        no se encontraba nunca y las direcciones se guardaban SIN estado. México
        lo exige para facturar, así que el checkout daba la dirección por
        incompleta y la volvía a pedir (7-oct-2026).
        """
        return self.env.ref('base.state_mx_nl', raise_if_not_found=False) \
            or self.env['res.country.state']

    # La dirección de facturación se SUPUSO igual a la de servicio y el cliente
    # todavía no la ha visto. El checkout le enseña el formulario una vez, ya
    # lleno, para que la confirme o la cambie. Ver `controllers/checkout.py`.
    visar_billing_assumed = fields.Boolean(
        "Facturación supuesta igual al servicio", copy=False)

    _VISAR_BILLING_ADDRESS_FIELDS = (
        'street', 'street2', 'zip', 'city', 'state_id', 'country_id')

    def _visar_prefill_billing_address(self):
        """Le presta al cliente su dirección de servicio como domicilio, si no tiene.

        El wizard guarda la dirección en un contacto de ENTREGA aparte; la ficha
        del cliente queda con nombre, correo y teléfono, y sin calle. El checkout
        normal ("Finalizar compra") exige dirección de facturación, no la
        encuentra y le pone delante un formulario VACÍO: al cliente le parece que
        la reserva no guardó nada y vuelve a teclear hasta el código postal.

        Solo si la ficha no tiene NINGÚN dato de dirección. Con que tenga uno
        —aunque esté incompleta— no se toca: mezclar media dirección suya con
        media del servicio daría un domicilio que no existe, y los datos de un
        cliente no se pisan desde un formulario público.

        Se llama solo desde el flujo WEB, que es el único que pasa por ese
        checkout. Devuelve True si rellenó.
        """
        self.ensure_one()
        origen = self.visar_service_partner_id
        cliente = self.partner_invoice_id or self.partner_id
        if not origen or not cliente or cliente == origen:
            return False
        if any(cliente[f] for f in self._VISAR_BILLING_ADDRESS_FIELDS):
            return False
        vals = {f: (origen[f].id if hasattr(origen[f], 'id') else origen[f])
                for f in self._VISAR_BILLING_ADDRESS_FIELDS if origen[f]}
        if not vals.get('street'):
            return False
        cliente.sudo().write(vals)
        self.sudo().visar_billing_assumed = True
        return True

    def _visar_fill_from_booking(self, booking, calendar_booking, zone, plan=None,
                                 tz=None, canal=None):
        """Agrega al pedido las lineas del wizard. Devuelve cuantas agrego (0 = fallo).

        NO borra la reserva ni redirige: eso es politica del llamador (el
        controlador redirige a 'failed-resource'; el agente devuelve un error
        tipado). Aqui solo se arma el pedido.

        El orden importa y no es arbitrario:
          1. se suelta el plan ANTES de resolver el nuevo (si no, un cliente que
             contrato poliza, volvio atras y reservo compra unica se llevaba el
             plan pegado, y cambiar de plan lanzaba UserError);
          2. el descuento se escribe **inmediatamente despues** del `_cart_add` de
             su propia linea, no al final: es como se identifica sin ambiguedad;
          3. las mensualidades adelantadas van al FINAL, cuando cada linea de
             servicio ya tiene su estado definitivo (el descuento de combo solo
             queda fijo al terminar el recorrido).
        """
        self.ensure_one()
        master = self.env['appointment.type'].browse(
            (booking or {}).get('master_appointment_type_id')).exists()
        if not master:
            return 0

        self.plan_id = False
        self._visar_apply_zone_pricelist(zone, plan=plan)

        sale_lines = master._visar_build_sale_lines(
            booking.get('items', []), zone,
            plagas=master._visar_selections_plagas(booking.get('selections')),
            extra_addons=booking.get('extras_accepted'))
        if not sale_lines:
            return 0

        tz = tz or calendar_booking.appointment_type_id.appointment_tz
        quantity = calendar_booking.asked_capacity or 1
        lines_added = 0

        for line_vals in sale_lines:
            if master._visar_skip_cart_line(line_vals, zone, plan=plan):
                continue
            line_qty = line_vals.get('quantity', quantity)
            # `allow_one_time_sale` deja inalcanzable la rama de suscripción de
            # website_sale_subscription en el flujo de compra única.
            cart_values = self._cart_add(
                product_id=line_vals['product_id'],
                quantity=line_qty,
                calendar_booking_id=calendar_booking.id,
                calendar_booking_tz=tz,
                plan_id=plan.id if plan else None,
                allow_one_time_sale=not plan,
            )
            if cart_values.get('quantity', 0) < line_qty:
                return 0
            lines_added += 1
            discount = line_vals.get('discount') or 0.0
            if discount:
                sol = self.order_line.filtered(
                    lambda line: line.product_id.id == line_vals['product_id']
                    and calendar_booking in line.calendar_booking_ids
                )[-1:]
                if sol:
                    sol.write({'discount': discount})

        if not lines_added:
            return 0
        if plan:
            self._visar_sync_anticipo_lines()
        # Ficha de CRM y enlace de la cotizacion, AQUI y no al confirmar: el core
        # cuenta las cotizaciones de un lead con `state in ('draft','sent')`, asi
        # que enlazar al pasar a 'sale' deja el contador en cero para siempre.
        # No-op si `visar_crm` no esta instalado (gancho en `visar_base`), y
        # `canal` lo pone el llamador porque es quien sabe de donde viene.
        self._visar_crm_after_fill(booking, canal=canal)
        return lines_added

    def _visar_set_service_shipping(self, partner):
        """Fija la dirección de servicio Visar y la usa como partner_shipping_id."""
        self.ensure_one()
        if not partner:
            return
        self.with_context(visar_allow_shipping_change=True).write({
            'visar_service_partner_id': partner.id,
            'partner_shipping_id': partner.id,
        })

    def _update_address(self, partner_id, fnames=None):
        """El checkout no reemplaza la dirección de servicio NI el precio ya cotizado.

        **La dirección.** Con dirección de servicio fijada, el checkout no puede
        cambiar `partner_shipping_id`.

        **El precio (6-oct-2026).** Cada vez que el checkout de eCommerce escribe
        el cliente o la dirección de facturación, `website_sale` deja que Odoo
        recalcule `pricelist_id` desde el partner y REPRECIA las líneas. Un
        cliente sin lista propia cae en la general del sitio, así que una póliza
        cotizada en el wizard con la lista (zona × plan) subía de precio al
        llenar el formulario de dirección de "Finalizar compra": veía 3,990 y
        pagaba más. El 25-sep se corrigió el mismo mecanismo en el traspaso del
        wizard al carrito (`_visar_reassert_zone_pricelist`); el checkout de
        después quedaba sin vigilar, y solo no se notaba porque las pruebas se
        pagaban con el botón exprés de "Demostración", que se salta esos pasos.

        Se RESTAURA la foto de antes en vez de repreciar con la lista buena:
        `_recompute_prices` pone el descuento de cada línea en cero y lo recalcula
        desde la lista, así que se llevaría el descuento de combo que el wizard
        escribió a mano. Lo que el cliente vio es exactamente lo que había antes
        de tocar la dirección, y eso es lo que se deja.

        La guardia es `visar_service_partner_id`: solo los pedidos armados por el
        flujo Visar, cuyo precio sale de la zona del domicilio de SERVICIO y no
        del cliente que factura. La tienda normal sigue igual.
        """
        if fnames and self.visar_service_partner_id:
            fnames = [f for f in fnames if f != 'partner_shipping_id']
            if not fnames:
                return
        if not (fnames and len(self) == 1 and self.visar_service_partner_id):
            return super()._update_address(partner_id, fnames)

        lista = self.pricelist_id
        foto = {line.id: (line.price_unit, line.discount)
                for line in self.order_line if not line.display_type}
        result = super()._update_address(partner_id, fnames)
        if lista and self.pricelist_id != lista:
            _logger.warning(
                "visar: el checkout cambio la lista de la orden %s de %s a %s al "
                "escribir %s; se restaura la lista y el precio cotizados.",
                self.name, lista.display_name, self.pricelist_id.display_name, fnames)
            self.pricelist_id = lista
            for line in self.order_line:
                if line.id in foto:
                    price_unit, discount = foto[line.id]
                    if line.price_unit != price_unit or line.discount != discount:
                        line.write({'price_unit': price_unit, 'discount': discount})
            self._visar_cache_request_pricelist(lista)
        return result

    @api.model
    def _visar_cache_request_pricelist(self, pricelist):
        """Deja la lista restaurada como la "actual" del sitio en esta sesión.

        `website_sale` guarda en la sesión la lista con la que pinta el carrito;
        `_update_address` acaba de dejar ahí la equivocada. Sin petición HTTP (el
        agente, un cron) no hay nada que corregir.
        """
        try:
            from odoo.http import request
            from odoo.addons.website_sale.models.website import (
                PRICELIST_SESSION_CACHE_KEY)
            if request and getattr(request, 'session', None) is not None:
                request.session[PRICELIST_SESSION_CACHE_KEY] = pricelist.id
                request.pricelist = pricelist
        except Exception:  # noqa: BLE001 - la sesion es comodidad; el pedido ya esta bien
            _logger.debug("visar: no se pudo refrescar la lista de la sesion", exc_info=True)

    def write(self, vals):
        if (
            'partner_shipping_id' in vals
            and not self.env.context.get('visar_allow_shipping_change')
        ):
            locked = self.filtered('visar_service_partner_id')
            unlocked = self - locked
            res = True
            if unlocked:
                res = super(SaleOrder, unlocked).write(vals)
            for order in locked:
                order_vals = dict(vals)
                order_vals['partner_shipping_id'] = order.visar_service_partner_id.id
                super(SaleOrder, order).write(order_vals)
            return res
        return super().write(vals)
