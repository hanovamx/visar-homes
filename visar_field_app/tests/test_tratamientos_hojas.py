# -*- coding: utf-8 -*-
"""Hojas, proyectos y visita de seguimiento de los tratamientos (22-sep-2026).

Termitas y chinches tienen hoja propia y proyecto propio: sin proyecto, una
cotización pagada crea la cita pero nunca la visita del técnico. Y la revisión
incluida se acuerda en la puerta: con la fecha en la hoja, Odoo crea la visita.
"""
from datetime import date, timedelta
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_field_app.hooks import (
    CHINCHES_NAME, TERMITAS_NAME, TRATAMIENTOS, seed_worksheet_templates)


@tagged('post_install', '-at_install')
class TestHojasDeTratamiento(TransactionCase):
    """Lo que el sembrador deja puesto. Corre sobre la BD ya migrada."""

    def _plantilla(self, nombre):
        return self.env['worksheet.template'].search([('name', '=', nombre)], limit=1)

    def test_las_dos_hojas_existen_con_sus_paginas(self):
        for nombre, paginas in ((TERMITAS_NAME, 3), (CHINCHES_NAME, 4)):
            plantilla = self._plantilla(nombre)
            self.assertTrue(plantilla, "falta la plantilla %s" % nombre)
            arch = self.env[plantilla.model_id.model].get_view(view_type='form')['arch']
            self.assertEqual(arch.count('<page '), paginas, nombre)

    def test_la_hoja_de_termitas_pide_lo_suyo(self):
        modelo = self.env[self._plantilla(TERMITAS_NAME).model_id.model]
        for campo in ('x_tipo_termita', 'x_estructuras_afectadas', 'x_nivel_dano',
                      'x_puntos_tratados', 'x_indicaciones_cliente',
                      'x_requiere_seguimiento', 'x_fecha_seguimiento'):
            self.assertIn(campo, modelo._fields, campo)
        linea = self.env[modelo._fields['x_puntos_tratados'].comodel_name]
        self.assertIn('x_perforaciones', linea._fields)
        self.assertIn('x_foto_evidencia', linea._fields)

    def test_la_hoja_de_chinches_pide_la_preparacion_del_cliente(self):
        modelo = self.env[self._plantilla(CHINCHES_NAME).model_id.model]
        for campo in ('x_nivel_infestacion', 'x_evidencia_encontrada',
                      'x_ropa_lavada', 'x_colchones_despejados',
                      'x_desorden_retirado', 'x_zonas_tratadas',
                      'x_requiere_seguimiento'):
            self.assertIn(campo, modelo._fields, campo)

    def test_cada_tratamiento_tiene_proyecto_con_su_hoja_y_su_producto(self):
        for nombre_proyecto, nombre_producto, nombre_hoja in TRATAMIENTOS:
            proyecto = self.env['project.project'].search(
                [('name', '=', nombre_proyecto)], limit=1)
            self.assertTrue(proyecto, nombre_proyecto)
            self.assertTrue(proyecto.is_fsm)
            self.assertEqual(proyecto.worksheet_template_id.name, nombre_hoja)
            producto = self.env['product.template'].search(
                [('name', '=', nombre_producto)], limit=1)
            if producto:
                self.assertEqual(producto.service_tracking, 'task_global_project')
                self.assertEqual(producto.project_id, proyecto,
                                 "una cotización pagada nace como visita aquí")

    def test_sembrar_dos_veces_no_duplica_nada(self):
        antes = self.env['worksheet.template'].search_count([])
        proyectos = self.env['project.project'].search_count([])
        seed_worksheet_templates(self.env)
        self.assertEqual(self.env['worksheet.template'].search_count([]), antes)
        self.assertEqual(self.env['project.project'].search_count([]), proyectos)


