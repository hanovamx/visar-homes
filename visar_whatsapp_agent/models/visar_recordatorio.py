# -*- coding: utf-8 -*-
"""Recordatorio de servicio a clientes sin suscripción (26-oct-2026).

Reemplaza las tareas de Google Calendar con que Visar se acordaba de llamar a
quien pidió que le avisaran cada 3 o 6 meses:

```
el asesor       en el contacto: «Recordatorio de servicio» = cada 3 o 6 meses
servicio hecho  la próxima visita estimada = hoy + 3/6 meses (cada servicio
                vuelve a empezar la cuenta)
7 días antes    oportunidad en CRM con actividad «Llamar» para el asesor, y un
                WhatsApp al cliente: «ya casi toca tu siguiente servicio de X.
                ¿Te lo agendamos?»; si contesta, el agente lo agenda
después         la siguiente fecha estimada se corre otros 3/6 meses
```

No se le recuerda a quien tiene una suscripción activa (esas visitas ya las
pre-agenda el sistema) ni a quien ya tiene un servicio por venir.

Nace **apagado** (Ajustes → Visar): encenderlo es mandar mensajes a clientes.
"""
import json
import logging
from datetime import timedelta

import pytz

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import format_date

from .visar_agent_tools import DEFAULT_TZ, TZ_PARAM

_logger = logging.getLogger(__name__)

RECORDATORIO_PARAM = 'visar.recordatorio.enabled'
DIAS_PARAM = 'visar.recordatorio.dias_antes'
DIAS_DEFAULT = 7
USER_PARAM = 'visar.recordatorio.user_id'

TEMPLATE_KEY = 'recordatorio_servicio'

# Mientras el cliente no conteste, su respuesta se retoma con el contexto del
# recordatorio (`agent_partner_facts`): una semana, lo que falta para la fecha.
VIGENCIA_DIAS = 7

# Cuántos contactos por corrida. Lo que no quepa sale en la siguiente hora.
LOTE = 100

MESES = [('3', "Cada 3 meses"), ('6', "Cada 6 meses")]


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_recordatorio_enabled = fields.Boolean(
        string="Recordatorio de servicio",
        config_parameter=RECORDATORIO_PARAM,
        help="A los clientes sin suscripción que pidieron que les avisemos cada "
             "3 o 6 meses (campo «Recordatorio de servicio» del contacto), unos "
             "días antes de la fecha estimada se les crea una oportunidad en CRM "
             "con una actividad para llamarles, y se les manda un WhatsApp para "
             "agendar.")
    visar_recordatorio_dias_antes = fields.Integer(
        string="Días antes de la fecha estimada",
        config_parameter=DIAS_PARAM, default=DIAS_DEFAULT)
    visar_recordatorio_user_id = fields.Many2one(
        'res.users', string="Quién recibe la actividad",
        config_parameter=USER_PARAM, domain=[('share', '=', False)],
        help="Vacío: la persona a cargo de los escalamientos.")

    @api.constrains('visar_recordatorio_dias_antes')
    def _check_visar_recordatorio(self):
        for record in self:
            if not 1 <= record.visar_recordatorio_dias_antes <= 60:
                raise ValidationError(_(
                    "Los días antes de la fecha estimada van de 1 a 60."))


class ResPartner(models.Model):
    _inherit = 'res.partner'

    visar_recordatorio_meses = fields.Selection(
        MESES, string="Recordatorio de servicio", tracking=True,
        help="Para clientes sin suscripción que pidieron que les avisemos. Unos "
             "días antes de la próxima visita estimada se crea una oportunidad "
             "con una actividad para llamarle, y se le manda un WhatsApp.")
    visar_recordatorio_fecha = fields.Date(
        string="Próxima visita estimada", tracking=True,
        help="Se calcula sola al terminar cada servicio (fecha + 3/6 meses). Se "
             "puede cambiar a mano.")
    visar_recordatorio_servicio = fields.Char(
        string="Servicio a recordar",
        help="El último servicio terminado; es lo que se le ofrece agendar.")
    visar_recordatorio_avisado = fields.Date(
        string="Recordatorio enviado para", readonly=True, copy=False,
        help="La fecha estimada para la que ya salió el recordatorio.")
    visar_recordatorio_enviado_at = fields.Datetime(
        string="Recordatorio enviado el", readonly=True, copy=False)
    visar_recordatorio_texto = fields.Char(readonly=True, copy=False)

    def write(self, vals):
        res = super().write(vals)
        # Se acaba de pedir el recordatorio y no hay fecha: se cuenta desde el
        # último servicio terminado (o desde hoy, si no tiene ninguno).
        if vals.get('visar_recordatorio_meses') and 'visar_recordatorio_fecha' not in vals:
            for partner in self.filtered(lambda p: not p.visar_recordatorio_fecha):
                partner._visar_recordatorio_desde_ultimo_servicio()
        return res

    def _visar_recordatorio_desde_ultimo_servicio(self):
        self.ensure_one()
        tarea = self.env['project.task'].sudo().search([
            ('partner_id', 'child_of', self.commercial_partner_id.id),
            ('project_id.is_fsm', '=', True),
            ('state', '=', '1_done'),
        ], order='planned_date_begin desc, id desc', limit=1)
        base = (tarea.planned_date_begin.date() if tarea.planned_date_begin
                else fields.Date.context_today(self))
        vals = {'visar_recordatorio_fecha':
                base + relativedelta(months=int(self.visar_recordatorio_meses))}
        if tarea and not self.visar_recordatorio_servicio:
            vals['visar_recordatorio_servicio'] = tarea._visar_recordatorio_nombre()
        super(ResPartner, self).write(vals)


