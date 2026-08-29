# -*- coding: utf-8 -*-
"""Recontacto de leads fríos: cuándo vuelve a escribir el agente, y a quién no.

Un lead de WhatsApp nace en *Nuevo* y ahí se queda. Si el cliente no vuelve, lo
único que le pasa es que días después el cron de caducidad lo marca perdido —
**sin que nadie le haya escrito nunca**. Este archivo cierra ese hueco: seis
horas después de que el cliente dejó de escribir, y solo dentro del horario
hábil, el agente vuelve con un mensaje que el modelo redacta a partir de lo que
esa conversación fue.

## El reparto del trabajo

* **Odoo decide CUÁNDO** (aquí). Tiene el cron, la durabilidad y el lead a la
  vista del asesor. Un temporizador que vive en la memoria de un proceso se
  pierde en el primer reinicio.
* **El runtime decide QUÉ** (`/internal/lead-followup`). Tiene el modelo. Un
  texto fijo con el nombre del servicio metido con `%s` no es un recontacto
  personalizado, es una campaña — y se lee como tal.

Odoo le manda al runtime la **foto** (`visar_wa_followup_context`) porque para
cuando toca escribir la conversación ya no existe: el runtime la caduca a las 3 h
y el recontacto sale a las 6 h. Guardar la foto en el lead —y no alargar el TTL—
deja intacto que un cliente que vuelve cinco horas después empiece de cero en vez
de reanudar un cuestionario a medias.

## Quién NO recibe recontacto

Cinco exclusiones, y ninguna es cosmética:

1. **Ya no está en *Nuevo*.** Pagó, o un asesor lo movió. El pipeline es la
   fuente de verdad; no se duplica el estado.
2. **Escalado a un asesor** (`visar_source = whatsapp_handoff`). Hay una persona
   encima; un empujón automático le pasa por arriba.
3. **Dijo que no.** Lo detecta el runtime y lo avisa (`agent_drop_followup`).
   Insistirle a quien ya declinó es exactamente lo que hace que la gente bloquee
   el número.
4. **Se quejó.** Igual que el anterior. Venderle a alguien enojado se lee fatal.
5. **Ya es cliente de Visar** en ese grupo. Misma exclusión que `agent_track_lead`
   aplica al crear.

Las dos primeras y la última se comprueban **al enviar**, no al programar: entre
que se programa y que sale pasan seis horas, y en seis horas el cliente puede
pagar. Las otras dos las marca el runtime en el momento.
"""
import json
import logging

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

WA_STAGE_NUEVO_XMLID = 'visar_crm.crm_stage_wa_nuevo'

