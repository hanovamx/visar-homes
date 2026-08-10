# Handoff — Deploy & verify latest Visar changes on the production server

> **Audience:** an agent/operator with SSH + code access to the host running the
> **productive Odoo 19 Enterprise** instance (`visar_prod`). You do the deploy and
> the post-deploy configuration/verification. This doc is self-contained; you do
> **not** need the `.context/` history, but `80-deploy-prod.md` and `25-field-app.md`
> in the module repo have the deep background.
>
> **Written:** 2026-07-08. **Repo state at handoff:** branch `main`, latest commit
> `feat(visar_field_app): jump to In-Progress on arrival + track client wait time`.

---

## 0. TL;DR

1. Back up the DB and filestore.
2. `git pull` the module repo on the server (branch `main`).
3. Upgrade the four modules with `-u` in dependency order, then **restart** Odoo.
4. Do the **manual post-deploy steps** in §4 — the code does NOT do them:
   **assign worksheet templates to FSM projects**, run the **geocode action**,
   **de-duplicate technician PINs**.
5. Run the **verification checklist** (§5).
6. Watch for the **gotchas** in §6 (idempotent seeder overwrites Studio edits;
   `visar_appointment` has no install hook).

---

## 1. What you are deploying

Four custom modules (technical names) with these versions **in the repo now**:

| Module | Repo version | Depends on | Install hook | Migrations present |
|---|---|---|---|---|
| `visar_base` | `19.0.1.1.0` | `sale`, `product`, `appointment` | — | none (setup via `data/`) |
| `visar_fsm` | `19.0.1.0.4` | `visar_base`, `appointment`, `hr`, `industry_fsm`, `industry_fsm_sale` | `post_init_hook` | none |
| `visar_appointment` | `19.0.2.0.20` | `visar_base`, `visar_fsm`, `website_appointment`, `website_appointment_sale`, `website_sale`, `hr`, `worksheet` | **none** ⚠️ | `19.0.2.0.0`…`19.0.2.0.20` |
| `visar_field_app` | `19.0.1.10.0` | `visar_fsm`, `website`, `industry_fsm_report`, `base_geolocalize` | `post_init_hook` | `19.0.1.2.0`, `19.0.1.3.1`, `19.0.1.4.0`, `19.0.1.7.0` |

**Dependency / upgrade order:** `visar_base` → `visar_fsm` → `visar_appointment` → `visar_field_app`.

**New third-party dependency since the last prod deploy:** `base_geolocalize`
(pulled in by `visar_field_app` for the service map / geocoding). It ships with Odoo
core — no external install, but the upgrade must be allowed to auto-install it.

### What actually changed in this batch (the "latest")

Mostly `visar_field_app` (the field-technician web app), 07–08 Jul 2026:

- **Worksheet renderer** over the *native* worksheet: one2many as cards, many2many as
  checkbox groups (incl. inside cards), per-line images, per-field help (ⓘ),
  conditional "Otro" companion fields, multi-photo galleries; technician-photo delete.
- **Service map + geocoding** (Leaflet + OSM tiles, Mapbox/OSM geocoding server-side),
  "Open in Google Maps".
- **On-site stage flow (Req 2):** Enroute → Arrive → Wait (editable countdown + alarm)
  → Start → Close, plus a "client no-show → reschedule" path. Reuses **native FSM
  stages** and the **native timesheet**. Latest tweak: **"Confirm arrival" now jumps
  the FSM stage straight to *En ejecución*** and records client wait minutes
  (`visar_client_wait_minutes`).
- **Worksheet template seeder** (`hooks.py`): builds three `worksheet.template`
  records in code (see §3).

#### Newer batch — `visar_field_app` 16–17 Jul 2026 (v19.0.1.5.0 → 19.0.1.10.0)

Later work, all in `visar_field_app`; full detail in `25-field-app.md` "🆕 Actualización" sections:

- **App icon** (green brand leaf) on the "App de Campo Visar" menu (`web_icon_data` is stored →
  refreshes on `-u`).
- **List scope Hoy/Todos** (`?scope=`) in the technician's timezone; the map is always today's route.
- **Drag-and-drop route ordering** — new model `visar.field.route.order` (per technician), reflected
  on the map and persisted across sessions.
