# Encargo (ronda 2): re-verificar las correcciones del agendado por WhatsApp

> **Para un agente con acceso al servidor de Odoo.** Escrito 18-ago-2026, después
> de la primera verificación. Es un encargo **acotado**: solo lo que cambió y lo
> que fallaba. No hace falta repetir la ronda 1 completa.
>
> Contexto: [`33-whatsapp-agendado-design.md`](../33-whatsapp-agendado-design.md)
> §10.2 (resultado de la ronda 1 y qué se corrigió). Encargo anterior:
> [`2026-08-18-verificacion-agendado-whatsapp.md`](./2026-08-18-verificacion-agendado-whatsapp.md).

## Qué cambió

Commit nuevo en `main` de `/opt/custom`: **`eea8b52`**. Corrige los tres fallos que
encontró la ronda 1, más dos molestias medidas.

| Ref | Fallaba | Arreglo a verificar |
|---|---|---|
| **T3f** | Se pagaba y **no** se creaba la cita (el apartado del propio cliente lo dejaba sin cupo al pagar) | Override de `_filter_unavailable_bookings` en `calendar.booking` |
| **T3h** | `agent_request_handoff` **lanzaba** con lead nuevo (`visar_source` no aceptaba el valor) | Valor añadido a la Selection + `try/except` |
| **T3e** | El dueño no veía **su propio** horario apartado en el listado | El teléfono siembra `visar_hold_owner` en `_agent_slot_tree` |
| **T4c** | El calendario tardaba ~1 s más (+57%) | `_get_appointment_slots` precarga una foto de apartados |
| — | `UserWarning` por slot (recordset vs cadena) | Se filtra por tipo |

## Reglas de trabajo (iguales que la ronda 1)

- `git -C /opt/custom pull`; confirma que tienes **`eea8b52`**.
- **⚠️ Ahora `visar_crm` TAMBIÉN necesita `-u`** (se le añadió un valor de
  Selection a `visar_source`). Va junto con `visar_appointment`.
- **Recrea la copia desde cero** (`dropdb visar-scratch` y volver a dumpear): la de
  ayer tiene el esquema viejo. Si dejaste `visar-scratch-parent`, bórrala también.
- Escrituras **solo** sobre la copia, nunca `visar-db`. En `odoo shell`, **nunca
  `commit()`** — `savepoint` + `rollback`.
- **No modifiques código de los módulos.** Traceback tal cual si algo falla.
- La base es **`visar-db`** (`visar_prod` no existe). Módulos en **`/opt/custom`**.

## R1 — Pruebas

```bash
sudo -u odoo /opt/odoo/venv/bin/odoo -c /tmp/odoo-test.conf -d visar-scratch \
    -u visar_appointment,visar_base,visar_crm,visar_whatsapp_agent --test-enable \
    --test-tags=/visar_appointment,/visar_base,/visar_crm,/visar_whatsapp_agent,/visar_subscription \
    --stop-after-init --http-port=8199 --gevent-port=8299 --log-level=test
```

- **R1a —** `test_booking_partner` y `test_poliza` siguen verdes (red de seguridad
  del refactor). Si alguna cae, **párate y repórtalo**.
- **R1b —** Pruebas de regresión nuevas, todas deben pasar:
  `test_una_reserva_no_compite_contra_su_propio_apartado`,
  `test_las_dos_rutas_de_capacidad_coinciden`,
  `test_lead_nuevo_registra_el_origen_escalado`.
- **R1c —** `test_interior_mas_exterior_es_una_linea_combinada` ahora filtra
  `is_free = False`. ¿Pasa, o sigue fallando por otra razón?
- **R1d —** Reporta skips. Un skip no es un pase.
- **Ignorado a propósito:** los 2 fallos de `test_partner_dedupe` por `assertLogs`
  son preexistentes y del harness (el logger queda en nivel 25). Confírmalos como
  "siguen igual" y sigue; no los investigues otra vez.

## R2 — Los tres fallos, uno por uno (lo importante)

Repite exactamente las pruebas que los destaparon:

- **R2a (T3f) — la crítica.** Flujo completo: `agent_prepare_booking` → pagar con
  el proveedor **Demo** → confirma que **AHORA SÍ** existe `calendar.event`, que
  `booking.not_available` es `False`, que aparece la tarea FSM y que **el apartado
  se libera**. Ayer daba `calendar_event_id = None` con el cobro adentro.
- **R2b (T3h).** `agent_request_handoff` con un teléfono **que no exista** (lead
  nuevo, que es el caso que reventaba) → sin excepción, lead creado,
  `visar_source = 'whatsapp_handoff'`, nota de chatter con el contexto y actividad
  agendada. Prueba también un `reason` inventado: debe caer en "Otro motivo" sin
  romperse.
- **R2c (T3e).** Aparta un horario y vuelve a llamar `agent_day_slots` **pasando el
  teléfono del dueño**: el horario **sí** debe aparecer. Con otro teléfono, **no**.

## R3 — Que el arreglo de rendimiento sirvió

Repite la medición de ayer (misma página, mismos datos, varias corridas):

- **R3a —** `/appointment/13` con `eea8b52` vs. el padre `3b6838f`. Ayer eran
  2.69 s vs 1.72 s (**+0.97 s, +57%**). Di cuánto es ahora.
- **R3b —** Cronometra también `agent_available_days`.
- **R3c —** Confirma que el `UserWarning` de `appointment_type.py` **ya no aparece**
  en el log durante la generación de slots.

## R4 — Dónde sospecho que puede haberse roto algo nuevo

Los arreglos tocan caminos calientes; estas son mis apuestas:

- **R4a —** El override nuevo de `_get_appointment_slots` tiene que respetar la
  firma nativa (`timezone, filter_users, filter_resources, asked_capacity,
  reference_date`). Si no coincide, **la generación de slots se rompe entera** —
  tanto en el web como en el agente. Que el calendario del wizard siga pintando.
- **R4b —** El override de `_filter_unavailable_bookings` llama
  `super(CalendarBooking, records)` con un recordset re-contextualizado. Verifica
  que sigue detectando de verdad la indisponibilidad **real**: aparta el horario
  con OTRO cliente (no el de la reserva), paga, y confirma que esa reserva **sí**
  se descarta. No vaya a ser que ahora nunca descarte nada.
- **R4c —** La foto de apartados (`visar_hold_cache`) se toma al inicio de la
  generación. Confirma que un apartado creado **durante** una sesión de navegación
  aparece en la siguiente carga del calendario (que la foto no se quede pegada
  entre peticiones).

## Cómo reportar

Por punto: qué corriste, qué obtuviste, veredicto de una línea (**confirmado /
refutado / no se pudo probar — por qué**). Tracebacks tal cual. Señala cualquier
cosa rara aunque no la pregunte.

**Si solo haces dos cosas:** **R2a** (que ya no se cobre sin cita) y **R4b** (que el
arreglo no haya desactivado la detección de indisponibilidad real).

**No hace falta repetir** de la ronda 1: T2a/T2c/T2d (wizard web), T3a–T3d, T3g,
T4a/T4b/T4d — todos pasaron y ese código no cambió. Si algo de R1 los contradice,
entonces sí, dilo.

## Lo que NO es de este encargo

El sobrecobro del web (I-11: cobra 2,400 donde la cotización dice 1,900, por
perder el descuento de combo en `_update_address`) es **preexistente y del canal
web**. Ya está documentado con causa exacta en
[`90-improvements-later.md`](../90-improvements-later.md). **No lo arregles aquí**
— es una decisión aparte.
