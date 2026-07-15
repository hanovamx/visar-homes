/* Visar - App de Campo: vista de mapa de servicios (Leaflet + OpenStreetMap),
   vanilla JS (sin OWL). Plotea un pin numerado por servicio geolocalizado, en orden
   de agenda, y dibuja la ruta entre paradas; alterna entre las vistas Lista y Mapa.
   Convive con field_app.js. */
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

    function initMap() {
        var el = document.getElementById("visar-field-map");
        if (!el || typeof window.L === "undefined") {
            return;
        }

        var tasks;
        try {
            tasks = JSON.parse(el.getAttribute("data-tasks") || "[]");
        } catch (e) {
            tasks = [];
        }
        // Ruta que sigue las calles (Directions de Mapbox, calculada en el servidor
        // para no exponer el token). Puntos [[lat, lng], ...]. Vacío si no hay token.
        var route;
        try {
            route = JSON.parse(el.getAttribute("data-route") || "[]");
        } catch (e2) {
            route = [];
        }
        // Solo servicios geolocalizados, ordenados por parada (orden de agenda).
        var located = tasks.filter(function (t) {
            return t.has_coords;
        }).sort(function (a, b) {
            return (a.order || 0) - (b.order || 0);
        });

        var L = window.L;

        // Centro por defecto: Monterrey, MX (se ajusta a los marcadores si hay).
        var map = L.map(el).setView([25.6866, -100.3161], 11);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: "&copy; OpenStreetMap",
        }).addTo(map);

        // --- Ruta: polilínea sobre las calles (Mapbox) o, si no hay, líneas rectas
        //     entre paradas en orden como respaldo visual. ---
        var routeLayer = null;
        if (route && route.length > 1) {
            routeLayer = L.polyline(route, {
                color: "#0d6efd", weight: 5, opacity: 0.7,
            }).addTo(map);
        } else if (located.length > 1) {
            routeLayer = L.polyline(located.map(function (t) {
                return [t.lat, t.lng];
            }), {
                color: "#0d6efd", weight: 4, opacity: 0.5, dashArray: "6 8",
            }).addTo(map);
        }

        var markers = [];
        located.forEach(function (t) {
            // Pin numerado (número de parada) como los waypoints del mapa nativo.
            var marker = L.marker([t.lat, t.lng], {
                icon: numberedIcon(L, t.order),
            }).addTo(map);
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
            markers.push(marker);
        });

        // Encuadra a todos los marcadores (y la ruta si la hay); maxZoom evita
        // acercarse de más con una sola parada.
        var fitLayers = markers.slice();
        if (routeLayer) {
            fitLayers.push(routeLayer);
        }
        var group = fitLayers.length ? L.featureGroup(fitLayers) : null;
        function fitToMarkers() {
            if (group) {
                map.fitBounds(group.getBounds().pad(0.2), { maxZoom: 16 });
            }
        }
        fitToMarkers();

        // El mapa arranca oculto (d-none): Leaflet calcula mal tamaño y encuadre.
        // Al mostrar la vista Mapa hay que invalidar el tamaño y RE-encuadrar.
        initViewToggle(function (view) {
            if (view === "map") {
                map.invalidateSize();
                fitToMarkers();
            }
        });
    }

    /* Pin numerado como divIcon (número de parada centrado sobre la gota). */
    function numberedIcon(L, number) {
        var label = number == null ? "" : String(number);
        return L.divIcon({
            className: "o-visar-route-marker",
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