- **Button tracing:** tapping Llamar / WhatsApp / Google Maps posts an internal chatter note.
- **Ordered flow:** the **worksheet** is hidden until "Comenzar servicio"; the **signature** is
  hidden until the worksheet is saved once, which moves the task to a new **"Pendiente de firma"**
  stage (seeded active, between En ejecución and Completado — `hooks.py::seed_signature_stage`,
  migration `19.0.1.7.0`).
- **Required-field validation** on the worksheet (client red-mark/block + server), incl. conditional
  and min-one rules (closes I-05).
- **PDF "Tiempo en sitio"** block (arrival → last worksheet save), above "Registro de horas".
- **PDF photo fix:** worksheet photos now actually embed (base64 round-trip) as **JPEG** (avoids a
  wkhtmltopdf blank-first-page bug with large PNG data-URIs).

`visar_base` / `visar_fsm` / `visar_appointment` may also carry version bumps vs. what
is currently installed in prod — the `-u` in §3 upgrades all four regardless, so you do
not need to diff them by hand.

---

## 2. Pre-deploy checklist

- [ ] **Backup** the database and the **filestore** (worksheet photos, signatures live
      as `ir.attachment` on disk). This deploy runs schema migrations — a rollback
      needs both.
- [ ] Confirm the instance is **Odoo 19 Enterprise** (worksheets need `industry_fsm*`
      + `worksheet`, all Enterprise).
- [ ] Record the **currently-installed versions** so you know which migrations will run:
      ```sql
      SELECT name, latest_version, state FROM ir_module_module
      WHERE name IN ('visar_base','visar_fsm','visar_appointment','visar_field_app',
                     'base_geolocalize');
      ```
- [ ] Confirm the module repo path is on Odoo's `addons_path` and pull the latest
      `main` there.
- [ ] Do this in a **maintenance window** — an active countdown/close in the field app
      during restart is fine (state is on the task), but avoid mid-checkout on `/appointment`.

---

## 3. Deploy mechanics

### 3.1 Pull + upgrade + restart

```bash
# On the server, in the module repo:
git pull origin main

# Upgrade all four in one shot (dependency order is resolved by Odoo, but list base first):
<odoo-bin> -c <odoo.conf> \
  -u visar_base,visar_fsm,visar_appointment,visar_field_app \
  --stop-after-init

# Then RESTART the Odoo service (workers cache the model registry, QWeb templates
# and assets — a live worker will serve stale code/worksheet arch until restarted).
```

> Replace `<odoo-bin>` / `<odoo.conf>` with the host's actual launcher and config.

### 3.2 What the upgrade runs automatically

- **`visar_base`** — reloads `data/` (incl. the SEPOMEX CP→zone catalog
  `visar_zone_cp_data.xml`). No hook.
- **`visar_fsm`** — `post_init_hook` only runs on clean install; on `-u` its setup is
  idempotent via prior migrations. Ensures FSM projects + `project_id`/`service_tracking`
  on products.
- **`visar_appointment`** — runs any pending `migrations/19.0.2.0.*` (legacy catalog
  linking, entry types `visar_flow`, master type "Servicios Visar", question
  detachment, etc.).
- **`visar_field_app`** — runs `migrations/19.0.1.2.0`, `19.0.1.3.1`, `19.0.1.4.0`,
  **`19.0.1.16.0`** (**each calls `seed_worksheet_templates(env)`**, idempotent) plus **`19.0.1.7.0`**
  (**`seed_signature_stage(env)`** — unarchives/orders the "Pendiente de firma" stage and
  gives it a stable xmlid). The seeders create/update the three worksheet templates and
  their dynamic models/fields:
  - **"Fumigación interior o exterior (App v2)"**
  - **"Mantenimiento de áreas verdes (App v2)"**
  - **"Visita de valoración técnica (App v2)"**

  If the module in prod is already at the latest version (no migration to run) but the
  templates / stage need re-seeding, run the seeders manually:
  ```bash
  <odoo-bin> shell -c <odoo.conf> -d visar_prod
  >>> from odoo.addons.visar_field_app.hooks import seed_worksheet_templates, seed_signature_stage
  >>> seed_worksheet_templates(env); seed_signature_stage(env)
  >>> env.cr.commit()
  ```

