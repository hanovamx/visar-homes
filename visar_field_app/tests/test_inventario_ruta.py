# -*- coding: utf-8 -*-
"""Inventario por ruta: cada técnico gasta de SU ubicación (23-sep-2026).

Lo que se protege aquí:

- que el catálogo y el desplegable de plaguicidas enseñen lo que el técnico TRAE,
  y no el almacén entero;
- que al cerrar el servicio la existencia baje de verdad, una sola vez;
- que un conteo desfasado NO impida cerrar: se descuenta igual, queda en negativo
  y se avisa (el servicio ya se prestó, el cliente está en la puerta);
- que el recorrido se le cargue al servicio al que se iba.
"""
from odoo.tests import tagged

from .test_upsell_en_pedido_original import CasoAdicional


class CasoInventario(CasoAdicional):
    """Un técnico con camioneta propia y material cargado."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        almacen = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        cls.camioneta = cls.env['stock.location'].create({
            'name': 'Camioneta del técnico', 'usage': 'internal',
            'location_id': almacen.lot_stock_id.id})
        cls.empleado.visar_stock_location_id = cls.camioneta
        cls.ml = cls.env.ref('uom.product_uom_milliliter')
        cls.plaguicida = cls.env['product.product'].create({
            'name': 'Cipermetrina de prueba', 'type': 'consu', 'is_storable': True,
            'uom_id': cls.ml.id, 'visar_consumible_ok': True})
        cls.material = cls.env['product.product'].create({
            'name': 'Cebo de prueba', 'type': 'consu', 'is_storable': True,
            'visar_consumible_ok': True})
        cls._surtir(cls.plaguicida, 1000)
        cls._surtir(cls.material, 10)

    @classmethod
    def _surtir(cls, producto, cantidad, ubicacion=None):
        cls.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': producto.id,
            'location_id': (ubicacion or cls.camioneta).id,
            'inventory_quantity': cantidad,
        }).action_apply_inventory()

    def _a_mano(self, producto, ubicacion=None):
        ubicacion = ubicacion or self.camioneta
        return ubicacion._visar_on_hand(producto).get(producto.id, 0.0)


@tagged('post_install', '-at_install')
class TestExistenciasDelTecnico(CasoInventario):

    def test_on_hand_solo_de_su_ubicacion(self):
        otra = self.env['stock.location'].create({
            'name': 'Camioneta ajena', 'usage': 'internal',
            'location_id': self.camioneta.location_id.id})
        self.assertEqual(self._a_mano(self.plaguicida), 1000.0)
        self.assertFalse(otra._visar_on_hand(self.plaguicida),
                         "lo de un técnico no aparece en la camioneta de otro")

    def test_on_hand_incluye_las_ubicaciones_hijas(self):
        hija = self.env['stock.location'].create({
            'name': 'Caja de herramienta', 'usage': 'internal',
            'location_id': self.camioneta.id})
        self._surtir(self.plaguicida, 200, ubicacion=hija)
        self.assertEqual(self._a_mano(self.plaguicida), 1200.0)

    def test_etiqueta_en_la_unidad_del_producto(self):
        self.assertEqual(
            self.plaguicida._visar_field_stock_label(750), "750 ml")
        self.assertEqual(
            self.plaguicida._visar_field_stock_label(12.5), "12.5 ml")


@tagged('post_install', '-at_install')
class TestCatalogoSegunLaCamioneta(CasoInventario):

    def _nombres(self, tarea, employee=None):
        return {p['name'] for p in tarea._visar_upsell_catalog(employee)}

    def test_lo_que_no_trae_no_se_ofrece(self):
        vendible = self.env['product.product'].create({
            'name': 'Malla que no trae', 'type': 'consu', 'is_storable': True,
            'list_price': 200.0, 'invoice_policy': 'order',
            'sale_ok': True, 'visar_upsell_ok': True})
        tarea = self._tarea(self._pedido())

        self.assertNotIn(vendible.display_name, self._nombres(tarea, self.empleado))
        self._surtir(vendible, 3)
        self.assertIn(vendible.display_name, self._nombres(tarea, self.empleado))

    def test_lo_que_no_se_cuenta_se_ofrece_siempre(self):
        # `self.extra` (estación antirroedores) no es almacenable: no hay existencia
        # que mirar, así que se ofrece aunque la camioneta esté vacía.
        tarea = self._tarea(self._pedido())
        self.assertIn(self.extra.display_name, self._nombres(tarea, self.empleado))

    def test_sin_ubicacion_configurada_se_ofrece_todo(self):
        sin_camioneta = self.env['hr.employee'].create({'name': 'Técnico nuevo'})
        vendible = self.env['product.product'].create({
            'name': 'Tapón sin surtir', 'type': 'consu', 'is_storable': True,
            'list_price': 90.0, 'invoice_policy': 'order',
            'sale_ok': True, 'visar_upsell_ok': True})
        tarea = self._tarea(self._pedido())
        self.assertIn(vendible.display_name, self._nombres(tarea, sin_camioneta),
                      "que administración no lo haya configurado no lo deja sin vender")

    def test_la_cantidad_viaja_al_catalogo(self):
        vendible = self.env['product.product'].create({
            'name': 'Trampa surtida', 'type': 'consu', 'is_storable': True,
            'list_price': 120.0, 'invoice_policy': 'order',
            'sale_ok': True, 'visar_upsell_ok': True})
        self._surtir(vendible, 4)
        tarea = self._tarea(self._pedido())
        fila = next(p for p in tarea._visar_upsell_catalog(self.empleado)
                    if p['id'] == vendible.id)
        self.assertEqual(fila['stock_label'], "4 Unidades")


@tagged('post_install', '-at_install')
class TestConsumoAlCerrar(CasoInventario):

    def _con_material(self, cantidad=2, producto=None):
        tarea = self._tarea(self._pedido())
        self.env['visar.field.consumo'].create({
            'task_id': tarea.id, 'product_id': (producto or self.material).id,
            'quantity': cantidad, 'employee_id': self.empleado.id})
        return tarea

    def test_descuenta_de_la_camioneta(self):
        tarea = self._con_material(3)
        moves = tarea._visar_consumo_post(self.empleado)

        self.assertEqual(len(moves), 1)
        self.assertEqual(moves.state, 'done')
        self.assertEqual(moves.location_id, self.camioneta)
        self.assertEqual(moves.location_dest_id.usage, 'production',
                         "se consume produciendo el servicio, no es un ajuste")
        self.assertEqual(self._a_mano(self.material), 7.0)

    def test_cerrar_dos_veces_no_descuenta_dos_veces(self):
        tarea = self._con_material(3)
        tarea._visar_consumo_post(self.empleado)
        self.assertFalse(tarea._visar_consumo_post(self.empleado))
        self.assertEqual(self._a_mano(self.material), 7.0)

    def test_sin_existencia_suficiente_se_descuenta_igual_y_se_avisa(self):
        tarea = self._con_material(25)  # trae 10

        moves = tarea._visar_consumo_post(self.empleado)

        self.assertEqual(moves.state, 'done', "el servicio ya se prestó: se registra")
        self.assertEqual(self._a_mano(self.material), -15.0)
        self.assertIn("negativo", tarea.message_ids[0].body,
                      "el descubierto se avisa para que administración lo cuadre")

    def test_sin_ubicacion_no_mueve_nada_pero_deja_constancia(self):
        sin_camioneta = self.env['hr.employee'].create({'name': 'Técnico sin ruta'})
        tarea = self._con_material(2)

        self.assertFalse(tarea._visar_consumo_post(sin_camioneta))
        self.assertFalse(tarea.visar_consumo_at)
        self.assertIn("ubicación de inventario", tarea.message_ids[0].body)

    def test_un_producto_que_no_es_insumo_no_genera_movimiento(self):
        suelto = self.env['product.product'].create({
            'name': 'Producto ajeno', 'type': 'consu', 'is_storable': True})
        tarea = self._tarea(self._pedido())
        # Se salta la validación de la ruta a propósito: simula un id colado.
        self.env['visar.field.consumo'].create({
            'task_id': tarea.id, 'product_id': suelto.id, 'quantity': 1})

        self.assertFalse(tarea._visar_consumo_post(self.empleado))


@tagged('post_install', '-at_install')
class TestEntregaDeAdicionales(CasoInventario):

    def test_lo_vendido_sale_de_la_camioneta(self):
        vendible = self.env['product.product'].create({
            'name': 'Guardapolvo vendido', 'type': 'consu', 'is_storable': True,
            'list_price': 350.0, 'invoice_policy': 'order',
            'sale_ok': True, 'visar_upsell_ok': True})
        self._surtir(vendible, 5)
        tarea = self._tarea(self._pedido())
        self.assertTrue(tarea._visar_upsell_add(self.empleado, vendible.id, 2))

        moves = tarea._visar_entrega_upsell(self.empleado)

        self.assertTrue(moves)
        self.assertEqual(set(moves.mapped('state')), {'done'})
        self.assertEqual(moves.location_id, self.camioneta,
                         "sale de la camioneta, no del almacén central")
        self.assertEqual(self._a_mano(vendible), 3.0)

    def test_un_servicio_vendido_no_mueve_inventario(self):
        tarea = self._tarea(self._pedido())
        self.assertTrue(tarea._visar_upsell_add(self.empleado, self.extra.id, 1))
        self.assertFalse(tarea._visar_entrega_upsell(self.empleado))


@tagged('post_install', '-at_install')
class TestRecorrido(CasoInventario):

    def _jornada(self, inicio):
        return self.env['visar.field.session'].create({
            'employee_id': self.empleado.id, 'visar_odometer_start': inicio})

    def test_el_primer_tramo_se_mide_desde_el_arranque_del_dia(self):
        jornada = self._jornada(45000)
        tarea = self._tarea()
        tarea.write({'visar_odometer_session_id': jornada.id,
                     'visar_odometer_arrival': 45012})

        self.assertEqual(tarea.visar_km_leg, 12,
                         "sin el ancla de la mañana el primer viaje no se podría medir")

    def test_cada_tramo_se_mide_desde_la_llegada_anterior(self):
        jornada = self._jornada(45000)
        primera = self._tarea()
        primera.write({'visar_odometer_session_id': jornada.id,
                       'visar_odometer_arrival': 45012})
        segunda = self._tarea()
        segunda.write({'visar_odometer_session_id': jornada.id,
                       'visar_odometer_arrival': 45030})

        self.assertEqual(segunda.visar_km_leg, 18,
                         "el viaje es del servicio al que se iba")
        self.assertEqual(primera.visar_km_leg, 12, "el tramo anterior no cambia")

    def test_la_ultima_lectura_es_el_suelo_de_la_siguiente(self):
        jornada = self._jornada(45000)
        tarea = self._tarea()
        tarea.write({'visar_odometer_session_id': jornada.id,
                     'visar_odometer_arrival': 45030})

        self.assertEqual(jornada._visar_odometer_last(), 45030)

    def test_sin_lectura_no_se_inventa_un_tramo(self):
        jornada = self._jornada(45000)
        tarea = self._tarea()
        tarea.visar_odometer_session_id = jornada.id

        self.assertEqual(tarea.visar_km_leg, 0.0)
