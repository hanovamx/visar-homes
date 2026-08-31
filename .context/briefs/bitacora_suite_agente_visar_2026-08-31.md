# Bitácora · Suite de aceptación del Agente de WhatsApp Visar

**Fecha:** 31 de agosto de 2026
**Entorno:** `visar-db` · Odoo 19.0+e-20260318 · https://visar.hanova.consulting
**Entregable:** [Suite de Aceptación Agente Visar](https://claude.ai/code/artifact/7e5d3c80-0f94-46be-977f-4811eb85170b) — 109 casos en 10 categorías
**Método:** análisis de prompts y documentación + validación por RPC contra el entorno vivo

---

## 1. Qué se pidió

Construir la lista final de casos de prueba para validar el agente de IA, derivada de:

- El prompt base y las memorias de ruta
- La documentación de comportamiento
- Los casos de uso existentes
- El feedback del cliente en la sesión del 24 de agosto
- El comportamiento real validado por RPC

Con, para cada prueba: ID, nombre, objetivo, precondiciones, input, acción esperada, respuesta esperada, comportamiento prohibido, si requiere RPC, prioridad y fuente.

## 2. Insumos

| Insumo | Estado |
|---|---|
| Prompt base (`Prompt base.txt`) | Recibido. Corresponde al prompt vigente id 2 |
| 5 memorias de ruta | Recibidas |
| `Agente-VISAR-Especificacion.pdf` | Recibido |
| Transcripción Touchpoint 24 ago | Recibida |
| `odoo_client.py` para RPC | Recibido y funcional |
| **Lista de pruebas existente** | **No se recibió.** No hay ningún archivo de casos de prueba en las carpetas compartidas |

Ante la falta de la lista existente, los apartados de *mantener / modificar / eliminar* se construyeron tomando **la demo del 24 de agosto** como suite de facto.

---

## 3. Configuración verificada por RPC

### 3.1 Prompts y rutas

| Elemento | Valor |
|---|---|
| Prompt vigente | `visar.agent.prompt` id **2** «Prompt Updated» · 23,477 caracteres |
| Prompt eclipsado | id **1** «Prompt base» · 17,449 car. · `es_vigente = false` |
| Rutas vivas | `reception`, `schedule`, `existing`, `other` |
| Ruta muerta | `info` — marcada **inalcanzable** por el propio sistema |
| Herramientas | `resolve_zone`, `quote_service`, `start_booking`, `my_services`, `escalate_to_human` |
| Modelo | `claude-haiku-4-5` · `max_tokens` 1024 · `max_tool_iterations` 4 |
| Canal WhatsApp | `visar.whatsapp.config` con **0 registros** — la API de WhatsApp Business no está dada de alta |
| Acceso a herramientas | `visar.agent.tools` exige el grupo **101 «Agente WhatsApp / Solo lectura»**. `admin` no lo tiene |

El prompt id 2 se editó **ocho veces** el 31 de agosto: 17:33, 19:15, 19:20, 20:50, 21:19, 21:31, 21:41 y una previa.

### 3.2 Catálogo

| Elemento | Valor |
|---|---|
| Dimensiones | `fumigacion_interior`, `fumigacion_exterior`, `corte_poda` |
| Regla de combo | Exige las tres dimensiones · descuento **50%** solo sobre `corte_poda` |
| Zonas | A (San Pedro/Santiago), B (base), C (periferia) → listas 7, 8, 9 |
| Cobertura | 1,080 códigos postales mapeados · 199 marcados `needs_review` |
| Listas de precios | **26** en total, 23 activas |
| Productos | 30 Fumigación interior o exterior · 31 Mantenimiento de áreas verdes · 33 Valoración técnica $500 |

**Umbrales de valoración técnica**

| Medida | Tramos con precio | Va a valoración |
|---|---|---|
| Fumigación interior (construcción) | 1–250 · 251–500 · 501–1000 | > 1,000 m² |
| Fumigación exterior (jardín) | 0–50 (gratis) · 51–100 · 101–500 | > 500 m² |
| Mantenimiento de áreas verdes | 0–50 · 51–100 · 101–150 · 151–200 | > 200 m² |

**Códigos postales de prueba**

| Alias | CP | Zona |
|---|---|---|
| CP-A | 66260 | A · San Pedro |
| CP-B | 64000 | B · Monterrey centro |
| CP-LEJOS | 65000 | B · Anáhuac, a ~200 km |
| CP-FUERA | 44100 | Fuera de cobertura (Jalisco) |
| CP-FUERA-2 | 06000 | Fuera de cobertura (CDMX) |

### 3.3 Precios de contado · fumigación (27 celdas)

Filas = jardín, columnas = construcción.

**Zona A**

| Jardín | 1–250 | 251–500 | 501–1000 |
|---|---|---|---|
| 0–50 m² | $690 | $920 | $1,150 |
| 51–100 m² | $1,610 | $1,840 | $2,070 |
| 101–500 m² | $1,840 | $2,070 | $2,300 |

**Zona B**

| Jardín | 1–250 | 251–500 | 501–1000 |
|---|---|---|---|
| 0–50 m² | $600 | $800 | $1,000 |
| 51–100 m² | $1,400 | $1,600 | $1,800 |
| 101–500 m² | $1,600 | $1,800 | $2,000 |

**Zona C**

| Jardín | 1–250 | 251–500 | 501–1000 |
|---|---|---|---|
| 0–50 m² | $540 | $720 | $900 |
| 51–100 m² | $1,260 | $1,440 | $1,620 |
| 101–500 m² | $1,440 | $1,620 | $1,800 |

La matriz es regular: **zona A = zona B × 1.15** y **zona C = zona B × 0.9**, en las 27 celdas.

### 3.4 Áreas verdes por separado

| Tramo | Zona A | Zona B | Zona C |
|---|---|---|---|
| 0–50 m² | $920 | $800 | $720 |
| 51–100 m² | $1,150 | $1,000 | $900 |
| 101–150 m² | $1,380 | $1,200 | $1,080 |
| 151–200 m² | $1,610 | $1,400 | $1,260 |

### 3.5 Combo de contado

Total = fumigación + **mitad** del corte del tramo de jardín. Solo posible hasta 200 m² de jardín.

**Zona B**

| Jardín | 1–250 | 251–500 | 501–1000 |
|---|---|---|---|
| 0–50 | $1,000 | $1,200 | $1,400 |
| 51–100 | $1,900 | $2,100 | $2,300 |
| 101–150 | $2,200 | $2,400 | $2,600 |
| 151–200 | $2,300 | $2,500 | $2,700 |

### 3.6 Planes de suscripción

| Como lo llama el prompt | Nombre en Odoo | id | Cobro | Dto. | 1er cobro | Visitas | Permanencia | Subs. |
|---|---|---|---|---|---|---|---|---|
| Póliza mensual | Suscripción Mensual | 3 | cada mes | **5%** | 3 periodos | 1 | — | 9 |
| Póliza semestral | Suscripción semestral | 10 | cada 6 meses | **5%** | 1 periodo | 6 | — | 4 |
| Póliza anual | Suscripción anual | 13 | cada año | **5%** | 1 periodo | 12 | — | 8 |
| 3 servicios | 3 servicios: Servicio plaga recurrente | 12 | cada mes | 0% | 3 periodos | 3 | — | 2 |

**Cómo se arma el precio.** Cada plan tiene su lista, encadenada a una lista base por `base_pricelist_id`:

| Plan | Lista del plan | Lista base | Cálculo | Zona B, 1–250, jardín 0–50 |
|---|---|---|---|---|
| Mensual | 15, 16, 17 | única venta | contado − 5% | **$570** al mes |
| Semestral | 10, 45, 46 | Pago semestral (×6) | contado × 6 − 5% | **$3,420** cada 6 meses |
| Anual | 47, 48, 49 | Pago Anual (×12) | contado × 12 − 5% | **$6,840** al año |
| 3 servicios | 31, 32, 42 | única venta | sin descuento | **$600** al mes |

Por servicio los tres planes con descuento cuestan **lo mismo**: $3,420 ÷ 6 = $6,840 ÷ 12 = **$570**. Solo cambia la cadencia y las visitas.

No hay redondeo configurado (`price_round = 0`), así que en zona A salen centavos: $690 − 5% = **$655.50**.

**Combo bajo póliza**, zona B, construcción 1–250 con jardín 0–50:

| Contado | Mensual | Semestral | Anual |
|---|---|---|---|
| $1,000 | $950 | $5,700 | $11,400 |

Planes archivados: Póliza Bimestral-no, Póliza Trimestral-no, Suscripción anual-no usar., Fumigación Anual - Mensual, Mantenimiento Áreas Verdes Anual. **Dos conservan suscripciones vivas**: Fumigación Anual - Mensual con 3 y Póliza Bimestral-no con 1.

### 3.7 Tipos de cita

| id | Nombre | Flujo | Productos |
|---|---|---|---|
| 11 | Valoración técnica. | valuation | 33 |
| 12 | Cita de Servicios. | wizard | 30 |
| 13 | Visar — cita multi-servicio | maestro | — |
| 15 | Opción de Suscripción | wizard | **ninguno** |

---

## 4. Cronología de la sesión

El cliente fue corrigiendo el sistema en vivo mientras se construía la suite. Cada afirmación se reverificó por RPC antes de cerrarla.

| Hora | Cambio | Verificado |
|---|---|---|
| 17:33 | Se retira la póliza bimestral del prompt | Sí |
| 19:15 | Umbral de valoración pasa de 500 a **1,000 m²** de construcción; primer cobro a tres meses | Sí |
| 19:20 | Se acota el escalamiento por salud; se unifica el tuteo en «usted» | Sí |
| 19:29 | Se corrige la celda de zona A: $1,610 → **$1,840** | Sí |
| 20:29 | Se corrige el precio anual de esa celda: $19,320 → **$22,080** | Sí |
| 20:44 | Se renombran los planes; se elimina la permanencia | Sí |
| 20:50 | Se renombran las 26 listas de precios y los productos | Sí |
| 21:19 | Sale la palabra «CONFIRMAR» del prompt | Sí |
| 21:31 | Se corrigen erratas de la sección 14 | Parcial |
| 21:41 | Se terminan de corregir las erratas | Sí |
| 21:45 | Se limpian las listas de Pago Anual de zona B y C | Sí |

---

## 5. Hallazgos cerrados

| Hallazgo | Resolución |
|---|---|
| «CONFIRMAR» en el prompt de producción | Eliminado |
| Póliza bimestral inexistente en Odoo | Retirada del prompt |
| Tres versiones del primer cobro | Unificado en tres meses, cargo triple |
| Umbral de 500 m² contra los tramos cargados | Aclarado: los 500 son del jardín; en construcción el corte es 1,000 |
| Salud y mascotas: conflicto interno del prompt | Acotado a reacción alérgica o mascota afectada |
| Tuteo contra trato de usted | Unificado en usted |
| Celda de zona A fuera de patrón | Corregida a $1,840, y su gemela anual a $22,080 |
| Nombres de planes duplicados y desalineados entre idiomas | Renombrados y alineados |
| Listas de precios con nombres repetidos | Las 26 renombradas, ninguna repetida |
| Reglas apuntando al plan archivado | Limpiadas en las tres zonas |
| Erratas de redacción en la sección 14 | Corregidas |
| Plan de tres servicios descrito distinto a su configuración | **Intencional**, confirmado por el cliente |
| Alcance residencial contra catálogo comercial | La Especificación quedó declarada **desactualizada**; la regla vigente es residencial |

## 6. Hallazgos abiertos

**1 · El prompt usa códigos de dimensión que no existen** — Alto
Instruye mandar `FUM_INT` y `FUM_EXT` a `quote_service`. Los reales son `fumigacion_interior` y `fumigacion_exterior`. Si la capa de herramientas no traduce, toda cotización de fumigación falla. Se prueba en **TOOL-03**.

**2 · La memoria de servicio existente llama a una herramienta inexistente** — Alto
Dice `open_my_services`; la registrada es `my_services`. Esa memoria no se toca desde el 27 de agosto. Se prueba en **TOOL-05**.

**3 · El estimador de metros nunca llega al umbral de valoración** — Medio
La fórmula da 119 m² para 3 recámaras y 2 pisos, 370 para 8 recámaras y 3 pisos, y 552 para 10 recámaras y 4 pisos. Hacen falta ~20 recámaras para pasar de 1,000. **El riesgo es el contrario al que cubre la regla**: una casa grande se estima bajo y se cotiza barata. Se prueba en **PRI-03** y **LIM-06**.

## 7. Defectos de sistema vigentes (regresiones)

| Ref. | Estado |
|---|---|
| **REQ-001** · póliza multi-servicio genera 2 tareas | Sin implementar. Los 19 pedidos del agente son ventas puntuales; la ruta combinada nunca se ha ejecutado con póliza |
| **REQ-002** · pedido no se confirma ni factura tras el pago | **Sigue ocurriendo.** De 56 pedidos con pago completado, 3 siguen en Cotización — S00252, S00242, S00197 — y 30 confirmados sin factura. S00252 es posterior al diagnóstico original |
| **REQ-003** · la cita dice «En línea» | Los 4 tipos tienen `location` vacío; el texto viene del default |
| Tipo de cita 15 sin producto | Renombrado a «Opción de Suscripción», pero sigue sin producto asociado |
| Sin tipo de cita para áreas verdes sola | Ningún tipo tiene el producto 31 |
| `visar.slot.hold` con 0 registros | El mecanismo de apartado de horario nunca se ha ejercitado |

---

## 8. Decisiones del cliente registradas

1. El nombre **«3 servicios: Servicio plaga recurrente»** está bien así.
2. Que ese plan **no lleve descuento** es correcto.
3. Que el prompt lo describa como **tres visitas semanales con pago único**, aunque en Odoo se facture con periodo mensual, es **intencional**. La prueba se valida contra el texto del prompt, no contra la configuración.
4. El catálogo de **servicios comerciales e industriales** de la Especificación es **información desactualizada**. La regla vigente es residencial y escalar.
5. **Precedencia de fuentes:** cuando el prompt vigente y la Especificación digan cosas distintas, **manda el prompt**.

## 9. Pendientes

**Bloqueante para ejecutar la suite**

- Crear un usuario de pruebas dentro del grupo **101 «Agente WhatsApp / Solo lectura»**. Sin él no se puede invocar `quote_service`, `resolve_zone` ni `my_services` por RPC, y quedan sin ejecutar los ~40 casos marcados RPC. La primera tarea al tenerlo es contrastar las tablas de precios de este documento contra lo que devuelve la herramienta.

**Para cerrar los hallazgos abiertos**

- Corregir `FUM_INT` / `FUM_EXT` en el prompt.
- Corregir `open_my_services` en la memoria de servicio existente.
- Revisar los coeficientes del estimador de metros.

**Higiene de datos**

- Asociar un producto al tipo de cita 15, o archivarlo.
- Crear un tipo de cita para áreas verdes sola, o asociar el producto 31 a uno existente.
- Migrar las 4 suscripciones vivas que cuelgan de planes archivados.
- Auditar los 199 códigos postales marcados `needs_review`.
- Revisar CP-LEJOS (65000, Anáhuac): está en cobertura nominal pero a ~200 km.

**Sin cobertura de pruebas**

- Recordatorios proactivos (8 disparadores de la Especificación) — depende del alta de WhatsApp Business.
- Seguimiento de lead a 24 horas — misma dependencia.
- Captura al CRM de los 14 campos de la Parte 4 — **confirmar si sigue siendo requisito**.
- Riego y diseño de jardines — no existen como servicio cotizable.
- Comportamiento bajo carga, mensajes simultáneos, reintentos de la cola de salida.
- Multimedia: audios, fotos y ubicación compartida.

---

## 10. Correcciones sobre el propio análisis

Tres conclusiones que emití y luego tuve que retirar, todas por releer los datos:

1. **«Las zonas no apuntan a ninguna lista con precios de póliza».** Falso. Las listas de suscripción se encadenan a las de zona por `base_pricelist_id`.
2. **«Hay dos juegos paralelos de precios semestrales y anuales».** Falso. Las listas «Pago semestral» y «Pago Anual» son las **bases** de esos planes, no duplicados.
3. **«El combo no se puede cotizar en póliza semestral ni anual».** Falso. La dimensión `corte_poda` apunta a la plantilla «Mantenimiento de áreas verdes», que sí tiene precio en esas listas.

Y un error de procedimiento: al actuar sobre los primeros comentarios del artifact, mapeé mal los anclajes CSS y borré un bloque que no se había pedido. Se restauró.

---

## 11. Estructura de la suite

**109 casos en 10 categorías**

| Categoría | Casos |
|---|---|
| Casos de uso principales | 13 |
| Casos de uso secundarios | 14 |
| Manejo de errores | 7 |
| Casos límite | 13 |
| Ambigüedad y falta de información | 8 |
| Manejo de contexto y multi-turno | 7 |
| Validación de instrucciones y restricciones | 15 |
| Uso correcto de herramientas y RPC | 11 |
| Comportamientos señalados por el cliente | 12 |
| Regresiones | 9 |

Cada caso trae: ID, nombre, objetivo, contexto, mensaje del usuario, acción esperada, respuesta esperada, comportamiento prohibido, validación RPC, prioridad y fuente.

El documento vivo, con filtros por prioridad y marcado de ejecución, está en:
**https://claude.ai/code/artifact/7e5d3c80-0f94-46be-977f-4811eb85170b**
