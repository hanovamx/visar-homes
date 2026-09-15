# -*- coding: utf-8 -*-
""""Hoja de trabajo — Completada" solo cuando está guardada y validada (REQ-005).

El indicador de la ficha FSM es nativo y se enciende con `worksheet_count`, que
cuenta si EXISTE el registro de la hoja. La app lo crea al pulsar "Comenzar
servicio" (necesita un id para sembrar las áreas obligatorias), así que un
servicio recién empezado aparecía con su hoja completada.

Las tres situaciones que hay que distinguir —recién comenzado, borrador guardado,
hoja válida guardada— se montan con los mismos sellos que escribe la app:
`visar_worksheet_draft_at` para el borrador y `visar_worksheet_saved_at` para el
guardado válido (`controllers/main.py`, ruta `worksheet/save`).
"""
from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestHojaCompletada(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plantilla = cls.env.ref('industry_fsm_report.fsm_worksheet_template')
        cls.proyecto = cls.env['project.project'].create({
            'name': 'FSM Hoja REQ-005', 'is_fsm': True,
            'allow_worksheets': True,
            'worksheet_template_id': cls.plantilla.id,
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({'name': 'Cliente Hoja REQ-005'})

    def _tarea(self):
        tarea = self.env['project.task'].create({
            'name': 'Servicio Hoja REQ-005', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id})
        self.assertTrue(tarea.worksheet_template_id, "la tarea hereda la plantilla")
        return tarea

    def _comenzar_servicio(self, tarea):
        """Lo que hace la app al habilitar la captura: crea la hoja vacía."""
        modelo = self.env[tarea.worksheet_template_id.sudo().model_id.model]
        return modelo.sudo().create({'x_project_task_id': tarea.id})

    def _indicador_completada(self, tarea):
        """¿Se vería el botón verde "Completada" en la ficha?

        Se evalúa el `invisible` REAL de la vista con los valores del registro: si
        alguien cambia la condición en el XML, esta prueba lo dice.
        """
        arch = etree.fromstring(
            self.env['project.task'].get_view(view_type='form')['arch'])
        boton = arch.xpath("//button[contains(@class, 'oe_worksheet_completed')]")
        self.assertTrue(boton, "el botón nativo de hoja completada sigue en la vista")
        campos = {
            'partner_id': tarea.partner_id.id,
            'allow_worksheets': tarea.allow_worksheets,
            'worksheet_template_id': tarea.worksheet_template_id.id,
            'worksheet_count': tarea.worksheet_count,
            'has_template_ancestor': tarea.has_template_ancestor,
            'has_project_template': tarea.has_project_template,
            'is_fsm': tarea.is_fsm,
        }
        return not safe_eval(boton[0].get('invisible'), dict(campos))

    def test_al_comenzar_el_servicio_no_esta_completada(self):
        tarea = self._tarea()
        self._comenzar_servicio(tarea)
        tarea.invalidate_recordset(['worksheet_count'])
        self.assertEqual(tarea.worksheet_count, 0)
        self.assertFalse(self._indicador_completada(tarea))

    def test_un_borrador_tampoco_la_completa(self):
        tarea = self._tarea()
        self._comenzar_servicio(tarea)
        tarea.visar_worksheet_draft_at = '2026-09-15 10:00:00'
        tarea.invalidate_recordset(['worksheet_count'])
        self.assertEqual(tarea.worksheet_count, 0)
        self.assertFalse(self._indicador_completada(tarea))

    def test_guardar_la_hoja_valida_si_la_completa(self):
        tarea = self._tarea()
        self._comenzar_servicio(tarea)
        tarea.visar_worksheet_saved_at = '2026-09-15 10:30:00'
        tarea.invalidate_recordset(['worksheet_count'])
        self.assertEqual(tarea.worksheet_count, 1)
        self.assertTrue(self._indicador_completada(tarea))

    def test_sin_hoja_guardada_no_se_ofrece_firmar_ni_enviar_el_reporte(self):
        """El mismo conteo alimenta los botones nativos de firma y envío: sobre una
        hoja vacía ofrecían firmar y mandarle el reporte al cliente."""
        tarea = self._tarea()
        self._comenzar_servicio(tarea)
        tarea.invalidate_recordset()
        self.assertFalse(tarea.display_satisfied_conditions_count)
        self.assertFalse(tarea.display_sign_report_primary)
        self.assertFalse(tarea.display_send_report_primary)

        tarea.visar_worksheet_saved_at = '2026-09-15 10:30:00'
        tarea.invalidate_recordset()
        self.assertTrue(tarea.display_satisfied_conditions_count)

    def test_de_punta_a_punta(self):
        tarea = self._tarea()
        hoja = self._comenzar_servicio(tarea)
        tarea.invalidate_recordset(['worksheet_count'])
        self.assertFalse(self._indicador_completada(tarea), "recién comenzado")

        tarea.visar_worksheet_draft_at = '2026-09-15 10:00:00'
        tarea.invalidate_recordset(['worksheet_count'])
        self.assertFalse(self._indicador_completada(tarea), "borrador sin validar")

        tarea.visar_worksheet_saved_at = '2026-09-15 10:30:00'
        tarea.invalidate_recordset(['worksheet_count'])
        self.assertTrue(self._indicador_completada(tarea), "hoja guardada")
        self.assertTrue(hoja.exists())