@tagged('post_install', '-at_install')
class TestVisitaDeSeguimiento(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.proyecto = cls.env['project.project'].create({
            'name': 'FSM tratamiento prueba', 'is_fsm': True, 'allow_billable': True,
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({'name': 'Cliente termitas seg'})
        cls.tecnico = cls.env['hr.employee'].create({'name': 'Tecnico tratamiento'})
        cls.manana = date.today() + timedelta(days=15)

    def _visita(self):
        return self.env['project.task'].create({
            'name': 'Tratamiento antitermita', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id,
            'visar_technician_ids': [(6, 0, self.tecnico.ids)]})

    def _con_hoja(self, tarea, requiere, fecha, franja="Tarde (13:00 - 17:00)"):
        """La hoja dice lo que el técnico capturó en el cierre."""
        return patch.object(type(tarea), '_visar_followup_request',
                            lambda self: (requiere, fecha, franja))

    def test_la_fecha_acordada_crea_la_visita_sin_cargo(self):
        tarea = self._visita()
        with self._con_hoja(tarea, True, self.manana):
            seguimiento = tarea._visar_followup_sync(self.tecnico)

        self.assertTrue(seguimiento)
        self.assertEqual(seguimiento.visar_followup_origin_task_id, tarea)
        self.assertEqual(seguimiento.project_id, self.proyecto)
        self.assertEqual(seguimiento.partner_id, self.cliente)
        self.assertEqual(seguimiento.visar_technician_ids, self.tecnico)
        self.assertFalse(seguimiento.sale_line_id, "va incluida: no se cobra")
        self.assertEqual(seguimiento.planned_date_begin.date(), self.manana)
        self.assertIn("Seguimiento", seguimiento.name)

    def test_la_franja_decide_la_hora(self):
        tarea = self._visita()
        with self._con_hoja(tarea, True, self.manana, "Mañana (9:00 - 13:00)"):
            manana = tarea._visar_followup_sync()
        otra = self._visita()
        with self._con_hoja(otra, True, self.manana, "Tarde (13:00 - 17:00)"):
            tarde = otra._visar_followup_sync()
        self.assertLess(manana.planned_date_begin, tarde.planned_date_begin)
        self.assertEqual(
            (manana.date_deadline - manana.planned_date_begin).seconds // 3600, 4)

    def test_guardar_otra_vez_no_duplica_y_corregir_la_fecha_la_mueve(self):
        tarea = self._visita()
        with self._con_hoja(tarea, True, self.manana):
            primera = tarea._visar_followup_sync()
            tarea._visar_followup_sync()
        self.assertEqual(len(tarea.visar_followup_task_ids), 1)

        otra_fecha = self.manana + timedelta(days=3)
        with self._con_hoja(tarea, True, otra_fecha):
            tarea._visar_followup_sync()
        self.assertEqual(len(tarea.visar_followup_task_ids), 1)
        self.assertEqual(primera.planned_date_begin.date(), otra_fecha)

    def test_sin_fecha_no_se_inventa_una(self):
        tarea = self._visita()
        with self._con_hoja(tarea, True, False):
            self.assertFalse(tarea._visar_followup_sync())
        self.assertFalse(tarea.visar_followup_task_ids)

    def test_desmarcar_el_seguimiento_retira_la_visita_no_empezada(self):
        tarea = self._visita()
        with self._con_hoja(tarea, True, self.manana):
            tarea._visar_followup_sync()
        with self._con_hoja(tarea, False, False):
            tarea._visar_followup_sync()
        self.assertFalse(tarea.visar_followup_task_ids)

    def test_una_hoja_sin_esos_campos_no_pide_seguimiento(self):
        """Fumigación y jardinería usan el mismo guardado: no puede reventar ahí."""
        tarea = self._visita()
        self.assertEqual(tarea._visar_followup_request(), (False, False, ''))
        self.assertFalse(tarea._visar_followup_sync())


@tagged('post_install', '-at_install')
class TestTratamientoEsServicio(TransactionCase):
    """Para el cliente, un tratamiento cotizado es un servicio suyo: sale en "Mis
    servicios" del agente aunque no sea agendable por la web (22-sep-2026)."""

    def test_el_producto_cuenta_como_servicio(self):
        producto = self.env['product.template'].search(
            [('visar_quote_trigger', '!=', False)], limit=1)
        if not producto:
            self.skipTest("Sin productos con cotización manual configurada")
        self.assertFalse(producto.visar_is_service,
                         "no es agendable por la web: eso exigiría tipo de cita")
        self.assertTrue(producto._visar_counts_as_service())
        self.assertTrue(producto.product_variant_id._visar_counts_as_service())

    def test_el_agente_lo_lista_en_mis_servicios(self):
        if 'visar.agent.tools' not in self.env:
            self.skipTest("visar_whatsapp_agent no está instalado")
        producto = self.env['product.template'].search(
            [('visar_quote_trigger', '!=', False)], limit=1)
        if not producto:
            self.skipTest("Sin productos con cotización manual configurada")
        cliente = self.env['res.partner'].create({
            'name': 'Cliente tratamiento agente', 'phone': '5218190007788'})
        # Lista de precios: REQ-004 no deja confirmar sin ella (la real la copia la
        # cotización del pedido de la visita).
        lista = self.env['product.pricelist'].create({'name': 'Lista tratamiento agente'})
        pedido = self.env['sale.order'].create({
            'partner_id': cliente.id, 'pricelist_id': lista.id,
            'order_line': [(0, 0, {'product_id': producto.product_variant_id.id,
                                   'price_unit': 3700.0})]})
        pedido.action_confirm()

        servicios = self.env['visar.agent.tools'].agent_customer_services(
            {'phone': '5218190007788', 'scope': 'all'})

        self.assertTrue(servicios['found'])
        self.assertIn(producto.name, [s['service'] for s in servicios['services']])
