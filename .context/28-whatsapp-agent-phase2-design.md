# WhatsApp agent — Fase 2: plataforma de capacidades (DISEÑO)

> **Estado: DISEÑO, no implementado.** Escrito 2026-07-24. Extiende
> `27-whatsapp-agent.md` (Fase 1 = solo lectura, ya construida). Este doc está
> para revisarse e **implementarse en una sesión posterior**. El runtime vive en
> el repo aparte `visar_fastapi` (`github.com/igeg1/visar-fastapi`); su lado de
> este diseño está en su `.context/80-phase2-capability-platform.md`.

## Modelo mental: un número, un dueño, varios handlers

Hoy hay **un** agente haciendo **un** trabajo (dudas de servicios/precios por LLM).
El objetivo de Fase 2 es que el **mismo número** haga **varios trabajos**, sin que
todos pasen por el LLM. La clave: el número es un **canal**, y detrás hay un
**dispatcher** que enruta cada mensaje a un **handler**.

Los trabajos difieren en **dos ejes** (no solo "prompt"):

| Trabajo | Dirección | Cerebro | Estado |
|---|---|---|---|
| Dudas de servicios y precios | Entrante (lo inicia el cliente) | **LLM** + prompt | Hecho (Fase 1) |
| Agendar cita | Entrante | **Flujo determinista** (cuestionario Q&A predefinido) — sin LLM | Fase 2c |
| Mensajes automáticos (app de técnicos, etc.) | **Saliente** (lo inicia Odoo) | **Template** determinista — sin LLM, por evento | Fase 2b |

> "Prompt editable" solo gobierna la **primera** fila. La segunda es un
> **flujo/máquina de estados**; la tercera es un **template + disparador**.
> Modelar todo como "prompts" sería un error.

## La regla de plataforma: ventana de 24 h + templates aprobados

WhatsApp solo permite texto **libre** dentro de las **24 h** desde el último
mensaje del cliente. Fuera de esa ventana —que es donde caen casi siempre las
notificaciones "tu técnico va en camino"— **solo** se pueden mandar **mensajes de
template pre-aprobados** por Meta. Por eso el trabajo saliente (Fase 2b) necesita
**templates aprobados**, no un prompt. Presupuestar el paso de aprobación en Meta.

## Arquitectura: dispatcher + handlers

```
entrante → dispatcher → { LLM Q&A (prompt) | flujo cita (cuestionario) | ... }
saliente ← evento Odoo (app técnicos) → enviar TEMPLATE aprobado vía Cloud API
```

- **Entrante:** hoy siempre va al LLM. Cuando exista un 2º handler entrante
  (flujo de cita), el dispatcher decide por intención/palabra clave. **No se
  construye el enrutado hasta que haya un 2º handler.**
- **Saliente:** una automatización de Odoo dispara en un evento (p. ej. la tarea
  FSM cambia de etapa) y llama a un endpoint de envío del runtime, que manda el
  template por la Cloud API. **No usa el webhook** (enviar solo necesita el token),
  así que no choca con el agente entrante.

## Modelos Odoo (config + capacidades)

Menús bajo un apartado "Agente WhatsApp", con el look de la config del módulo
nativo (`whatsapp.account`). **Todos nuevos, en `visar_whatsapp_agent`.**

### `visar.whatsapp.config` (la cuenta / las 6 variables del `.env`)
| Campo | Tipo | Nota |
|---|---|---|
| `name` | Char | etiqueta |
| `phone_uid` | Char | Phone Number ID |
| `app_uid` | Char | Application ID |
| `waba_uid` | Char | WhatsApp Business Account ID (hoy el runtime no lo usa) |
| `verify_token` | Char | lo inventa el consultor; se pega igual en Meta |
| `webhook_path` | Char | default `/whatsapp/webhook` |
| `token` | Char | access token — **secreto** (ver nota) |
| `app_secret` | Char | **secreto** (ver nota) |
| `active` | Boolean | |

### `visar.llm.config` (selector de proveedor + knobs)
| Campo | Tipo | Nota |
|---|---|---|
| `provider` | Selection | `anthropic_api_key` / `anthropic_oauth` / `openai_api_key` / `codex_oauth` |
| `model` | Char | default `claude-haiku-4-5` |
| `max_tokens` | Integer | default 1024 |
| `max_tool_iterations` | Integer | default 4 |
| `api_key` / `oauth_token` | Char | **secreto** (ver nota) |
| `active` | Boolean | |

### `visar.agent.prompt` (el prompt editable — handler LLM)
| Campo | Tipo | Nota |
|---|---|---|
| `name` | Char | nombre del caso de uso |
| `body` | Text | el prompt base del sistema |
| `active` | Boolean | el runtime usa **el activo**; el modelo admite varios |
| `sequence` | Integer | para orden/selección futura |

