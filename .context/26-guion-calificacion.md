# Guión de calificación en el wizard web (D-08)

> Plan de implementación. Adapta a la página web (`/appointment` → wizard "Cita de Servicios")
> el guión de calificación que originalmente se diseñó para el bot de WhatsApp
> (`Visar_Guion_Calificacion_Citas.docx`). Reemplaza las ~4/5 preguntas básicas actuales
> del wizard por el cuestionario completo del guión.
>
> Fuentes: `Visar_Guion_Calificacion_Citas.docx` (guión v2, Hanova) + `fumiquote.html`
> (cotizador interno del agente, del que sale la estimación por proxies) + decisiones
> tomadas en sesión (ver §7).

## 1. Principio de diseño (lo que hace el cambio de bajo riesgo)

Todo el motor de precios/variantes/pools/horario del wizard depende de **una sola cosa**:
que en la sesión `visar_booking['selections']` quede un `tier_<dimension_id>` elegido por cada
dimensión seleccionada (ver `appointment.type._visar_resolve_wizard_items`,
`_visar_build_sale_lines`, `_visar_service_resource_pools`).

El guión **no toca ese motor**. Solo cambia *cómo* se llega al `tier` por dimensión:
- **rango directo** (como hoy),
- **estimación proxy** (recámaras/baños/niveles/cochera → m² estimados → tier),
- **banda unificada de exterior** (una medición → resuelve el tier de fumigación exterior *y* el de corte).

Cada rama nueva termina escribiendo `selections['tier_<dim_id>']` + flags de calificación. El resto
del pipeline (variante por zona → pools → horario multi-técnico → carrito) queda **intacto**.

## 2. Orden de pasos nuevo

```
Paso 1  Servicios (P0)      grupos: Fumigación / Corte / (ambos = combo)         [existe]
Paso 2  Motivo (P1)         preventivo / correctivo          solo si fumigación  [NUEVO, movido arriba]
Paso 3  Plagas (P2)         rastreros/voladores/roedores + termitas/chinches/no-sé
                            solo si fumigación · aquí ocurren los cortes por plaga [NUEVO]
Paso 4  Cobertura (P3)      interior / exterior / ambos       solo si fumigación
                            (reemplaza el "dimensiones de grupo" genérico)         [reframe]
Paso 5  Interior (Etapa 2)  ¿sabes m²? → rango directo(+niveles P6)
                            ó proxy (rec/baños/niveles/cochera + terreno OPCIONAL vía Maps)
                            solo si cobertura incluye interior                     [NUEVO: rama proxy]
Paso 6  Exterior (Etapa 3)  ¿sabes m²? → banda directa(+P13) ó comparativos visuales(P14+P15)
                            UNA sola vez; alimenta fumigación exterior Y corte     [NUEVO: unifica]
Paso 7  Dirección (Etapa 4) calle/CP → zona A/B/C                                  [existe]
        → horario multi-técnico → pago   ó   → aviso valoración si hubo corte
```

Saltos: **pasto-solo** → salta 2–5, va directo a Exterior. **Combo** → 2–6 (Exterior una vez).
Motivo/Plagas **solo** cuando el grupo fumigación está seleccionado (pasto-solo no las ve — §7).

## 3. Cambios de datos / modelos (`visar_base`)

**3.1 `visar.service.dimension` — nuevo campo `measure_type`** (Selection, default `direct`):
- `direct` → lista de tiers tal cual (comportamiento legacy / fallback).
- `interior` → sub-flujo de estimación interior (rango directo o proxy).
- `exterior` → sub-flujo de banda unificada de exterior.

La migración marca: fumigación-interior=`interior`, fumigación-exterior=`exterior`, corte=`exterior`.
Reconfigurable en backend (cero hardcode: qué dimensión usa qué medición es dato).

