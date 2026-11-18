# -*- coding: utf-8 -*-
"""Carril «Descartadas»: la oportunidad que no se concretó (19-nov-2026).

Pedido de Visar: «si la última conversación no se concreta en un plazo de 15
días, deberá pasar automáticamente a este carril. En caso de que el cliente
vuelva a enviar un mensaje, se deberá generar una nueva oportunidad».

```
WhatsApp   Nuevo / Contactado / Cotización enviada, sin mensaje del cliente en
           N días (15 por defecto, Ajustes → Visar)  → Descartadas
Sitio web  solo Nuevo (el carrito que no se pagó), sin movimiento en la ficha
           ni en su orden en N días                    → Descartadas
vuelve     el agente o la web ya no reusan la descartada: abren otra en Nuevo
           con una nota que apunta a la anterior
a mano     oficina la arrastra a otra etapa y vuelve a ser una ficha abierta
```

Las etapas con cita (valoración agendada, servicio programado) y la cotización
manual no se descartan: ahí ya hay algo real en marcha. «Cerrado» tampoco.

Reemplaza al cron de caducidad de la Fase D, que marcaba las fichas como
perdidas (archivadas) según `write_date` y estaba apagado (sin días puestos).
La fecha de la conversación la da el módulo del agente
(`_visar_descartar_ultima_actividad`); aquí solo hay el respaldo.
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

DESCARTADAS_XMLID = 'visar_crm.crm_stage_descartadas'
DESCARTAR_PARAM = 'visar.crm.descartar_dias'
DESCARTAR_DEFAULT = 15

# Etapas que se descartan, por canal. «Contactado» es la etapa nativa de Odoo
# (`crm.stage_lead2`) que Visar usa en el tablero.
DESCARTABLES_WA = ('visar_crm.crm_stage_wa_nuevo', 'crm.stage_lead2',
                   'visar_crm.crm_stage_wa_cotizacion')
DESCARTABLES_WEB = ('visar_crm.crm_stage_wa_nuevo',)

TEAM_WA = 'visar_crm.crm_team_whatsapp'
TEAM_WEB = 'sales_team.salesteam_website_sales'

LOTE = 200


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_crm_descartar_dias = fields.Integer(
        string="Días para descartar una oportunidad",
        config_parameter=DESCARTAR_PARAM, default=DESCARTAR_DEFAULT,
        help="Una oportunidad de WhatsApp en Nuevo, Contactado o Cotización "
             "enviada pasa a «Descartadas» si el cliente no escribe en estos "
             "días. Las del sitio web, solo si siguen en Nuevo (carrito sin "
             "pagar) y nadie las movió. 0 = no se descarta nada.")

    @api.constrains('visar_crm_descartar_dias')
    def _check_visar_crm_descartar_dias(self):
        for record in self:
            if record.visar_crm_descartar_dias < 0:
                raise ValidationError(_("Los días para descartar no pueden ser "
                                        "negativos (0 = no se descarta)."))


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    visar_descartada_previa_id = fields.Many2one(
        'crm.lead', string="Oportunidad descartada anterior", readonly=True,
        copy=False, index='btree_not_null',
        help="El cliente volvió a escribir después de que su oportunidad "
             "anterior se descartó: esta es nueva, y aquella es su historia.")

    # ------------------------------------------------------------------
    # Etapas
    # ------------------------------------------------------------------

    @api.model
    def _visar_descartadas_stage(self):
        return self.env.ref(DESCARTADAS_XMLID, raise_if_not_found=False)

    @api.model
    def _visar_descartar_dias(self):
        try:
            dias = int(self.env['ir.config_parameter'].sudo().get_param(
                DESCARTAR_PARAM, DESCARTAR_DEFAULT))
        except (TypeError, ValueError):
            dias = DESCARTAR_DEFAULT
        return max(dias, 0)

    @api.model
    def _visar_stage_ids(self, xmlids):
        etapas = [self.env.ref(x, raise_if_not_found=False) for x in xmlids]
        return [e.id for e in etapas if e]

    # ------------------------------------------------------------------
    # Cuándo fue lo último
    # ------------------------------------------------------------------

    def _visar_descartar_ultima_actividad(self):
        """Fecha de lo último que pasó con esta ficha. Respaldo: la ficha y su
        orden. El módulo del agente lo cambia por el último mensaje del cliente
        en las de WhatsApp."""
        self.ensure_one()
        fechas = [self.create_date, self.write_date]
        fechas += self.sudo().order_ids.mapped('write_date')
        return max(f for f in fechas if f)

    # ------------------------------------------------------------------
    # El cron
    # ------------------------------------------------------------------

    @api.model
    def _visar_crm_descartar_inactivas(self):
        """Pasa a «Descartadas» las fichas que no se concretaron. Idempotente."""
        dias = self._visar_descartar_dias()
        descartadas = self._visar_descartadas_stage()
        if not dias or not descartadas:
            return 0
        limite = fields.Datetime.now() - timedelta(days=dias)
        candidatas = self.browse()
        for team_xmlid, etapas in ((TEAM_WA, DESCARTABLES_WA),
                                   (TEAM_WEB, DESCARTABLES_WEB)):
            team = self.env.ref(team_xmlid, raise_if_not_found=False)
            stage_ids = self._visar_stage_ids(etapas)
            if not team or not stage_ids:
                continue
            # Prefiltro barato: algo que se movió después del límite no puede
            # estar inactivo (la conversación nunca es más nueva que «ahora»,
            # pero sí puede ser más vieja que la ficha: eso lo decide abajo).
            candidatas |= self.sudo().search([
                ('team_id', '=', team.id),
                ('stage_id', 'in', stage_ids),
                ('create_date', '<', limite),
            ], limit=LOTE, order='id')
        hechas = 0
        for lead in candidatas:
            try:
                with self.env.cr.savepoint():
                    if lead._visar_descartar_ultima_actividad() < limite:
                        lead._visar_descartar(dias)
                        hechas += 1
            except Exception:  # noqa: BLE001 - una ficha no tumba el cron
                _logger.exception("Descartadas: falló la ficha %s", lead.id)
        if hechas:
            _logger.info("Descartadas: %s oportunidad(es) sin respuesta", hechas)
        return hechas

    def _visar_descartar(self, dias):
        self.ensure_one()
        etapa_antes = self.stage_id.display_name
        self.sudo().write({'stage_id': self._visar_descartadas_stage().id})
        self._visar_descartar_extra()
        self.sudo().message_post(
            body=Markup(_("Pasó a <b>Descartadas</b>: %(dias)s días sin que se "
                          "concretara (estaba en «%(etapa)s»). Si el cliente "
                          "vuelve a escribir, se abre una oportunidad nueva.",
                          dias=dias, etapa=etapa_antes)),
            subtype_xmlid='mail.mt_note')

    def _visar_descartar_extra(self):
        """Gancho: el módulo del agente cancela aquí el recontacto pendiente."""
        return True

    # ------------------------------------------------------------------
    # Volvió: otra ficha, con la anterior a la vista
    # ------------------------------------------------------------------

    @api.model
    def _visar_descartada_previa(self, nat, group, team):
        """La última ficha descartada de (teléfono, grupo, equipo), o vacío."""
        descartadas = self._visar_descartadas_stage()
        if not descartadas or not nat or not team:
            return self.browse()
        return self.sudo().search([
            ('visar_wa_phone_norm', '=', nat),
            ('visar_service_group_id', '=', group.id if group else False),
            ('team_id', '=', team.id),
            ('stage_id', '=', descartadas.id),
        ], order='id desc', limit=1)

    def _visar_anotar_previa(self, previa):
        """En la ficha NUEVA: de dónde viene."""
        self.ensure_one()
        if not previa:
            return
        self.sudo().write({'visar_descartada_previa_id': previa.id})
        self.sudo().message_post(
            body=Markup(_("El cliente volvió a escribir. Su oportunidad anterior "
                          "se había descartado: <a href='#' data-oe-model='crm.lead' "
                          "data-oe-id='%(id)s'>%(nombre)s</a>.",
                          id=previa.id, nombre=previa.display_name)),
            subtype_xmlid='mail.mt_note')
