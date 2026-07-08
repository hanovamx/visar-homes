# Tabulador de precios VISAR (fuente: `VISAR_Tabulador_Final.xlsx`)

> Capturado del Excel entregado por Visar (`/Users/luisgarza27/Downloads/VISAR_Tabulador_Final.xlsx`,
> hoja **"Tabulador VISAR"**). Esta es la **fuente de verdad** para poblar `visar.service.tier`
> (rangos → variante) y las pricelists por zona. Si el Excel cambia, re-sincronizar aquí.

## Reglas de zona (pricelist con %)

- **Zona B = precio base (100%).**
- **Zona A = base × 1.15 (+15%, premium).**
- **Zona C = base × 0.90 (−10%, periférica).**
- Los % están parametrizados en el Excel (filas 4–5): A = `0.15`, C = `-0.10`. Tratar como **configurables**.
- Excepción: **Visita de Valoración Técnica = $500 plano** en las 3 zonas (no escala con %).

## FUMIGACIÓN INTERIOR  — se cotiza por m² del **interior** de la propiedad

| Rango (m² interior) | Zona B (base) | Zona A | Zona C | Nota |
|---|---:|---:|---:|---|
| 1 – 250 m²      |  600 |  690 |  540 | Cubre la mayoría de las casas. |
| 251 – 500 m²    |  800 |  920 |  720 | Casas medianas / residenciales. |
| 501 – 1,000 m²  | 1000 | 1150 |  900 | Casas grandes. Precio cerrado dentro del rango. |
| Más de 1,000 m² | — | — | — | **Requiere visita de valoración técnica** ($500 abonable al servicio). |

## FUMIGACIÓN EXTERIOR  — se cotiza por m² de **jardín**; **se SUMA al precio de interior**

| Rango (m² jardín) | Zona B (base) | Zona A | Zona C | Nota |
|---|---:|---:|---:|---|
| 0 – 50 m²       | **0** (incluida) | 0 | 0 | Incluida sin costo adicional; ya está en el precio base de interior. |
| 51 – 100 m²     |  800 |  920 |  720 | Se suma al precio de interior. |
| 101 – 500 m²    | 1000 | 1150 |  900 | Se suma al precio de interior. |
| Más de 500 m²   | — | — | — | **Requiere visita de valoración técnica.** |

> **Implicación de diseño:** exterior es un **complemento aditivo** de interior, no un servicio de
> precio independiente. En el modelo multi-línea esto se resuelve solo: si se eligen interior + exterior,
> son **dos líneas** de la misma cita (interior a precio completo + exterior con su precio de tramo).
> El tramo "0–50 m²" mapea a una variante de **precio 0** (o no se agrega línea).
> **Confirmado con Visar (22-jun-2026):** fumigación exterior **sí puede agendarse sin interior**
> (servicio independiente válido). El "se suma" es la forma de cobrar cuando ambos están presentes,
> no una restricción de venta.

## CORTE DE PASTO / MANTENIMIENTO DE ÁREAS VERDES

> **Nota reunión 22-jun-2026:** Visar renombró "corte de pasto y poda" a **"Mantenimiento de áreas verdes"**
> para el servicio completo (corte + detallado + poda formativa). Ese servicio **no tiene precio en línea**
> → debe disparar flujo valoración (`is_valuation`). El **corte de pasto del combo** (solo cortar y embolsar)
> sigue siendo el producto con tramos del tabulador abajo.

| Rango (m² jardín) | Zona B (base) | Zona A | Zona C | Nota |
|---|---:|---:|---:|---|
| 0 – 50 m²       |  800 |  920 |  720 | Precio base de jardinería. |
| 51 – 100 m²     | 1000 | 1150 |  900 | Sube $200 por cada bloque de 50 m². |
| 101 – 150 m²    | 1200 | 1380 | 1080 | |
| 151 – 200 m²    | 1400 | 1610 | 1260 | |
| Más de 200 m²   | — | — | — | **Requiere visita de valoración técnica.** |

## COMBO  — Fumigación **interior + exterior + corte de pasto** en una sola visita

- **Definición (Visar, 22-jun-2026):** el combo es la reserva que incluye los **tres** servicios:
  fumigación **interior** + fumigación **exterior** + **corte de pasto**. Solo entonces aplica el descuento.
- **Regla:** `precio combo = fumigación interior + fumigación exterior + factor × corte de pasto`.
- **factor = 0.5** (se cobra el **50%** del corte). Parametrizable.
- **Ejemplo:** fumigación $600 + 50% × corte $800 = **$1,000** (más el exterior que corresponda).
- **Implicación de diseño:** solo cuando los **tres** servicios están en la reserva, la **línea de corte**
  se cobra al 50% (descuento del 50% en la línea de SO). Si falta cualquiera de los tres, el corte va a
  precio completo. El factor 0.5 configurable (sugerencia: `ir.config_parameter` `visar.combo_corte_factor`).

## VISITA DE VALORACIÓN TÉCNICA

- **$500 plano** (las 3 zonas).
- Aplica cuando un servicio cae en su rango "Requiere valoración" (interior >1000, exterior >500, corte >200).
- Es **abonable**: si el cliente acepta el presupuesto, los $500 se descuentan del total del servicio.
- **Regla de dedupe (provisional, Visar 22-jun-2026):** si varios servicios caen en rango de valoración,
  se agrega **una sola** línea de Valoración ($500), no una por servicio. Marcado como provisional,
  a reconfirmar.

## Catálogo de productos/variantes que implica el tabulador

| Producto (`product.template`) | `visar_service_group` | `visar_dimension_kind` | Variantes (tramos) |
|---|---|---|---|
| Fumigación Interior | `fumigacion` | `interior` | 1-250, 251-500, 501-1000, (→valoración) |
| Fumigación Exterior | `fumigacion` | `exterior` | 0-50 ($0), 51-100, 101-500, (→valoración) |
| Corte de Pasto      | `corte`      | `poda`     | 0-50, 51-100, 101-150, 151-200, (→valoración) |
| Visita de Valoración Técnica | — | — | única, $500 plano |

> Precios `list_price` de cada variante = **Zona B (base)**. Zonas A/C vía pricelist con %.
</content>
</invoke>
