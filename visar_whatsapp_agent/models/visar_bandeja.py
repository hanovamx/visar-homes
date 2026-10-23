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

Para trabajarla (23-oct-2026, segunda parte):

  * **Agente WhatsApp → Bandeja**: cada número con su estado y su color
    —necesita una persona / la atiende una persona / la atiende el agente /
    cerrada (más de 24 h sin escribir: ya no se le puede contestar libre)—,
    con «Abrir chat», «Tomar» y «Devolver al agente».
  * En el chat mismo, «Tomar la conversación» y «Devolver al agente»
    (`static/src/bandeja/thread_actions.js`). Tomar aparta al agente ANTES de
    escribir, para que no conteste mientras la persona redacta.
  * La actividad del escalamiento trae el enlace al chat.

**El contacto del chat (23-oct-2026, arreglo).** El módulo nativo no abre un chat
sin contacto: si el número no tiene ficha, crea una con el número por nombre.
Eso convertía a todo cliente nuevo en «conocido» antes de que el agente le
pidiera el nombre: no se le preguntaba al agendar y la dirección y los leads
quedaban a nombre de «+52…». Ahora ese contacto nace ARCHIVADO —el agente y el
resto de Odoo no lo ven— y el chat se pasa a la ficha real en cuanto existe
(`_visar_bandeja_chat`).
"""
import logging
import re
from datetime import timedelta

import requests
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.addons.visar_base.models.wa_outbox_mixin import (
    BASE_URL_PARAM,
    DEFAULT_BASE_URL,
    SEND_TIMEOUT,
    TOKEN_PARAM,
)
from odoo.exceptions import UserError
from odoo.tools import html2plaintext, plaintext2html

_logger = logging.getLogger(__name__)

BANDEJA_PARAM = 'visar.bandeja.copiar'

# Prefijo del `msg_uid` de las copias: no es un id de Meta (no llegan estados
# para ellas) y así se distinguen de lo que Odoo mandó de verdad.
UID_COPIA = 'visar:'
# El uid con que el runtime anota lo que una persona escribió en Odoo: ya está en
# el chat, no se copia otra vez.
UID_DESDE_ODOO = 'odoo-wa:'

# Meta solo deja escribir libre dentro de las 24 h del último mensaje del cliente.
VENTANA_HORAS = 24

ESTADOS = [
    ('escalado', "Necesita una persona"),
    ('persona', "La atiende una persona"),
    ('agente', "La atiende el agente"),
    ('cerrada', "Cerrada"),
]
ORDEN = {clave: n for n, (clave, _nombre) in enumerate(ESTADOS)}


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
        Contacto = self.env['visar.agent.contacto'].sudo()
        canales = {}
        copiados = 0
        for m in copiables:
            msg_uid = UID_COPIA + m['uid']
            if msg_uid in ya:
                continue
            numero = _numero_whatsapp(m['phone'])
            if numero not in canales:
                canales[numero] = self._visar_bandeja_chat(cuenta, m['phone'])
                contacto = Contacto._visar_buscar_o_crear(m['phone'])
                if contacto and contacto.visar_canal_id != canales[numero]:
                    contacto.visar_canal_id = canales[numero]
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


    @api.model
    def _visar_bandeja_canal(self, phone):
        """El chat de WhatsApp de `phone` (creándolo), o vacío sin bandeja."""
        Canal = self.env['discuss.channel']
        numero = _numero_whatsapp(phone)
        if not numero or not visar_bandeja_encendida(self.env):
            return Canal
        cuenta = self._visar_bandeja_cuenta()
        if not cuenta:
            return Canal
        return self._visar_bandeja_chat(cuenta, phone)

    @api.model
    def _visar_bandeja_chat(self, cuenta, phone):
        """El chat de `phone`, con el contacto correcto.

        Sin ficha del cliente, el contacto que crea el módulo nativo para abrir
        el chat se archiva en el acto: es solo el número, y el agente tiene que
        seguir tratándolo como nuevo (pedirle el nombre y crear su ficha). En
        cuanto la ficha real existe, el chat pasa a ella.
        """
        Partner = self.env['res.partner'].sudo()
        clave = Partner._visar_phone_nat10_value(phone)
        reales = Partner.search([('visar_phone_nat10', '=', clave)]) if clave else Partner
        canal = cuenta._find_active_channel(
            _numero_whatsapp(phone), create_if_not_found=True)
        socio = canal.whatsapp_partner_id.sudo()
        if (socio and socio.active and socio not in reales
                and socio.create_date and socio.create_date >= self.env.cr.now()):
            socio.active = False
            _logger.info("Bandeja: el contacto provisional de %s queda archivado",
                         canal.whatsapp_number)
        elif len(reales) == 1 and socio != reales and not socio.active:
            canal.sudo().write({
                'whatsapp_partner_id': reales.id,
                'name': f"{reales.name} ({canal.whatsapp_number})",
            })
            canal.sudo()._add_members(partners=reales, post_joined_message=False)
        return canal

    @api.model
    def _agent_handoff_donde_contestar(self, payload):
        """Con la bandeja, la actividad lleva al chat de Odoo."""
        try:
            with self.env.cr.savepoint():
                canal = self._visar_bandeja_canal(payload.get('phone'))
        except Exception:  # noqa: BLE001 - sin enlace, la actividad igual sale
            _logger.exception("Bandeja: no se pudo enlazar el chat de %s",
                              payload.get('phone'))
            canal = None
        if not canal:
            return super()._agent_handoff_donde_contestar(payload)
        return Markup(
            '<p>El cliente está esperando. Contéstale desde '
            '<a href="/odoo/action-mail.action_discuss?active_id={id}">'
            'su chat de WhatsApp en Odoo</a> (o desde la app WhatsApp Business); '
            'el agente no le escribe mientras tanto. Al terminar, <b>Devolver al '
            'agente</b> desde el chat o en Agente WhatsApp → Bandeja.</p>'
        ).format(id=canal.id)


class VisarAgentContacto(models.Model):
    _inherit = 'visar.agent.contacto'

    visar_canal_id = fields.Many2one(
        'discuss.channel', string="Chat de WhatsApp", readonly=True,
        ondelete='set null')
    visar_bandeja_estado = fields.Selection(
        ESTADOS, string="Estado", compute='_compute_visar_bandeja_estado',
        store=True, index=True)
    # Para ordenar la bandeja: lo urgente arriba (un Selection ordena por texto).
    visar_bandeja_orden = fields.Integer(
        compute='_compute_visar_bandeja_estado', store=True)

    @api.depends('visar_atencion', 'ultimo_mensaje')
    def _compute_visar_bandeja_estado(self):
        limite = fields.Datetime.now() - timedelta(hours=VENTANA_HORAS)
        for contacto in self:
            if contacto.visar_atencion:
                estado = contacto.visar_atencion
            elif contacto.ultimo_mensaje and contacto.ultimo_mensaje >= limite:
                estado = 'agente'
            else:
                estado = 'cerrada'
            contacto.visar_bandeja_estado = estado
            contacto.visar_bandeja_orden = ORDEN[estado]

    @api.model
    def _visar_bandeja_cerrar_vencidas(self):
        """Cron: las que pasaron 24 h sin que el cliente escriba, a «Cerrada»."""
        limite = fields.Datetime.now() - timedelta(hours=VENTANA_HORAS)
        vencidas = self.sudo().search([
            ('visar_bandeja_estado', '=', 'agente'),
            '|', ('ultimo_mensaje', '<', limite), ('ultimo_mensaje', '=', False)])
        if vencidas:
            # Nada en lo que depende cambió (solo pasó el tiempo): se recalcula a mano.
            vencidas._compute_visar_bandeja_estado()
            vencidas.flush_recordset(['visar_bandeja_estado', 'visar_bandeja_orden'])

    def _visar_runtime(self, ruta, payload):
        """POST al runtime con el token. Devuelve la respuesta 200 o lanza."""
        params = self.env['ir.config_parameter'].sudo()
        base = (params.get_param(BASE_URL_PARAM) or DEFAULT_BASE_URL).rstrip('/')
        token = (params.get_param(TOKEN_PARAM) or '').strip()
        if not token:
            raise UserError(_(
                "No está configurada la conexión con el agente (token vacío en "
                "Ajustes técnicos). Pídele a soporte que la revise."))
        try:
            respuesta = requests.post(f"{base}{ruta}", json=payload,
                                      headers={'X-Visar-Token': token},
                                      timeout=SEND_TIMEOUT)
        except requests.RequestException as exc:
            raise UserError(_(
                "No se pudo hablar con el agente: %s. Inténtalo en un momento.",
                exc)) from exc
        if respuesta.status_code != 200:
            detalle = ''
            try:
                detalle = respuesta.json().get('detail') or ''
            except ValueError:
                pass
            raise UserError(detalle or _(
                "El agente no aceptó el cambio (HTTP %s). Inténtalo en un "
                "momento.", respuesta.status_code))
        return respuesta

    def action_visar_tomar(self):
        """«Tomar»: el agente deja de contestarle, antes de que la persona escriba."""
        for contacto in self:
            contacto._visar_runtime('/internal/handoff-take',
                                    {'phone': contacto.phone or contacto.name})
            vals = {'visar_atencion': 'persona',
                    'visar_atencion_motivo': contacto.visar_atencion_motivo or _(
                        "La tomó %s desde Odoo", self.env.user.name)}
            if not contacto.visar_atencion:
                vals['visar_atencion_desde'] = fields.Datetime.now()
            contacto.sudo().write(vals)
            if contacto.visar_canal_id:
                contacto.visar_canal_id._visar_nota(_(
                    "%s tomó la conversación: el agente ya no contesta.",
                    self.env.user.name))
        return True

    def action_visar_devolver_al_agente(self):
        canales = self.filtered('visar_atencion').visar_canal_id
        res = super().action_visar_devolver_al_agente()
        for canal in canales:
            canal._visar_nota(_("%s devolvió la conversación al agente.",
                                self.env.user.name))
        return res

    def action_visar_abrir_chat(self):
        self.ensure_one()
        canal = self.visar_canal_id
        if not canal:
            raise UserError(_("Este número todavía no tiene chat en Odoo."))
        canal._visar_unirme()
        return canal._visar_accion_abrir()


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    def _visar_nota(self, texto):
        """Una línea interna en el chat: no se le manda al cliente."""
        self.sudo().message_post(body=texto, message_type='notification',
                                 subtype_xmlid='mail.mt_note')

    def _visar_unirme(self):
        """Quien abre o toma el chat entra como miembro, para verlo y escribir."""
        socio = self.env.user.partner_id
        if socio not in self.sudo().channel_member_ids.partner_id:
            self.sudo()._add_members(users=self.env.user, post_joined_message=False)

    def _visar_accion_abrir(self):
        return {'type': 'ir.actions.client', 'tag': 'mail.action_discuss',
                'context': {'active_id': self.id}}

    def _visar_contacto(self):
        self.ensure_one()
        if self.channel_type != 'whatsapp' or not self.whatsapp_number:
            raise UserError(_("Esto solo aplica a chats de WhatsApp."))
        contacto = self.env['visar.agent.contacto'].sudo()._visar_buscar_o_crear(
            self.whatsapp_number)
        if not contacto:
            raise UserError(_("No se encontró el contacto de este número."))
        if contacto.visar_canal_id != self:
            contacto.visar_canal_id = self
        return contacto

    @staticmethod
    def _visar_aviso(mensaje, tipo='success'):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'type': tipo, 'message': mensaje, 'sticky': False}}

    def action_visar_tomar(self):
        """Botón del chat «Tomar la conversación»."""
        contacto = self._visar_contacto()
        if contacto.visar_atencion == 'persona':
            return self._visar_aviso(_("Ya la atiende una persona; el agente "
                                       "no contesta."), 'info')
        self._visar_unirme()
        contacto.action_visar_tomar()
        return self._visar_aviso(_("Tomaste la conversación: el agente ya no "
                                   "contesta."))

    def action_visar_devolver(self):
        """Botón del chat «Devolver al agente»."""
        contacto = self._visar_contacto()
        if not contacto.visar_atencion:
            return self._visar_aviso(_("Ya la atiende el agente."), 'info')
        contacto.action_visar_devolver_al_agente()
        return self._visar_aviso(_("El agente vuelve a contestar en esta "
                                   "conversación."))

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