# Motivos por los que un recontacto programado acaba sin enviarse. Se guardan
# para que el asesor pueda ver en el lead por qué el agente no insistió.
MOTIVOS = {
    'etapa_avanzada': "El lead ya no está en Nuevo",
    'escalado': "Escalado a un asesor",
    'declino': "El cliente dijo que no",
    'queja': "El cliente puso una queja",
    'cliente_existente': "Ya es cliente de Visar",
    'apagado': "El recontacto está apagado",
    'caducado': "Se pasó la ventana de 24 h de WhatsApp",
}


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    visar_wa_followup_state = fields.Selection(
        selection=[
            ('none', "Sin programar"),
            ('scheduled', "Programado"),
            ('queued', "En cola"),
            ('sent', "Enviado"),
            ('skipped', "Descartado"),
        ],
        string="Recontacto", default='none', copy=False, index=True,
        help="Estado del mensaje de recontacto automático por WhatsApp.")

    visar_wa_followup_due = fields.Datetime(
        string="Recontactar el", copy=False, index=True,
        help="Momento calculado: último mensaje del cliente + la espera "
             "configurada, empujado al siguiente horario hábil si cae fuera.")

    visar_wa_followup_context = fields.Text(
        string="Contexto del recontacto (JSON)", copy=False,
        help="Foto de la conversación al momento de enfriarse: servicio, "
             "cotización, en qué paso se quedó. Es lo que lee el modelo para "
             "redactar, porque para entonces la conversación ya caducó.")

    visar_wa_followup_sent_at = fields.Datetime(
        string="Recontactado el", readonly=True, copy=False)

    visar_wa_followup_skip_reason = fields.Char(
        string="Motivo de descarte", readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Programar
    # ------------------------------------------------------------------

    def _visar_wa_schedule_followup(self, context=None, desde=None):
        """(Re)programa el recontacto contando desde AHORA. Nunca lanza.

        Se llama en cada turno del cliente que llega hasta Odoo, así que la
        reprogramación es lo normal y no la excepción: mientras el cliente
        siga escribiendo, el reloj se reinicia. El recontacto sale seis horas
        después del último mensaje, no seis horas después del primero.

        Un lead ya enviado o descartado NO se reprograma aquí: si el cliente
        vuelve a escribir después de un recontacto, la conversación es nueva y
        lo que corresponde es atenderla, no volver a empujar.
        """
        config = self.env['visar.followup.config'].sudo()._visar_active()
        if not config or not config.enabled:
            return
        desde = desde or fields.Datetime.now()
        for lead in self:
            if lead.visar_wa_followup_state in ('queued', 'sent', 'skipped'):
                continue
            valores = {
                'visar_wa_followup_state': 'scheduled',
                'visar_wa_followup_due': self._visar_wa_followup_due_at(
                    desde, config),
            }
            if context is not None:
                valores['visar_wa_followup_context'] = json.dumps(
                    context, ensure_ascii=False)
            lead.sudo().write(valores)

    @api.model
    def _visar_wa_followup_due_at(self, desde, config):
        """Cuándo toca escribir: espera + horario hábil, en la zona de Visar.

        Aritmética pura y sin efectos, para poder probarla sin cron ni HTTP.
        Si la espera vence antes de que abra el horario hábil, se adelanta a la
        apertura del mismo día; si vence después de que cierre, se va a la
        apertura del día siguiente.
        """
        tz_name = self.env['ir.config_parameter'].sudo().get_param(
            'visar.agent.timezone', 'America/Monterrey')
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone('America/Monterrey')

        objetivo = fields.Datetime.add(desde, minutes=config.delay_minutes)
        local = pytz.utc.localize(objetivo).astimezone(tz).replace(tzinfo=None)

        inicio, fin = config.window_start_hour, config.window_end_hour
        if local.hour < inicio:
            local = local.replace(hour=inicio, minute=0, second=0, microsecond=0)
        elif local.hour >= fin:
            local = fields.Datetime.add(local, days=1).replace(
                hour=inicio, minute=0, second=0, microsecond=0)

        # localize (y no replace(tzinfo=…)) para que el offset lo ponga la zona:
        # México no tiene horario de verano desde 2022, pero atarse a eso es
        # atarse a una decisión de un gobierno.
        return tz.localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    def _visar_wa_drop_followup(self, reason):
        """Cancela el recontacto por algo que solo el runtime puede ver."""
        for lead in self:
            if lead.visar_wa_followup_state == 'sent':
                continue
            lead.sudo().write({
                'visar_wa_followup_state': 'skipped',
                'visar_wa_followup_due': False,
                'visar_wa_followup_skip_reason': MOTIVOS.get(reason, reason),
            })

    # ------------------------------------------------------------------
    # Exclusiones
    # ------------------------------------------------------------------

    def _visar_wa_followup_blocked(self):
        """Motivo por el que este lead NO debe recibir recontacto, o None.

        Se vuelve a preguntar **al enviar**. Entre programar y enviar pasan seis
        horas: en seis horas el cliente paga, un asesor toma el caso, o resulta
        que ya tenía servicio con nosotros.
        """
        self.ensure_one()
        if self.visar_source == 'whatsapp_handoff':
            return 'escalado'
        nuevo = self.env.ref(WA_STAGE_NUEVO_XMLID, raise_if_not_found=False)
        if not nuevo or self.stage_id != nuevo:
            return 'etapa_avanzada'
        if self._visar_wa_ya_es_cliente():
            return 'cliente_existente'
        return None

    def _visar_wa_ya_es_cliente(self):
        """¿El contacto ya tiene servicio con Visar en el grupo del lead?

        Reusa el mismo predicado que `agent_track_lead` aplica al crear, para que
        "cliente existente" signifique una sola cosa en todo el agente. Sin grupo
        (leads que nacieron de una pregunta suelta) no se puede afirmar nada:
        se deja pasar, que es el lado seguro para un mensaje que solo pregunta si
        le seguimos sirviendo.
        """
        self.ensure_one()
        if not self.partner_id or not self.visar_service_group_id:
            return False
        Tools = self.env['visar.agent.tools'].sudo()
        return Tools._agent_partner_has_service_in_group(
            self.partner_id, self.visar_service_group_id)

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _visar_wa_cron_followup(self):
        """Encola los recontactos que ya tocan. Entrada del cron.

        No envía: encola en `visar.wa.lead.message`, que es quien habla con el
        runtime y quien sabe reintentar. Aquí solo se decide **a quién**.
        """
        config = self.env['visar.followup.config'].sudo()._visar_active()
        vencidos = self.sudo().search([
            ('visar_wa_followup_state', '=', 'scheduled'),
            ('visar_wa_followup_due', '<=', fields.Datetime.now()),
        ])
        if not vencidos:
            return 0
        if not config or not config.enabled:
            vencidos._visar_wa_drop_followup('apagado')
            return 0

        encolados = 0
        for lead in vencidos:
            motivo = lead._visar_wa_followup_blocked()
            if motivo:
                lead._visar_wa_drop_followup(motivo)
                continue
            if not lead._visar_wa_enqueue_followup():
                continue
            # 'En cola', no 'Enviado': quien lo da por enviado es el buzón,
            # cuando el POST al runtime contesta 200. Marcarlo aquí sería
            # adelantarse a un envío que todavía puede fallar cinco veces.
            lead.sudo().write({'visar_wa_followup_state': 'queued',
                               'visar_wa_followup_due': False})
            encolados += 1
        return encolados

    def _visar_wa_enqueue_followup(self):
        """Pone el recontacto en el buzón de salida. Devuelve si se encoló."""
        self.ensure_one()
        telefono = self._visar_wa_followup_phone()
        if not telefono:
            _logger.info(
                "Lead %s sin wa_id: no hay a dónde mandar el recontacto", self.id)
            return False
        mensaje = self.env['visar.wa.lead.message'].sudo()._visar_wa_enqueue(
            'lead_followup', telefono,
            self._visar_wa_followup_fallback(),
            values={'lead_id': self.id, 'partner_id': self.partner_id.id or False})
        return bool(mensaje)

    def _visar_wa_followup_phone(self):
        """El wa_id EXACTO con el que escribir, del contexto o del lead.

        El `visar_wa_phone_norm` son los últimos 10 dígitos: sirve para deduplicar
        y NO sirve para mandar, porque el runtime encuentra la conversación por el
        wa_id completo. El runtime guarda el suyo en la foto justamente por esto.
        """
        self.ensure_one()
        contexto = self._visar_wa_followup_data()
        wa_id = (contexto.get('wa_id') or '').strip()
        if wa_id:
            return wa_id
        return (self.phone or self.visar_wa_phone_norm or '').strip()

    def _visar_wa_followup_data(self):
        """La foto guardada, como dict. Nunca lanza: JSON roto = sin contexto."""
        self.ensure_one()
        try:
            data = json.loads(self.visar_wa_followup_context or '{}')
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _visar_wa_followup_fallback(self):
        """Texto si el modelo no puede redactar (runtime sin LLM, o error).

        Deliberadamente corto y sin inventar nada: no nombra servicio ni precio,
        porque el caso en que se usa es justo aquel en que no se confía en tener
        el contexto bien. Un recontacto genérico es peor que uno personalizado y
        mucho mejor que ninguno.
        """
        self.ensure_one()
        return ("Hola, te escribo de Visar Homes. Nos quedamos a medias el otro "
                "día. ¿Sigues interesado? Con gusto retomamos donde lo dejamos.")
