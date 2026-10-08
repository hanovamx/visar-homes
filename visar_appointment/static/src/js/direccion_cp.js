/* Código postal sugerido desde calle + número + colonia, en el formulario de
 * dirección del sitio.
 *
 * Vive AQUÍ y no dentro de la plantilla a propósito. La plantilla
 * `visar_delivery_address_fields` tiene una copia por website (creada al editar
 * la página desde el editor), y esa copia es la que se sirve: el `-u` actualiza
 * la del módulo y la copia se queda como estaba. El 7-oct-2026 este código iba
 * dentro de la plantilla y por eso nunca llegó a la página. El bundle del
 * frontend no tiene copias: lo que hay aquí es lo que se sirve.
 *
 * Por lo mismo no da nada por sentado sobre el HTML: busca los campos por id en
 * el momento del evento, y si el renglón del aviso no existe, lo crea.
 *
 * Reglas:
 *   - solo escribe en el campo si está vacío o si lo que hay lo puso esta misma
 *     sugerencia. Lo que tecleó la persona nunca se pisa;
 *   - es una sugerencia (el CP coincide ~la mitad de las veces): el campo sigue
 *     editable y el aviso dice que lo corrija si no es el suyo;
 *   - con la función apagada en Ajustes → Visar el servidor contesta
 *     `found: false` y aquí no pasa nada.
 *
 * SIN `DOMContentLoaded` y con los listeners en `document`: este archivo viaja
 * en el bundle diferido, igual que `wizard_poliza.js` (ver ahí el porqué).
 */
(function () {
    const CAMPOS = ['visar_addr_street', 'visar_addr_ext_num', 'visar_addr_neighborhood'];
    let sugerido = '';
    let consultado = '';

    function aviso(zip) {
        let hint = document.getElementById('visar_cp_hint');
        if (!hint) {
            hint = document.createElement('div');
            hint.id = 'visar_cp_hint';
            hint.className = 'form-text mt-2 text-muted';
            const feedback = document.getElementById('visar_cp_feedback');
            const ancla = feedback || zip.closest('.row') || zip;
            ancla.parentNode.insertBefore(hint, ancla);
        }
        return hint;
    }

    document.addEventListener('focusout', function (ev) {
        if (!ev.target || CAMPOS.indexOf(ev.target.id) === -1) {
            return;
        }
        const zip = document.getElementById('visar_addr_zip');
        const valores = CAMPOS.map(function (id) {
            const el = document.getElementById(id);
            return el ? el.value.trim() : '';
        });
        if (!zip || valores.some(function (v) { return !v; })) {
            return;
        }
        if (zip.value && zip.value !== sugerido) {
            return;
        }
        const clave = valores.join('|');
        if (clave === consultado) {
            return;
        }
        consultado = clave;
        fetch('/appointment/visar/address-cp?street=' + encodeURIComponent(valores[0])
              + '&ext_num=' + encodeURIComponent(valores[1])
              + '&neighborhood=' + encodeURIComponent(valores[2]), {
            headers: {'X-Requested-With': 'XMLHttpRequest'},
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (!data || !data.found) {
                return;
            }
            if (zip.value && zip.value !== sugerido) {
                return;
            }
            zip.value = data.zip;
            sugerido = data.zip;
            aviso(zip).textContent = 'Sugerimos el código postal ' + data.zip
                + ' por tu dirección. Si no es el tuyo, corrígelo.';
            // El formulario ya sabe llenar municipio y zona al cambiar el CP.
            zip.dispatchEvent(new Event('change', {bubbles: true}));
        }).catch(function () {});
    });

    document.addEventListener('input', function (ev) {
        if (!ev.target || ev.target.id !== 'visar_addr_zip') {
            return;
        }
        const hint = document.getElementById('visar_cp_hint');
        if (hint && ev.target.value !== sugerido) {
            hint.textContent = '';
        }
    });
})();
