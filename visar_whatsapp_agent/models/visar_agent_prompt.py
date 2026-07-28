# -*- coding: utf-8 -*-
"""Prompt del sistema para el agente LLM, editable sin tocar codigo.

El runtime (`visar_fastapi`) trae el prompt activo por RPC y lo cachea con TTL.
Antes vivia en `prompts.py` (constante `BASE_PROMPT`), que ahora queda solo como
respaldo si Odoo no responde.

Se modela como una LISTA de registros (varios casos de uso, uno activo) aunque el
runtime hoy solo lea el activo: da una UI tipo "plantillas" y deja crecer sin
rediseñar. "Activo" = el primero por `sequence` entre los no archivados.
"""
from odoo import api, fields, models


class VisarAgentPrompt(models.Model):
    _name = 'visar.agent.prompt'
    _description = "Prompt del agente de WhatsApp"
    _order = 'sequence, id'

    name = fields.Char(
        string="Nombre", required=True,
        help="Nombre del caso de uso, p. ej. 'Atencion a clientes'.")
    body = fields.Text(
        string="Prompt del sistema", required=True,
        help="Texto base del system prompt. El catalogo de servicios se agrega "
             "automaticamente despues de este texto; no hace falta listarlo aqui.")
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)

    @api.model
    def _agent_active_body(self):
        """Cuerpo del prompt activo (o None si no hay ninguno configurado)."""
        record = self.search([], order='sequence, id', limit=1)
        return (record.body or None) if record else None
