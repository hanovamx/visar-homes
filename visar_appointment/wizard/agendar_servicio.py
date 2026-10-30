# -*- coding: utf-8 -*-
"""Agendar a mano con guía: los horarios que el web y el agente ofrecerían (29-oct-2026).

## El hueco que cierra

Cuando la venta entra por el web o por WhatsApp, el cliente solo ve horarios en los
que el técnico de su zona está libre, trabaja y le da tiempo de llegar. Cuando
oficina hace la venta a mano en Ventas, el servicio nace sin fecha y alguien le
escribe una en la tarea **a ciegas**. Y esa fecha escrita a mano no crea cita ni
líneas de reserva, así que **no ocupa la agenda del técnico**: el web y el agente
podían vender esa misma hora a otro cliente. Es el mismo agujero que la pre-agenda
de pólizas cerró el 1-oct para las visitas de póliza
(`project_task_preagenda.py`), abierto todavía para la venta suelta.

## Lo que hace

Un botón «Agendar» en el servicio (y en la orden confirmada) abre esta guía:

* lista los huecos del **mismo motor** que el web y el agente — zona por CP,
  técnicos de la zona, horario laboral y descansos del recurso de agenda,
  capacidad ya reservada y apartados vivos, y las dos reglas de ruta;
* quita además los huecos que caen en una **ausencia del empleado** (Empleados →
  Ausencias / tiempo libre del recurso del empleado). El web todavía no la mira:
  el recurso de agenda y el del empleado son registros distintos;
* al elegir uno crea la **cita de verdad**, callada, y la engancha a la tarea, que
  toma fecha y técnico de la cita como en una reserva del web.

Escribir la fecha a mano en la tarea sigue funcionando igual que antes, con el
aviso de ruta de siempre. Esto guía; no le quita nada a oficina.

## Lo que NO hace, a propósito

* **No le avisa al cliente.** Ni correo ni WhatsApp: oficina está hablando con él
  cuando agenda a mano. Es lo mismo que pasaba al escribir la fecha.
* **No mueve citas existentes.** Si el servicio ya tiene cita, se reagenda por el
  camino de siempre; aquí no se ofrece el botón.
"""
import pytz
from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_datetime

from odoo.addons.project.models.project_task import CLOSED_STATES

from ..models.project_task_preagenda import CTX_SILENCIO
from ..models.visar_travel_feasibility import TIER_CON_TRABAJO

# Cuántos días se enseñan a partir de «Desde». Dos semanas bastan para elegir y
# mantienen la lista legible; quien necesite más lejos mueve «Desde».
DIAS_A_MOSTRAR = 14


