# Estado y roadmap

> **22-sep-2026 — tratamientos que se cotizan a mano (termitas, chinches), pasos 1 y 2:
> DESPLEGADO en `visar-db`** (visar_field_app 19.0.1.38.0; `deploy-cotizacion-manual-22sep.sh`).
> La hoja de trabajo pide la cotización; oficina le pone precio (descuento de valoración
> automático) y elige "hacer en esta visita" o "agendar después". Detalle en
> `25-field-app.md` §"Tratamientos que se cotizan a mano". **Paso 3 pendiente**: el agente
> manda precio + "Elegir fecha" + liga (necesita plantilla de Meta con respuesta rápida).
>
> **22-sep-2026 01:17 — varias rondas de adicionales en la misma visita: DESPLEGADO en
> `visar-db`** (visar_field_app 19.0.1.37.0, visar_commission 19.0.1.0.2;
> `deploy-rondas-adicionales-22sep.sh`, backup
> `/var/backups/visar-db_rondas-adicionales_20260922-011710.sql.gz`). Pagado un cobro de
> adicionales, la tarjeta ofrece "+ Agregar más (nuevo cobro)": otra factura en el mismo
> pedido. Detalle en `25-field-app.md` §"Varias rondas". **S00316:** su pedido aparte S00318
> (INV/2026/00172, $450 pagado en efectivo) se borró a mano el 22-sep 00:42; la factura
> quedó sin pedido y la visita se cerró con una estación de $100 sin cobrar.
>
> **22-sep-2026 00:20 — pólizas: se quita "Visitas incluidas"; las visitas salen de los
> meses pagados. DESPLEGADO en `visar-db`** (visar_subscription 19.0.1.8.0,
> visar_whatsapp_agent 19.0.1.23.0; `deploy-sin-visitas-incluidas-22sep.sh`, backup
> `/var/backups/visar-db_sin-visitas-incluidas_20260922-001924.sql.gz`). Visitas por factura
> = periodos pagados × (meses del periodo ÷ "Meses entre visitas"): anual 12, semestral 6,
> mensual 3 en el primer cobro y luego 1. "Meses entre visitas" ocupa en el plan el lugar del
> campo quitado. Motivo y efecto en pólizas activas en `35-polizas.md` §"Cuántas visitas
> genera cada factura". S00154 (bimestral archivado, factura el 30-sep) pasa a 2 visitas; si
> ese plan debe ser una cada dos meses, poner su "Meses entre visitas" en 2.
>
> **21-sep-2026 23:26 — servicio vendido y hecho en la misma visita: DESPLEGADO en
> `visar-db`** (leído de la BD: visar_field_app 19.0.1.35.0, visar_whatsapp_agent
> 19.0.1.22.0; `deploy-upsell-servicio-21sep.sh`, backup
> `/var/backups/visar-db_upsell-servicio_20260921-232453.sql.gz`). Todavía sin salida en vivo;
> pago de prueba ENCENDIDO. **23:35 — visar_field_app 19.0.1.36.0:** quitar un adicional del
> carrito ya no deja "0× …" en el pedido; se borra si nunca se facturó ni se entregó
> (la constancia queda en el chatter). Se limpiaron las 2 líneas en 0 de la prueba en S00284. visar_field_app **19.0.1.35.0**, visar_whatsapp_agent
> **19.0.1.22.0**, visar_base (método nuevo, sin columnas) y el runtime (clave
> `upsell_payment`). El técnico en una **visita de valoración** captura los m² del servicio
> que puede dejar hecho ahí mismo (fumigación interior/exterior, áreas verdes) y, al generar
> el cobro, en la **misma orden de venta**: nace el servicio como su propia visita
> ("S00284 - Fumigación interior o exterior (B, 1-250, 0 - 50)", con su hoja), ya **En
> ejecución** en el paso de la hoja (sin "Voy en camino" ni "Confirmar llegada": el cliente
> no recibe otra vez esos avisos); se emite una factura **solo por el extra**, con la
> **valoración descontada**; y la liga de pago sale **del número de Visar** (más el botón de
> respaldo desde el teléfono del técnico). Detalle en `25-field-app.md` §"Servicio vendido y
> hecho en la visita".
> **Decisiones de Visar (18-sep):** descuento = la línea de valoración del pedido, UNA vez por
> pedido, solo contra servicios, nunca por más que el servicio, solo si la valoración está
> pagada; precio del motor del agendado (el mismo que web y WhatsApp); pago Demo permitido
> **bajo un ajuste** (*Ajustes → Visar → Venta en campo*), con marca **PRUEBA** en la app.
> **Tres bugs de REQ-007 que salieron al medirlo en la copia, corregidos:** (1) el destino
> comparaba el **contacto** y no el cliente: la visita usa la dirección de servicio (hijo del
> cliente), así que **73 de 80** visitas abiertas caían al pedido aparte siendo el mismo
> cliente (S00318 fue una); (2) la factura del extra **se llevaba el pago en línea de la
> cita** (S00284: $100 "en pago" al instante contra PBNK1/2026/00142): el técnico veía
> "Pagado" sin cobrar nada; (3) el QR de cobro respondía **500** (falta el renderizador PNG de
> reportlab en el servidor; ahora `qrcode` + PIL).
> **Probado:** 17 pruebas nuevas; runtime 690. En la copia con la app real por HTTP (PIN,
> m², cargo, QR, liga) y **pagado con Demo desde el portal como el cliente**: la app pasó a
> "Pagado". **Al desplegar:** runtime primero, `-u visar_base,visar_whatsapp_agent,
> visar_field_app`, y en *Ajustes → Visar → Venta en campo* el producto de descuento
> ("Descuento", plantilla 42) y el pago de prueba. ⚠️ **Apagar el pago de prueba antes de
> salir en vivo.** Pendiente: plantilla de Meta para `upsell_payment` ({{1}} monto, {{2}}
> liga) — hasta entonces la liga desde el número de Visar solo llega con la ventana de 24 h
> abierta; el botón del técnico cubre el resto.
>
> **16-sep-2026 00:10 — paso 1 del rezago de pólizas: EN `visar-test` Y EN UNA COPIA DE
> PRODUCCIÓN, SIN DESPLEGAR.** `visar_subscription` **19.0.1.6.0**: las visitas de
> póliza estrenan **tipo** (preventiva / correctiva / garantía), **número de visita** en
> un campo y **fecha propuesta**, con la pantalla *Field Service → Planning → Visitas de
> póliza por agendar*. Nada de esto le escribe a ningún cliente: es solo ver lo que se
> le debe a cada uno. 39 pruebas del módulo y 456 de todos los módulos en `visar-test`
> (los 2 de `TestBookingDedupe` de siempre). En una copia exacta de `visar-db`
> (`visar-agenda-clone`) la migración numeró 191 visitas, conservó las 10 de garantía y
> dejó la pantalla con **150 filas: 63 con fecha propuesta, 41 fuera de vigencia y 46 sin
> ancla**; 4 ya vencidas. ⚠️ El código vive en un `git worktree` fuera de `/opt`
> (`/var/tmp/visar-wt/poliza-agenda`) precisamente para que el árbol de producción no lo
> tenga en disco antes del `-u`. Falta decidir con Visar: **cuántas visitas debe
> Suscripción Mensual** (cobra 3 meses por adelantado y genera 1 visita por factura, 59
> pedidos), el **margen de días** para que el cliente pacte otra fecha en una correctiva,
> y **quién marca el tipo** de visita. Detalle en `35-polizas.md`.
>
> **15-sep-2026 00:39 — en `visar-db`: visar_appointment 19.0.2.21.0 y
> visar_whatsapp_agent 19.0.1.20.0**, leídas de la BD después del `-u`. Dos
> despliegues, los dos por feedback de Visar probando en vivo (detalle en
> "Feedback del 14 y 15-sep" más abajo):
> **14-sep 23:53** — los metros de la casa se preguntan **igual en agendar que en
> información**: aviso de que no saberlos tiene salida, y la casa pieza por pieza
> (`estimate_fields[].pregunta`). Con `deploy-estimacion-14sep.sh`.
> **15-sep 00:38** — **primera prueba real de la reagenda por incidencia**, y tres
> fallos: el botón contestaba *"¿En qué te ayudo?"*, el agente no se enteraba de que
> ya había movido la cita, y el servicio externo no registraba cuándo reagendó el
> cliente. Además, fuera el 🎉 al apartar horario. Con `deploy-reagenda-15sep.sh`.
> 347 pruebas de los dos módulos (los 2 fallos de `TestBookingDedupe` de siempre) y
> 657 del runtime. Sigue pendiente la plantilla de Meta.
>
> Anterior: **14-sep-2026 23:15 — reagenda por incidencia y plantillas en Odoo: DESPLEGADO en
> `visar-db`**, fusionado a `main` (todavía sin salida en vivo). En la BD:
> **visar_base 19.0.1.12.0**, **visar_fsm 19.0.1.3.0**, **visar_appointment
> 19.0.2.19.0**, **visar_field_app 19.0.1.27.0**, **visar_whatsapp_agent
> 19.0.1.19.0**. 358 pruebas del módulo y 643 del runtime. La plantilla de cada
> aviso se elige en *Agente WhatsApp → Configuración → Plantillas de avisos* (todas
> vacías = todo libre, como antes). Pendiente: que Meta apruebe
> `visar_reagenda_elegir_horario`, asignarla ahí, y la prueba con una cita real.
> El primer intento del script de despliegue no corrió el `-u` y dijo "terminado":
> detalle y arreglo en `visar_fastapi/.context/87-reagendar-citas.md` §2.
>
> Anterior: **12-sep-2026 01:15 — reagenda por incidencia: EN `visar-test`, SIN DESPLEGAR.**
> El botón "Cliente no llegó" pasa de avisar a **dejar que el cliente elija
> horario**, con botón equivalente en el backend. Sube
> **visar_base 19.0.1.12.0**, **visar_fsm 19.0.1.3.0**,
> **visar_appointment 19.0.2.19.0**, **visar_field_app 19.0.1.27.0** y
> **visar_whatsapp_agent 19.0.1.18.0** (versiones **del árbol**, no de la BD de
> producción). 337 pruebas del módulo y 635 del runtime en `visar-test`; las 2 de
> `TestBookingDedupe` que fallan ya fallaban antes.
> ⚠️ **El código está en disco en `/opt/custom` y `/opt/visar_fastapi`, que son
> los que sirven a producción.** Un reinicio de `odoo` antes del `-u` en
> `visar-db` dejaría `calendar.event.visar_reschedule_granted_at` sin columna.
> Plantilla `visar_reagenda_elegir_horario` **creada y enviada a Meta el 14-sep**
> (Odoo, *WhatsApp → Plantillas*, id 18, pendiente de revisión). Desde el 14-sep
> la plantilla de cada aviso **se asigna en Odoo** (*Agente WhatsApp →
> Configuración → Plantillas de avisos*), ya no en el `.env`; ver
> `40-decisions.md`. Falta el E2E con una cita real. Diseño en `visar_fastapi/.context/87-reagendar-citas.md` §2.
> **Se descartó la liga de portal del documento de diseño**: el portal nativo de
> Odoo no reprograma, solo cancela — y con `has_payment_step` en 11 de 12 tipos
> de cita ni eso (`min_cancellation_hours` es código muerto ahí).
>
> Anterior: **11-sep-2026 23:40** — en producción
> **visar_appointment 19.0.2.18.0** y **visar_whatsapp_agent 19.0.1.17.0**,
> leídas de la BD después del `-u`. Dos despliegues esa noche, y este archivo
> llevaba **dos días y 6 commits** sin tocarse: el detalle, en "Feedback del 10 y
> 11-sep" más abajo.
> **22:51** — la **etapa** le dice al agente qué hacer con el servicio en vez de
> deducirlo de `fold`: `project.task.type` gana `visar_agent_bucket` y
> `visar_agent_label`, con pantalla propia en *Agente WhatsApp → Etapas de
> servicio*. Con `deploy-etapas-11sep.sh`.
> **23:38** — la fila de salida del paso de póliza pasa a llamarse **"Un solo
> servicio"** y viaja con `salida: True`; la pista del paso de servicios avisa
> que por aquí se agenda **hogar**; y los prompts reciben los puntos 1, 2, 3, 5,
> 6 y 7 del segundo feedback (metros con salida, hogar/negocio, **"Visar Homes"**
> en las 14 menciones sueltas, tono cuando no hay fecha u hora, jardín antes del
> precio, y el combo de áreas verdes **solo cuando de verdad aplica**). Con
> `visar_fastapi/deploy/deploy-feedback2-11sep.sh`.
> Anterior: **9-sep-2026 00:10** — **visar_appointment 19.0.2.15.0**
> en producción: acceso completo a `base.group_system` sobre
> `visar.agent.vocabulario`, para que quien ve la pantalla (el menú vive bajo
> Ajustes) pueda guardarla, como en los otros tres modelos de config del agente.
> Fue junto con el arreglo del runtime del día del mes que apartaba esa hora
> (§24 de `85-motor-de-flujos-agendado.md`), con
> `visar_fastapi/deploy/deploy-fecha-del-dia.sh`.
> Anterior: **8-sep-2026 (noche)**, **EN PRODUCCIÓN a las 23:53**:
> el vocabulario del cliente se edita desde Odoo (`visar.agent.vocabulario`,
> **visar_appointment 19.0.2.14.0**) y los prompts se aplican al runtime con un
> botón (**visar_whatsapp_agent 19.0.1.13.0**), versiones leídas de la BD
> después del `-u`. Desplegado con `visar_fastapi/deploy/deploy-vocabulario.sh`.
> **El runtime no se tocó**: `agent_booking_step` ya recibía `keywords`; lo
> único que cambia es de dónde salen del otro lado. Ver "Vocabulario editable y
> botón de aplicar" más abajo y §23 de `85-motor-de-flujos-agendado.md`.
> Anterior: **8-sep-2026**, con dos despliegues más ese día:
> **visar_appointment 19.0.2.13.0** y **visar_whatsapp_agent 19.0.1.12.0**,
> leídos de la BD después del `-u`, no del manifiesto.
> Qué entra el 8-sep: **nombrar una plaga ya es correctivo** —y no saber cuál
> es, también—, porque nadie previene las termitas que está viendo; la opción
> *"No estoy seguro"* del paso de plagas gana vocabulario, que es la fila que
> abre la valoración; **`_VISAR_GRUPO_KEYWORDS`**, para que "tengo termitas"
> conteste *qué servicio necesitas* (nadie escribe "Fumigación"); y el mensaje
> de `agent_resolve_zone` fuera de cobertura, redactado para el modelo, que le
> ordena decírselo al cliente con su CP y dejar de preguntar.
> El vocabulario de cliente que viaja en cada paso está documentado en
> `visar_fastapi/.context/30-odoo-contract.md`; el diario, en §19–§22 de
> `85-motor-de-flujos-agendado.md`.
> Anterior: **7-sep-2026**. Correcciones del segundo test con
> conversación real, **desplegadas en producción** esa noche:
> **visar_appointment 19.0.2.11.0** y **visar_whatsapp_agent 19.0.1.11.0**.
> Qué entra: `_VISAR_LUGARES_KEYWORDS` en un solo sitio y `mide_lugar` /
> `lugares` en los pasos que miden (una medida es de un lugar, y por eso "el
> patio son 27 metros" ya no tumba la cobertura ni contesta los metros de la
> casa); `_agent_rank_days` publica en orden de calendario —el tier elige los
> días, no los ordena—; y `agent_estimate_m2` devuelve `usado`, con qué datos
> salió el número. Detalle en §19 y §20 de
> `visar_fastapi/.context/85-motor-de-flujos-agendado.md`.
> ⚠️ **El `-u` no basta: hay que reiniciar odoo**, o el proceso vivo sigue con
> el Python viejo en memoria y el despliegue no cambia nada pareciendo aplicado.
> Anterior: **4-sep-2026** (diseño de la agrupación por zona del día;
> **no se tocó código**). Releído del manifiesto ese día: **visar_whatsapp_agent
> 19.0.1.10.0**, no 1.8.0 como decía la línea de abajo — el aviso de siempre,
> cumpliéndose otra vez.
> Anterior: **3-sep-2026** (entrada nueva arriba; la cabecera de
> versiones se releyó el 31-ago salvo `visar_appointment`, que sube hoy).
> Anterior: **31-ago-2026** — versiones releídas de los `__manifest__.py`, no de la
> memoria. Versiones **en el árbol de trabajo**: **visar_base 19.0.1.10.0**,
> **visar_fsm 19.0.1.2.0**, **visar_appointment 19.0.2.10.0**, **visar_field_app 19.0.1.26.0**,
> **visar_subscription 19.0.1.5.0**, **visar_crm 19.0.1.3.0**,
> **visar_whatsapp_agent 19.0.1.8.0** (en producción: **1.6.0**). *Las dos cifras estaban mal
> el 4-sep: el manifiesto va por **19.0.1.10.0** y producción también.*
> Entrada anterior: 29-ago-2026, reconciliada contra `git log` tras 8 días sin tocarse. Sus
> versiones ya iban una menor en tres módulos, que es el aviso de siempre: **estas cifras
> caducan en días — reléelas del manifiesto antes de usarlas para nada.**
> Entradas anteriores: 3-ago-2026 (pólizas en producción) · 26-jun-2026 (split en módulos + D-06
> + D-07 parcial + calificación wizard).
> Productos/variantes **no se crean en XML** — se configuran/enlazan en backend + migraciones legacy.

