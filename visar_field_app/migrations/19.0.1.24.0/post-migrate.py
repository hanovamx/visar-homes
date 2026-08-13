# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.24.0, siembra la plantilla del COMBO.

Cuando una misma cita trae fumigación **y** mantenimiento de áreas verdes,
`visar_fsm` ahora genera UN solo servicio externo en el proyecto "Servicios
combinados". Esa tarea necesita una hoja de trabajo que traiga los dos juegos de
campos: es la plantilla "Fumigación + Mantenimiento de áreas verdes (App v2)" que
se siembra aquí, con sus dos modelos de línea propios
(`x_visar_area_tratada_combo`, `x_visar_labor_combo`).

También apunta el proyecto "Servicios combinados" a esa plantilla
(`wire_combined_project`). Es una excepción deliberada a "la asignación de
plantilla no se automatiza": ese proyecto lo crea el código y Odoo le pone la
plantilla nativa genérica al nacer, así que sin esto el técnico abriría el combo
con una hoja de un solo campo.

Re-siembra además las tres plantillas existentes (idempotente). El arch de
Fumigación y Áreas verdes se reescribe desde los MISMOS fragmentos de página que
usa el combo; se verificó que el resultado es XML idéntico al anterior, así que
las hojas ya capturadas no cambian.

⚠️ Corre después del post-migrate de `visar_fsm` (dependencia de módulo), que es
quien crea el proyecto anfitrión.
"""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)