> ⚠️ **`19.0.1.16.0` es la ÚNICA migración del módulo que BORRA datos.** Re-estructura la
> taxonomía de plagas de Fumigación a 2 niveles y siembra el catálogo de categorías con
> `prune=True`: las etiquetas de nivel 1 que ya no son categorías (**Termitas, Polilla, Chinches,
> Otros**) **se eliminan** de `x_visar_plaga` — ahora son *especies* bajo "Otras plagas". Eliminar
> una etiqueta solo quita las filas de relación de los m2m que la tenían capturada (no borra
> líneas ni hojas), pero **no es reversible**. Es lo deseado mientras los datos sean de prueba; si
> esa BD ya tuviera hojas reales de fumigación, **mapear los valores antes** de correr el `-u`.
> Verificar antes: `SELECT x_name FROM x_visar_plaga;` y
> `SELECT count(*) FROM x_area_plaga_rel;`

### 3.3 Install vs upgrade (why it matters)

| Action | Runs | Does NOT run |
|---|---|---|
| `-u` (upgrade — the normal prod path) | `migrations/*` + `data/` | `post_init_hook` |
| `-i` (clean install on an empty DB) | `data/` + `post_init_hook` | `migrations/*` |

Prod is an **existing DB → `-u`**, so migrations (and thus the field-app seeder) run.
The clean-`-i` path has **never been tested end-to-end on an empty DB** and has a known
gap (`visar_appointment`, see §6) — only relevant if you ever rebuild from scratch.

---

## 4. Manual post-deploy steps (REQUIRED — code does not do these)

### 4.1 Assign worksheet templates to FSM projects  ⭐ most important

Seeding only **creates** the templates; it does **not** attach them. The field app
reads `project.task.worksheet_template_id`, which tasks inherit from their
**project** (`project.project.worksheet_template_id`). Since `visar_fsm` creates **one
task per project**, assignment is effectively per-project.

- In **Servicio externo (Field Service) → Configuración → Proyectos**, set each FSM
  project's **Plantilla de hoja de trabajo** to the matching v2 template:
  - Fumigación project → **"Fumigación interior o exterior (App v2)"**
  - Áreas verdes / jardinería project → **"Mantenimiento de áreas verdes (App v2)"**
  - Valoración técnica project → **"Visita de valoración técnica (App v2)"**
- ⚠️ Changing a project's template affects **new** tasks only; existing tasks keep the
  template they were created with. Re-point existing test tasks by hand if needed.

Verify the templates exist first:
```sql
SELECT id, name FROM worksheet_template WHERE name ILIKE '%(App v2)%';
```

### 4.1b Wire the WhatsApp report sending (REQUIRED for the "send report" button)

