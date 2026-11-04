# -*- coding: utf-8 -*-
"""Ajuste de medidas en campo (3-nov-2026).

Lo que se fija aquí, en orden de lo que cuesta si falla:

* que el cliente pague SOLO la diferencia: precio nuevo del mismo motor menos lo
  ya pagado, y que la factura del cobro sea exactamente esa diferencia;
* que no nazca otro servicio ni se le descuente otra vez la valoración;
* que sin foto y motivo no se aplique;
* los caminos de Visar: más chica (nada se devuelve, oficina avisada), cliente que
  no paga (incompleto, factura cancelada) y cierre con la diferencia sin pagar.

Usa el tabulador real de fumigación de la base; si no está, se salta.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_field_app.models.ajuste_medidas import PARAM_AJUSTE_COMISION
from odoo.addons.visar_field_app.models.upsell_servicio import PARAM_PRODUCTO_CREDITO

FOTO = [('casa.jpg', b'\xff\xd8\xff\xe0 foto de prueba', 'image/jpeg')]


@tagged('post_install', '-at_install')
class TestAjusteMedidas(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Dimension = env['visar.service.dimension']
        cls.interior = Dimension.search([('measure_type', '=', 'interior')], limit=1)
        cls.exterior = Dimension.search([
            ('measure_type', '=', 'exterior'), ('id', '!=', cls.interior.id)], limit=1)
        Template = env['product.template']
        cls.plantilla = (Template._visar_get_service_template_for_dimension(cls.interior)
                         if cls.interior else Template)
        cp = env['visar.zone.cp'].search([('zone_id.pricelist_id', '!=', False)], limit=1)
        cls.zona = cp.zone_id
        cls.saltar = not (cls.interior and cls.exterior and cls.plantilla and cls.zona
                          and cls.plantilla == Template._visar_get_service_template_for_dimension(
                              cls.exterior))
        if cls.saltar:
            return
        cls.cliente = env['res.partner'].create({
            'name': 'Cliente medidas (prueba)', 'zip': cp.name, 'phone': '8187654321'})
        cls.tecnico = env['hr.employee'].create({'name': 'Tecnico medidas'})
        cls.descuento = env['product.product'].create({
            'name': 'Descuento medidas prueba', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 0.0})
        env['ir.config_parameter'].sudo().set_param(PARAM_PRODUCTO_CREDITO, cls.descuento.id)
        cls.tramos_int = cls.plantilla._visar_tiers_for_dimension(cls.interior).filtered(
            lambda t: not t.is_valuation).sorted('m2_min')
        cls.tramo_ext = cls.plantilla._visar_tiers_for_dimension(cls.exterior).filtered(
            lambda t: not t.is_valuation).sorted('m2_min')[:1]

    def setUp(self):
        super().setUp()
        if self.saltar or len(self.tramos_int) < 2:
            self.skipTest("la base no tiene el tabulador de fumigación interior + exterior")

    # ------------------------------------------------------------------
    def _visita(self, tramo_interior=None, con_interior=True):
        """Pedido pagado con fumigación del tramo dado; devuelve su servicio."""
        tramo_interior = tramo_interior or self.tramos_int[0]
        items = [self.exterior._visar_quote_item(max(self.tramo_ext.m2_min, 1))[0]]
        if con_interior:
            items.insert(0, self.interior._visar_quote_item(max(tramo_interior.m2_min, 1))[0])
        Motor = self.env['appointment.type'].sudo()
        lineas = Motor._visar_build_sale_lines(items, self.zona)
        pedido = self.env['sale.order'].create({
            'partner_id': self.cliente.id,
            'pricelist_id': self.zona.pricelist_id.id,
            'order_line': [(0, 0, {
                'product_id': v['product_id'],
                'product_uom_qty': v.get('quantity') or 1,
                'price_unit': Motor._visar_list_unit_price(
                    self.env['product.product'].browse(v['product_id']), self.zona),
                'discount': v.get('discount') or 0.0,
            }) for v in lineas if not Motor._visar_skip_cart_line(v, self.zona)]})
        if not con_interior:
            # Como en producción: la fumigación solo exterior es la variante con
            # interior «0». El catálogo de prueba viejo la cotiza con «1-250».
            eje = self.plantilla._visar_axis_attribute('interior')
            linea = pedido.order_line[:1]
            otros = linea.product_id.product_template_attribute_value_ids.filtered(
                lambda v: v.attribute_id != eje)
            cero = self.plantilla.product_variant_ids.filtered(
                lambda v: otros <= v.product_template_attribute_value_ids
                and v.product_template_attribute_value_ids.filtered(
                    lambda x: x.attribute_id == eje).name == '0')[:1]
            if not cero:
                self.skipTest("el catálogo no tiene la variante «interior 0»")
            linea.product_id = cero
        pedido.action_confirm()
        tarea = pedido.tasks_ids[:1]
        if not tarea:
            self.skipTest("el producto de fumigación no genera servicio en esta base")
        tarea.visar_technician_ids = self.tecnico
        return tarea

    def _ajustar(self, tarea, tramo_interior, motivo="Tiene tercer piso", fotos=FOTO):
        return tarea._visar_ajuste_aplicar(
            self.tecnico, {self.interior.id: tramo_interior.id}, motivo, fotos)

    # ------------------------------------------------------------------
    def test_lee_el_tramo_pagado(self):
        tarea = self._visita()
        self.assertTrue(tarea._visar_ajuste_disponible())
        pagados = {d.id: p for d, _t, p in tarea._visar_ajuste_dimensiones()}
        self.assertEqual(pagados[self.interior.id], self.tramos_int[0])

    def test_mas_grande_cobra_solo_la_diferencia(self):
        tarea = self._visita()
        pagado = tarea._visar_ajuste_pagado()
        cotizacion = tarea._visar_ajuste_cotizar({self.interior.id: self.tramos_int[1].id})
        self.assertFalse(cotizacion['error'])
        self.assertGreater(cotizacion['diferencia'], 0)
        self.assertAlmostEqual(cotizacion['diferencia'], cotizacion['nuevo'] - pagado, 2)

        tareas_antes = self.env['project.task'].search_count([])
        estado, error = self._ajustar(tarea, self.tramos_int[1])
        self.assertFalse(error)
        self.assertEqual(estado, 'mayor')
        lineas = tarea._visar_ajuste_lineas()
        self.assertEqual(len(lineas), 2, "servicio con el tamaño real + «ya pagado»")
        self.assertAlmostEqual(sum(lineas.mapped('price_total')), cotizacion['diferencia'], 2)
        self.assertFalse(tarea._visar_upsell_lines().filtered('visar_valuation_credit'),
                         "la valoración no se descuenta del ajuste")

        tarea._visar_upsell_confirm(self.tecnico)
        factura = tarea._visar_upsell_invoice()
        self.assertAlmostEqual(factura.amount_total, cotizacion['diferencia'], 2,
                               msg="la factura es solo la diferencia")
        self.assertEqual(self.env['project.task'].search_count([]), tareas_antes,
                         "no nace otro servicio")
        self.assertEqual(lineas.mapped('task_id'), tarea)
        self.assertFalse(tarea._visar_ajuste_disponible(), "un ajuste por servicio")

    def test_exige_foto_y_motivo(self):
        tarea = self._visita()
        self.assertEqual(self._ajustar(tarea, self.tramos_int[1], fotos=[]), (None, 'sin_foto'))
        self.assertEqual(self._ajustar(tarea, self.tramos_int[1], motivo="  "),
                         (None, 'sin_motivo'))
        self.assertEqual(self._ajustar(tarea, self.tramos_int[0]), (None, 'sin_cambio'))
        self.assertFalse(tarea.visar_ajuste_estado)

    def test_mas_chica_no_devuelve_y_avisa(self):
        tarea = self._visita(self.tramos_int[1])
        estado, error = self._ajustar(tarea, self.tramos_int[0])
        self.assertFalse(error)
        self.assertEqual(estado, 'menor')
        self.assertFalse(tarea._visar_ajuste_lineas(), "nada se devuelve")
        self.assertTrue(tarea.activity_ids.filtered(
            lambda a: 'Medidas menores' in (a.summary or '')))
        self.assertTrue(self.env['ir.attachment'].search_count([
            ('res_model', '=', 'project.task'), ('res_id', '=', tarea.id),
            ('visar_photo_key', '=', 'visar_ajuste_medidas')]))

    def test_cliente_no_acepta_cancela_el_cobro(self):
        tarea = self._visita()
        self._ajustar(tarea, self.tramos_int[1])
        tarea._visar_upsell_confirm(self.tecnico)
        factura = tarea._visar_upsell_invoice()
        ok, error = tarea._visar_ajuste_rechazar(self.tecnico)
        self.assertTrue(ok, error)
        self.assertEqual(factura.state, 'cancel')
        self.assertEqual(tarea.visar_ajuste_estado, 'rechazado')
        self.assertFalse(tarea._visar_ajuste_lineas())
        self.assertTrue(tarea.activity_ids.filtered(
            lambda a: 'incompleto' in (a.summary or '')))

    def test_cierre_sin_pagar_avisa(self):
        tarea = self._visita()
        self._ajustar(tarea, self.tramos_int[1])
        tarea._visar_upsell_confirm(self.tecnico)
        tarea._visar_ajuste_al_cerrar()
        self.assertTrue(tarea.activity_ids.filtered(
            lambda a: 'Cobrar diferencia' in (a.summary or '')))

    def test_comision_solo_con_el_ajuste_encendido(self):
        if 'visar.commission.rule' not in self.env:
            self.skipTest("sin visar_commission")
        tarea = self._visita()
        self._ajustar(tarea, self.tramos_int[1])
        Regla = self.env['visar.commission.rule']
        regla = Regla.new({'origen': 'upsell_campo'})
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param(PARAM_AJUSTE_COMISION, False)
        lineas = self.env['sale.order.line'].search(regla._visar_dominio_lineas(self.tecnico))
        self.assertFalse(lineas.filtered('visar_ajuste_medidas'))
        Param.set_param(PARAM_AJUSTE_COMISION, True)
        lineas = self.env['sale.order.line'].search(regla._visar_dominio_lineas(self.tecnico))
        self.assertEqual(len(lineas.filtered('visar_ajuste_medidas')), 2)

    # ------------------------------------------------------------------
    # Combo: el corte agregado en sitio a una fumigación ya pagada (4-nov-2026)
    # ------------------------------------------------------------------
    def _combo_y_corte(self):
        Regla = self.env['visar.combo.rule']
        regla = Regla.search([('active', '=', True)]).filtered(
            lambda r: {self.interior.id, self.exterior.id} <= set(r.required_dimension_ids.ids)
            and r.discount_dimension_ids - self.interior - self.exterior)[:1]
        if not regla:
            self.skipTest("sin regla de combo fumigación + otra dimensión")
        corte = (regla.discount_dimension_ids - self.interior - self.exterior)[:1]
        plantilla = self.env['product.template']._visar_get_service_template_for_dimension(corte)
        plantilla.write({'visar_upsell_ok': True, 'sale_ok': True})
        tramo = plantilla._visar_tiers_for_dimension(corte).filtered(
            lambda t: not t.is_valuation and not t.is_free).sorted('m2_min')[:1]
        return regla, corte, max(tramo.m2_min, 1)

    def _corte_vendido(self, tarea, corte, m2):
        ok, error = tarea._visar_upsell_add_service(self.tecnico, {corte.id: m2})
        self.assertTrue(ok, error)
        return tarea._visar_upsell_lines().filtered(
            lambda l: not l.visar_valuation_credit)

    def test_combo_cuenta_la_fumigacion_ya_pagada(self):
        regla, corte, m2 = self._combo_y_corte()
        tarea = self._visita()
        self.assertEqual(set(tarea._visar_upsell_dimensiones_previas()),
                         {self.interior.id, self.exterior.id})
        linea = self._corte_vendido(tarea, corte, m2)
        self.assertEqual(len(linea), 1, "la fumigación no se vuelve a cobrar")
        self.assertAlmostEqual(linea.discount, regla._visar_discount_percent(), 2,
                               msg="el corte lleva el descuento del combo")

    def test_combo_solo_exterior_no_aplica(self):
        """La regla exige interior + exterior, igual que en el web."""
        _regla, corte, m2 = self._combo_y_corte()
        tarea = self._visita(con_interior=False)
        self.assertNotIn(self.interior.id, tarea._visar_upsell_dimensiones_previas())
        linea = self._corte_vendido(tarea, corte, m2)
        self.assertFalse(linea.discount)

    # ------------------------------------------------------------------
    # Corte de pasto: producto de un solo eje (4-nov-2026)
    # ------------------------------------------------------------------
    def test_corte_de_pasto_cobra_la_diferencia(self):
        _regla, corte, _m2 = self._combo_y_corte()
        plantilla = self.env['product.template']._visar_get_service_template_for_dimension(corte)
        tramos = plantilla._visar_tiers_for_dimension(corte).filtered(
            lambda t: not t.is_valuation).sorted('m2_min')
        if len(tramos) < 2:
            self.skipTest("el corte de pasto no tiene dos tramos")
        Motor = self.env['appointment.type'].sudo()
        item = corte._visar_quote_item(max(tramos[0].m2_min, 1))[0]
        lineas = Motor._visar_build_sale_lines([item], self.zona)
        pedido = self.env['sale.order'].create({
            'partner_id': self.cliente.id, 'pricelist_id': self.zona.pricelist_id.id,
            'order_line': [(0, 0, {
                'product_id': v['product_id'],
                'price_unit': Motor._visar_list_unit_price(
                    self.env['product.product'].browse(v['product_id']), self.zona),
                'discount': v.get('discount') or 0.0}) for v in lineas]})
        pedido.action_confirm()
        tarea = pedido.tasks_ids[:1]
        if not tarea:
            self.skipTest("el corte de pasto no genera servicio en esta base")
        tarea.visar_technician_ids = self.tecnico
        pagados = {d.id: p for d, _t, p in tarea._visar_ajuste_dimensiones()}
        self.assertEqual(pagados.get(corte.id), tramos[0], "lee el tramo pagado del corte")
        estado, error = tarea._visar_ajuste_aplicar(
            self.tecnico, {corte.id: tramos[1].id}, "El jardín es más grande", FOTO)
        self.assertFalse(error)
        self.assertEqual(estado, 'mayor')
        self.assertAlmostEqual(sum(tarea._visar_ajuste_lineas().mapped('price_total')),
                               tarea.visar_ajuste_diferencia, 2)
