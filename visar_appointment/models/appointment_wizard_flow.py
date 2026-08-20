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
import re

from odoo import _, api, models
from odoo.tools import format_amount

# Grupos de claves de selección por área del wizard.
_VISAR_INTERIOR_KEYS = ('interior_niveles', 'interior_estimado_m2', 'interior_proxy')
_VISAR_EXTERIOR_KEYS = ('exterior_band_id', 'exterior_rodea')
# El acuse del aviso de valoración. Separado de las claves del corte porque no se
# limpia en los mismos sitios: donde muere el corte muere el acuse (va dentro de
# `_VISAR_CUT_KEYS`), pero además hay pasos que NO tumban el corte y sí tienen que
# volver a avisar — cambiar de tramo puede meter un corte por área nuevo.
_VISAR_ACK_KEYS = ('valuation_ack',)
_VISAR_CUT_KEYS = ('requiere_valoracion', 'motivo_valoracion') + _VISAR_ACK_KEYS
_VISAR_PLAGA_KEYS = (
    'servicio_plaga', 'roedores', 'upsell_cebaderos', 'upsell_tapon',
    'upsell_guardapolvo') + _VISAR_CUT_KEYS

# Al (re)enviar un paso, se limpian estas claves de selección (dependencias que quedan
# inválidas si esa respuesta cambia). Además, los pasos en _VISAR_CLEARS_TIERS limpian
# todos los tramos elegidos (tier_*). Solo se limpia lo realmente dependiente: cambiar
# interior NO invalida exterior (mediciones independientes).
# La póliza y los extras se cotizan sobre los items resueltos: cualquier paso que
# los cambie invalida lo elegido, o se cobraría el precio de otra configuración.
#
# `extras_ids` NO es lo que se compra —eso es `booking['extras_accepted']`— sino
# la marca de "este paso ya se contestó". Sin ella, "no quiero ningún extra" y
# "todavía no le he preguntado" son el mismo estado (una lista vacía), y al
# corregir cualquier cosa se le volvía a preguntar.
_VISAR_POLIZA_KEYS = ('poliza_plan_id', 'extras_ids')
_VISAR_STEP_CLEARS = {
    'services': ('motivo',) + _VISAR_PLAGA_KEYS + ('cobertura',)
                + _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS + _VISAR_POLIZA_KEYS,
    'motivo': _VISAR_PLAGA_KEYS,
    'plagas': _VISAR_PLAGA_KEYS + _VISAR_POLIZA_KEYS,
    'cobertura': _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS
                 + _VISAR_POLIZA_KEYS,
    'group': _VISAR_INTERIOR_KEYS + _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS
             + _VISAR_POLIZA_KEYS,
    # Interior y dimensiones NO limpian el corte por calificación —cambiar unos
    # metros no desdice que el cliente reportó termitas— pero SI el acuse: un
    # tramo nuevo puede meter un corte por área (`is_valuation`), y el aviso de
    # ese corte todavía no se ha dado.
    'interior': _VISAR_INTERIOR_KEYS + _VISAR_ACK_KEYS + _VISAR_POLIZA_KEYS,
    'exterior': _VISAR_EXTERIOR_KEYS + _VISAR_CUT_KEYS + _VISAR_POLIZA_KEYS,
    'dimensiones': _VISAR_ACK_KEYS + _VISAR_POLIZA_KEYS,
}
_VISAR_CLEARS_TIERS = ('services', 'cobertura', 'group')

# Paréntesis final del nombre de un tramo ("… (valoración técnica)"). Se recorta
# para la etiqueta del chat y se reemplaza por un subtítulo escrito aquí.
_VISAR_TRAILING_PAREN = re.compile(r'\s*\([^()]*\)\s*$')

# Categorías de plaga que SÍ se atienden con el tabulador (no cortan a valoración).
VISAR_PLAGA_CATEGORIES = ('rastreros', 'voladores', 'roedores')