class VisarAgendarServicio(models.TransientModel):
    _name = 'visar.agendar.servicio'
    _description = "Agendar servicio con guía de horarios"

    task_id = fields.Many2one(
        'project.task', string="Servicio", required=True, ondelete='cascade',
        domain="[('id', 'in', tareas_posibles_ids)]")
    tareas_posibles_ids = fields.Many2many(
        'project.task', string="Servicios de la orden sin cita")
    # Para la vista: el cliente web no evalúa `len()` en `readonly`.
    varias_tareas = fields.Boolean(compute='_compute_varias_tareas')
    zona_id = fields.Many2one(
        'visar.zone', string="Zona",
        help="Sale del código postal del domicilio del servicio. Se puede cambiar "
             "si el CP no está en el catálogo.")
    recurso_id = fields.Many2one(
        'appointment.resource', string="Técnico",
        help="Vacío = todos los técnicos de la zona.")
    desde = fields.Date(string="Desde", required=True,
                        default=fields.Date.context_today)
    duracion = fields.Float(
        string="Duración (horas)",
        help="Cuánto bloquea el servicio en la agenda del técnico.")
    hueco_ids = fields.One2many(
        'visar.agendar.servicio.hueco', 'wizard_id', string="Horarios libres")
    nota = fields.Text(string="Nota", readonly=True)

    # ------------------------------------------------------------------
    # Abrir
    # ------------------------------------------------------------------

    @api.model
    def _visar_abrir(self, tareas):
        """Crea la guía para la primera tarea sin cita de `tareas` y la abre."""
        tareas = tareas.filtered(lambda t: t._visar_agendable())
        if not tareas:
            raise UserError(_("Este servicio ya tiene cita, o no es un servicio "
                              "de campo abierto."))
        tarea = tareas[:1]
        wizard = self.create({
            'task_id': tarea.id,
            'tareas_posibles_ids': [(6, 0, tareas.ids)],
        })
        wizard._visar_por_defecto()
        wizard._visar_buscar()
        return wizard._visar_accion()

    @api.depends('tareas_posibles_ids')
    def _compute_varias_tareas(self):
        for wizard in self:
            wizard.varias_tareas = len(wizard.tareas_posibles_ids) > 1

    def _visar_accion(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Agendar servicio"),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _visar_maestro(self):
        maestro = self.env['appointment.type'].sudo()._visar_get_master_appointment_type()
        if not maestro:
            raise UserError(_("No hay tipo de cita maestro configurado."))
        return maestro

    def _visar_por_defecto(self):
        """Zona por el CP del domicilio, duración la del bloque del web."""
        self.ensure_one()
        domicilio = self.task_id._visar_agendar_domicilio()
        registro = self.env['visar.zone.cp'].sudo()._get_cp_record(
            domicilio.zip if domicilio else None)
        self.zona_id = registro.zone_id if registro else False
        self.duracion = self._visar_maestro().appointment_duration or 1.0

    @api.onchange('task_id')
    def _onchange_task_id(self):
        if self.task_id:
            self._visar_por_defecto()

    # ------------------------------------------------------------------
    # Buscar huecos
    # ------------------------------------------------------------------

    def action_buscar(self):
        self.ensure_one()
        self._visar_buscar()
        return self._visar_accion()

    def _visar_buscar(self):
        """Rellena `hueco_ids` con lo que el web ofrecería, más las ausencias."""
        self.ensure_one()
        self.hueco_ids.unlink()
        self.nota = False
        if not self.zona_id:
            self.nota = _("No se sabe la zona: el código postal del domicilio no "
                          "está en el catálogo. Elige la zona a mano.")
            return
        maestro = self._visar_maestro()
        AptType = self.env['appointment.type'].sudo()
        pool = maestro._visar_eligible_resources(self.zona_id)
        if self.recurso_id:
            pool = pool & self.recurso_id
        if not pool:
            self.nota = _("No hay técnicos dados de alta para esa zona.")
            return

        tz_name = maestro.appointment_tz or 'America/Monterrey'
        tz_info = pytz.timezone(tz_name)
        ahora = fields.Datetime.now()
        inicio_dia = tz_info.localize(
            fields.Datetime.to_datetime(self.desde)).astimezone(pytz.utc).replace(tzinfo=None)
        # El nativo empieza en `reference_date + min_schedule_hours` (24 h en
        # producción): sin restarlas, oficina no podría agendar para mañana, y el
        # mínimo de anticipación es una regla para el cliente, no para oficina.
        # Pero nunca antes de ahora: el pasado no tiene huecos.
        margen = relativedelta(hours=maestro.min_schedule_hours or 0)
        referencia = max(inicio_dia, ahora) - margen
        meses = maestro.with_context(tz=tz_name)._get_appointment_slots(
            tz_name, filter_resources=pool, asked_capacity=1,
            reference_date=referencia)

        destino = AptType._visar_travel_partner_coords(
            self.task_id._visar_agendar_domicilio())
        meses = AptType._visar_filter_slots_travel(
            maestro, meses, tz_name, destino, require='any')

        hasta = self.desde + relativedelta(days=DIAS_A_MOSTRAR)
        duracion = self.duracion or maestro.appointment_duration or 1.0
        Resource = self.env['appointment.resource'].sudo()
        filas = []
        for mes in meses or []:
            for semana in mes.get('weeks', []):
                for dia in semana:
                    if not isinstance(dia, dict):
                        continue
                    tier = dia.get('visar_travel_tier')
                    for hueco in dia.get('slots') or []:
                        crudo = hueco.get('datetime')
                        if not crudo:
                            continue
                        local = fields.Datetime.from_string(crudo)
                        if not (self.desde <= local.date() < hasta):
                            continue
                        inicio = tz_info.localize(local).astimezone(
                            pytz.utc).replace(tzinfo=None)
                        if inicio < ahora:
                            continue
                        fin = inicio + relativedelta(hours=duracion)
                        for recurso in Resource.browse(
                                AptType._visar_slot_resource_ids(hueco)):
                            if recurso not in pool:
                                continue
                            if self._visar_ausente(recurso, inicio, fin):
                                continue
                            # El hueco del nativo mide el bloque del web; un
                            # servicio más largo tiene que caber entero.
                            if (duracion > (maestro.appointment_duration or 1.0)
                                    and not AptType._visar_resource_free_at(
                                        maestro, recurso, inicio, fin, 1)):
                                continue
                            filas.append({
                                'wizard_id': self.id,
                                'inicio': inicio,
                                'fin': fin,
                                'recurso_id': recurso.id,
                                'ruta': ('cerca' if tier == TIER_CON_TRABAJO
                                         else 'libre' if tier else False),
                            })
        self.env['visar.agendar.servicio.hueco'].create(filas)
        if not filas:
            self.nota = _("No hay horarios libres en los %(dias)s días desde esa "
                          "fecha. Prueba más adelante u otro técnico.",
                          dias=DIAS_A_MOSTRAR)
        elif not destino:
            self.nota = _("El domicilio no tiene ubicación ni CP con centroide: no "
                          "se pudo revisar el traslado, solo la disponibilidad.")

    def _visar_ausente(self, recurso, inicio, fin):
        """¿El EMPLEADO del técnico tiene una ausencia en ese tramo?

        El nativo ya respeta el horario y los descansos del recurso de agenda; lo
        que no ve es el tiempo libre puesto en el empleado, porque el recurso de
        agenda y el del empleado son `resource.resource` distintos.
        """
        empleado = recurso.visar_employee_id
        if not empleado or not empleado.resource_id:
            return False
        return bool(self.env['resource.calendar.leaves'].sudo().search_count([
            ('resource_id', '=', empleado.resource_id.id),
            ('date_from', '<', fin),
            ('date_to', '>', inicio),
        ], limit=1))

    # ------------------------------------------------------------------
    # Elegir
    # ------------------------------------------------------------------

    def _visar_agendar(self, hueco):
        """Crea la cita callada en ese hueco y la engancha a la tarea."""
        self.ensure_one()
        tarea = self.task_id
        if not tarea._visar_agendable():
            raise UserError(_("Este servicio ya tiene cita. Recarga la página."))
        maestro = self._visar_maestro()
        AptType = self.env['appointment.type'].sudo()
        recurso = hueco.recurso_id
        # Se revalida: la lista se calculó hace un rato y el web no aparta mientras
        # el cliente paga, así que entre tanto pudo venderse.
        if (not AptType._visar_resource_free_at(maestro, recurso, hueco.inicio,
                                                hueco.fin, 1)
                or self._visar_ausente(recurso, hueco.inicio, hueco.fin)):
            # Sin refrescar la lista aquí: el error deshace la transacción.
            raise UserError(_("Ese horario se acaba de ocupar. Pulsa «Buscar "
                              "horarios» y elige otro."))

        orden = tarea.sale_order_id.sudo()
        domicilio = tarea._visar_agendar_domicilio()
        duracion = (hueco.fin - hueco.inicio).total_seconds() / 3600.0
        vals = {
            'name': tarea.name or _("Servicio"),
            'start': hueco.inicio,
            'stop': hueco.fin,
            'allday': False,
            'duration': duracion,
            'appointment_type_id': maestro.id,
            'appointment_status': 'booked',
            'booking_line_ids': [(0, 0, v) for v in maestro._visar_booking_line_values(
                recurso, hueco.inicio, hueco.fin, asked_capacity=1)],
            'user_id': orden.user_id.id or self.env.user.id,
            # Vacía y explícita: omitirla engancha los recordatorios del tipo
            # (ver la misma línea en `_visar_preagenda_crear`).
            'alarm_ids': [(5, 0, 0)],
            'visar_zone_id': self.zona_id.id,
        }
        if domicilio:
            # Las coordenadas de la parada para el motor de rutas.
            vals['partner_ids'] = [(6, 0, domicilio.ids)]
        cita = self.env['calendar.event'].sudo().with_context(**CTX_SILENCIO).create(vals)
        tarea.with_context(visar_sin_aviso_ruta=True).sudo().write(
            {'visar_visit_event_id': cita.id})
        # Fecha, fin y técnico salen de la cita, como en una reserva del web. Sin
        # `visar_sin_aviso_ruta`: si la ruta quedó apretada, el aviso de siempre lo
        # dice, y si la tarea traía uno viejo de una fecha escrita a mano, se borra.
        cita.with_context(**CTX_SILENCIO)._visar_sync_fsm_tasks()

        tarea._visar_preagenda_cerrar_avisos()

        cuando = format_datetime(self.env, cita.start, tz=maestro.appointment_tz,
                                 dt_format="EEEE d 'de' MMMM, HH:mm", lang_code='es_MX')
        tarea.message_post(body=Markup("<p>%s</p>") % _(
            "Agendado desde la guía de horarios: %(cuando)s con %(tecnico)s. "
            "No se le avisó al cliente.",
            cuando=cuando, tecnico=recurso.name))
        return {'type': 'ir.actions.act_window_close'}


class VisarAgendarServicioHueco(models.TransientModel):
    _name = 'visar.agendar.servicio.hueco'
    _description = "Horario libre de la guía de agenda"
    _order = 'inicio, id'

    wizard_id = fields.Many2one(
        'visar.agendar.servicio', required=True, ondelete='cascade')
    inicio = fields.Datetime(string="Inicio", required=True)
    fin = fields.Datetime(string="Fin", required=True)
    recurso_id = fields.Many2one('appointment.resource', string="Técnico", required=True)
    ruta = fields.Selection(
        [('cerca', "Cerca de su ruta del día"), ('libre', "Día sin servicios")],
        string="Ruta",
        help="«Cerca de su ruta» rellena un día que ya tiene servicios en la zona; "
             "es lo que el web ofrece primero.")

    def action_elegir(self):
        self.ensure_one()
        return self.wizard_id._visar_agendar(self)


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_agendable = fields.Boolean(compute='_compute_visar_agendable')

    def _visar_agendable(self):
        """¿Le cabe la guía? Servicio de campo, abierto y sin cita propia."""
        self.ensure_one()
        return bool(self.project_id.is_fsm
                    and self.state not in CLOSED_STATES
                    and not self._visar_calendar_event())

    def _compute_visar_agendable(self):
        for task in self:
            task.visar_agendable = task._visar_agendable()

    def _visar_agendar_domicilio(self):
        """Dónde es el servicio: la dirección de servicio de la orden, la de
        entrega, o el contacto de la tarea."""
        self.ensure_one()
        orden = self.sale_order_id.sudo()
        return (orden.visar_service_partner_id
                or (orden.partner_shipping_id if orden else False)
                or self.partner_id)

    def action_visar_agendar(self):
        self.ensure_one()
        return self.env['visar.agendar.servicio']._visar_abrir(self)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    visar_agendables_count = fields.Integer(compute='_compute_visar_agendables_count')

    def _visar_tareas_agendables(self):
        self.ensure_one()
        if self.state != 'sale' or self.plan_id:
            # Las visitas de una póliza las pre-agenda el cron, y una póliza vieja
            # tiene decenas de visitas pasadas sin cita: el botón en la orden solo
            # confundiría. Cada visita sigue teniendo el suyo en la tarea.
            return self.env['project.task']
        return self.tasks_ids.filtered(lambda t: t._visar_agendable())

    def _compute_visar_agendables_count(self):
        for order in self:
            order.visar_agendables_count = len(order._visar_tareas_agendables())

    def action_visar_agendar(self):
        self.ensure_one()
        return self.env['visar.agendar.servicio']._visar_abrir(
            self._visar_tareas_agendables())
