# -*- coding: utf-8 -*-
"""El flujo del wizard de reserva, sin HTTP.

Hasta ahora estas reglas —qué se invalida al cambiar un paso, qué paso viene
después, qué opciones son válidas y cómo se normaliza una respuesta— vivían en
el controlador web (`controllers/appointment.py`), atadas a `request.session`.

El agente de WhatsApp conduce **el mismo cuestionario** por RPC. Sin bajarlas
aquí tendría que reimplementarlas, y serían tres copias de la misma regla
divergiendo en cuanto alguien tocara una. Es el riesgo estructural del diseño 33
§11 ("dos front-ends, un flujo"), y ya se manifestó una vez: el web cobra 2,400
donde la cotización dice 1,900 (I-11).

**El estado entra y sale por parámetro.** `booking` es el mismo dict que el
controlador guardaba en la sesión:

    {'mode': 'wizard', 'master_appointment_type_id': int,
     'selections': {...}, 'zone_id': int, 'items': [...],
     'delivery_address': {...}, 'extras_accepted': [...]}

El controlador lo persiste en la sesión HTTP; el runtime de WhatsApp lo persiste
en su conversación. Ninguno de los dos decide nada: preguntan.

Tres cosas que NO son evidentes:

* **No se expone la tabla de dependencias, se expone la operación.**
  `_VISAR_CLEARS_TIERS` añade una regla de prefijo (`tier_*`) que no está en el
  dict; publicar el dict obligaría a reimplementar esa regla del otro lado, que
  es justo la divergencia que esto viene a cerrar.
* **La secuencia también es regla de negocio.** Saber que "plagas" va después de
  "motivo", y que un corte a valoración se salta las mediciones, es tan
  duplicable como los precios.
* **La normalización de la respuesta también.** "Protección general" activa las
  tres categorías; "termitas" corta a valoración. Si el runtime arma
  `selections` por su cuenta, esas reglas se pierden.
"""
from odoo import _, api, models
from odoo.tools import format_amount

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
# La póliza se cotiza sobre los items resueltos: cualquier paso que los cambie
# invalida el plan elegido, o se cobraría el precio de otra configuración.
_VISAR_POLIZA_KEYS = ('poliza_plan_id',)
_VISAR_STEP_CLEARS = {
    'services': ('motivo',) + _VISAR_PLAGA_KEYS + ('cobertura',)
                + _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS + _VISAR_POLIZA_KEYS,
    'motivo': _VISAR_PLAGA_KEYS,
    'plagas': _VISAR_PLAGA_KEYS + _VISAR_POLIZA_KEYS,
    'cobertura': _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS
                 + _VISAR_POLIZA_KEYS,
    'group': _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS
             + _VISAR_POLIZA_KEYS,
    'interior': _VISAR_INTERIOR_KEYS + _VISAR_POLIZA_KEYS,
    'exterior': _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS + _VISAR_POLIZA_KEYS,
    'dimensiones': _VISAR_POLIZA_KEYS,
}
_VISAR_CLEARS_TIERS = ('services', 'cobertura', 'group')

# Categorías de plaga que SÍ se atienden con el tabulador (no cortan a valoración).
VISAR_PLAGA_CATEGORIES = ('rastreros', 'voladores', 'roedores')

# Opciones que cortan a valoración, y con qué motivo. Solo en la rama correctiva:
# en preventivo el cliente no está reportando una plaga, está contratando protección.
VISAR_PLAGA_CUTS = (
    ('termitas', 'termitas'),
    ('chinches', 'chinches'),
    ('no_se', 'plaga_no_identificada'),
)

# Claves de paso que el flujo puede devolver como "el siguiente".
VISAR_STEP_SERVICES = 'services'
VISAR_STEP_VALUATION = 'valuation'
VISAR_STEP_ADDRESS = 'address'
VISAR_STEP_NAME = 'nombre'
VISAR_STEP_EXTRAS = 'extras'
VISAR_STEP_POLIZA = 'poliza'
VISAR_STEP_SCHEDULE = 'schedule'

# Valor de la opcion "no quiero poliza". Cero porque no puede ser el id de ningun
# plan, y porque `_visar_wizard_id_list` ya lo descarta: la respuesta acaba en
# `poliza_plan_id = False`, exactamente igual que si el cliente no eligiera nada.
VISAR_POLIZA_NONE = 0

# Periodicidad en espanol, por (unidad, singular/plural). El campo nativo
# `billing_period_display_sentence` NO sirve para el chat: su fuente es inglesa
# ("per month") y se traduce con el idioma del USUARIO RPC, que es en_US. Aqui no
# se depende de traducciones para un texto que ve el cliente.
# Etiquetas CORTAS de cada paso, para el menú de "quiero cambiar algo". No sirve
# el título del paso: son preguntas ("¿Qué servicio necesitas?") y una fila de
# WhatsApp son 24 caracteres. Sin `_()` por lo mismo que las de abajo.
_VISAR_STEP_LABELS = {
    'services': "Servicio",
    'motivo': "Preventivo o correctivo",
    'plagas': "Plagas",
    'cobertura': "Interior o exterior",
    'interior': "Medidas de interior",
    'exterior': "Medidas de exterior",
    'dimensiones': "Medidas",
    'address': "Dirección",
    'nombre': "Nombre",
    'extras': "Extras",
    'poliza': "Póliza",
}

# Sin `_()`: a nivel de modulo se evaluaria una sola vez, al importar, con el
# idioma que hubiera entonces. Son literales en el idioma en el que se habla con
# el cliente, que es el mismo en los dos canales.
_VISAR_PERIOD_LABELS = {
    'week': ("a la semana", "cada %s semanas"),
    'month': ("al mes", "cada %s meses"),
    'year': ("al año", "cada %s años"),
}