**3.2 Nuevo modelo `visar.measure.band`** (bandas unificadas de exterior, configurable):
- `name` ("101 – 150 m²"), `m2_ref` (float; m² representativo = `m2_min` de la banda, que por
  rangos inclusivos cae en el tier correcto de cada servicio), `comparative_label`
  ("Como una cancha de básquet" → P14), `sequence`, `is_valuation` (banda >500 → corte), `active`.
- Al elegir banda, por cada dimensión `measure_type=exterior` seleccionada se resuelve su tier con la
  lógica existente `m2_min ≤ m2_ref ≤ m2_max` y se escribe `selections['tier_<dim_id>']`.
  Ej. banda `201–500` (m2_ref=201): fum-exterior→tier `101–500` ✓, corte→tier `>200` (is_valuation) ✓
  — sin alinear tablas. (Y por §7.1, si un servicio cae en valoración, toda la cita va a valoración.)
- `ir.model.access.csv` + seeding en `hooks.py`/migración (config idempotente, no XML de productos).

**3.3 Estimador interior (proxy tipo FumiQuote)** — método puro en `appointment.type`:
```
_visar_estimate_interior_m2(rec, ban, niv, gar, predio=0):
   factor = _visar_estimator_factor(predio)          # tabla configurable, §3.4; sin predio → 1.0
   mRec, mBan, mGar, mSala, mCirc = 12*f, 5*f, 14*f, 22*f, 8*f
   pb    = mSala + rec*mRec + ban*mBan + gar*mGar
   extra = (niv-1) * (ceil(rec*0.6)*mRec + ceil(ban*0.5)*mBan + mCirc)   si niv>1, si no 0
   total = round(pb + extra)
   si predio:                                         # clamp FumiQuote (constantes fijas)
       total = min(total, round(predio*niv*0.82))
       total = max(total, round(predio*0.35))
   return total
```
El resultado se resuelve a tier interior con `_visar_resolve_tier` → escribe `tier_<interior_dim>`.
**Nota:** los rangos/precios salen del tabulador oficial (tiers ya configurados, límite 1000 m²).
NO se usan los números del `fumiquote.html` (tabulador viejo 1–200/201–350/351–500, límite 500).
Solo se reutiliza la *forma* de la fórmula y la tabla de factores.

**3.4 Tabla de factores de terreno — configurable en backend** (decisión §7.3):
- Nuevo modelo `visar.estimator.factor`: `predio_max` (float, cota superior inclusiva; última fila
  valor alto), `factor` (float), `sequence`. Método `_visar_estimator_factor(predio)` devuelve el
  factor de la primera fila cuyo `predio_max ≥ predio`; sin predio → 1.0.
- Seed inicial (de `fumiquote.html`): ≤100→0.72, ≤150→0.84, ≤250→1.00, ≤400→1.20, ≤700→1.45, resto→1.70.
- `ir.model.access.csv` + seeding en `hooks.py`/migración.

## 4. Cambios de controlador (`controllers/appointment.py`)

**4.1 Resolutor central `_visar_wizard_next(selections)`** — devuelve la ruta del siguiente paso
incompleto según el orden de §2. Centraliza toda la ramificación (fumigación sí/no, cobertura,
interior/exterior, pasto, cortes) en un solo lugar; cada POST escribe su respuesta y hace
`redirect(_visar_wizard_next(...))`. Sustituye los redirects dispersos actuales y robustece los saltos.

**4.2 Rutas nuevas** (mismo patrón GET/POST que las actuales):
- `…/wizard/motivo` (P1).
- `…/wizard/plagas` (P2) — valida categorías; si termitas/chinches, o (correctivo + "no-sé"),
  setea `selections['requiere_valoracion']=True` + `motivo_valoracion`.
- `…/wizard/cobertura` (P3) — radio interior/exterior/ambos → fija `dimension_ids` del grupo
  fumigación (reemplaza el uso de `…/wizard/group/<id>` para fumigación).
