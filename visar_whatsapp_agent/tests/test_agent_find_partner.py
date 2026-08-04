from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentFindPartner(TransactionCase):
    """El agente resuelve teléfono -> res.partner por la MISMA clave indexada
    (`visar_phone_nat10`) que usa el dedupe de reservas, y conserva la guarda de
    ambigüedad: ante 2+ partners con el número, no revela ninguno.
    """

    # Número nacional sintético (no existe en la copia de producción) y su forma
    # entrante de WhatsApp (52 país + 1 móvil + 10 dígitos).
    RAW = '9990001122'
    E164 = '+529990001122'
    WA = '5219990001122'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']

    def test_normalize_delegates_to_shared_rule(self):
        # _agent_normalize_phone delega en la regla compartida: mismos 10 dígitos.
        for raw in ('8123415696', '+528123415696', '5218123415696',
                    '52 812 341 5696'):
            self.assertEqual(self.Tools._agent_normalize_phone(raw), '8123415696')
        # Sin 10 dígitos -> '' (conserva el contrato de str del método del agente).
        self.assertEqual(self.Tools._agent_normalize_phone(''), '')
        self.assertEqual(self.Tools._agent_normalize_phone(None), '')

    def test_find_single_partner_via_index(self):
        # Un solo partner con el número -> se resuelve (aunque el formato guardado
        # sea el legacy raw y el entrante traiga 52 + 1 de móvil).
        partner = self.env['res.partner'].create({
            'name': 'Jonathan Velazquez', 'phone': self.RAW})
        found = self.Tools._agent_find_partner(self.WA)
        self.assertEqual(found, partner)

    def test_ambiguous_number_reveals_nothing(self):
        # 2 partners con el mismo número nacional -> guarda de ambigüedad: vacío.
        self.env['res.partner'].create({
            'name': 'Jonathan 1', 'phone': self.RAW})
        self.env['res.partner'].create({
            'name': 'Jonathan 2', 'phone': self.E164})
        found = self.Tools._agent_find_partner(self.WA)
        self.assertFalse(found)

    def test_unknown_number_empty(self):
        # Número desconocido (no se crea ningún partner con él) -> vacío.
        found = self.Tools._agent_find_partner('5219990007777')
        self.assertFalse(found)

    def test_short_number_empty(self):
        # Menos de 10 dígitos -> vacío, sin tocar la BD.
        self.assertFalse(self.Tools._agent_find_partner('12345'))
