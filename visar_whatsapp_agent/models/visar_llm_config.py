# -*- coding: utf-8 -*-
"""Configuracion del LLM del agente (proveedor + knobs), sin secretos.

El runtime trae esto por RPC y aplica lo barato en caliente: `max_tokens` y
`max_tool_iterations` (parametros por llamada) surten efecto en el siguiente
mensaje. El `model` se surface aqui pero cambiarlo requiere REINICIAR el servicio
(el proveedor LLM se construye al arrancar).

SECRETOS FUERA DE ODOO: las credenciales del LLM (API key / token OAuth) NO se
guardan aqui; siguen en el `.env` del runtime. Ponerlas en Odoo las meteria en la
BD y en todos los backups. Mover secretos a Odoo es una decision posterior con
almacenamiento seguro. Ver `visar_fastapi/.context/50-status-roadmap.md`.
"""
from odoo import api, fields, models

PROVIDERS = [
    ('anthropic_api_key', "Anthropic (API key)"),
    ('anthropic_oauth', "Anthropic (OAuth)"),
    ('openai_api_key', "OpenAI (API key)"),
    ('codex_oauth', "Codex (OAuth)"),
]


class VisarLlmConfig(models.Model):
    _name = 'visar.llm.config'
    _description = "Configuracion LLM del agente de WhatsApp"
    _order = 'sequence, id'

    name = fields.Char(string="Nombre", required=True, default="Configuracion LLM")
    provider = fields.Selection(
        PROVIDERS, string="Proveedor", required=True, default='anthropic_api_key',
        help="Selector informativo: la credencial vive en el .env del runtime. "
             "Cambiar de proveedor requiere reiniciar el servicio.")
    model = fields.Char(
        string="Modelo", required=True, default='claude-haiku-4-5',
        help="Cambiar el modelo requiere REINICIAR el runtime (el proveedor se "
             "construye al arrancar).")
    max_tokens = fields.Integer(
        string="Max tokens", default=1024,
        help="Se aplica en caliente: surte efecto en el siguiente mensaje.")
    max_tool_iterations = fields.Integer(
        string="Max iteraciones de tool", default=4,
        help="Tope de vueltas del loop de tool calling. Se aplica en caliente.")
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)

    @api.model
    def _agent_active_payload(self):
        """Config del LLM activa para el runtime (sin secretos)."""
        record = self.search([], order='sequence, id', limit=1)
        if not record:
            return {}
        return {
            'provider': record.provider,
            'model': record.model,
            'max_tokens': record.max_tokens,
            'max_tool_iterations': record.max_tool_iterations,
        }
