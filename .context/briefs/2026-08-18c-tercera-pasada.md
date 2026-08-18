# Encargo (ronda 3): confirmar cinco correcciones y re-medir

> **Corto.** Los tres fallos grandes ya quedaron cerrados en la ronda 2. Esto es
> solo lo que salió de tu propio reporte. Contexto:
> [`33-whatsapp-agendado-design.md`](../33-whatsapp-agendado-design.md) §10.3.

Commit nuevo en `main` de `/opt/custom`. **Módulos a actualizar:
`visar_appointment`, `visar_base`, `visar_crm`, `visar_whatsapp_agent`.**
Recrea la copia desde cero. Reglas de siempre: escrituras solo sobre la copia,
nunca `commit()` en shell, no modifiques código.

## Qué cambió (todo salió de tu reporte de la ronda 2)

1. La prueba de T3f ahora manda `product_id` (era NOT NULL y reventaba antes de
   la aserción).
2. `_visar_filter_slots_multi_service` también toma foto de apartados: ahí se iban
   las otras ~221 consultas.
3. `agent_hold_slot` comprueba disponibilidad antes de apartar.
4. El hand-off nunca asigna la actividad al usuario RPC ni a usuarios *share*.
5. El `try/except` del hand-off ahora envuelve también `message_post` y
   `activity_schedule`.

## Qué verificar

- **V1 —** Corre las pruebas. `test_una_reserva_no_compite_contra_su_propio_apartado`
  tiene que **ejecutarse y pasar** esta vez (antes moría en el `create`). Nuevas:
  `test_dos_clientes_no_pueden_apartar_el_mismo_horario` y
  `test_la_actividad_no_se_asigna_al_propio_bot`. `test_booking_partner` y
  `test_poliza` siguen verdes. Los 2 de `test_partner_dedupe` siguen igual (no los
  investigues).
- **V2 — la medición que importa.** Repite R3a con los tres builds. Ronda 2 dio
  baseline 1.95 s / con apartados 2.56 s (**+31%**). ¿Cuánto queda ahora? Y repite
  el conteo de tu reporte: cuántas veces `_visar_used_capacity` se resuelve con
  foto vs. contra la base en un `agent_available_days` completo. Esperado: cerca
  de cero contra la base.
- **V3 —** `agent_hold_slot` dos veces sobre el mismo horario con teléfonos
  distintos: el segundo debe devolver `held: False`, `reason: 'slot_taken'`, y el
  **primero** debe seguir viendo su horario.
- **V4 —** `agent_request_handoff` con teléfono nuevo: mira a quién queda asignada
  la actividad. Con el equipo de WhatsApp **sin miembros** lo correcto es que
  **no** se agende ninguna (`activity_scheduled: false`) y quede un `warning` en el
  log — no que se asigne al bot. Si le pones un líder al equipo, entonces sí debe
  agendarse a ese humano; pruébalo si puedes.
- **V5 —** Que nada de esto rompió la generación de slots ni el wizard web
  (una carga de calendario y una reserva web completa bastan).

## Reportar

Como siempre: qué corriste, qué obtuviste, veredicto de una línea. **Lo más
importante es V2** — es lo único que quedó a medias de la ronda 2.

## Fuera de alcance

I-11 (el web cobra 2,400 donde la cotización dice 1,900). Documentado, decisión
aparte, no lo toques.