- `…/wizard/interior` (Etapa 2) — GET "¿sabes m²?"; POST rama rango (P5+P6) o proxy (P7–P10 + terreno
  opcional con botón "Ver en Google Maps") → estima → escribe `tier_<interior>`.
- `…/wizard/exterior` (Etapa 3) — GET bandas o comparativos; POST → banda → escribe `tier_<exterior>`
  y `tier_<corte>`.

**4.3 `_visar_selections_require_valuation`** — extender: `True` si algún tier `is_valuation`
**o** `selections.get('requiere_valoracion')`. El corte por plaga reusa el aviso + flujo de
valoración existentes sin tocarlos.

**4.4 `roedores` retro-compatible** — derivar `roedores='si'` cuando `'roedores' in servicio_plaga`,
para que `_visar_booking_has_roedores` y la inyección del producto roedores + estaciones sigan igual.

**4.5 "Paso X de Y"** — helper que enumera los pasos aplicables a las `selections` actuales y deriva
índice y total (reemplaza `_visar_wizard_step_count` con conteo dinámico correcto por rama).

## 5. Templates (`views/wizard_templates.xml`)

- **Nuevos:** `visar_wizard_motivo`, `visar_wizard_plagas`, `visar_wizard_cobertura`,
  `visar_wizard_interior` (JS toggle sabe/estimar + campo terreno opcional con botón Maps),
  `visar_wizard_exterior` (JS toggle banda/comparativos).
- **Retirado:** `visar_wizard_calificacion` (se descompone en motivo + plagas).
- **Conservado:** `visar_wizard_dimensiones` solo para dimensiones `measure_type=direct` (fallback).
- Botón "Ver en Google Maps": abre `https://www.google.com/maps` en pestaña nueva con instrucción
  breve de medir el predio (la dirección aún no se captura en este paso; el cliente navega a su casa).
- Reutiliza el patrón visual actual (fieldset/radio/checkbox + barra "Paso X de Y").

## 6. Persistencia de flags / Q&A (sin UI de upsell aún — decisión §7.4)

Extender `_visar_build_calification_answer_inputs` para escribir en Questions & Answers (persisten al
evento/cita):
- `motivo` (preventivo/correctivo), `servicio_plaga` (categorías), `motivo_valoracion` si aplica.
- Confirmaciones ligeras P6/P13/P15 → **solo nota** en Q&A (sin alertas accionables — §7.5).
- Flags de upsell candidato (`cebaderos` si roedores; `tapon`/`guardapolvo` si rastreros) → guardados
  en `selections` + reflejados en Q&A para el administrativo. **No** se construye pantalla de upsell;
  quedan listos para una fase posterior.

Añadir 2–3 `appointment.question` en `visar_questions_data.xml` (Motivo, Plagas a tratar, Motivo de
valoración) y agregarlas a `_visar_unlink_questions_from_entry_types` (no aparecen en formulario nativo).

## 7. Decisiones tomadas (cerradas)

1. **Valoración en multi-servicio = todo-o-nada.** Si en una cita con varios servicios *alguno*
   requiere Visita de Valoración (excede rango, termitas/chinches/no-identificada), **toda la cita**
   pasa a valoración técnica $500. Es el comportamiento actual del código
   (`_visar_build_sale_lines` line ~540) y queda **confirmado** (ya no es provisional a reconfirmar).
2. **Estimador interior = proxy + terreno OPCIONAL.** Recámaras/baños/niveles/cochera siempre; campo
   de tamaño de terreno opcional con ayuda de Google Maps. Con terreno → factor FumiQuote; sin terreno
   → factor 1.0. (La estimación solo necesita acertar el rango 1–250 / 251–500 / 501–1000.)
3. **Tabla de factores = configurable en backend** (`visar.estimator.factor`).
4. **Upsells = guardar flags, sin UI** en esta fase.
5. **Confirmaciones ligeras = solo nota** (P6/P13/P15), sin alertas accionables.

## 7.bis Hallazgo de Fase 5 — conflación del tabulador de fumigación (resuelto)

