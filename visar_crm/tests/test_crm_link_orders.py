# -*- coding: utf-8 -*-
"""Enlace cotizacion <-> ficha de CRM, y la ficha de una reserva web (2-oct-2026).

Hasta hoy **nadie escribia `sale.order.opportunity_id`**: 0 de 379 ordenes en
produccion, y la pestana "Cotizaciones" de toda ficha estaba vacia en todos los
canales. Y el lead de una reserva web lo creaba `appointment_crm` sin telefono
normalizado ni grupo de servicio, asi que los automatismos de avance no lo
encontraban nunca y oficina lo movia a mano.

Lo que se protege aqui, por orden de importancia:

* **que el enlace se haga en BORRADOR.** El core cuenta las cotizaciones de una
  ficha con `[('state','in',('draft','sent'))]`, asi que enlazar al confirmar
  deja `quotation_count` en cero para siempre. Es el fallo que casi se desplego:
  `test_cotizacion_en_borrador_queda_enlazada_al_lead` es el que lo caza.
* que un combo **reparta** su importe entre sus fichas en vez de copiar el total
  en todas (que es lo que hace hoy el agente, y por eso el embudo cuenta doble).
* que la ficha principal sea **la mas antigua** y no la de mayor importe: con el
  importe, la identidad de la ficha depende del precio.
* que una reserva web deje ficha **con identidad**, y que el lead pobre del core
  ya no aparezca.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestCrmLinkOrders(TransactionCase):

    NAT = '9990007788'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Lead = cls.env['crm.lead']
        cls.team_wa = cls.env.ref('visar_crm.crm_team_whatsapp')
        cls.team_web = cls.env.ref('sales_team.salesteam_website_sales')
        cls.s_nuevo = cls.env.ref('visar_crm.crm_stage_wa_nuevo')
        cls.s_valor = cls.env.ref('visar_crm.crm_stage_wa_valoracion')
        cls.s_cotiz = cls.env.ref('visar_crm.crm_stage_wa_cotizacion')
        cls.s_prog = cls.env.ref('visar_crm.crm_stage_wa_programado')

        # IVA explicito: los precios de Visar lo llevan incluido, y la gracia del
        # reparto es que use `price_total` y no `price_subtotal`.
        cls.iva = cls.env['account.tax'].create({
            'name': 'IVA prueba 16', 'amount': 16.0, 'amount_type': 'percent',
            'type_tax_use': 'sale'})

        cls.group_fum = cls.env['visar.service.group'].create({
            'name': 'Enlace Fumigacion', 'code': 'ENL_FUM'})
        cls.dim_int = cls.env['visar.service.dimension'].create({
            'name': 'Interior', 'code': 'ENL_INT', 'group_id': cls.group_fum.id})
        cls.prod_fum = cls.env['product.template'].create({
            'name': 'Enlace Fum Interior', 'visar_is_service': True,
            'list_price': 1000.0, 'taxes_id': [(6, 0, cls.iva.ids)],
            'visar_dimension_id': cls.dim_int.id}).product_variant_id

        cls.group_mav = cls.env['visar.service.group'].create({
            'name': 'Enlace Areas Verdes', 'code': 'ENL_MAV'})
        cls.dim_jar = cls.env['visar.service.dimension'].create({
            'name': 'Jardin', 'code': 'ENL_JAR', 'group_id': cls.group_mav.id})
        cls.prod_mav = cls.env['product.template'].create({
            'name': 'Enlace Jardin', 'visar_is_service': True,
            'list_price': 400.0, 'taxes_id': [(6, 0, cls.iva.ids)],
            'visar_dimension_id': cls.dim_jar.id}).product_variant_id

        cls.prod_valor = cls.env['product.template'].create({
            'name': 'Enlace Valoracion Tecnica', 'visar_is_service': True,
            'visar_is_valuation': True, 'list_price': 500.0}).product_variant_id

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _partner(self, phone=None):
        return self.env['res.partner'].create({
            'name': 'Cliente enlace', 'phone': phone or self.NAT})

    def _draft_order(self, products, phone=None, partner=None):
        """Orden EN BORRADOR, como la deja el wizard antes de que se pague."""
        partner = partner or self._partner(phone)
        return self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': p.id, 'product_uom_qty': 1})
                           for p in products],
        })

    def _lead(self, group, team=None, stage=None, nat=None, source='whatsapp'):
        return self.Lead.create({
            'name': 'Ficha enlace', 'type': 'opportunity',
            'team_id': (team or self.team_wa).id,
            'stage_id': (stage or self.s_nuevo).id,
            'visar_service_group_id': group.id if group else False,
            'visar_wa_phone_norm': nat or self.NAT,
            'visar_source': source,
        })

    # ------------------------------------------------------------------
    # 1. el enlace, y que sea EN BORRADOR
    # ------------------------------------------------------------------

    def test_cotizacion_en_borrador_queda_enlazada_al_lead(self):
        """EL test de este trabajo.

        `quotation_count` del core filtra por `state in ('draft','sent')`. Si el
        enlace se hiciera al confirmar, este contador seria 0 para siempre y la
        pestana que pidio el cliente seguiria vacia. Asertar `order_ids` NO
        habria cazado el fallo: `order_ids` se llena igual.
        """
        lead = self._lead(self.group_fum)
        order = self._draft_order([self.prod_fum])

        order._visar_crm_after_fill({}, canal='whatsapp')

        self.assertEqual(order.state, 'draft', "el enlace tiene que ser en borrador")
        self.assertEqual(order.opportunity_id, lead)
        self.assertEqual(
            lead.quotation_count, 1,
            "si esto es 0, la pestana «Cotizaciones» de la ficha sigue vacia "
            "aunque order_ids tenga la orden: el core filtra por estado")

    def test_el_agente_no_necesita_codigo_propio(self):
        """El camino del agente pasa por el mismo `_visar_fill_from_booking`, asi
        que enlaza con el mismo gancho y sin nada suyo."""
        lead = self._lead(self.group_fum)
        order = self._draft_order([self.prod_fum])
        order._visar_crm_after_fill({}, canal='whatsapp')
        self.assertEqual(order.opportunity_id, lead)

    def test_sin_canal_no_se_abre_ficha(self):
        """Una orden de backoffice no tiene canal, e inventarselo seria adivinar."""
        order = self._draft_order([self.prod_fum])
        order._visar_crm_after_fill({}, canal=None)
        self.assertFalse(order.opportunity_id)
        self.assertFalse(self.Lead.search([
            ('visar_wa_phone_norm', '=', self.NAT)]))

    # ------------------------------------------------------------------
    # 2. el combo
    # ------------------------------------------------------------------

    def test_combo_enlaza_al_lead_mas_antiguo(self):
        """`opportunity_id` es Many2one y el combo tiene una ficha por grupo.

        Gana la MAS ANTIGUA, no la de mayor importe: con el importe, la misma
        casa cambiaria de ficha principal si se mueve el tabulador.
        """
        primera = self._lead(self.group_mav)     # la barata, pero la primera
        segunda = self._lead(self.group_fum)     # la cara, pero posterior
        self.assertLess(primera.id, segunda.id)
        order = self._draft_order([self.prod_fum, self.prod_mav])

        order._visar_crm_after_fill({}, canal='whatsapp')

        self.assertEqual(
            order.opportunity_id, primera,
            "la principal es la ficha que el cliente abrio primero, no la del "
            "servicio mas caro")

    def test_combo_reparte_el_importe_por_grupo(self):
        """Cada ficha recibe SU trozo, con IVA. Ninguna recibe el total.

        Es lo contrario de lo que hace hoy el agente, que manda el total de la
        canasta en cada llamada: un combo de 1,800 enseña 1,800 en las dos
        fichas y el embudo cuenta 3,600.
        """
        lead_fum = self._lead(self.group_fum)
        lead_mav = self._lead(self.group_mav)
        order = self._draft_order([self.prod_fum, self.prod_mav])

        order._visar_crm_after_fill({}, canal='whatsapp')

        self.assertAlmostEqual(lead_fum.expected_revenue, 1160.0, 2)
        self.assertAlmostEqual(lead_mav.expected_revenue, 464.0, 2)
        self.assertAlmostEqual(
            lead_fum.expected_revenue + lead_mav.expected_revenue,
            order.amount_total, 2,
            "la suma de los trozos es el total de la orden: eso es lo que hace "
            "que el embudo deje de contar el combo dos veces")

    def test_el_trozo_lleva_IVA_y_no_el_subtotal(self):
        """Los precios de Visar llevan IVA incluido y es lo que el agente guarda
        en este campo; mezclar las dos bases lo volveria ilegible."""
        lead = self._lead(self.group_fum)
        order = self._draft_order([self.prod_fum])
        order._visar_crm_after_fill({}, canal='whatsapp')
        self.assertAlmostEqual(lead.expected_revenue, order.amount_total, 2)
        self.assertNotAlmostEqual(lead.expected_revenue, order.amount_untaxed, 2)

    def test_el_core_no_pisa_el_reparto_al_confirmar(self):
        """`sale_crm` sube `expected_revenue` a `amount_untaxed` de la orden
        COMPLETA al confirmar. Sobre un combo eso deshace el reparto."""
        lead_fum = self._lead(self.group_fum)
        lead_mav = self._lead(self.group_mav)
        order = self._draft_order([self.prod_fum, self.prod_mav])
        order._visar_crm_after_fill({}, canal='whatsapp')

        order.opportunity_id._update_revenues_from_so(order)

        self.assertAlmostEqual(lead_fum.expected_revenue, 1160.0, 2)
        self.assertAlmostEqual(lead_mav.expected_revenue, 464.0, 2)

    def test_un_lead_sin_grupo_lo_sigue_llevando_el_core(self):
        """Sin grupo no hay trozo que calcular, asi que ahi el nativo manda."""
        lead = self._lead(None)
        order = self._draft_order([self.prod_fum])
        lead.expected_revenue = 0.0
        lead._update_revenues_from_so(order)
        self.assertAlmostEqual(lead.expected_revenue, order.amount_untaxed, 2)

    # ------------------------------------------------------------------
    # 3. identidad, partner e idempotencia
    # ------------------------------------------------------------------

    def test_orden_web_sin_ficha_crea_una_con_identidad(self):
        """Lo que el lead pobre de `appointment_crm` no tenia: telefono
        normalizado y grupo, que es por lo que buscan los automatismos."""
        order = self._draft_order([self.prod_fum])

        order._visar_crm_after_fill({}, canal='web')

        lead = order.opportunity_id
        self.assertTrue(lead)
        self.assertEqual(lead.visar_wa_phone_norm, self.NAT)
        self.assertEqual(lead.visar_service_group_id, self.group_fum)
        self.assertEqual(lead.visar_source, 'web')
        self.assertEqual(lead.team_id, self.team_web)
        self.assertEqual(lead.stage_id, self.s_nuevo)

    def test_el_lead_adopta_el_partner_de_la_orden(self):
        """El lead del agente nace con solo telefono (no crea partner a
        proposito). Aqui el partner ya existe y es la misma persona, porque
        casamos por ese telefono. Mismo criterio que `_agent_open_lead`."""
        lead = self._lead(self.group_fum)
        self.assertFalse(lead.partner_id)
        order = self._draft_order([self.prod_fum])

        order._visar_crm_after_fill({}, canal='whatsapp')

        self.assertEqual(lead.partner_id, order.partner_id)

    def test_enlace_idempotente(self):
        """El carrito web se reutiliza, asi que el gancho corre varias veces."""
        lead = self._lead(self.group_fum)
        order = self._draft_order([self.prod_fum])

        order._visar_crm_after_fill({}, canal='whatsapp')
        notas = len(lead.message_ids)
        order._visar_crm_after_fill({}, canal='whatsapp')
        order._visar_crm_after_fill({}, canal='whatsapp')

        self.assertEqual(order.opportunity_id, lead)
        self.assertEqual(len(order.order_line.mapped('order_id')), 1)
        self.assertEqual(
            len(lead.message_ids), notas,
            "sin idempotencia el expediente del cliente acumula la misma nota "
            "en cada pasada del carrito")

    def test_carrito_reutilizado_reapunta_a_la_ficha_nueva(self):
        """Last-write-wins: si la misma sesion reserva para otro grupo, el
        enlace condicional dejaria pegada la oportunidad anterior."""
        lead_fum = self._lead(self.group_fum)
        order = self._draft_order([self.prod_fum])
        order._visar_crm_after_fill({}, canal='whatsapp')
        self.assertEqual(order.opportunity_id, lead_fum)

        # El wizard rehace el carrito: ahora es de areas verdes.
        order.order_line.unlink()
        self.env['sale.order.line'].create({
            'order_id': order.id, 'product_id': self.prod_mav.id,
            'product_uom_qty': 1})
        lead_mav = self._lead(self.group_mav)

        order._visar_crm_after_fill({}, canal='whatsapp')

        self.assertEqual(order.opportunity_id, lead_mav)

    def test_no_resucita_una_ficha_archivada(self):
        """`search` inyecta `active = True`. Revivir en silencio un lead que
        alguien dio por perdido es peor que no enlazarlo."""
        lead = self._lead(self.group_fum)
        lead.action_archive()
        order = self._draft_order([self.prod_fum])

        order._visar_crm_after_fill({}, canal='whatsapp')

        self.assertFalse(lead.active)
        self.assertNotEqual(order.opportunity_id, lead)

    # ------------------------------------------------------------------
    # 4. dos canales, dos fichas (decision del 2-oct-2026)
    # ------------------------------------------------------------------

    def test_dos_canales_dos_fichas_y_un_solo_enlace(self):
        """El cliente chatea y luego compra en la web.

        Las DOS fichas avanzan —si no, el tablero de WhatsApp se queda con una
        ficha muerta en 'Nuevo' de alguien que si compro— pero la cotizacion se
        enlaza solo a la del canal que vendio.
        """
        wa = self._lead(self.group_fum, team=self.team_wa)
        order = self._draft_order([self.prod_fum])

        order._visar_crm_after_fill({}, canal='web')
        web = order.opportunity_id

        self.assertNotEqual(web, wa, "la web NO reutiliza la ficha del agente")
        self.assertEqual(web.team_id, self.team_web)
        self.assertEqual(wa.stage_id, self.s_nuevo)

        order.write({'state': 'sale'})

        self.assertEqual(web.stage_id, self.s_prog)
        self.assertEqual(wa.stage_id, self.s_prog,
                         "la ficha del otro canal tambien avanza")
        self.assertEqual(order.opportunity_id, web,
                         "pero la cotizacion es del canal que vendio")

    # ------------------------------------------------------------------
    # 5. las dos etapas que estaban muertas
    # ------------------------------------------------------------------

    def test_valoracion_avanza_por_su_propia_ficha(self):
        """El producto de valoracion NO lleva grupo, y es por diseno. Con el
        enlace en borrador no hace falta emparejar: la orden ya sabe su ficha."""
        order = self._draft_order([self.prod_valor])
        order._visar_crm_after_fill({}, canal='web')
        ficha = order.opportunity_id
        self.assertTrue(ficha, "una valoracion tambien deja ficha, sin grupo")
        self.assertFalse(ficha.visar_service_group_id)

        order.write({'state': 'sale'})

        self.assertEqual(ficha.stage_id, self.s_valor)

    def test_valoracion_sin_enlace_con_una_sola_ficha_abierta(self):
        lead = self._lead(None)
        order = self._draft_order([self.prod_valor])
        order.write({'state': 'sale'})
        self.assertEqual(lead.stage_id, self.s_valor)

    def test_valoracion_con_dos_fichas_abiertas_no_toca_nada(self):
        """Mover la equivocada es peor que no mover ninguna."""
        a = self._lead(self.group_fum)
        b = self._lead(self.group_mav)
        order = self._draft_order([self.prod_valor])
        order.write({'state': 'sale'})
        self.assertEqual(a.stage_id, self.s_nuevo)
        self.assertEqual(b.stage_id, self.s_nuevo)

    def test_un_carrito_web_enviado_NO_avanza_cotizacion(self):
        """El gemelo negativo. «Cotizacion enviada» es la cotizacion formal que
        arma una persona tras la visita, no cualquier orden que llegue a 'sent'."""
        lead = self._lead(self.group_fum)
        order = self._draft_order([self.prod_fum])
        order._visar_crm_after_fill({}, canal='whatsapp')

        order.write({'state': 'sent'})

        self.assertEqual(lead.stage_id, self.s_nuevo)

    def test_una_cotizacion_formal_enviada_SI_avanza(self):
        """La que pide un tecnico desde su hoja y le pone precio una persona.

        Quien responde `_visar_is_formal_quote` es `visar_field_app`, que es un
        modulo HERMANO: `visar_crm` no puede importarlo. Asi que la prueba monta
        el caso real —una orden con `visar_quote_origin_task_id`— y se salta si
        ese modulo no esta instalado, que es la unica forma honesta de probar un
        contrato que cruza dos modulos sin inventarse una dependencia.
        """
        SO = self.env['sale.order']
        if 'visar_quote_origin_task_id' not in SO._fields:
            self.skipTest("visar_field_app no esta instalado: no hay cotizacion "
                          "formal que probar")
        lead = self._lead(self.group_fum)
        order = self._draft_order([self.prod_fum])
        order._visar_crm_after_fill({}, canal='whatsapp')
        tarea = self.env['project.task'].create({'name': 'Visita de prueba'})
        order.sudo().write({'visar_quote_origin_task_id': tarea.id})
        self.assertTrue(order._visar_is_formal_quote())

        order.write({'state': 'sent'})

        self.assertEqual(lead.stage_id, self.s_cotiz)
