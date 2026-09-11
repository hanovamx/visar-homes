# -*- coding: utf-8 -*-
"""Qué le dice al agente cada etapa de servicio — configurable, no deducido.

## El problema que cierra

El agente clasificaba los servicios del cliente **deduciendo**: si Odoo marcaba
la etapa como cerrada (`fold`), el servicio "ya pasó". La deducción es razonable
y estuvo bien un mes, hasta que se topó con la etapa nativa de Field Service
`industry_fsm.planning_project_stage_4` — que en inglés se llama *Cancelled*,
que Visar renombró a **"Incidencia — Reprogramar"**, y que significa justo lo
contrario de cerrado: el técnico fue, el cliente no estaba, y el servicio sigue
**pendiente**. El cliente la leía bajo "tus servicios anteriores", con una fecha
futura, y encima con el nombre interno de la etapa.

Visar no cancela servicios (regla de negocio, 11-sep-2026), así que la etapa
"cerrada" de Odoo no puede seguir significando "terminado" para el chat.

## Por qué configurable y no un arreglo puntual

Se barajó quitarle la marca de cerrada a esa etapa. Arregla el caso y no toca
código, pero mueve una casilla que también gobierna el kanban de operaciones —
y dejaría la regla escondida en una casilla de Odoo que se llama distinto de lo
que significa. Aquí la etapa **dice** lo que el agente debe hacer con ella:

  * `visar_agent_bucket`: si el servicio va en "próximos" o en "anteriores".
    `auto` (el valor de fábrica) conserva EXACTAMENTE lo de antes, así que
    instalar esto no cambia nada por su cuenta;
  * `visar_agent_label`: lo que lee el cliente. Vacío = el nombre de la etapa,
    como siempre. Existe porque los nombres de etapa están escritos para el
    staff: "Incidencia — Reprogramar" es lenguaje de operaciones, no algo que
    se le diga a alguien que solo quiere saber cuándo van a su casa.

Y cuando Visar cree la etapa número seis, la configura en su pantalla en vez de
pedir un despliegue. Es la misma decisión que `visar.agent.vocabulario`.

Lo lee `visar.agent.tools`: `_agent_service_bucket` y `_agent_service_status`.
"""
from odoo import fields, models


class ProjectTaskType(models.Model):
    _inherit = 'project.task.type'

    visar_agent_bucket = fields.Selection(
        selection=[
            ('auto', "Automático"),
            ('upcoming', "Pendiente"),
            ('history', "Terminado"),
        ],
        string="Para el agente", default='auto', required=True,
        help="Cómo trata el agente de WhatsApp un servicio en esta etapa.\n"
             "• Automático: como hasta ahora — cuenta como terminado si la "
             "etapa está marcada como cerrada, o si la fecha ya pasó.\n"
             "• Pendiente: aparece en «tus próximos servicios» aunque la etapa "
             "esté cerrada. Es el caso de una incidencia por reprogramar.\n"
             "• Terminado: aparece en «tus servicios anteriores».")

    visar_agent_label = fields.Char(
        string="Cómo decírselo al cliente",
        help="El estado que lee el cliente por WhatsApp. Vacío = el nombre de "
             "la etapa. Sirve para no mandarle lenguaje interno: la etapa puede "
             "llamarse «Incidencia — Reprogramar» y el cliente leer «Pendiente "
             "de reprogramar».")
