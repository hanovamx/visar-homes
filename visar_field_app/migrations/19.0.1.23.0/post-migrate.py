# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.23.0, re-siembra las plantillas para aplicar el catálogo
de especies que dictó Visar (11-ago-2026) en "Fumigación interior o exterior
(App v2)":

- **Rastreros** (10): Cucaracha alemana (chica) / Cucaracha americana (grande) /
  Araña / Garrapata / Larva (gusano) / Hormiga / Pescado de plata / Alacrán /
  Cochinilla / Tijerillas.
- **Voladores** (6): Mosca de hogar / Mosca de fruta / Mosca de drenaje / Zancudo /
  Polilla / Avispa y avispón.
- **Polilla** pasa de "Otras plagas" a "Voladores" (la lista de Visar la puso ahí);
  "Otras plagas" se queda con Termitas y Chinches de cama.

⚠️ Los catálogos de especies pasan a sembrarse con `prune=True`, así que las
etiquetas viejas (Cucarachas / Alacranes / Hormigas / Arañas / Moscas / Mosquitos o
zancudos, y la Polilla de "Otras plagas") **se borran**. Borrarlas solo quita las
filas de relación de los m2m que ya las tenían capturadas: no borra líneas ni hojas,
pero **una hoja vieja pierde en silencio la especie** que registró. No es reversible
y no hay mapeo automático posible ("Cucarachas" no sabe si era la alemana o la
americana). Se acepta porque los datos actuales son de PRUEBA; si en la BD hubiera
hojas reales, hay que exportar esas relaciones antes de correr esta migración.

`post_init_hook` solo corre en instalación limpia; esta migración cubre la BD donde
el módulo YA está instalado (QA y producción). Idempotente (re-siembra las tres)."""
from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)
