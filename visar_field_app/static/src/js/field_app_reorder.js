/* Visar - App de Campo: reordenar la ruta del día arrastrando las tarjetas.
   Vanilla JS (sin OWL, mismo patrón que el resto de la app).

   Se implementa con **Pointer Events** y no con el drag&drop nativo de HTML5
   porque este último NO dispara en táctil (móvil = el 100% del uso real de la
   app). Los pointer events cubren dedo, mouse y stylus con un solo camino.

   Al soltar: POST del orden nuevo → el servidor lo persiste
   (`visar.field.route.order`) y devuelve el mapa recalculado, que se repinta
   sin recargar la página (`window.visarFieldMap.refresh`). */
(function () {
    "use strict";

    // Píxeles que hay que mover el dedo antes de considerar que es un arrastre
    // (por debajo, es un toque: deja pasar el enlace de la tarjeta).
    var DRAG_THRESHOLD = 6;
    // Franja (px) junto al borde de la pantalla que hace rodar la lista sola
    // mientras se arrastra, y velocidad máxima (px por fotograma). Sin esto, en
    // un teléfono no se puede llevar una tarjeta más allá de lo que se ve: la
    // lista del día no cabe en pantalla y el gesto se topa con el borde.
    var EDGE_ZONE = 70;
    var EDGE_SPEED = 12;

    function initReorder() {
        var list = document.querySelector(".o_visar_task_list");
        if (!list || !list.getAttribute("data-reorder-action")) {
            return;  // vista "Todos" (mezcla días): no se reordena.
        }

        var dragging = null;   // <a> que se arrastra
        var placeholder = null;
        var startY = 0;
        var offsetY = 0;
        var moved = false;
        var lastY = 0;         // último clientY (lo reusa el auto-scroll)
        var scrollRAF = null;

        function items() {
            return Array.prototype.slice.call(
                list.querySelectorAll(".o_visar_task_item"));
        }

        function onPointerDown(ev) {
            var handle = ev.target.closest(".o_visar_drag_handle");
            if (!handle || ev.button > 0) {
                return;
            }
            var item = handle.closest(".o_visar_task_item");
            if (!item) {
                return;
            }
            // Evita que el navegador arranque su propio arrastre del enlace y
            // que la lista haga scroll mientras se arrastra.
            ev.preventDefault();
            handle.setPointerCapture(ev.pointerId);

            dragging = item;
            moved = false;
            startY = ev.clientY;
            var rect = item.getBoundingClientRect();
            offsetY = ev.clientY - rect.top;

            // Hueco del mismo alto que la tarjeta, para que la lista no salte.
            placeholder = document.createElement("div");
            placeholder.className = "o_visar_task_placeholder";
            placeholder.style.height = rect.height + "px";

            item.classList.add("o_visar_task_dragging");
            item.style.width = rect.width + "px";
            item.style.top = rect.top + "px";
            item.parentNode.insertBefore(placeholder, item.nextSibling);
        }

        function onPointerMove(ev) {
            if (!dragging) {
                return;
            }
            if (!moved && Math.abs(ev.clientY - startY) < DRAG_THRESHOLD) {
                return;
            }
            moved = true;
            ev.preventDefault();
            lastY = ev.clientY;
            reposition();
            autoScroll();
        }

        /* Coloca la tarjeta bajo el dedo y mueve el hueco al lugar que le toca.
           Se llama en cada pointermove Y en cada fotograma de auto-scroll (el
           dedo puede estar quieto en el borde mientras la lista rueda). */
        function reposition() {
            dragging.style.top = (lastY - offsetY) + "px";

            // Punto medio de la tarjeta arrastrada contra el de cada hermana:
            // la primera cuya mitad quede por debajo marca dónde va el hueco.
            var middle = lastY - offsetY + dragging.offsetHeight / 2;
            var siblings = items().filter(function (el) {
                // Las cerradas viven al final y no se reordenan.
                return el !== dragging && !el.classList.contains("o_visar_task_done");
            });
            var before = null;
            for (var i = 0; i < siblings.length; i++) {
                var r = siblings[i].getBoundingClientRect();
                if (middle < r.top + r.height / 2) {
                    before = siblings[i];
                    break;
                }
            }
            if (before) {
                list.insertBefore(placeholder, before);
            } else if (siblings.length) {
                var last = siblings[siblings.length - 1];
                list.insertBefore(placeholder, last.nextSibling);
            }
        }

        /* Con el dedo pegado a un borde, rueda la página mientras siga ahí. La
           tarjeta arrastrada es `position: fixed`, así que se queda quieta bajo
           el dedo y es la lista la que pasa por debajo. */
        function edgeSpeed() {
            // Más cerca del borde, más rápido (0 fuera de la franja).
            if (lastY < EDGE_ZONE) {
                return -EDGE_SPEED * (1 - lastY / EDGE_ZONE);
            }
            if (lastY > window.innerHeight - EDGE_ZONE) {
                return EDGE_SPEED * (1 - (window.innerHeight - lastY) / EDGE_ZONE);
            }
            return 0;
        }

        function autoScroll() {
            if (scrollRAF || !edgeSpeed()) {
                return;  // ya está rodando, o el dedo no está en el borde
            }
            var step = function () {
                scrollRAF = null;
                // La velocidad se recalcula CADA fotograma: el dedo puede
                // moverse dentro de la franja o salirse de ella sin soltar.
                var speed = edgeSpeed();
                if (!dragging || !speed) {
                    return;
                }
                var before = window.scrollY;
                window.scrollBy(0, speed);
                if (window.scrollY === before) {
                    return;  // tope de la página: no hay a dónde rodar
                }
                reposition();
                scrollRAF = window.requestAnimationFrame(step);
            };
            scrollRAF = window.requestAnimationFrame(step);
        }

        function stopAutoScroll() {
            if (scrollRAF) {
                window.cancelAnimationFrame(scrollRAF);
                scrollRAF = null;
            }
        }

        function onPointerUp() {
            if (!dragging) {
                return;
            }
            stopAutoScroll();
            var item = dragging;
            dragging = null;
            item.classList.remove("o_visar_task_dragging");
            item.style.width = "";
            item.style.top = "";
            if (placeholder) {
                placeholder.parentNode.insertBefore(item, placeholder);
                placeholder.remove();
                placeholder = null;
            }
            if (!moved) {
                return;  // fue un toque en el asa, no un arrastre
            }
            // El toque que terminó el arrastre no debe abrir el servicio.
            item.addEventListener("click", swallowClick, { once: true, capture: true });
            renumber();
            save();
        }

        function swallowClick(ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }

        /* Renumera las tarjetas pendientes 1..N (feedback inmediato; el servidor
           confirma con el mismo criterio al responder). */
        function renumber() {
            var n = 0;
            items().forEach(function (el) {
                if (el.classList.contains("o_visar_task_done")) {
                    return;
                }
                n += 1;
                var badge = el.querySelector(".o_visar_stop_num");
                if (badge) {
                    badge.textContent = String(n);
                }
            });
        }

        function pendingIds() {
            return items().filter(function (el) {
                return !el.classList.contains("o_visar_task_done");
            }).map(function (el) {
                return el.getAttribute("data-task-id");
            });
        }

        function save() {
            var body = new FormData();
            body.append("csrf_token", list.getAttribute("data-csrf"));
            body.append("task_ids", pendingIds().join(","));
            list.classList.add("o_visar_saving");
            fetch(list.getAttribute("data-reorder-action"), {
                method: "POST",
                body: body,
                credentials: "same-origin",
            }).then(function (resp) {
                if (!resp.ok) {
                    throw new Error("reorder failed: " + resp.status);
                }
                return resp.json();
            }).then(function (data) {
                // El mapa vive en la misma página: se repinta con la ruta y la
                // numeración que acaba de recalcular el servidor.
                if (window.visarFieldMap && window.visarFieldMap.refresh) {
                    window.visarFieldMap.refresh(data.tasks || [], data.route || []);
                }
                list.classList.remove("o_visar_saving");
            }).catch(function (err) {
                // Sin red (pasa en campo): el orden mostrado no está guardado.
                // Se recarga para volver al orden real en vez de mentir.
                console.warn("Visar: no se pudo guardar el orden", err);
                list.classList.remove("o_visar_saving");
                window.location.reload();
            });
        }

        list.addEventListener("pointerdown", onPointerDown);
        list.addEventListener("pointermove", onPointerMove);
        list.addEventListener("pointerup", onPointerUp);
        list.addEventListener("pointercancel", onPointerUp);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initReorder);
    } else {
        initReorder();
    }
})();