class ProjectTask(models.Model):
    _inherit = 'project.task'

    def write(self, vals):
        res = super().write(vals)
        # Mismo disparo que «Cerrado (won)» en visar_crm: el cierre del técnico
        # fija state = '1_done'.
        if vals.get('state') == '1_done':
            for tarea in self.filtered(lambda t: t.state == '1_done'):
                tarea._visar_recordatorio_reiniciar()
        return res

    def _visar_recordatorio_nombre(self):
        self.ensure_one()
        producto = self.sale_line_id.product_id
        return (producto.name if producto else self.name) or ''

    def _visar_recordatorio_reiniciar(self):
        """Servicio hecho: la cuenta del recordatorio vuelve a empezar."""
        self.ensure_one()
        if not self.project_id.is_fsm or self.visar_subscription_order_id:
            return
        cliente = self.partner_id.commercial_partner_id
        if not cliente or not cliente.visar_recordatorio_meses:
            return
        hoy = fields.Date.context_today(self)
        cliente.sudo().write({
            'visar_recordatorio_fecha':
                hoy + relativedelta(months=int(cliente.visar_recordatorio_meses)),
            'visar_recordatorio_servicio': self._visar_recordatorio_nombre(),
        })

    # ------------------------------------------------------------------
    # El cron
    # ------------------------------------------------------------------

    @api.model
    def _visar_cron_recordatorios(self):
        """Crea la oportunidad y encola el WhatsApp de a quien le toca. Idempotente:
        la fecha ya avisada queda en el contacto."""
        params = self.env['ir.config_parameter'].sudo()
        if params.get_param(RECORDATORIO_PARAM) not in ('True', 'true', '1'):
            return 0
        from .visar_handoff import visar_handoff_config
        horario = visar_handoff_config(self.env)
        ahora_local = self.env['visar.agent.tools']._visar_recordatorio_ahora(horario)
        if ahora_local.weekday() not in horario['dias'] \
                or not horario['desde'] <= ahora_local.hour < horario['hasta']:
            return 0
        try:
            dias = int(params.get_param(DIAS_PARAM) or DIAS_DEFAULT)
        except (TypeError, ValueError):
            dias = DIAS_DEFAULT
        hoy = ahora_local.date()
        clientes = self.env['res.partner'].sudo().search([
            ('visar_recordatorio_meses', '!=', False),
            ('visar_recordatorio_fecha', '!=', False),
            ('visar_recordatorio_fecha', '<=', hoy + timedelta(days=dias)),
        ], limit=LOTE, order='visar_recordatorio_fecha, id')
        hechos = 0
        for cliente in clientes:
            if cliente.visar_recordatorio_avisado == cliente.visar_recordatorio_fecha:
                continue
            try:
                with self.env.cr.savepoint():
                    if self._visar_recordatorio_avisar(cliente, hoy):
                        hechos += 1
            except Exception:  # noqa: BLE001 - un cliente no tumba el cron
                _logger.exception("Recordatorio: falló el contacto %s", cliente.id)
        if hechos:
            _logger.info("Recordatorio de servicio: %s cliente(s)", hechos)
        return hechos

    @api.model
    def _visar_recordatorio_avisar(self, cliente, hoy):
        """Oportunidad + actividad + WhatsApp para `cliente`. True si se avisó."""
        fecha = cliente.visar_recordatorio_fecha
        meses = int(cliente.visar_recordatorio_meses)
        # La siguiente vuelta, otros 3/6 meses: si no agenda, se le vuelve a
        # recordar. Si agenda, el servicio terminado reinicia la cuenta.
        siguiente = {'visar_recordatorio_avisado': fecha,
                     'visar_recordatorio_fecha': fecha + relativedelta(months=meses)}
        if self._visar_recordatorio_omitir(cliente):
            # Ya tiene suscripción o un servicio por venir: este no se avisa.
            cliente.write(siguiente)
            return False

        servicio = cliente.visar_recordatorio_servicio or _("servicio")
        nombre = (cliente.name or '').split()[0] if cliente.name else ''
        cuando = format_date(self.env, fecha, date_format="d 'de' MMMM")
        lead = self._visar_recordatorio_lead(cliente, servicio, cuando, meses)

        telefono = self._visar_poliza_telefono(cliente)
        texto = _("Hola%(nombre)s, ya casi toca tu siguiente servicio de "
                  "%(servicio)s. ¿Te lo agendamos?",
                  nombre=(' ' + nombre) if nombre else '', servicio=servicio)
        if telefono:
            ultimo = fecha - relativedelta(months=meses)
            self.env['visar.wa.recordatorio.message'].sudo()._visar_wa_enqueue(
                TEMPLATE_KEY, telefono, texto,
                params=[nombre or cliente.name or '', servicio],
                values={'partner_id': cliente.id, 'lead_id': lead.id,
                        'contexto_json': self.env['visar.wa.recordatorio.message']
                        ._visar_contexto_json(servicio, ultimo, meses, fecha)})
        else:
            lead.message_post(
                body=_("El cliente no tiene un teléfono válido: no se le mandó el "
                       "WhatsApp. Hay que llamarle."),
                subtype_xmlid='mail.mt_note')
        cliente.write(dict(siguiente, visar_recordatorio_texto=texto,
                           visar_recordatorio_enviado_at=fields.Datetime.now()
                           if telefono else False))
        return True

    @api.model
    def _visar_recordatorio_omitir(self, cliente):
        suscripcion = self.env['sale.order'].sudo().search_count([
            ('partner_id', 'child_of', cliente.id),
            ('is_subscription', '=', True),
            ('subscription_state', '=', '3_progress'),
        ], limit=1)
        if suscripcion:
            return True
        return bool(self.sudo().search_count([
            ('partner_id', 'child_of', cliente.id),
            ('project_id.is_fsm', '=', True),
            ('state', 'not in', ('1_done', '1_canceled')),
            ('planned_date_begin', '>=', fields.Datetime.now()),
        ], limit=1))

    @api.model
    def _visar_recordatorio_lead(self, cliente, servicio, cuando, meses):
        params = self.env['ir.config_parameter'].sudo()
        usuario = self.env['res.users']
        raw = params.get_param(USER_PARAM)
        if raw and raw.isdigit():
            usuario = usuario.sudo().browse(int(raw)).exists()
        if not usuario or usuario.share or not usuario.active:
            from .visar_handoff import visar_handoff_user
            usuario = visar_handoff_user(self.env)
        lead = self.env['crm.lead'].sudo().create({
            'name': _("Recordatorio: %(servicio)s — %(cliente)s",
                      servicio=servicio, cliente=cliente.name or ''),
            'type': 'opportunity',
            'partner_id': cliente.id,
            'user_id': usuario.id or False,
            'description': Markup(_(
                "<p>Pidió que le avisáramos <b>cada %(meses)s meses</b>. Su "
                "siguiente %(servicio)s se estima para el <b>%(cuando)s</b>.</p>",
                meses=meses, servicio=servicio, cuando=cuando)),
        })
        if usuario:
            lead.activity_schedule(
                'mail.mail_activity_data_call',
                date_deadline=fields.Date.context_today(lead),
                summary=_("Recordatorio: contactar para su %(servicio)s",
                          servicio=servicio),
                note=_("Pidió que le avisáramos cada %(meses)s meses. La "
                       "siguiente visita se estima para el %(cuando)s. También "
                       "se le mandó un WhatsApp para agendar.",
                       meses=meses, cuando=cuando),
                user_id=usuario.id)
        return lead


