# -*- coding: utf-8 -*-
"""¿Le da tiempo al técnico de llegar? — el predicado de factibilidad de ruta.

Implementa el §5 del diseño 33 (decisiones 7, 9 y 14). Hasta ahora se ofrecía
**cualquier** horario con capacidad libre, sin mirar si el técnico podía llegar
desde su parada anterior. Eso funciona mientras haya un solo técnico con mediana
de 2.5 paradas al día — y deja de funcionar en cuanto entre el segundo.

## La aritmética, y por qué no es un radio

El bloque de una hora **no** es una hora de servicio: son **20 min de traslado +
40 min de servicio** (confirmado con Visar el 19-ago-2026). Para un slot candidato
que empieza en `T`, con el compromiso anterior terminando en `E`:

    presupuesto de viaje = 20 min + (T − E)

- **Pegados** (`T = E`): 20 min justos. Es el caso que aprieta.
- **Con hueco** (`T > E`): el hueco se suma. Un trayecto de 40 min es
  perfectamente ofrecible si el técnico tiene la mañana libre por delante.

Es un **presupuesto entre paradas, no un radio de servicio** (decisión 14). No
existe un tope duro de "nunca a más de 20 min": lo que no se puede es **comerse el
traslado de otra cita**. La consecuencia buscada es que la disponibilidad dependa
de **quién reservó antes** — el primero que llega se lo lleva — y al cliente no
hay nada que explicarle, porque **nunca ve la opción que no cabe**.

## Los bordes del día no se restringen (decisión 9)

Solo se valida el viaje **entre** paradas. Si el slot cae antes de todas, solo se
exige `viaje(nuevo → primera)`; si cae después, solo `viaje(última → nuevo)`.

Con el modelo de presupuesto esto deja de ser una concesión y pasa a ser lo
correcto: un tope duro sí habría obligado a modelar de dónde sale el técnico —y el
primer trayecto del día es justo el más largo—, pero un presupuesto mide lo que una
parada nueva **le quita a la siguiente**, y la primera parada del día no le quita
el traslado a nadie. Efecto práctico: no hace falta geocodificar "Visar Home".

## Degradar, nunca bloquear (§5.4)

Sin coordenadas del destino, sin token, con Mapbox caído o con una parada sin
geocodificar → **el horario se ofrece igual**. Con el 77.6% de direcciones
geocodificadas medido en servidor, esta rama corre en ~1 de cada 4 casos: no es
excepcional, es normal. Una falla de geocodificación no puede costar una reserva.

## Lo que este módulo NO hace, a propósito

**No se re-valida el traslado al apartar ni al cobrar.** El filtro es del
*listado*. Rechazar un horario ya pagado porque el presupuesto cambió desde que se
listó es el fallo T3f otra vez —dinero dentro, cita fuera— y es peor que servir una
ruta apretada. Si alguien viene a "completar" esto enganchándolo en
`agent_hold_slot` o en `agent_prepare_booking`, que lea esta línea primero.
"""
import logging

import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.addons.visar_base.models.visar_travel import visar_round_up_minutes

_logger = logging.getLogger(__name__)

# Interruptor. Nace ENCENDIDO: el §10 del diseño 33 dice que el flag es "para
# desplegarlo con cuidado, no para dejarlo apagado", y el código que nace apagado
# se pudre. Existe para apagarlo en caliente si Mapbox se porta mal.
TRAVEL_ENABLED_PARAM = 'visar.travel.enabled'
# Los minutos de TRASLADO dentro del bloque. La parte de servicio se deriva
# (`appointment_duration * 60 − esto`): guardar los dos números es invitarlos a
# divergir, y la decisión 7 dice que el bloque sale de la configuración del tipo
# de cita, no del código.
TRAVEL_MINUTES_PARAM = 'visar.travel.minutes'
# Tope de llamadas a Matrix por corrida. El web pinta un MES entero, no los ~10
# días de una conversación de chat, así que hace falta una red.
TRAVEL_MAX_CALLS_PARAM = 'visar.travel.matrix_max_calls'
# Permite apagar solo la geocodificación bajo demanda (la parte con latencia) y
# quedarse con el centroide del CP.
TRAVEL_GEOCODE_PARAM = 'visar.travel.geocode_address'

