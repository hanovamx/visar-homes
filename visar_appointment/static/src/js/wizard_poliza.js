/* El recuadro "Tu reserva" del paso 7 sigue a la opción de póliza elegida.
 *
 * Antes se quedaba clavado en el precio de contado: el cliente elegía la póliza
 * anual y a la derecha seguía leyendo el importe de un servicio único.
 *
 * El servidor ya renderizó UN bloque por opción, cada uno con sus cifras
 * formateadas (`.o_visar_reserva[data-visar-plan]`); aquí solo se alterna cuál se
 * ve. A propósito no se formatea dinero en el navegador: la moneda y la lista de
 * precios las resuelve Odoo, y un `toFixed` aquí podría desviarse del importe que
 * de verdad se va a cobrar.
 *
 * Si este archivo no carga, se queda visible el bloque de la opción que el
 * servidor ya tenía por elegida: se pierde la actualización en vivo, nunca el dato.
 */
document.addEventListener('DOMContentLoaded', function () {
    const bloques = document.querySelectorAll('.o_visar_reserva[data-visar-plan]');
    if (!bloques.length) {
        return;
    }
    const radios = document.querySelectorAll('input[type="radio"][name="plan_id"]');
    if (!radios.length) {
        return;
    }

    function mostrar(planId) {
        // "" es la opción "sin póliza", que el servidor marca con 0.
        const clave = planId || '0';
        let encontrado = false;
        bloques.forEach(function (bloque) {
            const suyo = bloque.dataset.visarPlan === clave;
            bloque.classList.toggle('d-none', !suyo);
            encontrado = encontrado || suyo;
        });
        // Un plan sin bloque (no debería pasar) dejaría el recuadro vacío, que es
        // peor que enseñar el de contado: se cae a ese.
        if (!encontrado) {
            const contado = document.querySelector('.o_visar_reserva[data-visar-plan="0"]');
            if (contado) {
                contado.classList.remove('d-none');
            }
        }
    }

    radios.forEach(function (radio) {
        radio.addEventListener('change', function () {
            mostrar(radio.value);
        });
    });
});
