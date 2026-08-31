# -*- coding: utf-8 -*-
"""La cita reservada, y moverla de fecha sin romper nada de lo que cuelga.

Hasta agosto de 2026 **nada reagendaba nada**. El boton "el cliente no llego" de
la app de campo solo levanta una bandera y avisa a oficina —lo dice su propio
codigo: *"No reagenda el calendario (eso lo hace gestion en el backend)"*—, y el
asistente web no tiene camino de vuelta. Mover una cita era trabajo a mano.

## Lo que hay que mover, y por que no es solo la fecha

Una cita pagada es **cuatro cosas enlazadas**, y solo la primera se arrastra sola:

  1. `calendar.event` — su `start`/`stop`. Es lo unico que se escribe directo.
  2. `appointment.booking.line` — sus fechas son campos **related almacenados**
     del evento, asi que viajan gratis. El **recurso** no: si el horario nuevo lo
     sirve otro tecnico, hay que reescribirlo o la carga queda contada en la
     persona equivocada, que es de donde sale la factibilidad de ruta.
  3. `project.task` — la tarea del tecnico. Sus fechas son una **copia**, no un
     enlace: `_visar_enrich_fsm_tasks` las escribe una vez, al confirmar el
     pedido. Mover el evento sin tocar la tarea deja al tecnico con la hora vieja
     en su app y sin enterarse de nada.
  4. Los **tecnicos asignados** de esa tarea, por lo mismo del punto 2.

Que el punto 3 sea una copia y no un related es la trampa de este archivo. Si
algun dia se convierte en related, esta clase se simplifica sola; mientras tanto,
quien anada un campo derivado de la fecha tiene que anadirlo aqui tambien.

## Polizas: fuera de alcance, y bloqueadas a proposito

En una poliza solo la PRIMERA visita hereda fecha y tecnicos del evento; "las
demas visitas del ciclo nacen sin agendar" y no tienen `calendar.event` que
mover. Reagendar una poliza no es este problema —es *agendar* lo que nunca tuvo
fecha— asi que se rechaza con un motivo propio en vez de moverse a medias.
"""
from odoo import api, fields, models

# Horas minimas de antelacion, en las DOS puntas: para poder mover la cita
# actual, y para el horario nuevo que se elija. Editable sin desplegar.
RESCHEDULE_MIN_HOURS_PARAM = 'visar.reschedule.min_hours'
DEFAULT_MIN_HOURS = 24

# Cuantas veces puede moverse UNA cita antes de que haga falta una persona. Sin
# tope, una cita rebota por la agenda comiendose huecos que otros habrian usado.
RESCHEDULE_MAX_PARAM = 'visar.reschedule.max_times'
DEFAULT_MAX_TIMES = 2

