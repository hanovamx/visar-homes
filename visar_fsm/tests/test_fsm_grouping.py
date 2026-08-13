# -*- coding: utf-8 -*-
"""Agrupado de líneas de servicio en tareas FSM (visar_fsm).

Cubre la consolidación del COMBO: dos proyectos que apuntan al mismo proyecto
combinado generan UNA sola tarea cuando la cita trae trabajo de ambos, y la
etiqueta de grupos de servicio que sustituye al proyecto como eje de conteo.

Crean sus propios datos (no dependen del catálogo de la BD).
"""
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestFsmGrouping(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente FSM Test'})

        cls.project_fum = cls._make_project('FSM Fumigación Test')
        cls.project_jar = cls._make_project('FSM Áreas Verdes Test')
        cls.project_val = cls._make_project('FSM Valoración Test')
        cls.project_combo = cls._make_project('FSM Combinados Test')
        (cls.project_fum | cls.project_jar).write(
            {'visar_fsm_combined_project_id': cls.project_combo.id})

        # Catálogo de servicio: grupo -> dimensión -> producto. Es el enlace del que
        # cuelga `visar_service_group_ids`.
        cls.group_fum = cls._make_group('Fumigación Test', 'tst_fum')
        cls.group_jar = cls._make_group('Áreas Verdes Test', 'tst_jar')
        cls.product_fum = cls._make_service('Fumigación Test', cls.project_fum,
                                            cls.group_fum, 'tst_fum_int')
        cls.product_jar = cls._make_service('Poda Test', cls.project_jar,
                                            cls.group_jar, 'tst_jar_poda')
        cls.product_val = cls._make_service('Valoración Test', cls.project_val,
                                            None, None)
        cls.company = company

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @classmethod
    def _make_project(cls, name):
        return cls.env['project.project'].create({
            'name': name, 'is_fsm': True, 'company_id': cls.env.company.id})

    @classmethod
    def _make_group(cls, name, code):
        return cls.env['visar.service.group'].create({'name': name, 'code': code})

    @classmethod
    def _make_service(cls, name, project, group, dimension_code):
        tmpl = cls.env['product.template'].create({
            'name': name, 'type': 'service', 'invoice_policy': 'order',
            'list_price': 100.0, 'visar_is_service': True,
            'service_tracking': 'task_global_project', 'project_id': project.id,
            'taxes_id': [(6, 0, [])],
        })
        if group:
            cls.env['visar.service.dimension'].create({
                'group_id': group.id, 'name': name, 'code': dimension_code,
                'product_tmpl_id': tmpl.id})
        return tmpl

    def _confirm(self, products, addons=()):
        lines = [(0, 0, {'product_id': p.product_variant_id.id,
                         'product_uom_qty': 1}) for p in products]
        lines += [(0, 0, {'product_id': a.product_variant_id.id,
                          'product_uom_qty': 1}) for a in addons]
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'order_line': lines})
        order.action_confirm()
        return order

    @staticmethod
    def _tasks(order):
        return order.order_line.mapped('task_id')

    # ------------------------------------------------------------------
    # Agrupado
    # ------------------------------------------------------------------
    def test_01_un_servicio_una_tarea_en_su_proyecto(self):
        """Un solo servicio NO se consolida: la regla exige dos proyectos origen."""
        order = self._confirm([self.product_fum])
        tasks = self._tasks(order)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks.project_id, self.project_fum)
        self.assertTrue(tasks.sale_line_id, "sale_line_id sigue siendo obligatorio")

    def test_02_dos_lineas_del_mismo_proyecto_una_tarea(self):
        """Dos servicios del mismo proyecto ya caían en una tarea; sigue igual."""
        otro_fum = self._make_service('Fumigación Exterior Test', self.project_fum,
                                      self.group_fum, 'tst_fum_ext')
        order = self._confirm([self.product_fum, otro_fum])
        tasks = self._tasks(order)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks.project_id, self.project_fum)

    def test_03_combo_una_sola_tarea_en_el_proyecto_combinado(self):
        order = self._confirm([self.product_fum, self.product_jar])
        tasks = self._tasks(order)
        self.assertEqual(len(tasks), 1, "el combo genera UN solo servicio externo")
        self.assertEqual(tasks.project_id, self.project_combo)
        self.assertEqual(len(tasks.sale_line_id), 1,
                         "una sola línea representante")
        self.assertEqual(
            set(order.order_line.mapped('task_id').ids), {tasks.id},
            "todas las líneas cuelgan de la misma tarea")

    def test_04_tercer_servicio_no_combinable_queda_aparte(self):
        order = self._confirm([self.product_fum, self.product_jar, self.product_val])
        tasks = self._tasks(order)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(
            set(tasks.mapped('project_id')),
            {self.project_combo, self.project_val})

    def test_05_sin_proyecto_combinado_no_se_consolida(self):
        (self.project_fum | self.project_jar).write(
            {'visar_fsm_combined_project_id': False})
        order = self._confirm([self.product_fum, self.product_jar])
        tasks = self._tasks(order)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(set(tasks.mapped('project_id')),
                         {self.project_fum, self.project_jar})

    def test_06_proyecto_combinado_archivado_no_consolida(self):
        """Fallo seguro: mejor dos servicios externos que uno en un proyecto muerto."""
        self.project_combo.active = False
        order = self._confirm([self.product_fum, self.product_jar])
        tasks = self._tasks(order)
        self.assertEqual(len(tasks), 2)
        self.assertNotIn(self.project_combo, tasks.mapped('project_id'))

    def test_07_addon_cae_en_la_tarea_del_combo(self):
        addon = self.env['product.template'].create({
            'name': 'Estación de roedores Test', 'type': 'consu',
            'list_price': 50.0, 'taxes_id': [(6, 0, [])]})
        self.env['visar.product.optional.line'].create({
            'product_tmpl_id': self.product_fum.id,
            'optional_product_id': addon.id})
        order = self._confirm([self.product_fum, self.product_jar], addons=[addon])
        tasks = self._tasks(order)
        self.assertEqual(len(tasks), 1)
        addon_line = order.order_line.filtered(
            lambda l: l.product_id.product_tmpl_id == addon)
        self.assertEqual(addon_line.task_id, tasks)

    def test_08_tarea_combinada_se_renombra_con_sus_servicios(self):
        order = self._confirm([self.product_fum, self.product_jar])
        task = self._tasks(order)
        self.assertIn(self.group_fum.name, task.name)
        self.assertIn(self.group_jar.name, task.name)
        self.assertIn(order.name, task.name)

    def test_09_tarea_de_un_solo_proyecto_conserva_su_nombre(self):
        order = self._confirm([self.product_fum])
        task = self._tasks(order)
        self.assertNotIn(self.group_jar.name, task.name)

    # ------------------------------------------------------------------
    # Etiqueta de grupos de servicio (conteo por línea de negocio)
    # ------------------------------------------------------------------
    def test_10_etiqueta_de_servicios_del_combo(self):
        """La tarea combinada cuenta en las DOS líneas de negocio."""
        order = self._confirm([self.product_fum, self.product_jar])
        task = self._tasks(order)
        self.assertEqual(task.visar_service_group_ids,
                         self.group_fum | self.group_jar)

    def test_11_etiqueta_de_servicio_individual(self):
        order = self._confirm([self.product_fum])
        self.assertEqual(self._tasks(order).visar_service_group_ids, self.group_fum)

    def test_12_addon_no_aporta_grupo(self):
        addon = self.env['product.template'].create({
            'name': 'Extra sin dimensión Test', 'type': 'consu',
            'list_price': 10.0, 'taxes_id': [(6, 0, [])]})
        order = self._confirm([self.product_fum], addons=[addon])
        self.assertEqual(self._tasks(order).visar_service_group_ids, self.group_fum)

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------
    def test_13_no_se_permite_encadenar_proyectos_combinados(self):
        """Un combinado que a su vez apunta a otro dejaría el ruteo ambiguo."""
        with self.assertRaises(ValidationError):
            self.project_combo.visar_fsm_combined_project_id = self.project_fum.id

    def test_14_no_se_permite_apuntarse_a_si_mismo(self):
        with self.assertRaises(ValidationError):
            self.project_fum.visar_fsm_combined_project_id = self.project_fum.id
