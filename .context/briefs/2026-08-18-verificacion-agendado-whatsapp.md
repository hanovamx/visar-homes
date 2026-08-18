# Encargo: verificar en el servidor el slice de agendado por WhatsApp

> **Para un agente con acceso al servidor de Odoo.** Escrito 18-ago-2026.
> Contexto de diseño en [`../33-whatsapp-agendado-design.md`](../33-whatsapp-agendado-design.md)
> (leer al menos §6, §7 y §10.1). No hace falta más contexto que este archivo.

## Qué hay que verificar

Dos commits en `main` del repo de módulos (`/opt/custom`). Están **escritos y
compilando, pero NUNCA se han corrido contra una base de datos.** El encargo es
correrlos y reportar qué se rompe.

| Commit | Qué trae |
|---|---|
| `9e606c9` | Apartado de horario (`visar.slot.hold`) + RPCs de agendado + **refactor del camino de reserva del web** |
| `3b6838f` | Guardia defensiva en `visar_base` contra tramos cruzados |

**No "arregles" nada en silencio: quiero el diagnóstico, no un parche.**

## Reglas de trabajo

- **Haz `git -C /opt/custom pull` primero** y confirma que tienes los dos commits.
- **Las escrituras van a una copia**, nunca a `visar-db`:
  `createdb visar-scratch && pg_dump visar-db | psql -q -d visar-scratch`,
  con el filestore copiado por hardlink.
- En `odoo shell`, **nunca `commit()`** — trabaja con `savepoint` + `rollback`.
- **No modifiques código de los módulos.** Si algo falla, reporta el traceback tal cual.
- `visar_appointment` quedó en **19.0.2.6.0** y **necesita `-u`** (modelo, ACL y cron
  nuevos). `visar_whatsapp_agent` y `visar_base` son solo Python: basta reiniciar.
- La base se llama **`visar-db`**. `visar_prod` **no existe** (varios docs viejos lo
  dicen mal). Los módulos viven en **`/opt/custom`**.

---

## Tarea 1 — Correr las pruebas (lo primero)

```bash
sudo -u odoo /opt/odoo/venv/bin/odoo -c /tmp/odoo-test.conf -d visar-scratch \
    -u visar_appointment,visar_base,visar_whatsapp_agent --test-enable \
    --test-tags=/visar_appointment,/visar_base,/visar_whatsapp_agent,/visar_subscription \
    --stop-after-init --http-port=8199 --gevent-port=8299 --log-level=test
```

Pruebas nuevas: `test_slot_hold.py` (11 casos), `test_agent_handoff.py` (5),
`test_agent_prepare_booking.py` (guardias siempre; la paridad de precio **se salta**
si la BD no trae catálogo real) y `test_combined_variant_guard.py` (3).

- **T1a — El resultado más importante son las pruebas VIEJAS.**
  `test_booking_partner`, `test_partner_dedupe` y `test_poliza` tienen que pasar
  **sin tocarlas**: son la red de seguridad de un refactor que movió lógica viva del
  camino de reserva del web (`_visar_apply_delivery_address`, el armado del carrito y
  la creación de `calendar.booking`) del controlador a los modelos.
  **Si alguna de esas tres falla, párate y repórtalo**: es una regresión en el flujo
  web que cobra, y pesa más que todo lo demás de este encargo.
- **T1b —** Reporta cuáles de las pruebas nuevas **se saltaron** y por qué. Un skip no
  es un pase; las aserciones de paridad de precio son las que de verdad importan.

## Tarea 2 — Que el wizard web siga funcionando

El refactor tocó el camino vivo. Reserva de punta a punta **por el sitio web** en la
instancia de scratch (fumigación **interior + exterior**, para ejercitar la variante
combinada) y confirma:

- **T2a —** El carrito muestra **UNA** línea combinada, no dos, y el total coincide
  con `_visar_quote_booking`. Compara contra **`amount_total`**: los precios llevan
  **IVA incluido**, así que contra `amount_untaxed` no cuadra (es la confusión
  `price_total` / `price_subtotal` que ya mordió antes).
- **T2b —** El descuento de combo sigue llegando a la línea guardada.
- **T2c —** Se crea el contacto de entrega y queda como dirección de servicio.
- **T2d —** Una reserva con **póliza** sigue generando las líneas de mensualidad
  adelantada (`anticipo`).

## Tarea 3 — La superficie RPC nueva, de punta a punta en shell

Sobre `visar-scratch`, como usuario RPC `whatsapp_agent` donde se pueda:

1. **T3a —** `agent_available_days({selections, cp, mode})` → devuelve días y
   `min_hours`. Confirma que `min_hours` es **24** (no se puede reservar para hoy).
2. **T3b —** `agent_day_slots({..., date})` → horarios con `start`/`stop`/`resource_ids`.
3. **T3c —** `agent_prepare_booking({phone, name, selections, cp, delivery_address,
   slot})` → espera `prepared: True`, `payment_url` **absoluta**, `expire_at`, y un
   `total` igual al de `_visar_quote_booking`.
4. **T3d —** `curl` anónimo a esa URL → **200** con el importe renderizado.
5. **T3e — El apartado funciona (invariante central).** Vuelve a llamar
   `agent_day_slots` con **otro** teléfono: el horario apartado **no** debe aparecer.
   Con el **mismo** teléfono, **sí** debe aparecer (quien apartó tiene que poder
   reservar). Si esto falla, dilo fuerte.
6. **T3f —** Paga con el proveedor **Demo** → se crea `calendar.event`, aparece la
   tarea FSM, y la fila de `visar.slot.hold` **desaparece**.
7. **T3g —** Deja vencer un apartado (o retrasa `expire_at` a mano) → el horario
   vuelve a ofrecerse **sin** correr el cron.
8. **T3h —** `agent_request_handoff({phone, reason, context})` → lead creado o
   reusado, nota de chatter con el contexto, y actividad agendada.

## Tarea 4 — Lo que sospecho que puede estar mal

Revisa esto en concreto; son mis mejores apuestas de dónde está el error:

- **T4a —** `sale.order.create({website_id, partner_id})` seguido de `_cart_add`:
  ¿se comporta como un carrito de verdad? ¿Falta `pricelist_id`, compañía o posición
  fiscal?
- **T4b —** `payment.link.wizard` con `amount = order.amount_total`: ¿computa `link`
  sin `request`? (Hay guardia para `amount_total <= 0`.)
- **T4c —** El override de capacidad corre **dentro de la generación de slots**, una
  vez por slot. Cronometra `agent_available_days` y la página de calendario del web.
  Si va lento, di **cuánto**: una implementación lenta degrada el **wizard web**, no
  solo WhatsApp.
- **T4d —** `_visar_selections_has_roedores` se movió al modelo y el controlador ahora
  delega. Confirma que una reserva donde el cliente respondió **"no"** a roedores
  **NO** trae add-ons de roedores. (El valor guardado es `'si'`/`'no'` y **ambos son
  truthy**: comparar por verdad booleana los añadiría a todas.)

---

## Cómo reportar

Por cada punto: qué corriste, qué obtuviste, y un veredicto de una línea —
**confirmado / refutado / no se pudo probar (por qué)**. Tracebacks tal cual.
Señala cualquier cosa rara aunque no la haya preguntado. El output crudo gana sobre
el resumen cuando difieran.

**Si solo haces dos cosas:** **T1a** (las pruebas viejas siguen verdes) y **T3e** (el
apartado de verdad esconde el horario).
