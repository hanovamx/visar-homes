# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AppointmentType(models.Model):
    _inherit = 'appointment.type'

    visar_product_tmpl_ids = fields.One2many(
        'product.template', 'visar_appointment_type_id', string="Productos Visar")
    visar_is_master = fields.Boolean(
        "Tipo de cita maestro Visar (wizard multi-servicio)",
        help="Tipo interno usado tras el wizard para horario y pago; no aparece en /appointment.")
    visar_flow = fields.Selection(
        selection=[
            ('valuation', 'Valoración técnica (solo zona)'),
            ('wizard', 'Wizard multi-servicio'),
        ],
        string="Flujo Visar (entrada web)",
        help="Marca los dos tipos visibles en /appointment. Vacío = tipo interno/legacy.")

    def _visar_service_template(self):
        """ Devuelve el product.template (servicio Visar) ligado a este tipo de cita. """
        self.ensure_one()
        return self.visar_product_tmpl_ids.filtered('visar_is_service')[:1]

    def _visar_eligible_resources(self, zone):
        """ Recursos (técnicos) elegibles para este servicio + zona."""
        self.ensure_one()
        if not zone:
            return self.env['appointment.resource']
        return self.resource_ids.filtered(
            lambda r: zone in r.visar_zone_ids and self in r.visar_service_ids)

    def _visar_resolve_tier(self, m2):
        """ Encuentra el tramo (visar.service.tier) cuyo rango contiene m2."""
        self.ensure_one()
        template = self._visar_service_template()
        if not template:
            return self.env['visar.service.tier']
        return template.visar_tier_ids.filtered(
            lambda t: t.m2_min <= m2 <= t.m2_max)[:1]

    # Retorna el tipo maestro del wizard por parámetro de sistema o búsqueda por flag.
    @api.model
    def _visar_get_master_appointment_type(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'visar.master_appointment_type_id')
        if param and param.isdigit():
            apt = self.browse(int(param)).exists()
            if apt:
                return apt
        return self.search([('visar_is_master', '=', True)], limit=1)

    # Retorna el tipo de valoración por parámetro de sistema o búsqueda por flujo.
    @api.model
    def _visar_get_valuation_appointment_type(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'visar.valuation_entry_appointment_type_id')
        if param and param.isdigit():
            apt = self.browse(int(param)).exists()
            if apt:
                return apt
        return self.search([('visar_flow', '=', 'valuation')], limit=1)

    @api.model
    def _visar_question_zona(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_zona', raise_if_not_found=False)

    @api.model
    def _visar_question_metros(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_metros', raise_if_not_found=False)

    @api.model
    def _visar_question_plaga(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_plaga', raise_if_not_found=False)

    @api.model
    def _visar_question_roedores(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_roedores', raise_if_not_found=False)

    @api.model
    def _visar_question_tipo_plaga(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_tipo_plaga', raise_if_not_found=False)

    # Etiquetas legibles para las respuestas de calificación capturadas en el wizard.
    _VISAR_PLAGA_LABELS = {'preventivo': 'Preventivo', 'plaga': 'Plaga activa'}
    _VISAR_ROEDORES_LABELS = {'si': 'Sí', 'no': 'No'}
    _VISAR_TIPO_PLAGA_LABELS = {
        'cucarachas': 'Cucarachas',
        'hormigas': 'Hormigas',
        'aranas': 'Arañas',
    }

    @api.model
    def _visar_item_answer_label(self, item):
        """Etiqueta legible dimensión + tramo para respuestas nativas."""
        dimension = self.env['visar.service.dimension'].browse(
            item.get('dimension_id')).exists()
        tier_name = item.get('tier_name')
        if dimension and tier_name:
            return '%s: %s' % (dimension._visar_wizard_label(), tier_name)
        return self._visar_item_label(item)

    @api.model
    def _visar_build_native_answer_inputs(self, appointment_type, zone, items=None,
                                          partner_id=False, selections=None):
        """Construye appointment.answer.input para zona, m² y calificación capturados fuera del formulario."""
        if not appointment_type or not zone:
            return []
        inputs = []
        base = {'appointment_type_id': appointment_type.id}
        if partner_id:
            base['partner_id'] = partner_id

        zona_q = self._visar_question_zona()
        if zona_q:
            inputs.append({
                **base,
                'question_id': zona_q.id,
                'value_text_box': zone.name,
            })

        metros_q = self._visar_question_metros()
        if items and metros_q:
            labels = [
                label for label in (
                    self._visar_item_answer_label(item) for item in items
                ) if label
            ]
            if labels:
                inputs.append({
                    **base,
                    'question_id': metros_q.id,
                    'value_text_box': ', '.join(labels),
                })

        inputs.extend(self._visar_build_calification_answer_inputs(base, selections or {}))
        return inputs

    @api.model
    def _visar_build_calification_answer_inputs(self, base, selections):
        """Respuestas P1/P2/P3 (plaga, roedores, tipo de plaga) para el bloque Questions & Answers."""
        inputs = []

        plaga_q = self._visar_question_plaga()
        plaga = selections.get('plaga')
        if plaga_q and plaga:
            inputs.append({
                **base,
                'question_id': plaga_q.id,
                'value_text_box': self._VISAR_PLAGA_LABELS.get(plaga, plaga),
            })

        roedores_q = self._visar_question_roedores()
        roedores = selections.get('roedores')
        if roedores_q and roedores:
            inputs.append({
                **base,
                'question_id': roedores_q.id,
                'value_text_box': self._VISAR_ROEDORES_LABELS.get(roedores, roedores),
            })

        tipo_q = self._visar_question_tipo_plaga()
        tipos = selections.get('tipo_plaga') or []
        if isinstance(tipos, str):
            tipos = [t for t in tipos.split(',') if t]
        if tipo_q and tipos:
            labels = [self._VISAR_TIPO_PLAGA_LABELS.get(t, t) for t in tipos]
            inputs.append({
                **base,
                'question_id': tipo_q.id,
                'value_text_box': ', '.join(labels),
            })
        return inputs

    @api.model
    def _visar_unlink_questions_from_entry_types(self):
        """Quita preguntas Visar de los tipos de cita para que no aparezcan en el formulario web."""
        questions = (
            self._visar_question_zona()
            | self._visar_question_metros()
            | self._visar_question_plaga()
            | self._visar_question_roedores()
            | self._visar_question_tipo_plaga()
        )
        question_ids = questions.ids
        if not question_ids:
            return
        for apt_type in (
            self._visar_get_master_appointment_type()
            | self._visar_get_valuation_appointment_type()
        ):
            to_remove = apt_type.question_ids.filtered(lambda q: q.id in question_ids)
            if to_remove:
                apt_type.sudo().write({'question_ids': [(3, qid) for qid in to_remove.ids]})
        questions.sudo().write({'is_reusable': False})

    @api.model
    def _visar_selection_dimension_ids(self, selections):
        """Dimensiones activas según selecciones del wizard (BD)."""
        Dimension = self.env['visar.service.dimension'].sudo()
        dimension_ids = selections.get('dimension_ids') or []
        if isinstance(dimension_ids, str):
            dimension_ids = [int(x) for x in dimension_ids.split(',') if x.isdigit()]
        return Dimension.browse(dimension_ids).exists()

    # Devuelve la etiqueta legible de un item (dimensión, tier o producto) para mensajes de error.
    @api.model
    def _visar_item_label(self, item):
        dimension = self.env['visar.service.dimension'].browse(
            item.get('dimension_id')).exists()
        if dimension:
            return dimension._visar_wizard_label()
        if item.get('tier_name'):
            return item['tier_name']
        tmpl = self.env['product.template'].browse(item.get('product_tmpl_id')).exists()
        return tmpl.display_name if tmpl else ''

    @api.model
    def _visar_resolve_wizard_items(self, selections):
        """Resuelve tier por cada dimensión elegida en el wizard.

        La variante se resuelve después según la zona del cliente (no aquí).
        """
        Tier = self.env['visar.service.tier']
        ProductTemplate = self.env['product.template']
        items = []
        for dimension in self._visar_selection_dimension_ids(selections):
            tier_key = dimension._visar_tier_field_name()
            tier_id = selections.get(tier_key) or (selections.get('tiers') or {}).get(str(dimension.id))
            if not tier_id:
                continue
            tier = Tier.browse(int(tier_id)).exists()
            if not tier or not tier.product_tmpl_id:
                continue
            template = tier.product_tmpl_id or ProductTemplate._visar_get_service_template_for_dimension(
                dimension)
            apt_type = template.visar_appointment_type_id if template else False
            items.append({
                'dimension_id': dimension.id,
                'tier_id': tier.id,
                'tier_name': tier.name or tier.display_name,
                'variant_id': None,  # ← Será resuelto por zona en _visar_build_sale_lines
                'product_tmpl_id': template.id if template else False,
                'appointment_type_id': apt_type.id if apt_type else False,
                'is_valuation': tier.is_valuation,
                'is_free': tier.is_free,
            })
        return items

    @api.model
    def _visar_service_resource_pools(self, zone, items):
        """Por cada dimensión, pool de recursos elegibles. Retorna (pools, missing_labels)."""
        pools = {}
        missing = []
        for item in items:
            pool_key = str(item['dimension_id'])
            apt_type = self.browse(item['appointment_type_id']).exists()
            if not apt_type:
                missing.append(self._visar_item_label(item))
                continue
            eligible = apt_type._visar_eligible_resources(zone)
            if not eligible:
                missing.append(self._visar_item_label(item))
            pools[pool_key] = eligible
        return pools, missing

    # Cuenta las citas existentes del recurso que se solapan con el rango de tiempo dado.
    @api.model
    def _visar_resource_load(self, resource, start_utc, stop_utc):
        BookingLine = self.env['appointment.booking.line'].sudo()
        domain = [
            ('appointment_resource_id', '=', resource.id),
            ('event_start', '<', stop_utc),
            ('event_stop', '>', start_utc),
        ]
        return BookingLine.search_count(domain)

    # True si el recurso pertenece al tipo y tiene capacidad libre en el slot dado.
    @api.model
    def _visar_resource_free_at(self, apt_type, resource, start_utc, stop_utc, asked_capacity=1):
        if resource not in apt_type.resource_ids:
            return False
        remaining = apt_type._get_resources_remaining_capacity(
            resource, start_utc, stop_utc, with_linked_resources=False,
        )
        return remaining.get('total_remaining_capacity', 0) >= asked_capacity

    @api.model
    def _visar_pool_intersection(self, service_pools):
        """Recursos que pueden cubrir todos los servicios del wizard (todos los pools)."""
        pools = [pool for pool in service_pools.values() if pool]
        if not pools:
            return self.env['appointment.resource']
        common = pools[0]
        for pool in pools[1:]:
            common &= pool
        return common

    @api.model
    def _visar_pools_from_booking(self, booking):
        """Pools vivos desde zone + items (no IDs congelados en sesión)."""
        booking = booking or {}
        zone = self.env['visar.zone'].browse(booking.get('zone_id')).exists()
        if not zone:
            return {}
        items = booking.get('items') or []
        if not items and booking.get('selections'):
            items = self._visar_resolve_wizard_items(booking.get('selections'))
        if not items:
            return {}
        pools, _missing = self._visar_service_resource_pools(zone, items)
        return pools

    @api.model
    def _visar_filter_resource_ids_for_pools(self, service_pools):
        """IDs para filter_resource_ids: intersección (un solo técnico) o unión como fallback."""
        common = self._visar_pool_intersection(service_pools)
        if common:
            return common.ids
        return list({rid for pool in service_pools.values() for rid in pool.ids})

    @api.model
    def _visar_free_candidates(self, master_type, pool, start_utc, stop_utc, asked_capacity=1):
        return pool.filtered(
            lambda r: self._visar_resource_free_at(
                master_type, r, start_utc, stop_utc, asked_capacity)
        )

    # Prefiere un solo técnico que cubra todos los servicios; si no, uno libre por pool.
    @api.model
    def _visar_pick_resources_for_slot(self, master_type, service_pools, start_utc, stop_utc, asked_capacity=1):
        if not service_pools or any(not pool for pool in service_pools.values()):
            return self.env['appointment.resource']

        common = self._visar_pool_intersection(service_pools)
        free_common = self._visar_free_candidates(
            master_type, common, start_utc, stop_utc, asked_capacity)
        if free_common:
            best = min(
                free_common,
                key=lambda r: self._visar_resource_load(r, start_utc, stop_utc),
            )
            return best

        picked = self.env['appointment.resource']
        for pool in service_pools.values():
            candidates = self._visar_free_candidates(
                master_type, pool, start_utc, stop_utc, asked_capacity)
            if not candidates:
                return self.env['appointment.resource']
            best = min(candidates, key=lambda r: self._visar_resource_load(r, start_utc, stop_utc))
            picked |= best
        return picked

    # Filtra la estructura de slots del calendario dejando solo los con técnicos simultáneos disponibles.
    @api.model
    def _visar_filter_slots_multi_service(self, master_type, months, service_pools, timezone, asked_capacity=1):
        import pytz
        from dateutil.relativedelta import relativedelta
        from werkzeug.urls import url_decode, url_encode

        tz_info = pytz.timezone(timezone or master_type.appointment_tz)
        filtered_months = []
        for month in months:
            month_has_avail = False
            new_weeks = []
            for week in month.get('weeks', []):
                new_week = []
                for day in week:
                    if not isinstance(day, dict):
                        new_week.append(day)
                        continue
                    day_copy = dict(day)
                    new_slots = []
                    for slot in day.get('slots', []):
                        dt_str = slot.get('datetime')
                        if not dt_str:
                            continue
                        duration = float(slot.get('slot_duration') or master_type.appointment_duration)
                        start_local = fields.Datetime.from_string(dt_str)
                        start_utc = tz_info.localize(start_local).astimezone(pytz.utc).replace(tzinfo=None)
                        stop_utc = start_utc + relativedelta(hours=duration)
                        resources = self._visar_pick_resources_for_slot(
                            master_type, service_pools, start_utc, stop_utc, asked_capacity)
                        if not resources:
                            continue
                        slot_copy = dict(slot)
                        slot_copy['available_resources'] = [{
                            'id': resource.id,
                            'name': resource.name,
                            'capacity': resource.capacity,
                        } for resource in resources]
                        url_parameters = dict(url_decode(slot.get('url_parameters') or ''))
                        url_parameters['available_resource_ids'] = str(resources.ids)
                        slot_copy['url_parameters'] = url_encode(url_parameters)
                        new_slots.append(slot_copy)
                    day_copy['slots'] = new_slots
                    if new_slots:
                        month_has_avail = True
                    new_week.append(day_copy)
                new_weeks.append(new_week)
            filtered_months.append({
                **month,
                'weeks': new_weeks,
                'has_availabilities': month_has_avail,
            })
        return filtered_months

    @api.model
    def _visar_list_unit_price(self, product, zone):
        """Precio de lista unitario desde la pricelist de la zona.

        La variante ya fue resuelta correctamente por _visar_get_variant_for_zone(),
        así que la pricelist encuentra el precio correcto para la zona del cliente.
        """
        if not product:
            return 0.0
        website = self.env['website'].get_current_website(fallback=False)
        pricelist = zone.pricelist_id if zone else self.env['product.pricelist']
        if not pricelist and website:
            pricelist = website._get_and_cache_current_pricelist()
        if pricelist:
            return pricelist._get_product_price(product, 1.0)
        return product.lst_price

    @api.model
    def _visar_cart_line_net_unit_price(self, line_vals, zone):
        """Precio unitario neto de una línea antes de añadirla al carrito."""
        product = self.env['product.product'].browse(line_vals.get('product_id')).exists()
        if not product:
            return 0.0
        unit = self._visar_list_unit_price(product, zone)
        discount = line_vals.get('discount') or 0.0
        return unit * (1.0 - discount / 100.0)

    @api.model
    def _visar_skip_cart_line(self, line_vals, zone):
        """True si la línea no debe ir al carrito (Odoo bloquea precio 0)."""
        if line_vals.get('is_free'):
            return True
        return self._visar_cart_line_net_unit_price(line_vals, zone) <= 0

    @api.model
    def _visar_quote_line_label(self, line_vals, product):
        """Etiqueta legible para sidebar/checkout (dimensión — tramo, add-on ×N)."""
        if line_vals.get('is_addon'):
            qty = int(line_vals.get('quantity') or 1)
            name = product.display_name
            return '%s ×%s' % (name, qty) if qty > 1 else name
        tier_name = line_vals.get('tier_name')
        if tier_name:
            dimension = self.env['visar.service.dimension'].browse(
                line_vals.get('dimension_id')).exists()
            if dimension:
                return '%s — %s' % (dimension._visar_wizard_label(), tier_name)
            return tier_name
        return product.display_name

    @api.model
    def _visar_combo_discount_for_item(self, item, dimension_ids, combo_rules):
        """Descuento % para una línea según reglas de combo activas."""
        dimension_id = item.get('dimension_id')
        tier = self.env['visar.service.tier'].browse(item.get('tier_id')).exists()
        for rule in combo_rules:
            if not rule._visar_applies_to_items(dimension_ids):
                continue
            if dimension_id in rule.discount_dimension_ids.ids:
                return rule._visar_discount_percent()
            if tier and tier.combo_discount_eligible:
                return rule._visar_discount_percent()
        return 0.0

    # Construye las líneas de venta con variante por zona, descuento combo e incluidos al 100%.
    @api.model
    def _visar_build_sale_lines(self, items, zone, include_roedores=False):
        if any(item.get('is_valuation') for item in items):
            valuation_tmpl = self.env['product.template']._visar_get_valuation_template()
            variant = valuation_tmpl.product_variant_id if valuation_tmpl else False
            if variant:
                return [{'product_id': variant.id, 'discount': 0.0, 'dimension_id': False}]
            return []

        ComboRule = self.env['visar.combo.rule'].sudo()
        combo_rules = ComboRule.search([('active', '=', True)], order='sequence')
        dimension_ids = [item['dimension_id'] for item in items if item.get('dimension_id')]

        lines = []
        for item in items:
            tier = self.env['visar.service.tier'].browse(item.get('tier_id')).exists()
            if not tier:
                continue
            # Resuelve la variante correcta según la zona usando el tabulador
            variant = tier._visar_get_variant_for_zone(zone)
            if not variant:
                continue
            is_free = item.get('is_free') or (tier and tier.is_free)
            unit_price = self._visar_list_unit_price(variant, zone)
            if is_free or unit_price <= 0:
                lines.append({
                    'product_id': variant.id,
                    'discount': 100.0 if unit_price > 0 else 0.0,
                    'dimension_id': item.get('dimension_id'),
                    'tier_name': item.get('tier_name'),
                    'is_free': True,
                })
                continue
            discount = self._visar_combo_discount_for_item(item, dimension_ids, combo_rules)

            lines.append({
                'product_id': variant.id,
                'discount': discount,
                'dimension_id': item.get('dimension_id'),
                'tier_name': item.get('tier_name'),
            })

        ProductTemplate = self.env['product.template']
        roedores_tmpl = ProductTemplate._visar_get_roedores_template() if include_roedores \
            else ProductTemplate
        if roedores_tmpl and roedores_tmpl.product_variant_id:
            roedores_variant = ProductTemplate._visar_variant_for_zone(
                roedores_tmpl.product_variant_id, zone)
            # Producto disparador a $0: no genera línea; solo sus add-ons obligatorios.
            if self._visar_list_unit_price(roedores_variant, zone) > 0:
                lines.append({
                    'product_id': roedores_variant.id,
                    'discount': 0.0,
                    'dimension_id': False,
                    'tier_name': roedores_tmpl.name,
                    'is_roedores': True,
                })

        addon_qty = {}
        for item in items:
            if item.get('is_free') or item.get('is_valuation'):
                continue
            tmpl_id = item.get('product_tmpl_id')
            if not tmpl_id:
                continue
            tmpl = ProductTemplate.browse(tmpl_id).exists()
            if not tmpl:
                continue
            for product_id, qty in tmpl._visar_get_mandatory_addon_map(zone).items():
                addon_qty[product_id] = addon_qty.get(product_id, 0) + qty
        if roedores_tmpl:
            for product_id, qty in roedores_tmpl._visar_get_mandatory_addon_map(zone).items():
                addon_qty[product_id] = addon_qty.get(product_id, 0) + qty

        Product = self.env['product.product']
        for product_id, addon_quantity in addon_qty.items():
            product = Product.browse(product_id).exists()
            if not product:
                continue
            lines.append({
                'product_id': product.id,
                'discount': 0.0,
                'quantity': addon_quantity,
                'is_addon': True,
            })
        return lines

    # Calcula los precios estimados de la reserva respetando pricelist de zona y descuentos combo.
    @api.model
    def _visar_quote_booking(self, items, zone, quantity=1, include_roedores=False):
        sale_lines = self._visar_build_sale_lines(items, zone, include_roedores=include_roedores)
        if not sale_lines:
            return False

        website = self.env['website'].get_current_website(fallback=False)
        pricelist = zone.pricelist_id if zone else self.env['product.pricelist']
        if not pricelist and website:
            pricelist = website._get_and_cache_current_pricelist()
        currency = (pricelist.currency_id if pricelist else False) or (
            website.currency_id if website else self.env.company.currency_id)

        quote_lines = []
        total = 0.0
        qty = max(int(quantity or 1), 1)
        Product = self.env['product.product'].sudo()
        for line_vals in sale_lines:
            product = Product.browse(line_vals['product_id']).exists()
            if not product:
                continue
            list_unit_price = self._visar_list_unit_price(product, zone)
            discount = line_vals.get('discount') or 0.0
            is_free = line_vals.get('is_free')
            line_qty = line_vals.get('quantity', qty)
            if is_free:
                unit_price = 0.0
                line_total = 0.0
            else:
                unit_price = list_unit_price * (1.0 - discount / 100.0) if discount else list_unit_price
                line_total = unit_price * line_qty
            quote_lines.append({
                'name': self._visar_quote_line_label(line_vals, product),
                'unit_price': unit_price,
                'list_price': list_unit_price if (is_free or discount) else False,
                'price': line_total,
                'discount': discount,
                'is_free': bool(is_free),
                'is_addon': bool(line_vals.get('is_addon')),
                'quantity': line_qty,
                'has_discounted_price': bool(is_free or discount),
            })
            total += line_total

        if not quote_lines:
            return False
        return {
            'lines': quote_lines,
            'total': total,
            'currency_id': currency.id,
            'zone_name': zone.name if zone else False,
        }

    # Crea una copia serializable de los items del wizard para guardar en el evento de calendario.
    @api.model
    def _visar_items_snapshot(self, items):
        snapshot = []
        for item in items:
            snapshot.append({
                'dimension_id': item.get('dimension_id'),
                'tier_id': item.get('tier_id'),
                'tier_name': item.get('tier_name'),
                'variant_id': item.get('variant_id'),
                'product_tmpl_id': item.get('product_tmpl_id'),
                'appointment_type_id': item.get('appointment_type_id'),
                'is_valuation': item.get('is_valuation'),
                'is_free': item.get('is_free'),
            })
        return snapshot