> Diseñar como **lista de registros** (varios, uno activo) aunque el runtime hoy
> solo lea el activo. Da una UI tipo "Templates" y deja crecer a varios casos de
> uso sin rediseñar. **Construir el prompt activo único; dejar lugar para varios.**

### Futuro (Fase 2c / 2b), sketch:
- **`visar.agent.flow`** (+ `visar.agent.flow.step`): cuestionario determinista de
  la cita (pregunta, tipo de respuesta, opciones, siguiente paso). Alternativa:
  reutilizar `survey`. Decidir al implementar 2c.
- **Registro de templates salientes**: o un modelo
  `visar.whatsapp.outbound.template` (nombre del template en Meta, idioma, mapeo
  de parámetros, modelo/evento disparador), o simplemente referenciar el nombre
  del template de Meta desde la automatización. Empezar por lo simple.

### Nuevos métodos RPC (en `visar.agent.tools`, solo lectura)
- **`agent_runtime_config()`** → `{ "prompt": <body del activo>, "notes":
  <visar.agent.catalog_notes>, "llm": {"provider","model","max_tokens"},
  "generated_at" }`. Lo consume el runtime y lo cachea con TTL como el catálogo.
  **Por defecto NO devuelve secretos** (defensa): los secretos siguen en el `.env`
  del runtime salvo que se decida moverlos (ver nota de seguridad).

### Nota de seguridad — secretos
Los campos `token`, `app_secret`, `api_key`/`oauth_token` son **secretos**.
Ponerlos en Odoo los mete en la BD y en **todos los backups**, legibles por
cualquier admin. Recomendación: **Fase 2a mueve solo lo no-secreto** (prompt,
model, notas, config no sensible) a Odoo; los secretos y la conexión a Odoo
siguen en el `.env` del runtime. Mover secretos a Odoo es una decisión aparte y
posterior, con almacenamiento seguro. Ver `visar_fastapi/.context/50-status-roadmap.md`.

## El módulo WhatsApp nativo: decisión

**No usarlo.** Su fuerza (autoría de templates + aprobación + envío) asume que
**Odoo es dueño del webhook**. Pero el webhook (uno solo por App) lo tiene el
runtime FastAPI para las conversaciones entrantes. Si se levanta un
`whatsapp.account`:
- Puede **enviar** (enviar solo necesita el token), pero **nunca recibe** estados
  de entrega ni respuestas (van al webhook del runtime) → queda medio ciego
  (mensajes "enviado" para siempre, respuestas que no hilan en Discuss).
- El token quedaría en **dos** lados (Odoo y el `.env`).

Su valor (autoría/aprobación de templates) es reemplazable por el **WhatsApp
Manager de Meta** (autoría web) + **reglas de automatización de Odoo** (disparo).
Cuándo **sí** convendría el nativo: si NO hubiera agente externo y se quisiera que
Odoo fuera el hub de WhatsApp (conversaciones en Discuss, campañas, analítica).
No es el caso.

## Mensajes salientes disparados — forma concreta (Fase 2b)

Tres piezas, poco desarrollo:

| Pieza | Dónde vive | ¿Custom? |
|---|---|---|
| Los templates aprobados ("técnico en camino", etc.) | **Meta WhatsApp Manager** (autoría + aprobación, una vez, pocos) | Nada — es UI de Meta |
| El envío | **runtime FastAPI** (ya es dueño del número y el token; `pywa` tiene `send_template()`) | Chico: un endpoint interno |
| El disparo (evento app técnicos → enviar) | **Odoo** — regla de automatización / server action sobre el evento (p. ej. etapa de `project.task`) llama al endpoint | Config + snippet mínimo |

Seguridad del endpoint: `POST /internal/send` **solo loopback** + token
compartido (`INTERNAL_API_TOKEN` en `.env` del runtime y como
`ir.config_parameter` en Odoo). Mismo servidor → binding loopback ya acota.

## Plan por fases

- **2a (primer corte, recomendado):** `visar.whatsapp.config` + `visar.llm.config`
  (no-secreto) + `visar.agent.prompt` (prompt base editable) + método
  `agent_runtime_config()` + el runtime lee prompt/config cacheado. Vistas de
  formulario + menú, con el look de la config del módulo nativo. Entrega el valor
  de "prompt editable sin tocar código".
- **2b:** salientes disparados (templates en Meta + `/internal/send` +
  automatización Odoo sobre eventos de la app de técnicos).
- **2c:** flujo determinista de cita (cuestionario) + dispatcher/enrutado por
  intención cuando haya >1 handler entrante.

## Decisiones abiertas (resolver al implementar)
- **Secretos en Odoo vs `.env`** — recomendado `.env` hasta tener almacenamiento seguro.
- **Detección de intención** (palabra clave vs LLM-lite) — solo hace falta en 2c.
- **Estado del flujo de cita** — extender el `ConversationStore` del runtime vs
  guardarlo en Odoo (regla firme: no escribir en la BD de Odoo desde el runtime).
- **Registro de templates** — modelo propio vs referenciar nombres de Meta.
