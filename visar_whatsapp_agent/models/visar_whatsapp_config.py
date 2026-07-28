# -*- coding: utf-8 -*-
"""Cuenta de WhatsApp del agente (los datos no-secretos del .env del runtime).

En la Fase 2a esto es una SUPERFICIE DE CONFIGURACION: el runtime todavia no
consume estos campos por RPC (sigue leyendo su .env). Se modela ahora para tener
el look de la config del modulo nativo y para que la Fase 2b (salientes por
template) los use.

SECRETOS FUERA DE ODOO: el access token y el app secret NO se guardan aqui;
siguen en el `.env` del runtime (misma razon que en visar.llm.config).
"""
from odoo import fields, models


class VisarWhatsappConfig(models.Model):
    _name = 'visar.whatsapp.config'
    _description = "Cuenta de WhatsApp del agente"
    _order = 'sequence, id'

    name = fields.Char(string="Nombre", required=True, default="Cuenta WhatsApp")
    phone_uid = fields.Char(string="Phone Number ID")
    app_uid = fields.Char(string="Application ID")
    waba_uid = fields.Char(
        string="WhatsApp Business Account ID",
        help="Hoy el runtime no lo usa; se guarda por completitud.")
    verify_token = fields.Char(
        string="Verify token",
        help="Cadena que inventa el consultor; se pega igual en el panel de Meta.")
    webhook_path = fields.Char(string="Ruta del webhook", default='/whatsapp/webhook')
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)