The button is inert until both sides share a secret. The send itself happens in the
**`visar_fastapi` runtime** (that is where Meta's token lives); Odoo only hands it the PDF
over **loopback**.

1. Generate a secret: `openssl rand -hex 32`.
2. **Runtime** (`/opt/visar_fastapi/.env`): set `INTERNAL_TOKEN=<secret>` and restart
   `visar-fastapi`. Verify: `curl -s 127.0.0.1:8000/health`, and the startup log line
   `API interna montada en /internal`.
3. **Odoo** (Ajustes → Técnico → Parámetros del sistema):
   - `visar_field.agent_token` = the **same** secret.
   - `visar_field.agent_base_url` = `http://127.0.0.1:8000` (only if the runtime is elsewhere).
4. ⚠️ **Do NOT add an nginx `location` for `/internal/`.** It must stay loopback-only — exposing
   it turns Visar's verified number into a relay for sending documents to anyone.

⚠️ **Meta's 24-hour window — this is the part that will bite.** A free-form document is only
delivered if the customer messaged you in the last 24 h, and a customer who booked through the
web wizard **never messaged**. For production you need an **approved template with a DOCUMENT
header**, then set in the runtime `.env`:

```
WA_REPORT_TEMPLATE=<approved template name>
WA_REPORT_TEMPLATE_LANG=es_MX
```

Template approval is **Meta lead time**, not a code task — start it early. With the variable
empty the runtime sends free-form, which is fine to test the transport (message the business
number from the test phone first to open the window) but fails in the field.

Smoke test from the server:
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST 127.0.0.1:8000/internal/send-report \
  -H "X-Visar-Token: <secret>" -H 'Content-Type: application/json' \
  -d '{"phone":"5281XXXXXXXX","pdf_base64":"JVBERi0xLjQK"}'
# 200 = enviado · 401 = token mal · 503 = falta INTERNAL_TOKEN o WHATSAPP_ENABLED=false
# 422 = payload invalido · 502 = Meta rechazo (p. ej. fuera de la ventana de 24 h)
```

### 4.1b-bis Client notifications (en route / arrived / reschedule) — three MORE templates

Same wiring as §4.1b (the shared secret covers both endpoints — nothing extra to configure
in Odoo). What is new is that these three notifications **have no viable free-form path**.

The report can be tested free-form: a technician messages the business number, which opens
Meta's 24-hour window. These notifications go to a customer who booked through the web wizard
and **never messaged you**, so they are *always* outside the window. Until the templates are
approved, every one of them will 502, retry, and expire.

That failure is visible by design, not silent:
- the message is recorded in **App de Campo Visar → Avisos por WhatsApp** (opens filtered on
  *No entregados* — that list is the customers someone should phone);
- the task chatter gets "el cliente NO fue avisado — conviene llamarle", and still carries the
  full text of what the customer *would* have been told.

Create and submit three **text** templates (body parameters, no media header), es_MX, then set
in the runtime `.env` and restart `visar-fastapi`:

```
WA_TEMPLATE_ENROUTE=<name>      # {{1}} = technician, {{2}} = ETA minutes
WA_TEMPLATE_ARRIVED=<name>      # {{1}} = technician, {{2}} = waiting minutes
WA_TEMPLATE_RESCHEDULE=<name>   # {{1}} = technician
WA_NOTIFICATION_TEMPLATE_LANG=es_MX
```

The current Spanish wording lives in `project_task._visar_msg_enroute` / `_arrived` /
`_reschedule` — submit text that matches it, so the chatter record and what the customer
receives stay in agreement. ⚠️ Once approved, the wording the customer sees lives at Meta:
changing it is a re-approval, not a code change.

Verify per notification type — the parameter order must match the template placeholders, or
Meta rejects the send:
```bash
curl -s -X POST 127.0.0.1:8000/internal/send-notification \
  -H "X-Visar-Token: <secret>" -H 'Content-Type: application/json' \
  -d '{"phone":"5281XXXXXXXX","template_key":"enroute","params":["Juan","30"],
       "fallback_text":"prueba"}'
