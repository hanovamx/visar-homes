# -*- coding: utf-8 -*-
"""Deja UN solo prompt por ruta, archivando los eclipsados.

`_check_ruta_unica` cierra el catalogo, pero una restriccion nueva no valida lo
que ya estaba: produccion llevaba DOS prompts base -el sembrado por el modulo
(17 485 caracteres, `sequence` 2) y uno que alguien escribio despues («Prompt
Updated», 38 631, `sequence` 1)-. Manda el segundo desde que existe, asi que el
primero no se aplica desde entonces.

Se ARCHIVA, no se borra, y el criterio es el del runtime -`sequence, id`, el
mismo de `_agent_route_body`-, asi que el que sobrevive es exactamente el que ya
se estaba usando: **para el agente esta migracion no cambia absolutamente nada**.
Lo unico que cambia es que deja de haber un registro que parece editable y no
hace nada.

Se archiva con SQL y no por ORM a proposito: por ORM, `write({'active': False})`
dispara `_check_ruta_unica` sobre los hermanos y la migracion se caeria validando
justo el estado que viene a arreglar.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # NULL = prompt base. `IS NOT DISTINCT FROM` agrupa los NULL entre si, que es
    # justo lo que un `=` no hace y lo que convierte esto en un no-op silencioso.
    cr.execute("""
        SELECT id, ruta, sequence, name, length(body) AS largo, active
          FROM visar_agent_prompt
         WHERE active
         ORDER BY ruta NULLS FIRST, sequence, id
    """)
    filas = cr.dictfetchall()

    vistos = {}
    sobran = []
    for fila in filas:
        clave = fila['ruta']  # None = base
        if clave in vistos:
            sobran.append(fila)
        else:
            vistos[clave] = fila

    for clave, ganador in sorted(vistos.items(), key=lambda kv: kv[0] or ''):
        _logger.info(
            "visar.agent.prompt: %s -> id=%s «%s» (%s caracteres) SE QUEDA",
            clave or 'PROMPT BASE', ganador['id'], ganador['name'],
            ganador['largo'])

    if not sobran:
        _logger.info("visar.agent.prompt: no habia duplicados que archivar")
        return

    for fila in sobran:
        _logger.warning(
            "visar.agent.prompt: %s -> id=%s «%s» (%s caracteres) SE ARCHIVA; "
            "ya estaba eclipsado por el de menor secuencia, asi que el agente "
            "no lo leia",
            fila['ruta'] or 'PROMPT BASE', fila['id'], fila['name'],
            fila['largo'])

    cr.execute(
        "UPDATE visar_agent_prompt SET active = FALSE WHERE id IN %s",
        (tuple(f['id'] for f in sobran),))
