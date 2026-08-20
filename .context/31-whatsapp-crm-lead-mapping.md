# WhatsApp agent → CRM: mapeo de interacciones a etapas de lead (DISEÑO)

> ## ✅ Estado real (20-ago-2026): **IMPLEMENTADO**
>
> `visar_crm` v19.0.1.3.0 existe y está desplegado: `agent_track_lead` crea el lead en *Nuevo*
> (`b6ed4c3`), avance de etapa, *won* y cron de caducidad (`a236ba9`), y el grupo de servicio se
> resuelve por el enlace dimensión → producto (`4b2453b`). El hand-off humano aterriza aquí
> (`agent_request_handoff`, diseño 33 §9.1).
>
> ⛔ **Bloqueado por dato, no por código:** el equipo de CRM de WhatsApp **no tiene líder ni
> miembros**, así que la actividad del hand-off se crea pero **no cae en la bandeja de nadie**.
> Es lo que hoy separa "el agente promete que le contactan" de "alguien le contacta".

> **Estado original: DISEÑO, no implementado.** Escrito 2026-08-04. Decisiones tomadas en
> conversación con dirección/consultor. Define cómo las interacciones del número de
> WhatsApp (agente LLM + rutas deterministas) crean y hacen avanzar **leads de CRM**.
>
> Continúa la línea de [`27-whatsapp-agent.md`](./27-whatsapp-agent.md) (la superficie
> RPC), [`29-whatsapp-agent-routing-design.md`](./29-whatsapp-agent-routing-design.md)
> (enrutamiento por menú) y [`30-whatsapp-agent-routing-implementation.md`](./30-whatsapp-agent-routing-implementation.md).
> El runtime vive en el repo aparte `visar_fastapi` (su lado se resume abajo).
>
> **Cruza deliberadamente la regla de solo-lectura.** Hasta hoy el runtime **no
> escribe** en Odoo (doc 29 reservaba el "primer write" para insights). Este diseño
> introduce **un** método RPC de escritura **acotado** (`agent_track_lead`), con el
> mismo principio: no se amplía el ACL general, se expone una sola operación tipada.

## 1. Principio rector

**El runtime emite eventos semánticos; Odoo es dueño de toda la lógica de CRM.** El
runtime nunca decide una etapa ni un nombre de stage: solo reporta *"este teléfono
mostró interés en el grupo de servicio X"*. Odoo decide si eso crea un lead, en qué
etapa, con qué deduplicación y con qué exclusiones.

Consecuencia clave: **la única etapa que el runtime escribe es `Nuevo`.** Todo lo
demás (valoración agendada, cotización enviada, servicio programado, cerrado) se
**detecta dentro de Odoo** a partir de `sale.order` / `calendar.event` / acción de
staff / cron. El agente **nunca** empuja a nadie más allá de `Nuevo`.

Por qué así:
- Mantiene el runtime tonto respecto al CRM (renombrar/reordenar etapas no exige
  redeploy del servicio).
- La lógica de negocio (dedupe, alcance por servicio, exclusión de clientes,
  monotonía, caducidad) queda **visible y editable en Odoo**, como el prompt editable.
- Minimiza el cruce de la regla de solo-lectura: un solo método de escritura, que en
  la práctica solo crea/refresca leads en `Nuevo`.

## 2. Las cinco etapas y las dos ramas

Etapas (pipeline de CRM "WhatsApp"):

1. **Nuevo**
2. **Visita de valoración agendada**
3. **Cotización enviada**
4. **Servicio programado**
5. **Cerrado** (won o lost — ver §7)

**No es un embudo lineal único.** Hay dos ramas según si el servicio se puede
cotizar/agendar solo:

- **Rama automática** (el wizard puede precio + agendar):
  `Nuevo → Servicio programado → Cerrado`. **Salta** las etapas 2 y 3.
- **Rama manual / valoración** (área muy grande, termitas/chinches, especializado):
  `Nuevo → Visita de valoración agendada → Cotización enviada → Servicio programado → Cerrado`.