# 200 {"mode":"template"} = plantilla en uso · {"mode":"free"} = falta configurar esa clave
# 502 = Meta rechazó (fuera de la ventana de 24 h, o params que no cuadran con la plantilla)
```

Then check in Odoo that the queue record moved to **Enviado** with **Modo = template**.

Cron sanity: `visar_wa_outbox_cron` ("Visar App de Campo: enviar avisos de WhatsApp
pendientes") must exist and be active. It runs every 5 minutes as a retry safety net, but the
happy path fires immediately because enqueueing triggers it — if notifications only ever go out
in 5-minute clumps, `_trigger()` is not working and that is worth investigating.

### 4.1c Camera-only photos — what ops needs to know

Field photos are now taken **live with the device camera**; the gallery path is closed.

- **HTTPS is mandatory** (`getUserMedia` only exists in a secure context). The prod domain is
  fine; a technician hitting the app over plain HTTP or an IP will see "La cámara requiere una
  conexión segura" and be unable to add photos.
- The browser will ask for camera permission once per device. If a technician denies it, they
  must re-enable it in browser settings — the app cannot re-prompt.
- Escape hatch, **off by default**: set the system parameter
  `visar_field.allow_gallery_fallback` = `True` to reveal a "Usar la galería (excepción
  autorizada)" link. Use it to unblock a device whose camera does not work, not for convenience —
  it lets any old photo in as service evidence.

### 4.2 Geocode service addresses (for the map)

Run once after deploy: menu **App de Campo Visar → "Geolocalizar direcciones de
clientes"**. It geocodes the **service (delivery) partner** of each task
(`task.partner_id`), not the billing customer.

- By default it processes only partners **without** coordinates. To re-geocode
  everything (e.g. after adding a Mapbox token), call with `force=True`:
  ```bash
  >>> env['project.task'].sudo()._visar_geolocalize_service_partners(force=True)
  >>> env.cr.commit()
  ```
- The notification reports how many resolved to **street level** vs **CP centroid**.

### 4.3 (Optional) Mapbox/Google token for precise geocoding

Without a token, geocoding uses **OSM Nominatim**, which has poor MX residential-street
coverage and falls back to the CP centroid (two streets in one CP → same point).

- Set `ir.config_parameter` **`web_map.token_map_box`** (shared with the native FSM
  map) or **`base_geolocalize.google_map_api_key`** for street-level accuracy.
- Geocoding is **server-side** (token never exposed). **Map tiles stay on OSM** because
  the technician page is public — do not put a token in client JS.
- After setting a token, re-run 4.2 with `force=True`.

### 4.4 De-duplicate technician PINs  ⚠️ data bug

In `visar_prod` the PIN `123` was found on **two** employees (Pedro Martínez **and**
Administrator). Login-by-PIN returns an arbitrary match → **non-deterministic close /
timesheet attribution**. Ensure each active technician has a **unique** `visar_field_pin`
(field on the employee form, under the native PIN). Set a real PIN on real technicians
and clear/deconflict Administrator.
```sql
SELECT id, name, visar_field_pin FROM hr_employee
WHERE visar_field_pin IS NOT NULL AND visar_field_pin != '' ORDER BY visar_field_pin;
```

### 4.5 Confirm technician→task linkage

A task shows in the app only if `visar_technician_ids` is populated. `visar_fsm` fills
it from `calendar.event` appointment resources that have `visar_employee_id`. If an
appointment resource lacks `visar_employee_id`, the task has no technician and won't
appear — assign the technician manually on the task, and set `visar_employee_id` on the
resource for future bookings.

### 4.6 Verify the "Pendiente de firma" stage adopted correctly  ⚠️ touches a live DB record (Req 6)

The seeder `seed_signature_stage` (migration `19.0.1.7.0` + `post_init_hook`) does **not**
create a fresh stage in prod — it **adopts** the one that already exists there (created by
hand, **archived**, wrong `en_US` name, no xmlid): it un-archives it, sets `sequence=15`,
fixes both names, links it to every FSM project, and gives it the stable xmlid
`visar_field_app.visar_stage_pending_signature`. This runs automatically on `-u`.

**Verify — there must be exactly ONE active stage with the xmlid, linked to the FSM projects:**
```sql
SELECT t.id, t.name, t.active, t.sequence, d.module, d.name AS xmlid
FROM project_task_type t
LEFT JOIN ir_model_data d ON d.model='project.task.type' AND d.res_id=t.id
                          AND d.module='visar_field_app' AND d.name='visar_stage_pending_signature'
