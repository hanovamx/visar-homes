# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.44.0: "Diseño de jardín" pasa a cotizarse a mano.

Se vendía como adicional a precio fijo y resultó que el precio depende del
levantamiento (superficie, suelo, acceso, qué se instala). Ahora recorre el mismo
circuito que termitas y chinches: la hoja de la valoración lo marca, administración
le pone precio, y al pagarse nace su visita con su propia hoja.

`seed_worksheet_templates` crea la hoja, el proyecto y engancha el producto. Aquí
solo queda el DISPARADOR, que es configuración de negocio: qué palabra de "Servicios
identificados" pide este producto. Se pone únicamente si está vacío — si alguien ya
lo configuró a mano, manda lo suyo.

No hace falta apagar "Vendible en campo": `_visar_upsell_domain` exige
`service_tracking='no'`, y desde que el producto crea visita en su proyecto queda
fuera del catálogo de campo por sí solo.
"""
import logging

from odoo import api, SUPERUSER_ID

from odoo.addons.visar_field_app.hooks import seed_worksheet_templates

_logger = logging.getLogger(__name__)

# (nombre EXACTO del producto, etiqueta de "Servicios identificados" que lo dispara)
DISPARADOR = ("Diseño e instalación de áreas verdes", "Diseño de jardín")


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    seed_worksheet_templates(env)

    nombre, disparador = DISPARADOR
    producto = env['product.template'].with_context(active_test=False).search(
        [('name', '=', nombre)], limit=1)
    if not producto:
        _logger.info("Sin producto '%s': no se configura su disparador", nombre)
        return
    if producto.visar_quote_trigger:
        _logger.info("%s ya se cotiza con '%s': no se toca",
                     nombre, producto.visar_quote_trigger)
        return
    producto.visar_quote_trigger = disparador
    _logger.info("%s -> se cotiza cuando la hoja marca '%s'", nombre, disparador)

    # El enlace es por NOMBRE: sin la etiqueta en el catálogo, el técnico no tiene
    # dónde marcarlo y la cotización no nace nunca.
    Etiqueta = env['x_visar_servicio_identificado'].sudo()
    if not Etiqueta.search([('x_name', '=', disparador)], limit=1):
        Etiqueta.create({'x_name': disparador})
        _logger.info("Etiqueta '%s' agregada a Servicios identificados", disparador)
