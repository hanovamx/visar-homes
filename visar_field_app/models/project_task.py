# -*- coding: utf-8 -*-
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)

# Estados de tarea "cerrados": sus clientes no se geolocalizan.
_CLOSED_STATES = ('1_done', '1_canceled')


class ProjectTask(models.Model):
    _inherit = 'project.task'

    # Nota: `visar_technician_ids` (técnicos asignados) ahora vive en visar_fsm,
    # porque la asignación es responsabilidad de FSM y la usa el Gantt de técnicos.
    # Esta app solo lo consume (lista de servicios del técnico).

    # --- Atribución del cierre en campo ---
    # La captura (worksheet, firma) usa los modelos/campos NATIVOS para que los
    # reportes nativos funcionen. Aquí solo guardamos QUIÉN cerró (empleado), que
    # el flujo nativo no registra y Visar necesita para comisiones de upsell.
    visar_field_closed_by_id = fields.Many2one(
        'hr.employee', string="Cerrado por (técnico)", readonly=True,
        help="Técnico que cerró el servicio desde la app de campo. "
             "Base para comisiones de upsell y auditoría.")
    visar_field_closed_at = fields.Datetime(
        string="Cerrado en campo", readonly=True)

    # --- Flujo en sitio (Req 2) ---
    # El ESTADO visible usa las etapas nativas de FSM (`stage_id`), no un campo
    # propio. Aquí solo se sellan los momentos de las sub-fases que NO tienen etapa
    # nativa (llegada, espera del cliente, inicio del servicio) para calcular
    # tiempos y disparar el cronómetro de espera.
    visar_arrived_at = fields.Datetime(
        string="Llegada del técnico", readonly=True,
        help="Momento en que el técnico pulsó 'Confirmar llegada' en la app.")
    visar_waiting_start = fields.Datetime(
        string="Inicio de espera al cliente", readonly=True,
        help="Momento en que el técnico pulsó 'Esperar al cliente'. Dispara la "
             "cuenta regresiva en la app.")
    visar_waiting_minutes = fields.Integer(
        string="Minutos de espera al cliente", readonly=True,
        help="Duración (min) que el técnico eligió para la cuenta regresiva de "
             "espera. 0 = usar el valor por defecto (parámetro visar_field.waiting_minutes).")
    visar_service_start = fields.Datetime(
        string="Inicio del servicio", readonly=True,
        help="Momento en que el técnico pulsó 'Comenzar servicio'. Al cerrar se "
             "registra el tiempo trabajado como parte de horas (timesheet).")
    visar_client_wait_minutes = fields.Float(
        string="Espera al cliente (min)", readonly=True,
        help="Minutos que transcurrieron desde 'Esperar al cliente' hasta "
             "'Comenzar servicio' (cuánto se esperó a que el cliente abriera). "
             "0 si el técnico no inició el temporizador de espera.")
    visar_reschedule_requested_by_id = fields.Many2one(
        'hr.employee', string="Reagenda solicitada por", readonly=True,
        help="Técnico que marcó 'Cliente no llegó' (solicitud de reagenda).")
    visar_reschedule_requested_at = fields.Datetime(
        string="Reagenda solicitada en", readonly=True)

    # ==================================================================
    # Flujo en sitio: etapas nativas + timesheet + reagenda (Req 2)
    # ==================================================================
    def _visar_fsm_stage(self, n):
        """Etapa nativa de Field Service por su xmlid estable (portable, sin ids
        cableados). n ∈ {0..4}: 0 Programado, 1 En camino, 2 En ejecución,
        3 Completado, 4 Incidencia—Reprogramar."""
        return self.env.ref(
            'industry_fsm.planning_project_stage_%s' % n, raise_if_not_found=False)

    def _visar_set_stage(self, n):
        """Mueve la tarea a la etapa nativa n (si existe)."""
        self.ensure_one()
        stage = self._visar_fsm_stage(n)
        if stage:
            self.stage_id = stage.id

    def write(self, vals):
        """Al cambiar de etapa (desde la app O desde el backend "Servicio externo"),
        reconcilia los sellos de sub-fase para que la app muestre los botones que
        corresponden a la etapa. Sin esto, mover la etapa a mano en Odoo dejaba
        sellos obsoletos (p. ej. `visar_service_start`) que "ganaban" y congelaban
        la app en una fase vieja (timer/¡Tiempo!/reagenda fantasma)."""
        changed = self.browse()
        if 'stage_id' in vals:
            changed = self.filtered(lambda t: t.stage_id.id != vals['stage_id'])
        res = super().write(vals)
        for task in changed:
            task._visar_reconcile_flow_markers()
        return res

    def _visar_reconcile_flow_markers(self):
        """Limpia los sellos de sub-fase cuando la etapa deja de ser "de servicio".

        Desde que 'Confirmar llegada' salta directo a **En ejecución**, todas las
        sub-fases (llegada → espera → servicio) viven en esa etapa (y se conservan
        en Completado como historial). Si gestión mueve la etapa a Programado / En
        camino / Incidencia, los sellos se limpian para que la app muestre la fase
        correcta (sin timer/¡Tiempo!/reagenda fantasma). El `write` resultante no
        toca `stage_id`, así que no reentra en el override."""
        self.ensure_one()
        s2 = self._visar_fsm_stage(2)  # En ejecución
        s3 = self._visar_fsm_stage(3)  # Completado
        stage = self.stage_id
        in_service = (s2 and stage == s2) or (s3 and stage == s3)
        if not in_service:
            self.write({
                'visar_arrived_at': False,
                'visar_waiting_start': False,
                'visar_waiting_minutes': 0,
                'visar_service_start': False,
                'visar_client_wait_minutes': 0.0,
            })

    def _visar_write_service_timesheet(self, employee):
        """Registra el tiempo trabajado como línea de timesheet NATIVA (oculta al
        técnico), atribuida a su empleado. Reutiliza `account.analytic.line` (lo
        mismo que produce el cronómetro nativo) sin usar el widget ligado a usuario.

        No hace nada si no hubo 'Comenzar servicio' o el proyecto no lleva horas.
        """
        self.ensure_one()
        if not self.visar_service_start or not self.project_id.allow_timesheets:
            return
        delta = fields.Datetime.now() - self.visar_service_start
        hours = max(delta.total_seconds() / 3600.0, 0.0)
        if not hours:
            return
        self.env['account.analytic.line'].sudo().create({
            'task_id': self.id,
            'project_id': self.project_id.id,
            'date': fields.Date.context_today(self),
            'name': "Servicio en campo (app técnicos)",
            'unit_amount': hours,
            'employee_id': employee.id,
        })

    def _visar_reschedule_assignee(self):
        """Usuario al que se asigna la actividad de reagenda. Los técnicos no tienen
        usuario, así que `user_ids` suele estar vacío: se cae al vendedor de la orden
        y luego al responsable del proyecto."""
        self.ensure_one()
        return (self.user_ids[:1]
                or self.visar_sale_order_id.user_id
                or self.project_id.user_id)

    def _visar_flag_reschedule(self, employee):
        """Marca 'Cliente no llegó': etapa Incidencia—Reprogramar + cancelación,
        actividad para gestión (si hay a quién) y SIEMPRE una nota en el chatter.
        No reagenda el calendario (eso lo hace gestión en el backend)."""
        self.ensure_one()
        self.visar_reschedule_requested_by_id = employee.id
        self.visar_reschedule_requested_at = fields.Datetime.now()
        self._visar_set_stage(4)
        self.state = '1_canceled'
        body = ("Reagenda solicitada desde la app de campo por <b>%s</b>: el cliente "
                "no atendió tras la espera." % (employee.name or ''))
        assignee = self._visar_reschedule_assignee()
        if assignee:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=assignee.id,
                summary="Reagendar servicio — cliente no llegó",
                note=body)
        self.message_post(body=body)

    def _visar_geolocalize_service_partners(self, force=False):
        """Geolocaliza (lat/long) la **dirección de servicio** de los servicios
        abiertos, para que aparezcan en el mapa de la app de campo.

        La dirección de servicio es `task.partner_id` (contacto de entrega/obra,
        distinto del cliente de facturación). Los técnicos no pueden geolocalizar
        (no tienen usuario); esto lo dispara gestión desde el backend. Usa
        `res.partner._visar_geo_localize()` (consulta enriquecida con colonia +
        estado, con fallback al centroide de CP). Proveedor por defecto:
        OpenStreetMap, sin API key.

        Con `force=False` solo procesa los que no tienen coordenadas; con
        `force=True` re-geolocaliza todos (útil tras mejorar la consulta).
        Devuelve una notificación con cuántas direcciones resolvieron a nivel
        calle vs. solo al centroide.
        """
        tasks = self.search([('state', 'not in', list(_CLOSED_STATES))])
        partners = tasks.partner_id
        if not force:
            partners = partners.filtered(
                lambda p: not (p.partner_latitude and p.partner_longitude))
        exact = approx = failed = 0
        for partner in partners:
            try:
                kind = partner.with_context(force_geo_localize=True)._visar_geo_localize()
            except Exception as err:  # noqa: BLE001 - red/proveedor: no abortar el lote
                _logger.warning(
                    "Geolocalización fallida para el contacto %s: %s", partner.id, err)
                kind = False
            if kind == 'exact':
                exact += 1
            elif kind == 'approx':
                approx += 1
            else:
                failed += 1
        message = (
            "%d dirección(es) a nivel calle, %d solo aproximada(s) (centroide), "
            "%d sin resolver. Total: %d." % (exact, approx, failed, len(partners)))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Geolocalización de direcciones de servicio",
                'message': message,
                'type': 'success' if (exact or approx) else 'warning',
                'sticky': False,
            },
        }
