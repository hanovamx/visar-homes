# Casos de prueba — Pólizas VISAR (flujo del Excalidraw)

Guía para probar el ciclo de vida de la póliza en Odoo, siguiendo el diagrama
**"FLUJO DEBER SER"**. Cada caso indica pasos y resultado esperado.

## Preparación
- Servicios base marcados como póliza: **Fumigación**, **Mantenimiento de áreas
  verdes** y **Corte de pasto** (recurrente + "genera visita" + tablero FSM).
- Planes **Póliza Mensual/Bimestral/Trimestral** (compromiso 12 meses; 2 periodos en
  la 1ª factura).
- Cliente con **lista de precios de su zona** (A/B/C).

## CP-1 · Alta de póliza
**Pasos:** Ventas → nueva cotización → cliente → plan "Póliza Mensual" → agregar el
servicio base (variante por zona/m²) → confirmar.
**Esperado:** la orden se vuelve suscripción **"En progreso"**; el precio sale de la
lista de zona; la fecha "hasta" = inicio + 12 meses.

## CP-2 · Cobro inicial de 2 meses
**Pasos:** en la póliza confirmada, generar/ver la primera factura.
**Esperado:** la factura cobra **2 mensualidades**; la próxima fecha de facturación
salta al **mes 3**.

## CP-3 · Pago y generación de visitas
**Pasos:** registrar el pago de la primera factura (efectivo o tarjeta).
**Esperado:** se crean **2 visitas** (tareas de Field Service) en el tablero del
servicio, ligadas a la póliza. Volver a registrar el pago **no duplica**.

## CP-4 · Facturación del siguiente periodo
**Pasos:** al llegar el mes 3, correr la facturación recurrente y pagar.
**Esperado:** factura de **1 mensualidad**; se crea **1 visita** nueva; próxima
fecha = mes 4.

## CP-5 · Póliza combo (2 servicios)
**Pasos:** alta de póliza con **Fumigación + Corte de pasto**; confirmar.
**Esperado:** el corte recibe el **descuento de combo**; al pagar se generan
**2 visitas por periodo** (una por servicio, cada una en su tablero).

## CP-6 · Garantía por reincidencia
**Pasos:** con un servicio ejecutado hace **menos de 30 días**, usar el botón
"Visita de garantía".
**Esperado:** se crea una visita **sin costo**; la pestaña **"Siniestralidad"**
actualiza la tasa. Si la póliza no está activa o pasaron **más de 30 días**, el
sistema **lo bloquea**.

## CP-7 · Bloqueo de dirección
**Pasos:** en una póliza confirmada, intentar cambiar la dirección de servicio.
**Esperado:** el sistema **lo impide** con un aviso.

## CP-8 · Cancelación
**Pasos:** cerrar/cancelar la póliza (portal o backend) indicando el motivo.
**Esperado:** la póliza pasa a **"Cancelada"**; los pagos **no son reembolsables**;
deja de facturar.

## CP-9 · Visitas incluidas en el plan (un solo pago, varias visitas)
**Preparación:** plan anual con **periodo de facturación 1 año**, **periodos cobrados
por adelantado = 1** y **visitas incluidas = 12**.
**Pasos:** cotización con ese plan y un servicio de póliza → confirmar → facturar →
registrar el pago.
**Esperado:** el campo "Visitas incluidas" se llena solo en 12 al elegir el plan; el
total es el de **un solo año** (idéntico a si el campo fuera 0) y **no** aparece línea de
mensualidad adelantada; al pagar se crean **12 visitas** en el tablero del servicio,
numeradas `(1/12)`…`(12/12)`; la próxima fecha de facturación sigue **a un año**.
Con dos servicios en la póliza salen **24** visitas, 12 en cada tablero.
Editar el valor a 10 en la póliza persiste y **no** modifica el plan.

## Pendiente de configuración/desarrollo
- **Stripe**: link de pago + tokenización de tarjeta para cobros automáticos
  (requiere llaves del cliente).
- **Portal**: que el cliente **agende su visita** dentro de la ventana de 30 días.
- **Retención**: protocolo motivo → incentivo, y **checklist de reincidencia** en la
  visita de garantía.