> ⚠️ **Implicación de reporte:** `Cotización enviada` **solo existe en la rama manual**
> (es la cotización formal que hace finanzas tras la visita de valoración). La
> conversión `Nuevo → Cotización enviada` refleja **solo** la rama manual; los clientes
> auto-cotizables aparecerán "saltando" directo a `Servicio programado`. Es correcto —
> socializarlo con el equipo para que nadie lea el embudo como roto.

## 3. Modelo CRM

Se usa **`crm.lead` nativo** (tipo oportunidad) en un **pipeline dedicado**
(`crm.team` "WhatsApp"). Nada de modelo custom: reporting, Kanban y won/lost nativos
ya lo entienden.

**Etapas** (`crm.stage`): las cinco de §2, sembradas por el módulo. `Cerrado` con
`is_won = True` (el won se queda visible en la columna; el lost se archiva con
`lost_reason_id`, sigue siendo reportable — ver §7).

**Campos que se agregan a `crm.lead`:**

| Campo | Tipo | Para qué |
|---|---|---|
| `x_visar_service_group_id` | m2o `visar.service.group` | El **grupo** que acota el lead (Fumigación vs Áreas Verdes). Clave de dedupe. |
| `x_visar_wa_phone_norm` | Char (indexado) | Teléfono **normalizado a 10 dígitos** (ver §4). Búsqueda exacta del lead abierto, sin re-normalizar. |
| `x_visar_source` | Selection (`whatsapp`, …) | Origen. (Alternativa: `utm.source` nativo; decidir al implementar.) |

Se reutilizan nativos: `phone`, `partner_id`, `stage_id`, `expected_revenue`
(último total cotizado), `lost_reason_id`, `team_id`, chatter (`mail.message`).

## 4. Identidad del lead — `(teléfono, grupo)`

**La decisión más consecuente.** El lead **no** se identifica solo por teléfono, sino
por la pareja **(teléfono normalizado, grupo de servicio)**:

- Un cliente de fumigación que pregunta por jardinería → **lead nuevo en el grupo
  Áreas Verdes**, aunque ya sea cliente de fumigación. ✅
- **Alcance = grupo** (`visar.service.group`), **no** dimensión: interior vs exterior
  de fumigación es **un** lead de fumigación. El runtime manda un `service_code` de
  **dimensión** (mismo vocabulario que el resto de la API: `FUM_INT`, `MAV_JAR`…) y
  **Odoo resuelve dimensión → grupo** (reutiliza `_agent_resolve_dimension`).
- **Normalización de teléfono:** últimos **10 dígitos** (esquiva el `52` de país y el
  `1` de móvil de WhatsApp), igual que `_agent_normalize_phone` en la etapa C. Fuente
  clásica de "no encontró al cliente"; por eso se guarda ya normalizado en
  `x_visar_wa_phone_norm`.

**Multi-grupo en una conversación** (pregunta por fumigación *y* áreas verdes):
**dos leads**, creados **independientemente**, cada uno cuando *su* grupo surge por
primera vez — **no** ambos de golpe ni al cerrar la conversación. Cada uno avanza por
su propia rama. Un **combo** posterior (una cita con ambos servicios) hace avanzar a
**los dos** leads (fan-out, §6/§8).

**Cuándo se crea:** al **identificar el grupo**, no al tocar el menú. El grupo se
conoce de forma fiable cuando el agente cotiza (la tool `quote_service` trae la
dimensión → grupo). Preguntas sin grupo identificable (p. ej. "¿cubren mi zona?" vía
`resolve_zone`, que no trae grupo) **no** crean lead todavía: se espera a que aparezca
un grupo.

