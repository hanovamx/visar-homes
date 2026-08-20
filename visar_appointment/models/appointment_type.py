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
        return self.env['appointment.resource'].search([
            ('visar_zone_ids', 'in', zone.id),
            ('visar_service_ids', 'in', self.id),
        ])

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
    def _visar_question_address(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_direccion', raise_if_not_found=False)

    @api.model
    def _visar_format_delivery_address(self, address):
        """Dirección de entrega en una línea legible para el técnico."""
        if not address:
            return ''
        street = (address.get('street') or '').strip()
        ext_num = (address.get('ext_num') or '').strip()
        int_num = (address.get('int_num') or '').strip()
        neighborhood = (address.get('neighborhood') or '').strip()
        zip_code = (address.get('zip') or '').strip()
        city = (address.get('city') or '').strip()
        state = (address.get('state') or '').strip()
        line = street
        if ext_num:
            line = ('%s No. %s' % (line, ext_num)).strip()
        if int_num:
            line = ('%s Int. %s' % (line, int_num)).strip()
        parts = [p for p in [
            line,
            neighborhood,
            'C.P. %s' % zip_code if zip_code else '',
            city,
            state,
        ] if p]
        return ', '.join(parts)

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

    @api.model
    def _visar_question_motivo(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_motivo', raise_if_not_found=False)

    @api.model
    def _visar_question_servicio_plaga(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_servicio_plaga', raise_if_not_found=False)

    @api.model
    def _visar_question_motivo_valoracion(self):
        return self.env.ref(
            'visar_appointment.appointment_question_visar_motivo_valoracion', raise_if_not_found=False)

    # Etiquetas legibles para las respuestas de calificación capturadas en el wizard.
    _VISAR_PLAGA_LABELS = {'preventivo': 'Preventivo', 'plaga': 'Plaga activa'}
    _VISAR_ROEDORES_LABELS = {'si': 'Sí', 'no': 'No'}
    _VISAR_TIPO_PLAGA_LABELS = {
        'cucarachas': 'Cucarachas',
        'hormigas': 'Hormigas',
        'aranas': 'Arañas',
    }
    _VISAR_MOTIVO_LABELS = {
        'preventivo': 'Preventivo',
        'correctivo': 'Correctivo (plaga activa)',
    }
    _VISAR_SERVICIO_PLAGA_LABELS = {
        'rastreros': 'Rastreros',
        'voladores': 'Voladores',
        'roedores': 'Roedores',
    }
    _VISAR_MOTIVO_VALORACION_LABELS = {
        'termitas': 'Termitas',
        'chinches': 'Chinches de cama',
        'plaga_no_identificada': 'Plaga no identificada',
        'area_excede_limite': 'Área excede el límite del tabulador',
    }
    _VISAR_NIVELES_LABELS = {
        'planta_baja': 'Solo planta baja',
        'dos_o_mas': '2 niveles o más',
    }
    _VISAR_RODEA_LABELS = {
        'rodea': 'Rodea toda la casa',
        'un_lado': 'Solo de un lado',
    }
    _VISAR_UPSELL_LABELS = {
        'upsell_cebaderos': 'Estaciones antirroedores',
        'upsell_tapon': 'Tapón de drenaje',
        'upsell_guardapolvo': 'Guardapolvo',
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
    def _visar_combined_answer_label(self, interior_item, exterior_item):
        """Etiqueta única (producto + ambos tamaños) para el par interior+exterior
        fusionado, en lugar de dos etiquetas por dimensión (línea única = una entrada)."""
        tmpl = self.env['product.template'].browse(
            interior_item.get('product_tmpl_id')).exists()
        name = tmpl.display_name if tmpl else _('Fumigación')
        sizes = [t for t in (interior_item.get('tier_name'),
                             exterior_item.get('tier_name')) if t]
        return '%s: %s' % (name, ' / '.join(sizes)) if sizes else name

    @api.model
    def _visar_metros_labels(self, items):
        """Etiquetas de m² para Q&A; colapsa el par interior+exterior en una sola entrada
        (línea única) en vez de una por dimensión."""
        interior_item, exterior_item = self._visar_interior_exterior_pair(items)
        labels = []
        for item in items:
            if interior_item is not None and item is exterior_item:
                continue  # plegado en la etiqueta combinada del item interior
            if interior_item is not None and item is interior_item:
                labels.append(self._visar_combined_answer_label(interior_item, exterior_item))
                continue
            label = self._visar_item_answer_label(item)
            if label:
                labels.append(label)
        return labels

    @api.model
    def _visar_build_native_answer_inputs(self, appointment_type, zone, items=None,
                                          partner_id=False, selections=None,
                                          delivery_address=None):
        """Construye appointment.answer.input para zona, dirección, m² y calificación capturados fuera del formulario."""
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

        direccion_q = self._visar_question_address()
        address_text = self._visar_format_delivery_address(delivery_address)
        if direccion_q and address_text:
            inputs.append({
                **base,
                'question_id': direccion_q.id,
                'value_text_box': address_text,
            })

        metros_q = self._visar_question_metros()
        if items and metros_q:
            labels = self._visar_metros_labels(items)
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
        """Respuestas del guión (motivo, plagas, roedores, valoración) + notas de confirmación
        y flags de upsell candidato, para el bloque Questions & Answers de la cita."""
        inputs = []

        def _add(question, value):
            if question and value:
                inputs.append({**base, 'question_id': question.id, 'value_text_box': value})

        # Motivo (preventivo / correctivo).
        motivo = selections.get('motivo')
        _add(self._visar_question_motivo(),
             self._VISAR_MOTIVO_LABELS.get(motivo, motivo) if motivo else '')

        # Roedores (derivado de las categorías de plaga; conservado por compatibilidad).
        roedores = selections.get('roedores')
        _add(self._visar_question_roedores(),
             self._VISAR_ROEDORES_LABELS.get(roedores, roedores) if roedores else '')

        # Plagas a tratar (categorías) + flags de upsell candidato como nota.
        servicio = selections.get('servicio_plaga') or []
        if isinstance(servicio, str):
            servicio = [s for s in servicio.split(',') if s]
        plaga_bits = []
        if servicio:
            plaga_bits.append(', '.join(
                self._VISAR_SERVICIO_PLAGA_LABELS.get(s, s) for s in servicio))
        upsells = [
            self._VISAR_UPSELL_LABELS[key]
            for key in ('upsell_cebaderos', 'upsell_tapon', 'upsell_guardapolvo')
            if selections.get(key)
        ]
        if upsells:
            plaga_bits.append('Candidatos a ofrecer: %s' % ', '.join(upsells))
        _add(self._visar_question_servicio_plaga(), ' — '.join(plaga_bits))

        # Motivo de valoración (solo si se activó el corte).
        if selections.get('requiere_valoracion'):
            motivo_val = selections.get('motivo_valoracion')
            _add(self._visar_question_motivo_valoracion(),
                 self._VISAR_MOTIVO_VALORACION_LABELS.get(motivo_val, motivo_val)
                 if motivo_val else '')

        return inputs

    @api.model
    def _visar_calification_notes(self, selections):
        """Notas de confirmación ligera (niveles, si el jardín rodea la casa, m² estimados)
        para anexar a la descripción del evento. No accionables (solo referencia)."""
        selections = selections or {}
        bits = []
        niveles = selections.get('interior_niveles')
        if niveles:
            bits.append('Niveles: %s' % self._VISAR_NIVELES_LABELS.get(niveles, niveles))
        estimado = selections.get('interior_estimado_m2')
        if estimado:
            bits.append('Construcción estimada: %s m²' % estimado)
        rodea = selections.get('exterior_rodea')
        if rodea:
            bits.append('Jardín: %s' % self._VISAR_RODEA_LABELS.get(rodea, rodea))
        return bits

    @api.model
    def _visar_unlink_questions_from_entry_types(self):
        """Quita las preguntas Visar de TODOS los tipos de cita para que no aparezcan
        en el formulario web (se responden por código). Idempotente: puede ejecutarse
        cuantas veces haga falta. Cubre tanto las preguntas del módulo (por external id)
        como las creadas manualmente (por nombre, incluidas variantes de tipo selección)."""
        native_questions = (
            self._visar_question_zona()
            | self._visar_question_address()
            | self._visar_question_metros()
            | self._visar_question_plaga()
            | self._visar_question_roedores()
            | self._visar_question_tipo_plaga()
            | self._visar_question_motivo()
            | self._visar_question_servicio_plaga()
            | self._visar_question_motivo_valoracion()
        )
        # También busca por nombre para cubrir duplicados/manuales creados fuera del módulo.
        Question = self.env['appointment.question'].sudo()
        extra_questions = Question.search([('name', 'in', [
            'Zona', 'Zona geográfica', 'Zona Visar', 'Zona geografica',
            'Metros cuadrados', 'm2', 'M2', 'Metros',
            'Dirección de servicio', 'Dirección', 'Direccion', 'Dirección de entrega',
            'Domicilio', 'Address', 'Dirección de servicio (Visar)',
            '¿Tienes plaga o es preventivo?', '¿Tienes problema de roedores?', '¿Qué plaga tienes?',
            'Motivo', 'Plagas a tratar', 'Motivo de valoración', 'Motivo de valoracion',
        ])])
        all_questions = native_questions | extra_questions
        if not all_questions:
            return
        # Quita de CUALQUIER tipo de cita que las referencie (question_ids es Many2many),
        # no solo del maestro/valoración.
        linked_types = self.sudo().search([('question_ids', 'in', all_questions.ids)])
        for apt_type in linked_types:
            to_remove = apt_type.question_ids & all_questions
            if to_remove:
                apt_type.write({'question_ids': [(3, qid) for qid in to_remove.ids]})
        # Las preguntas nativas del módulo se responden por código: no reutilizables.
        if native_questions:
            native_questions.sudo().write({'is_reusable': False})

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

    @api.model
    def _visar_selections_has_roedores(self, selections):
        """True si el cliente pidió control de roedores en el wizard.

        Vive aquí -y no como literal repetido- porque lo consultan el controlador
        web y el agente de WhatsApp. Ojo con el `== 'si'`: la respuesta guardada
        es 'si'/'no', y ambas son *truthy*; comparar por verdad booleana añadiría
        roedores a toda reserva donde el cliente dijo que NO.
        """
        return (selections or {}).get('roedores') == 'si'

    # ------------------------------------------------------------------
    # Apartados temporales (visar.slot.hold)
    # ------------------------------------------------------------------

    def _get_appointment_slots(self, timezone, filter_users=None, filter_resources=None,
                               asked_capacity=1, reference_date=None):
        """Precarga los apartados una sola vez para toda la generacion de slots.

        `_get_resources_remaining_capacity` corre una vez POR SLOT, y un mes son
        cientos. Con una consulta por llamada el calendario tardaba ~1 s mas
        (+57%, medido en el servidor), y lo pagaba el wizard web igual que el
        agente. Aqui se toma una foto de los apartados vivos y el override la
        filtra en memoria.

        La foto solo vale para ESTA generacion; nada crea apartados a mitad de
        ella. Si alguien llama al calculo de capacidad por su cuenta (sin pasar
        por aqui), no hay foto y se consulta la base como siempre.
        """
        records = self
        if 'visar_hold_cache' not in self.env.context:
            snapshot = self.env['visar.slot.hold']._visar_snapshot(
                self.resource_ids | (filter_resources or self.env['appointment.resource']))
            records = self.with_context(visar_hold_cache=snapshot)
        return super(AppointmentType, records)._get_appointment_slots(
            timezone, filter_users=filter_users, filter_resources=filter_resources,
            asked_capacity=asked_capacity, reference_date=reference_date)

    def _get_resources_remaining_capacity(self, resources, slot_start_utc, slot_stop_utc,
                                          resource_to_bookings=None,
                                          with_linked_resources=True,
                                          filter_resources=None):
        """Descuenta los apartados vivos de la capacidad disponible.

        Se engancha AQUI y no en `_visar_filter_slots_multi_service` porque este
        es el único punto por el que pasan todos los caminos: la generación de
        slots del calendario, la validación final al enviar el formulario de cita
        (`appointment/controllers/appointment.py`) y, de rebote,
        `_visar_resource_free_at`. El filtro multi-servicio no habría cubierto la
        rama de valoración, que no pasa por él.

        Contexto que reconoce:
          * `visar_hold_owner`     — clave del cliente cuyos apartados NO cuentan
                                     (quien apartó el horario debe poder reservarlo);
          * `visar_ignore_hold_ids`— ids concretos a ignorar (re-validación de un
                                     apartado ya identificado).

        Ver `visar_slot_hold.py` para el porqué del modelo.
        """
        capacity = super()._get_resources_remaining_capacity(
            resources, slot_start_utc, slot_stop_utc,
            resource_to_bookings=resource_to_bookings,
            with_linked_resources=with_linked_resources,
            filter_resources=filter_resources)

        # Sin recursos el nativo devuelve {'total_remaining_capacity': 0}: no hay
        # nada que descontar y la clave de recurso ni siquiera existe.
        if not resources:
            return capacity

        # Las claves-registro del dict SON el conjunto de recursos que el nativo
        # considero (ya con linked/filter aplicados). Reusarlas evita repetir esa
        # logica y que las dos se desincronicen.
        # Se filtra por TIPO y no comparando contra la cadena: comparar un
        # recordset con un str hace que Odoo emita un UserWarning en cada slot, y
        # esto corre en el camino caliente de la generacion del calendario.
        resource_keys = [key for key in capacity if not isinstance(key, str)]
        if not resource_keys:
            return capacity

        Resource = self.env['appointment.resource']
        considered = Resource.browse([res.id for res in resource_keys])
        used = self.env['visar.slot.hold']._visar_used_capacity(
            considered, slot_start_utc, slot_stop_utc,
            exclude_owner=self.env.context.get('visar_hold_owner'),
            exclude_ids=self.env.context.get('visar_ignore_hold_ids'))
        if not used:
            return capacity

        total = 0
        for key in resource_keys:
            remaining = capacity[key] - used.get(key.id, 0)
            capacity[key] = remaining
            total += remaining
        capacity['total_remaining_capacity'] = total
        return capacity

    # Filtra la estructura de slots del calendario dejando solo los con técnicos simultáneos disponibles.
    @api.model
    def _visar_filter_slots_multi_service(self, master_type, months, service_pools, timezone,
                                          asked_capacity=1, destination=None):
        import pytz
        from dateutil.relativedelta import relativedelta
        from werkzeug.urls import url_decode, url_encode

        # Segunda foto de apartados. La de `_get_appointment_slots` no alcanza
        # hasta aqui: esta pasada corre DESPUES de que el nativo retorno, sobre el
        # recordset del llamador, que ya no lleva `visar_hold_cache`. Medido en el
        # servidor: la foto resolvia 223 consultas y otras 221 seguian yendo a la
        # base por este camino (`_visar_resource_free_at`), o sea la mitad del
        # ahorro. Se contextualiza `master_type` porque es el recordset sobre el
        # que `_visar_resource_free_at` acaba pidiendo la capacidad.
        if 'visar_hold_cache' not in master_type.env.context:
            pool_resources = self.env['appointment.resource'].browse(
                {rid for pool in service_pools.values() for rid in pool.ids})
            snapshot = self.env['visar.slot.hold']._visar_snapshot(
                master_type.resource_ids | pool_resources)
            master_type = master_type.with_context(visar_hold_cache=snapshot)

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
        # Factibilidad de ruta, como una pasada más (diseño 33 §5). Va DESPUÉS de
        # elegir recursos porque necesita saber de quién es la agenda que hay que
        # proteger. Con `destination=None` -o sin token, o con el flag apagado- no
        # toca nada: degradar, nunca bloquear (§5.4).
        #
        # `destination` es un PARÁMETRO y no una clave de contexto a propósito.
        # `visar_hold_cache` y `visar_hold_owner` son contexto porque su consumidor
        # se alcanza desde código NATIVO de Odoo y no hay firma que extender; aquí
        # los dos llamadores son código de Visar y ya tienen el booking en la mano.
        return self._visar_filter_slots_travel(
            master_type, filtered_months, timezone, destination, require='all')

    @api.model
    def _visar_list_unit_price(self, product, zone, plan=None):
        """Precio de lista unitario desde la pricelist de la zona.

        La variante ya fue resuelta correctamente por _visar_get_variant_for_zone(),
        así que la pricelist encuentra el precio correcto para la zona del cliente.

        Con `plan` cotiza desde la lista (zona × plan): el producto recurrente lleva
        el descuento de la póliza y el resto sigue al precio de la zona. Sin pasar
        `plan_id` la regla que resuelve es la de paso —el precio SIN descuento—, así
        que el plan tiene que llegar hasta aquí o el paso anunciaría el precio de una
        compra única.
        """
        if not product:
            return 0.0
        website = self.env['website'].get_current_website(fallback=False)
        pricelist = zone._visar_poliza_pricelist(plan) if zone else self.env['product.pricelist']
        if not pricelist and website:
            pricelist = website._get_and_cache_current_pricelist()
        if pricelist:
            kwargs = {'plan_id': plan.id} if plan and product.recurring_invoice else {}
            return pricelist._get_product_price(product, 1.0, **kwargs)
        return product.lst_price

    @api.model
    def _visar_cart_line_net_unit_price(self, line_vals, zone, plan=None):
        """Precio unitario neto de una línea antes de añadirla al carrito."""
        product = self.env['product.product'].browse(line_vals.get('product_id')).exists()
        if not product:
            return 0.0
        unit = self._visar_list_unit_price(product, zone, plan=plan)
        discount = line_vals.get('discount') or 0.0
        return unit * (1.0 - discount / 100.0)

    @api.model
    def _visar_skip_cart_line(self, line_vals, zone, plan=None):
        """True si la línea no debe ir al carrito (Odoo bloquea precio 0)."""
        if line_vals.get('is_free'):
            return True
        return self._visar_cart_line_net_unit_price(line_vals, zone, plan=plan) <= 0

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

    @api.model
    def _visar_offered_addons(self, items, zone, include_roedores=False):
        """Add-ons OPCIONALES (Obligatorio=No) ofrecibles como extras/upsell.

        Junta las líneas opcionales de los productos de la reserva (+ producto de
        roedores si aplica), suma cantidades por producto y resuelve variante/precio
        por zona. Omite los de precio 0 (Odoo bloquea líneas a 0)."""
        ProductTemplate = self.env['product.template']
        templates = ProductTemplate.browse(
            [i['product_tmpl_id'] for i in items if i.get('product_tmpl_id')]).exists()
        if include_roedores:
            roedores_tmpl = ProductTemplate._visar_get_roedores_template()
            if roedores_tmpl:
                templates |= roedores_tmpl

        # Un mismo add-on puede estar listado como opcional en varios productos de la
        # reserva. Para una oferta opt-in (un solo checkbox) se toma el MÁXIMO de las
        # cantidades configuradas, no la suma (sumar es la regla de los obligatorios).
        qty_by_tmpl = {}
        for tmpl in templates:
            for line in tmpl.visar_optional_line_ids.filtered(
                    lambda l: not l.is_mandatory and l.optional_product_id):
                qty_by_tmpl[line.optional_product_id] = max(
                    qty_by_tmpl.get(line.optional_product_id, 0), line.quantity)

        offers = []
        for opt_tmpl, qty in qty_by_tmpl.items():
            variant = opt_tmpl.product_variant_id
            if not variant:
                continue
            variant = ProductTemplate._visar_variant_for_zone(variant, zone)
            unit_price = self._visar_list_unit_price(variant, zone)
            if unit_price <= 0:
                continue
            offers.append({
                'product_id': variant.id,
                'template_id': opt_tmpl.id,
                'name': variant.display_name,
                'quantity': qty,
                'unit_price': unit_price,
                'subtotal': unit_price * qty,
            })
        offers.sort(key=lambda o: o['name'])
        return offers

    @api.model
    def _visar_interior_exterior_pair(self, items):
        """Par (item_interior, item_exterior) del MISMO producto, ambos con precio y no
        valoración, candidato a fusionarse en una variante combinada. (None, None) si no
        aplica (p. ej. exterior 0-50 incluida, o exterior de otro producto como el corte)."""
        Dimension = self.env['visar.service.dimension']
        interior_by_tmpl = {}
        for item in items:
            if item.get('is_free') or item.get('is_valuation') or not item.get('product_tmpl_id'):
                continue
            dimension = Dimension.browse(item.get('dimension_id')).exists()
            if dimension and dimension.measure_type == 'interior':
                interior_by_tmpl[item['product_tmpl_id']] = item
        for item in items:
            if item.get('is_free') or item.get('is_valuation') or not item.get('product_tmpl_id'):
                continue
            dimension = Dimension.browse(item.get('dimension_id')).exists()
            if dimension and dimension.measure_type == 'exterior' \
                    and item['product_tmpl_id'] in interior_by_tmpl:
                return interior_by_tmpl[item['product_tmpl_id']], item
        return None, None

    @api.model
    def _visar_combined_fumigacion_line(self, interior_item, exterior_item, zone):
        """Línea de venta única para el par interior+exterior: usa la variante combinada
        (ambos ejes de tamaño) y su precio leído en vivo de la pricelist. Devuelve None si
        no se puede resolver la variante combinada (el llamador cae a dos líneas)."""
        tmpl = self.env['product.template'].browse(interior_item.get('product_tmpl_id')).exists()
        Tier = self.env['visar.service.tier']
        interior_tier = Tier.browse(interior_item.get('tier_id')).exists()
        exterior_tier = Tier.browse(exterior_item.get('tier_id')).exists()
        if not (tmpl and interior_tier and exterior_tier):
            return None
        combined = tmpl._visar_combined_variant_for_tiers(interior_tier, exterior_tier, zone)
        if not combined or self._visar_list_unit_price(combined, zone) <= 0:
            return None
        return {
            'product_id': combined.id,
            'discount': 0.0,
            'dimension_id': False,
            'is_combined_fumigacion': True,
        }

    # Construye las líneas de venta con variante por zona, descuento combo e incluidos al 100%.
    @api.model
    def _visar_build_sale_lines(self, items, zone, include_roedores=False, extra_addons=None):
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
        # Fusiona interior + exterior del mismo producto en UNA línea (variante combinada
        # con ambos ejes de tamaño). Se resuelve antes del bucle para que dimension_ids
        # (arriba) conserve interior+exterior y el descuento combo del corte siga aplicando.
        interior_item, exterior_item = self._visar_interior_exterior_pair(items)
        combined_line = self._visar_combined_fumigacion_line(
            interior_item, exterior_item, zone) if (interior_item and exterior_item) else None
        if combined_line:
            lines.append(combined_line)
        else:
            interior_item = exterior_item = None  # sin fusión: se procesan normalmente
        for item in items:
            if item is interior_item or item is exterior_item:
                continue
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
        seen_addon_tmpls = set()
        for item in items:
            if item.get('is_free') or item.get('is_valuation'):
                continue
            tmpl_id = item.get('product_tmpl_id')
            # Un mismo producto (p. ej. fumigación interior + exterior) aparece en dos
            # items pero es un solo servicio: sus add-ons obligatorios se cuentan una vez.
            if not tmpl_id or tmpl_id in seen_addon_tmpls:
                continue
            seen_addon_tmpls.add(tmpl_id)
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

        # Extras opcionales aceptados por el cliente en el paso de extras (upsell).
        for extra in (extra_addons or []):
            product = Product.browse(extra.get('product_id')).exists()
            if not product:
                continue
            lines.append({
                'product_id': product.id,
                'discount': 0.0,
                'quantity': max(int(extra.get('quantity') or 1), 1),
                'is_addon': True,
            })
        return lines

    # Calcula los precios estimados de la reserva respetando pricelist de zona y descuentos combo.
    @api.model
    def _visar_quote_booking(self, items, zone, quantity=1, include_roedores=False,
                             extra_addons=None, plan=None):
        """Cotización de la reserva. Con `plan` cotiza como póliza.

        En una póliza el total tiene dos caras: lo que se paga HOY (que incluye las
        mensualidades adelantadas) y lo recurrente por periodo. Ambas van en el
        resultado para que el paso pueda enseñar "hoy pagas X, luego Y al mes".
        """
        sale_lines = self._visar_build_sale_lines(
            items, zone, include_roedores=include_roedores, extra_addons=extra_addons)
        if not sale_lines:
            return False

        website = self.env['website'].get_current_website(fallback=False)
        pricelist = (zone._visar_poliza_pricelist(plan) if zone
                     else self.env['product.pricelist'])
        if not pricelist and website:
            pricelist = website._get_and_cache_current_pricelist()
        currency = (pricelist.currency_id if pricelist else False) or (
            website.currency_id if website else self.env.company.currency_id)

        quote_lines = []
        total = 0.0
        recurring_total = 0.0
        qty = max(int(quantity or 1), 1)
        Product = self.env['product.product'].sudo()
        for line_vals in sale_lines:
            product = Product.browse(line_vals['product_id']).exists()
            if not product:
                continue
            list_unit_price = self._visar_list_unit_price(product, zone, plan=plan)
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
                'is_recurring': bool(product.recurring_invoice),
            })
            total += line_total
            # Se separa SIEMPRE (con o sin plan) para poder comparar manzanas con
            # manzanas: lo recurrente es lo único que la póliza abarata y repite;
            # los add-ons son cargo único aunque se contrate póliza.
            if product.recurring_invoice:
                recurring_total += line_total

        if not quote_lines:
            return False
        # Periodos que el pedido cobra de entrada (2 en la Póliza Mensual). Las
        # mensualidades extra solo aplican a lo recurrente: los add-ons y extras se
        # cobran una vez, en la primera factura.
        periods = max(1, plan.visar_first_invoice_periods or 1) if plan else 1
        return {
            'lines': quote_lines,
            'total': total,
            'currency_id': currency.id,
            'zone_name': zone.name if zone else False,
            'plan': plan or False,
            'periods': periods,
            # Servicio recurrente por periodo: lo que la póliza cobra "al mes".
            'recurring_total': recurring_total,
            # Add-ons y extras: cargo único en la primera factura, no se repiten.
            'addons_total': total - recurring_total,
            # Servicio × periodos adelantados (sin extras).
            'upfront_service_total': recurring_total * periods,
            # Lo que se cobra HOY en total, extras incluidos.
            'upfront_total': total + recurring_total * (periods - 1),
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
