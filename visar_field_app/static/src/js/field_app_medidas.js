/* Visar - App de Campo: ajuste de medidas, vanilla JS.

   Dos piezas:
     1. los m² escritos eligen solos el tramo que les toca;
     2. la diferencia se recalcula en el servidor (mismo motor que el cobro) cada vez
        que cambia un tramo, y el botón solo se habilita con una diferencia válida,
        el motivo escrito y al menos una foto.
   El servidor vuelve a validar todo al aplicar; esto solo evita viajes inútiles. */
(function () {
    "use strict";

    function money(value) {
        var n = Number(value || 0);
        return "$" + n.toLocaleString("es-MX", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }

    function init() {
        var form = document.getElementById("visar-ajuste-form");
        if (!form) {
            return;
        }
        var url = form.dataset.cotizar;
        var nuevo = form.querySelector(".o_visar_ajuste_nuevo");
        var dif = form.querySelector(".o_visar_ajuste_dif");
        var msg = form.querySelector(".o_visar_ajuste_msg");
        var submit = form.querySelector(".o_visar_ajuste_submit");
        var motivo = form.querySelector("#ajuste-motivo");
        var fotos = form.querySelector(".o_visar_ajuste_fotos");
        var valido = false;
        var pedido = 0;

        // Solo se escribe si cambia: el observador de abajo mira el DOM, y un
        // texto reescrito igual lo volvía a disparar sin fin (se congelaba la
        // pantalla al primer tramo válido, 4-nov-2026).
        function setText(el, text) {
            if (el.textContent !== text) {
                el.textContent = text;
            }
        }

        function refreshSubmit() {
            var hayFotos = !!(fotos && fotos.files && fotos.files.length);
            var hayMotivo = !!(motivo && motivo.value.trim());
            submit.disabled = !(valido && hayFotos && hayMotivo);
            if (valido && (!hayFotos || !hayMotivo)) {
                setText(msg, "Falta " + [!hayMotivo && "el motivo", !hayFotos && "al menos una foto"]
                    .filter(Boolean).join(" y ") + ".");
            }
        }

        function cotizar() {
            var params = new URLSearchParams();
            form.querySelectorAll("input[type=radio]:checked").forEach(function (r) {
                params.append(r.name, r.value);
            });
            var mio = ++pedido;
            valido = false;
            refreshSubmit();
            fetch(url + "?" + params.toString(), {credentials: "same-origin"})
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (mio !== pedido) {
                        return;  // llegó tarde: ya hay otra consulta en curso
                    }
                    if (data.error) {
                        setText(nuevo, "—");
                        setText(dif, "—");
                        setText(msg, data.mensaje || "No se pudo calcular.");
                        return;
                    }
                    valido = true;
                    if (data.valoracion) {
                        setText(nuevo, "Valoración técnica");
                        setText(dif, "Oficina cotiza");
                        setText(msg, "Esa medida no tiene precio de lista: oficina cotiza la diferencia. Haga solo lo pagado.");
                    } else {
                        setText(nuevo, money(data.nuevo));
                        setText(dif, money(Math.max(data.diferencia, 0)));
                        if (data.diferencia > 0) {
                            setText(msg, "Al aplicar se genera el cobro de la diferencia.");
                        } else if (data.diferencia < 0) {
                            setText(msg, "La casa es más chica: no se devuelve nada y oficina queda avisada.");
                        } else {
                            setText(msg, "Mismo precio: no hay nada que cobrar.");
                        }
                    }
                    refreshSubmit();
                })
                .catch(function () {
                    setText(msg, "Sin conexión. Intente de nuevo.");
                });
        }

        // m² → tramo: el primero cuyo rango los contiene.
        form.querySelectorAll(".o_visar_ajuste_m2").forEach(function (input) {
            input.addEventListener("input", function () {
                var m2 = parseFloat(input.value);
                if (!(m2 > 0)) {
                    return;
                }
                var dim = input.closest(".o_visar_ajuste_dim");
                var radios = dim.querySelectorAll("input[type=radio]");
                for (var i = 0; i < radios.length; i++) {
                    var min = parseFloat(radios[i].dataset.min);
                    var max = parseFloat(radios[i].dataset.max);
                    if (!isNaN(min) && !isNaN(max) && m2 >= min && m2 <= max) {
                        if (!radios[i].checked) {
                            radios[i].checked = true;
                            cotizar();
                        }
                        break;
                    }
                }
            });
        });
        form.addEventListener("change", function (ev) {
            if (ev.target.type === "radio") {
                cotizar();
            } else {
                refreshSubmit();
            }
        });
        if (motivo) {
            motivo.addEventListener("input", refreshSubmit);
        }
        // La cámara entrega las fotos al input oculto sin disparar siempre `change`.
        // Se mira solo el bloque de la cámara (sus miniaturas), no todo el formulario.
        var camara = form.querySelector(".o_visar_capture");
        if (camara) {
            new MutationObserver(refreshSubmit).observe(camara, {childList: true, subtree: true});
        }
        cotizar();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