**Exclusión de clientes existentes — también por grupo.** "Los clientes con
suscripción no son leads": antes de crear el `Nuevo`, Odoo verifica si el partner ya
tiene **suscripción activa u orden confirmada en ese mismo grupo**. Si sí → **no** se
crea lead. Si es suscriptor de fumigación preguntando por jardinería → el lead de
jardinería **sí** se crea. Esta verificación vive en Odoo (es quien ve
`visar_subscription` + historial de órdenes), otra razón para que la decisión de crear
esté en el método RPC, no en el runtime.

## 5. Mapeo etapa por etapa

| Etapa | Lo dispara | Dónde se detecta | Detalle |
|---|---|---|---|
| **Nuevo** | Grupo identificado en una conversación de WhatsApp; partner no es ya cliente de ese grupo | **Runtime** → `agent_track_lead` | Única etapa que escribe el runtime. La **cotización inline** del agente **no** avanza etapa: solo enriquece (ver §5.1). |
| **Visita de valoración agendada** | Se reservó y pagó la **Valoración Técnica $500** | **Odoo** (`calendar.event`/`sale.order` del producto de valoración) | Rama `is_valuation` del wizard: área fuera de rango, termitas/chinches, plaga no identificada. |
| **Cotización enviada** | Se envió la **cotización formal/manual** tras la visita de valoración | **Odoo** — acción de staff (botón) | Finanzas/técnicos arman la cotización manual. Botón que mueve el lead (ver §6). |
| **Servicio programado** | El cliente completó el wizard **y pagó** | **Odoo** (`sale.order` confirmada → `calendar.event`) | **No** es el clic en la liga "Agendar" (eso es solo intención). El **pago** es la compuerta. |
| **Cerrado** | Servicio realizado (won) o vencido por inactividad (lost) | **Odoo** (tarea FSM terminada / cron) | Distinguir won/lost con mecánica nativa (§7). |

### 5.1 Captura de la cotización inline (enriquecimiento, no avance)

