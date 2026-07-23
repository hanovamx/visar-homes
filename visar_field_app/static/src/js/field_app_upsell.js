/* Visar - App de Campo: venta de productos adicionales (upsell), vanilla JS.

   Tres piezas, todas opcionales — la página funciona sin JS (los contadores son
   inputs number normales y el formulario se envía igual):
     1. contadores +/− del catálogo, con resaltado del producto seleccionado;
     2. buscador en cliente (el catálogo de campo es corto: filtrar en el servidor
        obligaría a un viaje por cada letra, con la señal de una azotea);
     3. sondeo del cobro en la pantalla del QR, para que el técnico no tenga que
        recargar mientras el cliente paga en su propio teléfono. */
(function () {
    "use strict";

    // Cada cuánto se pregunta si el pago ya entró. 4 s es cómodo de esperar de
    // pie y no castiga al servidor: la pantalla vive un par de minutos.
    var POLL_MS = 4000;

    function initQuantitySteppers() {
        var form = document.getElementById("visar-upsell-form");
        if (!form) {
            return;
        }

        function refresh(item) {
            var input = item.querySelector(".o_visar_qty_input");
            var hint = item.querySelector(".o_visar_qty_hint");
            var qty = parseInt(input.value, 10) || 0;
            item.classList.toggle("o_selected", qty > 0);
            if (hint) {
                hint.textContent = qty > 0 ? qty + " por agregar" : "";
            }
        }

        function step(item, delta) {
            var input = item.querySelector(".o_visar_qty_input");
            var qty = (parseInt(input.value, 10) || 0) + delta;
            input.value = Math.max(qty, 0);
            refresh(item);
        }

        form.addEventListener("click", function (ev) {
            var plus = ev.target.closest(".o_visar_qty_plus");
            var minus = ev.target.closest(".o_visar_qty_minus");
            if (!plus && !minus) {
                return;
            }
            ev.preventDefault();
            step(ev.target.closest(".o_visar_prod_item"), plus ? 1 : -1);
        });

        form.addEventListener("input", function (ev) {
            if (ev.target.classList.contains("o_visar_qty_input")) {
                refresh(ev.target.closest(".o_visar_prod_item"));
            }
        });

        // Estado inicial (el navegador puede restaurar valores al volver atrás).
        form.querySelectorAll(".o_visar_prod_item").forEach(refresh);
    }

    function initCatalogSearch() {
        var search = document.getElementById("visar-upsell-search");
        if (!search) {
            return;
        }
        var items = document.querySelectorAll(".o_visar_upsell_catalog .o_visar_prod_item");
        var empty = document.getElementById("visar-upsell-empty");

        search.addEventListener("input", function () {
            var needle = search.value.trim().toLowerCase();
            var shown = 0;
            items.forEach(function (item) {
                // Un producto con cantidad puesta NO se oculta: si el técnico ya lo
                // eligió y luego busca otro, esconderlo lo haría creer que se perdió.
                var picked = item.classList.contains("o_selected");
                var match = !needle || (item.dataset.name || "").indexOf(needle) !== -1;
                item.classList.toggle("d-none", !match && !picked);
                if (match) {
                    shown += 1;
                }
            });
            if (empty) {
                empty.classList.toggle("d-none", shown > 0);
            }
        });
    }

    function initPaymentPolling() {
        var poll = document.getElementById("visar-upsell-poll");
        if (!poll || !poll.dataset.status) {
            return;
        }
        var waiting = document.getElementById("visar-upsell-waiting");

        window.setInterval(function () {
            // `document.hidden`: con la pantalla apagada o la app en segundo plano
            // no hay nadie mirando, y el técnico anda con datos móviles.
            if (document.hidden) {
                return;
            }
            fetch(poll.dataset.status, {credentials: "same-origin"})
                .then(function (response) {
                    return response.ok ? response.json() : null;
                })
                .then(function (data) {
                    if (data && data.state === "pagado") {
                        if (waiting) {
                            waiting.textContent = "Pago recibido. Actualizando…";
                        }
                        window.location.reload();
                    }
                })
                .catch(function () {
                    // Sin señal: se reintenta en el siguiente ciclo, sin ruido.
                });
        }, POLL_MS);
    }

    function init() {
        initQuantitySteppers();
        initCatalogSearch();
        initPaymentPolling();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
