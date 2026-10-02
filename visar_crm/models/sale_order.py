# -*- coding: utf-8 -*-
import logging

from markupsafe import Markup

from odoo import _, models
from odoo.tools import float_compare, format_amount

from .crm_lead import CANAL_TEAM_XMLIDS

_logger = logging.getLogger(__name__)

# Marca invisible que hace idempotente la nota de enlace. Va en un comentario
# HTML porque el cliente no tiene que verla, y se busca sobre `message_ids` del
# lead (que son pocos) en vez de llevar un campo nuevo: el carrito web se
# REUTILIZA entre reservas (`request.cart or website._create_cart()`), asi que
# `_visar_crm_after_fill` puede correr varias veces sobre la misma orden y sin
# esto el expediente del cliente acumularia la misma nota en cada pasada.
MARCA_ENLACE = "visar-crm-enlace-orden-%s"


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # ==================================================================
    # Enlace cotizacion <-> ficha de CRM (2-oct-2026)
    # ==================================================================
    #
    # Hasta hoy NADIE escribia `sale.order.opportunity_id`: 0 de 379 ordenes en
    # produccion. La pestana "Cotizaciones" de toda ficha estaba vacia en todos
    # los canales, y las 72 cotizaciones vivas eran invisibles desde su ficha.
    #
    # ## Por que se enlaza en BORRADOR y no al confirmar
    #
    # El core cuenta las cotizaciones de una ficha filtrando por estado
    # (`sale_crm/models/crm_lead.py::_get_lead_quotation_domain`):
    #
    #     [('state', 'in', ('draft', 'sent'))]
    #
    # Enlazar al confirmar (`state == 'sale'`) llega TARDE: en ese instante la
    # orden ya dejo de ser cotizacion, asi que `quotation_count` se queda en cero
    # para siempre y se llena "Pedidos" en vez de "Cotizaciones" — justo lo que
    # se venia a arreglar. El gancho real es `_visar_crm_after_fill`, que corre
    # cuando el wizard acaba de poner las lineas y la orden sigue en borrador.
    # El hook de `write` se queda solo como RED para ordenes de backoffice.

    def _visar_crm_link(self, leads):
        """Enlaza esta orden con `leads` y reparte su importe entre ellos.

        **La principal es la MAS ANTIGUA** (menor `id`), no la de mayor importe:
        `opportunity_id` es Many2one y una orden combo tiene una ficha por grupo,
        asi que hay que elegir. Desempatar por importe haria que la identidad de
        la ficha principal dependiera del PRECIO —la misma casa cambia de ficha
        principal si se mueve el tabulador o el descuento de combo—, mientras que
        "la ficha que el cliente abrio primero" es estable y se explica en una
        frase.

        Las demas fichas del combo no se quedan sin nada: cada una recibe nota
        con el numero de orden y **su** trozo del importe, y su
        `expected_revenue` se pone a ese trozo. Asi el valor del embudo deja de
        contar el combo dos veces.
        """
        self.ensure_one()
        if not leads:
            return
        Lead = self.env['crm.lead']
        principal = leads.sorted('id')[0]

        # Last-write-wins, NO "poner si esta vacio": el carrito web se reutiliza,
        # asi que si la misma sesion reserva para A y luego para B, un enlace
        # condicional dejaria pegada la oportunidad de A.
        if self.opportunity_id != principal:
            self.sudo().write({'opportunity_id': principal.id})

        for lead in leads:
            self._visar_crm_link_one(lead, Lead)

    def _visar_crm_link_one(self, lead, Lead):
        """Pone a `lead` su trozo del importe y le deja constancia una sola vez."""
        self.ensure_one()
        grupo = lead.visar_service_group_id
        trozo = (Lead._visar_order_group_amount(self, grupo) if grupo
                 else self.amount_total)
        moneda = self.currency_id or self.env.company.currency_id

        vals = {}
        # El partner aparecio despues de crear el lead: enlazarlo. No es politica
        # nueva — `visar.agent.tools._agent_open_lead` ya lo hace por la misma
        # razon. El lead del agente nace con solo telefono (no crea partner a
        # proposito), y aqui el partner YA existe y es demostrablemente la misma
        # persona, porque casamos por ese telefono.
        if not lead.partner_id and self.partner_id:
            vals['partner_id'] = self.partner_id.id
        if trozo and float_compare(
                lead.expected_revenue or 0.0, trozo,
                precision_rounding=moneda.rounding) != 0:
            vals['expected_revenue'] = trozo
        if vals:
            lead.sudo().write(vals)

        marca = MARCA_ENLACE % self.id
        if any(marca in (m.body or '') for m in lead.sudo().message_ids):
            return
        lead.sudo().message_post(
            body=Markup("<p><b>%s</b></p><p>%s</p><!-- %s -->") % (
                _("Cotización ligada: %s", self.name or ''),
                _("%(importe)s de esta orden corresponde a %(grupo)s.",
                  importe=format_amount(self.env, trozo, moneda),
                  grupo=grupo.display_name if grupo else _("este cliente")),
                marca),
            subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # Los dos puntos de extension que `visar_base` declara como no-op
    # ------------------------------------------------------------------

    def _visar_crm_after_fill(self, booking, canal=None):
        """El wizard acabo de poner las lineas: abrir ficha y enlazar, en BORRADOR.

        Lo llaman los dos canales desde `visar_appointment`. `canal` lo dice el
        LLAMADOR porque es quien lo sabe; deducirlo de `website_id` seria
        adivinarlo. Sin canal no se abre ficha (una orden de backoffice no tiene
        canal, e inventarselo seria peor que no enlazar).

        Best-effort de punta a punta: un fallo aqui NO puede tumbar una reserva
        que el cliente esta a punto de pagar.
        """
        for order in self:
            if not canal:
                continue
            try:
                leads = self.env['crm.lead']._visar_crm_ensure_order_leads(
                    order, canal)
                order._visar_crm_link(leads)
            except Exception:  # noqa: BLE001 - el CRM nunca tumba una reserva
                _logger.exception(
                    "visar_crm: no se pudo enlazar la orden %s (canal %s)",
                    order.name or order.id, canal)
        return super()._visar_crm_after_fill(booking, canal=canal)

    def _visar_inherit_crm_from(self, origin):
        """Hereda la oportunidad de la orden de la que ESTA nace.

        La usan la cotizacion manual de tratamientos y el upsell en sitio, los
        dos en `visar_field_app` — que no depende de `visar_crm` ni de
        `sale_crm`, y por eso el gancho se declara en `visar_base`.
        """
        if origin and origin.opportunity_id:
            for order in self.filtered(lambda o: not o.opportunity_id):
                order.sudo().write({'opportunity_id': origin.opportunity_id.id})
        return super()._visar_inherit_crm_from(origin)

    # ------------------------------------------------------------------
    # Avance de etapa por eventos reales de la orden
    # ------------------------------------------------------------------

    def write(self, vals):
        res = super().write(vals)
        # Servicio programado: la orden se confirmo/pago (state 'sale'). En el
        # flujo web la confirmacion nativa por transaccion de pago es lo que
        # lleva la orden a 'sale' (no hay confirmacion custom), asi que 'sale' es
        # la senal de "pagado y real". El grupo se deriva de las lineas de
        # servicio (que si llevan producto->dimension->grupo), no del evento ->
        # sin carrera de timing con la creacion del calendar.event.
        if vals.get('state') == 'sale':
            for order in self.filtered(lambda o: o.state == 'sale'):
                order._visar_crm_on_confirmed()
        # Cotizacion enviada: SOLO la cotizacion formal (ver
        # `_visar_is_formal_quote`). Un carrito web al que alguien le da "enviar
        # por correo" no es la cotizacion formal que arma una persona tras la
        # visita, que es como el doc 31 define esta etapa.
        if vals.get('state') == 'sent':
            etapa = self.env.ref('visar_crm.crm_stage_wa_cotizacion',
                                 raise_if_not_found=False)
            for order in self.filtered(
                    lambda o: o.state == 'sent' and o.opportunity_id
                    and o._visar_is_formal_quote()):
                order.opportunity_id._visar_advance_stage(etapa)
        return res

    def _visar_crm_on_confirmed(self):
        """La orden se pago: mover su(s) ficha(s) a la etapa que toque.

        **Red, no camino principal:** si la orden paso por el wizard ya tiene
        ficha y enlace desde el borrador. Esto cubre tambien la orden de
        backoffice que nunca paso por ahi — pero solo AVANZA, no abre fichas:
        `_visar_crm_ensure_order_leads` necesita canal, y una orden que capturo
        un humano a mano no tiene canal. Inventarselo seria adivinar.
        """
        self.ensure_one()
        Lead = self.env['crm.lead']
        # Valoracion ANTES que servicio programado, y fuera del guardia de los
        # grupos: una orden de pura valoracion no tiene ninguno (su producto no
        # lleva grupo, y es por diseno), asi que el guardia la descartaba en
        # silencio y la etapa "Visita de valoracion agendada" llevaba 0 leads en
        # toda su historia.
        grupos = Lead._visar_order_service_groups(self)
        if not grupos and Lead._visar_order_is_valuation(self):
            self._visar_crm_advance_valuation()
            return
        if grupos:
            # Una orden que trae valoracion Y servicio real va a "programado": el
            # cliente compro un servicio, no solo el diagnostico. La rama de
            # arriba es para la valoracion SOLA, que es la que no tiene grupo.
            Lead._visar_crm_advance_order_leads(
                self, 'visar_crm.crm_stage_wa_programado')

    def _visar_crm_advance_valuation(self):
        """Avanza a "Visita de valoracion agendada". Match unico o nada.

        El producto de valoracion **no lleva grupo de servicio y es a proposito**
        (`_visar_wizard_valuation_items` devuelve `dimension_id: False`: *"el
        corte existe justamente para no medir"*), asi que no se puede emparejar
        por (telefono, grupo) como el resto. Dos caminos, en este orden:

        1. Si la orden ya trae ficha (paso por el wizard, enlace en borrador),
           esa es la ficha. Exacto, sin adivinar.
        2. Si no, se busca por telefono **sin importar el grupo**, y solo se
           mueve si hay **exactamente una** ficha abierta. Con dos o mas no se
           toca nada: mover la equivocada es peor que no mover ninguna.
        """
        self.ensure_one()
        Lead = self.env['crm.lead']
        etapa = self.env.ref('visar_crm.crm_stage_wa_valoracion',
                             raise_if_not_found=False)
        if not etapa:
            return
        if self.opportunity_id:
            self.opportunity_id._visar_advance_stage(etapa)
            return
        nat = Lead._visar_crm_order_nat(self)
        if not nat:
            return
        cerrado = self.env.ref('visar_crm.crm_stage_wa_cerrado',
                               raise_if_not_found=False)
        dominio = [('visar_wa_phone_norm', '=', nat),
                   ('team_id', 'in', [t.id for t in (
                       Lead._visar_crm_team(c) for c in CANAL_TEAM_XMLIDS) if t])]
        if cerrado:
            dominio.append(('stage_id', '!=', cerrado.id))
        abiertas = Lead.sudo().search(dominio)
        if len(abiertas) == 1:
            abiertas._visar_advance_stage(etapa)
        elif len(abiertas) > 1:
            _logger.info(
                "visar_crm: la valoracion %s no se atribuyo: el telefono "
                "terminado en %s tiene %d fichas abiertas y ninguna se puede "
                "senalar sin adivinar.", self.name or self.id, nat[-4:],
                len(abiertas))
