/* El recuadro "Tu reserva" del paso 7 sigue a la opción de póliza elegida.
 *
 * Antes se quedaba clavado en el precio de contado: el cliente elegía la póliza
 * anual y a la derecha seguía leyendo el importe de un servicio único.
 *
 * El servidor ya renderizó UN bloque por opción, cada uno con sus cifras
 * formateadas (`.o_visar_reserva[data-visar-plan]`), y deja visible el de la opción
 * vigente; aquí solo se alterna cuál se ve al cambiar de radio. A propósito no se
 * formatea dinero en el navegador: la moneda y la lista de precios las resuelve
 * Odoo, y un `toFixed` aquí podría desviarse del importe que se va a cobrar.
 *
 * SIN `DOMContentLoaded` y con el listener en `document`: este archivo viaja en
 * `web.assets_frontend_lazy`, que Odoo carga en diferido (el `<script>` trae
 * `data-src`, no `src`). Cuando se ejecuta, `DOMContentLoaded` ya pasó y un
 * listener sobre ese evento no correría nunca — comprobado en el bundle servido.
 * Delegando en `document` y buscando los bloques en el momento del cambio, da igual
 * cuándo cargue.
 *
 * Si el archivo no llega a cargar, se queda visible el bloque que el servidor tenía
 * por elegido: se pierde la actualización en vivo, nunca el dato.
 */
document.addEventListener('change', function (ev) {
    const radio = ev.target;
    if (!radio || radio.name !== 'plan_id' || radio.type !== 'radio') {
        return;
    }
    const bloques = document.querySelectorAll('.o_visar_reserva[data-visar-plan]');
    if (!bloques.length) {
        return;
    }
    // "" es la opción "sin póliza", que el servidor marca con 0.
    const clave = radio.value || '0';
    let encontrado = false;
    bloques.forEach(function (bloque) {
        const suyo = bloque.dataset.visarPlan === clave;
        bloque.classList.toggle('d-none', !suyo);
        encontrado = encontrado || suyo;
    });
    // Un plan sin bloque (no debería pasar) dejaría el recuadro en blanco, que es
    // peor que enseñar el de contado: se cae a ese.
    if (!encontrado) {
        const contado = document.querySelector('.o_visar_reserva[data-visar-plan="0"]');
        if (contado) {
            contado.classList.remove('d-none');
        }
    }
});
