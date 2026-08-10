/* Visar - App de Campo: captura de fotos POR CÁMARA (no galería).
 *
 * Por qué existe: `<input type="file" capture="environment">` es una PISTA, no una
 * garantía. Android Chrome abre la cámara; iOS Safari lo ignora (más aún junto a
 * `multiple`) y sigue ofreciendo "Fototeca". Para que la evidencia del servicio sea
 * de verdad del servicio, la foto se toma en vivo con getUserMedia y se entrega al
 * `<input type="file">` oculto vía DataTransfer — así el servidor NO cambia: sigue
 * recibiendo multipart y leyendo `files.getlist(...)`.
 *
 * Requiere contexto seguro (HTTPS). En HTTP `navigator.mediaDevices` no existe: se
 * avisa en pantalla en vez de fallar en silencio.
 *
 * Límites honestos: esto cierra el camino fácil (el carrete), no vuelve imposible
 * falsificar una foto (una cámara virtual seguiría pasando). Para eso haría falta
 * verificación del lado del servidor, que es otra tarea.
 */
(function () {
    "use strict";

    // Lado mayor de la foto guardada. El teléfono captura a resolución completa
    // (varios MB); a 1920 la evidencia sigue siendo legible y la subida es viable
    // con datos móviles. El PDF vuelve a reducir por su cuenta.
    var MAX_EDGE = 1920;
    var JPEG_QUALITY = 0.85;

    var overlay = null;   // panel de cámara (uno solo, reutilizado)
    var stream = null;    // MediaStream activo
    var target = null;    // .o_visar_capture que pidió la cámara
    var shots = [];       // File[] de esta sesión de captura

    function cameraSupported() {
        return !!(window.isSecureContext
            && navigator.mediaDevices
            && navigator.mediaDevices.getUserMedia);
    }

    function showError(box, message) {
        var err = box.querySelector(".o_visar_capture_err");
        if (err) {
            err.textContent = message;
            err.classList.remove("d-none");
        }
    }

    function clearError(box) {
        var err = box.querySelector(".o_visar_capture_err");
        if (err) {
            err.classList.add("d-none");
        }
    }

    /* ---------------------------------------------------------------- */
    /* Panel de cámara                                                   */
    /* ---------------------------------------------------------------- */

    function buildOverlay() {
        var el = document.createElement("div");
        el.className = "o_visar_cam_overlay d-none";
        el.innerHTML =
            '<div class="o_visar_cam_stage">' +
            '  <video class="o_visar_cam_video" playsinline="playsinline" muted="muted"></video>' +
            '  <div class="o_visar_cam_count badge text-bg-dark"></div>' +
            '</div>' +
            '<div class="o_visar_cam_strip"></div>' +
            '<div class="o_visar_cam_bar">' +
            '  <button type="button" class="btn btn-outline-light o_visar_cam_cancel">Cancelar</button>' +
            '  <button type="button" class="btn btn-light o_visar_cam_shoot" aria-label="Tomar foto"></button>' +
            '  <button type="button" class="btn btn-success o_visar_cam_done">Listo</button>' +
            '</div>';
        document.body.appendChild(el);

        el.querySelector(".o_visar_cam_shoot").addEventListener("click", shoot);
        el.querySelector(".o_visar_cam_done").addEventListener("click", function () {
            commit();
        });
        el.querySelector(".o_visar_cam_cancel").addEventListener("click", function () {
            close();
        });
        return el;
    }

    function refreshCount() {
        var badge = overlay.querySelector(".o_visar_cam_count");
        badge.textContent = shots.length ? shots.length + " foto(s)" : "";
        // Sin fotos, "Listo" no tiene nada que entregar.
        overlay.querySelector(".o_visar_cam_done").disabled = !shots.length;
    }

    function open(box) {
        if (!cameraSupported()) {
            showError(box, window.isSecureContext
                ? "Este dispositivo no permite abrir la cámara desde el navegador."
                : "La cámara requiere una conexión segura (https).");
            return;
        }
        clearError(box);
        target = box;
        shots = [];
        if (!overlay) {
            overlay = buildOverlay();
        }
        overlay.querySelector(".o_visar_cam_strip").innerHTML = "";
        refreshCount();
        overlay.classList.remove("d-none");
        document.body.classList.add("o_visar_cam_open");

        var video = overlay.querySelector(".o_visar_cam_video");
        navigator.mediaDevices.getUserMedia({
            // `ideal` y no `exact`: en una tablet sin cámara trasera `exact`
            // reventaría; así cae a la que haya.
            video: { facingMode: { ideal: "environment" } },
            audio: false,
        }).then(function (ms) {
            stream = ms;
            video.srcObject = ms;
            return video.play();
        }).catch(function (err) {
            close();
            var denied = err && (err.name === "NotAllowedError"
                || err.name === "SecurityError");
            showError(box, denied
                ? "Permiso de cámara denegado. Habilítelo en los ajustes del navegador."
                : "No se pudo abrir la cámara.");
        });
    }

    function close() {
        if (stream) {
            stream.getTracks().forEach(function (t) { t.stop(); });
            stream = null;
        }
        if (overlay) {
            var video = overlay.querySelector(".o_visar_cam_video");
            video.srcObject = null;
            overlay.classList.add("d-none");
        }
        document.body.classList.remove("o_visar_cam_open");
        shots = [];
        target = null;
    }

    function shoot() {
        if (!overlay || !stream) {
            return;
        }
        var video = overlay.querySelector(".o_visar_cam_video");
        var w = video.videoWidth;
        var h = video.videoHeight;
        if (!w || !h) {
            return;  // el stream aún no da frames
        }
        var scale = Math.min(1, MAX_EDGE / Math.max(w, h));
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(w * scale);
        canvas.height = Math.round(h * scale);
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) {
            if (!blob) {
                return;
            }
            // El frame de vídeo no trae EXIF, así que no hay orientación que corregir.
            var name = "foto-" + Date.now() + "-" + (shots.length + 1) + ".jpg";
            shots.push(new File([blob], name, { type: "image/jpeg" }));
            addStripThumb(blob);
            refreshCount();
        }, "image/jpeg", JPEG_QUALITY);
    }

    function addStripThumb(blob) {
        var strip = overlay.querySelector(".o_visar_cam_strip");
        var img = document.createElement("img");
        img.className = "o_visar_cam_thumb";
        img.src = URL.createObjectURL(blob);
        img.addEventListener("load", function () { URL.revokeObjectURL(img.src); });
        strip.appendChild(img);
    }

    /* ---------------------------------------------------------------- */
    /* Entrega al <input type="file"> oculto                             */
    /* ---------------------------------------------------------------- */

    /* Acumula: lo ya capturado en rondas anteriores NO se pierde al volver a abrir
       la cámara (el técnico puede tomar 2 fotos, cerrar, y agregar una tercera). */
    function commit() {
        var box = target;
        var taken = shots.slice();
        close();
        if (!box || !taken.length) {
            return;
        }
        var input = box.querySelector(".o_visar_capture_input");
        if (!input) {
            return;
        }
        var dt = new DataTransfer();
        var existing = input.files ? Array.prototype.slice.call(input.files) : [];
        existing.concat(taken).forEach(function (f) { dt.items.add(f); });
        input.files = dt.files;
        renderPending(box, input);
        // La galería principal sube por AJAX: se dispara su botón para que la foto
        // quede guardada ya (en las tarjetas o2m se manda al guardar la hoja).
        var uploader = box.parentElement
            && box.parentElement.querySelector(".o_visar_ws_photo_upload");
        if (uploader) {
            uploader.click();
        } else {
            // Tarjeta o2m: la validación de obligatoriedad mira los ficheros del
            // input, así que hay que revalidar tras entregar.
            input.dispatchEvent(new Event("change", { bubbles: true }));
        }
    }

    /* Miniaturas de lo capturado y aún NO guardado, con "×" para descartar antes
       de subir/guardar. */
    function renderPending(box, input) {
        var wrap = box.querySelector(".o_visar_capture_pending");
        if (!wrap) {
            return;
        }
        wrap.innerHTML = "";
        var files = input.files ? Array.prototype.slice.call(input.files) : [];
        files.forEach(function (file, index) {
            var col = document.createElement("div");
            col.className = "col-4";
            var holder = document.createElement("div");
            holder.className = "o_visar_photo position-relative";
            var img = document.createElement("img");
            img.className = "img-fluid rounded o_visar_photo_img";
            img.src = URL.createObjectURL(file);
            img.addEventListener("load", function () { URL.revokeObjectURL(img.src); });
            var x = document.createElement("button");
            x.type = "button";
            x.className = "btn btn-danger o_visar_photo_x o_visar_capture_drop";
            x.setAttribute("aria-label", "Descartar foto");
            x.dataset.index = String(index);
            x.innerHTML = "&#215;";
            holder.appendChild(x);
            holder.appendChild(img);
            col.appendChild(holder);
            wrap.appendChild(col);
        });
    }

    function dropPending(box, index) {
        var input = box.querySelector(".o_visar_capture_input");
        if (!input || !input.files) {
            return;
        }
        var dt = new DataTransfer();
        Array.prototype.slice.call(input.files).forEach(function (f, i) {
            if (i !== index) {
                dt.items.add(f);
            }
        });
        input.files = dt.files;
        renderPending(box, input);
        input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    /* ---------------------------------------------------------------- */
    /* Wiring (delegado: sirve en tarjetas o2m clonadas)                 */
    /* ---------------------------------------------------------------- */

    function init() {
        document.addEventListener("click", function (ev) {
            var openBtn = ev.target.closest(".o_visar_capture_open");
            if (openBtn) {
                open(openBtn.closest(".o_visar_capture"));
                return;
            }
            var drop = ev.target.closest(".o_visar_capture_drop");
            if (drop) {
                ev.preventDefault();
                ev.stopPropagation();  // no lo trate initPhotoActions (borrado servidor)
                dropPending(drop.closest(".o_visar_capture"),
                    parseInt(drop.dataset.index, 10));
                return;
            }
            var fallback = ev.target.closest(".o_visar_capture_fallback");
            if (fallback) {
                // Excepción autorizada por negocio: se quita `capture` para que el
                // selector ofrezca de verdad el carrete.
                var box = fallback.closest(".o_visar_capture");
                var input = box && box.querySelector(".o_visar_capture_input");
                if (input) {
                    input.removeAttribute("capture");
                    input.click();
                }
            }
        });

        // Archivos elegidos por la escotilla de galería: también se previsualizan.
        document.addEventListener("change", function (ev) {
            var input = ev.target.closest(".o_visar_capture_input");
            if (input) {
                renderPending(input.closest(".o_visar_capture"), input);
            }
        });

        // Al subir por AJAX la galería principal vacía el input; hay que limpiar
        // las miniaturas pendientes para no duplicarlas con las ya guardadas.
        document.addEventListener("visar:photos-uploaded", function (ev) {
            var box = ev.target && ev.target.querySelector(".o_visar_capture");
            if (box) {
                var input = box.querySelector(".o_visar_capture_input");
                if (input) {
                    renderPending(box, input);
                }
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