## Feedback del 14 y 15-sep — **EN PRODUCCIÓN** el 15-sep-2026

> Commits de Odoo: `b9a19c2` (14-sep), `d7302ef` (15-sep). Del runtime: `2e40529`,
> `6552d04`. El diario del runtime, en `visar_fastapi/.context/85-motor-de-flujos-agendado.md`
> §27 y `87-reagendar-citas.md` §2.

| Qué se vio | Dónde vive el arreglo |
|---|---|
| Por **agendar**, la pregunta de metros no decía que no saberlos tiene salida (por información sí) | `hint` del paso `interior` |
| La estimación preguntaba **solo recámaras**, y un *"5"* suelto se tomó como **5 m² de construcción** | `estimate_fields[].pregunta` + el runtime pregunta de una en una |
| El botón *"Elegir nuevo horario"* contestaba *"Claro. ¿En qué te ayudo?"* | runtime: el número de la app de campo (`52…`) y el de WhatsApp (`521…`) eran dos conversaciones |
| Si el cliente tocaba el botón pasadas 3 h (la invitación vale 24) pasaría lo mismo | `agent_customer_services` publica `reschedule_granted` |
| El **servicio externo** no decía cuándo reagendó el cliente | `calendar.event._visar_log_reschedule` escribe también en cada tarea, con hora local y como nota interna |
| El 🎉 al apartar hacía creer que la cita ya estaba agendada, sin haber pagado | runtime |