La cotización que da el agente en la ruta Información **se guarda como dato del lead
`Nuevo`**, sin mover etapa:
- **Nota de chatter** por cada cotización ("Cotización del agente: Fumigación
  interior, 120 m², CP 64060 → $690") — conserva el historial si preguntan dos veces.
- **`expected_revenue`** = último total cotizado (aparece en el valor del pipeline).

Así el instinto de "quote generated" sigue siendo útil: **enriquece** el lead con
contexto para ventas, en vez de avanzarlo.

## 6. El método RPC de escritura acotado

Un único método nuevo en `visar.agent.tools` (o extendido vía `_inherit` desde el
módulo CRM). Mismo principio que el resto: parámetros tipados, sin nombres de modelo
ni dominios, respuesta mínima tipada, **`sudo()` acotado solo a este método** (cruza
datos de partner/órdenes/suscripción que el usuario share no ve por ACL).

```python
agent_track_lead(payload) -> dict

payload = {
  "phone": "5218112345678",
  "service_code": "FUM_INT",        # código de DIMENSIÓN; Odoo resuelve el grupo
  "quote": {                        # opcional: enriquecimiento (§5.1)
     "cp": "64060", "m2": 120, "total": 690.0, "currency": "MXN"
  } | None,
  "source": "whatsapp"
}

returns {
  "lead_id": int | None,            # None si se omitió (cliente existente del grupo)
  "created": bool,
  "stage": "nuevo",
  "skipped_reason": "existing_customer" | "no_group" | None
}
```

Lógica del método (todo en Odoo):
1. Normaliza teléfono (últimos 10) → resuelve `res.partner` si existe (**no** crea partner).
2. Resuelve `service_code` (dimensión) → `visar.service.group`. Si no resuelve → `no_group`.
3. **Exclusión de cliente existente** por grupo (suscripción activa u orden confirmada
   en el grupo) → `existing_customer`, no crea.
4. Busca lead **abierto** para `(x_visar_wa_phone_norm, x_visar_service_group_id)` en el
   pipeline WhatsApp (no `Cerrado`). Si existe → lo **refresca** (chatter + `expected_revenue`).
   Si no → lo **crea** en `Nuevo`.
5. **Nunca avanza** más allá de `Nuevo`: este método solo crea/refresca. El avance lo
   hacen las automatizaciones internas (§8).

### Lado runtime (`visar_fastapi`)

Al cambiar el contrato RPC se tocan **los dos lados** (convención del proyecto): método
en el protocolo `VisarOdooClient` + `OdooRPCClient` + `FakeOdooClient`. El hook natural
en `app/agent.py`: tras una llamada exitosa a la tool `quote_service` en la ruta
Información (de ahí sale la dimensión → grupo), el agente emite `agent_track_lead` en
segundo plano (no debe bloquear ni tumbar el turno si Odoo falla — igual que las demás
lecturas: se registra y se sigue). La respuesta al cliente **no** depende de que el
tracking tenga éxito.

## 7. Cerrado — won / lost (nativo)

- **Won:** mecánica nativa (`Cerrado.is_won = True`; marcar won al completar el
  servicio). Se queda visible en la columna `Cerrado`.
- **Lost:** `lost_reason_id` nativo (+ el cron pone una razón tipo "Sin respuesta").
  El lost nativo **archiva** el lead (`active = False`): sale del Kanban pero **sigue
  siendo reportable**. (Si en el futuro se quiere ver won y lost juntos en la columna,
  se agrega un `Selection` custom `x_close_type`; **no** es prioridad hoy.)

## 8. Automatizaciones internas de Odoo (avance de etapa)

Reglas `base.automation` / server actions, **forward-only** (nunca retroceden; si el
lead ya está en una etapa posterior, un evento tardío no lo regresa):

- **→ Visita de valoración agendada:** al crear `calendar.event`/confirmar `sale.order`
  cuyo producto sea el de **valoración** ($500) → mover el/los lead(s) que casen por
  `(teléfono, grupo)`.
- **→ Cotización enviada:** **botón/acción de staff** (la cotización formal es un
  `sale.order` presupuesto que arma finanzas tras la visita). Empezar por un botón en el
  lead "Marcar cotización enviada"; opcional: automatizar al enviar el presupuesto.
- **→ Servicio programado:** al confirmarse `sale.order` **pagada** con líneas de
  servicio real → mover el/los lead(s). **Combo → fan-out a varios leads** (uno por
  grupo; el grupo se deriva de las líneas de la orden / dimensión del producto).
- **→ Cerrado (won):** al terminar la tarea FSM (`project.task` en etapa done) del
  servicio → marcar won el lead correspondiente. (Puede empezar manual si el puente
  tarea→lead resulta caro.)

## 9. Atribución web → lead (puente)

**Decisión: casar por `(últimos-10-dígitos, grupo)`.** Sin lead token (por ahora).

Cuando una reserva se completa en el wizard web, lo único que la ata al lead es el
**teléfono + grupo**:
- El **grupo** se deriva de las **líneas del `sale.order`** (producto → dimensión →
  grupo). Un **combo** produce varios grupos → **fan-out** a los leads abiertos de cada
  grupo de ese teléfono.
- El **teléfono** se normaliza a 10 dígitos en ambos lados.

Modo de falla aceptado: dos leads abiertos del **mismo** grupo para el mismo teléfono,
o datos de teléfono muy sucios. Raro; se acepta perder alguna atribución.

**Lead token — descartado por ahora (nota para el futuro).** Sería un id opaco que la
liga "Agendar" lleva (`…/booking?lead=<token>`), que el wizard estampa en el
`sale.order` para atribución **exacta**. No se hace hoy porque: (a) **exige cirugía en
el wizard** — hoy **no** existe seed por URL; la sesión se arma solo con POSTs
secuenciales (la ruta `seed` está planeada pero no construida, ver doc 29/30); (b) solo
cubre a quien **usa la liga** del agente (quien navega al sitio por su cuenta llega sin
token → cae igual al matching por teléfono); (c) la ambigüedad principal (¿qué grupo?)
ya la resuelve el alcance por grupo. **Se puede añadir después** —cuando exista la ruta
`seed` del flujo completo de agendado— sin cambiar el modelo de lead.

## 10. Lead perdido — cron de caducidad

`ir.cron` diario que marca lost los leads abiertos inactivos, con **ventanas
configurables por etapa** (parámetros del sistema, editables sin deploy):

- `visar.crm.lost_days_nuevo` — un buscador de info se enfría rápido.
- `visar.crm.lost_days_cotizacion` — quien tiene una cotización formal aguanta más.

Base: `write_date` / fecha de entrada a la etapa. Usa `action_set_lost` nativo con
`lost_reason`. (Valores concretos de días: **pendientes de definir con el equipo**.)

## 11. Módulos (recomendación de layout)

- **Nuevo módulo `visar_crm`** (depende de `crm`, `visar_appointment`,
  `visar_subscription`): pipeline + `crm.stage` sembradas, campos de `crm.lead`,
  automatizaciones (§8), cron (§10).
- **`visar_whatsapp_agent`**: gana el método `agent_track_lead` (en `visar.agent.tools`)
  y depende de `visar_crm`. Alternativa: el método también en `visar_crm` extendiendo el
  AbstractModel por `_inherit`. Decidir al implementar.

## 12. Decisiones cerradas

1. **Leads en `crm.lead` nativo**, pipeline "WhatsApp" dedicado.
2. **Identidad `(teléfono últimos-10, grupo de servicio)`**, no solo teléfono.
   Multi-grupo → **un lead por grupo**, creados independientemente.
3. **El runtime solo escribe `Nuevo`**; todo lo demás lo detecta/avanza Odoo.
4. **Cotización inline del agente = `Nuevo`** (enriquece: chatter + `expected_revenue`),
   **no** avanza. `Cotización enviada` = **solo cotización formal/manual** (rama manual).
5. **Dos ramas:** automática (`Nuevo→Servicio programado`) y manual
   (`Nuevo→Valoración→Cotización→Servicio programado`).
6. **`Servicio programado` exige pago**, no el clic en "Agendar".
7. **Cerrado won/lost con mecánica nativa** (won flag + `lost_reason_id`); sin
   `Selection` custom por ahora.
8. **Atribución por `(teléfono, grupo)`**, sin lead token (documentado como mejora futura).
9. **Exclusión de clientes existentes por grupo** (suscripción/orden confirmada en el grupo).
10. **Un solo método RPC de escritura acotado** (`agent_track_lead`); primer write
    runtime→Odoo, sin ampliar el ACL general.

## 13. Pendientes / a definir

- Valores de las ventanas de caducidad (`lost_days_*`).
- Puente tarea FSM → lead para el won automático (o dejarlo manual al inicio).
- ¿`x_visar_source` propio vs `utm.source` nativo?
- Layout de módulos (`agent_track_lead` en `visar_whatsapp_agent` vs `visar_crm`).
- Verificar contra `visar_prod` la cadena real de atribución (teléfono+grupo desde
  líneas de `sale.order`), como se hizo con la cotización y con la etapa C.

## 14. Orden de implementación sugerido

1. **`visar_crm`**: pipeline + `crm.stage` + campos de `crm.lead` (base para todo).
2. **`agent_track_lead`** (Odoo) + tocar el runtime (protocolo/cliente/fake) + hook en
   `agent.py` tras `quote_service`. Entrega ya la etapa `Nuevo` con enriquecimiento.
3. **Automatizaciones de avance** (§8): valoración y servicio programado (detección por
   `sale.order`/`calendar.event`), botón de cotización enviada.
4. **Cron de caducidad** (§10).
5. **Won automático** (tarea FSM → lead) o dejarlo manual.
6. **Verificación E2E** contra `visar_prod`: creación por grupo, exclusión de cliente
   existente, fan-out de combo, atribución por teléfono+grupo, caducidad.
