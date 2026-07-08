/* Visar - App de Campo: vista de mapa de servicios (Leaflet + OpenStreetMap),
   vanilla JS (sin OWL). Plotea un marcador por servicio geolocalizado y alterna
   entre la vista Lista y la vista Mapa. Convive con field_app.js. */
(function () {
    "use strict";

    var LEAFLET_IMG = "/visar_field_app/static/src/lib/leaflet/images/";

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
        var located = tasks.filter(function (t) {
            return t.has_coords;
        });

        var L = window.L;
        // Icono explícito con rutas ABSOLUTAS al módulo. No se usa L.Icon.Default:
        // este antepone su `imagePath` a la URL y duplicaba la ruta (404). L.icon
        // (Icon base) usa las URLs tal cual.
        var icon = L.icon({
            iconUrl: LEAFLET_IMG + "marker-icon.png",
            iconRetinaUrl: LEAFLET_IMG + "marker-icon-2x.png",
            shadowUrl: LEAFLET_IMG + "marker-shadow.png",
            iconSize: [25, 41],
            iconAnchor: [12, 41],
            popupAnchor: [1, -34],
            shadowSize: [41, 41],
        });

        // Centro por defecto: Monterrey, MX (se ajusta a los marcadores si hay).
        var map = L.map(el).setView([25.6866, -100.3161], 11);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: "&copy; OpenStreetMap",
        }).addTo(map);

        var markers = [];
        located.forEach(function (t) {
            var marker = L.marker([t.lat, t.lng], { icon: icon }).addTo(map);
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

        var group = markers.length ? L.featureGroup(markers) : null;
        // Encuadra a todos los marcadores; maxZoom evita acercarse de más con uno solo.
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
