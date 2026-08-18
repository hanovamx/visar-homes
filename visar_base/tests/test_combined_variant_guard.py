from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestCombinedVariantGuard(TransactionCase):
    """`_visar_combined_variant_for_tiers` revienta si le cruzan los ejes.

    Por que existe este guardia: el metodo lee los ejes de tamano DE LOS TRAMOS
    QUE SE LE PASAN, pero quien decide cual es interior y cual exterior es el
    llamador (por `dimension.measure_type`). Son dos fuentes independientes, y el
    `measure_scope` de un tramo es contraintuitivo respecto a su nombre: el tramo
    "51 - 100 m2" tiene alcance EXTERIOR.

    Cruzarlos NO daba error: devolvia la variante combinada de la fila base -un
    tercio del precio- en silencio. El wizard web nunca cae en esto porque
    resuelve los tramos con `_visar_tier_field_name()`; un llamador nuevo (el
    agente de WhatsApp armando `items` a mano) si podria.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.env['product.template'].create({
            'name': 'Fumigacion Test Guardia', 'type': 'service',
        })
        cls.zone = cls.env['visar.zone'].create({
            'name': 'Zona Test Guardia', 'code': 'TSTG'})

    def _tier(self, name, scope, m2_min, m2_max):
        return self.env['visar.service.tier'].create({
            'name': name,
            'product_tmpl_id': self.template.id,
            'measure_scope': scope,
            'm2_min': m2_min,
            'm2_max': m2_max,
        })

    def test_tramo_exterior_como_interior_revienta(self):
        interior_ok = self._tier('251 - 500 m2', 'interior', 251, 500)
        exterior_ok = self._tier('51 - 100 m2', 'exterior', 51, 100)
        # Cruzados a proposito: el de exterior pasado como interior.
        with self.assertRaises(ValidationError):
            self.template._visar_combined_variant_for_tiers(
                exterior_ok, interior_ok, self.zone)

    def test_alcance_all_es_legitimo_en_cualquier_eje(self):
        # 'all' es el default y sirve para los dos ejes: NO debe rechazarse. Que
        # devuelva vacio (no hay variantes configuradas en este test) es correcto;
        # lo que se fija aqui es que no lance.
        tier_all_a = self._tier('0 - 50 m2', 'all', 0, 50)
        tier_all_b = self._tier('51 - 100 m2', 'all', 51, 100)
        result = self.template._visar_combined_variant_for_tiers(
            tier_all_a, tier_all_b, self.zone)
        self.assertFalse(result)

    def test_ejes_correctos_no_revientan(self):
        interior_ok = self._tier('251 - 500 m2', 'interior', 251, 500)
        exterior_ok = self._tier('51 - 100 m2', 'exterior', 51, 100)
        result = self.template._visar_combined_variant_for_tiers(
            interior_ok, exterior_ok, self.zone)
        # Sin variantes configuradas devuelve vacio, que es el contrato
        # documentado ("el llamador cae al comportamiento de dos lineas").
        self.assertFalse(result)
