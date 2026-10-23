# -*- coding: utf-8 -*-
"""El número de WhatsApp del agente se elige en Odoo (23-oct-2026).

Hasta hoy el runtime leía el número y las llaves de Meta de su `.env`: cambiar
de número pedía entrar al servidor. Ahora salen de la cuenta de WhatsApp del
módulo nativo (WhatsApp → Configuración → Cuentas), la misma que usa la bandeja:

  * el runtime las pide al arrancar (`agent_whatsapp_credentials`); si Odoo no
    contesta, arranca con las del `.env`, que quedan de respaldo;
  * el botón «Aplicar en el agente» de la cuenta le pide al runtime que las
    vuelva a leer y las cambie en caliente, sin reiniciar.

Decisión del usuario (23-oct): todo configurable en Odoo, aunque el token de
Meta quede en la base. Solo lo ven los administradores de WhatsApp (así lo
trae el módulo nativo) y este método solo se lo da al usuario del runtime.
"""
import logging

import requests

from odoo import _, api, models
from odoo.addons.visar_base.models.wa_outbox_mixin import (
    BASE_URL_PARAM,
    DEFAULT_BASE_URL,
    SEND_TIMEOUT,
    TOKEN_PARAM,
)
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

USUARIO_RUNTIME = 'visar_whatsapp_agent.user_whatsapp_agent'


class VisarAgentTools(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    @api.model
    def agent_whatsapp_credentials(self):
        """Número y llaves de Meta para el runtime. {} si no hay cuenta.

        Solo para el usuario del runtime: el grupo «Solo lectura» del agente no
        basta, porque esto es un secreto y no una lectura del catálogo.
        """
        if not self.env.su and self.env.user != self.env.ref(
                USUARIO_RUNTIME, raise_if_not_found=False):
            raise AccessError(_("Solo el agente puede leer las llaves de WhatsApp."))
        cuenta = self._visar_bandeja_cuenta()
        if not cuenta:
            return {}
        return {
            'account': cuenta.name,
            'phone_id': cuenta.phone_uid or '',
            'token': cuenta.token or '',
            'app_id': cuenta.app_uid or '',
            'app_secret': cuenta.app_secret or '',
            'verify_token': cuenta.webhook_verify_token or '',
        }


class WhatsappAccount(models.Model):
    _inherit = 'whatsapp.account'

    def action_visar_aplicar_en_agente(self):
        """Botón «Aplicar en el agente»: el runtime vuelve a leer la cuenta."""
        self.ensure_one()
        usada = self.env['visar.agent.tools']._visar_bandeja_cuenta()
        if usada != self:
            raise UserError(_(
                "El agente usa la cuenta «%s», no esta.", usada.name or "-"))
        params = self.env['ir.config_parameter'].sudo()
        base = (params.get_param(BASE_URL_PARAM) or DEFAULT_BASE_URL).rstrip('/')
        token = (params.get_param(TOKEN_PARAM) or '').strip()
        if not token:
            raise UserError(_(
                "No está configurada la conexión con el agente (token vacío en "
                "Ajustes técnicos). Pídele a soporte que la revise."))
        try:
            respuesta = requests.post(
                f"{base}/internal/whatsapp-reload",
                headers={'X-Visar-Token': token}, timeout=SEND_TIMEOUT)
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
            raise UserError(_(
                "El agente no aplicó la cuenta (HTTP %(codigo)s). %(detalle)s",
                codigo=respuesta.status_code, detalle=detalle))
        cambios = respuesta.json().get('changed') or []
        if cambios:
            mensaje = _("El agente ya usa esta cuenta. Cambió: %s.",
                        ", ".join(cambios))
        else:
            mensaje = _("El agente ya usaba esta cuenta; no había nada que cambiar.")
        _logger.info("Cuenta de WhatsApp aplicada en el agente: %s", cambios or "sin cambios")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'type': 'success', 'message': mensaje, 'sticky': False},
        }