**Dos cosas que conviene saber de aquí.** La nota de la reagenda **ya existía**, pero solo
en la cita del calendario, en UTC crudo y como comentario (que notifica a los seguidores
de la cita, entre ellos el cliente). Y el agente usaba dos estimadores distintos según la
ruta: el de información lo conduce el modelo con el prompt, el de agendar es el paso de
Odoo. Visar creía que eran el mismo, y deberían serlo.

**Comisiones por empleado: estructura lista, regla pendiente — 18-sep 00:05**
(módulo nuevo `visar_commission` 19.0.1.0.0, 31 pruebas). Copia la estructura del
Enterprise `sale_commission` —plan con vigencia y estado, periodos autogenerados,
logros con filtro por producto/categoría y tasa, modos "tasa directa" y "meta con
tabla", ajustes manuales y reporte por persona— pero con **`hr.employee`** en vez de
`res.users`, que es donde el nativo se cierra: su lista de vendedores exige un
usuario interno y su dominio `share = False` descarta hasta los de portal.

Agrega lo que el nativo no hace y Visar necesita: base **"cobrado"** (por REQ-002:
7 de 9 pedidos con venta en campo estaban pagados y sin facturar), atribución por
**línea** (el upsell del técnico vive dentro del pedido del servicio desde
REQ-007), **cerrar** un periodo para que lo pagado no se recalcule, periodicidad
**quincenal** y elegir si se comisiona con o sin IVA.

**La regla sigue sin definirse y el módulo no la inventa**: no trae plan sembrado,
el aviso de la ficha lo dice y `action_approve` se niega sin empleados ni reglas.
Todo lo pendiente es configuración, no código. Medido en la copia de producción:
Pedro Martínez vendió $3,405.15 en campo en 2026 → 10% = $340.52 sobre lo vendido,
$280.18 sobre lo cobrado. **$60 de diferencia según la base** que elija negocio.
Detalle y preguntas abiertas: `.context/36-comisiones-por-empleado.md`.

**Visto de paso:** el plan nativo "Upsell" que ya existe en producción le atribuye
todo a `admin` ($800.69 de comisión en junio) porque `sale_order.user_id` es admin
en esos pedidos. No se tocó.

**El adicional va en el pedido del servicio, no en uno nuevo (REQ-007) — 17-sep 23:15**
(visar_field_app 19.0.1.34.0). Antes cada upsell abría un pedido aparte ligado al original por
`visar_upsell_source_order_id` (S00282→S00264, S00239→S00238, …); administración veía la venta
partida en dos documentos. Ahora la línea entra al pedido original con marcador propio en la
LÍNEA (`sale.order.line.visar_upsell_task_id` + técnico + hora): `task_id` no basta, las líneas
del servicio contratado también lo llevan.

Lo que se midió antes de decidir, para no rediscutirlo:

- Los 10 adicionales vendidos hasta hoy tenían el pedido original **siempre confirmado** y
  **pagado en línea completo**; solo 2 de 9 estaban facturados. Odoo **sí** admite la línea nueva
  en un pedido confirmado e incluso facturado: pasó en producción en **S00264** (línea agregada a
  mano el 8-sep sobre INV/2026/00132 ya emitida) y dejó solo esa línea "por facturar". **No hace
  falta nota de crédito, ni reabrir, ni factura forzada.**
- Una línea **no recurrente** agregada a una suscripción activa se cobra **una sola vez**, no cada
  ciclo (medido en `visar-test`, tres ciclos). El motivo que justificó el pedido aparte era falso.
  Pero se cobra en la **siguiente** factura del ciclo (puede ser dentro de un año) y el técnico
  necesita cobrar en la puerta: **las pólizas conservan el pedido aparte** (decisión de negocio).

La factura del cobro en sitio se limita a las líneas del adicional
(`sale.order._get_invoiceable_lines` + contexto `visar_upsell_solo_lineas`): el pedido original
casi siempre llega pagado **pero sin facturar**, y facturarlo completo delante del cliente le
cobraría de nuevo el servicio. El enlace de pago cuelga de esa factura, nunca del pedido. El sello
de efectivo se mudó del pedido a la **tarea** (un pedido puede acumular varias visitas). Los 10
adicionales históricos **no se tocan**: `_visar_upsell_lines` los lee por pedido aparte. El precio
se fija explícito al del catálogo (lista de la ZONA) con descuento 0, porque el pedido original
puede traer otra lista. Verificado end-to-end en `visar-test` con la app real: pedido S02184 pasó
de $8,280 a $8,380, factura INV/2026/00128 por **solo $100**, la línea del servicio sigue por
facturar y el PDF firmado totaliza $100.

**Servicios vendibles en campo (REQ-006) — 17-sep 21:59** (visar_field_app 19.0.1.33.0,
`c784f54`/`4ae2b8a`, `deploy-upsell-17sep.sh`). El catálogo **nunca** filtró por tipo: ofrecer una
poda detectada en sitio es dato maestro, no código — se marca `visar_upsell_ok` en la ficha del
producto (Ventas ▸ Productos, fila de casillas). Lo que sí se exigió por código es poder COBRARLO
ahí mismo: `invoice_policy='order'` (facturar por entrega deja el pedido en "Nada que facturar" y
al técnico sin liga de pago) y, con REQ-007, `service_tracking='no'` (un producto que genera tarea
abriría un servicio nuevo sin fecha ni técnico en cuanto la línea toca el pedido confirmado).
**Bug corregido:** la casilla se había insertado entre `sale_ok` y su `<label>`, y la ficha salía
con 4 casillas y 3 textos. Pendiente de negocio: decidir qué servicios se marcan (los cinco
especializados son los candidatos).

**Lista de precios: sin default y obligatoria al confirmar (REQ-004) — 17-sep 21:17**
(visar_base 19.0.1.13.0, visar_appointment 19.0.2.22.0, `de2299c`, `deploy-req004-005-15sep.sh`).
`property_product_pricelist` NO se guarda: Odoo la calcula y sin una propia ponía la primera
lista activa sin grupo de países — "VISAR Zona A" (sequence 11) — así que toda cotización nacía
con el precio de otra zona; reordenar las listas no lo arregla. Fuera del sitio web solo se
muestra la lista ELEGIDA (en el sitio web no se toca: la tienda y el wizard la necesitan, y ahí
la zona del CP pone la correcta). `_confirmation_error_message` la exige al confirmar, con hook
`_visar_requiere_lista_de_precios`; el upsell de campo queda exento y un **pago en línea confirma
igual** y deja nota (el nativo llama `action_confirm` sin atrapar errores: bloquear ahí dejaría al
cliente cobrado y sin pedido, REQ-002). "Actualizar precios" queda visible mientras haya lista y
líneas, con aviso de que reemplaza descuentos. **Odoo no deja cambiar la lista de un pedido
confirmado**: las 6 pólizas activas sin lista se quedan así, con nota en su chatter diciendo qué
lista les toca al renovar. Efecto: toda alta manual (pólizas incluidas) exige elegir lista.

**"Hoja de trabajo — Completada" dice la verdad (REQ-005) — 17-sep 21:17**
(visar_field_app 19.0.1.31.0, `51262c9`). El indicador es NATIVO y se enciende con
`worksheet_count`, que solo mira si EXISTE el registro; la app lo crea al pulsar "Comenzar
servicio" (necesita id para sembrar las áreas obligatorias), así que un servicio recién empezado
salía con su hoja completada. Ahora el conteo exige el sello `visar_worksheet_saved_at`, que solo
escribe "Guardar hoja de trabajo" (el borrador no). Se arregló en el CÓMPUTO y no en la vista
porque el mismo conteo alimenta los botones nativos de firmar/enviar reporte y la hoja del
**portal del cliente**. En producción 12 tareas dejaban el verde; a las 3 ya cerradas con hoja
llena se les puso el sello a mano (nota en su chatter), las otras 9 están en ejecución. Hacia
adelante no se repite: la ruta de cierre de la app exige la hoja guardada.

