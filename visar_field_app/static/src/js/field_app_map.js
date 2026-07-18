/* Visar - App de Campo: vista de mapa de servicios (Leaflet + OpenStreetMap),
   vanilla JS (sin OWL). Plotea un pin numerado por servicio pendiente, en el
   orden de la ruta (el manual del técnico o, si no ha arrastrado, el de agenda),
   dibuja la ruta entre paradas y alterna entre las vistas Lista y Mapa.
   Convive con field_app.js y field_app_reorder.js.

   Solo muestra los servicios de HOY: el mapa es la ruta del día, no un historial.
   Los ya cerrados salen con un pin apagado de "✓" (sin número y fuera de la ruta).

   Expone `window.visarFieldMap.refresh(tasks, route)` para que el reordenar por
   arrastre repinte pines y ruta sin recargar la página. */
(function () {
    "use strict";

    /* Alternador Lista / Mapa: muestra el panel elegido y marca el botón activo.
       Devuelve el nombre de la vista recién activada (o null si no cambió). */
    function initViewToggle(onShow) {
        var btns = document.querySelectorAll(".o_visar_view_btn");
        var panels = document.querySelectorAll(".o_visar_view_panel");
        if (!btns.length || !panels.length) {
            return;
        }
        btns.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var view = btn.getAttribute("data-view");
                btns.forEach(function (b) {
                    b.classList.toggle("active", b === btn);
                });
                panels.forEach(function (p) {
                    p.classList.toggle("d-none", p.getAttribute("data-view") !== view);
                });
                if (onShow) {
                    onShow(view);
                }
            });
        });
    }

    function parseJsonAttr(el, attr) {
        try {
            return JSON.parse(el.getAttribute(attr) || "[]");
        } catch (e) {
            return [];
        }
    }

    function initMap() {
        var el = document.getElementById("visar-field-map");
        if (!el || typeof window.L === "undefined") {
            return;
        }

        var L = window.L;
        // Centro por defecto: Monterrey, MX (se ajusta a los marcadores si hay).
        var map = L.map(el).setView([25.6866, -100.3161], 11);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: "&copy; OpenStreetMap",
        }).addTo(map);

        // Capas repintables (pines + ruta): se limpian en cada `draw`.
        var overlay = L.layerGroup().addTo(map);
        var bounds = null;

        function draw(tasks, route) {
            overlay.clearLayers();
            // Servicios ploteables, en orden de parada; los cerrados no tienen
            // número y van al final (`order` nulo ordena como Infinity).
            var located = tasks.filter(function (t) {
                return t.has_coords;
            }).sort(function (a, b) {
                return (a.order || Infinity) - (b.order || Infinity);
            });

            var fitLayers = [];
            // --- Ruta: polilínea sobre las calles (Mapbox) o, si no hay, líneas
            //     rectas entre paradas en orden como respaldo visual. ---
            var pending = located.filter(function (t) {
                return !t.done;
            });
            if (route && route.length > 1) {
                fitLayers.push(L.polyline(route, {
                    color: "#0d6efd", weight: 5, opacity: 0.7,
                }).addTo(overlay));
            } else if (pending.length > 1) {
                fitLayers.push(L.polyline(pending.map(function (t) {
                    return [t.lat, t.lng];
                }), {
                    color: "#0d6efd", weight: 4, opacity: 0.5, dashArray: "6 8",
                }).addTo(overlay));
            }

            located.forEach(function (t) {
                var marker = L.marker([t.lat, t.lng], {
                    icon: stopIcon(L, t),
                }).addTo(overlay);
                var link = document.createElement("a");
                link.href = t.url;
                link.textContent = t.name || "Ver servicio";
                var html = "<strong>" + link.outerHTML + "</strong>";
                if (t.client) {
                    html += "<br/>" + escapeHtml(t.client);
                }
                if (t.address) {
                    html += '<br/><span class="small text-muted">' +
                        escapeHtml(t.address) + "</span>";
                }
                marker.bindPopup(html);
                fitLayers.push(marker);
            });

            bounds = fitLayers.length
                ? L.featureGroup(fitLayers).getBounds().pad(0.2)
                : null;
        }

        // Encuadra a todos los marcadores (y la ruta si la hay); maxZoom evita
        // acercarse de más con una sola parada.
        function fitToMarkers() {
            if (bounds && bounds.isValid()) {
                map.fitBounds(bounds, { maxZoom: 16 });
            }
        }

        draw(parseJsonAttr(el, "data-tasks"), parseJsonAttr(el, "data-route"));
        fitToMarkers();

        // El mapa arranca oculto (d-none): Leaflet calcula mal tamaño y encuadre.
        // Al mostrar la vista Mapa hay que invalidar el tamaño y RE-encuadrar.
        initViewToggle(function (view) {
            if (view === "map") {
                map.invalidateSize();
                fitToMarkers();
            }
        });

        // API para field_app_reorder.js: repintar tras guardar un orden nuevo.
        // No re-encuadra: reordenar no cambia QUÉ paradas hay, solo su número,
        // y mover la cámara bajo el dedo del técnico sería desconcertante.
        window.visarFieldMap = {
            refresh: function (tasks, route) {
                draw(tasks, route);
            },
        };
    }

    /* Pin de parada como divIcon: gota azul con el número de parada; apagada y
       con "✓" cuando el servicio ya se cerró. */
    function stopIcon(L, task) {
        var done = !!task.done;
        var label = done ? "✓" : (task.order == null ? "" : String(task.order));
        var cls = "o-visar-route-marker" + (done ? " o-visar-route-marker-done" : "");
        return L.divIcon({
            className: cls,
            html: '<span class="o-visar-route-marker-pin"></span>' +
                '<span class="o-visar-route-marker-num">' + label + "</span>",
            iconSize: [30, 42],
            iconAnchor: [15, 42],
            popupAnchor: [0, -38],
        });
    }

    function escapeHtml(str) {
        var div = document.createElement("div");
        div.textContent = str == null ? "" : String(str);
        return div.innerHTML;
    }

    function init() {
        // Si no hay mapa en la página, al menos deja funcionar el alternador.
        if (document.getElementById("visar-field-map")) {
            initMap();
        } else {
            initViewToggle(null);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