DEFAULT_TRAVEL_MINUTES = 20
DEFAULT_MAX_CALLS = 12


class AppointmentType(models.Model):
    _inherit = 'appointment.type'

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------

    @api.model
    def _visar_travel_enabled(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_ENABLED_PARAM, '1')
        return str(raw).strip().lower() not in ('0', 'false', '')

    @api.model
    def _visar_travel_minutes(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_MINUTES_PARAM, DEFAULT_TRAVEL_MINUTES)
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            return DEFAULT_TRAVEL_MINUTES

    @api.model
    def _visar_travel_max_calls(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_MAX_CALLS_PARAM, DEFAULT_MAX_CALLS)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_MAX_CALLS

    # ------------------------------------------------------------------
    # De dónde salen las coordenadas
    # ------------------------------------------------------------------

    @api.model
    def _visar_travel_destination(self, booking):
        """(lat, lng) del domicilio del cliente, o None. NUNCA lanza.

        Orden, del dato mejor al peor:

        1. la dirección exacta, geocodificada y cacheada;
        2. el **centroide del CP** (§4.0/§5.4), que no es la casa pero está mucho
           más cerca que rendirse;
        3. `None`.

        `None` significa "no filtres". Es la mitad de la regla de §5.4, y con 1 de
        cada 4 direcciones sin geocodificar es una rama normal, no un caso raro.

        Con el filtro apagado se sale de inmediato: geocodificar cuesta una
        llamada de red, y no tiene sentido pagarla para un dato que nadie va a
        mirar.
        """
        if not self._visar_travel_enabled():
            return None
        booking = booking or {}
        address = booking.get('delivery_address') or {}
        Cache = self.env['visar.travel.cache'].sudo()
        Mapbox = self.env['visar.mapbox.service']
        cp = (address.get('zip') or '').strip()

        geocode_on = str(self.env['ir.config_parameter'].sudo().get_param(
            TRAVEL_GEOCODE_PARAM, '1')).strip().lower() not in ('0', 'false', '')

        if geocode_on:
            partes = [p for p in (
                ' '.join(filter(None, [address.get('street'),
                                       address.get('ext_num')])),
                address.get('neighborhood'), cp, 'Nuevo León', 'México',
            ) if p and str(p).strip()]
            if len(partes) > 2:
                key = 'addr:' + '|'.join(str(p).strip().lower() for p in partes)
                cached = Cache._visar_point_get(key)
                if cached:
                    return cached
                found = Mapbox._visar_mapbox_geocode(', '.join(str(p) for p in partes))
                if found:
                    lat, lng, _kind = found
                    Cache._visar_point_store(key, lat, lng)
                    return lat, lng

        if cp:
            cp_record = self.env['visar.zone.cp'].sudo()._get_cp_record(cp)
            if cp_record:
                return cp_record._visar_centroid()
        return None

    @api.model
    def _visar_travel_stop_coords(self, event):
        """Coordenadas del domicilio de servicio de una cita ya confirmada.

        Tres candidatos, en orden de fiabilidad. `None` es una respuesta válida:
        una parada sin coordenadas **no impone ninguna restricción**, que es
        preferible a inventarse una.
        """
        if not event:
            return None
        # 1. La dirección de servicio del pedido: la crea
        #    `_visar_apply_delivery_address` y es LA dirección a la que se va.
        for line in (event.sale_order_line_ids or []):
            partner = line.order_id.partner_shipping_id
            if partner and (partner.partner_latitude or partner.partner_longitude):
                return partner.partner_latitude, partner.partner_longitude
        # 2. El partner de la tarea FSM, que es lo que `visar_field_app` ya
        #    geocodifica hoy (el 77.6% medido sale de ahí).
        for task in (event.visar_fsm_task_ids or []):
            partner = task.partner_id
            if partner and (partner.partner_latitude or partner.partner_longitude):
                return partner.partner_latitude, partner.partner_longitude
        # 3. Cualquier asistente con coordenadas, descartando empleados.
        for partner in (event.partner_ids or []):
            if partner.employee_ids:
                continue
            if partner.partner_latitude or partner.partner_longitude:
                return partner.partner_latitude, partner.partner_longitude
        return None

    @api.model
    def _visar_travel_window(self, months, tz_info):
        """(desde, hasta) en UTC que abarca el árbol de slots, o None.

        Acota la consulta de paradas a lo que de verdad se está ofreciendo. Sin
        esto se leería el histórico entero de `appointment.booking.line` para
        proteger el traslado de unas citas que ya ocurrieron.
        """
        stamps = []
        for month in months or []:
            for week in month.get('weeks', []):
                for day in week:
                    if not isinstance(day, dict):
                        continue
                    for slot in (day.get('slots') or []):
                        if slot.get('datetime'):
                            stamps.append(
                                fields.Datetime.from_string(slot['datetime']))
        if not stamps:
            return None
        # Un día de margen a cada lado: la parada que condiciona el primer slot
        # puede ser de la víspera en hora local.
        desde = tz_info.localize(min(stamps)).astimezone(
            pytz.utc).replace(tzinfo=None) - relativedelta(days=1)
        hasta = tz_info.localize(max(stamps)).astimezone(
            pytz.utc).replace(tzinfo=None) + relativedelta(days=1)
        return desde, hasta

    @api.model
    def _visar_travel_stops_by_day(self, resources, tz_info, window=None):
        """{(resource_id, fecha_local): [(inicio, fin, coords|None), ...]}

        ⚠️ **La carga por técnico sale SOLO de `appointment.booking.line` →
        `appointment.resource`.** Nunca de `project.task.user_ids`: en la base real
        hay 83 tareas activas asignadas a *admin*, 4 a `__system__` y 61 a nadie,
        así que ese campo diría que el técnico tiene el día libre (§5.3.2).

        Un solo `search()` para todo el árbol, agrupado en memoria. La alternativa
        —una consulta por (día, técnico)— es justo el patrón que
        `visar.slot.hold._visar_snapshot` ya tuvo que corregir cuando costó medio
        segundo por página.
        """
        if not resources:
            return {}
        domain = [
            ('appointment_resource_id', 'in', resources.ids),
            ('event_start', '!=', False),
        ]
        if window:
            domain += [('event_start', '<=', window[1]),
                       ('event_stop', '>=', window[0])]
        lines = self.env['appointment.booking.line'].sudo().search(
            domain, order='event_start')
        stops = {}
        for line in lines:
            start, stop = line.event_start, line.event_stop
            if not start or not stop:
                continue
            local_day = pytz.utc.localize(start).astimezone(tz_info).date()
            key = (line.appointment_resource_id.id, local_day)
            stops.setdefault(key, []).append(
                (start, stop, self._visar_travel_stop_coords(line.calendar_event_id)))
        for key in stops:
            stops[key].sort(key=lambda row: row[0])
        return stops

    # ------------------------------------------------------------------
    # El predicado
    # ------------------------------------------------------------------

    @api.model
    def _visar_travel_slot_fits(self, stops, start_utc, stop_utc, durations,
                                budget_minutes):
        """¿Cabe `[start_utc, stop_utc)` entre las paradas de ese día?

        `durations` es {índice de parada: (minutos_hacia_la_parada,
        minutos_desde_la_parada)}, ya resueltos. Aritmética pura: ninguna llamada
        de red pasa por aquí.
        """
        anterior = siguiente = None
        for index, (s_start, s_stop, _coords) in enumerate(stops):
            if s_stop <= start_utc:
                anterior = index
            elif s_start >= stop_utc:
                siguiente = index
                break
            else:
                # Se solapa con un compromiso. La capacidad ya debería haberlo
                # excluido; si llega aquí, no se adivina: no cabe.
                return False

        if anterior is not None:
            viaje = (durations.get(anterior) or (None, None))[1]
            if viaje is not None:
                hueco = (start_utc - stops[anterior][1]).total_seconds() / 60.0
                if viaje > budget_minutes + hueco:
                    return False

        if siguiente is not None:
            viaje = (durations.get(siguiente) or (None, None))[0]
            if viaje is not None:
                hueco = (stops[siguiente][0] - stop_utc).total_seconds() / 60.0
                if viaje > budget_minutes + hueco:
                    return False

        # Sin parada anterior no hay traslado que proteger; sin parada siguiente,
        # tampoco. Es la decisión 9, y con el modelo de presupuesto sale sola.
        return True

    @api.model
    def _visar_travel_durations(self, stops, destination, budget):
        """{índice: (min_hacia_la_parada, min_desde_la_parada)} para un día.

        **Una sola llamada a Matrix por (día, técnico)**, con `[destino, *paradas]`
        como coordenadas: se lee la fila 0 (destino → parada) y la columna 0
        (parada → destino), y con eso **todos** los slots de ese día se resuelven
        con aritmética. Es el control de costo del §5.3.

        `budget` es un contador mutable de la corrida: sirve de tope y de
        interruptor de circuito. Un token muerto no puede convertirse en 30
        timeouts al pintar un calendario.
        """
        con_coords = [(index, coords) for index, (_s, _e, coords)
                      in enumerate(stops) if coords]
        if not con_coords:
            return {}
        if budget.get('degraded'):
            return {}
        if budget.get('calls', 0) >= budget.get('max_calls', DEFAULT_MAX_CALLS):
            if not budget.get('capped'):
                budget['capped'] = True
                _logger.info(
                    "Factibilidad de traslado: alcanzado el tope de %s llamadas a "
                    "Matrix; el resto de los días se ofrecen sin filtrar.",
                    budget.get('max_calls'))
            return {}

        Cache = self.env['visar.travel.cache'].sudo()
        pares = []
        for _index, coords in con_coords:
            pares.append((destination, coords))
            pares.append((coords, destination))
        cached = Cache._visar_travel_get(pares)

        faltan = [par for par in pares
                  if Cache._visar_travel_key(*par) not in cached]
        if faltan:
            budget['calls'] = budget.get('calls', 0) + 1
            coords_list = [destination] + [coords for _i, coords in con_coords]
            matrix = self.env['visar.mapbox.service']._visar_mapbox_matrix(coords_list)
            if not matrix:
                # Ni castigo ni premio: este día no impone restricción. Y se corta
                # el resto de la corrida, para no repetir una llamada que falla.
                budget['degraded'] = True
                return {}
            nuevos = {}
            for position, (_index, coords) in enumerate(con_coords, start=1):
                ida = matrix[0][position]
                vuelta = matrix[position][0]
                if ida is not None:
                    nuevos[Cache._visar_travel_key(destination, coords)] = int(ida)
                if vuelta is not None:
                    nuevos[Cache._visar_travel_key(coords, destination)] = int(vuelta)
            Cache._visar_travel_store(nuevos)
            cached.update(nuevos)

        durations = {}
        for index, coords in con_coords:
            hacia = cached.get(Cache._visar_travel_key(destination, coords))
            desde = cached.get(Cache._visar_travel_key(coords, destination))
            durations[index] = (
                visar_round_up_minutes(hacia) if hacia is not None else None,
                visar_round_up_minutes(desde) if desde is not None else None,
            )
        return durations

    # ------------------------------------------------------------------
    # La pasada sobre el árbol de slots
    # ------------------------------------------------------------------

    @api.model
    def _visar_slot_resource_ids(self, slot):
        """Técnicos del slot, venga del filtro Visar o del árbol nativo.

        Los dos árboles traen la lista con nombres distintos —el multi-servicio ya
        eligió (`available_resources`), el nativo aún ofrece candidatos
        (`available_resource_ids`)— y las dos pasadas necesitan leerla. Vive aquí
        y no en `visar_whatsapp_agent` porque dos copias de un accesor "de
        cualquiera de las dos formas" es exactamente como divergen las formas.
        """
        resources = slot.get('available_resources')
        if resources:
            return [res['id'] for res in resources if res.get('id')]
        raw = slot.get('available_resource_ids')
        return raw.ids if hasattr(raw, 'ids') else list(raw or [])

    @api.model
    def _visar_filter_slots_travel(self, master_type, months, timezone,
                                   destination, require='all'):
        """Poda el árbol de slots por factibilidad de ruta.

        `require`:

        * **`'all'`** — árbol multi-servicio. Su `available_resources` ya fue
          **elegido** para cubrir TODOS los servicios, así que el slot solo
          sobrevive si todos son factibles, y la lista **no se poda**: quitar uno
          rompería la cobertura y obligaría a reescribir `url_parameters`.
        * **`'any'`** — árbol de valoración. Ahí la lista son **candidatos**: basta
          uno factible, y los que no lo son **se quitan**, porque el runtime toma
          `resource_ids[0]` y si no apartaría un técnico que no llega.

        Hoy, con un solo técnico usable, las dos reglas coinciden y la realidad no
        las distingue. De ahí que cada una lleve su prueba.
        """
        if not months or not destination or not self._visar_travel_enabled():
            return months
        if not self.env['visar.mapbox.service']._visar_mapbox_token():
            return months

        tz_info = pytz.timezone(timezone or master_type.appointment_tz or 'UTC')
        budget_minutes = self._visar_travel_minutes()
        budget = {'calls': 0, 'max_calls': self._visar_travel_max_calls(),
                  'degraded': False, 'capped': False}

        resource_ids = {
            rid
            for month in months
            for week in month.get('weeks', [])
            for day in week
            if isinstance(day, dict)
            for slot in (day.get('slots') or [])
            for rid in self._visar_slot_resource_ids(slot)
        }
        resources = self.env['appointment.resource'].sudo().browse(
            sorted(resource_ids))
        stops_by_day = self._visar_travel_stops_by_day(
            resources, tz_info, window=self._visar_travel_window(months, tz_info))
        if not stops_by_day:
            # Nadie tiene nada agendado: no hay traslado que proteger y no se
            # gasta ni una llamada. Es el caso (a) del §5.1, y con mediana de 2.5
            # paradas/día es el caso más frecuente.
            return months

        durations_cache = {}
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
                    for slot in (day.get('slots') or []):
                        kept = self._visar_travel_keep_slot(
                            master_type, slot, tz_info, stops_by_day,
                            durations_cache, destination, budget_minutes, budget,
                            require)
                        if kept is not None:
                            new_slots.append(kept)
                    day_copy['slots'] = new_slots
                    if new_slots:
                        month_has_avail = True
                    new_week.append(day_copy)
                new_weeks.append(new_week)
            filtered_months.append({
                **month, 'weeks': new_weeks, 'has_availabilities': month_has_avail,
            })
        return filtered_months

    @api.model
    def _visar_travel_keep_slot(self, master_type, slot, tz_info, stops_by_day,
                                durations_cache, destination, budget_minutes,
                                budget, require):
        """El slot tal cual, una copia con menos técnicos, o None si no cabe."""
        dt_str = slot.get('datetime')
        if not dt_str:
            return slot
        duration = float(slot.get('slot_duration')
                         or master_type.appointment_duration or 1.0)
        start_local = fields.Datetime.from_string(dt_str)
        start_utc = tz_info.localize(start_local).astimezone(
            pytz.utc).replace(tzinfo=None)
        stop_utc = start_utc + relativedelta(hours=duration)
        local_day = start_local.date()

        factibles = []
        for rid in self._visar_slot_resource_ids(slot):
            stops = stops_by_day.get((rid, local_day)) or []
            if not stops:
                factibles.append(rid)
                continue
            cache_key = (rid, local_day)
            if cache_key not in durations_cache:
                durations_cache[cache_key] = self._visar_travel_durations(
                    stops, destination, budget)
            if self._visar_travel_slot_fits(
                    stops, start_utc, stop_utc, durations_cache[cache_key],
                    budget_minutes):
                factibles.append(rid)

        if require == 'all':
            todos = self._visar_slot_resource_ids(slot)
            return slot if len(factibles) == len(todos) else None

        if not factibles:
            return None
        slot_copy = dict(slot)
        if slot.get('available_resources'):
            slot_copy['available_resources'] = [
                res for res in slot['available_resources']
                if res.get('id') in factibles]
        else:
            slot_copy['available_resource_ids'] = factibles
        return slot_copy