Interior y exterior comparten un `product.template` (id=30 en `visar_prod`) con **ambos
tabuladores mezclados en tramos solapados**. Como el wizard *resuelve* el tramo desde un
número (a diferencia del flujo viejo que lo *elegía*), el solape hacía la resolución ambigua
(exterior 75 m² → "1-250" $600 en vez de "51-100" $800).

**Fix aplicado:** campo **`measure_scope`** (`all`/`interior`/`exterior`) en `visar.service.tier`.
La resolución (`product.template._visar_tier_for_dimension_m2`) filtra por
`measure_scope in ('all', dimension.measure_type)` y ante solapes elige el **rango más angosto**.
Migración idempotente `visar_base/migrations/19.0.1.3.0` asigna scopes a productos conflacionados
(que respaldan a la vez una dimensión interior y una exterior). Ver
[[tabulador-interior-exterior-conflation]] en memoria.

## 8. Estado de implementación (jul-2026 — COMPLETO)

Verificado E2E contra `visar_prod` (upgrade limpio + `odoo shell`). Versiones:
`visar_base` **19.0.1.3.0**, `visar_appointment` **19.0.2.1.0**.

- [x] **Fase 1** — datos/modelos (`measure_type`, `visar.measure.band`, `visar.estimator.factor`, seed, migración).
- [x] **Fase 2** — controlador (`_visar_wizard_next`, rutas motivo/plagas/cobertura, `require_valuation`, roedores retro-compat, conteo dinámico).
- [x] **Fase 3** — interior (proxy + terreno Maps) y exterior (banda/comparativos) + templates con toggles.
- [x] **Fase 4** — Q&A (Motivo, Plagas a tratar, Motivo de valoración) + notas + flags upsell; el corte a valoración conserva el motivo en la cita.
- [x] **Fase 5** — verificación E2E; fix de conflación del tabulador vía `measure_scope`.
- Cotización combo verificada (banda 51-100, Zona B): interior $600 + exterior $800 + corte combo $500 = **$1900** (coincide con el guión).

## 8.bis Orden de implementación (fases originales)

1. **Datos/modelos** (`visar_base`): `measure_type`, `visar.measure.band`, `visar.estimator.factor`,
   estimador, `ir.model.access.csv`, migración/seed. *(base para todo)*
2. **Controlador**: `_visar_wizard_next` + rutas motivo/plagas/cobertura + extender
   `require_valuation` + roedores retro-compat + conteo de pasos.
3. **Interior + Exterior**: ramas proxy/banda + templates con toggles + botón Maps.
4. **Q&A/flags**: preguntas nuevas + inputs + flags de upsell.
5. **Verificación E2E** de cada rama: fumigación interior (sabe/estima ± terreno), exterior
   (banda/comparativo), pasto-solo, combo, cortes por plaga y por área.

## 9. Mapeo guión → implementación (referencia rápida)

| Guión | Dónde vive |
|---|---|
| P0 servicio (fumigación/pasto/combo) | Paso 1 grupos (checkboxes; ambos = combo) |
| P1 motivo | Paso 2 `…/wizard/motivo` |
| P2 plagas + cortes termitas/chinches/no-sé | Paso 3 `…/wizard/plagas` → `requiere_valoracion` |
| P3 cobertura int/ext/ambos | Paso 4 `…/wizard/cobertura` → `dimension_ids` fumigación |
| Etapa 2 interior (P4–P10) | Paso 5 `…/wizard/interior` + `_visar_estimate_interior_m2` |
| Etapa 3 exterior (P11–P15) | Paso 6 `…/wizard/exterior` + `visar.measure.band` |
| Etapa 4 dirección + CP → zona | Paso 7 `…/wizard/direccion` (existe) |
| Etapa 5 cierre / valoración | horario+pago existente / aviso valoración existente |
| Variables payload (§4 guión) | `selections` + Questions & Answers (§6) |
</content>
</invoke>
