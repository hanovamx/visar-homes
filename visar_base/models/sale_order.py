# -*- coding: utf-8 -*-
from odoo import _, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # --- Lista de precios obligatoria para confirmar (REQ-004) ---
    # Visar cobra por zona (A/B/C). Odoo llenaba la lista de toda cotización con
    # la primera lista activa ("VISAR Zona A", sequence 11) cuando el cliente no
    # tenía una propia, y así se podía confirmar con el precio de otra zona. Ese
    # default ya no existe fuera del sitio web (`visar_appointment/models/
    # res_partner.py`); esto impide confirmar sin elegirla.

    def _visar_requiere_lista_de_precios(self):
        """¿Esta orden necesita lista de precios para confirmarse?

        No cuando la confirma un PAGO en línea: ahí el dinero ya entró, y un error
        a media transacción deja al cliente pagado y sin pedido confirmado (el
        problema de REQ-002). Ver `payment.transaction`. Los módulos que tienen
        sus propias excepciones (el upsell de campo) extienden este método.
        """
        self.ensure_one()
        return not self.env.context.get('visar_confirmacion_por_pago')

    def _confirmation_error_message(self):
        error = super()._confirmation_error_message()
        if error or self.pricelist_id or not self._visar_requiere_lista_de_precios():
            return error
        return _("Selecciona la lista de precios de la zona del cliente antes de "
                 "confirmar la cotización.")

    def _visar_apply_mandatory_addons(self, addon_map):
        """Agrega o suma líneas de add-ons obligatorios (SO manual / backend)."""
        self.ensure_one()
        if not addon_map:
            return

        SaleOrderLine = self.env['sale.order.line'].with_context(
            visar_skip_mandatory_addons=True,
        )
        for product_id, qty in addon_map.items():
            if qty <= 0:
                continue
            existing = self.order_line.filtered(
                lambda line: line.product_id.id == product_id and not line.display_type
            )
            if existing:
                existing[0].write({'product_uom_qty': existing[0].product_uom_qty + qty})
            else:
                SaleOrderLine.create({
                    'order_id': self.id,
                    'product_id': product_id,
                    'product_uom_qty': qty,
                })

    # ------------------------------------------------------------------
    # Puntos de extensión para el CRM (no-op aquí a propósito)
    # ------------------------------------------------------------------
    #
    # Los implementa `visar_crm`. Viven en `visar_base` y no ahí porque quien los
    # LLAMA son módulos que no pueden depender de `visar_crm`:
    # `visar_field_app` depende de `visar_fsm` y `visar_appointment` no declara
    # `crm`. Definir el gancho en el ancestro común es lo que evita (a) añadir
    # `sale_crm` a las dependencias de `visar_field_app` solo para copiar un
    # campo, y (b) el duck-typing con `if 'opportunity_id' in order._fields`.
    # Sin `visar_crm` instalado, los dos no hacen nada y todo sigue funcionando.
    # (2-oct-2026, al enlazar cotización ↔ ficha de CRM.)

    def _visar_inherit_crm_from(self, origin):
        """Hereda la oportunidad de CRM de la orden `origin`. No-op sin `visar_crm`.

        La usan las órdenes que NACEN de otra: la cotización manual de
        tratamientos y el upsell en sitio, los dos en `visar_field_app`.
        """
        return

    def _visar_is_formal_quote(self):
        """¿Es la cotización FORMAL que arma una persona tras una visita?

        False aquí: una orden normal es un carrito, no una cotización formal.
        La sobreescribe `visar_field_app` para la cotización manual de
        tratamientos (termitas, chinches), que es la única que lo es.

        Existe porque `visar_crm` necesita la respuesta y **no puede preguntarle
        a `visar_field_app`**: son módulos hermanos. Y la distinción importa —
        el doc 31 §12 decisión 4 define la etapa «Cotización enviada» como *la
        cotización formal/manual tras la visita de valoración*, explícitamente
        NO la del agente. Mover esa etapa con cualquier orden que llegue a
        `sent` metería ahí cada carrito web al que alguien le diera «enviar».
        """
        self.ensure_one()
        return False

    def _visar_crm_after_fill(self, booking, canal=None):
        """Avisa de que la orden ya tiene sus líneas del wizard. No-op sin `visar_crm`.

        Es el momento en que se enlaza la cotización con su ficha, y es
        importante que sea ESTE y no la confirmación: el core cuenta las
        cotizaciones de una ficha con `[('state', 'in', ('draft', 'sent'))]`
        (`sale_crm/models/crm_lead.py`), así que enlazar al confirmar deja
        `quotation_count` en cero para siempre y la pestaña «Cotizaciones» vacía.

        `canal` lo dice el llamador ('web' o 'whatsapp'), que es quien lo sabe;
        deducirlo de `website_id` sería adivinarlo.
        """
        return
