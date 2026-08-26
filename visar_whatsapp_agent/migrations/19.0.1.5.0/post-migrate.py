# -*- coding: utf-8 -*-
"""Deja constancia de que prompt queda como BASE tras anadir `ruta`.

NO ESCRIBE NADA, a proposito. Con `ruta` vacia = base, el ALTER TABLE del -u ya
deja el registro de produccion (~20 000 caracteres) exactamente donde debe estar:
la columna nace en NULL y NULL ES base. Cualquier UPDATE aqui solo podria
empeorarlo — la fila que se dejara sin tocar no seria ni base ni ruta, o sea
invisible para los dos lectores, o sea el agente sin prompt en produccion.

Lo unico que falta es que el operador VEA cual quedo, con su longitud: un base de
800 caracteres en la salida del -u es una alarma evidente, y sin este log habria
que deducirlo.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT id, name, sequence, active, ruta, COALESCE(LENGTH(body), 0)
          FROM visar_agent_prompt
         ORDER BY sequence, id
    """)
    filas = cr.fetchall()

    if not filas:
        _logger.warning(
            "visar.agent.prompt: no hay ningun prompt. El runtime seguira "
            "usando su BASE_PROMPT de respaldo (app/prompts.py).")
        return

    bases = [f for f in filas if f[4] is None and f[3]]
    if not bases:
        _logger.warning(
            "visar.agent.prompt: ningun registro ACTIVO sin ruta -> el runtime "
            "se queda SIN prompt base y cae al de respaldo. Revisar.")
        return

    vigente = bases[0]
    _logger.info(
        "visar.agent.prompt: base = id=%s '%s' (secuencia %s, %s caracteres).",
        vigente[0], vigente[1], vigente[2], vigente[5])
    if len(bases) > 1:
        _logger.warning(
            "visar.agent.prompt: %s registros activos sin ruta; el runtime solo "
            "lee el primero. Los demas: %s",
            len(bases), [(f[0], f[1]) for f in bases[1:]])
