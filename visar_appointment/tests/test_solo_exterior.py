# -*- coding: utf-8 -*-
"""Fumigación SOLO exterior: se cobra el jardín, no interior + jardín.

El fallo: el sitio cobraba 1,840 por un jardín de 101–500 m² en zona A cuando la
lista dice 1,150. Un tramo del tabulador no arma combinaciones —apunta a UNA
variante— y los de exterior apuntaban a variantes con el interior fijo en "1-250".
La variante sin interior ("0") existía y tenía precio; lo que faltaba era que el
motor pudiera llegar a ella (`product.template._visar_wire_exterior_only_variants`).

Y un segundo fallo escondido detrás del primero: el jardín de 0–50 m² va marcado
"incluido", que es verdad cuando se fumiga el interior. Vendido solo salía gratis.

Se monta un producto propio con la MISMA forma que el de producción (zona ×
tamaño inmueble × tamaño jardín, una sola lista para todas las zonas, variantes
"0" recién creadas y sin Zona Visar) para que la prueba no dependa del catálogo
de nadie.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

INTERIOR = {'0': 0.0, '1-250': 600.0, '251 - 500': 800.0}
JARDIN = {'1 - 50': 0.0, '51 - 100': 800.0}
SOLO_JARDIN = {'1 - 50': 600.0, '51 - 100': 800.0}
FACTOR = {'TA': 1.15, 'TB': 1.0}


@tagged('post_install', '-at_install')
class TestSoloExterior(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AptType = cls.env['appointment.type']
        Attr = cls.env['product.attribute']
        cls.a_zona = Attr.create({'name': 'Zona T', 'create_variant': 'always', 'value_ids': [
            (0, 0, {'name': n}) for n in FACTOR]})
        cls.a_int = Attr.create({'name': 'Inmueble T', 'create_variant': 'always', 'value_ids': [
            (0, 0, {'name': n}) for n in INTERIOR]})
        cls.a_ext = Attr.create({'name': 'Jardín T', 'create_variant': 'always', 'value_ids': [
            (0, 0, {'name': n}) for n in JARDIN]})
        cls.tmpl = cls.env['product.template'].create({
            'name': 'Fumigación T', 'type': 'service', 'list_price': 600.0,
            'visar_is_service': True,
            'attribute_line_ids': [
                (0, 0, {'attribute_id': a.id, 'value_ids': [(6, 0, a.value_ids.ids)]})
                for a in (cls.a_int, cls.a_zona, cls.a_ext)]})
        cls.lista = cls.env['product.pricelist'].create({'name': 'Lista T'})
        cls.zonas = {
            code: cls.env['visar.zone'].create({
                'name': 'Zona %s' % code, 'code': code, 'pricelist_id': cls.lista.id})
            for code in FACTOR}
        for variant in cls.tmpl.product_variant_ids:
            zona, interior, jardin = cls._valores(variant)
            base = SOLO_JARDIN[jardin] if interior == '0' else INTERIOR[interior] + JARDIN[jardin]
            cls.env['product.pricelist.item'].create({
                'pricelist_id': cls.lista.id, 'applied_on': '0_product_variant',
                'product_id': variant.id, 'compute_price': 'fixed',
                'fixed_price': round(base * FACTOR[zona])})
            # Como en producción: Zona Visar solo en las variantes "de siempre";
            # las de interior "0" nacieron después y nadie se la puso.
            if interior != '0':
                variant.visar_zone_id = cls.zonas[zona]

        grupo = cls.env['visar.service.group'].create({'name': 'Grupo T', 'code': 'SOLOEXT_T'})
        Dim = cls.env['visar.service.dimension']
        cls.dim_int = Dim.create({
            'name': 'Interior T', 'code': 'SOLOEXT_INT', 'group_id': grupo.id,
            'product_tmpl_id': cls.tmpl.id, 'measure_type': 'interior'})
        cls.dim_ext = Dim.create({
            'name': 'Exterior T', 'code': 'SOLOEXT_EXT', 'group_id': grupo.id,
            'product_tmpl_id': cls.tmpl.id, 'measure_type': 'exterior'})
        Tier = cls.env['visar.service.tier']

        def tier(nombre, lo, hi, scope, interior, jardin, **extra):
            return Tier.create(dict({
                'product_tmpl_id': cls.tmpl.id, 'name': nombre, 'm2_min': lo, 'm2_max': hi,
                'measure_scope': scope,
                'product_id': cls._variante('TB', interior, jardin).id}, **extra))

        cls.t_int_1 = tier('1 – 250 m²', 1, 250, 'interior', '1-250', '1 - 50')
        cls.t_int_2 = tier('251 – 500 m²', 251, 500, 'interior', '251 - 500', '1 - 50')
        cls.t_ext_1 = tier('0 – 50 m² (incluida)', 0, 50, 'exterior', '1-250', '1 - 50',
                           is_free=True)
        cls.t_ext_2 = tier('51 – 100 m²', 51, 100, 'exterior', '1-250', '51 - 100')

    @classmethod
    def _valores(cls, variant):
        por_attr = {p.attribute_id: p.name
                    for p in variant.product_template_attribute_value_ids}
        return por_attr[cls.a_zona], por_attr[cls.a_int], por_attr[cls.a_ext]

    @classmethod
    def _variante(cls, zona, interior, jardin):
        return cls.tmpl.product_variant_ids.filtered(
            lambda v: cls._valores(v) == (zona, interior, jardin))

    def _cotiza(self, zona, interior=None, exterior=None):
        selecciones = {'dimension_ids': []}
        for dim, tier in ((self.dim_int, interior), (self.dim_ext, exterior)):
            if tier:
                selecciones['dimension_ids'].append(dim.id)
                selecciones[dim._visar_tier_field_name()] = tier.id
        items = self.AptType._visar_resolve_wizard_items(selecciones)
        return self.AptType._visar_quote_booking(items, self.zonas[zona])

    # ------------------------------------------------------------------
    # El fallo, tal y como estaba
    # ------------------------------------------------------------------

    def test_sin_coser_el_jardin_solo_cobra_tambien_el_interior(self):
        """Lo que se arregla: 1,610 (interior + jardín) donde la lista dice 920."""
        cotizacion = self._cotiza('TA', exterior=self.t_ext_2)
        self.assertEqual(cotizacion['total'], round((600 + 800) * 1.15))

    # ------------------------------------------------------------------
    # Cosido
    # ------------------------------------------------------------------

    def test_el_jardin_solo_cobra_la_variante_sin_interior_en_cada_zona(self):
        self.tmpl._visar_wire_exterior_only_variants()
        for zona, factor in FACTOR.items():
            for tier, jardin in ((self.t_ext_1, '1 - 50'), (self.t_ext_2, '51 - 100')):
                cotizacion = self._cotiza(zona, exterior=tier)
                self.assertEqual(
                    cotizacion['total'], round(SOLO_JARDIN[jardin] * factor),
                    "solo exterior %s en zona %s" % (jardin, zona))

    def test_el_jardin_chico_solo_no_sale_gratis_ni_dice_incluida(self):
        self.tmpl._visar_wire_exterior_only_variants()
        cotizacion = self._cotiza('TA', exterior=self.t_ext_1)
        linea = cotizacion['lines'][0]
        self.assertFalse(linea['is_free'])
        self.assertEqual(linea['price'], round(600 * 1.15))
        self.assertNotIn('incluida', linea['name'])

    def test_con_interior_el_jardin_chico_sigue_incluido(self):
        self.tmpl._visar_wire_exterior_only_variants()
        cotizacion = self._cotiza('TA', interior=self.t_int_1, exterior=self.t_ext_1)
        self.assertEqual(cotizacion['total'], round(600 * 1.15))
        gratis = [l for l in cotizacion['lines'] if l['is_free']]
        self.assertEqual(len(gratis), 1)

    def test_interior_mas_exterior_no_cambia_de_precio(self):
        antes = {
            (zona, ti.id, te.id): self._cotiza(zona, interior=ti, exterior=te)['total']
            for zona in FACTOR for ti in (self.t_int_1, self.t_int_2)
            for te in (self.t_ext_1, self.t_ext_2)}
        self.tmpl._visar_wire_exterior_only_variants()
        for (zona, ti, te), total in antes.items():
            Tier = self.env['visar.service.tier']
            self.assertEqual(
                self._cotiza(zona, interior=Tier.browse(ti), exterior=Tier.browse(te))['total'],
                total)
        # Y el caso que duele si se rompe: interior + jardín grande NO es solo jardín.
        self.assertEqual(
            self._cotiza('TA', interior=self.t_int_1, exterior=self.t_ext_2)['total'],
            round((600 + 800) * 1.15))

    def test_solo_interior_no_cambia_de_precio(self):
        self.tmpl._visar_wire_exterior_only_variants()
        self.assertEqual(self._cotiza('TA', interior=self.t_int_1)['total'], round(600 * 1.15))
        self.assertEqual(self._cotiza('TB', interior=self.t_int_2)['total'], 800)

    def test_un_tramo_interior_desviado_a_cero_vuelve_a_su_sitio(self):
        """Pasó en producción el 4-oct: probando arreglos, el tramo interior 1–250
        quedó apuntando a la variante "0". Con la Zona Visar ya puesta, interior +
        jardín habría cobrado solo el jardín."""
        self.t_int_1.product_id = self._variante('TB', '0', '1 - 50')
        self.tmpl._visar_wire_exterior_only_variants()
        self.assertEqual(self.t_int_1.product_id, self._variante('TB', '1-250', '1 - 50'))
        self.assertEqual(
            self._cotiza('TA', interior=self.t_int_1, exterior=self.t_ext_2)['total'],
            round((600 + 800) * 1.15))

    def test_coser_dos_veces_no_cambia_nada_la_segunda(self):
        self.assertTrue(self.tmpl._visar_wire_exterior_only_variants())
        self.assertFalse(self.tmpl._visar_wire_exterior_only_variants())

    def test_sin_variante_cero_no_se_toca_nada(self):
        self.tmpl.product_variant_ids.filtered(
            lambda v: self._valores(v)[1] == '0').write({'active': False})
        self.tmpl.attribute_line_ids.filtered(
            lambda l: l.attribute_id == self.a_int).product_template_value_ids.filtered(
            lambda p: p.name == '0').write({'ptav_active': False})
        self.assertFalse(self.tmpl._visar_wire_exterior_only_variants())
        self.assertEqual(self.t_ext_2.product_id, self._variante('TB', '1-250', '51 - 100'))

    # ------------------------------------------------------------------
    # Lo que se le ofrece al cliente antes de cotizar
    # ------------------------------------------------------------------

    def test_en_solo_exterior_el_tramo_chico_no_se_anuncia_incluido(self):
        seccion = {'dimension': self.dim_ext, 'tiers': self.t_ext_1 | self.t_ext_2}
        solo = self.AptType._visar_wizard_section_options(
            seccion, {'dimension_ids': [self.dim_ext.id]})
        self.assertFalse(any(o['is_free'] or o['description'] for o in solo))
        ambos = self.AptType._visar_wizard_section_options(
            seccion, {'dimension_ids': [self.dim_int.id, self.dim_ext.id]})
        self.assertTrue(ambos[0]['is_free'])
        self.assertTrue(ambos[0]['description'])