class AppointmentType(models.Model):
    _inherit = 'appointment.type'

    # ------------------------------------------------------------------
    # Estado: podar y fusionar
    # ------------------------------------------------------------------

    @api.model
    def _visar_wizard_clear_downstream(self, selections, step_key):
        """Selecciones que sobreviven al (re)enviar `step_key`.

        La regla de los tramos va por PREFIJO (`tier_*`) y no está en
        `_VISAR_STEP_CLEARS`: por eso lo que se publica hacia fuera es esta
        operación y no la tabla.
        """
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

    @api.model
    def _visar_wizard_commit(self, booking, step_key, updates):
        """Aplica la respuesta de un paso y devuelve un booking NUEVO.

        Puro: no toca sesión ni base. El llamador decide dónde persistirlo.
        """
        booking = dict(booking or {})
        if not booking.get('mode'):
            master = self._visar_get_master_appointment_type()
            booking['mode'] = 'wizard'
            booking['master_appointment_type_id'] = master.id if master else False
        selections = self._visar_wizard_clear_downstream(
            booking.get('selections') or {}, step_key)
        selections.update(updates or {})
        booking['selections'] = selections
        return booking

    # ------------------------------------------------------------------
    # Lecturas del catálogo que el flujo necesita
    # ------------------------------------------------------------------

    @api.model
    def _visar_wizard_groups(self):
        """Grupos de servicio ofrecibles en el paso 1."""
        return self.env['visar.service.group'].sudo().search([
            ('active', '=', True),
            ('show_in_wizard', '=', True),
        ])

    @api.model
    def _visar_wizard_selected_groups(self, selections):
        Group = self.env['visar.service.group'].sudo()
        return Group.browse((selections or {}).get('group_ids') or []).exists()

    @api.model
    def _visar_wizard_fumigacion_selected(self, selections):
        """True si entre los grupos elegidos está fumigación (dispara motivo/plagas/cobertura)."""
        return any(g.code == 'fumigacion'
                   for g in self._visar_wizard_selected_groups(selections))

    @api.model
    def _visar_wizard_coverage_group(self):
        """Grupo cuyas dimensiones se eligen por el paso de cobertura."""
        return self.env['visar.service.group'].sudo().search(
            [('code', '=', 'fumigacion')], limit=1)

    @api.model
    def _visar_wizard_fum_dimensions_for_coverage(self, coverage):
        """Dimensiones de fumigación que corresponden a la cobertura elegida."""
        group = self._visar_wizard_coverage_group()
        if not group:
            return self.env['visar.service.dimension']
        dims = group.dimension_ids.filtered('active')
        interior = dims.filtered(lambda d: d.measure_type == 'interior')
        exterior = dims.filtered(lambda d: d.measure_type == 'exterior')
        if coverage == 'interior':
            return interior
        if coverage == 'exterior':
            return exterior
        return interior | exterior

    @api.model
    def _visar_wizard_group_needs_substep(self, group):
        """True si el grupo tiene más de una dimensión activa y hay que preguntar cuál."""
        return len(group.dimension_ids.filtered('active')) > 1

    @api.model
    def _visar_wizard_next_group_substep(self, selections):
        """Primer grupo elegido que aún no tiene dimensiones.

        Excluye fumigación: sus dimensiones salen del paso de cobertura.
        """
        selections = selections or {}
        coverage_group = self._visar_wizard_coverage_group()
        dimension_ids = set(selections.get('dimension_ids') or [])
        for group in self._visar_wizard_selected_groups(selections).sorted('sequence'):
            if group == coverage_group:
                continue
            if not self._visar_wizard_group_needs_substep(group):
                continue
            group_dim_ids = set(group.dimension_ids.filtered('active').ids)
            if not group_dim_ids.intersection(dimension_ids):
                return group
        return self.env['visar.service.group']

    @api.model
    def _visar_wizard_auto_dimensions(self, groups, dimension_ids):
        """Añade las dimensiones de grupos que solo tienen una (no hay qué preguntar)."""
        result = set(dimension_ids or [])
        for group in groups:
            dims = group.dimension_ids.filtered('active')
            if len(dims) == 1:
                result.add(dims.id)
        return list(result)

    @api.model
    def _visar_wizard_dim_has_tier(self, selections, dimension):
        """True si la dimensión ya tiene tramo elegido."""
        selections = selections or {}
        key = dimension._visar_tier_field_name()
        return bool(selections.get(key)
                    or (selections.get('tiers') or {}).get(str(dimension.id)))

    @api.model
    def _visar_wizard_dims_by_measure(self, selections, measure_type):
        return self._visar_selection_dimension_ids(selections).filtered(
            lambda d: d.measure_type == measure_type)

    @api.model
    def _visar_wizard_tier_for_m2(self, dimension, m2):
        """Tramo cuyo rango contiene `m2` para esa dimensión (o vacío)."""
        template = self.env['product.template'].sudo(
        )._visar_get_service_template_for_dimension(dimension)
        if not template:
            return self.env['visar.service.tier']
        return template._visar_tier_for_dimension_m2(dimension, m2)

    @api.model
    def _visar_wizard_requires_valuation(self, selections):
        """True si el cuestionario cortó a visita de valoración."""
        selections = selections or {}
        # Corte por calificación (termitas/chinches/plaga no identificada).
        if selections.get('requiere_valoracion'):
            return True
        # Corte por tramo (área fuera del tabulador).
        return any(item.get('is_valuation')
                   for item in self._visar_resolve_wizard_items(selections))

    @api.model
    def _visar_wizard_dimension_sections(self, selections, measure_type='direct'):
        """Secciones de tramos por dimensión, para los pasos de medición."""
        ProductTemplate = self.env['product.template'].sudo()
        sections = []
        for dimension in self._visar_wizard_dims_by_measure(selections, measure_type):
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

    # ------------------------------------------------------------------
    # Secuencia
    # ------------------------------------------------------------------

    @api.model
    def _visar_wizard_next_step(self, booking):
        """Clave del siguiente paso PENDIENTE, hasta la dirección.

        Reproduce exactamente el orden del web. Termina en `address` a
        propósito: extras y póliza solo se pueden decidir con zona e items
        resueltos, y eso pasa AL enviar la dirección
        (ver `_visar_wizard_next_after_address`).
        """
        selections = (booking or {}).get('selections') or {}
        if not selections.get('group_ids'):
            return VISAR_STEP_SERVICES

        # Corte a valoración: atajo global para no re-preguntar mediciones cuando
        # ya se decidió que va valoración.
        if selections.get('requiere_valoracion'):
            return VISAR_STEP_VALUATION

        if self._visar_wizard_fumigacion_selected(selections):
            if not selections.get('motivo'):
                return 'motivo'
            if not selections.get('servicio_plaga'):
                return 'plagas'
            if not selections.get('cobertura'):
                return 'cobertura'

        next_group = self._visar_wizard_next_group_substep(selections)
        if next_group:
            return 'group_%s' % next_group.id

        dims = self._visar_selection_dimension_ids(selections)

        def needs(measure_type):
            return any(d.measure_type == measure_type
                       and not self._visar_wizard_dim_has_tier(selections, d)
                       for d in dims)

        if needs('interior'):
            return 'interior'
        if needs('exterior'):
            return 'exterior'
        if needs('direct'):
            return 'dimensiones'

        if self._visar_wizard_requires_valuation(selections):
            return VISAR_STEP_VALUATION
        return VISAR_STEP_ADDRESS

    @api.model
    def _visar_wizard_needs_name(self, booking):
        """¿Hay que preguntar el nombre del cliente?

        Solo cuando **el llamador dice que no sabe quién es** (`needs_name`) y el
        cliente no lo ha dado ya. Quién lo sabe es el canal, no el flujo: por
        WhatsApp la identidad es el teléfono —y si no hay `res.partner` con ese
        número, no hay nombre— mientras que el wizard web lo recoge en el
        formulario nativo del final. El web nunca pone la bandera, así que para
        él este paso no existe.
        """
        booking = booking or {}
        if not booking.get('needs_name'):
            return False
        return not ((booking.get('selections') or {}).get('nombre') or '').strip()

    @api.model
    def _visar_wizard_step_after(self, booking, step=VISAR_STEP_ADDRESS):
        """Siguiente paso del tramo POSTERIOR a la dirección.

        Es una cadena lineal —dirección → nombre → extras → póliza → horario—
        donde cada eslabón puede no existir. Se recorre desde el paso que se acaba
        de contestar, y no desde el principio: extras y póliza se preguntan UNA
        vez, y contestarlos no hace desaparecer la oferta (los add-ons se siguen
        ofreciendo aunque el cliente ya haya dicho que no). Arrancar siempre desde
        el principio devolvería al cliente al paso que acaba de contestar.

        El nombre va **aquí y no antes** a propósito: es el único dato que se pide
        por gusto del sistema y no del cliente, así que se cobra cuando ya hay
        algo que agendar, no en la puerta.
        """
        chain = [VISAR_STEP_NAME, VISAR_STEP_EXTRAS, VISAR_STEP_POLIZA,
                 VISAR_STEP_SCHEDULE]
        start = chain.index(step) + 1 if step in chain else 0
        for candidate in chain[start:]:
            if candidate == VISAR_STEP_NAME and not self._visar_wizard_needs_name(booking):
                continue
            if candidate == VISAR_STEP_EXTRAS and not self._visar_wizard_extras_offers(booking):
                continue
            if candidate == VISAR_STEP_POLIZA and not self._visar_wizard_poliza_context(booking):
                continue
            return candidate
        return VISAR_STEP_SCHEDULE

    @api.model
    def _visar_wizard_step_sequence(self, booking):
        """Pasos aplicables al estado actual (para el indicador "Paso X de Y").

        Los pasos de medición se infieren de los `measure_type` de las
        dimensiones elegidas; si aún no hay cobertura, se anticipan los de
        fumigación.
        """
        booking = booking or {}
        selections = booking.get('selections') or {}
        steps = [VISAR_STEP_SERVICES]
        fum = self._visar_wizard_fumigacion_selected(selections)
        if fum:
            steps += ['motivo', 'plagas', 'cobertura']

        coverage_group = self._visar_wizard_coverage_group()
        for group in self._visar_wizard_selected_groups(selections).sorted('sequence'):
            if group == coverage_group:
                continue
            if self._visar_wizard_group_needs_substep(group):
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
        steps.append(VISAR_STEP_ADDRESS)
        if self._visar_wizard_needs_name(booking):
            steps.append(VISAR_STEP_NAME)
        # Extras y póliza solo existen tras resolver zona/items y si hay qué ofrecer.
        if booking.get('zone_id') and booking.get('items'):
            if self._visar_wizard_extras_offers(booking):
                steps.append(VISAR_STEP_EXTRAS)
            if self._visar_wizard_poliza_context(booking):
                steps.append(VISAR_STEP_POLIZA)
        return steps

    @api.model
    def _visar_wizard_step_label(self, step_key):
        """Nombre corto de un paso, para ofrecerlo como "esto se puede cambiar"."""
        if step_key.startswith('group_'):
            group = self.env['visar.service.group'].sudo().browse(
                self._visar_wizard_id_list(step_key[len('group_'):])).exists()
            return group._visar_wizard_label() if group else step_key
        return _VISAR_STEP_LABELS.get(step_key, step_key)

    @api.model
    def _visar_wizard_editable_steps(self, booking):
        """Pasos que el cliente puede volver a contestar, en orden, con etiqueta.

        Es la MISMA lista que el indicador "Paso X de Y" (`_visar_wizard_step_sequence`):
        lo que se preguntó es exactamente lo que se puede corregir. Se publica con
        etiqueta porque el runtime solo tiene claves (`group_12`, `tier_7`) y
        traducirlas del otro lado sería otra regla duplicada.

        Corregir un paso NO es un modo especial: se contesta como la primera vez,
        `_visar_wizard_clear_downstream` tumba lo que dependía de la respuesta
        vieja, y el flujo sigue desde ahí. Por eso no hay "rewind" en ningún lado
        — solo volver a preguntar.
        """
        return [{'key': step, 'label': self._visar_wizard_step_label(step)}
                for step in self._visar_wizard_step_sequence(booking)]

    @api.model
    def _visar_wizard_schedule_key(self, booking):
        """Huella de lo que condiciona la AGENDA. Si no cambia, el horario sirve.

        Existe para una pregunta muy concreta: el cliente corrigió algo desde la
        pantalla de revisión — ¿hay que volver a elegir día y hora, o el horario
        que ya tenía apartado sigue valiendo?

        Depende de **quién puede hacer el trabajo**, y eso lo fija
        `_visar_service_resource_pools`: la zona y, por cada dimensión, su tipo de
        cita. Cambiar de interior a exterior cambia la dimensión y puede cambiar
        el técnico; cambiar de tramo o de plan de póliza cambia el precio y no la
        agenda (el bloque es de 1 h fija, decisión 7 del diseño 33).

        Se publica como una **cadena opaca** a propósito. El runtime no tiene que
        saber qué campos de un item importan: compara la de antes con la de ahora
        y ya. Si mañana la duración dependiera de los items, se añade aquí y el
        runtime no se entera.
        """
        booking = booking or {}
        firma = sorted(
            (int(item.get('dimension_id') or 0),
             int(item.get('appointment_type_id') or 0))
            for item in (booking.get('items') or [])
        )
        return '%s|%s' % (booking.get('zone_id') or 0, firma)

    @api.model
    def _visar_wizard_reapply_address(self, booking):
        """Re-resuelve zona e items con la dirección que ya se capturó.

        Cambiar un paso de arriba (cobertura, plagas, tamaño) invalida los items,
        y el único sitio donde se recalculan es el paso de la dirección. Sin esto,
        corregir "interior" por "ambos" obligaba al cliente a **volver a escribir
        su dirección**, que es la pregunta más cara del cuestionario y la que ya
        había contestado bien.

        Devuelve (booking, error). Sin dirección guardada no hace nada: es el
        cliente que todavía no ha llegado ahí.
        """
        address = (booking or {}).get('delivery_address') or {}
        if not address:
            return booking, None
        return self._visar_wizard_answer_address(booking, address)

    # ------------------------------------------------------------------
    # Extras y póliza (dependen de zona + items, no solo de selections)
    # ------------------------------------------------------------------

    @api.model
    def _visar_wizard_has_roedores(self, booking):
        return self._visar_selections_has_roedores((booking or {}).get('selections'))

    @api.model
    def _visar_wizard_extras_offers(self, booking):
        """Add-ons opcionales ofrecibles para la reserva actual."""
        booking = booking or {}
        zone = self.env['visar.zone'].sudo().browse(booking.get('zone_id')).exists()
        items = booking.get('items') or []
        if not zone or not items:
            return []
        return self._visar_offered_addons(
            items, zone, include_roedores=self._visar_wizard_has_roedores(booking))

    @api.model
    def _visar_wizard_poliza_plans(self):
        """Planes ofrecibles en el paso de póliza, en orden de presentación.

        Se leen de un parámetro de sistema para no hornear ids; por defecto, los
        planes que ya tienen lista (zona × plan) configurada.
        """
        Plan = self.env['sale.subscription.plan'].sudo()
        param = self.env['ir.config_parameter'].sudo().get_param('visar.poliza_plan_ids')
        if param:
            ids = []
            for value in param.replace(',', ' ').split():
                try:
                    ids.append(int(value))
                except (TypeError, ValueError):
                    continue
            plans = Plan.browse(ids).exists()
            if plans:
                return plans
        pricelists = self.env['product.pricelist'].sudo().search(
            [('visar_plan_id', '!=', False)])
        return pricelists.mapped('visar_plan_id').sorted('billing_period_value')

    @api.model
    def _visar_wizard_plan_period_label(self, plan):
        """Periodicidad de un plan, en espanol: "al mes", "cada 3 meses".

        NO se usa `billing_period_display_sentence`: su fuente es inglesa
        ("per month") y se traduce con el idioma del usuario que hace la llamada.
        Por RPC ese usuario esta en `en_US`, asi que el cliente recibia
        *"per year"* en mitad de una conversacion en espanol.
        """
        singular, plural = _VISAR_PERIOD_LABELS.get(
            plan.billing_period_unit, ("", "cada %s periodos"))
        value = plan.billing_period_value or 1
        return singular if value <= 1 else plural % value

    @api.model
    def _visar_wizard_poliza_description(self, offer):
        """Que dice de un plan la linea de debajo del nombre.

        Antes decia `billing_period_display_sentence` — *"per month"*, en ingles y
        sin aportar nada que el nombre no dijera ya. Aqui dice lo unico que el
        cliente necesita para decidir: **cuanto y cada cuanto**, y cuanto se ahorra.

        Con cuatro planes que hoy se llaman igual en el catalogo (I-15 del
        backlog), esta linea es ademas lo unico que los distingue en el chat.
        """
        currency = self.env['res.currency'].browse(offer.get('currency_id'))
        if not currency:
            currency = self.env.company.currency_id
        partes = ['%s %s' % (format_amount(self.env, offer['period_total'], currency),
                             offer.get('period_label') or '')]
        if offer.get('saving'):
            partes.append(_('ahorras %s') % format_amount(
                self.env, offer['saving'], currency))
        return ' · '.join(p.strip() for p in partes if p.strip())

    @api.model
    def _visar_wizard_poliza_context(self, booking):
        """(zone, master, plans) si la reserva puede volverse póliza; None si no.

        Comprobación barata, sin cotizar: la usa `_visar_wizard_step_sequence`,
        que corre en CADA página del wizard.
        """
        booking = booking or {}
        if booking.get('mode') != 'wizard':
            return None
        zone = self.env['visar.zone'].sudo().browse(booking.get('zone_id')).exists()
        items = booking.get('items') or []
        if not zone or not items:
            return None
        if self._visar_wizard_requires_valuation(booking.get('selections') or {}):
            return None
        master = self.browse(booking.get('master_appointment_type_id')).exists()
        if not master:
            return None
        # Sin lista (zona × plan) no hay precio de póliza que ofrecer.
        plans = self._visar_wizard_poliza_plans().filtered(
            lambda p: zone._visar_poliza_pricelist(p).visar_plan_id)
        if not plans:
            return None
        sale_lines = master._visar_build_sale_lines(
            items, zone, include_roedores=self._visar_wizard_has_roedores(booking),
            extra_addons=booking.get('extras_accepted'))
        Product = self.env['product.product'].sudo()
        if not any(Product.browse(l['product_id']).recurring_invoice for l in sale_lines):
            return None
        return zone, master, plans

    @api.model
    def _visar_wizard_poliza_offers(self, booking):
        """Ofertas de póliza (una por plan ofrecible). Cotiza de verdad."""
        context = self._visar_wizard_poliza_context(booking)
        if not context:
            return []
        zone, master, plans = context
        items = booking.get('items') or []
        include_roedores = self._visar_wizard_has_roedores(booking)
        extras = booking.get('extras_accepted')

        base = master._visar_quote_booking(
            items, zone, include_roedores=include_roedores, extra_addons=extras)
        offers = []
        for plan in plans:
            quote = master._visar_quote_booking(
                items, zone, include_roedores=include_roedores,
                extra_addons=extras, plan=plan)
            if not quote:
                continue
            offers.append({
                'plan': plan,
                'plan_id': plan.id,
                'name': plan.name,
                'billing_label': plan.billing_period_display_sentence,
                # La moneda sale de la cotizacion, que ya la resuelve bien (lista
                # de la zona -> website -> compania). Sin ella no se puede
                # redactar el precio del plan.
                'currency_id': quote.get('currency_id'),
                'period_label': self._visar_wizard_plan_period_label(plan),
                'periods': quote['periods'],
                # Precio de la PÓLIZA = solo el servicio recurrente. Los add-ons son
                # cargo único y no se repiten cada periodo, así que meterlos en el
                # precio "al mes" lo infla y no es lo que se va a cobrar en el mes 3.
                'period_total': quote['recurring_total'],
                'addons_total': quote['addons_total'],
                'upfront_service_total': quote['upfront_service_total'],
                'upfront_total': quote['upfront_total'],
                # Ahorro frente a contratar el mismo servicio una sola vez: se compara
                # solo la parte recurrente, que es la única que la póliza abarata.
                'saving': max(0.0, (base or {}).get('recurring_total', 0.0)
                              - quote['recurring_total']),
                'quote': quote,
            })
        return offers

    # ------------------------------------------------------------------
    # Resumen para la pantalla de revisión
    # ------------------------------------------------------------------

    @api.model
    def _visar_wizard_summary(self, booking):
        """Qué lleva el cliente, en texto legible, y cuánto cuesta.

        Existe para la **pantalla de revisión antes de cobrar**: pedir "¿lo
        confirmo?" sin decir qué se está comprando ni cuánto cuesta es pedir un
        cheque en blanco. El runtime no puede armar esto por su cuenta —
        `selections` trae `group_ids` y `tier_7`, no nombres— y decodificarlo del
        otro lado sería otra regla duplicada.

        Devuelve {'lines': [str], 'total': float|None, 'currency': str|None}.
        El total es **con IVA incluido** (`amount_total`), que es el único que el
        cliente reconoce; nunca el subtotal.
        """
        booking = booking or {}
        selections = booking.get('selections') or {}
        lines = [group._visar_wizard_label()
                 for group in self._visar_wizard_selected_groups(selections)]

        items = booking.get('items') or []
        if items:
            lines += [label for label in self._visar_metros_labels(items) if label]

        if self._visar_wizard_requires_valuation(selections):
            lines.append(_('Visita de valoración técnica'))

        zone = self.env['visar.zone'].sudo().browse(booking.get('zone_id')).exists()
        total = currency = None
        if items and zone:
            plan = self.env['sale.subscription.plan'].sudo().browse(
                int(selections.get('poliza_plan_id') or 0)).exists()
            quote = self._visar_quote_booking(
                items, zone,
                include_roedores=self._visar_wizard_has_roedores(booking),
                extra_addons=booking.get('extras_accepted'),
                plan=plan or None)
            if quote:
                # Con póliza, lo que se cobra HOY no es lo recurrente: enseñar el
                # "al mes" como si fuera el cargo sería mentir sobre el cobro.
                total = quote.get('upfront_total') or quote.get('total')
                # La moneda sale de la cotizacion, que ya la resuelve bien
                # (lista de la zona -> website -> compania). `visar.zone` NO
                # tiene `company_id`: calcularla aqui por segunda vez reventaba
                # el paso de la direccion con AttributeError.
                currency = (
                    self.env['res.currency'].browse(quote.get('currency_id'))
                    or self.env.company.currency_id).name
                if plan and quote.get('recurring_total'):
                    lines.append(_('Póliza: %(plan)s') % {'plan': plan.name})
        return {'lines': lines, 'total': total, 'currency': currency}

    # ------------------------------------------------------------------
    # Dirección
    # ------------------------------------------------------------------

    @api.model
    def _visar_wizard_resolve_address(self, values):
        """(zone, address, error). Ciudad y estado se derivan del CP en el servidor."""
        keys = ('street', 'ext_num', 'int_num', 'neighborhood', 'zip')
        address = {key: ((values or {}).get(key) or '').strip() for key in keys}
        required = ('street', 'ext_num', 'neighborhood', 'zip')
        if any(not address.get(key) for key in required):
            return self.env['visar.zone'], address, _(
                'Completa calle, número exterior, colonia y código postal.')
        CpModel = self.env['visar.zone.cp'].sudo()
        cp_record = CpModel._get_cp_record(address['zip'])
        if not cp_record or not cp_record.zone_id:
            return self.env['visar.zone'], address, _(
                'No damos servicio en el código postal %s. Contáctanos.'
            ) % address['zip']
        address['zip'] = CpModel._normalize_cp(address['zip'])
        address['city'] = cp_record.municipality or ''
        address['state'] = 'Nuevo León'
        return cp_record.zone_id, address, None

    # ------------------------------------------------------------------
    # Normalización de la respuesta de un paso
    # ------------------------------------------------------------------
    #
    # Es la tercera pieza que no se puede duplicar. "Protección general" activa
    # las tres categorías; "termitas" corta a valoración; una banda de exterior
    # fuera del tabulador también. Si el runtime armara `selections` por su
    # cuenta, esas reglas quedarían solo en el web.
    #
    # Devuelve (booking, error). `error` es None o
    # {'code', 'message', ...extras}: el controlador lo pinta en la plantilla del
    # paso y el agente se lo dice al cliente. Ninguno lanza.

    @api.model
    def _visar_wizard_id_list(self, values):
        """Lista de enteros, descartando lo que no lo sea."""
        if values in (None, False, ''):
            return []
        if not isinstance(values, (list, tuple, set)):
            values = [values]
        ids = []
        for value in values:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        return ids

    @api.model
    def _visar_wizard_error(self, code, message, **extra):
        return dict({'code': code, 'message': message}, **extra)

    @api.model
    def _visar_wizard_apply_answer(self, booking, step_key, answer):
        """Aplica la respuesta de un paso. Devuelve (booking, error)."""
        booking = dict(booking or {})
        answer = answer or {}
        handler = {
            VISAR_STEP_SERVICES: self._visar_wizard_answer_services,
            'motivo': self._visar_wizard_answer_motivo,
            'plagas': self._visar_wizard_answer_plagas,
            'cobertura': self._visar_wizard_answer_cobertura,
            'dimensiones': self._visar_wizard_answer_dimensiones,
            'interior': self._visar_wizard_answer_interior,
            'exterior': self._visar_wizard_answer_exterior,
            VISAR_STEP_ADDRESS: self._visar_wizard_answer_address,
            VISAR_STEP_NAME: self._visar_wizard_answer_nombre,
            VISAR_STEP_EXTRAS: self._visar_wizard_answer_extras,
            VISAR_STEP_POLIZA: self._visar_wizard_answer_poliza,
        }.get(step_key)
        if handler is None and step_key.startswith('group_'):
            return self._visar_wizard_answer_group(booking, step_key, answer)
        if handler is None:
            return booking, self._visar_wizard_error(
                'unknown_step', _('Ese paso no existe: %s') % step_key)
        return handler(booking, answer)

    @api.model
    def _visar_wizard_answer_services(self, booking, answer):
        Group = self.env['visar.service.group'].sudo()
        offered = self._visar_wizard_groups()
        ids = self._visar_wizard_id_list(answer.get('group_ids'))
        # No se confía en lo que llega: solo grupos realmente ofrecidos.
        groups = Group.browse([i for i in ids if i in offered.ids]).exists()
        if not groups:
            return booking, self._visar_wizard_error(
                'no_service', _('Selecciona al menos un servicio.'))
        return self._visar_wizard_commit(booking, VISAR_STEP_SERVICES, {
            'group_ids': groups.ids,
            'dimension_ids': self._visar_wizard_auto_dimensions(groups, []),
        }), None

    @api.model
    def _visar_wizard_answer_group(self, booking, step_key, answer):
        selections = booking.get('selections') or {}
        try:
            group_id = int(step_key[len('group_'):])
        except (TypeError, ValueError):
            return booking, self._visar_wizard_error(
                'unknown_step', _('Ese paso no existe: %s') % step_key)
        group = self.env['visar.service.group'].sudo().browse(group_id).exists()
        if not group or group.id not in (selections.get('group_ids') or []):
            return booking, self._visar_wizard_error(
                'group_not_selected', _('Ese servicio no está entre los elegidos.'))

        valid_ids = group.dimension_ids.filtered('active').ids
        chosen = [d for d in self._visar_wizard_id_list(answer.get('dimension_ids'))
                  if d in valid_ids]
        if not chosen:
            return booking, self._visar_wizard_error(
                'no_dimension', _('Selecciona al menos una opción.'))
        # Conserva las dimensiones de OTROS grupos y fija las de este.
        current = set(selections.get('dimension_ids') or [])
        current.update(chosen)
        current.difference_update(set(valid_ids) - set(chosen))
        return self._visar_wizard_commit(
            booking, step_key, {'dimension_ids': list(current)}), None

    @api.model
    def _visar_wizard_answer_motivo(self, booking, answer):
        motivo = answer.get('motivo')
        if motivo not in ('preventivo', 'correctivo'):
            return booking, self._visar_wizard_error(
                'bad_motivo', _('Indica si es preventivo o correctivo.'))
        return self._visar_wizard_commit(booking, 'motivo', {'motivo': motivo}), None

    @api.model
    def _visar_wizard_answer_plagas(self, booking, answer):
        selections = booking.get('selections') or {}
        motivo = selections.get('motivo')
        raw = answer.get('servicio_plaga')
        if isinstance(raw, str):
            raw = [raw]
        chosen = set(raw or [])
        categories = [c for c in VISAR_PLAGA_CATEGORIES if c in chosen]

        # Protección general (rama preventiva): activa las tres, sin corte.
        if 'proteccion_general' in chosen:
            categories = list(VISAR_PLAGA_CATEGORIES)

        # Cortes a valoración: SOLO en la rama correctiva. En preventivo el
        # cliente no está reportando una plaga, está contratando protección.
        cut_reason = False
        if motivo == 'correctivo':
            for option, reason in VISAR_PLAGA_CUTS:
                if option in chosen:
                    cut_reason = reason
                    break

        if not categories and not cut_reason:
            return booking, self._visar_wizard_error(
                'no_plaga', _('Selecciona al menos una opción.'))

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
        return self._visar_wizard_commit(booking, 'plagas', updates), None

    @api.model
    def _visar_wizard_answer_cobertura(self, booking, answer):
        selections = booking.get('selections') or {}
        coverage = answer.get('cobertura')
        if coverage not in ('interior', 'exterior', 'ambos'):
            return booking, self._visar_wizard_error(
                'bad_cobertura',
                _('Indica si fumigamos interior, exterior o ambos.'))
        fum_group = self._visar_wizard_coverage_group()
        fum_dim_ids = (set(fum_group.dimension_ids.filtered('active').ids)
                       if fum_group else set())
        chosen_dim_ids = self._visar_wizard_fum_dimensions_for_coverage(coverage).ids
        # Conserva las dimensiones de otros grupos y fija las de fumigación.
        current = [d for d in (selections.get('dimension_ids') or [])
                   if d not in fum_dim_ids]
        current += chosen_dim_ids
        return self._visar_wizard_commit(booking, 'cobertura', {
            'cobertura': coverage,
            'dimension_ids': current,
        }), None

    @api.model
    def _visar_wizard_answer_dimensiones(self, booking, answer):
        selections = booking.get('selections') or {}
        sections = self._visar_wizard_dimension_sections(selections)
        tiers = answer.get('tiers') or answer
        updates = {}
        for section in sections:
            tier_id = tiers.get(section['field_name'])
            valid_ids = section['tiers'].ids
            try:
                tier_id = int(tier_id or 0)
            except (TypeError, ValueError):
                tier_id = 0
            if tier_id not in valid_ids:
                return booking, self._visar_wizard_error(
                    'no_tier', _('Selecciona un rango para cada servicio.'))
            updates[section['field_name']] = tier_id
        return self._visar_wizard_commit(booking, 'dimensiones', updates), None

    @api.model
    def _visar_wizard_answer_interior(self, booking, answer):
        selections = booking.get('selections') or {}
        sections = self._visar_wizard_dimension_sections(
            selections, measure_type='interior')
        mode = answer.get('interior_mode')
        updates = {'interior_niveles': answer.get('interior_niveles') or ''}

        if mode == 'sabe':
            for section in sections:
                try:
                    tier_id = int(answer.get(section['field_name']) or 0)
                except (TypeError, ValueError):
                    tier_id = 0
                if tier_id not in section['tiers'].ids:
                    return booking, self._visar_wizard_error(
                        'no_tier', _('Selecciona un rango para cada servicio.'))
                updates[section['field_name']] = tier_id
        elif mode == 'estima':
            def _num(key):
                try:
                    return max(int(answer.get(key) or 0), 0)
                except (TypeError, ValueError):
                    return 0
            rec, ban = _num('rec'), _num('ban')
            niv, gar = max(_num('niv'), 1), _num('gar')
            predio = _num('predio')
            if rec <= 0:
                return booking, self._visar_wizard_error(
                    'no_rooms', _('Indica al menos el número de recámaras.'))
            m2 = self.env['visar.estimator.factor'].sudo()._visar_estimate_interior_m2(
                rec, ban, niv, gar, predio)
            for section in sections:
                tier = self._visar_wizard_tier_for_m2(section['dimension'], m2)
                if not tier:
                    return booking, self._visar_wizard_error(
                        'no_tier_for_m2',
                        _('No pudimos estimar el tamaño. Intenta con el rango directo.'))
                updates[section['field_name']] = tier.id
            updates.update({
                'interior_estimado_m2': m2,
                'interior_proxy': {'rec': rec, 'ban': ban, 'niv': niv,
                                   'gar': gar, 'predio': predio},
            })
        else:
            return booking, self._visar_wizard_error(
                'bad_interior_mode',
                _('Indica si conoces tus metros cuadrados o si prefieres estimarlos.'))

        return self._visar_wizard_commit(booking, 'interior', updates), None

    @api.model
    def _visar_wizard_answer_exterior(self, booking, answer):
        selections = booking.get('selections') or {}
        exterior_dims = self._visar_wizard_dims_by_measure(selections, 'exterior')
        Band = self.env['visar.measure.band'].sudo()
        bands = Band._visar_exterior_bands()
        try:
            band = Band.browse(int(answer.get('band_id') or 0)).exists()
        except (TypeError, ValueError):
            band = Band.browse()
        if band not in bands:
            return booking, self._visar_wizard_error(
                'bad_band', _('Selecciona el tamaño de tu jardín o exterior.'))

        updates = {
            'exterior_band_id': band.id,
            'exterior_rodea': answer.get('exterior_rodea') or '',
        }
        if band.is_valuation:
            updates['requiere_valoracion'] = True
            updates['motivo_valoracion'] = 'area_excede_limite'
        else:
            for dimension in exterior_dims:
                tier = self._visar_wizard_tier_for_m2(dimension, band.m2_ref)
                if not tier:
                    return booking, self._visar_wizard_error(
                        'no_tier_for_band',
                        _('No hay un rango configurado para ese tamaño. Contáctanos.'))
                updates[dimension._visar_tier_field_name()] = tier.id
        return self._visar_wizard_commit(booking, 'exterior', updates), None

    @api.model
    def _visar_wizard_answer_address(self, booking, answer):
        """Resuelve zona, items y pools. Es el paso que "cierra" el cuestionario.

        No pasa por `_visar_wizard_commit`, así que `_visar_wizard_clear_downstream`
        no corre aquí: si el CP cambió de zona hay que soltar a mano el plan
        elegido, que se cotizó contra la zona anterior. (Los extras se sueltan
        solos: no se arrastran al payload nuevo.)
        """
        selections = dict(booking.get('selections') or {})

        # Lo que escribió el cliente se valida ANTES que la configuración: si la
        # dirección viene incompleta, eso es lo que hay que decirle, no "falta
        # configurar el tipo de cita".
        zone, address, error = self._visar_wizard_resolve_address(answer)
        if error:
            return booking, self._visar_wizard_error(
                'bad_address', error, address=address)

        master = self._visar_get_master_appointment_type()
        if not master:
            return booking, self._visar_wizard_error(
                'config_missing', _('Falta configurar el tipo de cita.'))

        items = self._visar_resolve_wizard_items(selections)
        if not items:
            return booking, self._visar_wizard_error(
                'no_items',
                _('No se pudieron resolver los servicios seleccionados.'),
                address=address)

        pools, missing = self._visar_service_resource_pools(zone, items)
        if missing:
            return booking, self._visar_wizard_error(
                'no_resources',
                _('No tenemos técnicos disponibles para ese servicio en tu zona.'),
                address=address, missing_services=missing, zone_id=zone.id)

        previous_zone_id = booking.get('zone_id')
        if previous_zone_id and previous_zone_id != zone.id:
            selections.pop('poliza_plan_id', None)

        return {
            'mode': 'wizard',
            'master_appointment_type_id': master.id,
            'zone_id': zone.id,
            'delivery_address': address,
            'selections': selections,
            'items': items,
            'service_pools': {key: pool.ids for key, pool in pools.items()},
        }, None

    @api.model
    def _visar_wizard_answer_nombre(self, booking, answer):
        """Nombre del cliente nuevo. Valida poco, pero valida.

        No se acepta cualquier cosa: este texto acaba siendo el `res.partner` con
        el que se factura y el nombre que ve el técnico en su hoja de ruta. Una
        respuesta de dos letras o un número suelto casi siempre es el cliente
        contestando otra cosa (o tocando un botón viejo), y arreglarlo después es
        una ficha duplicada que nadie limpia.
        """
        nombre = ' '.join((answer.get('nombre') or '').split())
        if len(nombre) < 3 or not any(ch.isalpha() for ch in nombre):
            return booking, self._visar_wizard_error(
                'bad_name', _('¿Me confirmas tu nombre completo?'))
        return self._visar_wizard_commit(
            booking, VISAR_STEP_NAME, {'nombre': nombre}), None

    @api.model
    def _visar_wizard_answer_extras(self, booking, answer):
        offers = self._visar_wizard_extras_offers(booking)
        offered_by_id = {o['product_id']: o for o in offers}
        chosen = set(self._visar_wizard_id_list(answer.get('extra_ids')))
        booking = dict(booking)
        # No se confía en lo que llega: solo lo que de verdad se ofreció, y con la
        # cantidad de la oferta (no la que mande el cliente).
        booking['extras_accepted'] = [
            {'product_id': pid, 'quantity': offered_by_id[pid]['quantity']}
            for pid in chosen if pid in offered_by_id
        ]
        return booking, None

    @api.model
    def _visar_wizard_answer_poliza(self, booking, answer):
        offers = self._visar_wizard_poliza_offers(booking)
        offered_ids = {o['plan_id'] for o in offers}
        chosen = self._visar_wizard_id_list(answer.get('plan_id'))
        plan_id = chosen[0] if chosen and chosen[0] in offered_ids else False
        return self._visar_wizard_commit(
            booking, VISAR_STEP_POLIZA, {'poliza_plan_id': plan_id}), None

    # ------------------------------------------------------------------
    # Opciones válidas de un paso
    # ------------------------------------------------------------------
    #
    # Todo serializable: lo consume el runtime por RPC. Sin esto el agente
    # tendría que derivar las opciones del catálogo por su cuenta y volvería a
    # duplicar reglas (qué plagas se ofrecen según el motivo, qué tramos existen
    # para esta dimensión, qué planes tienen lista de precios en esta zona).
    #
    # `kind` le dice al canal CÓMO preguntar, no con qué widget:
    #   'single'  — elegir una      'multi' — elegir varias
    #   'measure' — medición (modo directo o estimado)
    #   'text'    — captura libre guiada (dirección)
    #   'terminal'— no se pregunta nada (valoración, horario)

    @api.model
    def _visar_wizard_tier_options(self, tiers):
        return [{
            'value': tier.id,
            'label': tier.name or ('%g - %g m2' % (tier.m2_min, tier.m2_max)),
            'm2_min': tier.m2_min,
            'm2_max': tier.m2_max,
            'is_free': tier.is_free,
            'is_valuation': tier.is_valuation,
        } for tier in tiers]

    @api.model
    def _visar_wizard_measure_sections(self, selections, measure_type):
        """Secciones de medición, ya serializadas."""
        return [{
            'dimension_id': section['dimension_id'],
            'label': section['label'],
            'field_name': section['field_name'],
            'options': self._visar_wizard_tier_options(section['tiers']),
        } for section in self._visar_wizard_dimension_sections(
            selections, measure_type=measure_type)]

    @api.model
    def _visar_wizard_step_options(self, booking, step_key):
        """Opciones VÁLIDAS del paso dado, serializables."""
        booking = booking or {}
        selections = booking.get('selections') or {}

        if step_key == VISAR_STEP_SERVICES:
            return {
                'step': step_key, 'kind': 'multi', 'answer_key': 'group_ids',
                'title': _('¿Qué servicio necesitas?'),
                'options': [{
                    'value': group.id,
                    'label': group._visar_wizard_label(),
                    'description': group.wizard_help or '',
                } for group in self._visar_wizard_groups()],
            }

        if step_key == 'motivo':
            return {
                'step': step_key, 'kind': 'single', 'answer_key': 'motivo',
                'title': _('¿Es preventivo o correctivo?'),
                'options': [
                    {'value': 'preventivo', 'label': _('Preventivo'),
                     'description': _('Quiero evitar que aparezcan')},
                    {'value': 'correctivo', 'label': _('Correctivo'),
                     'description': _('Ya tengo el problema')},
                ],
            }

        if step_key == 'plagas':
            # El juego de opciones DEPENDE del motivo: los cortes a valoración
            # (termitas, chinches, "no sé") solo existen en la rama correctiva.
            correctivo = selections.get('motivo') == 'correctivo'
            options = [
                {'value': 'rastreros', 'label': _('Rastreros'),
                 'description': _('Cucarachas, alacranes, hormigas, arañas')},
                {'value': 'voladores', 'label': _('Voladores'),
                 'description': _('Moscas, mosquitos o zancudos')},
                {'value': 'roedores', 'label': _('Roedores'),
                 'description': _('Ratas y ratones')},
            ]
            if not correctivo:
                options.append({
                    'value': 'proteccion_general',
                    'label': _('Protección general'),
                    'description': _('Las tres: rastreros, voladores y roedores'),
                })
            else:
                options += [
                    {'value': 'termitas', 'label': _('Termitas'),
                     'description': _('Madera dañada, polvo fino, túneles de lodo'),
                     'is_valuation': True},
                    {'value': 'chinches', 'label': _('Chinches de cama'),
                     'description': _('Picaduras en hilera, manchas en sábanas'),
                     'is_valuation': True},
                    {'value': 'no_se', 'label': _('No estoy seguro de qué es'),
                     'description': '', 'is_valuation': True},
                ]
            return {
                'step': step_key, 'kind': 'multi', 'answer_key': 'servicio_plaga',
                'title': (_('¿Qué estás viendo en casa?') if correctivo
                          else _('¿Contra qué te gustaría protegerte?')),
                'options': options,
            }

        if step_key == 'cobertura':
            return {
                'step': step_key, 'kind': 'single', 'answer_key': 'cobertura',
                'title': _('¿Dónde fumigamos?'),
                'options': [
                    {'value': 'interior', 'label': _('Interior'), 'description': ''},
                    {'value': 'exterior', 'label': _('Exterior'), 'description': ''},
                    {'value': 'ambos', 'label': _('Ambos'), 'description': ''},
                ],
            }

        if step_key.startswith('group_'):
            group = self.env['visar.service.group'].sudo().browse(
                self._visar_wizard_id_list(step_key[len('group_'):])).exists()
            return {
                'step': step_key, 'kind': 'multi', 'answer_key': 'dimension_ids',
                'title': (_('¿Qué necesitas de %s?') % group._visar_wizard_label()
                          if group else _('¿Qué necesitas?')),
                'options': [{
                    'value': dim.id,
                    'label': dim._visar_wizard_label(),
                    'description': '',
                } for dim in group.dimension_ids.filtered('active')],
            }

        if step_key in ('dimensiones', 'interior'):
            measure = 'direct' if step_key == 'dimensiones' else 'interior'
            payload = {
                'step': step_key, 'kind': 'measure', 'answer_key': None,
                'title': _('¿De qué tamaño es el área?'),
                'sections': self._visar_wizard_measure_sections(selections, measure),
                'options': [],
            }
            if step_key == 'interior':
                # El paso interior admite dos caminos, y el segundo evita que el
                # cliente que no sabe sus m² se caiga del flujo.
                payload['mode_key'] = 'interior_mode'
                payload['modes'] = [
                    {'value': 'sabe', 'label': _('Sé mis metros cuadrados')},
                    {'value': 'estima', 'label': _('Prefiero estimarlos')},
                ]
                payload['estimate_fields'] = [
                    {'name': 'rec', 'label': _('Recámaras'), 'required': True},
                    {'name': 'ban', 'label': _('Baños'), 'required': False},
                    {'name': 'niv', 'label': _('Niveles'), 'required': False},
                    {'name': 'gar', 'label': _('Cajones de garage'), 'required': False},
                    {'name': 'predio', 'label': _('Terreno (m²)'), 'required': False},
                ]
            return payload

        if step_key == 'exterior':
            bands = self.env['visar.measure.band'].sudo()._visar_exterior_bands()
            return {
                'step': step_key, 'kind': 'single', 'answer_key': 'band_id',
                'title': _('¿De qué tamaño es tu jardín o exterior?'),
                'options': [{
                    'value': band.id,
                    'label': band.name,
                    'description': band.comparative_label or '',
                    'is_valuation': band.is_valuation,
                } for band in bands],
            }

        if step_key == VISAR_STEP_ADDRESS:
            return {
                'step': step_key, 'kind': 'text', 'answer_key': None,
                'title': _('¿A qué dirección vamos?'),
                'options': [],
                'fields': [
                    {'name': 'street', 'label': _('Calle'), 'required': True},
                    {'name': 'ext_num', 'label': _('Número exterior'), 'required': True},
                    {'name': 'int_num', 'label': _('Número interior'), 'required': False},
                    {'name': 'neighborhood', 'label': _('Colonia'), 'required': True},
                    {'name': 'zip', 'label': _('Código postal'), 'required': True},
                ],
            }

        if step_key == VISAR_STEP_NAME:
            # `free_text` = una sola respuesta escrita, sin opciones. Se distingue
            # de `text` (la dirección) porque aquella son VARIOS campos y el canal
            # tiene que guiarlos uno por uno.
            return {
                'step': step_key, 'kind': 'free_text', 'answer_key': 'nombre',
                'title': _('¿A nombre de quién agendo el servicio?'),
                'placeholder': _('Ej: María López'),
                'options': [],
            }

        if step_key == VISAR_STEP_EXTRAS:
            return {
                'step': step_key, 'kind': 'multi', 'answer_key': 'extra_ids',
                'title': _('¿Quieres agregar algo más?'),
                'options': [{
                    'value': offer['product_id'],
                    'label': offer.get('name') or '',
                    'description': '',
                    'quantity': offer.get('quantity'),
                    'unit_price': offer.get('unit_price'),
                    'subtotal': offer.get('subtotal'),
                } for offer in self._visar_wizard_extras_offers(booking)],
            }

        if step_key == VISAR_STEP_POLIZA:
            options = [{
                'value': offer['plan_id'],
                'label': offer['name'],
                'description': self._visar_wizard_poliza_description(offer),
                # Recurrente y "lo que se paga hoy" van SEPARADOS: meter los
                # add-ons en el "al mes" infla un precio que no se va a cobrar
                # en el mes 3 (fue un bug real del primer corte del paso).
                'period_total': offer['period_total'],
                'upfront_total': offer['upfront_total'],
                'saving': offer['saving'],
            } for offer in self._visar_wizard_poliza_offers(booking)]
            # Sin esta opcion el paso no tenia salida en el chat: el web deja
            # seguir sin elegir, pero en WhatsApp un menu de un solo sentido es
            # una pregunta sin respuesta valida. El cliente se quedaba atrapado
            # justo antes del horario.
            options.append({
                'value': VISAR_POLIZA_NONE,
                'label': _('No, gracias'),
                'description': _('Contrato solo este servicio'),
            })
            return {
                'step': step_key, 'kind': 'single', 'answer_key': 'plan_id',
                'title': _('¿Te interesa contratarlo como póliza?'),
                'options': options,
            }

        return {'step': step_key, 'kind': 'terminal', 'answer_key': None,
                'title': '', 'options': []}
