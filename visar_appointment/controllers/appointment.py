# -*- coding: utf-8 -*-
import json
import logging
from urllib.parse import quote_plus, unquote_plus

import pytz
from dateutil.relativedelta import relativedelta

from markupsafe import Markup

from odoo import Command, fields, http, _
from odoo.fields import Domain
from odoo.http import request
from odoo.addons.website_appointment_sale.controllers.appointment import WebsiteAppointmentSale
from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)

SESSION_KEY = 'visar_booking'

# Grupos de claves de selección por área del wizard.
_VISAR_INTERIOR_KEYS = ('interior_niveles', 'interior_estimado_m2', 'interior_proxy')
_VISAR_EXTERIOR_KEYS = ('exterior_band_id', 'exterior_rodea')
_VISAR_CUT_KEYS = ('requiere_valoracion', 'motivo_valoracion')
_VISAR_PLAGA_KEYS = (
    'servicio_plaga', 'roedores', 'upsell_cebaderos', 'upsell_tapon',
    'upsell_guardapolvo') + _VISAR_CUT_KEYS

# Al (re)enviar un paso, se limpian estas claves de selección (dependencias que quedan
# inválidas si esa respuesta cambia). Además, los pasos en _VISAR_CLEARS_TIERS limpian
# todos los tramos elegidos (tier_*). Solo se limpia lo realmente dependiente: cambiar
# interior NO invalida exterior (mediciones independientes).
_VISAR_STEP_CLEARS = {
    'services': ('motivo',) + _VISAR_PLAGA_KEYS + ('cobertura',)
                + _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS,
    'motivo': _VISAR_PLAGA_KEYS,
    'plagas': _VISAR_PLAGA_KEYS,
    'cobertura': _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS,
    'group': _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS,
    'interior': _VISAR_INTERIOR_KEYS,
    'exterior': _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS,
    'dimensiones': (),
}
_VISAR_CLEARS_TIERS = ('services', 'cobertura', 'group')


