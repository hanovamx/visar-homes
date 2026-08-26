# -*- coding: utf-8 -*-
"""Siembra `m2_min`/`m2_max` de las bandas de exterior desde su etiqueta.

Los limites siempre estuvieron ahi —la etiqueta dice "101 – 150 m²"— pero solo
como texto para leer. Ahora hacen falta como numeros, para que el paso se pueda
contestar escribiendo los metros en vez de solo con el numero de la fila.

**Se parsea la etiqueta UNA vez, aqui, y nunca en caliente.** Un consultor puede
reescribir el nombre de una banda cualquier dia; si el flujo dependiera de
parsearlo, ese dia se rompe en silencio y en un paso de precio. De aqui en
adelante los limites son campos que se editan a mano.

Lo que no se entienda se deja VACIO a proposito: esa banda se sigue eligiendo por
su numero, que es exactamente como funcionaba hasta ahora. Degradar, nunca
bloquear.
"""
import logging
import re

_logger = logging.getLogger(__name__)

# "0 – 50 m²", "101 - 150 m2", "151–200"  -> (min, max)
_RANGO = re.compile(r"(\d[\d\s,]*)\s*[-–—]\s*(\d[\d\s,]*)")
# "Más de 500 m²"  -> (500, abierto). El limite inferior REAL es 501; el texto
# dice "mas de 500", asi que 500 exacto pertenece a la banda anterior.
_ABIERTO = re.compile(r"m[aá]s\s+de\s+(\d[\d\s,]*)", re.IGNORECASE)


def _numero(bruto):
    limpio = re.sub(r"[^\d]", "", bruto or "")
    return int(limpio) if limpio else None


def migrate(cr, version):
    if not version:
        return
    bandas = env_bandas(cr)
    sembradas, sin_leer = 0, []
    for band_id, etiqueta in bandas:
        minimo = maximo = None
        rango = _RANGO.search(etiqueta or "")
        if rango:
            minimo, maximo = _numero(rango.group(1)), _numero(rango.group(2))
        else:
            abierto = _ABIERTO.search(etiqueta or "")
            if abierto:
                tope = _numero(abierto.group(1))
                # Sin maximo: es la ultima banda y captura de ahi para arriba.
                minimo, maximo = (tope + 1 if tope is not None else None), None
        if minimo is None:
            sin_leer.append((band_id, etiqueta))
            continue
        cr.execute(
            "UPDATE visar_measure_band SET m2_min = %s, m2_max = %s WHERE id = %s",
            (minimo, maximo, band_id))
        sembradas += 1
    _logger.info("visar.measure.band: %s bandas con limites sembrados.", sembradas)
    if sin_leer:
        # No es un fallo: esas bandas siguen eligiendose por su numero de fila.
        _logger.warning(
            "visar.measure.band: no se pudieron leer los limites de %s; se "
            "quedan vacias y solo se eligen por numero: %s",
            len(sin_leer), sin_leer)


def env_bandas(cr):
    """(id, etiqueta) de cada banda. El nombre es jsonb traducible en Odoo 19."""
    cr.execute("SELECT id, name FROM visar_measure_band ORDER BY id")
    filas = []
    for band_id, name in cr.fetchall():
        if isinstance(name, dict):
            etiqueta = name.get("en_US") or next(iter(name.values()), "")
        else:
            etiqueta = name or ""
        filas.append((band_id, etiqueta))
    return filas
