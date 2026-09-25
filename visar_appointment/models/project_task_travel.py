# -*- coding: utf-8 -*-
"""Avisar cuando una tarea asignada a mano rompe las reglas de ruta.

## El hueco que cierra

Las reglas de traslado (`visar_travel_feasibility.py`) deciden **qué horarios ve
el cliente**: en el wizard web y en el agente de WhatsApp, un horario al que el
técnico no llegaría simplemente no se ofrece. Pero cuando oficina asigna o mueve
un servicio **a mano dentro de Odoo** —otro técnico, otro día, arrastrándolo en la
planificación— no pasaba nada: ahí no hay listado que filtrar, así que las reglas
no se consultaban ni para avisar.

## Avisa, no bloquea (decisión de Visar, 24-sep-2026)

Una ruta apretada puede ser exactamente lo que oficina quiso: un cliente que
insiste, una emergencia, un técnico que ese día ya andaba por allá. Impedir el
guardado convertiría una regla de rentabilidad en una pared, y una pared que
estorba acaba apagada. Así que la tarea se guarda **siempre** y el aviso queda:

* en el campo `visar_ruta_aviso`, que la vista pinta como recuadro amarillo;
* como nota en el chatter cuando aparece —y cuando se resuelve—, para que quede
  el rastro de que se supo.

## Cuándo se calcula

**Al guardar, y solo lo que cambió**: el técnico, las fechas o el cliente de la
tarea. Es lo que pidió Visar, y es lo que hace que el aviso sirva —quien está
asignando lo lee en el momento, no un cuarto de hora después—. El costo se acota
solo: el presupuesto de llamadas a Mapbox es el mismo mecanismo del listado, y
una cita sin coordenadas, sin token o con Mapbox caído **no dice nada**.

Tres cosas que NO se evalúan, y por qué:

* **Las tareas que nacen de una reserva** (`visar_sin_aviso_ruta`): ese camino ya
  pasó por el filtro al listar el horario. Volver a preguntarlo es pagar una
  llamada para confirmar lo que ya se sabe, y encima dentro del cobro.
* **Lo que ya pasó.** Un aviso sobre un servicio de la semana pasada no tiene
  dueño ni arreglo posible; es ruido.
* **Las tareas que no son de campo.** Una tarea de oficina no tiene ruta.
"""
import logging

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Campos que cambian la respuesta. Cambiar el nombre de la tarea, su etapa o su
# descripción no mueve a nadie por la ciudad.
VISAR_RUTA_DISPARADORES = (
    'visar_technician_ids', 'planned_date_begin', 'date_deadline', 'partner_id')

