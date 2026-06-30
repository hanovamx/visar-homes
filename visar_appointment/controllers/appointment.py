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
        for key in ('zone_id', 'appointment_type_id', 'm2', 'items', 'service_pools'):
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
            include_roedores=self._visar_booking_has_roedores(booking))
        return {'visar_quote': quote or False}

    # Obtiene el appointment.type maestro del wizard.
    def _visar_master_appointment_type(self):
        return request.env['appointment.type'].sudo()._visar_get_master_appointment_type()

    # Obtiene el appointment.type de entrada para el flujo de valoración técnica.
    def _visar_valuation_appointment_type(self):
        return request.env['appointment.type'].sudo()._visar_get_valuation_appointment_type()

    # True si algún item del wizard resuelto requiere visita de valoración técnica.
    def _visar_selections_require_valuation(self, selections):
        items = request.env['appointment.type'].sudo()._visar_resolve_wizard_items(selections)
        return any(item.get('is_valuation') for item in items)

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

    # True si entre los grupos elegidos está fumigación (dispara el paso de calificación).
    def _visar_fumigacion_selected(self, selections):
        return any(g.code == 'fumigacion' for g in self._visar_selected_groups(selections))

    # True si el cliente declaró problema de roedores en el paso de calificación.
    def _visar_booking_has_roedores(self, booking):
        return (booking.get('selections') or {}).get('roedores') == 'si'

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
        """Primer grupo seleccionado que aún no tiene dimensiones elegidas."""
        selected_groups = self._visar_selected_groups(selections)
        dimension_ids = set(selections.get('dimension_ids') or [])
        for group in selected_groups.sorted('sequence'):
            if not self._visar_group_needs_substep(group):
                continue
            group_dim_ids = set(group.dimension_ids.filtered('active').ids)
            if not group_dim_ids.intersection(dimension_ids):
                return group
        return request.env['visar.service.group']

    def _visar_dimension_sections(self, selections):
        """Secciones del paso de tramos por dimensión elegida."""
        ProductTemplate = request.env['product.template'].sudo()
        Dimension = request.env['visar.service.dimension'].sudo()
        sections = []
        for dimension in self._visar_selection_dimension_ids(selections):
            template = ProductTemplate._visar_get_service_template_for_dimension(dimension)
            if not template:
                continue
            sections.append({
                'dimension': dimension,
                'dimension_id': dimension.id,
                'label': dimension._visar_wizard_label(),
                'field_name': dimension._visar_tier_field_name(),
                'tiers': template.visar_tier_ids.sorted('sequence'),
            })
        return sections

    # Delega en el modelo para obtener las dimensiones activas de las selecciones actuales.
    def _visar_selection_dimension_ids(self, selections):
        return request.env['appointment.type'].sudo()._visar_selection_dimension_ids(selections)

    def _visar_wizard_step_count(self, selections=None):
        """Pasos: grupos + substeps + dimensiones + calificación (fumigación) + zona."""
        selections = selections or {}
        groups = self._visar_selected_groups(selections) or self._visar_wizard_groups()
        substeps = sum(1 for g in groups if self._visar_group_needs_substep(g))
        calification = 1 if self._visar_fumigacion_selected(selections) else 0
        return 1 + substeps + 1 + calification + 1

    # Renderiza el paso de zona (último paso) calculando su número según calificación.
    def _visar_render_zona(self, selections, values=None, error=None):
        step = 5 if self._visar_fumigacion_selected(selections) else 4
        ctx = self._visar_wizard_context_base(
            step, selections=selections, error=error, values=values or {})
        ctx['zones'] = request.env['visar.zone'].sudo().search([])
        return request.render('visar_appointment.visar_wizard_zona', ctx)

    # Construye el dict de contexto base común a todos los pasos del wizard.
    def _visar_wizard_context_base(self, step, selections=None, error=None, values=None):
        return {
            'wizard_groups': self._visar_wizard_groups(),
            'wizard_step': step,
            'wizard_total_steps': self._visar_wizard_step_count(selections),
            'error': error,
            'values': values or {},
            'selections': selections or {},
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
            return request.redirect('/appointment/visar/booking')
        if entry_flow == 'valuation':
            if not self._visar_valuation_done(appointment_type_id, kwargs):
                return request.redirect(
                    '/appointment/%s/visar/valoracion' % appointment_type_id)
            return super().appointment_type_page(appointment_type_id, **kwargs)
        if appointment_type.visar_is_master and not self._visar_wizard_done(appointment_type_id, kwargs):
            return request.redirect('/appointment/visar/booking')
        return super().appointment_type_page(appointment_type_id, **kwargs)

    # ------------------------------------------------------------------
    # Wizard — rutas dinámicas
    # ------------------------------------------------------------------
    # Inicia el wizard, inicializa la sesión y muestra el paso 1 de selección de servicios.
    @http.route(['/appointment/visar/booking'],
                type='http', auth='public', website=True, sitemap=False)
    def visar_wizard_start(self, **kwargs):
        master = self._visar_init_wizard_session()
        if not master:
            return request.not_found()
        ctx = self._visar_wizard_context_base(1, values=kwargs)
        ctx['error'] = kwargs.get('error')
        return request.render('visar_appointment.visar_wizard_services', ctx)

    # Procesa el POST del paso 1 y avanza al siguiente sub-paso o al paso de dimensiones.
    @http.route(['/appointment/visar/booking/wizard/services'],
                type='http', auth='public', website=True, methods=['POST'], sitemap=False)
    def visar_wizard_services(self, **post):
        master = self._visar_master_appointment_type()
        if not master:
            return request.not_found()
        group_ids = self._visar_form_id_list('group_ids')
        groups = request.env['visar.service.group'].sudo().browse(group_ids).exists()
        if not groups:
            ctx = self._visar_wizard_context_base(1, error=_('Selecciona al menos un servicio.'), values=post)
            return request.render('visar_appointment.visar_wizard_services', ctx)

        dimension_ids = self._visar_auto_dimensions_for_groups(groups, [])
        selections = {
            'group_ids': groups.ids,
            'dimension_ids': dimension_ids,
        }
        self._visar_update_selections(selections)

        next_group = self._visar_next_group_substep(selections)
        if next_group:
            return request.redirect('/appointment/visar/booking/wizard/group/%s' % next_group.id)
        return request.redirect('/appointment/visar/booking/wizard/dimensiones')

    # Muestra y procesa el sub-paso de dimensiones para un grupo con múltiples opciones.
    @http.route(['/appointment/visar/booking/wizard/group/<int:group_id>'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_group_dimensions(self, group_id, **post):
        booking = self._visar_get_booking_session()
        selections = booking.get('selections') or {}
        group = request.env['visar.service.group'].sudo().browse(group_id).exists()
        if not group or group.id not in (selections.get('group_ids') or []):
            return request.redirect('/appointment/visar/booking')

        if request.httprequest.method == 'GET':
            ctx = self._visar_wizard_context_base(2, selections=selections, values=post)
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
                2, selections=selections, error=_('Selecciona al menos una opción.'), values=post)
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
        selections['dimension_ids'] = list(current)
        self._visar_update_selections(selections)

        next_group = self._visar_next_group_substep(selections)
        if next_group:
            return request.redirect('/appointment/visar/booking/wizard/group/%s' % next_group.id)
        return request.redirect('/appointment/visar/booking/wizard/dimensiones')

    # Muestra y procesa el paso de selección de tramos (m²) por cada dimensión elegida.
    @http.route(['/appointment/visar/booking/wizard/dimensiones'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_dimensiones(self, **post):
        booking = self._visar_get_booking_session()
        selections = booking.get('selections') or {}
        sections = self._visar_dimension_sections(selections)
        if request.httprequest.method == 'GET':
            if not sections:
                return request.redirect('/appointment/visar/booking')
            ctx = self._visar_wizard_context_base(3, selections=selections, values=post)
            ctx['dimension_sections'] = sections
            return request.render('visar_appointment.visar_wizard_dimensiones', ctx)

        tier_updates = {}
        for section in sections:
            tier_id = post.get(section['field_name'])
            if not tier_id:
                ctx = self._visar_wizard_context_base(
                    3, selections=selections,
                    error=_('Selecciona un rango para cada servicio.'), values=post)
                ctx['dimension_sections'] = sections
                return request.render('visar_appointment.visar_wizard_dimensiones', ctx)
            tier_updates[section['field_name']] = int(tier_id)
        self._visar_update_selections(tier_updates)
        booking = self._visar_get_booking_session()
        selections = booking.get('selections') or {}
        if self._visar_selections_require_valuation(selections):
            return request.redirect('/appointment/visar/booking/wizard/valoracion-aviso')

        if self._visar_fumigacion_selected(selections):
            return request.redirect('/appointment/visar/booking/wizard/calificacion')
        return self._visar_render_zona(selections, values=post)

    # Muestra y procesa el paso de calificación (plaga/preventivo, roedores, tipo de plaga).
    @http.route(['/appointment/visar/booking/wizard/calificacion'],
                type='http', auth='public', website=True, methods=['GET', 'POST'], sitemap=False)
    def visar_wizard_calificacion(self, **post):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        if not self._visar_fumigacion_selected(selections):
            return self._visar_render_zona(selections, values=post)

        if request.httprequest.method == 'GET':
            ctx = self._visar_wizard_context_base(4, selections=selections, values=post)
            return request.render('visar_appointment.visar_wizard_calificacion', ctx)

        plaga = post.get('plaga')
        roedores = post.get('roedores')
        if plaga not in ('preventivo', 'plaga') or roedores not in ('si', 'no'):
            ctx = self._visar_wizard_context_base(
                4, selections=selections,
                error=_('Responde si tienes plaga o es preventivo, y si tienes problema de roedores.'),
                values=post)
            return request.render('visar_appointment.visar_wizard_calificacion', ctx)

        valid_tipos = request.env['appointment.type']._VISAR_TIPO_PLAGA_LABELS.keys()
        tipo_plaga = [
            t for t in request.httprequest.form.getlist('tipo_plaga')
            if t in valid_tipos
        ] if plaga == 'plaga' else []

        self._visar_update_selections({
            'plaga': plaga,
            'roedores': roedores,
            'tipo_plaga': tipo_plaga,
        })
        booking = self._visar_get_booking_session()
        selections = booking.get('selections') or {}
        return self._visar_render_zona(selections, values=post)

    # Muestra el aviso de que el servicio seleccionado requiere valoración técnica previa.
    @http.route(['/appointment/visar/booking/wizard/valoracion-aviso'],
                type='http', auth='public', website=True, methods=['GET'], sitemap=False)
    def visar_wizard_valuation_notice(self, **kwargs):
        booking = self._visar_get_booking_session()
        if not booking or booking.get('mode') != 'wizard':
            return request.redirect('/appointment/visar/booking')
        selections = booking.get('selections') or {}
        if not self._visar_selections_require_valuation(selections):
            return request.redirect('/appointment/visar/booking/wizard/dimensiones')
        valuation_type = self._visar_valuation_appointment_type()
        if not valuation_type:
            return request.not_found()
        ProductTemplate = request.env['product.template'].sudo()
        valuation_tmpl = ProductTemplate._visar_get_valuation_template()
        currency = (
            valuation_tmpl.currency_id if valuation_tmpl
            else request.env.company.currency_id
        )
        return request.render('visar_appointment.visar_wizard_valuation_notice', {
            'valuation_product': valuation_tmpl,
            'valuation_price': ProductTemplate._visar_valuation_price(),
            'valuation_currency': currency,
            'valuation_appointment_type': valuation_type,
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
            return request.redirect('/appointment/visar/booking/wizard/dimensiones')
        valuation_type = self._visar_valuation_appointment_type()
        if not valuation_type:
            return request.not_found()
        return request.redirect(
            '/appointment/%s/visar/valoracion?from_wizard=1' % valuation_type.id)

    # Procesa la zona elegida, verifica pools de recursos y redirige a la agenda.
    @http.route(['/appointment/visar/booking/wizard/zona'],
                type='http', auth='public', website=True, methods=['POST'], sitemap=False)
    def visar_wizard_zona(self, zone_id=None, **post):
        booking = self._visar_get_booking_session()
        master = self._visar_master_appointment_type()
        if not master:
            return request.not_found()
        zone = request.env['visar.zone'].sudo().browse(int(zone_id)) if zone_id else request.env['visar.zone']
        selections = booking.get('selections') or {}
        if self._visar_selections_require_valuation(selections):
            return request.redirect('/appointment/visar/booking/wizard/valoracion-aviso')
        if not zone:
            ctx = self._visar_wizard_context_base(
                4, selections=selections, error=_('Selecciona una zona.'), values=post)
            ctx['zones'] = request.env['visar.zone'].sudo().search([])
            return request.render('visar_appointment.visar_wizard_zona', ctx)

        AptType = request.env['appointment.type'].sudo()
        items = AptType._visar_resolve_wizard_items(selections)
        if not items:
            ctx = self._visar_wizard_context_base(
                4, selections=selections,
                error=_('No se pudieron resolver los servicios seleccionados.'), values=post)
            ctx['zones'] = request.env['visar.zone'].sudo().search([])
            return request.render('visar_appointment.visar_wizard_zona', ctx)

        pools, missing = AptType._visar_service_resource_pools(zone, items)
        if missing:
            return request.render('visar_appointment.visar_no_resources', {
                'appointment_type': master,
                'zone': zone,
                'missing_services': missing,
            })
        filter_ids = AptType._visar_filter_resource_ids_for_pools(pools)
        self._visar_persist_booking({
            'mode': 'wizard',
            'master_appointment_type_id': master.id,
            'zone_id': zone.id,
            'selections': selections,
            'items': items,
            'service_pools': {key: pool.ids for key, pool in pools.items()},
        })
        filter_param = quote_plus(json.dumps(filter_ids))
        return request.redirect(
            '/appointment/%s?filter_resource_ids=%s' % (master.id, filter_param))

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
        return request.render('visar_appointment.visar_valoracion_page', {
            'appointment_type': appointment_type,
            'zones': request.env['visar.zone'].sudo().search([]),
            'valuation_product': valuation_tmpl,
            'valuation_price': ProductTemplate._visar_valuation_price(),
            'valuation_currency': currency,
            'from_wizard': self._visar_parse_bool(kwargs.get('from_wizard')),
            'error': kwargs.get('error'),
            'values': kwargs,
        })

    # Procesa la zona elegida en valoración, guarda sesión y redirige a la agenda.
    @http.route(['/appointment/<int:appointment_type_id>/visar/valoracion/submit'],
                type='http', auth='public', website=True, methods=['POST'], sitemap=False)
    def visar_valoracion_submit(self, appointment_type_id, zone_id=None, **kwargs):
        appointment_type = request.env['appointment.type'].sudo().browse(appointment_type_id)
        if not appointment_type.exists() or self._visar_resolve_entry_flow(appointment_type) != 'valuation':
            return request.not_found()
        zone = request.env['visar.zone'].sudo().browse(int(zone_id)) if zone_id else None
        if not zone:
            return self.visar_valoracion(
                appointment_type_id,
                error=_('Selecciona tu zona geográfica.'),
                zone_id=zone_id,
            )
        eligible = appointment_type._visar_eligible_resources(zone)
        if not eligible:
            return request.render('visar_appointment.visar_no_resources', {
                'appointment_type': appointment_type,
                'zone': zone,
            })
        valuation_tmpl = request.env['product.template']._visar_get_valuation_template()
        variant = valuation_tmpl.product_variant_id if valuation_tmpl else False
        self._visar_persist_booking({
            'mode': 'valuation',
            'appointment_type_id': appointment_type_id,
            'zone_id': zone.id,
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

    # Construye el carrito multi-servicio del wizard y redirige a /shop/cart.
    def _visar_fill_wizard_cart_and_redirect(self, calendar_booking, booking):
        from odoo.addons.base.models.ir_qweb import keep_query

        order_sudo = request.cart or request.website._create_cart()
        zone = request.env['visar.zone'].sudo().browse(booking.get('zone_id'))
        order_sudo._visar_apply_zone_pricelist(zone)

        master = request.env['appointment.type'].sudo().browse(booking['master_appointment_type_id'])
        sale_lines = master._visar_build_sale_lines(
            booking.get('items', []), zone,
            include_roedores=self._visar_booking_has_roedores(booking))
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

        return request.redirect("/shop/cart")

    # Construye el carrito con líneas multi-servicio y redirige al checkout de pago.
    def _redirect_to_payment(self, calendar_booking):
        booking = self._visar_get_booking_session()
        if booking and booking.get('mode') == 'valuation':
            order_sudo = request.cart or request.website._create_cart()
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
