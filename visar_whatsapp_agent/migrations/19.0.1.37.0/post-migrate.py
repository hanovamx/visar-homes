# -*- coding: utf-8 -*-
"""El agente pasa de Claude Haiku 4.5 a Claude Haiku 5.5.

Hasta esta version el campo `model` era informativo: el runtime usaba el del
`.env`. Desde aqui manda el de Odoo, junto con `effort` y `thinking_headroom`,
que nacen con esta version. Las configuraciones que seguian en el modelo
anterior se llevan al nuevo con los valores que necesita para trabajar:
razonamiento medio y 2048 tokens de margen.

Solo se tocan las que dicen `claude-haiku-4-5`. Una que alguien haya apuntado a
otro modelo se queda con su modelo; los campos nuevos toman su default.

Para volver atras no hace falta codigo: basta escribir `claude-haiku-4-5` en
Agente -> Configuracion LLM y pulsar "Aplicar ahora".
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE visar_llm_config
           SET model = 'claude-haiku-5-5',
               effort = 'medium',
               thinking_headroom = 2048
         WHERE model = 'claude-haiku-4-5'
     RETURNING id, name
    """)
    for fila in cr.fetchall():
        _logger.info("visar.llm.config %s (%s): claude-haiku-4-5 -> "
                     "claude-haiku-5-5, effort medium, margen 2048", *fila)