# Contexto para no evaluar: lo pone el camino de la reserva, que ya filtró.
VISAR_RUTA_SIN_AVISO = 'visar_sin_aviso_ruta'


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_ruta_aviso = fields.Text(
        string="Aviso de ruta", readonly=True, copy=False,
        help="Qué regla de traslado no cumple esta asignación. Es un aviso: no "
             "impide guardar ni cambiar nada. Vacío = la ruta cuadra, o no se "
             "pudo comprobar (sin dirección geocodificada, por ejemplo).")

    # ------------------------------------------------------------------
    # Qué se le pregunta al motor
    # ------------------------------------------------------------------

    def _visar_ruta_evaluable(self):
        """¿Tiene sentido comprobarle la ruta a esta tarea?"""
        self.ensure_one()
        if not self.project_id.is_fsm:
            return False
        if not self.planned_date_begin:
            return False
        return self.planned_date_begin >= fields.Datetime.now()

    def _visar_ruta_cita(self):
        """La cita detrás de la tarea, si viene de una reserva. Puede no haber."""
        self.ensure_one()
        eventos = self.visar_sale_line_ids.mapped('calendar_event_id')
        return eventos[:1]

    def _visar_ruta_recursos(self):
        """Los recursos de agenda de los técnicos asignados.

        La carga de un técnico se mide por `appointment.resource` —es lo que
        `_visar_travel_stops_by_day` sabe leer— y la asignación de campo se hace
        por empleado, así que hay que cruzarlas. Si la tarea no tiene técnicos
        pero sí cita, manda la cita: es quien de verdad ocupa la agenda.
        """
        self.ensure_one()
        Resource = self.env['appointment.resource'].sudo()
        if self.visar_technician_ids:
            return Resource.search(
                [('visar_employee_id', 'in', self.visar_technician_ids.ids)])
        return self._visar_ruta_cita().appointment_resource_ids

    def _visar_ruta_destino(self, cita):
        """Coordenadas del domicilio del servicio, o None."""
        self.ensure_one()
        AptType = self.env['appointment.type'].sudo()
        if cita:
            coords = AptType._visar_travel_stop_coords(cita)
            if coords:
                return coords
        return AptType._visar_travel_partner_coords(self.partner_id)

    def _visar_ruta_avisos(self, budget=None):
        """Los motivos por los que esta tarea no cuadra. `[]` = cuadra o no se sabe."""
        self.ensure_one()
        AptType = self.env['appointment.type'].sudo()
        cita = self._visar_ruta_cita()
        recursos = self._visar_ruta_recursos()
        if not recursos:
            return []
        inicio = self.planned_date_begin
        fin = self.date_deadline or inicio
        if fin <= inicio:
            # Sin duración no hay tramo que medir: se le da el bloque de la cita,
            # y si tampoco hay cita, el del tipo maestro (que es de donde sale el
            # reparto 20/40 del listado).
            maestro = AptType._visar_get_master_appointment_type()
            horas = (cita.duration if cita and cita.duration
                     else (maestro.appointment_duration or 1.0))
            fin = fields.Datetime.add(inicio, hours=horas)
        return AptType._visar_travel_avisos(
            self._visar_ruta_destino(cita), recursos, inicio, fin,
            ignorar_event_id=cita.id or None, budget=budget)

    # ------------------------------------------------------------------
    # Lo que ve oficina
    # ------------------------------------------------------------------

    def _visar_ruta_revisar(self):
        """Recalcula el aviso y lo cuenta en el chatter si cambió.

        **Nunca lanza.** Es un aviso colgado de un guardado ajeno: si el motor
        falla, lo que no puede pasar es que se caiga el guardado de la tarea.
        """
        AptType = self.env['appointment.type'].sudo()
        budget = {'calls': 0, 'max_calls': AptType._visar_travel_max_calls()}
        for task in self:
            try:
                avisos = (task._visar_ruta_avisos(budget=budget)
                          if task._visar_ruta_evaluable() else [])
            except Exception:  # noqa: BLE001 - un aviso no tumba un guardado
                _logger.exception(
                    "No se pudo revisar la ruta de la tarea %s", task.id)
                continue
            texto = "\n".join(avisos)
            if texto == (task.visar_ruta_aviso or ''):
                continue
            task.with_context(**{VISAR_RUTA_SIN_AVISO: True}).sudo().write(
                {'visar_ruta_aviso': texto})
            task._visar_ruta_nota(avisos)

    def _visar_ruta_nota(self, avisos):
        """La nota del chatter. Tambien cuando el aviso DESAPARECE.

        Decir solo lo malo deja a quien corrigió sin saber si acertó, y el campo
        vaciándose en silencio se lee como que el aviso "se perdió".
        """
        self.ensure_one()
        if avisos:
            cuerpo = Markup("<p><b>%s</b></p><ul>%s</ul><p>%s</p>") % (
                _("Ojo con la ruta de este servicio"),
                Markup("").join(Markup("<li>%s</li>") % a for a in avisos),
                _("Se guardó igual: es un aviso, no un bloqueo."))
        else:
            cuerpo = Markup("<p>%s</p>") % _("La ruta de este servicio ya cuadra.")
        self.message_post(body=cuerpo)

    # ------------------------------------------------------------------
    # Los dos caminos por los que cambia una asignación
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        if not self.env.context.get(VISAR_RUTA_SIN_AVISO):
            tasks._visar_ruta_revisar()
        return tasks

    def write(self, vals):
        result = super().write(vals)
        if (not self.env.context.get(VISAR_RUTA_SIN_AVISO)
                and any(campo in vals for campo in VISAR_RUTA_DISPARADORES)):
            self._visar_ruta_revisar()
        return result


class CalendarEvent(models.Model):
    """Mover la cita tambien cambia la ruta de sus tareas."""

    _inherit = 'calendar.event'

    # Lo que mueve a alguien por la ciudad: cuando es, y quien va.
    VISAR_RUTA_DISPARADORES = ('start', 'stop', 'appointment_resource_ids')

    def write(self, vals):
        mueve = any(campo in vals for campo in self.VISAR_RUTA_DISPARADORES)
        # Los días de ANTES, que hay que revisar igual: una cita que se va deja de
        # estorbarle a las vecinas, y el aviso de aquellas se queda mintiendo.
        afectados = self._visar_ruta_afectadas() if mueve else self.env['project.task']
        result = super().write(vals)
        if mueve and not self.env.context.get(VISAR_RUTA_SIN_AVISO):
            # Incluye el reagendado por WhatsApp, que a proposito NO revalida la
            # factibilidad (§5 "no se re-valida al apartar ni al cobrar") — y es
            # justo el caso en que un aviso vale: la cita ya se movio.
            (afectados | self._visar_ruta_afectadas())._visar_ruta_revisar()
        return result

    def _visar_ruta_afectadas(self):
        """Tareas cuyo aviso puede haber cambiado porque esta cita se mueve.

        Las suyas propias y las del MISMO técnico ese día: una parada no solo se
        aleja o se acerca ella, tambien libera o estorba el traslado de sus
        vecinas. Se acota al día ±1 y a los técnicos de la cita; las distancias
        ya están en caché, así que revisarlas casi nunca cuesta una llamada.
        """
        tareas = self.visar_fsm_task_ids
        empleados = self.appointment_resource_ids.mapped('visar_employee_id')
        fechas = [event.start for event in self if event.start]
        if not (empleados and fechas):
            return tareas
        Task = self.env['project.task'].sudo()
        return tareas | Task.search([
            ('visar_technician_ids', 'in', empleados.ids),
            ('planned_date_begin', '>=', min(fechas) - relativedelta(days=1)),
            ('planned_date_begin', '<=', max(fechas) + relativedelta(days=1)),
        ])