**Visto y no corregido:** en cada arranque de Odoo el log repite
`Missing model x_labor_de_jardineria` (desde el 15-sep, 2 líneas por arranque). Huele a resto de
un modelo de Studio borrado; no rompe nada visible.

**Vendedor (empleado) en la cotización — 15-sep 21:50** (visar_field_app 19.0.1.29.0,
`d710c9b`, `deploy-vendedor-empleado-15sep.sh`). `sale.order.visar_salesperson_employee_id`,
Many2one a `hr.employee`, **opcional**, en *Otra información* debajo de Vendedor. `user_id` no
se toca (lo usan `sale_commission` y las reglas de "solo mis documentos"). Un vendedor sin
permisos de RH puede elegir empleados por el fallback público de `hr.employee` (probado).
Opcional porque las cotizaciones del agente y del agendado web se confirman solas al pagar.
Pendiente aparte: que alimente comisiones.

**"No me interesa" cierra el recontacto — 15-sep, noche** (visar_whatsapp_agent
19.0.1.21.0 + runtime `9bbc1f0`, `deploy-declino-15sep.sh`). El motivo `declino`
existía y nadie lo mandaba, y `agent_drop_followup` no tocaba leads ya *Enviado* ni
detenía un aviso *En cola*. Detalle en `visar_fastapi/.context/86-recontacto-de-leads.md`.

**HTML escapado en el chatter — corregido y desplegado el 15-sep 20:33**
(visar_field_app 19.0.1.28.0, visar_fsm 19.0.1.4.0, `deploy-notas-html-15sep.sh`). Dos
notas se posteaban como `str` con `<b>` adentro y Odoo 19 las escapa: *"por
&lt;b&gt;Pedro Martínez&lt;/b&gt;"* (`_visar_flag_reschedule`) y *"vuelve a
&lt;b&gt;Programado&lt;/b&gt;"* (`_visar_back_to_scheduled`). Ahora van como `Markup`
(el nombre del técnico se sigue escapando). En la BD se corrigieron las **11** notas ya
guardadas —3 de ellas de la nota vieja "Cita reagendada por el cliente", reemplazada esa
misma madrugada—, por id y solo `<b>`, `</b>`, `<br/>`. **Regla:** todo `message_post`
con etiquetas va con `Markup`; los `&lt;` que quedan en la BD (errores de urllib3 en los
avisos fallidos) son texto y están bien escapados.

## Feedback del 10 y 11-sep — **EN PRODUCCIÓN** el 11-sep-2026

> Lo que este archivo no registró durante dos días. Seis commits del lado de
> Odoo: `eaba145`, `c8cf394` (10-sep), `6cda362`, `49638bb`, `d4ad9b6`,
> `f91572e` (11-sep).

| Qué se vio | Dónde vive el arreglo |
|---|---|
| *"Si no quieres agregar nada, dime que no y seguimos"*: instrucción de máquina, no conversación | paso `extras` sin `hint`; la fila de salida se marca `oculta` y se contesta pero no se pinta |
| *"Solo este servicio"* **contrataba** una póliza: la raíz "servic" del plan *"3 servicios"* puntuaba y la fila de salida solo la tenía en su descripción | `_VISAR_POLIZA_KEYWORDS` — frases de varias palabras, que `_frase_unica` lee antes que el conteo de raíces |
| El recontacto de leads fríos no salía | `visar.followup.config` + `crm.lead`: uno por silencio, y la escalada solo cierra **el suyo** |
| Colgar el chat contaba como "declinó" y cerraba el recontacto de por vida | motivo `cerro`, fuera de `DESCARTES_DEFINITIVOS` |
| Un servicio **pendiente con fecha futura** salía bajo "servicios anteriores" | `project.task.type`: `visar_agent_bucket` / `visar_agent_label` |
| *"No, gracias"* no decía qué pierde quien no contrata póliza | fila **"Un solo servicio"** + `salida: True` + su etiqueta en el vocabulario |
| Quien escribía por su **negocio** se enteraba al final | `hint` del paso `services` |

**Las dos decisiones que valen para más adelante.**

*La etapa manda, no la deducción.* Se deducía de `stage_id.fold`, y la etapa
nativa `planning_project_stage_4` se llama *"Cancelled"* en Odoo mientras Visar
la usa como **"Incidencia — Reprogramar"**. Se resolvió con **configuración en la
etapa**, no dándole la vuelta al `fold`: así el kanban de operaciones y la app de
campo no se enteran, y el valor de fábrica (`auto`) conserva el comportamiento
anterior. En producción solo la etapa 20 está configurada; las otras 26 siguen en
`auto`.

*Cuál es la salida lo dice Odoo.* El runtime la reconocía corriendo un **detector
de negativos sobre la etiqueta**, así que renombrarla a algo que no es un "no"
dejaba el paso sin respuesta válida. Ahora viaja `salida: True` y el copy se
edita aquí. ⚠️ **Si alguien renombra esa fila, el nombre nuevo tiene que entrar en
`_VISAR_POLIZA_KEYWORDS`**: medido, *"un solo servicio"* elegía el plan
recurrente —la misma raíz "servic"— y contestar copiando lo que se ve en pantalla
es la forma más natural de contestar.

**Verificación.** 317 pruebas del módulo (4 nuevas), con los 2 fallos de
`test_partner_dedupe` que **ya fallaban el 10-sep**, antes de este trabajo, y que
no toca. 629 del runtime. Cero ERROR/CRITICAL en el log real de Odoo en la
ventana del despliegue. Suite de producción 105/106 (el `SEC-08` de siempre).
Marcha atrás: `/var/backups/visar-db_feedback2-11sep_2026-09-11-2338.sql.gz` y
los prompts aparte en `/opt/visar_fastapi/var/prompts-backup-20260911-233830.tsv`.

## Vocabulario editable y botón de aplicar — **EN PRODUCCIÓN** el 8-sep-2026

> **visar_appointment 19.0.2.14.0** · **visar_whatsapp_agent 19.0.1.13.0**,
> desplegados a las **23:53** con `visar_fastapi/deploy/deploy-vocabulario.sh`.
> 292 pruebas de los dos módulos en `visar-test`, con los **2 fallos previos**
> de `TestBookingDedupe` (son de logging en `controllers/appointment.py`, nada
> que ver). 16 pruebas nuevas.
>
> Verificado **contra producción, no contra el fake**: las 7 ranuras existen y
> `services` resuelve a `['fumigacion', 'corte']`; una fila de consultor llega
> al paso y se la llevan los pasos que miden (`interior` no la hereda); "patio"
> no se duplica; una clave mal escrita no se guarda; el formulario enseña lo
> mismo que se publica; y el botón "Aplicar ahora" alcanza al runtime vivo. La
> verificación escribe una fila y hace **rollback** — se comprobó desde otra
> conexión que la tabla quedó vacía. Aceptación de solo lectura: **105/106**.

### Por qué

Los comentarios del código ya decían lo que se quería: *"es copy de negocio: lo
edita un consultor cuando el chat le enseñe una palabra que no está"*. No era
verdad. `_VISAR_LUGARES_KEYWORDS`, `_VISAR_GRUPO_KEYWORDS` y los literales
sueltos de `motivo`, `plagas` y `extras` eran constantes de Python, y cambiar
una palabra costaba: editar, bump, `-u`, reiniciar odoo, reiniciar runtime.

**Tres de los cuatro fallos del 8-sep fueron una palabra que faltaba**, y cada
uno costó un despliegue. Esta es la clase de bug más frecuente del agente y la
única que no necesitaba a un desarrollador.

### Qué entra

**`visar.agent.vocabulario`** (modelo en `visar_appointment`, pantalla en
`visar_whatsapp_agent` → Agente WhatsApp → Vocabulario):

    lo que el agente ve  =  las palabras del código  +  las de la pantalla

- **Solo suma, nunca quita.** El código es el piso: es lo que afirman las
  pruebas y lo que sobrevive a una base nueva. Quitar una palabra desde una
  pantalla dejaría el repositorio verde y producción haciendo otra cosa.
- **No se puede guardar una opción que no existe.** Una clave mal escrita se
  guardaría sin protestar y no haría nada nunca — el peor final, porque parece
  que funciona. Ahora falla al guardar y enseña las claves válidas del paso.
- **No se repite.** El puntaje CUENTA keywords (`classify._scores`), así que
  una pista duplicada vale dos puntos por un solo dato. La comparación es la del
  runtime: sin acentos y en minúsculas.
- **El campo "Lo que el agente reconoce"** enseña la fusión ya hecha. Un campo
  que solo dijera "guardado" estaría verde sin estarlo.
