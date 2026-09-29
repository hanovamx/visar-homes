# -*- coding: utf-8 -*-
"""Siembra el vocabulario de fabrica como filas reales de `visar.agent.vocabulario`.

Los diccionarios del codigo dejaron de ser un suelo invisible: ahora la fila
manda (ver `_visar_vocabulario`). Para que eso no cambie NADA el dia del
despliegue, hay que sembrar cada ranura con exactamente lo que el codigo ya
decia —y eso es lo que hace esto—.

**Es un no-op de comportamiento.** Antes: codigo + filas (y en produccion habia
CERO filas, comprobado el 29-sep-2026, asi que era solo el codigo). Despues: las
filas, que contienen justamente lo que el codigo decia. El agente reconoce las
mismas palabras el minuto antes y el minuto despues.

Lo que cambia es que por primera vez se pueden LEER desde Odoo, y por lo tanto
quitar una que clasifique mal sin esperar un despliegue.

Las ranuras que ya tuvieran fila NO se tocan: en produccion no hay ninguna, pero
en una base de prueba con vocabulario a mano lo que escribio una persona vale
mas que la semilla.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID
    from odoo.addons.visar_appointment.models.appointment_wizard_flow import (
        _VISAR_VOCABULARIO_BASE,
    )

    env = api.Environment(cr, SUPERUSER_ID, {})
    Vocab = env['visar.agent.vocabulario'].sudo()

    # Una sola lectura: con 17 ranuras no compensa nada mas listo, y asi la
    # comprobacion de "ya existe" no depende del orden de creacion.
    existentes = {
        (fila['paso'], fila['opcion'])
        for fila in Vocab.with_context(active_test=False).search_read(
            [], ['paso', 'opcion'])
    }

    por_crear = []
    for paso, opciones in _VISAR_VOCABULARIO_BASE.items():
        for opcion, palabras in (opciones or {}).items():
            if (paso, opcion) in existentes:
                _logger.info(
                    "visar.agent.vocabulario: %s/%s ya tenia fila, no se toca",
                    paso, opcion)
                continue
            por_crear.append({
                'paso': paso,
                'opcion': opcion,
                'palabras': '\n'.join(palabras or ()),
            })

    if not por_crear:
        _logger.info("visar.agent.vocabulario: no habia nada que sembrar")
        return

    Vocab.create(por_crear)
    _logger.info(
        "visar.agent.vocabulario: sembradas %d ranuras de fabrica (%s)",
        len(por_crear),
        ", ".join(sorted({v['paso'] for v in por_crear})))
