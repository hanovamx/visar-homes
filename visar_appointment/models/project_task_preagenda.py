# -*- coding: utf-8 -*-
"""Pre-agenda de las visitas de póliza (paso 2, 1-oct-2026).

De una póliza **solo la primera visita nacía con fecha**. Las demás nacían sin
agendar y nadie las agendaba: el cliente no podía (`_visar_reschedule_blocked`
las rechazaba con motivo `poliza`), el agente no podía (no tenían cita que mover)
y oficina no lo estaba haciendo a mano. El cliente pagaba tres meses por
adelantado, recibía la primera visita, y las otras dos se quedaban en un limbo
del que solo salían si alguien se acordaba de llamarle.

Esto lo cierra: un cron pre-agenda cada visita preventiva **a un mes de la
anterior, a la misma hora y con el mismo técnico** si está libre, creándole una
`calendar.event` de verdad. «De verdad» es lo que importa: hasta hoy «agendar»
una visita de póliza era escribir `planned_date_begin` en la tarea, sin cita y
sin líneas de reserva, así que **no consumía la capacidad del técnico** y la web
podía vender ese mismo hueco a otro cliente.

Vive en `visar_appointment` porque es el único módulo que ve a la vez los
primitivos de agenda (`appointment.type`, las líneas de reserva, el motor de
traslado) y el modelo de pólizas de `visar_subscription`.

Decisiones del usuario (1-oct-2026), que son las que explican lo que parece raro:

* **Rutas peores son aceptables.** No se descarta un hueco porque el traslado
  quede apretado: el cliente de suscripción tiene prioridad sobre optimizar la
  ruta. El aviso de ruta que ya existe lo marca para oficina y ya.
* **Solo preventivas.** Correctivas y garantías las sigue agendando oficina.
* **El ancla de la siguiente visita es la fecha PROPUESTA, no la real**, para que
  el calendario no derive contrato a contrato. Eso vive en
  `visar_subscription/models/sale_order.py::_visar_walk_visit_series`.
"""
import logging

from datetime import timedelta

import pytz
from dateutil.relativedelta import relativedelta

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Cuántos días por delante se recogen visitas. El horizonte DURO es
# `appointment_type.max_schedule_days` (45 en producción): más allá el nativo no
# genera huecos, así que una póliza trimestral solo pre-agenda la próxima y el
# cron recoge las demás cuando entran en ventana.
PARAM_DIAS_ANTES = 'visar.poliza.preagenda_dias_antes'
DIAS_ANTES_DEFECTO = 30

# Cuántos días se busca hueco a partir de la fecha propuesta antes de rendirse.
PARAM_DIAS_BUSQUEDA = 'visar.poliza.preagenda_dias_busqueda'
DIAS_BUSQUEDA_DEFECTO = 10

PARAM_LOTE = 'visar.poliza.preagenda_lote'
LOTE_DEFECTO = 50

PARAM_RESPONSABLE = 'visar.poliza.preagenda_responsable_id'

# El resumen de la actividad es la CLAVE de idempotencia: el mixin de avisos no
# tiene dedupe y el cron corre a diario, así que sin esto una póliza sin capacidad
# dejaría una actividad nueva cada madrugada hasta enterrar la bandeja de oficina
# —que es la forma más rápida de que un aviso útil deje de leerse.
AVISO_SIN_HUECO = "Visita de póliza sin hueco para pre-agendar"
AVISO_SIN_TECNICO = "Visita de póliza pre-agendada sin técnico"
AVISO_CHOQUE = "Visita de póliza con el hueco ya ocupado"

# El contexto que calla a `calendar.event`. Cada clave está aquí por una razón
# concreta y ninguna es decorativa:
#
#   no_mail_to_attendees  — corta los correos de invitación
#                           (`calendar/models/calendar_attendee.py::_notify_attendees`).
#                           Sin esto el cliente recibe una invitación a una cita
#                           que todavía no ha confirmado.
#   dont_notify           — salta `_setup_alarms` en create y en write.
#   mail_create_nolog     — `appointment_status` lleva `tracking=True`, así que el
#                           create dejaría un mensaje de seguimiento.
#   tracking_disable      — y `mail_create_nolog` NO basta: solo calla el create.
#                           `appointment_status` se asienta en un `write` posterior
#                           (es un computed almacenado), y ahí el seguimiento
#                           publicaba en el chatter del evento un "Reserva
#                           confirmada" con cuerpo de correo al cliente. Medido el
#                           1-oct-2026: 12 citas pre-agendadas dejaban 12 de esos
#                           mensajes. No se notificaba a nadie (0 destinatarios),
#                           pero decía que el cliente confirmó algo que no ha
#                           confirmado, y eso es lo que acabaría leyendo oficina.
#   mail_create_nosubscribe / mail_notify_author / skip_contact_description
#                         — el mismo juego que usa el core al convertir una
#                           reserva pagada en cita.
#
# ⚠️ Lo que NO se usa: el `ir.config_parameter` `calendar.block_mail`. Apagaría
# también los correos de las citas pagadas, que sí hay que mandar.
CTX_SILENCIO = {
    'mail_create_nolog': True,
    'mail_create_nosubscribe': True,
    'mail_notify_author': True,
    'skip_contact_description': True,
    'no_mail_to_attendees': True,
    'dont_notify': True,
    'tracking_disable': True,
    # `appointment_crm` abre una oportunidad de CRM por cita cuando el tipo lleva
    # `lead_create`, y en producción los cuatro tipos lo llevan. Lo para
    # `calendar_event.py::_create_lead_from_appointment`, y hace falta un guardia
    # propio porque ese módulo publica en el chatter con `_message_log`, que
    # `tracking_disable` no toca. Descubierto desplegando el 1-oct-2026.
    'visar_preagenda_sin_lead': True,
}


