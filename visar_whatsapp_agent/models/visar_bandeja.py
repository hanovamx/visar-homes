# -*- coding: utf-8 -*-
"""Bandeja de WhatsApp en Odoo (opción 2 del escalamiento, 23-oct-2026).

Las charlas del agente aparecen en Conversaciones (Discuss) como chats de
WhatsApp del módulo nativo `whatsapp`, y una persona puede contestar desde ahí
—escritorio o app de Odoo—, no solo quien tiene la app WhatsApp Business.

El webhook de Meta NO se mueve: el runtime sigue siendo la puerta. Lo que ya
manda cada minuto para «Guardar las conversaciones» (`agent_log_messages`) se
copia también al chat de WhatsApp de ese número:

  * cliente → mensaje ENTRANTE: abre el chat y la ventana de 24 h de Odoo;
  * agente y persona (app) → mensaje SALIENTE ya enviado: se ve en el chat y
    Odoo no lo vuelve a mandar.

Lo que escribe una persona en el chat sí lo manda Odoo a Meta (el módulo nativo,
tal cual). Después se le avisa al runtime (`/internal/persona-odoo`) para que el
agente se aparte, igual que con un eco de la app WhatsApp Business.

Hechos de la prueba del 22-oct en visar-test (ver la memoria «bandeja-odoo-prueba»):
  * `message_post` ENVÍA todo mensaje que termina sin `wa_message_ids`. La copia
    del agente crea su `whatsapp.message` en `_notify_thread`, como hace Odoo con
    los entrantes, y nace `sent`: el cron «Send In Queue» manda lo `outgoing`.
  * `msg_uid` es único: repetir una copia revienta la transacción. Se busca antes.
  * La ventana de 24 h de Odoo se mide con el último ENTRANTE copiado: por eso se
    copia todo, no solo lo escalado.

Nace APAGADA (Ajustes → Visar → «Bandeja de WhatsApp en Odoo») y necesita
«Guardar las conversaciones» encendido: es la misma cola.
"""
import logging
import re

import requests

from odoo import api, fields, models
from odoo.addons.visar_base.models.wa_outbox_mixin import (
    BASE_URL_PARAM,
    DEFAULT_BASE_URL,
    SEND_TIMEOUT,
    TOKEN_PARAM,
)
from odoo.tools import html2plaintext, plaintext2html

_logger = logging.getLogger(__name__)

BANDEJA_PARAM = 'visar.bandeja.copiar'

# Prefijo del `msg_uid` de las copias: no es un id de Meta (no llegan estados
# para ellas) y así se distinguen de lo que Odoo mandó de verdad.
UID_COPIA = 'visar:'
# El uid con que el runtime anota lo que una persona escribió en Odoo: ya está en
# el chat, no se copia otra vez.
UID_DESDE_ODOO = 'odoo-wa:'


def visar_bandeja_encendida(env):
    return env['ir.config_parameter'].sudo().get_param(BANDEJA_PARAM, 'False') == 'True'


def _numero_whatsapp(phone):
    """'5218112345678' / '+52 81 1234 5678' → '528112345678' (como lo manda Meta).

    México: WhatsApp quitó el «1» de los móviles; el runtime puede traerlo.
    """
    digitos = re.sub(r'\D', '', str(phone or ''))
    if len(digitos) == 13 and digitos.startswith('521'):
        digitos = '52' + digitos[3:]
    return digitos