# Opciones que cortan a valoración, y con qué motivo. Solo en la rama correctiva:
# en preventivo el cliente no está reportando una plaga, está contratando protección.
VISAR_PLAGA_CUTS = (
    ('termitas', 'termitas'),
    ('chinches', 'chinches'),
    ('no_se', 'plaga_no_identificada'),
)

# Cómo se le nombra al cliente el motivo del corte, en el aviso de valoración.
# Sin `_()` a nivel de módulo por lo mismo que `_VISAR_STEP_LABELS`: se evaluaría
# al importar, con el idioma equivocado. Encajan en "Para ___ necesitamos…".
_VISAR_VALUATION_REASONS = {
    'termitas': "atender termitas",
    'chinches': "atender chinches de cama",
    'plaga_no_identificada': "identificar qué plaga es",
    'area_excede_limite': "un área de ese tamaño",
}

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
    def _visar_wizard_valuation_inline(self, booking):
        """¿Este canal pregunta el aviso de valoración EN LÍNEA, o corta ahí?

        Quién lo sabe es el canal, no el flujo — igual que `needs_name`. El web
        tiene a dónde cortar: una página de aviso propia (`/wizard/valoracion-aviso`)
        y un flujo de valoración con su propia URL. El chat no tiene páginas: si el
        cuestionario se para, la conversación se para, y eso es exactamente el fallo
        que describe I-17.

        El web NUNCA pone la bandera, así que para él este paso sigue siendo
        terminal y su recorrido no cambia ni un byte.
        """
        return bool((booking or {}).get('valuation_inline'))

    @api.model
    def _visar_wizard_valuation_ack(self, booking):
        """True si el cliente ya acusó el aviso (precio + motivo)."""
        selections = (booking or {}).get('selections') or {}
        return bool(selections.get('valuation_ack'))

    @api.model
    def _visar_wizard_valuation_items(self):
        """El único `item` de una visita de valoración: precio fijo, sin medidas.

        `_visar_resolve_wizard_items` no sirve aquí y no es un descuido suyo: solo
        emite items para dimensiones con un tramo elegido (`tier_*`), y el corte por
        calificación —termitas, chinches, "no sé qué es"— **nunca elige tramo**. El
        corte existe justamente para no medir.

        Sin esto, el paso de la dirección devuelve `no_items` y la rama sigue sin
        cerrar aunque la secuencia ya llegue hasta él.

        Es la misma lista que `_agent_booking_context` armaba a mano en el módulo
        del agente. Vive aquí para que resumen, extras, cotización y horarios lean
        todos de un solo sitio.
        """
        template = self.env['product.template'].sudo()._visar_get_valuation_template()
        apt_type = self._visar_get_valuation_appointment_type()
        if not template or not apt_type:
            return []
        variant = template.product_variant_id
        if not variant:
            return []
        return [{
            'dimension_id': False,
            'tier_id': False,
            'variant_id': variant.id,
            'product_id': variant.id,
            'product_tmpl_id': template.id,
            'appointment_type_id': apt_type.id,
            'is_valuation': True,
            'quantity': 1,
        }]

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

        inline = self._visar_wizard_valuation_inline(booking)
        acked = self._visar_wizard_valuation_ack(booking)

        # Corte a valoración: atajo global para no re-preguntar mediciones cuando
        # ya se decidió que va valoración. En el chat el aviso es un paso más y,
        # una vez acusado, se sigue a la dirección (el técnico va a ir igual). En
        # el web sigue siendo terminal: allí se corta a un flujo propio.
        if selections.get('requiere_valoracion'):
            if not (inline and acked):
                return VISAR_STEP_VALUATION
            # Acusado: se salta el resto del cuestionario —no hay nada que medir—
            # y se va derecho a la dirección.
            return VISAR_STEP_ADDRESS

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

        # Corte por tramo (área fuera del tabulador): mismo trato que el de
        # calificación, pero aquí las mediciones ya se contestaron.
        if self._visar_wizard_requires_valuation(selections) and not (inline and acked):
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
    def _visar_wizard_next_pending_step(self, booking):
        """El primer paso SIN CONTESTAR de todo el cuestionario, tramo final incluido.

        `_visar_wizard_next_step` solo cubre hasta la dirección, y
        `_visar_wizard_step_after` es otra cosa: "sigue la cadena desde el paso que
        acabas de contestar". Las dos sirven para avanzar hacia adelante, y ninguna
        contesta la pregunta que hace falta al **corregir**: *¿queda algo por
        preguntar?*

        Sin ella, corregir un paso volvía a recorrer la cadena entera desde el
        principio y le preguntaba otra vez los extras y la póliza aunque no
        dependieran de lo que había cambiado. La regla que se quiere es la del
        cliente: **cambia lo que tocaste, vuelve a preguntar solo lo que dependía
        de ello, y para lo demás enséñame el resumen.** Eso ya lo consigue la poda
        (`_visar_wizard_clear_downstream`): lo que dependía perdió su respuesta y
        aquí aparece como pendiente; lo que no, conserva la suya y se salta.

        Cómo se sabe que un paso del tramo final está contestado:

        | Paso | Marca |
        |---|---|
        | `nombre` | `selections['nombre']` |
        | `extras` | la CLAVE `extras_ids` existe (aunque esté vacía) |
        | `poliza` | la CLAVE `poliza_plan_id` existe (aunque sea `False`) |

        Es la presencia de la clave, no su valor: "dije que no" y "no me lo has
        preguntado" tienen que ser estados distintos, y la poda borra la clave
        justo cuando la respuesta deja de valer.
        """
        booking = booking or {}
        step = self._visar_wizard_next_step(booking)
        if step != VISAR_STEP_ADDRESS:
            return step
        # La dirección es el paso que resuelve zona e items: sin ellos resueltos,
        # sigue pendiente por mucho que haya un texto guardado.
        if not (booking.get('delivery_address') and booking.get('zone_id')
                and booking.get('items')):
            return VISAR_STEP_ADDRESS

        selections = booking.get('selections') or {}
        if self._visar_wizard_needs_name(booking):
            return VISAR_STEP_NAME
        if ('extras_ids' not in selections
                and self._visar_wizard_extras_offers(booking)):
            return VISAR_STEP_EXTRAS
        if ('poliza_plan_id' not in selections
                and self._visar_wizard_poliza_context(booking)):
            return VISAR_STEP_POLIZA
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
        # El corte a valoración entra en la huella porque cambia el POOL: la
        # valoración tiene tipo de cita y técnicos propios. Sin esto, corregir
        # "termitas" por "cucarachas" podía dejar la misma huella —si los items
        # coincidían— y el runtime conservaría un horario apartado sobre un técnico
        # que ya no es el que va a ir.
        valuation = self._visar_wizard_requires_valuation(
            booking.get('selections') or {})
        return '%s|%s|%s' % (booking.get('zone_id') or 0, int(valuation), firma)

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
        # En valoración no hay add-ons: lo que se vende es la visita. Sin esto se
        # le ofrecían al cliente y luego `_visar_build_sale_lines` los tiraba
        # —corta en seco al ver `is_valuation`—, así que aceptaba unos extras que
        # nunca aparecían en el total. La póliza ya se guardaba así (§4.1); los
        # extras se habían quedado fuera.
        if self._visar_wizard_requires_valuation(booking.get('selections') or {}):
            return []
        zone = self.env['visar.zone'].sudo().browse(booking.get('zone_id')).exists()
        items = booking.get('items') or []
        if not zone or not items:
            return []
        return self._visar_offered_addons(
            items, zone, include_roedores=self._visar_wizard_has_roedores(booking))

    @api.model
    def _visar_wizard_extra_description(self, offer):
        """Qué dice de un add-on la línea de debajo del nombre.

        Estaba vacía, y el paso ofrecía *"Estación antirroedores"* a secas: ni
        cuántas van ni cuánto cuestan. Se aceptaba —o se rechazaba— un cargo a
        ciegas, que es justo lo que la pantalla de revisión existe para evitar.

        El precio unitario va aparte del total porque el add-on se ofrece por
        paquete (3 estaciones): sin el desglose, el total parece el precio de una.
        """
        currency = self.env['res.currency'].browse(offer.get('currency_id'))
        if not currency:
            currency = self.env.company.currency_id
        subtotal = format_amount(self.env, offer.get('subtotal') or 0.0, currency)
        quantity = offer.get('quantity') or 1
        if quantity <= 1:
            return subtotal
        return _('%(qty)s × %(unit)s · total %(subtotal)s') % {
            'qty': int(quantity) if float(quantity).is_integer() else quantity,
            'unit': format_amount(self.env, offer.get('unit_price') or 0.0, currency),
            'subtotal': subtotal,
        }

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
            VISAR_STEP_VALUATION: self._visar_wizard_answer_valuation,
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

        # En valoración lo que se vende es la VISITA, no los servicios: no hay
        # tramos elegidos que resolver, y los técnicos salen del pool de valoración
        # y no del cruce por dimensión. Es la misma bifurcación que ya hace
        # `_agent_slot_tree` al ofrecer horarios.
        valuation = self._visar_wizard_requires_valuation(selections)
        if valuation:
            items = self._visar_wizard_valuation_items()
            if not items:
                # Falta el producto o el tipo de cita de valoración: es
                # configuración, no una respuesta mala del cliente. Decirle
                # "no se pudieron resolver los servicios" le haría corregir algo
                # que no está mal.
                return booking, self._visar_wizard_error(
                    'config_missing',
                    _('Falta configurar la visita de valoración.'))
            pools = {}
            valuation_type = self._visar_get_valuation_appointment_type()
            if not valuation_type._visar_eligible_resources(zone):
                return booking, self._visar_wizard_error(
                    'no_resources',
                    _('No tenemos técnicos disponibles para una valoración en tu zona.'),
                    address=address,
                    missing_services=[_('Visita de valoración técnica')],
                    zone_id=zone.id)
        else:
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

        # Cambiar de zona invalida lo que se cotizó contra la anterior. Si la zona
        # es la misma, los extras aceptados SOBREVIVEN: este método se vuelve a
        # llamar cada vez que se corrige un paso de arriba (para recalcular los
        # items), y tirarlos ahí le volvía a preguntar por unos add-ons que ya
        # había contestado.
        previous_zone_id = booking.get('zone_id')
        misma_zona = previous_zone_id == zone.id
        if previous_zone_id and not misma_zona:
            for key in _VISAR_POLIZA_KEYS:
                selections.pop(key, None)

        return {
            # El modo lo fija QUIEN resuelve los items, que es este paso. Con
            # `valuation`, `_visar_wizard_poliza_context` corta sola (mira el modo)
            # y `_agent_booking_context` resuelve el tipo de cita y el precio de la
            # valoración en vez de los del servicio.
            'mode': 'valuation' if valuation else 'wizard',
            'master_appointment_type_id': master.id,
            'zone_id': zone.id,
            'delivery_address': address,
            'selections': selections,
            'items': items,
            'extras_accepted': (booking.get('extras_accepted') or []) if misma_zona else [],
            'service_pools': {key: pool.ids for key, pool in pools.items()},
        }, None

    @api.model
    def _visar_wizard_answer_valuation(self, booking, answer):
        """Acusar el aviso de valoración. No elige nada: dice "sí, adelante".

        Es el único paso que no recoge un dato del cliente sino que le da uno
        nuestro (el precio de la visita y por qué hace falta). Lo que se guarda es
        que ya se le dijo — sin eso, el aviso se repetiría en bucle, que es la
        otra mitad del fallo de I-17.

        `_visar_wizard_commit` corre `_visar_wizard_clear_downstream('valuation')`,
        que no tiene entrada en `_VISAR_STEP_CLEARS` y por tanto no limpia nada.
        Correcto: acusar un aviso no invalida ninguna respuesta anterior.
        """
        if not self._visar_wizard_requires_valuation(
                (booking or {}).get('selections') or {}):
            # Ya no hay corte que avisar (el cliente corrigió el paso de arriba
            # mientras tanto). No es un error del cliente: se sigue sin marcar.
            return booking, None
        return self._visar_wizard_commit(
            booking, VISAR_STEP_VALUATION, {'valuation_ack': True}), None

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
        # No se confía en lo que llega: solo lo que de verdad se ofreció, y con la
        # cantidad de la oferta (no la que mande el cliente).
        aceptados = [pid for pid in chosen if pid in offered_by_id]
        # `extras_ids` queda en `selections` para que el paso se sepa CONTESTADO
        # aunque no se haya aceptado nada. Es lo que evita volver a preguntarlo al
        # corregir otra cosa.
        booking = self._visar_wizard_commit(
            booking, VISAR_STEP_EXTRAS, {'extras_ids': aceptados})
        booking['extras_accepted'] = [
            {'product_id': pid, 'quantity': offered_by_id[pid]['quantity']}
            for pid in aceptados
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
        """Tramos del tabulador, con el paréntesis del nombre bajado a subtítulo.

        Los tramos se llaman *"Más de 1,000 m² (valoración técnica)"*, y una fila
        de WhatsApp son 24 caracteres: al cliente le llegaba **"Más de 1,000 m²
        (valo…"**, cortado justo donde empieza lo que había que entender. El
        paréntesis no se pierde, se mueve al subtítulo —que admite 72— y ahí se
        lee entero.

        La condición sale del FLAG (`is_valuation` / `is_free`), no del texto del
        paréntesis: parsear el nombre para saber qué significa sería adivinar
        sobre un campo que un consultor puede reescribir desde el backend.
        """
        return [{
            'value': tier.id,
            'label': self._visar_wizard_tier_label(tier),
            'description': self._visar_wizard_tier_description(tier),
            'm2_min': tier.m2_min,
            'm2_max': tier.m2_max,
            'is_free': tier.is_free,
            'is_valuation': tier.is_valuation,
        } for tier in tiers]

    @api.model
    def _visar_wizard_tier_label(self, tier):
        """El nombre del tramo sin el paréntesis final."""
        name = tier.name or ('%g - %g m2' % (tier.m2_min, tier.m2_max))
        return _VISAR_TRAILING_PAREN.sub('', name).strip() or name

    @api.model
    def _visar_wizard_tier_description(self, tier):
        """Lo que hay que saber del tramo antes de elegirlo."""
        if tier.is_valuation:
            return _('Requiere visita de valoración técnica')
        if tier.is_free:
            return _('Incluido sin costo')
        return ''

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
                'hint': _('Puedes seleccionar varios servicios.'),
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
                    # Corta a proposito: la fila de WhatsApp son 24 caracteres
                    # y "No estoy seguro de qué es" llegaba como "No estoy
                    # seguro de qu…". Lo que hay que entender cabe en dos
                    # palabras.
                    {'value': 'no_se', 'label': _('No estoy seguro'),
                     'description': '', 'is_valuation': True},
                ]
            return {
                'step': step_key, 'kind': 'multi', 'answer_key': 'servicio_plaga',
                'title': (_('¿Qué estás viendo en casa?') if correctivo
                          else _('¿Contra qué te gustaría protegerte?')),
                # En correctivo el cliente reporta lo que TIENE, y puede tener
                # varias cosas. En preventivo elige contra qué protegerse, y
                # "Protección general" ya cubre las tres: pedirle que marque
                # varias es empujarlo a la respuesta larga de la corta.
                'hint': (_('Puedes seleccionar varias opciones.') if correctivo
                         else _('Selecciona la opción más adecuada y da click '
                                'en "{done}".')),
                'options': options,
            }

        if step_key == 'cobertura':
            return {
                'step': step_key, 'kind': 'single', 'answer_key': 'cobertura',
                'title': _('¿Dónde fumigamos?'),
                # El tramo exterior de 0-50 m2 va incluido en el servicio, asi
                # que para la mayoria de los patios "ambos" no cuesta mas. Sin
                # decirlo, el cliente elige interior por miedo al precio.
                'hint': _('Te recomendamos fumigar tanto interior como '
                          'exterior: no hay costo adicional si tu patio o '
                          'jardín mide entre 1 y 50 m².'),
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
                'hint': _('Puedes seleccionar varios.'),
                'options': [{
                    'value': dim.id,
                    'label': dim._visar_wizard_label(),
                    'description': '',
                } for dim in group.dimension_ids.filtered('active')],
            }

        if step_key in ('dimensiones', 'interior'):
            measure = 'direct' if step_key == 'dimensiones' else 'interior'
            # `interior` mide la CASA; `dimensiones` mide lo que toque el
            # servicio (un jardín, por ejemplo), asi que no pueden preguntar lo
            # mismo. "¿De qué tamaño es el área?" a secas dejaba al cliente
            # eligiendo un tramo sin saber si contaba el terreno.
            interior = step_key == 'interior'
            payload = {
                'step': step_key, 'kind': 'measure', 'answer_key': None,
                'title': (_('¿De qué tamaño es la construcción?') if interior
                          else _('¿De qué tamaño es el área?')),
                'sections': self._visar_wizard_measure_sections(selections, measure),
                'options': [],
            }
            if interior:
                payload['hint'] = _('Son los metros construidos de tu casa, no '
                                    'los del terreno.')
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

        if step_key == VISAR_STEP_VALUATION:
            if not self._visar_wizard_valuation_inline(booking):
                # Canal que corta (el web): sin opciones, como siempre.
                return {'step': step_key, 'kind': 'terminal', 'answer_key': None,
                        'title': '', 'options': []}
            ProductTemplate = self.env['product.template'].sudo()
            zone = self.env['visar.zone'].sudo().browse(
                booking.get('zone_id')).exists()
            price = ProductTemplate._visar_valuation_price(zone or None)
            currency = (zone.pricelist_id.currency_id if zone and zone.pricelist_id
                        else self.env.company.currency_id)
            motivo = _VISAR_VALUATION_REASONS.get(
                selections.get('motivo_valoracion'),
                "lo que nos describes")
            precio = format_amount(self.env, price, currency) if price else None
            # El precio va en el titulo y no en la descripcion de la opcion: es lo
            # que el cliente necesita para decidir, y una fila de WhatsApp son 24
            # caracteres. Mismo dato que ensena el aviso del web
            # (`visar_wizard_valuation_notice`), para que los dos canales digan lo
            # mismo.
            if precio:
                title = _(
                    'Para %(motivo)s necesitamos una visita de valoración '
                    'técnica (%(precio)s). El técnico revisa en sitio y te '
                    'pasamos la propuesta. ¿Te la agendamos?'
                ) % {'motivo': motivo, 'precio': precio}
            else:
                title = _(
                    'Para %(motivo)s necesitamos una visita de valoración '
                    'técnica. El técnico revisa en sitio y te pasamos la '
                    'propuesta. ¿Te la agendamos?'
                ) % {'motivo': motivo}
            return {
                'step': step_key, 'kind': 'single', 'answer_key': 'valuation_ack',
                'title': title,
                'options': [{
                    'value': 'continuar',
                    'label': _('Sí, agendar'),
                    'description': _('Elegir día y hora'),
                }],
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
                # Los extras son OPCIONALES y el paso se cierra vacio; decirlo
                # evita el bucle de quien no quiere nada y no encuentra la salida.
                'hint': _('Si no quieres agregar nada, da click en "{done}".'),
                'options': [{
                    'value': offer['product_id'],
                    'label': offer.get('name') or '',
                    'description': self._visar_wizard_extra_description(offer),
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
