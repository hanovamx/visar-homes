# -*- coding: utf-8 -*-
"""Configuracion del LLM del agente (proveedor + knobs), sin secretos.

El runtime trae esto por RPC y lo aplica en caliente, con cada refresco de su
cache (15 minutos, o al momento con "Aplicar ahora"): el modelo, cuanto razona
(`effort`), el margen para razonar (`thinking_headroom`), `max_tokens` y
`max_tool_iterations`. Solo el PROVEEDOR pide reiniciar el servicio, porque
cambia la credencial con la que se conecta.

SECRETOS FUERA DE ODOO: las credenciales del LLM (API key / token OAuth) NO se
guardan aqui; siguen en el `.env` del runtime. Ponerlas en Odoo las meteria en la
BD y en todos los backups. Mover secretos a Odoo es una decision posterior con
almacenamiento seguro. Ver `visar_fastapi/.context/50-status-roadmap.md`.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

PROVIDERS = [
    ('anthropic_api_key', "Anthropic (API key)"),
    ('anthropic_oauth', "Anthropic (OAuth)"),
    ('openai_api_key', "OpenAI (API key)"),
    ('codex_oauth', "Codex (OAuth)"),
]

EFFORTS = [
    ('low', "Bajo"),
    ('medium', "Medio"),
    ('high', "Alto"),
    ('xhigh', "Muy alto"),
    ('max', "Maximo"),
]


class VisarLlmConfig(models.Model):
    _name = 'visar.llm.config'
    _description = "Configuracion LLM del agente de WhatsApp"
    _inherit = ['visar.agent.runtime.mixin']
    _order = 'sequence, id'

    name = fields.Char(string="Nombre", required=True, default="Configuracion LLM")
    provider = fields.Selection(
        PROVIDERS, string="Proveedor", required=True, default='anthropic_api_key',
        help="Selector informativo: la credencial vive en el .env del runtime. "
             "Cambiar de proveedor requiere reiniciar el servicio.")
    model = fields.Char(
        string="Modelo", required=True, default='claude-haiku-5-5',
        help="Identificador del modelo tal como lo nombra el proveedor, por "
             "ejemplo claude-haiku-5-5. Se aplica en caliente.")
    effort = fields.Selection(
        EFFORTS, string="Razonamiento", default='medium',
        help="Hasta donde puede razonar el modelo antes de contestar; el decide "
             "en cada mensaje si lo usa. Mas alto = mejores respuestas en casos "
             "enredados, mas lento y mas caro. Vacio = no se le indica nada. "
             "Solo aplica de Claude Haiku 5.5 en adelante; a Haiku 4.5 no se le "
             "envia. Se aplica en caliente.")
    thinking_headroom = fields.Integer(
        string="Margen para razonar (tokens)", default=2048,
        help="Tokens que se suman a 'Max tokens' en cada llamada para que el "
             "modelo razone. El razonamiento sale del mismo tope que la "
             "respuesta: sin margen, el modelo puede agotarlo pensando y el "
             "cliente recibe una disculpa en vez de la respuesta. Solo se cobra "
             "lo que se usa. 0 para modelos que no razonan (Haiku 4.5). Se "
             "aplica en caliente.")
    max_tokens = fields.Integer(
        string="Max tokens", default=1024,
        help="Se aplica en caliente: surte efecto en el siguiente mensaje.")
    max_tool_iterations = fields.Integer(
        string="Max iteraciones de tool", default=4,
        help="Tope de vueltas del loop de tool calling. Se aplica en caliente.")
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)

    @api.constrains('thinking_headroom')
    def _check_thinking_headroom(self):
        for record in self:
            if record.thinking_headroom < 0:
                raise ValidationError(_(
                    "El margen para razonar no puede ser negativo."))

    @api.model
    def _agent_active_payload(self):
        """Config del LLM activa para el runtime (sin secretos)."""
        record = self.search([], order='sequence, id', limit=1)
        if not record:
            return {}
        return {
            'provider': record.provider,
            'model': record.model,
            'effort': record.effort or False,
            'thinking_headroom': record.thinking_headroom,
            'max_tokens': record.max_tokens,
            'max_tool_iterations': record.max_tool_iterations,
        }