class VisarAgentTools(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    @api.model
    def agent_log_messages(self, payload):
        result = super().agent_log_messages(payload)
        if result.get('apagado') or not visar_bandeja_encendida(self.env):
            return result
        mensajes = [m for m in (payload or {}).get('mensajes') or [] if isinstance(m, dict)]
        try:
            # Un fallo de la bandeja no puede perder la conversación guardada.
            with self.env.cr.savepoint():
                result['bandeja'] = self._visar_bandeja_copiar(mensajes)
        except Exception:  # noqa: BLE001
            _logger.exception("Bandeja: no se pudieron copiar %d mensajes", len(mensajes))
        return result

    @api.model
    def _visar_bandeja_cuenta(self):
        cuentas = self.env['whatsapp.account'].sudo().search([], order='id')
        if len(cuentas) > 1:
            _logger.warning("Bandeja: hay %d cuentas de WhatsApp; se usa «%s»",
                            len(cuentas), cuentas[0].name)
        return cuentas[:1]

    @api.model
    def _visar_bandeja_copiar(self, mensajes):
        """Copia el lote a los chats de WhatsApp. Devuelve cuántos copió."""
        cuenta = self._visar_bandeja_cuenta()
        if not cuenta:
            return 0
        copiables = [
            m for m in mensajes
            if m.get('uid') and str(m.get('texto') or '').strip()
            and m.get('quien') in ('cliente', 'agente', 'persona')
            and not str(m['uid']).startswith(UID_DESDE_ODOO)
            and _numero_whatsapp(m.get('phone'))
        ]
        WaMessage = self.env['whatsapp.message'].sudo()
        ya = set(WaMessage.search([
            ('msg_uid', 'in', [UID_COPIA + m['uid'] for m in copiables]),
        ]).mapped('msg_uid'))
        autor_agente = self.env.user.partner_id
        autor_persona = self.env.company.partner_id
        canales = {}
        copiados = 0
        for m in copiables:
            msg_uid = UID_COPIA + m['uid']
            if msg_uid in ya:
                continue
            numero = _numero_whatsapp(m['phone'])
            if numero not in canales:
                canales[numero] = cuenta._find_active_channel(
                    numero, create_if_not_found=True)
            canal = canales[numero]
            body = plaintext2html(str(m['texto']).strip())
            if m['quien'] == 'cliente':
                canal.message_post(
                    body=body, message_type='whatsapp_message',
                    author_id=canal.whatsapp_partner_id.id,
                    subtype_xmlid='mail.mt_comment',
                    whatsapp_inbound_msg_uid=msg_uid)
            else:
                canal.with_context(visar_bandeja_saliente=msg_uid).message_post(
                    body=body, message_type='whatsapp_message',
                    author_id=(autor_agente if m['quien'] == 'agente'
                               else autor_persona).id,
                    subtype_xmlid='mail.mt_comment')
            ya.add(msg_uid)
            copiados += 1
        return copiados


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    def _notify_thread(self, message, msg_vals=False, **kwargs):
        # La copia de algo que el runtime YA mandó: su `whatsapp.message` nace
        # enviado, antes de notificar, y `message_post` ya no lo manda.
        msg_uid = self.env.context.get('visar_bandeja_saliente')
        if msg_uid and self.channel_type == 'whatsapp':
            self.env['whatsapp.message'].sudo().create({
                'body': message.body,
                'mail_message_id': message.id,
                'message_type': 'outbound',
                'mobile_number': f'+{self.whatsapp_number}',
                'msg_uid': msg_uid,
                'state': 'sent',
                'wa_account_id': self.wa_account_id.id,
            })
        return super()._notify_thread(message, msg_vals=msg_vals, **kwargs)


class WhatsappMessage(models.Model):
    _inherit = 'whatsapp.message'

    def _send_message(self, with_commit=False):
        res = super()._send_message(with_commit=with_commit)
        if visar_bandeja_encendida(self.env):
            self._visar_avisar_persona()
        return res

    def _visar_avisar_persona(self):
        """Alguien contestó desde un chat de Odoo: que el agente se aparte.

        Solo mensajes escritos en un chat (no plantillas mandadas desde un
        pedido o una cita) que Meta aceptó. Después del commit: si la
        transacción se cae, el agente no debe callarse por un mensaje que
        Odoo no registró.
        """
        avisos = []
        for wa in self.sudo():
            if (wa.state != 'sent' or wa.message_type != 'outbound'
                    or wa.wa_template_id
                    or wa.mail_message_id.model != 'discuss.channel'
                    or (wa.msg_uid or '').startswith(UID_COPIA)):
                continue
            texto = html2plaintext(wa.body or '').strip()
            if not texto:
                texto = "[adjunto]" if wa.mail_message_id.attachment_ids else ""
            avisos.append({
                'phone': _numero_whatsapp(wa.mobile_number_formatted or wa.mobile_number),
                'texto': texto or "[mensaje]",
                'uid': f'{UID_DESDE_ODOO}{wa.id}',
            })
        if not avisos:
            return
        params = self.env['ir.config_parameter'].sudo()
        base = (params.get_param(BASE_URL_PARAM) or DEFAULT_BASE_URL).rstrip('/')
        token = (params.get_param(TOKEN_PARAM) or '').strip()
        if not token:
            _logger.warning("Bandeja: sin token del runtime; el agente no sabrá "
                            "que contestó una persona")
            return

        def avisar():
            for aviso in avisos:
                try:
                    requests.post(f"{base}/internal/persona-odoo", json=aviso,
                                  headers={'X-Visar-Token': token},
                                  timeout=SEND_TIMEOUT).raise_for_status()
                except requests.RequestException:
                    _logger.exception("Bandeja: el runtime no recibió que una "
                                      "persona contestó a %s", aviso['phone'])

        self.env.cr.postcommit.add(avisar)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Sin `config_parameter`, como «Guardar las conversaciones»: así se guarda
    # 'True'/'False' a mano y un apagado no se confunde con «nunca se tocó».
    visar_bandeja = fields.Boolean(
        string="Bandeja de WhatsApp en Odoo",
        help="Cada charla del agente aparece en Conversaciones como un chat de "
             "WhatsApp, y cualquiera con usuario de Odoo puede contestar desde "
             "ahí. Al contestar, el agente se aparta como con la app WhatsApp "
             "Business.")

    @api.model
    def get_values(self):
        res = super().get_values()
        res['visar_bandeja'] = visar_bandeja_encendida(self.env)
        return res

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            BANDEJA_PARAM, 'True' if self.visar_bandeja else 'False')