class VisarWaRecordatorioMessage(models.Model):
    """Buzón del recordatorio de servicio. Transporte: el mixin de siempre."""

    _name = 'visar.wa.recordatorio.message'
    _inherit = ['visar.wa.outbox.mixin']
    _description = "Recordatorio de servicio por WhatsApp (buzón de salida)"
    _order = 'create_date desc, id desc'
    _rec_name = 'template_key'

    partner_id = fields.Many2one('res.partner', string="Cliente", index=True,
                                 ondelete='cascade')
    lead_id = fields.Many2one('crm.lead', string="Oportunidad", ondelete='set null')
    contexto_json = fields.Char(default='{}')

    @api.model
    def _visar_wa_keys(self):
        return [(TEMPLATE_KEY, "Recordatorio de servicio")]

    @api.model
    def _visar_wa_ttl_minutes(self, template_key):
        return 6 * 60

    @api.model
    def _visar_wa_cron_xmlid(self):
        return 'visar_whatsapp_agent.visar_wa_recordatorio_outbox_cron'

    @api.model
    def _visar_wa_endpoints(self):
        # El «sí» tiene que entrar al agendado sabiendo de qué servicio se habla.
        return {TEMPLATE_KEY: '/internal/booking-event'}

    @api.model
    def _visar_contexto_json(self, servicio, ultimo, meses, fecha):
        return json.dumps({
            'servicio': servicio, 'meses': meses,
            'ultimo': format_date(self.env, ultimo, date_format="d 'de' MMMM"),
            'fecha_estimada': format_date(self.env, fecha, date_format="d 'de' MMMM"),
        }, ensure_ascii=False)

    def _visar_wa_context(self):
        self.ensure_one()
        try:
            contexto = json.loads(self.contexto_json or '{}')
        except ValueError:
            contexto = {}
        return {'contexto': contexto}

    def _visar_wa_chatter(self):
        self.ensure_one()
        return self.lead_id

    def _visar_attempt_send(self):
        enviado = super()._visar_attempt_send()
        if enviado and self.lead_id:
            self.lead_id.message_post(
                body=_("Se le mandó al cliente por WhatsApp el recordatorio: "
                       "«%s»", self.fallback_text),
                subtype_xmlid='mail.mt_note')
        return enviado


