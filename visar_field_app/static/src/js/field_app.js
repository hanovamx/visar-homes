/* Visar - App de Campo: pad de firma sobre <canvas>, vanilla JS (sin OWL).
   Vuelca la firma a un input oculto al enviar el formulario de cierre. */
(function () {
    "use strict";

    function initSignaturePad() {
        var canvas = document.getElementById("visar-signature-pad");
        if (!canvas) {
            return;
        }
        var ctx = canvas.getContext("2d");
        var drawing = false;
        var dirty = false;

        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.strokeStyle = "#000";

        function pos(ev) {
            var rect = canvas.getBoundingClientRect();
            var point = ev.touches ? ev.touches[0] : ev;
            return {
                x: (point.clientX - rect.left) * (canvas.width / rect.width),
                y: (point.clientY - rect.top) * (canvas.height / rect.height),
            };
        }

        function start(ev) {
            ev.preventDefault();
            drawing = true;
            dirty = true;
            var p = pos(ev);
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
        }

        function move(ev) {
            if (!drawing) {
                return;
            }
            ev.preventDefault();
            var p = pos(ev);
            ctx.lineTo(p.x, p.y);
            ctx.stroke();
        }

        function end() {
            drawing = false;
        }

        canvas.addEventListener("mousedown", start);
        canvas.addEventListener("mousemove", move);
        canvas.addEventListener("mouseup", end);
        canvas.addEventListener("mouseleave", end);
        canvas.addEventListener("touchstart", start, { passive: false });
        canvas.addEventListener("touchmove", move, { passive: false });
        canvas.addEventListener("touchend", end);

        var clearBtn = document.getElementById("visar-signature-clear");
        if (clearBtn) {
            clearBtn.addEventListener("click", function () {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                dirty = false;
                var input = document.getElementById("visar-signature-data");
                if (input) {
                    input.value = "";
                }
            });
        }

        var form = canvas.closest("form");
        if (form) {
            form.addEventListener("submit", function (ev) {
                // Cierre válido solo con firma (canvas dibujado) Y nombre.
                var nameInput = document.getElementById("visar-signature-name");
                var name = nameInput ? nameInput.value.trim() : "";
                var msg = form.querySelector(".o_visar_close_msg");
                if (!dirty || !name) {
                    ev.preventDefault();
                    if (msg) {
                        msg.classList.remove("d-none");
                    }
                    return;
                }
                var input = document.getElementById("visar-signature-data");
                if (input) {
                    input.value = canvas.toDataURL("image/png");
                }
            });
        }
    }

    /* Cuenta regresiva de "Esperando cliente": lee data-start (ISO) + data-minutes,
       recalcula al cargar (sobrevive recargas) y al expirar dispara alarma (beeps
       WebAudio + vibración + banner) y revela "Cliente no llegó". El audio en móvil
       exige un gesto del usuario: se re-arma con el primer toque tras la recarga. */
    function initWaiting() {
        var box = document.getElementById("visar-waiting");
        if (!box) {
            return;
        }
        var startStr = box.getAttribute("data-start");
        var minutes = parseFloat(box.getAttribute("data-minutes")) || 10;
        var start = startStr ? new Date(startStr).getTime() : NaN;
        if (isNaN(start)) {
            return;
        }
        var deadline = start + minutes * 60000;
        var clock = box.querySelector(".o_visar_waiting_clock");
        var noshow = document.querySelector(".o_visar_noshow");
        var fired = false;
        var audioCtx = null;

        function unlockAudio() {
            try {
                var Ctx = window.AudioContext || window.webkitAudioContext;
                if (Ctx && !audioCtx) {
                    audioCtx = new Ctx();
                }
                if (audioCtx && audioCtx.state === "suspended") {
                    audioCtx.resume();
                }
            } catch (e) {
                /* audio no disponible */
            }
        }
        document.addEventListener("pointerdown", unlockAudio, { once: true });
        document.addEventListener("touchstart", unlockAudio, { once: true });

        function beep() {
            if (!audioCtx) {
                return;
            }
            if (audioCtx.state === "suspended") {
                audioCtx.resume();
            }
            var t0 = audioCtx.currentTime;
            for (var i = 0; i < 6; i++) {
                var osc = audioCtx.createOscillator();
                var gain = audioCtx.createGain();
                osc.type = "square";
                osc.frequency.value = 880;
                var t = t0 + i * 0.4;
                gain.gain.setValueAtTime(0.0001, t);
                gain.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
                gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
                osc.connect(gain).connect(audioCtx.destination);
                osc.start(t);
                osc.stop(t + 0.28);
            }
        }

        function fireAlarm() {
            if (fired) {
                return;
            }
            fired = true;
            box.classList.remove("alert-warning");
            box.classList.add("alert-danger", "o_visar_waiting_expired");
            if (clock) {
                clock.textContent = "¡Tiempo!";
            }
            if (noshow) {
                noshow.classList.remove("d-none");
            }
            beep();
            if (navigator.vibrate) {
                navigator.vibrate([400, 200, 400, 200, 400]);
            }
        }

        function tick() {
            var remaining = deadline - Date.now();
            if (remaining <= 0) {
                fireAlarm();
                return;
            }
            var total = Math.floor(remaining / 1000);
            var mm = Math.floor(total / 60);
            var ss = total % 60;
            if (clock) {
                clock.textContent =
                    (mm < 10 ? "0" : "") + mm + ":" + (ss < 10 ? "0" : "") + ss;
            }
        }
        tick();
        var handle = setInterval(function () {
            tick();
            if (fired) {
                clearInterval(handle);
            }
        }, 1000);
    }

    /* Subfichas one2many: "Agregar" clona una tarjeta inerte (inputs disabled,
       nombres con __IDX__) y la activa; "Eliminar" quita su tarjeta. Un solo POST
       envía todas las tarjetas presentes. */
    function initO2M() {
        document.querySelectorAll(".o_visar_o2m").forEach(function (box) {
            var cards = box.querySelector(".o_visar_o2m_cards");
            var tpl = box.querySelector(".o_visar_o2m_template");
            var addBtn = box.querySelector(".o_visar_o2m_add");
            if (!cards) {
                return;
            }
            // Índices nuevos: 'n0', 'n1'… (no colisionan con los numéricos existentes).
            var counter = 0;

            if (addBtn && tpl) {
                addBtn.addEventListener("click", function () {
                    var idx = "n" + counter++;
                    var tmp = document.createElement("div");
                    tmp.innerHTML = tpl.innerHTML.replace(/__IDX__/g, idx);
                    var card = tmp.firstElementChild;
                    if (!card) {
                        return;
                    }
                    card.querySelectorAll("[disabled]").forEach(function (el) {
                        el.removeAttribute("disabled");
                    });
                    cards.appendChild(card);
                    evalCondFields();
                });
            }

            cards.addEventListener("click", function (ev) {
                var rm = ev.target.closest(".o_visar_o2m_remove");
                if (!rm) {
                    return;
                }
                ev.preventDefault();
                var card = rm.closest(".o_visar_o2m_card");
                if (card) {
                    card.remove();
                }
            });
        });
    }

    /* Ayuda por campo: toca "ⓘ" para mostrar/ocultar el texto de ayuda. Delegado
       en document para que funcione también en las tarjetas clonadas dinámicamente. */
    function initHelp() {
        document.addEventListener("click", function (ev) {
            var btn = ev.target.closest(".o_visar_help_btn");
            if (!btn) {
                return;
            }
            ev.preventDefault();
            var wrap = btn.closest(".o_visar_help");
            var txt = wrap && wrap.querySelector(".o_visar_help_text");
            if (txt) {
                txt.classList.toggle("o_show");
            }
        });
    }

    /* Token CSRF de la página (cualquier form lo trae como input oculto). */
    function csrfToken() {
        var el = document.querySelector('input[name="csrf_token"]');
        return el ? el.value : "";
    }

    /* POST por fetch (AJAX): así subir/borrar fotos NO recarga la página y no se
       pierde nada de lo escrito en la hoja de trabajo. El controlador responde JSON
       cuando ve la cabecera X-Requested-With. Nunca rechaza: resuelve null en error
       (el llamador recae en recargar, que preserva la foto ya persistida). */
    function postForm(action, formData) {
        if (!formData.has("csrf_token")) {
            formData.append("csrf_token", csrfToken());
        }
        return fetch(action, {
            method: "POST",
            body: formData,
            headers: { "X-Requested-With": "XMLHttpRequest" },
            credentials: "same-origin",
        })
            .then(function (resp) {
                return resp.ok ? resp.json().catch(function () { return null; }) : null;
            })
            .catch(function () { return null; });
    }

    /* Construye la miniatura de una foto de campo principal (misma estructura que
       renderiza el servidor: imagen + botón "×" con la ruta de borrado). */
    function mainThumb(taskId, field, id) {
        var col = document.createElement("div");
        col.className = "col-4";
        var wrap = document.createElement("div");
        wrap.className = "o_visar_photo position-relative";
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-danger o_visar_photo_x";
        btn.setAttribute("aria-label", "Eliminar foto");
        btn.setAttribute(
            "data-action",
            "/visar/field/task/" + taskId + "/ws-photo/" + field + "/" + id + "/delete");
        btn.innerHTML = "&#215;";
        var img = document.createElement("img");
        img.src = "/visar/field/task/" + taskId + "/image/" + id;
        img.className = "img-fluid rounded o_visar_photo_img";
        wrap.appendChild(btn);
        wrap.appendChild(img);
        col.appendChild(wrap);
        return col;
    }

    /* Galería viva de campos-foto principales: "Subir fotos" adjunta por AJAX y
       repinta las miniaturas SIN recargar; si no hay archivos elegidos, abre el
       selector. Todo lo demás de la hoja de trabajo queda intacto. */
    function initWsPhotos() {
        document.querySelectorAll(".o_visar_ws_photo_upload").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var box = btn.closest(".o_visar_ws_photos");
                var input = box && box.querySelector(".o_visar_ws_photo_input");
                if (!input) {
                    return;
                }
                if (!input.files || !input.files.length) {
                    input.click();
                    return;
                }
                var taskId = box.getAttribute("data-task");
                var field = box.getAttribute("data-field");
                var fd = new FormData();
                for (var i = 0; i < input.files.length; i++) {
                    fd.append("photos", input.files[i]);
                }
                btn.disabled = true;
                postForm(btn.getAttribute("data-action"), fd).then(function (data) {
                    btn.disabled = false;
                    if (!data || !data.ok) {
                        window.location.reload();
                        return;
                    }
                    input.value = "";
                    var grid = box.querySelector(".o_visar_photo_grid");
                    if (grid) {
                        grid.innerHTML = "";
                        (data.photos || []).forEach(function (id) {
                            grid.appendChild(mainThumb(taskId, field, id));
                        });
                    }
                });
            });
        });
    }

    /* Fotos: toca una imagen para revelar su "×"; púlsala para borrar por AJAX (sin
       recargar → no se pierden ediciones sin guardar). Delegado en document → sirve
       para galerías principales y de tarjeta (incl. clonadas). */
    function initPhotoActions() {
        document.addEventListener("click", function (ev) {
            var x = ev.target.closest(".o_visar_photo_x");
            if (x) {
                ev.preventDefault();
                var action = x.getAttribute("data-action");
                if (!action) {
                    return;
                }
                var thumb = x.closest(".col-4") || x.closest(".o_visar_photo");
                x.disabled = true;
                postForm(action, new FormData()).then(function (data) {
                    if (!data || !data.ok) {
                        window.location.reload();
                        return;
                    }
                    if (thumb) {
                        thumb.remove();
                    }
                });
                return;
            }
            var img = ev.target.closest(".o_visar_photo_img");
            if (img) {
                var card = img.closest(".o_visar_photo");
                if (card) {
                    card.classList.toggle("o_show");
                }
            }
        });
    }

    /* Campos condicionales ("Especifique cuál otro"): se muestran solo cuando el
       campo que los controla tiene 'Otro' elegido. Delegado en document → funciona
       también en tarjetas clonadas. Selección: value === trigger. Múltiple (m2m):
       la casilla de la etiqueta 'Otro' (value === trigger) está marcada. */
    function evalCondFields() {
        document.querySelectorAll(".o_visar_condfield").forEach(function (el) {
            var ctrl = el.getAttribute("data-showif");
            if (!ctrl) {
                return;
            }
            var val = el.getAttribute("data-showif-val");
            var show;
            if (el.getAttribute("data-showif-kind") === "many2many") {
                var cb = document.querySelector(
                    '[name="' + ctrl + '"][value="' + val + '"]');
                show = !!(cb && cb.checked);
            } else {
                var sel = document.querySelector('[name="' + ctrl + '"]');
                show = !!(sel && sel.value === val);
            }
            el.classList.toggle("d-none", !show);
        });
    }

    function init() {
        initSignaturePad();
        initO2M();
        initHelp();
        initWsPhotos();
        initPhotoActions();
        initWaiting();
        evalCondFields();
        document.addEventListener("change", evalCondFields);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
