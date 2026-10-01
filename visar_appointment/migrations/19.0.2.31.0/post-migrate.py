# -*- coding: utf-8 -*-
"""Rellena `visar_visit_event_id` donde se puede DEMOSTRAR, y le crea cita a las
visitas de poliza que ya tenian fecha.

Son dos faenas distintas y conviene no confundirlas:

1. **Relleno historico del enlace.** El campo nuevo nace vacio en toda la base.
   Se enlaza solo donde no hay ambiguedad posible: una tarea cuya cadena de
   lineas resuelve EXACTAMENTE una cita. Mismo criterio conservador que la
   migracion 19.0.2.30.0 de `visar_appointment`: enlazar por adivinanza seria
   peor que no enlazar, porque `_visar_ruta_cita` y el reagendado empezarian a
   confiar en un dato inventado.

2. **Las visitas de poliza FUTURAS que ya tenian fecha.** Se les crea su cita
   real para que OCUPEN la agenda del tecnico: hasta ahora tenian
   `planned_date_begin` pero ninguna `calendar.event`, asi que no consumian
   capacidad y la web podia vender el mismo hueco a otro cliente.

   **Solo las futuras, y eso no es un detalle.** Una cita para una visita que paso
   en julio no protege ninguna capacidad —el dia ya se gasto— y en cambio ensucia
   el calendario del tecnico con horas que ya no existen. En `visar-test` las 27
   candidatas estaban TODAS en el pasado, asi que ahi esta migracion es un no-op:
   si en produccion sale lo mismo, es la respuesta correcta, no un fallo.

   **Si el hueco choca con algo ya reservado, NO se toca la visita**: queda una
   actividad para oficina. Nunca se mueve una visita sin que una persona lo
   decida — el cliente ya tiene esa fecha, y cambiarsela por detras durante un
   `-u` seria lo peor que podria hacer esta migracion.

   Estas visitas NO se marcan como `visar_visit_preagendada`: su fecha no la puso
   el robot a partir de una propuesta, la puso alguien. Marcarlas haria que la
   serie anclara en una propuesta que quizas ni existe.

**Vive en `visar_appointment` y no en `visar_subscription`, aunque los campos sean
de los otros dos.** Las migraciones corren en orden de dependencias, asi que
`visar_fsm` y `visar_subscription` se actualizan ANTES: desde ahi,
`_visar_preagenda_cita_para_fecha_existente` todavia no existe y el `-u` moriria
con AttributeError. Aqui ya estan las tres piezas: el campo (`visar_fsm`), las
banderas (`visar_subscription`) y el motor (este modulo).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    _rellenar_enlace(env, cr)
    _citas_para_las_ya_fechadas(env)


def _rellenar_enlace(env, cr):
    """Enlaza tarea -> cita donde la cadena de lineas resuelve UNA sola cita."""
    cr.execute("""
        SELECT sol.task_id, MIN(sol.calendar_event_id), COUNT(DISTINCT sol.calendar_event_id)
          FROM sale_order_line sol
          JOIN project_task t ON t.id = sol.task_id
         WHERE sol.task_id IS NOT NULL
           AND sol.calendar_event_id IS NOT NULL
           AND t.visar_visit_event_id IS NULL
         GROUP BY sol.task_id
        HAVING COUNT(DISTINCT sol.calendar_event_id) = 1
    """)
    filas = cr.fetchall()
    if not filas:
        _logger.info("Pre-agenda: no habia ninguna tarea con cita unica que enlazar")
        return
    # SQL directo a proposito: son cientos de tareas y el ORM disparar�a el
    # recalculo de series de poliza y los avisos de ruta por cada una, durante el
    # `-u`. El campo es un m2o simple y no tiene computes colgando.
    cr.executemany(
        "UPDATE project_task SET visar_visit_event_id = %s WHERE id = %s",
        [(event_id, task_id) for task_id, event_id, _n in filas])
    _logger.info("Pre-agenda: enlazadas %s tareas con su cita (cita unica)", len(filas))


def _citas_para_las_ya_fechadas(env):
    """Las visitas de poliza CON fecha y SIN cita: se les crea la suya."""
    Task = env['project.task'].sudo()
    from odoo import fields

    visitas = Task.search([
        ('visar_subscription_order_id', '!=', False),
        ('visar_visit_kind', '=', 'preventiva'),
        # Solo las FUTURAS: una cita para una visita que ya paso no protege nada.
        ('planned_date_begin', '>', fields.Datetime.now()),
        ('visar_visit_event_id', '=', False),
        ('state', 'not in', ('1_canceled', '1_done')),
    ])
    if not visitas:
        _logger.info(
            "Pre-agenda: ninguna visita de poliza FUTURA fechada sin cita "
            "(las pasadas se dejan como estan a proposito)")
        return

    creadas = choques = fallos = 0
    for visita in visitas:
        try:
            with env.cr.savepoint():
                resultado = visita._visar_preagenda_cita_para_fecha_existente()
            if resultado is True:
                creadas += 1
            elif resultado is False:
                choques += 1
        except Exception:  # noqa: BLE001 - una visita no tumba el -u
            fallos += 1
            _logger.exception(
                "Pre-agenda: no se pudo crear la cita de la visita %s", visita.id)
    _logger.info(
        "Pre-agenda (visitas ya fechadas): %s con cita nueva, %s con el hueco "
        "ocupado (actividad para oficina), %s con error, de %s en total",
        creadas, choques, fallos, len(visitas))