- **Los pasos que MIDEN heredan lo de `cobertura`.** Es la propiedad que costó
  el bug del 7-sep: enseñarle "azotea" a Exterior tiene que llegar también a
  quien sabe que *"la azotea son 30 metros"* no contesta los metros de la casa.
- **Se aplica en el siguiente paso, sin reiniciar nada**: `agent_booking_step`
  es una llamada RPC viva, no una caché.
- **`poliza` estrena ranura vacía.** Su fila "No, gracias" nunca tuvo ni una
  palabra; `extras` necesitó un despliegue para aprender "nel". Ahora se enseña
  desde la pantalla.

**Botón "Aplicar ahora"** (`visar.agent.runtime.mixin`, en las tres formas de
prompt y en la config del LLM): fuerza `POST /debug/runtime/refresh` y
`/debug/catalog/refresh` en vez de esperar los 15 minutos del TTL. Cuenta lo que
pasó: si el runtime no responde, es un error con el motivo; si vence el tiempo,
dice que *pudo* aplicarse, porque pudo. La dirección va en el
`ir.config_parameter` `visar_whatsapp_agent.runtime_url`.

> ⚠️ **Odoo se llama a sí mismo, en redondo**: el refresco hace que el runtime
> pida la config a Odoo por RPC mientras este proceso atiende el clic. Con
> `workers = 2` hay sitio, y por eso el tiempo de espera es corto.

### Lo que falta

- Las medidas por grupo (`group_*`) no tienen ranura: sus claves son ids de
  dimensión, que cambian entre bases.
- `poliza` tiene la ranura vacía a propósito: alguien tiene que escribirle las
  palabras de "No, gracias" desde la pantalla (es lo que `extras` aprendió a
  base de un despliegue).

## Agrupación por zona del día — construida **y EN PRODUCCIÓN** el 4-sep-2026

> **Desplegada a las 23:06** con `visar_fastapi/deploy/deploy-agrupacion-zona-dia.sh`:
> backup (16 MB), `-u visar_base` **19.0.1.10.0 → 19.0.1.11.0**, reinicio, y primera
> corrida del precalentado (**200 de 1080** centroides; el cron horario hace el resto).
> Odoo responde 200, el runtime no se tocó, y no hay ni un ERROR en el log.
>
> **Primer efecto medido sobre la agenda real**, con destino en el Centro de Monterrey:
> el **7-sep pierde sus 7 horarios** y el **8-sep queda marcado `tier=1`** (preferido).
> Y el porqué es exactamente el caso que motivó la regla: el 7-sep ya tiene **tres
> paradas repartidas entre el norte (25.7047, -100.3449) y el sureste
> (25.5744, -100.2484)** —unos 19 km— así que ese día ya está partido y deja de
> absorber trabajo de una tercera zona. Lo agendó una persona desde el backend, que es
> el camino que **no** pasa por el filtro.
>
> ⚠️ **Marcha atrás inmediata y sin reiniciar:** `visar.travel.cluster_minutes = 999`.

- [x] **Agrupación por zona del día** — el presupuesto entre paradas protege el traslado de la
      cita *vecina*, no el *día*: 9:00 en San Nicolás y 12:00 en García se ofrecían los dos.
      Visar quiere los servicios de un día cerca unos de otros para **caber más servicios al
      día**. Diseño en **§5.7 del doc 33**, decisión en `40-decisions.md`, encargo en
      [`briefs/2026-09-04-agrupacion-por-zona-del-dia.md`](./briefs/2026-09-04-agrupacion-por-zona-del-dia.md).
      Se **suma** al presupuesto, no lo sustituye. Umbral **derivado**:
      `visar.travel.minutes + 10` → 30 min. Coste previsto: **cero llamadas nuevas** a Mapbox.
- [x] ~~⛔ Bloqueador: los centroides de CP~~ — **resuelto, y no era donde parecía.** El
      respaldo del **destino** ya existía y se auto-puebla; las 1080 filas sin centroide eran una
      rama sin pisar, no un fallo. El agujero estaba en **`_visar_travel_stop_coords`**, que no
      tenía respaldo ninguno: ahora cae al centroide del CP del partner, con `geocode=False` para
      no salir a la red al pintar horarios, y un cron precalienta en lote (200 por corrida).
      Verificado en vivo: Monterrey, García y Apodaca dan tres puntos distintos y bien puestos.
- [x] **Corregido en el doc 33:** el §10.10(c) decía que la rama que gasta llamadas de Matrix no
      llega a correr porque el técnico no tiene dos paradas el mismo día. **Falso desde hace
      semanas** — recurso 1 con 10 paradas el 11-ago y 9 el 1-sep, y `visar_travel_cache` con 164
      filas escritas hasta el 3-sep. La factibilidad de traslado **sí** está llamando a Mapbox en
      producción.

> **Medido en servidor el 4-sep** sobre las 102 líneas de reserva de 2026: dispersión intra-día
> mediana **8.3 km**, p75 **16.7 km**, máximo **30.8 km**; **el 43% de los días multi-parada
> superan 15 km**. La regla nueva prohíbe cerca de la mitad de los días que Visar arma hoy — es
> un **cambio de política de operación**, no un ajuste del filtro. Y en los próximos 30 días solo
> hay **3 días-técnico con paradas**: con un técnico el cuello de botella sigue siendo la
> demanda, así que el umbral nace flojo y configurable.

## Hecho — 3-sep-2026 — **DESPLEGADO el 4-sep-2026**