# Motivos por los que una cita NO se puede mover. El agente los traduce a algo
# que un cliente entienda; aqui son claves estables para poder probarlas.
MOTIVOS = (
    'sin_fecha',        # todavia no tiene horario que mover
    'ya_paso',          # la cita ya ocurrio
    'muy_proxima',      # faltan menos de N horas
    'limite',           # se agotaron los cambios permitidos
    'poliza',           # visita de poliza: fuera de alcance
    'cancelada',        # el evento esta archivado
)


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    visar_zone_id = fields.Many2one(
        'visar.zone', string="Zona (Visar)",
        help="Zona geográfica respondida por el cliente al agendar.")
    visar_booking_items = fields.Json(
        string="Servicios reservados (Visar)",
        help="Snapshot de los servicios/variantes elegidos en el wizard multi-servicio (D-05).")

    visar_reschedule_count = fields.Integer(
        string="Veces reagendada", default=0, readonly=True, copy=False,
        help="Cuántas veces se ha movido esta cita a petición del cliente. Al "
             "llegar al tope, el cambio lo tiene que hacer un asesor.")

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------

    @api.model
    def _visar_reschedule_min_hours(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            RESCHEDULE_MIN_HOURS_PARAM, DEFAULT_MIN_HOURS)
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            return DEFAULT_MIN_HOURS

    @api.model
    def _visar_reschedule_max(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            RESCHEDULE_MAX_PARAM, DEFAULT_MAX_TIMES)
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            return DEFAULT_MAX_TIMES

    # ------------------------------------------------------------------
    # ¿Se puede mover?
    # ------------------------------------------------------------------

    def _visar_reschedule_blocked(self, nuevo_inicio=None):
        """Motivo por el que esta cita NO se puede mover, o None.

        Se pregunta DOS veces y por eso acepta `nuevo_inicio`: al listar los
        servicios —para no ofrecer un cambio imposible— y otra vez al confirmar,
        ya con el horario elegido, porque entre una cosa y otra el cliente
        estuvo conversando y el reloj corrio.
        """
        self.ensure_one()
        if not self.active:
            return 'cancelada'
        if not self.start:
            return 'sin_fecha'
        if self._visar_is_subscription_visit():
            return 'poliza'

        ahora = fields.Datetime.now()
        if self.start <= ahora:
            return 'ya_paso'

        minimo = self._visar_reschedule_min_hours()
        limite = fields.Datetime.add(ahora, hours=minimo)
        # Punta 1: la cita ACTUAL tiene que estar suficientemente lejos.
        if self.start < limite:
            return 'muy_proxima'
        # Punta 2: y el horario NUEVO tambien. Decision de negocio (ago-2026):
        # un cambio a ultima hora desordena la ruta del tecnico igual de tarde
        # se pida desde donde se pida.
        if nuevo_inicio is not None and nuevo_inicio < limite:
            return 'muy_proxima'

        if self.visar_reschedule_count >= self._visar_reschedule_max():
            return 'limite'
        return None

    def _visar_is_subscription_visit(self):
        """¿Esta cita pertenece a una póliza? Entonces no es de este flujo."""
        self.ensure_one()
        lineas = self.env['sale.order.line'].sudo().search([
            ('calendar_event_id', '=', self.id),
        ])
        return any(linea.order_id.plan_id for linea in lineas)

    # ------------------------------------------------------------------
    # Mover
    # ------------------------------------------------------------------

    def _visar_reschedule(self, start, stop, resource_ids=None):
        """Mueve la cita y todo lo que cuelga de ella. Devuelve (ok, motivo).

        **No cobra ni reembolsa nada**: el servicio ya está pagado y lo único
        que cambia es cuándo se presta. Por eso tampoco toca el pedido.

        Se vuelve a comprobar el bloqueo aquí dentro, con el horario nuevo ya
        conocido: quien llama pudo preguntar hace diez minutos.
        """
        self.ensure_one()
        motivo = self._visar_reschedule_blocked(nuevo_inicio=start)
        if motivo:
            return False, motivo

        anterior = self.start
        self.sudo().write({
            'start': start,
            'stop': stop,
            'visar_reschedule_count': self.visar_reschedule_count + 1,
        })

        # Las fechas de las líneas de reserva son related almacenados y ya
        # viajaron con el write de arriba. El recurso no.
        if resource_ids:
            lineas = self.env['appointment.booking.line'].sudo().search([
                ('calendar_event_id', '=', self.id),
            ])
            nuevos = self.env['appointment.resource'].sudo().browse(
                resource_ids).exists()
            if nuevos and lineas and set(lineas.mapped(
                    'appointment_resource_id').ids) != set(nuevos.ids):
                # Un recurso por línea: se reasigna la primera y se descartan las
                # sobrantes solo si el horario nuevo necesita menos técnicos.
                for linea, recurso in zip(lineas, nuevos):
                    linea.write({'appointment_resource_id': recurso.id})
                self.sudo().write({
                    'appointment_resource_ids': [(6, 0, nuevos.ids)]})

        self._visar_sync_fsm_tasks()
        self._visar_log_reschedule(anterior)
        return True, None

    def _visar_sync_fsm_tasks(self):
        """Reescribe fecha y técnicos en las tareas de campo de esta cita.

        Es la mitad que se olvida. `_visar_enrich_fsm_tasks` copió estos valores
        una sola vez al confirmar el pedido, así que sin esto el técnico abre su
        app y sigue viendo la hora vieja — y se presenta cuando no toca.
        """
        self.ensure_one()
        lineas = self.env['sale.order.line'].sudo().search([
            ('calendar_event_id', '=', self.id),
        ])
        tareas = lineas.mapped('task_id').filtered(lambda t: t.id)
        if not tareas:
            return

        vals = {}
        if self.start:
            vals['planned_date_begin'] = self.start
        if self.stop:
            vals['date_deadline'] = self.stop

        empleados = (self.appointment_resource_ids
                     .mapped('visar_employee_id').filtered(lambda e: e.id))
        if empleados:
            vals['visar_technician_ids'] = [(6, 0, empleados.ids)]
            usuarios = empleados.mapped('user_id').filtered(lambda u: u.id).ids
            if usuarios:
                vals['user_ids'] = [(6, 0, usuarios)]
        if vals:
            tareas.sudo().write(vals)

    def _visar_log_reschedule(self, anterior):
        """Deja rastro en el chatter. Nunca lanza: una nota no bloquea un cambio."""
        self.ensure_one()
        try:
            self.sudo().message_post(body=(
                "Cita reagendada por el cliente desde WhatsApp.<br/>"
                "Antes: %s<br/>Ahora: %s<br/>Cambios usados: %s de %s."
                % (anterior or "sin fecha", self.start,
                   self.visar_reschedule_count, self._visar_reschedule_max())))
        except Exception:  # noqa: BLE001 - la nota es rastro, no el trabajo
            pass
