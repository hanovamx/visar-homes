# -*- coding: utf-8 -*-
from odoo import fields, models


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
        state = self.env['res.country.state'].sudo().search([
            ('country_id', '=', country.id), ('code', '=', 'NL'),
        ], limit=1) if country else self.env['res.country.state'].sudo()

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

    def _visar_fill_from_booking(self, booking, calendar_booking, zone, plan=None,
                                 tz=None):
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
            include_roedores=master._visar_selections_has_roedores(
                booking.get('selections')),
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
        """Evita que el checkout reemplace la dirección de servicio Visar."""
        if fnames and self.visar_service_partner_id:
            fnames = [f for f in fnames if f != 'partner_shipping_id']
            if not fnames:
                return
        return super()._update_address(partner_id, fnames)

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