class VisarAppointmentController(WebsiteAppointmentSale):

    # ------------------------------------------------------------------
    # Sesión wizard
    # ------------------------------------------------------------------
    # Recupera y deserializa la sesión del wizard desde la sesión HTTP.
    def _visar_get_booking_session(self):
        raw = request.session.get(SESSION_KEY)
        if not raw:
            return {}
        return self._visar_booking_payload(raw)

    # Normaliza el dict de booking conservando solo los campos permitidos.
    def _visar_booking_payload(self, booking):
        raw = booking or {}
        payload = {
            'mode': raw.get('mode'),
            'master_appointment_type_id': raw.get('master_appointment_type_id'),
            'selections': dict(raw.get('selections') or {}),
        }
        for key in ('zone_id', 'appointment_type_id', 'm2', 'items', 'service_pools',
                    'delivery_address', 'extras_accepted'):
            if key in raw:
                payload[key] = raw[key]
        return payload

    # Guarda el payload normalizado del wizard en la sesión HTTP.
    def _visar_persist_booking(self, booking):
        request.session[SESSION_KEY] = self._visar_booking_payload(booking)
        return request.session[SESSION_KEY]

    @staticmethod
    def _visar_id_eq(left, right):
        try:
            return int(left or 0) == int(right or 0)
        except (TypeError, ValueError):
            return left == right

    # Reconstruye items desde selections si faltan; None si el wizard no está listo para pago.
    def _visar_resolve_wizard_payment_booking(self, booking, appointment_type):
        booking = booking or {}
        if booking.get('mode') != 'wizard':
            return None
        if not self._visar_id_eq(booking.get('master_appointment_type_id'), appointment_type.id):
            return None
        if not appointment_type.has_payment_step or not booking.get('zone_id'):
            return None
        items = booking.get('items') or []
        if not items and booking.get('selections'):
            items = request.env['appointment.type'].sudo()._visar_resolve_wizard_items(
                booking.get('selections'))
            if items:
                booking = dict(booking)
                booking['items'] = items
                self._visar_persist_booking(booking)
        return booking if items else None

    # Devuelve True si hay una sesión de wizard activa en curso.
    def _visar_wizard_active(self):
        booking = self._visar_get_booking_session()
        return booking.get('mode') == 'wizard'

    # Devuelve True si el wizard fue completado con items para el tipo de cita dado.
    def _visar_wizard_done(self, appointment_type_id, kwargs):
        if kwargs.get('filter_resource_ids'):
            return True
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return False
        if not self._visar_id_eq(booking.get('master_appointment_type_id'), appointment_type_id):
            return False
        return bool(booking.get('items'))

    # Devuelve True si el flujo de valoración fue completado para el tipo dado.
    def _visar_valuation_done(self, appointment_type_id, kwargs):
        if kwargs.get('filter_resource_ids'):
            return True
        booking = self._visar_get_booking_session()
        return bool(
            booking
            and booking.get('mode') == 'valuation'
            and booking.get('appointment_type_id') == appointment_type_id
            and booking.get('zone_id')
        )

    # Reconstruye los recordsets de recursos por servicio desde los IDs guardados en sesión.
    def _visar_pools_from_session(self, booking):
        pools = {}
        AptResource = request.env['appointment.resource'].sudo()
        for pool_key, resource_ids in (booking.get('service_pools') or {}).items():
            pools[pool_key] = AptResource.browse(resource_ids).exists()
        return pools

    # Pools actuales por zona + servicios (preferido sobre IDs congelados en sesión).
    def _visar_get_service_pools(self, booking):
        AptType = request.env['appointment.type'].sudo()
        pools = AptType._visar_pools_from_booking(booking)
        if pools:
            return pools
        return self._visar_pools_from_session(booking)

    # Genera el dict visar_quote con precios estimados para mostrar en la página de cita.
    def _visar_appointment_quote_context(self, appointment_type, asked_capacity=1):
        booking = self._visar_get_booking_session()
        AppointmentType = request.env['appointment.type'].sudo()

        if (
            booking
            and booking.get('mode') == 'valuation'
            and booking.get('appointment_type_id') == appointment_type.id
        ):
            items = booking.get('items') or []
            if not items:
                return {'visar_quote': False}
            zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id'))
            quote = AppointmentType._visar_quote_booking(
                items, zone, quantity=int(asked_capacity or 1))
            return {'visar_quote': quote or False}

        if not appointment_type.visar_is_master or not self._visar_wizard_active():
            return {'visar_quote': False}
        if not self._visar_id_eq(booking.get('master_appointment_type_id'), appointment_type.id):
            return {'visar_quote': False}
        items = booking.get('items') or []
        if not items:
            return {'visar_quote': False}
        zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id'))
        quote = AppointmentType._visar_quote_booking(
            items, zone, quantity=int(asked_capacity or 1),
            include_roedores=self._visar_booking_has_roedores(booking),
            extra_addons=booking.get('extras_accepted'))
        return {'visar_quote': quote or False}

    # Obtiene el appointment.type maestro del wizard.
    def _visar_master_appointment_type(self):
        return request.env['appointment.type'].sudo()._visar_get_master_appointment_type()

    # Obtiene el appointment.type de entrada para el flujo de valoración técnica.
    def _visar_valuation_appointment_type(self):
        return request.env['appointment.type'].sudo()._visar_get_valuation_appointment_type()

    # True si algún item del wizard resuelto requiere visita de valoración técnica.
    def _visar_selections_require_valuation(self, selections):
        # Corte por calificación (termitas/chinches/plaga no identificada) marcado en el paso de plagas.
        if (selections or {}).get('requiere_valoracion'):
            return True
        items = request.env['appointment.type'].sudo()._visar_resolve_wizard_items(selections)
        return any(item.get('is_valuation') for item in items)

    # Razón por la que el wizard cortó a valoración (para registrar en la cita).
    def _visar_resolve_valuation_reason(self, selections):
        selections = selections or {}
        reason = selections.get('motivo_valoracion')
        if reason:
            return reason
        # Corte por tramo (área que excede el límite del tabulador, sin flag explícito).
        items = request.env['appointment.type'].sudo()._visar_resolve_wizard_items(selections)
        if any(item.get('is_valuation') for item in items):
            return 'area_excede_limite'
        return False

    # Devuelve los grupos de servicio activos y visibles en el paso 1 del wizard.
    def _visar_wizard_groups(self):
        return request.env['visar.service.group'].sudo().search([
            ('active', '=', True),
            ('show_in_wizard', '=', True),
        ])

    # Inicializa la sesión wizard con el tipo maestro y selecciones vacías.
    def _visar_init_wizard_session(self):
        master = self._visar_master_appointment_type()
        self._visar_persist_booking({
            'mode': 'wizard',
            'master_appointment_type_id': master.id if master else False,
            'selections': {'group_ids': [], 'dimension_ids': []},
        })
        return master

    # Fusiona los nuevos valores de selección con el estado actual de la sesión.
    def _visar_update_selections(self, values):
        booking = self._visar_get_booking_session()
        if not booking.get('mode'):
            master = self._visar_master_appointment_type()
            booking['mode'] = 'wizard'
            booking['master_appointment_type_id'] = master.id if master else False
        selections = dict(booking.get('selections') or {})
        selections.update(values)
        booking['selections'] = selections
        self._visar_persist_booking(booking)

    # Convierte un string de formulario web a booleano Python.
    def _visar_parse_bool(self, value):
        return value in ('1', 'on', 'true', 'True', True)

    # Convierte una lista de strings a enteros, descartando valores no numéricos.
    def _visar_parse_id_list(self, values):
        ids = []
        for value in values:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        return ids

    # Extrae una lista de IDs enteros desde un campo multi-valor del formulario POST.
    def _visar_form_id_list(self, field):
        return self._visar_parse_id_list(request.httprequest.form.getlist(field))

    # Devuelve el recordset de grupos actualmente seleccionados en el wizard.
    def _visar_selected_groups(self, selections):
        Group = request.env['visar.service.group'].sudo()
        return Group.browse(selections.get('group_ids') or []).exists()

    # True si entre los grupos elegidos está fumigación (dispara motivo/plagas/cobertura).
    def _visar_fumigacion_selected(self, selections):
        return any(g.code == 'fumigacion' for g in self._visar_selected_groups(selections))

    # Grupo cuyas dimensiones se eligen por el paso de cobertura (interior/exterior/ambos).
    def _visar_coverage_group(self):
        return request.env['visar.service.group'].sudo().search(
            [('code', '=', 'fumigacion')], limit=1)

    # Dimensiones del grupo fumigación que corresponden a la cobertura elegida.
    def _visar_fum_dimensions_for_coverage(self, coverage):
        group = self._visar_coverage_group()
        if not group:
            return request.env['visar.service.dimension']
        dims = group.dimension_ids.filtered('active')
        interior = dims.filtered(lambda d: d.measure_type == 'interior')
        exterior = dims.filtered(lambda d: d.measure_type == 'exterior')
        if coverage == 'interior':
            return interior
        if coverage == 'exterior':
            return exterior
        return interior | exterior

    # True si la dimensión ya tiene un tramo elegido en las selecciones.
    def _visar_dim_has_tier(self, selections, dimension):
        key = dimension._visar_tier_field_name()
        return bool(
            (selections or {}).get(key)
            or ((selections or {}).get('tiers') or {}).get(str(dimension.id)))

    # True si el cliente declaró problema de roedores en el paso de calificación.
    def _visar_booking_has_roedores(self, booking):
        return (booking.get('selections') or {}).get('roedores') == 'si'

    # Add-ons opcionales ofrecibles como extras para la reserva actual.
    def _visar_extras_offers(self, booking):
        booking = booking or {}
        zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id')).exists()
        items = booking.get('items') or []
        if not zone or not items:
            return []
        return request.env['appointment.type'].sudo()._visar_offered_addons(
            items, zone, include_roedores=self._visar_booking_has_roedores(booking))

    def _visar_auto_dimensions_for_groups(self, groups, dimension_ids):
        """Añade dimensiones únicas de grupos con una sola opción."""
        Dimension = request.env['visar.service.dimension'].sudo()
        result = set(dimension_ids or [])
        for group in groups:
            dims = group.dimension_ids.filtered('active')
            if len(dims) == 1:
                result.add(dims.id)
        return list(result)

    # True si el grupo tiene más de una dimensión activa y requiere un sub-paso.
    def _visar_group_needs_substep(self, group):
        return len(group.dimension_ids.filtered('active')) > 1

    def _visar_next_group_substep(self, selections):
        """Primer grupo seleccionado que aún no tiene dimensiones elegidas.

        Excluye el grupo de fumigación: sus dimensiones se eligen en el paso de
        cobertura (interior/exterior/ambos), no en el sub-paso genérico.
        """
        selected_groups = self._visar_selected_groups(selections)
        coverage_group = self._visar_coverage_group()
        dimension_ids = set(selections.get('dimension_ids') or [])
        for group in selected_groups.sorted('sequence'):
            if group == coverage_group:
                continue
            if not self._visar_group_needs_substep(group):
                continue
            group_dim_ids = set(group.dimension_ids.filtered('active').ids)
            if not group_dim_ids.intersection(dimension_ids):
                return group
        return request.env['visar.service.group']

    def _visar_dimension_sections(self, selections, measure_type='direct'):
        """Secciones de tramos (radio por rango) para las dimensiones del tipo dado.

        - 'direct': paso de rango directo (fallback / legacy).
        - 'interior': modo 'sé mis m²' del paso interior (mismos rangos del tabulador).
        Las dimensiones de exterior no usan secciones: se resuelven por banda unificada.
        """
        ProductTemplate = request.env['product.template'].sudo()
        sections = []
        for dimension in self._visar_selection_dimension_ids(selections).filtered(
                lambda d: d.measure_type == measure_type):
            template = ProductTemplate._visar_get_service_template_for_dimension(dimension)
            if not template:
                continue
            sections.append({
                'dimension': dimension,
                'dimension_id': dimension.id,
                'label': dimension._visar_wizard_label(),
                'field_name': dimension._visar_tier_field_name(),
                'tiers': template._visar_tiers_for_dimension(dimension),
            })
        return sections

    # Dimensiones seleccionadas con el tipo de medición dado.
    def _visar_dims_by_measure(self, selections, measure_type):
        return self._visar_selection_dimension_ids(selections).filtered(
            lambda d: d.measure_type == measure_type)

    # Tramo cuyo rango de m² contiene el valor dado, para una dimensión concreta.
    # (Acotado por measure_scope; ante solapes gana el rango más angosto.)
    def _visar_tier_for_dimension_m2(self, dimension, m2):
        ProductTemplate = request.env['product.template'].sudo()
        template = ProductTemplate._visar_get_service_template_for_dimension(dimension)
        if not template:
            return request.env['visar.service.tier']
        return template._visar_tier_for_dimension_m2(dimension, m2)

    # Delega en el modelo para obtener las dimensiones activas de las selecciones actuales.
    def _visar_selection_dimension_ids(self, selections):
        return request.env['appointment.type'].sudo()._visar_selection_dimension_ids(selections)

    def _visar_wizard_steps(self, selections):
        """Lista ordenada de claves de paso aplicables a las selecciones actuales.

        Sirve para el indicador 'Paso X de Y'. Los pasos de medición se infieren
        de los measure_type de las dimensiones elegidas; si aún no se eligió la
        cobertura, se anticipan los de fumigación.
        """
        selections = selections or {}
        steps = ['services']
        fum = self._visar_fumigacion_selected(selections)
        if fum:
            steps += ['motivo', 'plagas', 'cobertura']

        coverage_group = self._visar_coverage_group()
        for group in self._visar_selected_groups(selections).sorted('sequence'):
            if group == coverage_group:
                continue
            if self._visar_group_needs_substep(group):
                steps.append('group_%s' % group.id)

        measure_types = {
            d.measure_type for d in self._visar_selection_dimension_ids(selections)
        }
        if fum and not selections.get('cobertura') and coverage_group:
            measure_types |= {
                d.measure_type for d in coverage_group.dimension_ids.filtered('active')
            }
        for mtype, key in (('interior', 'interior'), ('exterior', 'exterior'),
                           ('direct', 'dimensiones')):
            if mtype in measure_types:
                steps.append(key)
        steps.append('address')
        # El paso de extras solo existe tras resolver zona/items y si hay algo que ofrecer.
        booking = self._visar_get_booking_session()
        if booking.get('zone_id') and booking.get('items') and self._visar_extras_offers(booking):
            steps.append('extras')
        return steps

    # Devuelve (índice 1-based, total) del paso actual para el indicador de progreso.
    def _visar_wizard_position(self, selections, step_key):
        steps = self._visar_wizard_steps(selections)
        idx = steps.index(step_key) + 1 if step_key in steps else 1
        return idx, len(steps)

    # URL (ruta GET) del paso dado.
    def _visar_step_url(self, step_key):
        base = '/appointment/visar/booking'
        if step_key.startswith('group_'):
            return base + '/wizard/group/%s' % step_key[len('group_'):]
        return {
            'services': base,
            'motivo': base + '/wizard/motivo',
            'plagas': base + '/wizard/plagas',
            'cobertura': base + '/wizard/cobertura',
            'interior': base + '/wizard/interior',
            'exterior': base + '/wizard/exterior',
            'dimensiones': base + '/wizard/dimensiones',
            'address': base + '/wizard/direccion',
            'extras': base + '/wizard/extras',
        }.get(step_key, base)

    # URL del paso anterior al actual (para el botón "Volver"); None si es el primero.
    def _visar_wizard_prev_url(self, step_key, selections):
        steps = self._visar_wizard_steps(selections)
        if step_key not in steps:
            return None
        idx = steps.index(step_key)
        if idx <= 0:
            return None
        return self._visar_step_url(steps[idx - 1])

    # Limpia las selecciones que quedan inválidas al (re)enviar el paso dado.
    def _visar_clear_downstream(self, selections, step_key):
        norm = 'group' if step_key.startswith('group_') else step_key
        keys = set(_VISAR_STEP_CLEARS.get(norm, ()))
        clears_tiers = norm in _VISAR_CLEARS_TIERS
        result = {}
        for key, value in (selections or {}).items():
            if key in keys:
                continue
            if clears_tiers and key.startswith('tier_'):
                continue
            result[key] = value
        return result

    # Aplica la respuesta de un paso: limpia estado aguas abajo y fusiona los nuevos valores.
    def _visar_commit_step(self, step_key, updates):
        booking = self._visar_get_booking_session()
        if not booking.get('mode'):
            master = self._visar_master_appointment_type()
            booking['mode'] = 'wizard'
            booking['master_appointment_type_id'] = master.id if master else False
        selections = self._visar_clear_downstream(booking.get('selections') or {}, step_key)
        selections.update(updates)
        booking['selections'] = selections
        self._visar_persist_booking(booking)
        return selections

    # Ruta del siguiente paso incompleto del wizard según las selecciones actuales.
    def _visar_wizard_next(self, selections=None):
        booking = self._visar_get_booking_session()
        selections = selections if selections is not None else (booking.get('selections') or {})
        base = '/appointment/visar/booking'
        if not selections.get('group_ids'):
            return base

        # Corte a valoración (plaga compleja o banda de exterior fuera de rango):
        # atajo global para no re-preguntar mediciones cuando ya se decidió valoración.
        if selections.get('requiere_valoracion'):
            return base + '/wizard/valoracion-aviso'

        if self._visar_fumigacion_selected(selections):
            if not selections.get('motivo'):
                return base + '/wizard/motivo'
            if not selections.get('servicio_plaga'):
                return base + '/wizard/plagas'
            if not selections.get('cobertura'):
                return base + '/wizard/cobertura'

        next_group = self._visar_next_group_substep(selections)
        if next_group:
            return base + '/wizard/group/%s' % next_group.id

        dims = self._visar_selection_dimension_ids(selections)

        def needs(measure_type):
            return any(
                d.measure_type == measure_type and not self._visar_dim_has_tier(selections, d)
                for d in dims)

        if needs('interior'):
            return base + '/wizard/interior'
        if needs('exterior'):
            return base + '/wizard/exterior'
        if needs('direct'):
            return base + '/wizard/dimensiones'

        if self._visar_selections_require_valuation(selections):
            return base + '/wizard/valoracion-aviso'
        return base + '/wizard/direccion'

    # Renderiza el paso de dirección (último paso del wizard).
    def _visar_render_address(self, selections, values=None, error=None):
        ctx = self._visar_wizard_context_base(
            'address', selections=selections, error=error, values=values or {})
        return request.render('visar_appointment.visar_wizard_zona', ctx)

    # Extrae y normaliza los campos de dirección de entrega del formulario.
    def _visar_extract_address(self, post):
        keys = ('street', 'ext_num', 'int_num', 'neighborhood', 'zip')
        return {key: (post.get(key) or '').strip() for key in keys}

    # Resuelve la zona desde el CP y completa ciudad/estado.
    # Devuelve (zone, address, error): el número interior es opcional; ciudad y
    # estado se derivan del CP en el servidor (no se confía en el cliente).
    def _visar_resolve_address_zone(self, post):
        address = self._visar_extract_address(post)
        required = ('street', 'ext_num', 'neighborhood', 'zip')
        if any(not address.get(key) for key in required):
            return request.env['visar.zone'], address, _(
                'Completa calle, número exterior, colonia y código postal.')
        CpModel = request.env['visar.zone.cp'].sudo()
        cp_record = CpModel._get_cp_record(address['zip'])
        if not cp_record or not cp_record.zone_id:
            return request.env['visar.zone'], address, _(
                'No damos servicio en el código postal %s. Contáctanos.'
            ) % address['zip']
        address['zip'] = CpModel._normalize_cp(address['zip'])
        address['city'] = cp_record.municipality or ''
        address['state'] = 'Nuevo León'
        return cp_record.zone_id, address, None

    # Construye el dict de contexto base común a todos los pasos del wizard.
    def _visar_wizard_context_base(self, step_key, selections=None, error=None, values=None):
        selections = selections or {}
        idx, total = self._visar_wizard_position(selections, step_key)
        return {
            'wizard_groups': self._visar_wizard_groups(),
            'wizard_step': idx,
            'wizard_total_steps': total,
            'back_url': self._visar_wizard_prev_url(step_key, selections),
            'error': error,
            'values': values or {},
            'selections': selections,
        }

    # Determina el flujo Visar del tipo de cita consultando el campo y los parámetros de sistema.
    def _visar_resolve_entry_flow(self, appointment_type):
        if appointment_type.visar_flow:
            return appointment_type.visar_flow
        icp = request.env['ir.config_parameter'].sudo()
        wizard_id = int(icp.get_param('visar.wizard_entry_appointment_type_id') or 0)
        valuation_id = int(icp.get_param('visar.valuation_entry_appointment_type_id') or 0)
        if wizard_id and appointment_type.id == wizard_id:
            return 'wizard'
        if valuation_id and appointment_type.id == valuation_id:
            return 'valuation'
        return False

    # Persiste el flujo en el tipo de cita si aún no estaba guardado en el campo.
    def _visar_ensure_entry_flow(self, appointment_type):
        flow = self._visar_resolve_entry_flow(appointment_type)
        if flow and not appointment_type.visar_flow:
            appointment_type.sudo().write({'visar_flow': flow})
        return flow

    # ------------------------------------------------------------------
    # Intercepción flujo nativo
    # ------------------------------------------------------------------
    # Restringe el listado público para mostrar solo tipos con flujo Visar.
    @classmethod
    def _appointments_base_domain(
        cls, filter_appointment_type_ids, search=False, invite_token=False,
        additional_domain=None, filter_countries=False,
    ):
        domain = super()._appointments_base_domain(
            filter_appointment_type_ids, search=search, invite_token=invite_token,
            additional_domain=additional_domain, filter_countries=filter_countries,
        )
        if not invite_token and not filter_appointment_type_ids:
            if not request.httprequest.args.get('filter_resource_ids'):
                domain &= Domain('visar_flow', 'in', ['valuation', 'wizard'])
        return domain

    # Añade la cotización Visar estimada a los valores de la página de cita.
    def _prepare_appointment_type_page_values(
        self, appointment_type, staff_user_id=False, resource_selected_id=False, **kwargs,
    ):
        values = super()._prepare_appointment_type_page_values(
            appointment_type, staff_user_id=staff_user_id,
            resource_selected_id=resource_selected_id, **kwargs,
        )
        asked_capacity = int(kwargs.get('asked_capacity', 1) or 1)
        values.update(self._visar_appointment_quote_context(appointment_type, asked_capacity))
        return values

    # Inyecta el contexto de cotización Visar en el formulario de confirmación de cita.
    def appointment_type_id_form(
        self, appointment_type_id, date_time, duration, staff_user_id=None,
        resource_selected_id=None,         available_resource_ids=None, asked_capacity=1, **kwargs,
    ):
        appointment_type = request.env['appointment.type'].sudo().browse(int(appointment_type_id))
        quote_ctx = self._visar_appointment_quote_context(appointment_type, int(asked_capacity))
        render = request.render

        # Intercepta request.render para inyectar quote_ctx en los valores del template.
        def render_with_quote(template, values, **kw):
            if isinstance(values, dict):
                values = dict(values, **quote_ctx)
            return render(template, values, **kw)

        request.render = render_with_quote
        try:
            return super().appointment_type_id_form(
                appointment_type_id, date_time, duration,
                staff_user_id=staff_user_id, resource_selected_id=resource_selected_id,
                available_resource_ids=available_resource_ids, asked_capacity=asked_capacity,
                **kwargs,
            )
        finally:
            request.render = render

    # Intercepta la página de cita y redirige al flujo Visar que corresponda.
    def appointment_type_page(self, appointment_type_id, **kwargs):
        appointment_type = request.env['appointment.type'].sudo().browse(appointment_type_id)
        if not appointment_type.exists():
            return request.not_found()
        entry_flow = self._visar_ensure_entry_flow(appointment_type)
        if entry_flow == 'wizard':
            return request.redirect('/appointment/visar/booking?restart=1')
        if entry_flow == 'valuation':
            if not self._visar_valuation_done(appointment_type_id, kwargs):
                return request.redirect(
                    '/appointment/%s/visar/valoracion' % appointment_type_id)
            return super().appointment_type_page(appointment_type_id, **kwargs)
        if appointment_type.visar_is_master and not self._visar_wizard_done(appointment_type_id, kwargs):
            return request.redirect('/appointment/visar/booking?restart=1')
        return super().appointment_type_page(appointment_type_id, **kwargs)

    # ------------------------------------------------------------------
    # Wizard — rutas dinámicas
    # ------------------------------------------------------------------
    # Inicia el wizard, inicializa la sesión y muestra el paso 1 de selección de servicios.
    @http.route(['/appointment/visar/booking'],
                type='http', auth='public', website=True, sitemap=False)
    def visar_wizard_start(self, **kwargs):
        booking = self._visar_get_booking_session()
        # Reinicia al entrar desde el selector (restart=1) o si no hay sesión activa;
        # al regresar con "Volver" (sin restart) conserva las respuestas ya dadas.
        if kwargs.get('restart') or booking.get('mode') != 'wizard':
            master = self._visar_init_wizard_session()
        else:
            master = self._visar_master_appointment_type()
        if not master:
            return request.not_found()
        selections = self._visar_get_booking_session().get('selections') or {}
        ctx = self._visar_wizard_context_base('services', selections=selections, values=kwargs)
        ctx['error'] = kwargs.get('error')
        return request.render('visar_appointment.visar_wizard_services', ctx)

    # Procesa el POST del paso 1 (grupos) y delega el siguiente paso al resolutor.
    @http.route(['/appointment/visar/booking/wizard/services'],
                type='http', auth='public', website=True, methods=['POST'], sitemap=False)
    def visar_wizard_services(self, **post):
        master = self._visar_master_appointment_type()
        if not master:
            return request.not_found()
        group_ids = self._visar_form_id_list('group_ids')
        groups = request.env['visar.service.group'].sudo().browse(group_ids).exists()
        if not groups:
            ctx = self._visar_wizard_context_base(
                'services', error=_('Selecciona al menos un servicio.'), values=post)
            return request.render('visar_appointment.visar_wizard_services', ctx)

        dimension_ids = self._visar_auto_dimensions_for_groups(groups, [])
        selections = self._visar_commit_step('services', {
            'group_ids': groups.ids,
            'dimension_ids': dimension_ids,
        })
        return request.redirect(self._visar_wizard_next(selections))

    # Muestra y procesa el sub-paso de dimensiones para un grupo con múltiples opciones.
    @http.route(['/appointment/visar/booking/wizard/group/<int:group_id>'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_group_dimensions(self, group_id, **post):
        booking = self._visar_get_booking_session()
        selections = booking.get('selections') or {}
        group = request.env['visar.service.group'].sudo().browse(group_id).exists()
        if not group or group.id not in (selections.get('group_ids') or []):
            return request.redirect('/appointment/visar/booking')

        step_key = 'group_%s' % group.id
        if request.httprequest.method == 'GET':
            ctx = self._visar_wizard_context_base(step_key, selections=selections, values=post)
            ctx.update({
                'wizard_group': group,
                'wizard_dimensions': group.dimension_ids.filtered('active'),
            })
            return request.render('visar_appointment.visar_wizard_group_dimensions', ctx)

        dimension_ids = self._visar_form_id_list('dimension_ids')
        valid_ids = group.dimension_ids.filtered('active').ids
        chosen = [d for d in dimension_ids if d in valid_ids]
        if not chosen:
            ctx = self._visar_wizard_context_base(
                step_key, selections=selections, error=_('Selecciona al menos una opción.'),
                values=post)
            ctx.update({
                'wizard_group': group,
                'wizard_dimensions': group.dimension_ids.filtered('active'),
            })
            return request.render('visar_appointment.visar_wizard_group_dimensions', ctx)

        current = set(selections.get('dimension_ids') or [])
        for dim_id in chosen:
            current.add(dim_id)
        for dim_id in valid_ids:
            if dim_id not in chosen:
                current.discard(dim_id)
        selections = self._visar_commit_step(step_key, {'dimension_ids': list(current)})
        return request.redirect(self._visar_wizard_next(selections))

    # Muestra y procesa el paso de selección de tramos (m²) por cada dimensión elegida.
    @http.route(['/appointment/visar/booking/wizard/dimensiones'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_dimensiones(self, **post):
        booking = self._visar_get_booking_session()
        selections = booking.get('selections') or {}
        sections = self._visar_dimension_sections(selections)
        if request.httprequest.method == 'GET':
            if not sections:
                return request.redirect(self._visar_wizard_next(selections))
            ctx = self._visar_wizard_context_base('dimensiones', selections=selections, values=post)
            ctx['dimension_sections'] = sections
            return request.render('visar_appointment.visar_wizard_dimensiones', ctx)

        tier_updates = {}
        for section in sections:
            tier_id = post.get(section['field_name'])
            if not tier_id:
                ctx = self._visar_wizard_context_base(
                    'dimensiones', selections=selections,
                    error=_('Selecciona un rango para cada servicio.'), values=post)
                ctx['dimension_sections'] = sections
                return request.render('visar_appointment.visar_wizard_dimensiones', ctx)
            tier_updates[section['field_name']] = int(tier_id)
        selections = self._visar_commit_step('dimensiones', tier_updates)
        return request.redirect(self._visar_wizard_next(selections))

    # Paso Motivo (P1): preventivo o correctivo. Solo aplica con fumigación.
    @http.route(['/appointment/visar/booking/wizard/motivo'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_motivo(self, **post):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        if not self._visar_fumigacion_selected(selections):
            return request.redirect(self._visar_wizard_next(selections))

        if request.httprequest.method == 'GET':
            ctx = self._visar_wizard_context_base('motivo', selections=selections, values=post)
            return request.render('visar_appointment.visar_wizard_motivo', ctx)

        motivo = post.get('motivo')
        if motivo not in ('preventivo', 'correctivo'):
            ctx = self._visar_wizard_context_base(
                'motivo', selections=selections,
                error=_('Indica si es preventivo o correctivo.'), values=post)
            return request.render('visar_appointment.visar_wizard_motivo', ctx)

        selections = self._visar_commit_step('motivo', {'motivo': motivo})
        return request.redirect(self._visar_wizard_next(selections))

    # Paso Plagas (P2): categorías + cortes a valoración (termitas/chinches/no identificada).
    @http.route(['/appointment/visar/booking/wizard/plagas'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_plagas(self, **post):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        if not self._visar_fumigacion_selected(selections) or not selections.get('motivo'):
            return request.redirect(self._visar_wizard_next(selections))

        if request.httprequest.method == 'GET':
            ctx = self._visar_wizard_context_base('plagas', selections=selections, values=post)
            return request.render('visar_appointment.visar_wizard_plagas', ctx)

        motivo = selections.get('motivo')
        chosen = set(request.httprequest.form.getlist('servicio_plaga'))
        categories = [c for c in ('rastreros', 'voladores', 'roedores') if c in chosen]

        # Protección general (rama preventiva): activa las tres categorías, sin corte.
        if 'proteccion_general' in chosen:
            categories = ['rastreros', 'voladores', 'roedores']

        # Cortes a valoración: solo en la rama correctiva.
        cut_reason = False
        if motivo == 'correctivo':
            if 'termitas' in chosen:
                cut_reason = 'termitas'
            elif 'chinches' in chosen:
                cut_reason = 'chinches'
            elif 'no_se' in chosen:
                cut_reason = 'plaga_no_identificada'

        if not categories and not cut_reason:
            ctx = self._visar_wizard_context_base(
                'plagas', selections=selections,
                error=_('Selecciona al menos una opción.'), values=post)
            return request.render('visar_appointment.visar_wizard_plagas', ctx)

        updates = {
            'servicio_plaga': categories,
            'roedores': 'si' if 'roedores' in categories else 'no',
            # Flags de upsell candidato (guardados para fase posterior, sin UI aún).
            'upsell_cebaderos': 'roedores' in categories,
            'upsell_tapon': 'rastreros' in categories,
            'upsell_guardapolvo': 'rastreros' in categories,
        }
        if cut_reason:
            updates['requiere_valoracion'] = True
            updates['motivo_valoracion'] = cut_reason
        selections = self._visar_commit_step('plagas', updates)
        return request.redirect(self._visar_wizard_next(selections))

    # Paso Cobertura (P3): interior / exterior / ambos. Fija las dimensiones de fumigación.
    @http.route(['/appointment/visar/booking/wizard/cobertura'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_cobertura(self, **post):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        if not self._visar_fumigacion_selected(selections):
            return request.redirect(self._visar_wizard_next(selections))

        if request.httprequest.method == 'GET':
            ctx = self._visar_wizard_context_base('cobertura', selections=selections, values=post)
            return request.render('visar_appointment.visar_wizard_cobertura', ctx)

        coverage = post.get('cobertura')
        if coverage not in ('interior', 'exterior', 'ambos'):
            ctx = self._visar_wizard_context_base(
                'cobertura', selections=selections,
                error=_('Indica si fumigamos interior, exterior o ambos.'), values=post)
            return request.render('visar_appointment.visar_wizard_cobertura', ctx)

        fum_group = self._visar_coverage_group()
        fum_dim_ids = set(fum_group.dimension_ids.filtered('active').ids) if fum_group else set()
        chosen_dim_ids = self._visar_fum_dimensions_for_coverage(coverage).ids
        # Conserva las dimensiones de otros grupos (p. ej. corte) y fija las de fumigación.
        current = [d for d in (selections.get('dimension_ids') or []) if d not in fum_dim_ids]
        current += chosen_dim_ids
        selections = self._visar_commit_step('cobertura', {
            'cobertura': coverage,
            'dimension_ids': current,
        })
        return request.redirect(self._visar_wizard_next(selections))

    # Paso Interior (Etapa 2): sé mis m² (rango) o los estimo (proxy + terreno opcional).
    @http.route(['/appointment/visar/booking/wizard/interior'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_interior(self, **post):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        sections = self._visar_dimension_sections(selections, measure_type='interior')
        if not sections:
            return request.redirect(self._visar_wizard_next(selections))

        if request.httprequest.method == 'GET':
            ctx = self._visar_wizard_context_base('interior', selections=selections, values=post)
            ctx['dimension_sections'] = sections
            return request.render('visar_appointment.visar_wizard_interior', ctx)

        def _error(msg):
            ctx = self._visar_wizard_context_base(
                'interior', selections=selections, error=msg, values=post)
            ctx['dimension_sections'] = sections
            return request.render('visar_appointment.visar_wizard_interior', ctx)

        mode = post.get('interior_mode')
        updates = {'interior_niveles': post.get('interior_niveles') or ''}

        if mode == 'sabe':
            for section in sections:
                tier_id = post.get(section['field_name'])
                if not tier_id:
                    return _error(_('Selecciona un rango para cada servicio.'))
                updates[section['field_name']] = int(tier_id)
        elif mode == 'estima':
            def _num(key):
                try:
                    return max(int(post.get(key) or 0), 0)
                except (TypeError, ValueError):
                    return 0
            rec, ban, niv, gar = _num('rec'), _num('ban'), max(_num('niv'), 1), _num('gar')
            predio = _num('predio')
            if rec <= 0:
                return _error(_('Indica al menos el número de recámaras.'))
            m2 = request.env['visar.estimator.factor'].sudo()._visar_estimate_interior_m2(
                rec, ban, niv, gar, predio)
            for section in sections:
                tier = self._visar_tier_for_dimension_m2(section['dimension'], m2)
                if not tier:
                    return _error(_('No pudimos estimar el tamaño. Intenta con el rango directo.'))
                updates[section['field_name']] = tier.id
            updates.update({
                'interior_estimado_m2': m2,
                'interior_proxy': {'rec': rec, 'ban': ban, 'niv': niv, 'gar': gar, 'predio': predio},
            })
        else:
            return _error(_('Indica si conoces tus metros cuadrados o si prefieres estimarlos.'))

        selections = self._visar_commit_step('interior', updates)
        return request.redirect(self._visar_wizard_next(selections))

    # Paso Exterior (Etapa 3): medición única del jardín (banda directa o comparativo visual).
    @http.route(['/appointment/visar/booking/wizard/exterior'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_exterior(self, **post):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        exterior_dims = self._visar_dims_by_measure(selections, 'exterior')
        if not exterior_dims:
            return request.redirect(self._visar_wizard_next(selections))

        Band = request.env['visar.measure.band'].sudo()
        bands = Band._visar_exterior_bands()

        if request.httprequest.method == 'GET':
            ctx = self._visar_wizard_context_base('exterior', selections=selections, values=post)
            ctx['measure_bands'] = bands
            ctx['comparative_bands'] = bands.filtered('comparative_label')
            return request.render('visar_appointment.visar_wizard_exterior', ctx)

        def _error(msg):
            ctx = self._visar_wizard_context_base(
                'exterior', selections=selections, error=msg, values=post)
            ctx['measure_bands'] = bands
            ctx['comparative_bands'] = bands.filtered('comparative_label')
            return request.render('visar_appointment.visar_wizard_exterior', ctx)

        band = Band.browse(int(post.get('band_id') or 0)).exists()
        if band not in bands:
            return _error(_('Selecciona el tamaño de tu jardín o exterior.'))

        updates = {
            'exterior_band_id': band.id,
            'exterior_rodea': post.get('exterior_rodea') or '',
        }
        if band.is_valuation:
            updates['requiere_valoracion'] = True
            updates['motivo_valoracion'] = 'area_excede_limite'
        else:
            for dimension in exterior_dims:
                tier = self._visar_tier_for_dimension_m2(dimension, band.m2_ref)
                if not tier:
                    return _error(_('No hay un rango configurado para ese tamaño. Contáctanos.'))
                updates[dimension._visar_tier_field_name()] = tier.id
        selections = self._visar_commit_step('exterior', updates)
        return request.redirect(self._visar_wizard_next(selections))

    # Muestra el aviso de que el servicio seleccionado requiere valoración técnica previa.
    @http.route(['/appointment/visar/booking/wizard/valoracion-aviso'],
                type='http', auth='public', website=True, methods=['GET'], sitemap=False)
    def visar_wizard_valuation_notice(self, **kwargs):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        if not self._visar_selections_require_valuation(selections):
            return request.redirect(self._visar_wizard_next(selections))
        valuation_type = self._visar_valuation_appointment_type()
        if not valuation_type:
            return request.not_found()
        ProductTemplate = request.env['product.template'].sudo()
        valuation_tmpl = ProductTemplate._visar_get_valuation_template()
        currency = (
            valuation_tmpl.currency_id if valuation_tmpl
            else request.env.company.currency_id
        )
        # "Volver" del aviso: regresa al paso que originó el corte para poder cambiarlo.
        if selections.get('motivo_valoracion') in ('termitas', 'chinches', 'plaga_no_identificada'):
            back_step = 'plagas'
        elif self._visar_dims_by_measure(selections, 'exterior'):
            back_step = 'exterior'
        elif self._visar_dims_by_measure(selections, 'interior'):
            back_step = 'interior'
        else:
            back_step = 'dimensiones'
        return request.render('visar_appointment.visar_wizard_valuation_notice', {
            'valuation_product': valuation_tmpl,
            'valuation_price': ProductTemplate._visar_valuation_price(),
            'valuation_currency': currency,
            'valuation_appointment_type': valuation_type,
            'back_url': self._visar_step_url(back_step),
        })

    # Redirige al flujo de valoración al confirmar el aviso desde el wizard.
    @http.route(['/appointment/visar/booking/wizard/valoracion-aviso/continuar'],
                type='http', auth='public', website=True, methods=['POST'], sitemap=False)
    def visar_wizard_valuation_notice_continue(self, **post):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        if not self._visar_selections_require_valuation(selections):
            return request.redirect(self._visar_wizard_next(selections))
        valuation_type = self._visar_valuation_appointment_type()
        if not valuation_type:
            return request.not_found()
        return request.redirect(
            '/appointment/%s/visar/valoracion?from_wizard=1' % valuation_type.id)

    # Consulta AJAX: municipio y zona de un CP para autocompletar el formulario.
    @http.route(['/appointment/visar/cp-info'],
                type='http', auth='public', website=True, methods=['GET'], sitemap=False)
    def visar_cp_info(self, cp=None, **kwargs):
        cp_record = request.env['visar.zone.cp'].sudo()._get_cp_record(cp)
        if cp_record and cp_record.zone_id:
            payload = {
                'found': True,
                'zip': cp_record.name,
                'municipality': cp_record.municipality or '',
                'state': 'Nuevo León',
                'zone_name': cp_record.zone_id.name,
            }
        else:
            payload = {'found': False}
        return request.make_json_response(payload)

    # Muestra (GET) y procesa (POST) el paso de dirección de entrega.
    @http.route(['/appointment/visar/booking/wizard/direccion'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_address(self, **post):
        booking = self._visar_get_booking_session()
        master = self._visar_master_appointment_type()
        if not master:
            return request.not_found()
        selections = booking.get('selections') or {}
        if self._visar_selections_require_valuation(selections):
            return request.redirect('/appointment/visar/booking/wizard/valoracion-aviso')

        if request.httprequest.method == 'GET':
            # Prefill from session so the user can revisit and edit the address.
            values = dict(booking.get('delivery_address') or {})
            values.update({k: v for k, v in (post or {}).items() if v})
            if values.get('zip') and not values.get('city'):
                cp_record = request.env['visar.zone.cp'].sudo()._get_cp_record(values['zip'])
                if cp_record:
                    values['city'] = cp_record.municipality or ''
                    values['state'] = 'Nuevo León'
            return self._visar_render_address(selections, values=values)

        zone, address, error = self._visar_resolve_address_zone(post)
        if error:
            return self._visar_render_address(selections, values=address, error=error)

        AptType = request.env['appointment.type'].sudo()
        items = AptType._visar_resolve_wizard_items(selections)
        if not items:
            return self._visar_render_address(
                selections, values=address,
                error=_('No se pudieron resolver los servicios seleccionados.'))

        pools, missing = AptType._visar_service_resource_pools(zone, items)
        if missing:
            return request.render('visar_appointment.visar_no_resources', {
                'appointment_type': master,
                'zone': zone,
                'missing_services': missing,
            })
        filter_ids = AptType._visar_filter_resource_ids_for_pools(pools)
        booking = self._visar_persist_booking({
            'mode': 'wizard',
            'master_appointment_type_id': master.id,
            'zone_id': zone.id,
            'delivery_address': address,
            'selections': selections,
            'items': items,
            'service_pools': {key: pool.ids for key, pool in pools.items()},
        })
        # Si hay add-ons opcionales para ofrecer, intercala el paso de extras.
        if self._visar_extras_offers(booking):
            return request.redirect('/appointment/visar/booking/wizard/extras')
        filter_param = quote_plus(json.dumps(filter_ids))
        return request.redirect(
            '/appointment/%s?filter_resource_ids=%s' % (master.id, filter_param))

    # Paso Extras (upsell): ofrece add-ons opcionales antes de elegir horario.
    @http.route(['/appointment/visar/booking/wizard/extras'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_extras(self, **post):
        booking = self._visar_get_booking_session()
        master = self._visar_master_appointment_type()
        if not master or not booking or booking.get('mode') != 'wizard' \
                or not booking.get('zone_id') or not booking.get('items'):
            return request.redirect('/appointment/visar/booking')

        AptType = request.env['appointment.type'].sudo()

        def _to_schedule():
            pools = self._visar_get_service_pools(booking)
            filter_ids = AptType._visar_filter_resource_ids_for_pools(pools)
            filter_param = quote_plus(json.dumps(filter_ids))
            return request.redirect(
                '/appointment/%s?filter_resource_ids=%s' % (master.id, filter_param))

        offers = self._visar_extras_offers(booking)
        if not offers:
            return _to_schedule()

        if request.httprequest.method == 'GET':
            zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id'))
            # Sidebar base (sin extras); el total se actualiza en vivo por JS.
            quote = AptType._visar_quote_booking(
                booking.get('items') or [], zone,
                include_roedores=self._visar_booking_has_roedores(booking))
            ctx = self._visar_wizard_context_base(
                'extras', selections=booking.get('selections') or {}, values=post)
            ctx.update({
                'extras_offers': offers,
                'accepted_ids': [e['product_id'] for e in (booking.get('extras_accepted') or [])],
                'visar_quote': quote or False,
            })
            return request.render('visar_appointment.visar_wizard_extras', ctx)

        # POST: guarda los extras aceptados (checkboxes) y sigue al horario.
        chosen = set(self._visar_parse_id_list(request.httprequest.form.getlist('extra_ids')))
        offered_by_id = {o['product_id']: o for o in offers}
        accepted = [
            {'product_id': pid, 'quantity': offered_by_id[pid]['quantity']}
            for pid in chosen if pid in offered_by_id
        ]
        booking = dict(booking)
        booking['extras_accepted'] = accepted
        self._visar_persist_booking(booking)
        return _to_schedule()

    # ------------------------------------------------------------------
    # Valoración técnica
    # ------------------------------------------------------------------
    # Muestra la página de entrada del flujo de valoración técnica con zonas y precio.
    @http.route(['/appointment/<int:appointment_type_id>/visar/valoracion'],
                type='http', auth='public', website=True, sitemap=False)
    def visar_valoracion(self, appointment_type_id, **kwargs):
        appointment_type = request.env['appointment.type'].sudo().browse(appointment_type_id)
        if not appointment_type.exists() or self._visar_resolve_entry_flow(appointment_type) != 'valuation':
            return request.not_found()
        ProductTemplate = request.env['product.template'].sudo()
        valuation_tmpl = ProductTemplate._visar_get_valuation_template()
        currency = (
            valuation_tmpl.currency_id if valuation_tmpl
            else request.env.company.currency_id
        )
        booking = self._visar_get_booking_session()
        values = dict(booking.get('delivery_address') or {})
        values.update({k: v for k, v in kwargs.items() if v})
        if values.get('zip') and not values.get('city'):
            cp_record = request.env['visar.zone.cp'].sudo()._get_cp_record(values['zip'])
            if cp_record:
                values['city'] = cp_record.municipality or ''
                values['state'] = 'Nuevo León'
        return request.render('visar_appointment.visar_valoracion_page', {
            'appointment_type': appointment_type,
            'valuation_product': valuation_tmpl,
            'valuation_price': ProductTemplate._visar_valuation_price(),
            'valuation_currency': currency,
            'from_wizard': self._visar_parse_bool(kwargs.get('from_wizard')),
            'error': kwargs.get('error'),
            'values': values,
        })

    # Procesa la dirección en valoración, deriva la zona del CP y redirige a la agenda.
    @http.route(['/appointment/<int:appointment_type_id>/visar/valoracion/submit'],
                type='http', auth='public', website=True, methods=['POST'], sitemap=False)
    def visar_valoracion_submit(self, appointment_type_id, **kwargs):
        appointment_type = request.env['appointment.type'].sudo().browse(appointment_type_id)
        if not appointment_type.exists() or self._visar_resolve_entry_flow(appointment_type) != 'valuation':
            return request.not_found()
        zone, address, error = self._visar_resolve_address_zone(kwargs)
        if error:
            return self.visar_valoracion(
                appointment_type_id, error=error,
                from_wizard=kwargs.get('from_wizard'), **address,
            )
        eligible = appointment_type._visar_eligible_resources(zone)
        if not eligible:
            return request.render('visar_appointment.visar_no_resources', {
                'appointment_type': appointment_type,
                'zone': zone,
            })
        valuation_tmpl = request.env['product.template']._visar_get_valuation_template()
        variant = valuation_tmpl.product_variant_id if valuation_tmpl else False

        # Si el corte vino del wizard, arrastra el contexto de calificación (motivo,
        # plagas, razón del corte) para que quede registrado en la cita de valoración.
        valuation_selections = {}
        if self._visar_parse_bool(kwargs.get('from_wizard')):
            prior = self._visar_get_booking_session()
            if prior.get('mode') == 'wizard':
                valuation_selections = dict(prior.get('selections') or {})
                reason = self._visar_resolve_valuation_reason(valuation_selections)
                if reason:
                    valuation_selections['motivo_valoracion'] = reason
                valuation_selections['requiere_valoracion'] = True

        self._visar_persist_booking({
            'mode': 'valuation',
            'appointment_type_id': appointment_type_id,
            'zone_id': zone.id,
            'delivery_address': address,
            'selections': valuation_selections,
            'items': [{
                'dimension_id': False,
                'variant_id': variant.id if variant else False,
                'is_valuation': True,
            }],
        })
        filter_param = quote_plus(json.dumps(eligible.ids))
        return request.redirect(
            '/appointment/%s?filter_resource_ids=%s' % (appointment_type_id, filter_param))

    # ------------------------------------------------------------------
    # Slots multi-técnico
    # ------------------------------------------------------------------
    # Filtra los slots para garantizar disponibilidad simultánea de todos los técnicos requeridos.
    def _get_slots_from_filter(self, appointment_type, filter_records, asked_capacity=1):
        result = super()._get_slots_from_filter(appointment_type, filter_records, asked_capacity)
        if not self._visar_wizard_active():
            return result
        booking = self._visar_get_booking_session()
        pools = self._visar_get_service_pools(booking)
        timezone = request.session.get('timezone') or appointment_type.appointment_tz
        filtered = request.env['appointment.type']._visar_filter_slots_multi_service(
            appointment_type, result['slots'], pools, timezone, asked_capacity)
        return {
            'slots': filtered,
            'month_first_available': next(
                (month['id'] for month in filtered if month.get('has_availabilities')), False),
        }

    # Muestra la página de sin-disponibilidad si no hay horarios comunes entre técnicos.
    def _get_appointment_type_page_view(self, appointment_type, page_values, state=False, **kwargs):
        if self._visar_wizard_active() and appointment_type.visar_is_master:
            request.session['timezone'] = self._get_default_timezone(appointment_type)
            asked_capacity = int(kwargs.get('asked_capacity', 1))
            slots_values = self._get_slots_values(
                appointment_type,
                selected_filter_record=page_values['resource_selected'],
                default_filter_record=page_values['resource_default'],
                possible_filter_records=page_values['resources_possible'],
                asked_capacity=asked_capacity,
            )
            if slots_values.get('month_first_available') is False:
                booking = self._visar_get_booking_session()
                zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id'))
                return request.render('visar_appointment.visar_no_common_slots', {
                    'appointment_type': appointment_type,
                    'zone': zone,
                })
        return super()._get_appointment_type_page_view(
            appointment_type, page_values, state, **kwargs)

    # Valida que existan técnicos disponibles para el slot elegido en el wizard multi-servicio.
    def _check_appointment_is_valid_slot(
        self, appointment_type, staff_user_id, resource_selected_id,
        available_resource_ids, start_dt, duration, asked_capacity, **kwargs,
    ):
        if self._visar_wizard_active() and appointment_type.visar_is_master:
            booking = self._visar_get_booking_session()
            pools = self._visar_get_service_pools(booking)
            try:
                duration_f = float(duration)
                asked_capacity_i = int(asked_capacity)
            except (TypeError, ValueError):
                return False
            timezone = request.session.get('timezone') or appointment_type.appointment_tz
            tz_session = pytz.timezone(timezone)
            allday = bool(int(kwargs.get('allday', 0)))
            start_dt = unquote_plus(start_dt)
            start_local = fields.Datetime.from_string(start_dt)
            if allday:
                date_start = pytz.timezone(appointment_type.appointment_tz).localize(
                    start_local).astimezone(pytz.utc).replace(tzinfo=None)
            else:
                date_start = tz_session.localize(start_local).astimezone(pytz.utc).replace(tzinfo=None)
            date_end = date_start + relativedelta(hours=duration_f)
            resources = request.env['appointment.type']._visar_pick_resources_for_slot(
                appointment_type, pools, date_start, date_end, asked_capacity_i)
            return bool(resources)
        return super()._check_appointment_is_valid_slot(
            appointment_type, staff_user_id, resource_selected_id,
            available_resource_ids, start_dt, duration, asked_capacity, **kwargs)

    # Resuelve los recursos disponibles en el slot y delega al submit nativo.
    @http.route(['/appointment/<int:appointment_type_id>/submit'],
                type='http', auth="public", website=True, methods=["POST"], csrf=False)
    def appointment_form_submit(
        self, appointment_type_id, datetime_str, duration_str, name, email,
        staff_user_id=None, available_resource_ids=None, asked_capacity=1,
        guest_emails_str=None, **kwargs,
    ):
        appointment_type = request.env['appointment.type'].sudo().browse(appointment_type_id)
        booking = self._visar_get_booking_session()
        if booking.get('mode') == 'wizard' and appointment_type.visar_is_master:
            timezone = request.session.get('timezone') or appointment_type.appointment_tz
            tz_session = pytz.timezone(timezone)
            allday = bool(int(kwargs.get('allday', 0)))
            datetime_str_parsed = unquote_plus(datetime_str)
            start_dt = fields.Datetime.from_string(datetime_str_parsed)
            if allday:
                date_start = pytz.timezone(appointment_type.appointment_tz).localize(
                    start_dt).astimezone(pytz.utc).replace(tzinfo=None)
            else:
                date_start = tz_session.localize(start_dt).astimezone(pytz.utc).replace(tzinfo=None)
            duration = float(duration_str)
            date_end = date_start + relativedelta(hours=duration)
            pools = self._visar_get_service_pools(booking)
            resources = request.env['appointment.type']._visar_pick_resources_for_slot(
                appointment_type, pools, date_start, date_end, int(asked_capacity))
            if not resources:
                from odoo.addons.base.models.ir_qweb import keep_query
                return request.redirect('/appointment/%s?%s' % (
                    appointment_type.id, keep_query('*', state='failed-resource')))
            available_resource_ids = quote_plus(json.dumps(resources.ids))
        return super().appointment_form_submit(
            appointment_type_id, datetime_str, duration_str, name, email,
            staff_user_id=staff_user_id, available_resource_ids=available_resource_ids,
            asked_capacity=asked_capacity, guest_emails_str=guest_emails_str, **kwargs)

    # Añade respuestas nativas de zona y m² desde la sesión del wizard/valoración.
    def _visar_enrich_answer_inputs(self, appointment_type, booking, answer_input_values, customer):
        if not booking:
            return answer_input_values or [], []
        zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id')).exists()
        if not zone:
            return answer_input_values or [], []

        AptType = request.env['appointment.type'].sudo()
        items = None
        if (
            booking.get('mode') == 'wizard'
            and self._visar_id_eq(booking.get('master_appointment_type_id'), appointment_type.id)
        ):
            items = booking.get('items') or []
        elif (
            booking.get('mode') == 'valuation'
            and booking.get('appointment_type_id') == appointment_type.id
        ):
            pass
        else:
            return answer_input_values or [], []

        visar_inputs = AptType._visar_build_native_answer_inputs(
            appointment_type, zone, items=items, partner_id=customer.id,
            selections=booking.get('selections'),
            delivery_address=booking.get('delivery_address'),
        )
        if not visar_inputs:
            return answer_input_values or [], []

        replace_qids = {inp['question_id'] for inp in visar_inputs}
        merged = [
            vals for vals in (answer_input_values or [])
            if vals.get('question_id') not in replace_qids
        ]
        merged.extend(visar_inputs)
        return merged, visar_inputs

    def _visar_append_answers_to_description(self, description, visar_inputs):
        """Añade Zona/m² al campo description del evento (bloque Questions & Answers)."""
        if not visar_inputs:
            return description
        Question = request.env['appointment.question'].sudo()
        bits = []
        for vals in visar_inputs:
            question = Question.browse(vals.get('question_id')).exists()
            answer = vals.get('value_text_box')
            if not question or not answer:
                continue
            bits.append(Markup('<span>%s - %s</span>') % (question.name, answer))
        if not bits:
            return description
        snippet = Markup('<br/>').join([
            Markup('<br/><strong>%s</strong>') % _('Questions & Answers'),
            Markup('<br/>').join(bits),
        ])
        if description:
            return description + Markup('<br/>') + snippet
        return snippet

    def _visar_append_notes_to_description(self, description, notes):
        """Añade notas de confirmación ligera (niveles, jardín, m² estimados) a la descripción."""
        if not notes:
            return description
        snippet = Markup('<br/>').join([
            Markup('<br/><strong>%s</strong>') % _('Notas de calificación'),
            Markup('<br/>').join(Markup('<span>%s</span>') % note for note in notes),
        ])
        if description:
            return description + Markup('<br/>') + snippet
        return snippet

    # Crea un registro calendar.booking con todos los campos necesarios para el flujo de pago.
    def _visar_create_calendar_booking(
        self, appointment_type, date_start, date_end, description, allday,
        answer_input_values, name, customer, appointment_invite, guests=None,
        staff_user=None, asked_capacity=1, booking_line_values=None,
    ):
        return request.env['calendar.booking'].sudo().create([{
            'allday': bool(allday),
            'appointment_answer_input_ids': [
                Command.create(vals) for vals in (answer_input_values or [])
            ],
            'appointment_invite_id': appointment_invite.id,
            'appointment_type_id': appointment_type.id,
            'asked_capacity': asked_capacity,
            'booking_line_ids': [
                Command.create(vals) for vals in (booking_line_values or [])
            ],
            'description': description,
            'guest_ids': [Command.link(pid) for pid in guests.ids] if guests else [],
            'name': name,
            'partner_id': customer.id,
            'product_id': appointment_type.product_id.id,
            'staff_user_id': staff_user.id if staff_user else False,
            'start': date_start,
            'stop': date_end,
        }])

    # Añade zona e items al evento de calendario y gestiona el flujo de pago Visar.
    def _handle_appointment_form_submission(
        self, appointment_type,
        date_start, date_end, description, duration, allday,
        answer_input_values, name, customer, appointment_invite, guests=None,
        staff_user=None, asked_capacity=1, booking_line_values=None,
        extra_calendar_event_params=None,
    ):
        booking = self._visar_get_booking_session()
        extra_calendar_event_params = dict(extra_calendar_event_params or {})
        if booking and booking.get('mode') == 'wizard' \
                and self._visar_id_eq(booking.get('master_appointment_type_id'), appointment_type.id):
            extra_calendar_event_params['visar_zone_id'] = booking.get('zone_id')
            extra_calendar_event_params['visar_booking_items'] = \
                request.env['appointment.type']._visar_items_snapshot(booking.get('items', []))
        elif booking and booking.get('mode') == 'valuation' \
                and self._visar_id_eq(booking.get('appointment_type_id'), appointment_type.id):
            extra_calendar_event_params['visar_zone_id'] = booking.get('zone_id')
            extra_calendar_event_params['visar_booking_items'] = \
                request.env['appointment.type']._visar_items_snapshot(booking.get('items', []))

        answer_input_values, visar_inputs = self._visar_enrich_answer_inputs(
            appointment_type, booking, answer_input_values, customer)
        description = self._visar_append_answers_to_description(description, visar_inputs)
        if booking:
            notes = request.env['appointment.type'].sudo()._visar_calification_notes(
                booking.get('selections'))
            description = self._visar_append_notes_to_description(description, notes)

        wizard_booking = self._visar_resolve_wizard_payment_booking(booking, appointment_type)
        visar_wizard_payment = bool(wizard_booking)
        visar_valuation_payment = (
            booking
            and booking.get('mode') == 'valuation'
            and self._visar_id_eq(booking.get('appointment_type_id'), appointment_type.id)
            and appointment_type.has_payment_step
        )
        if visar_wizard_payment or visar_valuation_payment:
            if wizard_booking:
                booking = wizard_booking
            calendar_booking = self._visar_create_calendar_booking(
                appointment_type, date_start, date_end, description, allday,
                answer_input_values, name, customer, appointment_invite, guests=guests,
                staff_user=staff_user, asked_capacity=asked_capacity,
                booking_line_values=booking_line_values,
            )
            response = self._redirect_to_payment(calendar_booking)
            request.session.pop(SESSION_KEY, None)
            return response

        if appointment_type.visar_is_master and appointment_type.has_payment_step:
            from odoo.addons.base.models.ir_qweb import keep_query
            return request.redirect('/appointment/%s?%s' % (
                appointment_type.id, keep_query('*', state='failed-resource')))

        response = super()._handle_appointment_form_submission(
            appointment_type, date_start, date_end, description, duration, allday,
            answer_input_values, name, customer, appointment_invite, guests=guests,
            staff_user=staff_user, asked_capacity=asked_capacity,
            booking_line_values=booking_line_values,
            extra_calendar_event_params=extra_calendar_event_params,
        )
        request.session.pop(SESSION_KEY, None)
        return response

    # Crea (o reutiliza) un contacto de entrega tipo 'delivery' con la dirección
    # capturada y la fija como dirección de servicio Visar (no la pisa el checkout).
    def _visar_apply_delivery_address(self, order_sudo, booking, partner_name=None):
        address = (booking or {}).get('delivery_address') or {}
        if not address or not order_sudo.partner_id:
            return
        Partner = request.env['res.partner'].sudo()
        commercial = order_sudo.partner_id.commercial_partner_id
        country = request.env.ref('base.mx', raise_if_not_found=False)
        state = request.env['res.country.state'].sudo().search([
            ('country_id', '=', country.id), ('code', '=', 'NL'),
        ], limit=1) if country else request.env['res.country.state'].sudo()

        street = (address.get('street') or '').strip()
        ext_num = (address.get('ext_num') or '').strip()
        int_num = (address.get('int_num') or '').strip()
        if ext_num:
            street = ('%s No. %s' % (street, ext_num)).strip()
        if int_num:
            street = ('%s Int. %s' % (street, int_num)).strip()

        vals = {
            'name': partner_name or order_sudo.partner_id.name or _('Dirección de servicio'),
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
        order_sudo._visar_set_service_shipping(delivery_partner)

    # Elimina del carrito las líneas de reservas Visar anteriores, para que rehacer
    # el wizard REEMPLACE la cita en lugar de acumular líneas duplicadas. Solo toca
    # líneas ligadas a un calendar.booking de un tipo de cita Visar (maestro o
    # valoración); cualquier otro producto del carrito se conserva.
    def _visar_clear_previous_booking_lines(self, order, keep_booking=None):
        if not order:
            return
        AptType = request.env['appointment.type'].sudo()
        visar_types = (AptType._visar_get_master_appointment_type()
                       | AptType._visar_get_valuation_appointment_type())
        if not visar_types:
            return
        stale = order.order_line.filtered(
            lambda l: l.calendar_booking_ids
            and (l.calendar_booking_ids.appointment_type_id & visar_types)
            and (not keep_booking or keep_booking not in l.calendar_booking_ids)
        )
        if stale:
            stale.sudo().unlink()

    # Construye el carrito multi-servicio del wizard y redirige a /shop/cart.
    def _visar_fill_wizard_cart_and_redirect(self, calendar_booking, booking):
        from odoo.addons.base.models.ir_qweb import keep_query

        order_sudo = request.cart or request.website._create_cart()
        self._visar_clear_previous_booking_lines(order_sudo, keep_booking=calendar_booking)
        zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id'))
        order_sudo._visar_apply_zone_pricelist(zone)

        master = request.env['appointment.type'].sudo().browse(booking['master_appointment_type_id'])
        sale_lines = master._visar_build_sale_lines(
            booking.get('items', []), zone,
            include_roedores=self._visar_booking_has_roedores(booking),
            extra_addons=booking.get('extras_accepted'))
        if not sale_lines:
            calendar_booking.sudo().unlink()
            return request.redirect('/appointment/%s?%s' % (
                master.id, keep_query('*', state='failed-resource')))

        tz = (request.session.get('timezone') or
              request.env.context.get('tz') or
              calendar_booking.appointment_type_id.appointment_tz)
        quantity = calendar_booking.asked_capacity or 1
        lines_added = 0

        for line_vals in sale_lines:
            if master._visar_skip_cart_line(line_vals, zone):
                continue
            line_qty = line_vals.get('quantity', quantity)
            cart_values = order_sudo._cart_add(
                product_id=line_vals['product_id'],
                quantity=line_qty,
                calendar_booking_id=calendar_booking.id,
                calendar_booking_tz=tz,
            )
            if cart_values.get('quantity', 0) < line_qty:
                calendar_booking.sudo().unlink()
                return request.redirect('/appointment/%s?%s' % (
                    master.id, keep_query('*', state='failed-resource')))
            lines_added += 1
            discount = line_vals.get('discount') or 0.0
            if discount:
                sol = order_sudo.order_line.filtered(
                    lambda line: line.product_id.id == line_vals['product_id']
                    and calendar_booking in line.calendar_booking_ids
                )[-1:]
                if sol:
                    sol.write({'discount': discount})

        if not lines_added:
            calendar_booking.sudo().unlink()
            return request.redirect('/appointment/%s?%s' % (
                master.id, keep_query('*', state='failed-resource')))

        if order_sudo._is_anonymous_cart():
            partner_values = {
                'name': calendar_booking.name,
                'email': calendar_booking.partner_id.email,
                'phone': calendar_booking.partner_id.phone,
            }
            booked_by_partner, feedback_dict = CustomerPortal()._create_or_update_address(
                request.env['res.partner'].sudo(),
                order_sudo=order_sudo,
                verify_address_values=False,
                **partner_values,
            )
            if not feedback_dict.get('invalid_fields'):
                order_sudo._update_address(booked_by_partner.id, ['partner_id'])

        self._visar_apply_delivery_address(
            order_sudo, booking, partner_name=calendar_booking.name)
        return request.redirect("/shop/cart")

    # Construye el carrito con líneas multi-servicio y redirige al checkout de pago.
    def _redirect_to_payment(self, calendar_booking):
        booking = self._visar_get_booking_session()
        if booking and booking.get('mode') == 'valuation':
            order_sudo = request.cart or request.website._create_cart()
            self._visar_clear_previous_booking_lines(order_sudo, keep_booking=calendar_booking)
            zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id'))
            order_sudo._visar_apply_zone_pricelist(zone)
            items = booking.get('items') or []
            variant_id = items[0].get('variant_id') if items else False
            if not variant_id:
                valuation_tmpl = request.env['product.template']._visar_get_valuation_template()
                variant_id = valuation_tmpl.product_variant_id.id if valuation_tmpl else False
            if not variant_id:
                calendar_booking.sudo().unlink()
                from odoo.addons.base.models.ir_qweb import keep_query
                return request.redirect('/appointment/%s?%s' % (
                    booking.get('appointment_type_id'),
                    keep_query('*', state='failed-resource'),
                ))
            tz = (request.session.get('timezone') or
                  request.env.context.get('tz') or
                  calendar_booking.appointment_type_id.appointment_tz)
            quantity = calendar_booking.asked_capacity or 1
            cart_values = order_sudo._cart_add(
                product_id=variant_id,
                quantity=quantity,
                calendar_booking_id=calendar_booking.id,
                calendar_booking_tz=tz,
            )
            if cart_values.get('quantity', 0) < quantity:
                calendar_booking.sudo().unlink()
                from odoo.addons.base.models.ir_qweb import keep_query
                return request.redirect('/appointment/%s?%s' % (
                    booking.get('appointment_type_id'),
                    keep_query('*', state='failed-resource'),
                ))
            if order_sudo._is_anonymous_cart():
                partner_values = {
                    'name': calendar_booking.name,
                    'email': calendar_booking.partner_id.email,
                    'phone': calendar_booking.partner_id.phone,
                }
                booked_by_partner, feedback_dict = CustomerPortal()._create_or_update_address(
                    request.env['res.partner'].sudo(),
                    order_sudo=order_sudo,
                    verify_address_values=False,
                    **partner_values,
                )
                if not feedback_dict.get('invalid_fields'):
                    order_sudo._update_address(booked_by_partner.id, ['partner_id'])
            self._visar_apply_delivery_address(
                order_sudo, booking, partner_name=calendar_booking.name)
            return request.redirect("/shop/cart")

        apt_type = calendar_booking.appointment_type_id
        wizard_booking = self._visar_resolve_wizard_payment_booking(booking, apt_type)
        if wizard_booking:
            return self._visar_fill_wizard_cart_and_redirect(calendar_booking, wizard_booking)

        if apt_type.visar_is_master:
            from odoo.addons.base.models.ir_qweb import keep_query
            calendar_booking.sudo().unlink()
            return request.redirect('/appointment/%s?%s' % (
                apt_type.id, keep_query('*', state='failed-resource')))

        return super()._redirect_to_payment(calendar_booking)
