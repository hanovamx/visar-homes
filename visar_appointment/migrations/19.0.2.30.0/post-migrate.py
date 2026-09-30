# -*- coding: utf-8 -*-
"""Borra los avisos de ruta que decian un solapamiento contra la PROPIA cita.

`_visar_ruta_cita()` solo miraba `visar_sale_line_ids`, asi que una visita de
poliza se quedaba sin cita aunque la tuviera -la suya vive en
`visar_source_line_ids`- y su propia franja contaba como parada del dia. El
aviso resultante señalaba al servicio consigo mismo.

El texto ya esta ESCRITO en `visar_ruta_aviso`, y ese campo solo se recalcula al
guardar la tarea. Sin esto, la visita 710 seguiria enseñando el recuadro amarillo
para siempre.

**Se limpia solo donde se puede DEMOSTRAR que era el fallo**, y no recalculando
todo a lo bruto: la tarea tiene que (a) llevar exactamente la linea del
solapamiento y (b) resolver ahora una cita que empieza a su misma hora. Lo
segundo es lo que separa el falso positivo de un solapamiento de verdad —la
visita 663 se movio a mano a una hora donde el tecnico ya tenia otro servicio, su
cita sigue en la hora vieja, y ese aviso es correcto y se queda—.

Recalcular todo tampoco seria inocuo: `_visar_ruta_avisos` necesita Mapbox, y sin
token o con la API caida devuelve lista vacia. Un recalculo a ciegas durante el
`-u` borraria tambien los avisos legitimos.
"""
import logging

_logger = logging.getLogger(__name__)

MARCA = 'ya tiene otro servicio a esa hora'


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    Task = env['project.task'].sudo()

    cr.execute("""
        SELECT id FROM project_task
         WHERE visar_ruta_aviso IS NOT NULL
           AND visar_ruta_aviso LIKE %s
    """, ('%' + MARCA + '%',))
    ids = [fila[0] for fila in cr.fetchall()]
    if not ids:
        _logger.info("visar_ruta_aviso: no habia avisos de solapamiento")
        return

    limpiadas = []
    for task in Task.browse(ids):
        try:
            cita = task._visar_ruta_cita()
        except Exception:  # noqa: BLE001 - un aviso no tumba un `-u`
            _logger.exception(
                "visar_ruta_aviso: no se pudo revisar la tarea %s", task.id)
            continue
        if not cita or not task.planned_date_begin:
            _logger.info(
                "visar_ruta_aviso: tarea %s sin cita propia, se deja el aviso",
                task.id)
            continue
        if cita.start != task.planned_date_begin:
            _logger.info(
                "visar_ruta_aviso: tarea %s conserva su aviso — su cita es de "
                "las %s y la tarea esta a las %s, asi que el solapamiento es "
                "con un servicio AJENO",
                task.id, cita.start, task.planned_date_begin)
            continue
        # Solo la linea del solapamiento: si la tarea tenia ademas un aviso de
        # traslado, ese sigue siendo cierto y no se toca.
        resto = [linea for linea in (task.visar_ruta_aviso or '').splitlines()
                 if MARCA not in linea]
        cr.execute("UPDATE project_task SET visar_ruta_aviso = %s WHERE id = %s",
                   ('\n'.join(resto) or None, task.id))
        limpiadas.append(task.id)
        _logger.info(
            "visar_ruta_aviso: tarea %s limpiada — el solapamiento era contra su "
            "propia cita (evento %s, %s)", task.id, cita.id, cita.start)

    _logger.info("visar_ruta_aviso: %d tarea(s) limpiada(s) de %d revisada(s): %s",
                 len(limpiadas), len(ids), limpiadas or 'ninguna')
