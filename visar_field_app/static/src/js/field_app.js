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
            form.addEventListener("submit", function () {
                var input = document.getElementById("visar-signature-data");
                if (input && dirty) {
                    input.value = canvas.toDataURL("image/png");
                }
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initSignaturePad);
    } else {
        initSignaturePad();
    }
})();
