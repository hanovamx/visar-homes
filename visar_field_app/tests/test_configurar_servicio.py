# -*- coding: utf-8 -*-
"""Un servicio especializado nuevo se puede configurar sin ayuda (25-sep-2026).

Visar intentó dar de alta un tratamiento antialacranes y se quedó atascado en el
primer paso: el campo "Se cotiza cuando la hoja marca" caía como última fila suelta
de la pestaña Ventas, debajo de dos bloques de comercio electrónico, y el catálogo
de "Servicios identificados" no tenía menú por el que abrirse.

Lo que se protege aquí: que el campo esté donde se configura el servicio (junto a
`service_tracking` y `project_id`) y que el catálogo tenga forma de abrirse.
"""
from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestConfigurarServicioCotizado(TransactionCase):

    def _arch_producto(self):
        vista = self.env.ref('product.product_template_form_view')
        return etree.fromstring(
            self.env['product.template'].get_view(vista.id, 'form')['arch'].encode())

    def test_el_disparador_vive_junto_a_lo_que_se_configura_con_el(self):
        raiz = self._arch_producto()
        campo = raiz.xpath("//field[@name='visar_quote_trigger']")
        self.assertTrue(campo, "el campo tiene que estar en el formulario")

        paginas = [n.get('name') for n in campo[0].iterancestors() if n.tag == 'page']
        self.assertEqual(paginas[0], 'general_information',
                         "va en Información general, no perdido al final de Ventas")
        hermanos = [h.get('name') for h in campo[0].getparent()]
        self.assertIn('service_tracking', hermanos)
        self.assertIn('project_id', hermanos,
                      "los tres ajustes del servicio cotizado, en el mismo grupo")

    def test_no_estorba_en_productos_que_no_son_servicio(self):
        campo = self._arch_producto().xpath("//field[@name='visar_quote_trigger']")[0]

        self.assertEqual(campo.get('invisible'), "type != 'service'")

    def test_el_catalogo_de_servicios_identificados_se_puede_abrir(self):
        """Se buscan por modelo y no por xmlid: van sin xmlid a propósito, porque
        Odoo borra al final de la actualización los `ir.model.data` de su propio
        módulo que no vengan de un fichero de datos (medido el 25-sep-2026)."""
        accion = self.env['ir.actions.act_window'].search(
            [('res_model', '=', 'x_visar_servicio_identificado')], limit=1)
        self.assertTrue(accion, "sin acción no hay forma de listar las etiquetas")

        menu = self.env['ir.ui.menu'].search(
            [('action', '=', 'ir.actions.act_window,%s' % accion.id)], limit=1)
        self.assertTrue(menu, "y sin menú nadie la encuentra")
        self.assertEqual(menu.parent_id,
                         self.env.ref('industry_fsm.fsm_menu_settings'),
                         "junto a las plantillas de hoja de trabajo")

    def test_sembrar_dos_veces_no_duplica_el_menu(self):
        from odoo.addons.visar_field_app.hooks import _ensure_catalog_menu
        antes = self.env['ir.ui.menu'].search_count([])
        acciones = self.env['ir.actions.act_window'].search_count(
            [('res_model', '=', 'x_visar_servicio_identificado')])

        _ensure_catalog_menu(self.env, 'x_visar_servicio_identificado',
                             "Servicios identificados (valoración)")

        self.assertEqual(self.env['ir.ui.menu'].search_count([]), antes)
        self.assertEqual(self.env['ir.actions.act_window'].search_count(
            [('res_model', '=', 'x_visar_servicio_identificado')]), acciones)

    def test_el_enlace_producto_etiqueta_sigue_siendo_por_nombre(self):
        """Es la regla que hay que explicar al configurar: el texto de la etiqueta y
        el del disparador tienen que coincidir."""
        etiqueta = self.env['x_visar_servicio_identificado'].create(
            {'x_name': "Alacranes de prueba"})
        producto = self.env['product.template'].create({
            'name': "Tratamiento de prueba", 'type': 'service',
            'visar_quote_trigger': "  alacranes DE PRUEBA  "})

        mapa = self.env['project.task']._visar_quote_products()

        self.assertIn(etiqueta.x_name.strip().lower(), mapa)
        self.assertEqual(mapa[etiqueta.x_name.strip().lower()], producto,
                         "coincide sin distinguir mayúsculas ni espacios")