class ProjectTask(models.Model):
    _inherit = 'project.task'

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------

    @api.model
    def _visar_preagenda_param(self, clave, defecto):
        crudo = self.env['ir.config_parameter'].sudo().get_param(clave)
        try:
            valor = int(crudo)
        except (TypeError, ValueError):
            return defecto
        return valor if valor > 0 else defecto

    @api.model
    def _visar_preagenda_responsable(self, poliza):
        """A quién le toca enterarse. Config → vendedor de la póliza → admin.

        Nunca se devuelve vacío: una actividad sin responsable no aparece en
        ninguna bandeja, o sea que el aviso existiría solo en la base de datos.
        """
        crudo = self.env['ir.config_parameter'].sudo().get_param(PARAM_RESPONSABLE)
        try:
            usuario = self.env['res.users'].sudo().browse(int(crudo)).exists()
        except (TypeError, ValueError):
            usuario = self.env['res.users'].sudo().browse()
        if usuario:
            return usuario
        if poliza and poliza.user_id:
            return poliza.user_id
        return self.env.ref('base.user_admin')

    # ------------------------------------------------------------------
    # Lo que se sabe de la visita anterior
    # ------------------------------------------------------------------

    def _visar_preagenda_anterior(self):
        """La visita agendada de la que se copia la hora y el técnico.

        «Su serie» es por `visar_source_line_id`, igual que
        `_visar_schedule_visit_due_dates`: una póliza que no se consolidó (12 podas
        y 6 fumigaciones al año) tiene dos series de verdad, y copiarle a la poda
        la hora de la fumigación sería mezclarlas.

        Se prefiere la **anterior** (por id), y si no hay ninguna anterior se coge la
        más reciente de las posteriores. Eso último parece raro y es a propósito: lo
        que se busca aquí no es el orden de la serie sino **la hora a la que este
        cliente está acostumbrado a que lo visiten**, y la de una visita posterior ya
        agendada sirve igual de bien. La alternativa sería no tener hora de
        referencia y pre-agendar a la primera que hubiera libre.
        """
        self.ensure_one()
        hermanas = self.visar_subscription_order_id.sudo().visar_visit_ids.filtered(
            lambda t: t.visar_visit_kind == 'preventiva'
            and t.state != '1_canceled'
            and t.visar_source_line_id == self.visar_source_line_id
            and t.planned_date_begin
            and t.id != self.id
        )
        anteriores = hermanas.filtered(lambda t: t.id < self.id)
        candidatas = anteriores or hermanas
        return candidatas.sorted('planned_date_begin')[-1:] if candidatas else candidatas

    def _visar_preagenda_zona(self, anterior):
        """La zona del servicio: la de la cita anterior, si no la del CP del domicilio."""
        self.ensure_one()
        cita = anterior._visar_calendar_event() if anterior else None
        if cita and cita.visar_zone_id:
            return cita.visar_zone_id
        orden = self.visar_subscription_order_id.sudo() or self.visar_sale_order_id.sudo()
        domicilio = (orden.visar_service_partner_id if orden else None) or self.partner_id
        registro = self.env['visar.zone.cp'].sudo()._get_cp_record(
            domicilio.zip if domicilio else None)
        return registro.zone_id if registro else self.env['visar.zone'].sudo().browse()

    # ------------------------------------------------------------------
    # Elegir el hueco
    # ------------------------------------------------------------------

    @api.model
    def _visar_preagenda_arbol(self, maestro, pool, desde_utc, tz_info):
        """El árbol de huecos del nativo, pedido UNA vez para toda la cascada.

        **`reference_date` es obligatorio, no un adorno.** El core calcula
        `first_day = reference_date + min_schedule_hours`, así que sin restarle esas
        horas el día objetivo se cae del árbol y la visita se pre-agendaría siempre
        al día siguiente del que le toca.

        Y se pide el árbol en vez de preguntar solo por capacidad
        (`_visar_resource_free_at`) porque **la capacidad no sabe de horario
        laboral, descansos ni vacaciones**: eso lo aplica
        `_slots_fill_resources_availability` al generar los huecos. Preguntando solo
        por capacidad, esto pre-agendaría un domingo a las tres de la mañana.
        """
        margen = relativedelta(hours=maestro.min_schedule_hours or 0)
        referencia = desde_utc - margen
        ahora = fields.Datetime.now()
        if referencia < ahora - relativedelta(days=1):
            # Una visita ya vencida: no se busca hueco en el pasado.
            referencia = ahora
        return maestro._get_appointment_slots(
            str(tz_info), filter_resources=pool, asked_capacity=1,
            reference_date=referencia)

    @api.model
    def _visar_preagenda_huecos_por_dia(self, meses):
        """Aplana el árbol del nativo a {date: [datetime local, ...]} ordenado.

        No hace falta zona horaria: el nativo ya devuelve los huecos en la hora
        local del tipo de cita (es la que se le pasó al generarlos).
        """
        por_dia = {}
        for mes in meses or []:
            for semana in mes.get('weeks', []):
                for dia in semana:
                    if not isinstance(dia, dict):
                        continue
                    for hueco in dia.get('slots', []):
                        crudo = hueco.get('datetime')
                        if not crudo:
                            continue
                        local = fields.Datetime.from_string(crudo)
                        por_dia.setdefault(local.date(), []).append(local)
        for clave in por_dia:
            por_dia[clave].sort()
        return por_dia

    @api.model
    def _visar_preagenda_elegir(self, por_dia, objetivo, hora_objetivo, dias_busqueda):
        """El hueco: esa hora ese día → la más cercana ese día → siguiente día con hueco.

        Devuelve el `datetime` LOCAL elegido, o None.

        El orden no es negociable y es la decisión del usuario: se prefiere
        **conservar el día** aunque cambie la hora, antes que conservar la hora
        corriendo el día. Un cliente que tiene su fumigación los días 20 organiza el
        mes alrededor de eso.

        Dos detalles del límite, los dos aprendidos corrigiendo este método:

        * **Es una distancia en días naturales, no un número de días con hueco.**
          Cortar la lista por posición no limitaba nada: como se devuelve en el
          primer día con hueco, el primer elemento siempre entra, aunque caiga tres
          semanas después. El parámetro no habría hecho lo que su nombre dice, y una
          agenda llena un mes pre-agendaría a un mes en vez de rendirse y avisar a
          oficina — justo la decisión que `_visar_preagendar` documenta.
        * **La ventana se ancla en `max(propuesta, hoy)`.** Hay visitas VENCIDAS (181
          en producción al 1-oct-2026) y para esas la propuesta está en el pasado:
          anclando en ella, la ventana entera caería antes de hoy, no habría ni un
          candidato y el aviso diría «no hay capacidad» cuando el problema es otro.
          Para una visita vencida lo que toca es el primer hueco que haya.
        """
        hoy = fields.Date.context_today(self)
        base = max(objetivo, hoy)
        candidatos = sorted(d for d in por_dia
                            if base <= d <= base + timedelta(days=dias_busqueda))
        for dia in candidatos:
            huecos = por_dia.get(dia) or []
            if not huecos:
                continue
            if dia == objetivo and hora_objetivo is not None:
                exacto = [h for h in huecos
                          if (h.hour, h.minute) == (hora_objetivo.hour, hora_objetivo.minute)]
                if exacto:
                    return exacto[0]
            if hora_objetivo is None:
                return huecos[0]
            # La más cercana a la hora de siempre, por distancia real en minutos.
            referencia = hora_objetivo.hour * 60 + hora_objetivo.minute
            return min(huecos, key=lambda h: abs(h.hour * 60 + h.minute - referencia))
        return None

    # ------------------------------------------------------------------
    # El punto de entrada
    # ------------------------------------------------------------------

    def _visar_preagendar(self):
        """Le pone fecha, técnico y CITA PROPIA a una visita de póliza.

        Devuelve el `calendar.event` creado, o un recordset vacío si no se pudo.

        ------------------------------------------------------------------
        EL LÍMITE DURO: aquí NO se sobrevende a un técnico
        ------------------------------------------------------------------
        La decisión original del usuario (1-oct-2026) fue **«nunca se deja sin
        pre-agendar»**. Este método hace una **excepción explícita y única** a esa
        regla, aprobada por el usuario el mismo día con la condición de que quedara
        bien documentada:

        > Si en `visar.poliza.preagenda_dias_busqueda` días no hay un hueco con
        > capacidad libre, la visita **se queda sin fecha**, con una actividad para
        > oficina **vencida hoy** (es la única urgencia que `mail.activity` sabe
        > expresar: no tiene campo de prioridad en Odoo 19) y una nota en la póliza.

        El motivo: la única forma de «nunca dejarla sin fecha» cuando no hay
        capacidad sería escribirle una hora a un técnico que ya está ocupado. Eso
        destruye lo único que esta pieza existe para lograr —que la visita OCUPE
        agenda de verdad— y convierte un rezago visible (malo, pero conocido) en dos
        clientes esperando al mismo técnico a la misma hora (peor, y además
        invisible hasta que el técnico no llega).

        «Rutas peores sí, capacidad inexistente no.» Una ruta apretada la absorbe el
        técnico conduciendo más; una hora ya vendida no la absorbe nadie.

        Lo mismo está escrito, con el razonamiento completo, en
        `.context/35-polizas.md` (sección del paso 2), y cada vez que ocurre queda
        en la actividad de oficina —que dice **cuántos días** se buscaron y **en qué
        zona**, no solo que no se pudo— y en el chatter de la póliza.

        La prueba `test_sin_capacidad_no_sobrevende` fija esta conducta. Si alguien
        la borra «arreglando» el caso sin hueco, lo que vuelve es la sobreventa.
        """
        self.ensure_one()
        AptType = self.env['appointment.type'].sudo()

        # Idempotencia: el cron corre a diario y no se vuelve a tocar lo hecho.
        if self.planned_date_begin or self.visar_visit_event_id:
            return self.env['calendar.event'].browse()
        if not self.visar_visit_due_date:
            return self.env['calendar.event'].browse()

        maestro = AptType._visar_get_master_appointment_type()
        if not maestro:
            _logger.warning("Pre-agenda: no hay tipo de cita maestro configurado")
            return self.env['calendar.event'].browse()

        anterior = self._visar_preagenda_anterior()
        zona = self._visar_preagenda_zona(anterior)
        if not zona:
            # No es falta de capacidad, es falta de DATO: sin zona no se sabe a qué
            # técnicos preguntar. Decirlo distinto importa, porque lo que hay que
            # arreglar es otra cosa (el CP del domicilio, o la cita anterior).
            self._visar_preagenda_sin_hueco(
                zona, 0,
                motivo=_("no se pudo determinar la zona del domicilio (ni la cita "
                         "anterior la tiene, ni el código postal está en el "
                         "catálogo de zonas)"))
            return self.env['calendar.event'].browse()
        pool = maestro._visar_eligible_resources(zona)
        if not pool:
            self._visar_preagenda_sin_hueco(
                zona, 0,
                motivo=_("no hay ningún técnico dado de alta para esa zona con este "
                         "servicio"))
            return self.env['calendar.event'].browse()

        tz_info = pytz.timezone(maestro.appointment_tz or 'America/Monterrey')
        objetivo = self.visar_visit_due_date
        hora_objetivo = None
        if anterior and anterior.planned_date_begin:
            hora_objetivo = pytz.utc.localize(
                anterior.planned_date_begin).astimezone(tz_info)

        desde_utc = tz_info.localize(
            fields.Datetime.to_datetime('%s 00:00:00' % objetivo)
        ).astimezone(pytz.utc).replace(tzinfo=None)

        dias_busqueda = self._visar_preagenda_param(
            PARAM_DIAS_BUSQUEDA, DIAS_BUSQUEDA_DEFECTO)
        meses = self._visar_preagenda_arbol(maestro, pool, desde_utc, tz_info)
        por_dia = self._visar_preagenda_huecos_por_dia(meses)
        elegido_local = self._visar_preagenda_elegir(
            por_dia, objetivo, hora_objetivo, dias_busqueda)
        if not elegido_local:
            self._visar_preagenda_sin_hueco(zona, dias_busqueda)
            return self.env['calendar.event'].browse()

        inicio = tz_info.localize(elegido_local).astimezone(pytz.utc).replace(tzinfo=None)
        duracion = maestro.appointment_duration or 1.0
        cita_anterior = anterior._visar_calendar_event() if anterior else None
        if cita_anterior and cita_anterior.duration:
            duracion = cita_anterior.duration
        fin = inicio + relativedelta(hours=duracion)

        recursos = self._visar_preagenda_tecnico(
            maestro, pool, anterior, cita_anterior, inicio, fin)
        if not recursos:
            # El árbol decía que había hueco y al revalidar ya no. Pasa si otro
            # cliente reservó entre el cálculo y ahora, y es justo por lo que se
            # revalida: crear la cita igual sería la sobreventa que el método evita.
            self._visar_preagenda_sin_hueco(
                zona, dias_busqueda,
                motivo=_("el hueco se ocupó mientras se calculaba"))
            return self.env['calendar.event'].browse()

        return self._visar_preagenda_crear(maestro, recursos, zona, inicio, fin, duracion)

    def _visar_preagenda_tecnico(self, maestro, pool, anterior, cita_anterior,
                                 inicio, fin):
        """El MISMO técnico de la visita anterior si está libre; si no, el menos cargado.

        «El mismo» se busca por **dos caminos**, y el segundo hace falta más de lo
        que parece:

        1. los recursos de la CITA anterior;
        2. si la visita anterior no tiene cita —la agendó oficina a mano, que es
           justo el caso que esta pieza viene a sustituir— los recursos cuyo empleado
           esté en su `visar_technician_ids`.

        Sin el segundo camino, la primera pre-agenda de cualquier póliza cuyo
        historial lo llevara oficina perdía al técnico de siempre y caía en «el menos
        cargado». Y el dato estaba ahí todo el tiempo: la tarea sí sabe qué técnico
        fue, aunque no haya cita de la que deducirlo.

        Se revalida la capacidad aunque el árbol acabe de decir que hay hueco: el
        cron puede ir lento y el wizard web **no crea apartado**, así que entre el
        cálculo y el `create` cabe una reserva ajena.
        """
        self.ensure_one()
        AptType = self.env['appointment.type'].sudo()

        candidatos = (cita_anterior.appointment_resource_ids
                      if cita_anterior else self.env['appointment.resource'].browse())
        if not candidatos and anterior:
            empleados = anterior.visar_technician_ids
            if empleados:
                candidatos = pool.filtered(lambda r: r.visar_employee_id in empleados)
        if candidatos:
            mismo = candidatos.filtered(
                lambda r: r in pool
                and AptType._visar_resource_free_at(maestro, r, inicio, fin, 1))
            if mismo:
                return mismo[:1]
        return AptType._visar_pick_resources_for_slot(
            maestro, {'poliza': pool}, inicio, fin, 1)

    def _visar_preagenda_crear(self, maestro, recursos, zona, inicio, fin, duracion,
                               preagendada=True):
        """Crea la cita CALLADA y la engancha a la visita.

        El orden de las escrituras importa y no es casual:

        1. la bandera `visar_visit_preagendada` y el enlace a la cita, **antes** de
           sincronizar fechas. Si se hiciera al revés, `_visar_sync_fsm_tasks`
           escribe `planned_date_begin`, eso dispara el recálculo de la serie, y el
           recálculo —viendo una visita agendada SIN la bandera— borraría la fecha
           propuesta, que es justo el ancla que la decisión del usuario quiere
           conservar;
        2. la sincronización de fechas y técnicos desde la cita;
        3. la actividad y las notas.
        """
        self.ensure_one()
        cliente = self._visar_preagenda_cliente()
        lineas = maestro._visar_booking_line_values(recursos, inicio, fin, asked_capacity=1)

        vals = {
            'name': self.name or _("Visita de póliza"),
            'start': inicio,
            'stop': fin,
            'allday': False,
            'duration': duracion,
            'appointment_type_id': maestro.id,
            'appointment_status': 'booked',
            'booking_line_ids': [(0, 0, v) for v in lineas],
            'user_id': (self.visar_subscription_order_id.user_id.id
                        or self.env.ref('base.user_admin').id),
            # ⚠️ La lista VACÍA explícita, y es lo contrario de lo que parece.
            # `appointment/models/calendar_event.py::_compute_alarm_ids` copia
            # `appointment_type_id.reminder_ids` **cuando el campo viene vacío**, así
            # que OMITIR `alarm_ids` es justo lo que engancha el recordatorio.
            # Pasando un valor explícito el compute no corre. Medido el 1-oct-2026:
            # sin esta línea la cita salía con 1 alarma pese a `dont_notify`.
            'alarm_ids': [(5, 0, 0)],
        }
        if zona:
            vals['visar_zone_id'] = zona.id
        if cliente:
            # Las COORDENADAS. `_visar_travel_stop_partners` llega al domicilio por
            # `sale_order_line_ids` → `visar_fsm_task_ids` → `partner_ids`, y la
            # primera vía no existe en una póliza: sin partner la parada queda sin
            # coordenadas y el motor de rutas lee el día entero como vacío.
            vals['partner_ids'] = [(6, 0, cliente.ids)]

        cita = self.env['calendar.event'].sudo().with_context(**CTX_SILENCIO).create(vals)

        # `preagendada=False` lo usa la migración de las visitas que YA tenían
        # fecha: esa fecha la puso una persona, no el robot a partir de una
        # propuesta, y marcarla haría que la serie anclara en una propuesta que
        # quizás ni existe.
        self.with_context(visar_sin_aviso_ruta=True, visar_poliza_due_sync=True).sudo().write({
            'visar_visit_event_id': cita.id,
            'visar_visit_preagendada': preagendada,
            'visar_visit_preagenda_confirmada': False,
        })
        cita.with_context(visar_sin_aviso_ruta=True,
                          **CTX_SILENCIO)._visar_sync_fsm_tasks()

        empleados = recursos.mapped('visar_employee_id').filtered(lambda e: e.id)
        if not empleados:
            # La cita ocupa agenda pero la visita queda sin técnico en la app de
            # campo. No se descarta el recurso (sería inconsistente con el web, que
            # tampoco lo filtra), pero alguien tiene que enterarse.
            self._visar_preagenda_actividad(
                AVISO_SIN_TECNICO,
                _("La visita quedó pre-agendada para el %(cuando)s, pero el recurso "
                  "«%(recurso)s» no tiene empleado asociado, así que el servicio no "
                  "le va a aparecer a nadie en la app de campo. Hay que enlazar el "
                  "recurso con su empleado, o asignar el técnico a mano.",
                  cuando=self._visar_preagenda_cuando(cita),
                  recurso=', '.join(recursos.mapped('display_name'))))

        self._visar_preagenda_nota(cita, recursos)
        return cita

    def _visar_preagenda_cliente(self):
        """El domicilio de servicio de la póliza; si no, el contacto de la tarea."""
        self.ensure_one()
        orden = self.visar_subscription_order_id.sudo()
        domicilio = orden.visar_service_partner_id if orden else None
        return domicilio or self.partner_id

    def _visar_preagenda_cuando(self, cita):
        """La fecha de la cita en palabras y en la zona horaria del cliente."""
        if not cita or not cita.start:
            return ''
        return fields.Datetime.context_timestamp(
            cita.sudo(), cita.start).strftime('%d/%m/%Y %H:%M')

    # ------------------------------------------------------------------
    # Lo que ve oficina
    # ------------------------------------------------------------------

    def _visar_preagenda_actividad(self, resumen, nota, prioridad=False):
        """Una actividad por motivo y por visita, UNA sola vez.

        La idempotencia es la mitad que importa: el cron corre a diario y una
        póliza sin capacidad volvería a fallar mañana. Sin esta comprobación, la
        bandeja de oficina acumularía la misma actividad cada madrugada.

        `prioridad=True` **no escribe un campo `priority`: `mail.activity` no tiene
        ninguno** en Odoo 19 (comprobado el 1-oct-2026; intentarlo levanta
        `ValueError: Invalid field 'priority'`). La urgencia se expresa con el único
        mecanismo que el modelo ofrece y que oficina ya lee: `date_deadline` hoy, que
        es lo que la pinta como vencida y la sube al principio de la bandeja.
        """
        self.ensure_one()
        modelo = self.env['ir.model']._get_id('project.task')
        existe = self.env['mail.activity'].sudo().search_count([
            ('res_model_id', '=', modelo),
            ('res_id', '=', self.id),
            ('summary', '=', resumen),
        ])
        if existe:
            return False
        responsable = self._visar_preagenda_responsable(
            self.visar_subscription_order_id.sudo())
        return self.sudo().activity_schedule(
            'mail.mail_activity_data_todo',
            summary=resumen,
            note=Markup("<p>%s</p>") % nota,
            user_id=responsable.id,
            date_deadline=fields.Date.context_today(self) if prioridad else None)

    def _visar_preagenda_sin_hueco(self, zona, dias, motivo=None):
        """No se pudo: la visita se queda SIN fecha, y alguien se entera. Ver `_visar_preagendar`.

        La actividad dice **por qué** y con qué números, no solo que no se pudo: es
        la diferencia entre una tarea accionable («contratar o liberar capacidad en
        la zona B») y una queja. Y la nota va también al chatter de la PÓLIZA, para
        que quede en el expediente del cliente y no solo en la bandeja de alguien.
        """
        self.ensure_one()
        explicacion = motivo or _(
            "no hay capacidad libre en los próximos %(dias)s días desde la fecha "
            "propuesta", dias=dias)
        texto = _(
            "A esta visita le toca el %(propuesta)s y no se pudo pre-agendar: "
            "%(motivo)s, en la zona «%(zona)s».\n\n"
            "Se dejó SIN fecha a propósito. La alternativa sería darle una hora a un "
            "técnico que ya está ocupado, y entonces dos clientes esperarían al mismo "
            "técnico a la misma vez. Hay que agendarla a mano, mover otra visita, o "
            "liberar capacidad en esa zona.",
            propuesta=fields.Date.to_string(self.visar_visit_due_date),
            motivo=explicacion,
            zona=zona.display_name if zona else _("sin determinar"))
        # La nota va colgada de que la ACTIVIDAD sea nueva. El cron corre a diario y
        # una póliza sin capacidad va a volver a fallar mañana: sin esta condición,
        # el expediente del cliente acumularía la misma nota cada madrugada hasta
        # que no se pudiera leer nada más en él.
        nueva = self._visar_preagenda_actividad(AVISO_SIN_HUECO, texto, prioridad=True)
        poliza = self.visar_subscription_order_id.sudo()
        if nueva and poliza:
            poliza.message_post(
                body=Markup("<p><b>%s</b></p><p>%s</p>") % (
                    _("Visita sin pre-agendar: %s", self.name or ''),
                    texto.split('\n\n')[0]),
                subtype_xmlid='mail.mt_note')

    def _visar_preagenda_nota(self, cita, recursos):
        """Deja constancia en la visita y en la póliza. Nadie confirmó nada todavía."""
        self.ensure_one()
        cuerpo = Markup("<p><b>%s</b></p><p>%s</p><p>%s</p>") % (
            _("Visita pre-agendada automáticamente"),
            _("Quedó para el %(cuando)s con %(quien)s, a partir de la fecha "
              "propuesta (%(propuesta)s).",
              cuando=self._visar_preagenda_cuando(cita),
              quien=', '.join(recursos.mapped('display_name')) or _("sin técnico"),
              propuesta=fields.Date.to_string(self.visar_visit_due_date)),
            _("El cliente TODAVÍA no lo ha confirmado. La cita ya ocupa la agenda "
              "del técnico para que nadie más pueda reservar ese hueco."))
        self.sudo().message_post(body=cuerpo, subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # Las visitas que YA tenían fecha (migración 19.0.2.31.0)
    # ------------------------------------------------------------------

    def _visar_preagenda_cita_para_fecha_existente(self):
        """Le crea su cita a una visita que YA tiene fecha, sin moverla.

        Devuelve `True` si la creó, `False` si el hueco está ocupado (y deja
        actividad), `None` si no había nada que hacer.

        En producción había 40 visitas de póliza con `planned_date_begin` y sin
        `calendar.event`: tenían fecha, pero **no consumían la capacidad del
        técnico**, así que la web podía vender ese mismo hueco a otro cliente. Esto
        las pone a ocupar agenda.

        **La fecha NO se toca nunca.** Si el hueco choca con algo ya reservado, la
        visita se queda exactamente como está y queda una actividad para oficina. El
        cliente ya tiene esa fecha: moverle la cita por detrás durante un `-u` es lo
        peor que podría hacer esta migración, y «el técnico está doble» es un
        problema que una persona resuelve en dos minutos sabiendo cuál mover.
        """
        self.ensure_one()
        if self.visar_visit_event_id or not self.planned_date_begin:
            return None
        if self.planned_date_begin <= fields.Datetime.now():
            # Una cita para una visita que ya pasó no protege ninguna capacidad: el
            # día ya se gastó. Solo ensuciaría el calendario del técnico con horas
            # que no existen.
            return None
        AptType = self.env['appointment.type'].sudo()
        maestro = AptType._visar_get_master_appointment_type()
        if not maestro:
            return None

        anterior = self._visar_preagenda_anterior()
        zona = self._visar_preagenda_zona(anterior)
        pool = (maestro._visar_eligible_resources(zona) if zona
                else self.env['appointment.resource'].browse())
        if not pool:
            return None

        inicio = self.planned_date_begin
        duracion = maestro.appointment_duration or 1.0
        fin = self.date_deadline or (inicio + relativedelta(hours=duracion))
        if fin <= inicio:
            fin = inicio + relativedelta(hours=duracion)
        duracion = (fin - inicio).total_seconds() / 3600.0

        # Se prefiere el técnico que la visita YA tiene asignado: la cita debe
        # reflejar la realidad que oficina montó, no reasignarla.
        empleados = self.visar_technician_ids
        recursos = pool.filtered(lambda r: r.visar_employee_id in empleados) if empleados else pool.browse()
        candidatos = recursos or pool
        libres = candidatos.filtered(
            lambda r: AptType._visar_resource_free_at(maestro, r, inicio, fin, 1))
        if not libres:
            self._visar_preagenda_actividad(
                AVISO_CHOQUE,
                _("Esta visita ya tenía fecha (%(cuando)s) pero no tenía cita, así "
                  "que no estaba ocupando la agenda de nadie. Al intentar crearle la "
                  "cita, el hueco ya estaba reservado por otro servicio.\n\n"
                  "La visita se dejó TAL COMO ESTÁ: nadie le cambia la fecha a un "
                  "cliente por detrás. Hay que decidir a mano cuál de los dos "
                  "servicios se mueve.",
                  cuando=fields.Datetime.to_string(inicio)),
                prioridad=True)
            return False

        self._visar_preagenda_crear(maestro, libres[:1], zona, inicio, fin, duracion,
                                    preagendada=False)
        return True

    # ------------------------------------------------------------------
    # El cron
    # ------------------------------------------------------------------

    @api.model
    def _visar_cron_preagenda(self, limit=None):
        """Pre-agenda las visitas preventivas que entran en ventana. Idempotente.

        **Corre de madrugada a propósito.** El wizard web no crea apartado
        (`visar.slot.hold`) mientras el cliente paga, así que si el cron le quita el
        hueco a una reserva en vuelo, `_filter_unavailable_bookings` la descarta
        **después de cobrar** — el desastre que ya está documentado en
        `visar_appointment/models/calendar_booking.py`. De noche no hay reservas en
        vuelo.

        **Los avisos de ruta van en lote, al final.** Cada `write` normal abriría su
        propio presupuesto de llamadas a Mapbox (`max_calls`), así que una corrida de
        50 visitas podría costar cientos de llamadas. Con
        `visar_sin_aviso_ruta=True` en todas las escrituras y un solo
        `_visar_ruta_revisar()` al cerrar, las 50 comparten un único presupuesto.
        """
        hoy = fields.Date.context_today(self)
        dias = self._visar_preagenda_param(PARAM_DIAS_ANTES, DIAS_ANTES_DEFECTO)
        lote = limit or self._visar_preagenda_param(PARAM_LOTE, LOTE_DEFECTO)

        self._visar_preagenda_chequeo_config()

        visitas = self.sudo().search([
            ('visar_subscription_order_id', '!=', False),
            ('visar_visit_kind', '=', 'preventiva'),
            ('planned_date_begin', '=', False),
            ('visar_visit_event_id', '=', False),
            ('visar_visit_due_date', '!=', False),
            ('visar_visit_due_date', '<=', hoy + relativedelta(days=dias)),
            ('visar_visit_due_out_of_term', '=', False),
            ('state', 'not in', ('1_canceled', '1_done')),
            # **`subscription_state`, no `state`.** Una póliza dada de baja se queda
            # en `state='sale'` con `subscription_state='6_churn'`: en `visar-test`
            # son 41 de 68. Filtrando por `state` se le mandaría un técnico a
            # clientes que cancelaron. Es el mismo criterio que usa el resto de
            # `visar_subscription` (`sale_order.py`, tres sitios).
            ('visar_subscription_order_id.subscription_state', '=', '3_progress'),
        ], limit=lote, order='visar_visit_due_date, id')

        hechas = self.browse()
        for visita in visitas:
            # Un savepoint por visita: una póliza mal configurada no tumba la
            # corrida entera y las demás siguen agendándose.
            try:
                with self.env.cr.savepoint():
                    if visita._visar_preagendar():
                        hechas |= visita
            except Exception:  # noqa: BLE001 - una visita no tumba el cron
                _logger.exception(
                    "Pre-agenda: falló la visita %s (póliza %s)",
                    visita.id, visita.visar_subscription_order_id.display_name)

        if hechas:
            hechas._visar_ruta_revisar()
        _logger.info("Pre-agenda de pólizas: %s de %s visitas pre-agendadas",
                     len(hechas), len(visitas))
        return len(hechas)

    @api.model
    def _visar_preagenda_chequeo_config(self):
        """Avisa UNA vez por corrida de la configuración que rompería la pre-agenda.

        Dos cosas, y las dos son silenciosas si no se miran:

        * **`lead_create` en el tipo maestro.** `appointment_crm` crea un lead de CRM
          por cada cita cuyo tipo lo tenga activo, y además le escribe
          `res_model_id`/`res_id`, lo que genera una `mail.activity` automática. Con
          él encendido, cada visita pre-agendada ensucia el embudo con una
          oportunidad que nadie pidió. Hoy está apagado y nadie lo puso así a
          propósito, que es exactamente por lo que hace falta el aviso.
        * **El tipo maestro ausente de los servicios de los técnicos.** La cita se
          crea igual, pero el cliente nunca vería horarios para moverla.
        """
        maestro = self.env['appointment.type'].sudo()._visar_get_master_appointment_type()
        if not maestro:
            return
        if maestro.lead_create:
            _logger.warning(
                "Pre-agenda: el tipo de cita maestro (%s) tiene `lead_create` "
                "activo. Cada visita pre-agendada va a crear una oportunidad de CRM "
                "y una actividad que nadie pidió. Apágalo.", maestro.display_name)
        habilitados = self.env['appointment.resource'].sudo().search_count([
            ('visar_service_ids', 'in', maestro.id)])
        if not habilitados:
            _logger.warning(
                "Pre-agenda: NINGÚN técnico lleva el tipo maestro (%s) en "
                "`visar_service_ids`, así que `_visar_eligible_resources` devuelve "
                "vacío para toda zona y no se va a pre-agendar nada. Es "
                "configuración, no falta de capacidad.", maestro.display_name)
