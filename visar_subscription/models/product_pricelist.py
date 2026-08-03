from odoo import _, api, fields, models


class ProductPricelist(models.Model):
    _inherit = 'product.pricelist'

    visar_zone_id = fields.Many2one(
        'visar.zone', string="Zona Visar", index=True,
        help="Zona a la que pertenece esta lista de precios de póliza. Junto con el "
             "plan identifica la lista (zona × plan) que se aplica al carrito.")
    visar_plan_id = fields.Many2one(
        'sale.subscription.plan', string="Plan de póliza", index=True,
        help="Plan de suscripción de esta lista. Sus reglas NO llevan precio: derivan "
             "del precio de la lista de la zona. El precio vive solo en la lista de zona.")

    # ------------------------------------------------------------------
    # Listas (zona × plan): 2 reglas globales que derivan de la lista de zona
    # ------------------------------------------------------------------
    @api.model
    def _visar_legacy_poliza_discounts(self):
        """{(plan_id, zone_pricelist_id): descuento} leído de las listas heredadas.

        Las reglas heredadas son una por variante, pero todas dicen lo mismo: "toma el
        precio de la zona y réstale N%". De ahí se lee N, en vez de re-teclear los
        descuentos o hornearlos en el código.
        """
        items = self.env['product.pricelist.item'].sudo().search([
            ('plan_id', '!=', False),
            ('base', '=', 'pricelist'),
            ('base_pricelist_id', '!=', False),
            # Excluye las listas nuevas: ellas mismas cumplen el dominio.
            ('pricelist_id.visar_plan_id', '=', False),
        ])
        discounts = {}
        for item in items:
            discounts.setdefault(
                (item.plan_id.id, item.base_pricelist_id.id), item.price_discount)
        return discounts

    @api.model
    def _visar_sync_poliza_pricelists(self):
        """Crea/actualiza una lista por (zona activa × plan de póliza). Idempotente.

        Cada lista lleva exactamente dos reglas globales sobre la lista de la zona:
          1. sin plan  → precio de zona tal cual (add-ons, extras, roedores…)
          2. con plan  → precio de zona menos el descuento del plan
        Así el carrito de una póliza cotiza todo desde su propia lista sin duplicar
        un solo precio: la lista de zona sigue siendo la única fuente de verdad.
        """
        discounts = self._visar_legacy_poliza_discounts()
        if not discounts:
            return self.browse()

        zones = self.env['visar.zone'].sudo().search([('pricelist_id', '!=', False)])
        zone_by_pricelist = {z.pricelist_id.id: z for z in zones}
        Plan = self.env['sale.subscription.plan'].sudo()
        result = self.browse()

        for (plan_id, zone_pricelist_id), discount in discounts.items():
            zone = zone_by_pricelist.get(zone_pricelist_id)
            plan = Plan.browse(plan_id).exists()
            if not zone or not plan:
                continue
            result |= self._visar_upsert_poliza_pricelist(zone, plan, discount)
        return result

    @api.model
    def _visar_upsert_poliza_pricelist(self, zone, plan, discount):
        """Una lista (zona × plan) con sus dos reglas. Reescribe las reglas si cambian."""
        zone_pricelist = zone.pricelist_id
        pricelist = self.sudo().search([
            ('visar_zone_id', '=', zone.id),
            ('visar_plan_id', '=', plan.id),
        ], limit=1)
        vals = {
            'name': _("%(zone)s — %(plan)s", zone=zone.name, plan=plan.name),
            'visar_zone_id': zone.id,
            'visar_plan_id': plan.id,
            'currency_id': zone_pricelist.currency_id.id,
            'company_id': zone_pricelist.company_id.id,
            'website_id': zone_pricelist.website_id.id,
            # Se asigna por código al armar el carrito; no debe ofrecerse al cliente.
            'selectable': False,
        }
        if pricelist:
            pricelist.sudo().write(vals)
        else:
            pricelist = self.sudo().create(vals)

        pricelist.item_ids.sudo().unlink()
        base = {
            'pricelist_id': pricelist.id,
            'applied_on': '3_global',
            'compute_price': 'formula',
            'base': 'pricelist',
            'base_pricelist_id': zone_pricelist.id,
        }
        self.env['product.pricelist.item'].sudo().create([
            # Sin plan: todo lo no recurrente cotiza exactamente igual que en el
            # carrito de compra única (add-ons, extras, roedores, valoración).
            dict(base, plan_id=False, price_discount=0.0),
            # Con plan: el servicio recurrente lleva el descuento de la póliza.
            dict(base, plan_id=plan.id, price_discount=discount),
        ])
        return pricelist

    @api.model
    def _visar_retire_legacy_poliza_pricelists(self):
        """Marca las listas heredadas como no seleccionables y las renombra.

        Se dejan ACTIVAS a propósito: hay órdenes vivas que las referencian y sus
        renovaciones siguen cotizando bien desde ellas.
        """
        # OJO: no sirve mirar `pricelist.item_ids` — sale_subscription lo filtra a las
        # reglas SIN plan, así que en estas listas (que son solo reglas de plan) sale
        # vacío. Hay que consultar product.pricelist.item directamente.
        Item = self.env['product.pricelist.item'].sudo()
        items = Item.search([('plan_id', '!=', False),
                             ('pricelist_id.visar_plan_id', '=', False)])
        legacy = self.browse()
        for pricelist, plan in {i.pricelist_id: i.plan_id for i in items}.items():
            name = pricelist.name or ''
            legacy |= pricelist
            if not name.startswith('(heredada)'):
                pricelist.sudo().write({
                    'name': _("(heredada) %(name)s — %(plan)s",
                              name=name, plan=plan.name or '—'),
                    'selectable': False,
                })
        return legacy