> *Este encabezado decía "commiteado, **sin desplegar**" y dejó de ser cierto el 4-sep.
> Comprobado ese día: los **siete** módulos `visar_*` tienen en `ir_module_module` la misma
> `latest_version` que su `__manifest__.py`, el servidor de `visar-db` rearrancó a las **18:37**
> y el runtime a las **18:52** — después de todos los commits de código del día. El despliegue
> lo hizo `aba303e` del runtime (*"script del recorrido del 3-sep: módulo, prompts y runtime, en
> orden"*).*


- [x] **La oferta de póliza empieza por el ahorro** (`visar_appointment`
      **19.0.2.10.0**). Los cuatro planes se llaman igual (I-15), así que lo
      único que los distinguía era una línea de tres cifras encadenadas: cuatro
      filas iguales y doce números. Ahora: *"Ahorro del 22% · $570.00 al mes ·
      hoy pagas $1,710.00 por 3 meses"*, y el paso lleva pista de qué **es** una
      póliza. Detalle en `35-polizas.md`.
- [x] **Las pistas del chat dejan de mandar pulsar cosas.** *"Selecciona"* y
      *da click en "{done}"* describían un widget que dejó de existir con las
      listas de WhatsApp. Estas opciones solo las usa el chat —el wizard web
      tiene sus propias plantillas—, así que no había dos canales que contentar.
- [x] **"Tengo alacranes" ya contesta que es correctivo.** `keywords` en el paso
      del motivo. Nadie llega diciendo "correctivo": llega diciendo lo que tiene,
      y volver a preguntárselo es la queja más repetida del chat. Con esto el
      runtime puede darlo por contestado al entregar la conversación.
- [x] **El prompt base volvió al repo.** `prompt-agente-informacion.txt` estaba
      **sin versionar** y había divergido 8 000 caracteres de la base: reglas
      distintas, no formato. Ver el aviso en `34-prompt-agente-informacion.md`.

> **Falta desplegar**: `sudo bash /opt/visar_fastapi/deploy/deploy-sin-numeros.sh`
> (módulo + prompts en `visar-db` + reinicio del runtime). Probado en
> `visar-test`: 254 tests de los dos módulos, con los **2 fallos previos** de
> `TestBookingDedupe` que ya venían de antes (comprobado con y sin estos
> cambios). Los prompts ya están aplicados en `visar-test`.

## Hecho — 31-ago-2026

- [x] **La póliza combo ya es UNA visita** (`visar_fsm` 19.0.1.2.0, `visar_subscription`
      19.0.1.5.0). Era la "fase 2" que quedó abierta el 13-ago en `40-decisions.md`: la venta
      puntual consolidaba y la póliza no, así que un cliente con fumigación + áreas verdes
      recibía **dos visitas a la misma hora** cada periodo (17 pólizas así en la BD; S00246 con
      12 tareas donde iban 6). Se veía como "por la web falla y por WhatsApp no" — es
      casualidad: ninguna orden del agente había sido póliza, y la consolidación nunca dependió
      del canal. La regla vive ahora en `project.project._visar_effective_projects`, compartida
      por los dos caminos. Detalle en `35-polizas.md` y `40-decisions.md`.
      **Desplegado en `visar-db` el 31-ago-2026 23:07** (`-u visar_fsm,visar_subscription`,
      sin errores en el log, servicio reiniciado). Respaldos previos en
      `/var/lib/odoo/backups/visar-db-{pre-combo-poliza,filestore-pre-combo-poliza}-20260831-230547.*`
      (BD + filestore: la migración toca esquema). La migración enlazó **147 visitas**
      existentes a su línea. Antes: `visar-test` con 39 tests verdes y un E2E con los
      productos reales. **Las 12 tareas de S00246 siguen ahí**: la consolidación aplica a
      lo que se genere desde la próxima factura pagada.

## Hecho — desde el 20-ago, y que este archivo no registraba

*Añadido el 29-ago al reconciliar. Ocho días y 18 commits sin que nadie tocara el roadmap.*

- [x] **Se fueron los menús y los botones** (`6ada85c` + `a2a6865` del runtime). El cuestionario
      se contesta escribiendo y **el LLM enruta desde el primer mensaje**. La consecuencia para
      este módulo: `ruta` dejó de decidir qué handler corre y pasó a decidir **qué memoria
      recibe el modelo**, que es lo que hizo falta rediseñar la consola.
- [x] **Prompt base + memoria por ruta** (`ccdcd59`, `2018bb8`, `2d8ee13`). El base se inyecta
      siempre y cada ruta añade la suya. `2d8ee13` es de los que valen releer: un `try/except`
      no protege una transacción de Odoo — hacía falta un savepoint.
- [x] **El chat no sabía que el combo existía** (`332ee5b`) y cotizaba de menos.
- [x] **Consola del Agente WhatsApp** (`19.0.1.6.0`, desplegada en producción el 28-ago,
      **sin commitear**): rutas y prompt base en pantallas separadas, con disparador,
      herramientas, garantías y un `estado` en badge. Ver §"Consola" de
      [`27-whatsapp-agent.md`](./27-whatsapp-agent.md).
- [x] **Ronda de QA manual** (28-ago, Luis Ángel Ríos Jasso, 13 hallazgos). Cerrados los de
      código: dos caminos de **venta silenciosa** —una pregunta que nombraba un servicio lo
      seleccionaba, y una queja sobre un técnico vendía el tramo de valoración de 800 m²—
      llegaban a la pantalla de pago sin que el cliente eligiera nada. Quedan los de prompt
      (1.1, 2.1, 2.2, 2.3, 5.1) y el 6.3 de arriba.

### ~~En el árbol de trabajo, sin desplegar~~ — **desplegado el 4-sep-2026**

*Actualizado el 31-ago-2026; **cerrado el 4-sep-2026**, cuando las dos entradas de abajo
llegaron a producción (ver el encabezado del 3-sep).*

> ⚠️ **Dos afirmaciones de esta sección eran falsas y se corrigen aquí:**
> 1. *"El repo de Odoo no es un repo git en este checkout"* — **sí lo es**: `/opt/custom` tiene
>    `.git` y `git log` funciona. "Commiteado" **sí** es una propiedad observable de este lado,
>    y de hecho es como se reconcilió este archivo.
> 2. Las dos entradas seguían marcadas `[ ]` (sin desplegar) tres días después de estarlo.

- [x] **Reagendar una cita ya pagada** (`19.0.1.8.0` + `visar_appointment 19.0.2.8.0` +
      `visar_base 19.0.1.10.0`): `agent_reschedule_days` / `_slots` / `_confirm`, el contexto
      `visar_ignore_event_id` para que una cita no compita consigo misma, y **Ajustes → Visar →
      Reagendar**. Cancelar **no existe** a propósito. Diseño en
      `visar_fastapi/.context/87-reagendar-citas.md`. Validado en `visar-test` (142 pruebas del
      módulo) y con **443** del runtime.
- [x] **Recontacto de leads fríos** (`19.0.1.7.0`): `visar.followup.config`, campos y cron en
      `crm.lead`, buzón `visar.wa.lead.message`, y `agent_track_interest` /
      `agent_drop_followup`. Diseño en `visar_fastapi/.context/86-recontacto-de-leads.md`.
      Validado en `visar-test` (127 pruebas del módulo).
- [x] ~~**Ajustes → Visar → Agendado**~~ — **construida**, y creció: la pantalla existe en
      `visar_base` (`models/res_config_settings.py` + `views/res_config_settings_views.xml`) con
      **tres** bloques, no dos — *Configuración Visar*, **Agendado** (minutos de apartado
      `visar.slot_hold_minutes` y traslado entre servicios `visar.travel.minutes`) y
      **Reagendar** (`visar.reschedule.min_hours`, `visar.reschedule.max_times`), todos con
      `@api.constrains` de cordura. **Desplegada el 4-sep-2026**, como el resto de esta sección.
      *(Esta línea decía `visar_base 19.0.1.8.0`; el módulo va por 19.0.1.10.0.)*

## Hecho — Agendado completo por WhatsApp (19/20-ago-2026) — **EN PRODUCCIÓN**

Diseño, estado detallado y las 15 decisiones en
[`33-whatsapp-agendado-design.md`](./33-whatsapp-agendado-design.md). El lado runtime está en el
`.context/` de `visar_fastapi` (`85-motor-de-flujos-agendado.md`).

**Hoy un cliente puede reservar escribiendo por WhatsApp**, de punta a punta y sin salir del chat
salvo para pagar. 17 de los últimos 22 commits del repo son esto.

- [x] **Apartado de horario** (`visar.slot.hold`, 10 min configurables) descontado en
      `_get_resources_remaining_capacity` — protege **los dos canales** con un solo cambio.
- [x] **El cuestionario bajó al modelo** (`appointment_wizard_flow.py`, ~1,405 líneas): podar,
      secuenciar, normalizar y ofrecer. El controlador web pasó de 1,961 a 1,664 líneas y
      **delega**. Su comportamiento no cambió.
- [x] **`agent_booking_step`** — el cuestionario entero por RPC, sin escribir nada.
- [x] Días y horarios (`agent_available_days`, `agent_day_slots`) con **hora local**, no UTC.
- [x] Reserva, pedido y **liga de pago** (`agent_prepare_booking`, `payment.link.wizard`).
- [x] **La liga vive y muere con el apartado** — al caducar, deja de cobrar.
- [x] **Corregir UN paso** desde la revisión, re-preguntando solo lo que dependía de él.
- [x] Retomar una conversación estacionada **sin volver a pedir la dirección**.
- [x] Multi-selección contestando por escrito; menú de "¿qué quieres cambiar?".
- [x] Avisos salientes de reserva por buzón (`visar.wa.booking.message`) sobre el mixin
      compartido `visar.wa.outbox.mixin` de `visar_base`.
- [x] **Hand-off humano** con lead + nota en el chatter + actividad asignada.
- [x] Verificado en servidor en **cuatro rondas** (ver §10.2–§10.4 del doc 33) y corregido con
      tres fallos salidos del **primer uso real como cliente** (§10.6).

### Lo que NO cierra todavía

*Revisado el 29-ago-2026 contra el log y contra la BD de producción. Los dos ⛔ que había aquí
se cerraron el mismo día en que se escribieron y nadie los tachó.*

- [x] ~~⛔ La rama de valoración no llega a horarios (§10.7 / I-17)~~ — **cerrada**
      (`b9e7669`, `044e256`, y `b7a2aec` del lado runtime). El aviso de valoración es ahora
      **un paso más del cuestionario**, que Odoo devuelve como `kind: 'single'` con su texto y
      su precio. Quien reporta termitas agenda por WhatsApp.
- [x] ~~⛔ Factibilidad de traslado sin construir~~ — **construida**
      (`7369898`, `95792c3`, `584d2b1`, `cd888bb`, `d8ed83b`):
      `visar_appointment/models/visar_travel_feasibility.py`, 585 líneas. Es un **presupuesto
      entre paradas**, no un radio: 20 min (`visar.travel.minutes`) más el hueco que ya haya.
      `depart_at` nace **apagado** tras el 422 de la beta de Mapbox.
- [x] ~~CP temprano (§4.0), la salida probable para I-17~~ — **sin objeto**: I-17 se resolvió
      por otro camino. Si se retoma, que sea por sus propios méritos, no por I-17.
- [ ] **Equipo CRM de WhatsApp sin líder ni miembros** → el hand-off escala **a nadie**.
      *Comprobado en producción el 29-ago: `crm_team` id 5, `user_id` vacío y **0 miembros**.*
      Sigue siendo dato, no código, y sigue siendo lo que separa "prometemos que le contactan"
      de que le contacten.
- [ ] **Plantillas de Meta sin aprobar** → los avisos salientes están siempre fuera de la ventana
      de 24 h: se encolan, dan 502 y caducan. *Comprobado el 29-ago: no hay ninguna
      `WA_TEMPLATE_*` en el `.env` del runtime.*
- [ ] **Stripe**: el pago sigue simulado (proveedor Demo).
- [ ] **I-15** — cuatro planes de póliza se llaman igual. *Comprobado el 29-ago: siguen los 4
      "Póliza Mensual" + 1 "Monthly" activos.* Es dato, no código. **Relacionado y nuevo:** hay
      además dos planes anuales activos, "Suscripción anual" (id 11) y "Suscripción anualV2"
      (id 13) — el nombre interno se le enseñó a un cliente en la QA del 28-ago (hallazgo 6.3) y
      renombrarlo crea un duplicado. **Falta decidir cuál es el vigente.**
- [ ] **I-16** — nadie se entera de que un servicio se cayó: `visar-fastapi` estuvo ~2 días
      muerto sin que nadie lo notara.

## Hecho — Pólizas: cobro adelantado + paso en el wizard (ago-2026)

Detalle completo en [`35-polizas.md`](./35-polizas.md).

- [x] El cobro de 2 meses por adelantado es una **línea real del pedido**, no un
      multiplicador al facturar. Antes el carrito cobraba 1 mes, la factura salía por 2 y,
      al no quedar pagada, **no se generaba ninguna visita**.
- [x] 6 listas (zona × plan) con 2 reglas globales sustituyen a las 78 por variante.
      Los precios siguen viviendo solo en las listas de zona.
- [x] Paso de póliza en el wizard tras *¿Deseas agregar algo más?*; se retira la
      contratación desde `/shop/...` (productos 30 y 31 siguen sin publicar).
- [x] La **primera visita hereda fecha y técnico** de la cita reservada; las demás nacen
      sin agendar.
- [x] El precio anunciado es **solo el servicio recurrente**; los add-ons son cargo único.
- [x] Migración: 14 pedidos con anticipo, 41 omitidos por tener factura posteada; planes
      anuales bajados a 1 periodo (cobraban dos años de entrada).
- [x] 15 tests en verde contra copia de la BD real.
- [x] Desplegado en `visar-db` el 3-ago-2026 (backup previo en `/var/lib/odoo/backups/`).

### Bugs de producción encontrados de paso

1. **Carrito de compra única se confirmaba como suscripción** —
   `_visar_clear_previous_booking_lines` no limpiaba `order.plan_id`. **Corregido.**
2. **S00087/S00088 con dos facturas de 2 meses** y **S00084/S00085 con una de 1 mes** —
   causa eliminada, pero las facturas ya están posteadas: **decisión de finanzas pendiente**.

### Pendiente de esta tanda

- [ ] Repuntar el botón *Contratar póliza mensual* de la portada (editor web, no código).
- [ ] Definir si la Póliza Bimestral se queda a precio de paridad o lleva descuento.
- [ ] Cerrar los 4 pedidos con factura mal emitida con finanzas.

## Hecho — D-03 (inversión de flujo + filtrado de técnicos)

- [x] Modelos `visar.zone`, `visar.service.tier` (ahora en `visar_base`).
- [x] Extensiones `product.template`, `appointment.type`, `appointment.resource`, `calendar.event`.
- [x] Controlador: intercepción del flujo, página `prequalify`, cálculo de elegibles, persistencia de respuestas.
- [x] Vistas backend (producto, tabulador global, zona, recurso, cita) y frontend.
- [x] Seguridad (`ir.model.access.csv` en `visar_base`).
- [x] Validado end-to-end en BD `visar_local` (flujo legacy 1 servicio).

### Bugs encontrados y corregidos durante la instalación

1. **`__manifest__.py` — dependencias faltantes:** `product` y `hr`.
2. **`is_auto_assign = False`** en tipos de cita → pantalla intermedia no deseada. Fix: `is_auto_assign=True`, `assignment_method='auto'`.
3. **`TypeError: extra_calendar_event_params`** — firma del override alineada al core Odoo 19.

## Hecho — D-04 + D-05 (wizard multi-servicio + precio)

### Modelos / datos (`visar_base` + `visar_appointment`)

- [x] `visar.service.group` + `visar.service.dimension` — wizard configurable desde backend.
- [x] `visar.combo.rule` — reglas de combo configurables.
- [x] `product.template`: `visar_is_service`, `visar_is_valuation`, `visar_dimension_id`, campos legacy.
- [x] `visar.service.tier`: `name`, `is_valuation`, `is_free`, `combo_discount_eligible`.
- [x] `visar.zone`: `pricelist_id`.
- [x] `calendar.event`: `visar_booking_items` (JSON); conservado `visar_m2` (legacy D-03).
- [x] `appointment.type`: `visar_is_master`, **`visar_flow`**, helpers maestro/valoración.
- [x] Catálogo legacy enlazado por `migrations/19.0.2.0.7/post-migrate.py`.
- [x] Pricelists por zona + ítem fijo $500 Valoración.
- [x] Dependencia `website_appointment_sale` para SO multi-línea en checkout.

### Wizard + controlador (`visar_appointment`)

- [x] `views/wizard_templates.xml` — servicios / substeps / dimensiones / **calificación** / zona + aviso valoración.
- [x] Rutas wizard completas incl. **`…/wizard/calificacion`**.
- [x] Bifurcación valoración desde wizard (aviso → flujo valoración directa).
- [x] Sesión `visar_booking` con `mode=wizard|valuation`, `items`, `service_pools`, selecciones calificación.
- [x] Resolución multi-variante, pools, agenda multi-técnico.
- [x] Punto de entrada `/appointment` con dos tipos (`visar_flow` valuation/wizard).

### Cita multi-línea + pago

- [x] `_redirect_to_payment` — N `_cart_add` (wizard) o una línea $500 (valoración).
- [x] Valoración → **una** línea $500 (dedupe).
- [x] Pricelist de zona en SO.
- [x] Sidebar precio multi-línea (`visar_quote`).

## Hecho — D-06 (add-ons obligatorios) — `visar_base`

- [x] Modelo `visar.product.optional.line` (`is_mandatory`, `quantity`).
- [x] Sincronización Opción A con `optional_product_ids` (onchange + reconcile en create/write).
- [x] Vista tabla en producto (invisible si M2m vacío).
- [x] Inyección en `_visar_build_sale_lines` (checkout web) con **suma** de cantidades si mismo add-on en varios servicios.
- [x] Auto-inyección en backend (`sale.order` / `sale.order.line`) para pedidos manuales.
- [x] Flujo valoración **no** agrega add-ons.

## Hecho — Calificación wizard + roedores — `visar_appointment`

- [x] Paso wizard **calificación**: plaga/preventivo, roedores, tipo de plaga (opcional).
- [x] Producto `visar_is_roedores` + parámetro `visar.roedores_product_tmpl_id`.
- [x] Si `roedores=si` → línea producto roedores + add-ons obligatorios del producto roedores (p. ej. 3 estaciones).
- [x] Preguntas nativas en `data/visar_questions_data.xml`.
- [x] Respuestas inyectadas en Questions & Answers vía `_visar_enrich_answer_inputs` (zona, m²/rangos, calificación).
- [x] Preguntas **desvinculadas** del formulario nativo de cita (migración 19.0.2.0.12).

## Parcial — D-07 (FSM) — `visar_fsm` (v19.0.1.0.1)

- [x] `post_init_hook` — proyectos FSM + `service_tracking` + `project_id` en productos.
- [x] Override `_timesheet_service_generation` — **una tarea por proyecto**.
- [x] Add-ons asignados a tarea del servicio que los declara (`task_id` en línea SO).
- [x] Enriquecimiento: técnico (`appointment.resource` → `user_ids`) y fechas desde `calendar.event`.
- [x] `calendar.event.visar_fsm_task_ids` (computed).
- [x] **UI tarea FSM:** ocultar `sale_line_id` nativo; mostrar `visar_sale_order_id` (orden completa de la cita).
- [x] **Worksheet / checklist / fotos / firma** — resuelto vía `visar_field_app` (app web sobre
      worksheet NATIVA, no `worksheet.template` propias). **Ampliado 07-jul-2026:** renderiza o2m
      (tarjetas), m2m (casillas), imágenes por línea, widgets, pestañas invisibles, ayuda ⓘ, borrado
      de fotos; plantilla "Fumigación interior o exterior (App v2)".
      **08-jul-2026 (bis):** m2m EN tarjetas (plaga multiselección), 2 columnas por grupo anidado
      (plaguicida nombre+dosis), campo condicional "Otro" (selección y m2m), polish de espaciado/etiquetas;
      2ª plantilla "Mantenimiento de áreas verdes (App v2)"; **sembrador versionado** `hooks.py`
      (`post_init_hook` + migración 19.0.1.2.0). Ver `25-field-app.md`.
      **16-17-jul-2026 (v19.0.1.5.0→1.10.0):** flujo ordenado (hoja tras "Comenzar", firma tras guardar
      la hoja → etapa **"Pendiente de firma"**); **validación de obligatoriedad** requerido/condicional/
      min-uno (cliente rojo+bloqueo y servidor) — **cierra I-05**; traza de botones (Llamar/WhatsApp/
      Maps) al chatter; lista **Hoy/Todos** + **ruta arrastrable**; icono de la app; PDF con **"Tiempo
      en sitio"** y **fotos** (fix de encoding + JPEG). Ver `25-field-app.md`.
      **10-ago-2026 (v19.0.1.16.0):** Fumigación reestructurada — **áreas obligatorias**
      (Cocina/Baño/Área de basura, no borrables, con dispensa "cliente no permitió") y **taxonomía de
      plagas de 2 niveles** (categoría → especies, indentadas) tras el gate "¿presencia activa?";
      el sembrador ahora **converge catálogos** (`_sync_selection` / `_sync_tag_records`) y la
      obligatoriedad respeta la visibilidad **también en servidor**. Ver `25-field-app.md`.
      **10-ago-2026 (v19.0.1.17.0):** **cámara obligatoria** — toda foto se toma en vivo con
      `getUserMedia` (el `<input capture>` era solo una pista que iOS ignora); el input oculto se
      rellena por `DataTransfer` así que el servidor no cambió. Multi-foto ya funcionaba.
      **Reporte firmado por WhatsApp** desde la app: Odoo renderiza el PDF y el runtime
      (`visar_fastapi`, `POST /internal/send-report` por loopback) lo entrega con pywa — primer
      camino Odoo → runtime. Ver `25-field-app.md`.
      **10-ago-2026 (v19.0.1.18.0):** los avisos al cliente (**en camino / llegó / reagendar**)
      dejan de ser simulación: se encolan en `visar.wa.message` y los manda un cron (disparado al
      encolar, con **caducidad por tipo** — un aviso viejo no se manda tarde) contra
      `/internal/send-notification`. Vista de oficina "Avisos por WhatsApp" filtrada por
      *No entregados*. Ver `25-field-app.md`.
- [x] **Reporte por WhatsApp funcionando en el servidor** (modo libre, dentro de la ventana de 24 h).
- [ ] **Plantillas de Meta aprobadas** — prerrequisito de negocio, bloquea el uso real:
      `WA_REPORT_TEMPLATE` (cabecera DOCUMENT) y las tres de aviso (`WA_TEMPLATE_ENROUTE`,
      `_ARRIVED`, `_RESCHEDULE`). Los **avisos no tienen camino libre viable**: van siempre a un
      cliente que agendó por la web y nunca escribió, así que están siempre fuera de la ventana —
      hasta la aprobación se encolan, dan 502 y caducan (visible en el buzón y en el chatter).
- [ ] Verificar la **cámara en teléfono real** (iOS no debe ofrecer la fototeca).
- [ ] Reporte dual interno vs cliente.
- [ ] Cross-link explícito cita ↔ tarea en agenda (hoy vía SO compartida + `visar_sale_order_id` en tarea).
- [ ] E2E: confirmar pago → verificar N tareas FSM correctas en UI técnico.

## Hecho — Fixes E2E web Odoo 19 (jun-2026)

| # | Síntoma | Causa | Fix |
|---|---------|-------|-----|
| 1 | 500 al Book now valoración | `website.pricelist_id` no existe en Odoo 19 | `website._get_and_cache_current_pricelist()` |
| 2 | 500 sidebar horarios | QWeb no soporta `getattr` | Plantilla usa solo `visar_quote`; inyección vía `request.render` |
| 3 | 500 wizard paso servicios | `post.getlist` en dict plano | `_visar_form_id_list()` → `request.httprequest.form.getlist()` |
| 4 | Cita maestro con SO valoración | Wizard `is_valuation` seguía al maestro | Bifurcación a flujo valoración + aviso |

## Parcial / pendiente

### Go-live / despliegue (CRÍTICO — ver `80-deploy-prod.md`)

- [x] `visar_fsm` tiene `post_init_hook` (proyectos FSM) — corre en `-i`.
- [ ] Catálogo legacy + tipos entrada `visar_appointment` **siguen solo en migraciones** (no en hook).
- [ ] Mover setup estructural de citas a `post_init_hook` idempotente compartido con migración.
- [ ] Configurar productos/catálogo/zonas/combo/add-ons en backend de prod.
- [ ] Probar **`-i` en BD vacía** con los tres módulos.

### E2E web (manual)

- [ ] Wizard completo: servicios → rangos → **calificación** → zona → horario → checkout (con/sin roedores).
- [ ] Verificar add-ons obligatorios en sidebar y checkout (cantidades sumadas).
- [ ] Wizard → tramo `is_valuation` → aviso → valoración → checkout.
- [ ] Combinaciones combo (interior + exterior + corte) y totales vs tabulador.
- [ ] Confirmar pago → tareas FSM agrupadas. 🆕 **13-ago-2026:** el combo fumigación +
      áreas verdes cae en **UNA** tarea de "Servicios combinados" (hoja, firma y PDF
      únicos); un combo triple con interior + exterior + corte sigue siendo 1 tarea.
      Cubierto por `visar_fsm/tests/test_fsm_grouping.py`. **Pendiente: pólizas** (fase 2,
      su generación de visitas es por línea y por periodo, ver `40-decisions.md`).

### Datos / operación

- [ ] Renombrar dimensión "Corte y poda" → **"Mantenimiento de áreas verdes"** (reunión 22-jun); forzar valoración si aplica.
- [ ] Configurar add-ons en fumigación (estaciones ×3 obligatorias cuando roedores=si vía producto roedores).
- [ ] Decidir migración única: attrs A/B/C vs solo pricelist por zona.

## Entorno local activo

- **Odoo:** `/Users/luisgarza27/Documents/HANOVA/odoo_19_visar`
- **Repo módulos:** `/Users/luisgarza27/Documents/HANOVA/VISAR/repo`
> ⚠️ **Esta sección describía la máquina de otra persona** (rutas `/Users/luisgarza27/…`, BD
> `visar_local`). Se deja genérica: sustituye `<RAÍZ>` por la raíz de tu checkout.

- **Git remoto:** `https://github.com/luisgarza-g/visar-luisg.git` (rama `main`)
- **Config:** `odoo.visar.conf`
- **BD local:** puerto **8071**, credenciales `admin / admin`
- **Arranque:**
  ```bash
  cd <RAÍZ>/odoo_19_visar
  PYTHONPATH=<RAÍZ>/odoo_19_visar:<RAÍZ>/odoo_19_visar/visar-homes \
    .venv/bin/python setup/odoo -c odoo.visar.conf
  ```
- **Actualizar módulos:**
  ```bash
  PYTHONPATH=<RAÍZ>/odoo_19_visar:<RAÍZ>/odoo_19_visar/visar-homes \
    .venv/bin/python setup/odoo -c odoo.visar.conf \
    -u visar_base,visar_fsm,visar_appointment --stop-after-init
  ```
- Tras `-u`, **reiniciar el servidor** (workers cachean registro de modelos/plantillas).

> **Nombres de base, para que no se repita el error.** En el servidor la base es **`visar-db`**.
> **`visar_prod` NO EXISTE** — no aparece en `psql -l`, y `/etc/odoo/odoo.conf` trae
> `db_name = visar-db`. Existen `visar-db`, `visar-db-2`, `visar-db-pres`,
> `visar-db-rehearsal` y `visar-test`. Varios documentos de esta carpeta usan el nombre
> equivocado. **Los módulos custom viven en `/opt/custom`**, no en una ruta `visar-homes/`.
>
> ⚠️ **Nunca correr tests contra `visar-db`.** Siempre sobre una copia desechable.

## Cómo probar (checklist actualizado)

1. **`/appointment`** — solo **Valoración Técnica** y **Cita de Servicios**.
2. **Valoración directa:** Book now → zona → horario → checkout ($500).
3. **Wizard normal:** servicios → rangos → **calificación** → zona → maestro → horario → checkout multi-línea (+ add-ons si aplica).
4. **Wizard con roedores:** calificación con roedores=Sí → verificar línea control roedores + estaciones en total.
5. **Wizard → valoración:** rango `is_valuation` → aviso → valoración → checkout ($500).
6. **FSM:** tras pago, revisar tareas en proyecto FSM correspondiente (backend / app técnico).
7. **Legacy D-03:** URL directa tipo individual → prequalify Zona + m².
8. **Agendado por WhatsApp (RPC):** desde `odoo shell`, recorrer `agent_booking_step` de
   principio a fin pasando en cada llamada el `booking` que devolvió la anterior. Confirmar que
   `step` avanza y **nunca repite** un paso ya contestado, que `options` no viene vacío donde
   debería haber opciones, y que con una respuesta inválida `error` viene lleno y `step` **no se
   mueve**. Probarlo **por JSON-RPC de verdad**, no solo en shell: un recordset colado en
   `options` revienta ahí y no en el shell.
9. **Paridad web ↔ agente:** el mismo cuestionario por los dos caminos, con las mismas
   respuestas, tiene que dar el **mismo `selections`**. Si divergen, eso es exactamente lo que
   `c115c21` venía a impedir.

## Convenciones de trabajo

- No instalar/actualizar contra la BD del usuario sin avisar.
- Validar Python (`py_compile`) y XML antes de dar por hecho un cambio.
- Respetar `60-odoo19-conventions.md`.
- **No crear productos en XML** — configurar en backend.
- Actualizar los **tres módulos** cuando cambie lógica compartida (`visar_base` primero en dependencias).