WHERE t.name ILIKE 'Pendiente de firma' OR t.name ILIKE 'Pending Signature';
```
- Expect **one** row, `active=true`, `sequence=15`, with the xmlid populated.
- If you see **two** rows (an adopted one + the old hand-made one), the search didn't match
  the manual stage (e.g. its name differed) and a duplicate was created. Fix by hand:
  merge/relabel so only one active "Pendiente de firma" remains, then re-point the xmlid:
  ```bash
  >>> from odoo.addons.visar_field_app.hooks import seed_signature_stage
  >>> seed_signature_stage(env); env.cr.commit()   # idempotent; re-adopts the surviving one
  ```
- Sanity from the app: save a worksheet on a test task → the task moves to **Pendiente de
  firma** and the signature form appears (see 5.2).

### 4.7 Verify the report base URL for the worksheet PDF  ⚠️ `ir.config_parameter` (Req 8)

The Req 8 "Tiempo en sitio" PDF (and worksheet photos) render via wkhtmltopdf, which
fetches the report's CSS/assets over HTTP from the server's **own** base URL. If that URL
is unreachable **from the server itself**, each asset fetch hangs to timeout → the PDF
takes tens of seconds and/or comes out with **blank pages / no styling**.

```sql
SELECT key, value FROM ir_config_parameter WHERE key IN ('web.base.url','report.url');
```
- On **prod**, `web.base.url` should be the **real domain** and must be reachable from the
  server (loopback/self). Confirm `report.url` is either unset (falls back to `web.base.url`)
  or also a self-reachable address. **Do NOT set `report.url` to `localhost` on prod** — that
  was only a fix for the **cloned** DB, whose stale LAN IP no longer matched the machine.
- Quick check: `curl -sI <web.base.url>` from the server returns 200/redirect, not a hang.

### 4.8 Caveat — re-seeding reverts Studio edits on the two "App v2" templates (affects Req 7)

`seed_worksheet_templates` is **idempotent** and rewrites the arch of "Fumigación interior
o exterior (App v2)" and "Mantenimiento de áreas verdes (App v2)" to the **canonical** code
version on every `-u`. That canonical arch is what carries the `required="1"` node attributes
that **Req 7 validation enforces**. Consequence: **do not hand-edit these two templates in
Studio on prod** — any Studio change (including toggling a field's required flag) is reverted
on the next upgrade/seed. To change what's mandatory, edit the arch in `hooks.py` and redeploy.

---

## 5. Verification checklist

### 5.1 Deploy sanity
- [ ] All four modules `state = installed` at the repo versions (query in §2).
- [ ] `base_geolocalize` installed.
- [ ] No errors in the Odoo log during `-u`; server restarted; assets rebuilt (load
      `/visar/field` and confirm Leaflet CSS/JS 200, not 404).
- [ ] **"Pendiente de firma" stage** adopted, not duplicated (§4.6 SQL: one active row,
      `sequence=15`, xmlid `visar_field_app.visar_stage_pending_signature`).
- [ ] **Report base URL** (`web.base.url` / `report.url`) is self-reachable from the server,
      not `localhost` on prod (§4.7) — else the Req 8 PDF hangs or loses styling/photos.

### 5.2 Field app — technician flow (`/visar/field`)
- [ ] Login with a **unique** PIN → shift opens (`visar.field.session`).
- [ ] Task list shows only that technician's tasks, **scoped to today** by default
      (technician's timezone); "Todos" shows every date/state. Services closed today stay
      at the bottom, dimmed, with a *Completado* / *Reprogramar* badge.
- [ ] **Drag the `⠿` handle** to reorder today's route: cards renumber, the map repaints
      without reloading, and the order survives a reload and a logout/login.
- [ ] **List ⇄ Map** toggle: markers appear for geocoded services; popup links to detail;
      "Abrir en Google Maps" opens the service address.
- [ ] Task detail: client **contact** block (phone / Llamar / WhatsApp) present. Tapping
      **Llamar / WhatsApp / Abrir en Google Maps** leaves an internal note in the task chatter
      (technician + button + destination).
- [ ] **Worksheet is hidden** until "Comenzar servicio"; **signature is hidden** until the worksheet
      is saved once — saving it moves the task to the **Pendiente de firma** stage (seeded active,
      between En ejecución and Completado).
- [ ] **Worksheet** renders per its template: o2m cards ("+ Agregar"), m2m checkboxes,
      per-field help (ⓘ), conditional "Otro" fields show/hide, per-card image capture +
      thumbnail. **Save** persists (re-open to confirm).
- [ ] **Required validation (Req 7):** required labels show a red `*`; saving with a
      missing required/conditional/min-one field is **blocked** (red field + inline error,
      scrolls to first); conditional `*` (e.g. evidence photo when "infestación activa")
      appears/disappears with its trigger; a complete worksheet saves.
- [ ] **PDF report (Req 8):** the worksheet PDF shows a **"Tiempo en sitio"** block
      (Confirmó llegada → Última guarda de la hoja → **Duración**), just above the native
      "Registro de horas"/Timesheets section. (The two are different metrics: on-site
      documentation time vs. worked time.)
- [ ] **Photos:** upload, thumbnail, tap-to-delete (× only on technician photos — the
      signature must NOT be deletable here).
- [ ] **Stage flow:** "Voy en camino" → "Confirmar llegada" (**backend stage jumps to
      *En ejecución***) → optional "Esperar al cliente" (editable countdown; on expiry:
      alarm + "Cliente no llegó" button) → "Comenzar servicio" (records
      `visar_client_wait_minutes`) → "Cerrar servicio".
- [ ] **Close validation:** blocked without signature + name (JS and server).
- [ ] On close: task → stage **Completado** + `state='1_done'`; a **timesheet line**
      (`account.analytic.line`) is written, attributed to the technician employee;
      `visar_field_closed_by_id/_at` set.
- [ ] **Reschedule:** "Cliente no llegó" → stage **Incidencia—Reprogramar** +
      `state='1_canceled'` + an **activity** (assigned to salesperson if technician has
      no user) + a chatter note.
- [ ] **Backend↔app stage sync:** manually move the task's stage back to *Programado* in
      the backend → app reflects it and sub-phase stamps are cleared (no phantom
      timer/reschedule).
- [ ] `GET …/task/<id>/report` renders the native `industry_fsm.worksheet_custom` PDF,
      **with the worksheet photos visible** (they embed as resized JPEGs) and every page's
      text intact.
      - If the PDF is **very slow** (tens of seconds) or pages come out **blank**, it's
        almost always wkhtmltopdf failing to fetch report assets from a wrong base URL:
        check `ir.config_parameter` **`report.url`** / **`web.base.url`** point at an address
        the server can reach itself. (On prod that's the real domain; this bit the local
        clone when its stale LAN IP no longer matched.)

### 5.3 Booking + FSM (regression — only if `visar_appointment` changed)
- [ ] `/appointment` shows exactly **Valoración Técnica** and **Cita de Servicios**.
- [ ] Wizard (services → ranges → qualification → zone → schedule → checkout), incl.
      add-ons and the `is_valuation` branch → $500 valuation flow.
- [ ] After payment: FSM tasks generated, grouped one-per-project, with technician/date
      from the appointment.

### 5.4 On a **real phone** (cannot be verified over HTTP)
- [ ] Wait-timer **audible alarm + vibration** on expiry.
- [ ] Mapbox geocoding success path (only if a real token is set — see §4.3).

---

## 6. Gotchas / risks (read before and after deploy)

- **The seeder is idempotent and rewrites the worksheet view `arch` to canonical.**
  Any **Studio edits** made directly in prod to the three "(App v2)" templates are
  **lost** on the next upgrade/seed. The **code (`hooks.py`) is the source of truth**
  for those three templates. If ops needs a template change, change it in code, don't
  Studio-edit in prod.
- **`visar_appointment` has NO `post_init_hook`.** Its legacy catalog (service
  groups/dimensions/combo rule) and entry-type setup live **only in `migrations/`**. On
  the normal `-u` prod path this is fine (migrations run). But a **clean `-i` on an empty
  DB will NOT create them** — so a from-scratch rebuild needs either the migration path
  or manual catalog setup. This is the top open structural gap (see §7 / `80-deploy-prod.md`).
- **Clean `-i` on an empty DB is unverified** for the whole stack. Treat any rebuild as a
  project, not a routine op.
- **`visar_client_wait_minutes` is a new column** — that is why this batch needs `-u`,
  not just a restart.
- **PIN is plaintext, no throttling, no uniqueness constraint** (prototype-grade). §4.4
  is a data workaround, not a fix.

---

## 7. Still to IMPLEMENT (backlog — not done, do not expect it to work)

Ordered roughly by operational impact. IDs reference `.context/90-improvements-later.md`.

| # | Item | Notes |
|---|---|---|
| **I-01** | `visar_appointment` idempotent **install hook** for legacy catalog + entry types | Mirror `visar_fsm`'s pattern; make migrations call the same function (DRY). Unblocks clean `-i`. |
| — | ~~**I-05 — Required / conditional-mandatory enforcement**~~ | **DONE (17-jul-2026)** — the worksheet now validates `required="1"`, "Otro" companions, trigger-conditional fields, and ≥1 line per sub-section, on client (red + block) and server. |
| **#2** | **Multiple photos per field** (galleries) + remove the separate external "Fotos" section | Photo fields were relabeled to plural but full gallery capture is not implemented. |
| **I-08** | **Travel timer** ("en camino") | Deferred; only the client-wait timer exists. |
| — | ~~**"Mis servicios de hoy" date filter**~~ | **DONE (16-jul-2026)** — Hoy/Todos scope (`?scope=`) in the technician's timezone; the map is always today. |
| — | **Worksheet + close are separate forms** | Closing without pressing "Guardar hoja de trabajo" loses worksheet input. Consider merging or warning. |
| — | **Harden identity** | Hash PINs, add throttling, cap `/report`. |
| — | **Dual report (internal vs client)** | D-07 still open; app serves one native PDF. |
| — | **Offline capture** | None; a `1_done` task disappears from the technician list. Whole flow assumes connectivity. |

---

## 8. Rollback

1. Stop Odoo.
2. Restore the **database** and **filestore** from the §2 backup (schema migrations are
   not auto-reversible).
3. `git checkout` the previous commit in the module repo.
4. Restart Odoo. Do **not** run `-u` against the restored (older) DB with the newer code.
