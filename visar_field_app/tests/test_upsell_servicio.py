# -*- coding: utf-8 -*-
"""El técnico puede vender SERVICIOS en campo, no solo bienes (REQ-006).

El catálogo de campo nunca filtró por tipo de producto —lo comprobó este
requerimiento—, así que ofrecer una poda detectada en sitio es configuración de
datos maestros. Lo que sí se exige es poder COBRARLA ahí mismo: un producto que
factura por entrega confirma su pedido y se queda en "Nada que facturar", y el
técnico se queda sin liga de pago delante del cliente.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestUpsellDeServicios(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.proyecto = cls.env['project.project'].create({
            'name': 'FSM upsell servicio', 'is_fsm': True,
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Cliente upsell servicio', 'zip': '64000'})
        cls.empleado = cls.env['hr.employee'].create({'name': 'Tecnico upsell'})

    def _tarea(self):
        return self.env['project.task'].create({
            'name': 'Servicio con upsell', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id})

    def _servicio(self, **vals):
        return self.env['product.template'].create(dict({
            'name': 'Poda detectada en sitio', 'type': 'service',
            'list_price': 350.0, 'visar_upsell_ok': True, 'sale_ok': True,
            'invoice_policy': 'order',
        }, **vals))

    def _en_catalogo(self, tarea, template):
        return template.product_variant_id.id in {
            p['id'] for p in tarea._visar_upsell_catalog()}

    def test_un_servicio_se_ofrece_igual_que_un_bien(self):
        tarea = self._tarea()
        self.assertTrue(self._en_catalogo(tarea, self._servicio()))

    def test_un_servicio_se_agrega_y_se_cobra_en_sitio(self):
        tarea = self._tarea()
        servicio = self._servicio()

        self.assertTrue(tarea._visar_upsell_add(
            self.empleado, servicio.product_variant_id.id, 1))
        tarea._visar_upsell_confirm(self.empleado)

        pedido = tarea._visar_upsell_order()
        self.assertEqual(pedido.state, 'sale')
        factura = tarea._visar_upsell_invoice()
        self.assertTrue(factura, "sin factura no hay liga de pago que darle al cliente")
        # El precio de lista lleva IVA incluido: lo que se le cobra al cliente es
        # el total, que es lo que va en la liga de pago.
        self.assertEqual(factura.amount_total, 350.0)

    def test_vender_un_servicio_no_abre_otro_servicio_externo(self):
        """Un producto de servicio con proyecto crearía una tarea al confirmar. No
        pasa porque la línea del upsell ya nace ligada a ESTE servicio."""
        tarea = self._tarea()
        servicio = self._servicio(service_tracking='task_global_project',
                                  project_id=self.proyecto.id)
        antes = self.env['project.task'].search_count([])

        tarea._visar_upsell_add(self.empleado, servicio.product_variant_id.id, 1)
        tarea._visar_upsell_confirm(self.empleado)

        self.assertEqual(self.env['project.task'].search_count([]), antes)
        self.assertEqual(tarea._visar_upsell_order().order_line.task_id, tarea)

    def test_lo_que_no_se_puede_cobrar_en_sitio_no_se_ofrece(self):
        """Factura por entrega: el pedido se confirma y no hay nada que facturar."""
        tarea = self._tarea()
        servicio = self._servicio(invoice_policy='delivery')
        self.assertFalse(self._en_catalogo(tarea, servicio))
        self.assertFalse(tarea._visar_upsell_add(
            self.empleado, servicio.product_variant_id.id, 1),
            "el candado tambien va del lado del servidor: la app es publica")

    def test_una_suscripcion_sigue_fuera_del_catalogo(self):
        tarea = self._tarea()
        poliza = self._servicio(name='Poliza mensual', recurring_invoice=True)
        self.assertFalse(self._en_catalogo(tarea, poliza))


@tagged('post_install', '-at_install')
class TestCasillaEnLaFicha(TransactionCase):
    """Cada casilla de la fila de opciones con SU texto.

    El campo se insertaba detrás de `sale_ok`, que comparte `span` con su
    `<label for="sale_ok"/>`. Colarse en medio dejaba la fila con cuatro casillas
    y tres textos: "Ventas" se leía sobre la casilla de upsell y la de Ventas
    quedaba muda. Visto en producción el 17-sep-2026.
    """

    def _fila(self):
        from lxml import etree
        arch = etree.fromstring(
            self.env['product.template'].get_view(view_type='form')['arch'])
        return arch.xpath("//div[@name='options']")[0]

    def test_cada_casilla_lleva_su_etiqueta(self):
        for span in self._fila().xpath('./span'):
            campos = [f.get('name') for f in span.xpath('./field')]
            etiquetas = [l.get('for') for l in span.xpath('./label')]
            self.assertEqual(len(campos), 1, "una casilla por bloque: %s" % campos)
            self.assertEqual(etiquetas, campos,
                             "la etiqueta tiene que ser la de SU casilla")

    def test_la_casilla_de_campo_esta_en_la_fila_y_con_su_texto(self):
        fila = self._fila()
        propio = fila.xpath("./span[field/@name='visar_upsell_ok']")
        self.assertTrue(propio, "la casilla sigue en la fila de opciones")
        self.assertEqual(propio[0].xpath('./label/@for'), ['visar_upsell_ok'])
        self.assertIn('not sale_ok', propio[0].get('invisible') or '',
                      "solo se ofrece si el producto se puede vender")
