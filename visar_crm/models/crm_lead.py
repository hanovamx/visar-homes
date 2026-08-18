# -*- coding: utf-8 -*-
from odoo import api, fields, models

# XMLID del equipo/pipeline WhatsApp.
WA_TEAM_XMLID = 'visar_crm.crm_team_whatsapp'

# Etapas del pipeline WhatsApp EN ORDEN (por xmlid). El avance forward-only se
# rankea por POSICION en esta lista, NO por crm.stage.sequence: asi es inmune a
# las etapas stock de Odoo (globales, team_ids vacio) que se muestran en todos los
# pipelines y comparten sequence con las nuestras. Ver 32-...-implementation.md.
WA_PIPELINE_STAGE_XMLIDS = (
    'visar_crm.crm_stage_wa_nuevo',
    'visar_crm.crm_stage_wa_valoracion',
    'visar_crm.crm_stage_wa_cotizacion',
    'visar_crm.crm_stage_wa_programado',
    'visar_crm.crm_stage_wa_cerrado',
)

# Etapa -> parametro del sistema con la ventana de caducidad en dias (cron).
# Ausente o <= 0 = esa etapa NO caduca. Editables sin deploy.
WA_LOST_DAYS_PARAMS = {
    'visar_crm.crm_stage_wa_nuevo': 'visar.crm.lost_days_nuevo',
    'visar_crm.crm_stage_wa_valoracion': 'visar.crm.lost_days_valoracion',
    'visar_crm.crm_stage_wa_cotizacion': 'visar.crm.lost_days_cotizacion',
    'visar_crm.crm_stage_wa_programado': 'visar.crm.lost_days_programado',
}
WA_LOST_REASON_XMLID = 'visar_crm.crm_lost_reason_wa_inactivo'


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    # Grupo de servicio que acota el lead (Fumigacion vs Areas Verdes). Es, junto
    # con el telefono, la clave de deduplicacion: un cliente de fumigacion que
    # pregunta por jardineria abre un lead NUEVO en Areas Verdes. Ver
    # .context/31-whatsapp-crm-lead-mapping.md seccion 4.
    visar_service_group_id = fields.Many2one(
        'visar.service.group',
        string="Grupo de servicio (Visar)",
        index=True,
        help="Grupo de servicio que acota este lead. Clave de dedupe junto con "
             "el telefono normalizado.",
    )

    # Telefono normalizado a los ultimos 10 digitos (numero nacional MX). Misma
    # normalizacion que el resto del agente (_agent_normalize_phone ->
    # res.partner._visar_phone_nat10_value), asi "mismo numero" significa lo mismo
    # para el lead, el partner y el dedupe de reservas. Indexado: agent_track_lead
    # busca por igualdad en cada cotizacion.
    visar_wa_phone_norm = fields.Char(
        string="Telefono WhatsApp (nat. 10)",
        index=True,
        copy=False,
        help="Ultimos 10 digitos del telefono; clave de dedupe del pipeline WhatsApp.",
    )

    # Origen del lead. Selection para poder crecer sin migrar.
    # 'whatsapp_handoff' = el agente escalo la conversacion a un humano
    # (agent_request_handoff). Se distingue de 'whatsapp' a proposito: un lead que
    # nace de un escalamiento necesita atencion, uno que nace de una cotizacion no
    # necesariamente.
    visar_source = fields.Selection(
        selection=[
            ('whatsapp', "WhatsApp"),
            ('whatsapp_handoff', "WhatsApp (escalado a asesor)"),
        ],
        string="Origen (Visar)",
        copy=False,
    )

    # True si el lead esta en el pipeline WhatsApp. Gobierna la visibilidad de los
    # botones manuales (valoracion / cotizacion enviada) en el formulario.
    visar_is_wa_pipeline = fields.Boolean(
        string="En pipeline WhatsApp",
        compute='_compute_visar_is_wa_pipeline',
    )

    @api.depends('team_id')
    def _compute_visar_is_wa_pipeline(self):
        team = self.env.ref(WA_TEAM_XMLID, raise_if_not_found=False)
        team_id = team.id if team else False
        for lead in self:
            lead.visar_is_wa_pipeline = bool(team_id) and lead.team_id.id == team_id

    # ------------------------------------------------------------------
    # Avance de etapa forward-only (por posicion en el pipeline, no sequence)
    # ------------------------------------------------------------------

    @api.model
    def _visar_wa_stage_ids(self):
        """Ids de las etapas del pipeline WhatsApp, EN ORDEN (omite las que no
        resuelvan por xmlid)."""
        ids = []
        for xmlid in WA_PIPELINE_STAGE_XMLIDS:
            rec = self.env.ref(xmlid, raise_if_not_found=False)
            if rec:
                ids.append(rec.id)
        return ids

    def _visar_advance_stage(self, target_stage):
        """Mueve el lead a `target_stage` solo si es un AVANCE (forward-only).

        Rankea por POSICION en el pipeline WhatsApp (no por crm.stage.sequence),
        asi que nunca regresa de etapa y es inmune a las etapas stock globales.
        Si el lead esta hoy en una etapa que NO es del pipeline (rank -1, p. ej.
        una etapa stock), cualquiera de las nuestras cuenta como avance -> lo
        "rescata" al pipeline. Devuelve True si hubo cambio.
        """
        self.ensure_one()
        order = self._visar_wa_stage_ids()
        if not target_stage or target_stage.id not in order:
            return False
        target_rank = order.index(target_stage.id)
        current_rank = order.index(self.stage_id.id) if self.stage_id.id in order else -1
        if target_rank <= current_rank:
            return False
        self.stage_id = target_stage.id
        return True

    # ------------------------------------------------------------------
    # Botones manuales (staff): valoracion agendada / cotizacion enviada
    # ------------------------------------------------------------------
    #
    # Ambas ramas viven en la rama "manual/valoracion" (diseno 31 seccion 5):
    # - 'Cotizacion enviada' es la cotizacion FORMAL que arma finanzas tras la
    #   visita (siempre manual por diseno).
    # - 'Valoracion agendada' se deja manual porque la orden de una valoracion
    #   trae el PRODUCTO de valoracion (sin grupo de servicio), asi que no se
    #   puede atribuir por (telefono, grupo) de forma fiable. Automatizable luego
    #   via calendar.event.visar_booking_items tras verificar en visar-db.

    def action_visar_mark_valoracion(self):
        stage = self.env.ref('visar_crm.crm_stage_wa_valoracion', raise_if_not_found=False)
        if stage:
            for lead in self:
                lead._visar_advance_stage(stage)

    def action_visar_mark_cotizacion(self):
        stage = self.env.ref('visar_crm.crm_stage_wa_cotizacion', raise_if_not_found=False)
        if stage:
            for lead in self:
                lead._visar_advance_stage(stage)

    # ------------------------------------------------------------------
    # Avance automatico desde una orden (lo llaman los hooks de sale.order /
    # project.task). El grupo se deriva de las lineas; combo -> fan-out.
    # ------------------------------------------------------------------

    @api.model
    def _visar_order_service_groups(self, order):
        """Grupos de servicio DISTINTOS de una orden: linea -> producto -> grupo.

        Filtra a servicios Visar y delega la resolucion del grupo en
        `product.template._visar_service_groups()`, que usa el enlace autoritativo
        dimension -> producto (varias dimensiones pueden compartir un producto) y
        cae al puntero inverso `visar_dimension_id` solo como respaldo. Combo ->
        varios grupos.
        """
        templates = order.order_line.filtered(
            lambda l: l.product_id.visar_is_service
        ).mapped('product_id.product_tmpl_id')
        return templates._visar_service_groups()

    @api.model
    def _visar_open_lead(self, nat, group, team, cerrado):
        """Lead ABIERTO (aun no Cerrado) de (telefono, grupo) en el pipeline."""
        domain = [
            ('visar_wa_phone_norm', '=', nat),
            ('visar_service_group_id', '=', group.id),
            ('team_id', '=', team.id),
        ]
        if cerrado:
            domain.append(('stage_id', '!=', cerrado.id))
        return self.sudo().search(domain, order='id desc', limit=1)

    @api.model
    def _visar_crm_advance_order_leads(self, order, target_xmlid):
        """Avanza a `target_xmlid` los leads abiertos (telefono+grupo) de la orden.

        Fan-out por grupo (una orden combo mueve el lead de CADA grupo). Solo
        mueve leads que ya existen (el lead nace en la cotizacion del agente); una
        reserva web sin chat previo no crea lead. Forward-only e idempotente.
        """
        team = self.env.ref(WA_TEAM_XMLID, raise_if_not_found=False)
        target = self.env.ref(target_xmlid, raise_if_not_found=False)
        cerrado = self.env.ref('visar_crm.crm_stage_wa_cerrado', raise_if_not_found=False)
        if not team or not target or not order.partner_id:
            return
        nat = self.env['res.partner']._visar_phone_nat10_value(order.partner_id.phone) or ''
        if len(nat) != 10:
            return
        for group in self._visar_order_service_groups(order):
            lead = self._visar_open_lead(nat, group, team, cerrado)
            if lead:
                lead._visar_advance_stage(target)

    @api.model
    def _visar_crm_win_order_leads(self, order):
        """Marca won (avanza a Cerrado) los leads abiertos de la orden.

        Idempotente: un lead ya en Cerrado se excluye del search, asi que reabrir
        y re-cerrar la tarea FSM no lo re-procesa.
        """
        self._visar_crm_advance_order_leads(order, 'visar_crm.crm_stage_wa_cerrado')

    # ------------------------------------------------------------------
    # Cron de caducidad (lost) — ventanas por etapa configurables
    # ------------------------------------------------------------------

    @api.model
    def _visar_crm_expire_stale_leads(self):
        """Marca lost los leads abiertos inactivos, por etapa, segun ventanas en
        ir.config_parameter (0/ausente = esa etapa no caduca). Inactividad =
        write_date. Lo llama el ir.cron diario. Ver diseno 31 seccion 10.
        """
        Param = self.env['ir.config_parameter'].sudo()
        reason = self.env.ref(WA_LOST_REASON_XMLID, raise_if_not_found=False)
        now = fields.Datetime.now()
        for stage_xmlid, param in WA_LOST_DAYS_PARAMS.items():
            try:
                days = int(Param.get_param(param, 0) or 0)
            except (TypeError, ValueError):
                days = 0
            if days <= 0:
                continue
            stage = self.env.ref(stage_xmlid, raise_if_not_found=False)
            if not stage:
                continue
            cutoff = fields.Datetime.subtract(now, days=days)
            stale = self.sudo().search([
                ('stage_id', '=', stage.id),
                ('write_date', '<', cutoff),
            ])
            if stale:
                stale.action_set_lost(
                    **({'lost_reason_id': reason.id} if reason else {}))
