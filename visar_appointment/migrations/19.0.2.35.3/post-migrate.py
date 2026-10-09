# -*- coding: utf-8 -*-
"""Lleva dos cambios de texto a las COPIAS POR WEBSITE de las plantillas.

El paso de póliza y el formulario de dirección tienen copia por website (se
crean al editar la página desde el editor del sitio) y la que se sirve es la
copia. El `-u` no las deja al día de forma fiable: la del formulario de dirección
ya tiene texto editado a mano por Visar, y una frase retocada puede quedarse con
su traducción vieja. Aquí se reemplazan las frases exactas, en todos los idiomas
de cada copia, sin tocar nada más de lo que Visar haya escrito.

1. "póliza" pasa a "suscripción" en el paso de póliza.
2. El formulario de dirección ya no dice el nombre de la zona (A, B, C): solo
   "Zona Cubierta".
"""
import json
import logging

_logger = logging.getLogger(__name__)

CAMBIOS = {
    'visar_appointment.visar_wizard_poliza': [
        ("¿Prefieres contratarlo como póliza?", "¿Prefieres contratarlo como suscripción?"),
        ("Con una póliza tu servicio", "Con una suscripción tu servicio"),
        ("Solo este servicio, sin póliza", "Solo este servicio, sin suscripción"),
        ("Total sin póliza", "Total sin suscripción"),
    ],
    'visar_appointment.visar_delivery_address_fields': [
        ("'Zona detectada: ' + (data.zone_name || '')", "'Zona Cubierta'"),
    ],
}


def migrate(cr, version):
    for key, cambios in CAMBIOS.items():
        cr.execute("SELECT id, arch_db FROM ir_ui_view WHERE key = %s", (key,))
        for view_id, arch in cr.fetchall():
            nuevo = {}
            for lang, texto in (arch or {}).items():
                for viejo, bueno in cambios:
                    texto = texto.replace(viejo, bueno)
                nuevo[lang] = texto
            if nuevo != arch:
                cr.execute("UPDATE ir_ui_view SET arch_db = %s WHERE id = %s",
                           (json.dumps(nuevo), view_id))
                _logger.info("Textos actualizados en la vista %s (%s)", view_id, key)
