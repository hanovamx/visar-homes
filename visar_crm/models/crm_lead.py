# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

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

# Canal -> equipo donde vive su ficha. **Dos fichas por cliente, una por canal**
# (decision de Visar, 2-oct-2026): quien chatea y luego compra en la web deja
# rastro en los dos tableros. El coste aceptado es que la suma de "Servicio
# programado" de los dos cuenta esa venta dos veces; se tomo sabiendolo.
#
# El equipo del canal web es el NATIVO de Odoo (`salesteam_website_sales`), no uno
# propio: es donde oficina ya habia puesto a mano las fichas de reservas web.
WA_TEAM_XMLID_WEB = 'sales_team.salesteam_website_sales'
# Prefijo del nombre de la ficha, para que en el tablero se vea de donde salio.
CANAL_NOMBRES = (('whatsapp', "WhatsApp"), ('web', "Web"))
CANAL_TEAM_XMLIDS = {
    'whatsapp': WA_TEAM_XMLID,
    'web': WA_TEAM_XMLID_WEB,
}


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
            # 2-oct-2026: antes una reserva web dejaba el lead POBRE que crea
            # `appointment_crm` (sin telefono normalizado ni grupo), invisible
            # para los automatismos de avance. Ahora nace aqui, con identidad.
            ('web', "Sitio web"),
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
    def _visar_crm_order_nat(self, order):
        """Telefono nat10 del cliente de la orden, o '' si no sirve para emparejar."""
        if not order.partner_id:
            return ''
        nat = self.env['res.partner']._visar_phone_nat10_value(
            order.partner_id.phone) or ''
        return nat if len(nat) == 10 else ''

    @api.model
    def _visar_crm_team(self, canal):
        """Equipo donde vive la ficha de `canal` ('whatsapp' | 'web')."""
        xmlid = CANAL_TEAM_XMLIDS.get(canal)
        if not xmlid:
            return self.env['crm.team'].browse()
        return self.env.ref(xmlid, raise_if_not_found=False) \
            or self.env['crm.team'].browse()

    @api.model
    def _visar_order_group_amount(self, order, group):
        """Importe de la orden que corresponde a `group`, CON impuestos.

        `price_total` y no `price_subtotal`: los precios de Visar llevan IVA
        incluido, y es lo que el agente ya guarda en `expected_revenue` (lo dice
        su propio codigo: *"amount_TOTAL: los precios de Visar llevan IVA
        incluido, asi que el subtotal NO es lo que el cliente ve"*). Mezclar las
        dos bases fiscales en el mismo campo haria que el valor del embudo
        dependiera de quien escribio ultimo.

        Es "el trozo" del reparto por grupo: un combo reparte su importe entre
        sus fichas en vez de copiar el total en todas. Lo contrario es lo que
        hace hoy el agente (`app/agent.py`, manda `quote["total"]` de la canasta
        entera en cada llamada), y por eso un combo de 1,800 enseña 1,800 en las
        dos fichas y el embudo cuenta 3,600.
        """
        total = 0.0
        for line in order.order_line:
            if line.display_type or not line.product_id:
                continue
            if group in line.product_id.product_tmpl_id._visar_service_groups():
                total += line.price_total
        return total

    @api.model
    def _visar_order_is_valuation(self, order):
        """¿La orden vende una Valoracion Tecnica?

        Hace falta como caso aparte porque el producto de valoracion **no lleva
        grupo de servicio, y es por diseno**: `_visar_wizard_valuation_items`
        devuelve `dimension_id: False` a proposito — *"el corte por calificacion
        —termitas, chinches, 'no se que es'— nunca elige tramo. El corte existe
        justamente para no medir"*. Sin este caso, una cotizacion de valoracion
        se quedaria sin ficha: `_visar_order_service_groups` no devuelve nada.
        """
        return bool(order.order_line.mapped('product_id.product_tmpl_id')
                    .filtered('visar_is_valuation'))

    @api.model
    def _visar_crm_match_order_leads(self, order):
        """TODOS los leads abiertos que casan con la orden, en CUALQUIER canal.

        Dos fichas por cliente (una por canal) significa que una venta puede
        tocar dos, y las dos tienen que avanzar: si no, el tablero de WhatsApp se
        queda con una ficha muerta en 'Nuevo' de alguien que SI compro, y el cron
        de caducidad acabaria marcandola "sin respuesta".

        Fan-out por grupo (una orden combo casa el lead de CADA grupo).
        **Nunca resucita archivados:** `search` inyecta `active = True`, asi que
        un lead perdido no casa. Es deliberado — revivir en silencio un lead que
        alguien dio por perdido es peor que no enlazarlo (2-oct-2026).
        """
        leads = self.browse()
        nat = self._visar_crm_order_nat(order)
        if not nat:
            return leads
        cerrado = self.env.ref('visar_crm.crm_stage_wa_cerrado',
                               raise_if_not_found=False)
        grupos = self._visar_order_service_groups(order)
        if not grupos:
            return leads
        for canal in CANAL_TEAM_XMLIDS:
            team = self._visar_crm_team(canal)
            if not team:
                continue
            for group in grupos:
                leads |= self._visar_open_lead(nat, group, team, cerrado)
        return leads

    @api.model
    def _visar_crm_ensure_order_leads(self, order, canal):
        """Fichas de `canal` para esta orden, ABRIENDO las que falten.

        Es lo que hace que una reserva web deje ficha con identidad —telefono
        normalizado y grupo— en vez del lead pobre que creaba `appointment_crm`.
        Un lead por grupo: el combo abre dos, cada uno con su trozo del importe.

        Idempotente: si la ficha de (telefono, grupo, canal) ya existe se
        reutiliza. **No** mira las de otros canales a proposito (decision "dos
        fichas, una por canal").
        """
        abiertas = self.browse()
        nat = self._visar_crm_order_nat(order)
        team = self._visar_crm_team(canal)
        nuevo_stage = self.env.ref('visar_crm.crm_stage_wa_nuevo',
                                   raise_if_not_found=False)
        if not nat or not team or not nuevo_stage:
            if not team or not nuevo_stage:
                _logger.warning(
                    "visar_crm: falta el equipo o la etapa 'Nuevo' del canal %r; "
                    "no se abren fichas. Actualizar el modulo visar_crm.", canal)
            return abiertas
        cerrado = self.env.ref('visar_crm.crm_stage_wa_cerrado',
                               raise_if_not_found=False)
        grupos = self._visar_order_service_groups(order)
        if not grupos and self._visar_order_is_valuation(order):
            # Ficha SIN grupo, igual que `agent_track_interest` cuando todavia no
            # se sabe que quiere el cliente: una valoracion es exactamente eso,
            # alguien que no sabe que plaga tiene. El grupo llegara con el
            # servicio que se le venda despues de la visita.
            grupos = [self.env['visar.service.group'].browse()]
        for group in grupos:
            lead = self._visar_open_lead(nat, group, team, cerrado)
            if not lead:
                lead = self.sudo().create({
                    'name': "%s %s" % (
                        dict(CANAL_NOMBRES).get(canal, canal),
                        order.partner_id.name or nat),
                    'type': 'opportunity',
                    'team_id': team.id,
                    'stage_id': nuevo_stage.id,
                    'visar_service_group_id': group.id,
                    'visar_wa_phone_norm': nat,
                    'visar_source': canal if canal == 'web' else 'whatsapp',
                    'phone': order.partner_id.phone or nat,
                    'partner_id': order.partner_id.id,
                })
            abiertas |= lead
        return abiertas

    @api.model
    def _visar_crm_advance_order_leads(self, order, target_xmlid):
        """Avanza a `target_xmlid` los leads abiertos que casan con la orden.

        Fan-out por grupo Y por canal (ver `_visar_crm_match_order_leads`). Solo
        mueve leads que ya existen; abrirlos es trabajo de
        `_visar_crm_ensure_order_leads`, que corre antes, en borrador.
        Forward-only e idempotente.
        """
        target = self.env.ref(target_xmlid, raise_if_not_found=False)
        if not target:
            return
        for lead in self._visar_crm_match_order_leads(order):
            lead._visar_advance_stage(target)

    def _update_revenues_from_so(self, order):
        """El importe de un lead con grupo lo manda el reparto, no el core.

        El nativo (`sale_crm/models/crm_lead.py`) sube `expected_revenue` a
        `order.amount_untaxed` al confirmar, y eso rompe dos cosas a la vez:

        1. **Mezcla bases fiscales.** Los precios de Visar llevan IVA incluido y
           es lo que el agente guarda en este campo; `amount_untaxed` es el
           subtotal, que no es lo que el cliente ve. El valor del embudo
           acabaria dependiendo de quien escribio ultimo.
        2. **Deshace el reparto del combo.** Una orden de fumigacion + areas
           verdes escribiria su total ENTERO en la ficha principal, y entonces el
           embudo volveria a contar el combo dos veces (su total en una ficha mas
           su trozo en la otra).

        Los leads SIN grupo no tienen trozo que calcular, asi que para esos se
        deja al core hacer lo suyo. (2-oct-2026.)
        """
        delegados = self.filtered(lambda lead: not lead.visar_service_group_id)
        if delegados:
            return super(CrmLead, delegados)._update_revenues_from_so(order)
        return

    @api.model
    def _visar_crm_win_order_leads(self, order):
        """Marca won (avanza a Cerrado) los leads abiertos de la orden.

        Idempotente: un lead ya en Cerrado se excluye del search, asi que reabrir
        y re-cerrar la tarea FSM no lo re-procesa.
        """
        self._visar_crm_advance_order_leads(order, 'visar_crm.crm_stage_wa_cerrado')

    # ------------------------------------------------------------------
    # Relleno del historico (lo dispara la migracion 19.0.1.4.0)
    # ------------------------------------------------------------------

    @api.model
    def _visar_crm_backfill_order_links(self, limit=None):
        """Enlaza ordenes antiguas con su ficha, SOLO donde se puede demostrar.

        Vive en el modelo y no en la migracion para que se pueda probar.

        Nada escribia `sale.order.opportunity_id` hasta el 2-oct-2026: **0 de 379
        ordenes**. Pero rellenar "a ver que casa" seria peor que no rellenar,
        porque la base trae mucho dato de prueba: `7774501440` lo comparten
        **siete** `res.partner` (uno de ellos Administrator, con 171 ordenes) y
        tiene fichas DUPLICADAS por grupo. Medido antes de escribir esto:
        **197 de 220 pares (orden, grupo) casan con mas de una ficha.**

        Dos puertas, y las dos tienen que abrirse:

        1. el telefono resuelve a **un solo** `res.partner`;
        2. cada grupo de la orden casa con **una sola** ficha abierta.

        Con las dos, la principal se elige igual que en el camino vivo: **la
        ficha mas antigua**. Lo que no pasa las puertas se deja intacto y se dice
        en el log con su motivo, que es la diferencia entre una migracion y una
        adivinanza.

        **No reparte `expected_revenue` del historico**: solo enlaza. Repartirlo
        pisaria cifras que alguien pudo ajustar a mano hace meses.

        Devuelve un dict de contadores (lo usa la prueba).
        """
        SO = self.env['sale.order'].sudo()
        Partner = self.env['res.partner'].sudo()
        cerrado = self.env.ref('visar_crm.crm_stage_wa_cerrado',
                               raise_if_not_found=False)
        equipos = [t.id for t in (self._visar_crm_team(c)
                                  for c in CANAL_TEAM_XMLIDS) if t]
        if not equipos:
            _logger.warning(
                "visar_crm: no hay equipos de canal; no se rellena nada.")
            return {}

        cuenta = {'enlazadas': 0, 'sin_telefono': 0, 'telefono_ambiguo': 0,
                  'ficha_ambigua': 0, 'sin_ficha': 0, 'total': 0}
        ordenes = SO.search([('opportunity_id', '=', False)],
                            order='id', limit=limit)
        cuenta['total'] = len(ordenes)

        for order in ordenes:
            nat = self._visar_crm_order_nat(order)
            if not nat:
                cuenta['sin_telefono'] += 1
                continue

            duenos = Partner.search([('visar_phone_nat10', '=', nat)])
            if len(duenos) != 1:
                cuenta['telefono_ambiguo'] += 1
                _logger.info(
                    "visar_crm: %s se queda sin enlazar — el telefono terminado "
                    "en %s es de %d contactos distintos, asi que no se puede "
                    "demostrar de quien es la orden.",
                    order.name or order.id, nat[-4:], len(duenos))
                continue

            grupos = self._visar_order_service_groups(order)
            if not grupos and self._visar_order_is_valuation(order):
                grupos = [self.env['visar.service.group'].browse()]
            if not grupos:
                cuenta['sin_ficha'] += 1
                continue

            candidatas = self.browse()
            ambigua = False
            for group in grupos:
                dominio = [('visar_wa_phone_norm', '=', nat),
                           ('visar_service_group_id', '=', group.id),
                           ('team_id', 'in', equipos)]
                if cerrado:
                    dominio.append(('stage_id', '!=', cerrado.id))
                encontradas = self.sudo().search(dominio)
                if len(encontradas) > 1:
                    ambigua = True
                    _logger.info(
                        "visar_crm: %s se queda sin enlazar — el grupo «%s» del "
                        "telefono terminado en %s casa con %d fichas abiertas "
                        "(%s) y senalar una seria adivinar.",
                        order.name or order.id,
                        group.display_name or "sin grupo", nat[-4:],
                        len(encontradas), encontradas.ids)
                    break
                candidatas |= encontradas
            if ambigua:
                cuenta['ficha_ambigua'] += 1
                continue
            if not candidatas:
                cuenta['sin_ficha'] += 1
                continue

            order.write({'opportunity_id': candidatas.sorted('id')[0].id})
            cuenta['enlazadas'] += 1

        if not cuenta['enlazadas']:
            _logger.info(
                "visar_crm: no habia ninguna orden que se pudiera enlazar sin "
                "adivinar (de %s revisadas).", cuenta['total'])
        else:
            _logger.info(
                "visar_crm: %(enlazadas)s ordenes enlazadas con su ficha. Se "
                "dejaron intactas: %(telefono_ambiguo)s por telefono de varios "
                "contactos, %(ficha_ambigua)s por fichas duplicadas, "
                "%(sin_ficha)s sin ficha que casara, %(sin_telefono)s sin "
                "telefono usable. Total revisadas: %(total)s.", cuenta)
        return cuenta

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