class VisarAgentRecordatorio(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    @api.model
    def _visar_recordatorio_ahora(self, horario=None):
        tz = pytz.timezone((horario or {}).get('tz') or self.env[
            'ir.config_parameter'].sudo().get_param(TZ_PARAM) or DEFAULT_TZ)
        return pytz.utc.localize(fields.Datetime.now()).astimezone(tz)

    @api.model
    def agent_partner_facts(self, payload):
        """Si el cliente contesta días después al recordatorio, el agente lo
        retoma con su contexto (como un recontacto de lead)."""
        datos = super().agent_partner_facts(payload)
        if datos.get('recontacto'):
            return datos
        try:
            with self.env.cr.savepoint():
                recordatorio = self._agent_recordatorio_vigente(
                    (payload or {}).get('phone'))
        except Exception:  # noqa: BLE001 - un dato de contexto no tumba nada
            _logger.exception("agent_partner_facts: no se pudo leer el recordatorio")
            recordatorio = None
        if recordatorio:
            datos['recontacto'] = recordatorio
        return datos

    @api.model
    def _agent_recordatorio_vigente(self, phone):
        nat = self._agent_normalize_phone(phone)
        if len(nat) != 10:
            return None
        desde = fields.Datetime.now() - timedelta(days=VIGENCIA_DIAS)
        clientes = self.env['res.partner'].sudo().search([
            ('visar_phone_nat10', '=', nat),
            ('visar_recordatorio_enviado_at', '>=', desde),
        ], order='visar_recordatorio_enviado_at desc', limit=2)
        if len(clientes) != 1:
            return None   # nadie, o ambiguo: mejor no decirle nada de otro
        mensaje = self.env['visar.wa.recordatorio.message'].sudo().search([
            ('partner_id', '=', clientes.id), ('state', '=', 'sent'),
        ], order='sent_at desc, id desc', limit=1)
        if not mensaje or mensaje.sent_at < desde:
            return None
        try:
            contexto = json.loads(mensaje.contexto_json or '{}')
        except ValueError:
            contexto = {}
        contexto['motivo'] = TEMPLATE_KEY
        return {
            'lead_id': mensaje.lead_id.id or None,
            'texto': mensaje.fallback_text,
            'contexto': contexto,
            'enviado': fields.Datetime.to_string(mensaje.sent_at),
        }
