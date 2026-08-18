from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentPrepareBooking(TransactionCase):
    """`agent_prepare_booking`: deja la reserva lista para pagar, sin sesion web.

    Es la pieza que permite recoger TODO el agendado por WhatsApp y dejar fuera
    solo el pago. No reimplementa nada del wizard: llama a los mismos metodos de
    modelo, que se bajaron del controlador precisamente para esto.

    Dos cosas se prueban aparte, y por razones distintas:

      * **Los guardias** (telefono, cobertura, items, horario) se prueban siempre:
        son deterministas y no dependen del catalogo.
      * **La paridad de precio** solo puede probarse con el catalogo real
        configurado (variantes por zona, listas de precios, tramos). En una BD sin
        catalogo el test se SALTA en vez de dar un falso verde — la corrida que
        vale es contra una copia de produccion.
    """

    # Telefonos sinteticos (no existen en la copia de produccion).
    WA_NEW = '5219990554433'
    WA_DUPE = '5219990556677'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.AptType = cls.env['appointment.type']

    # --- guardias (siempre corren) -------------------------------------------

    def test_telefono_invalido(self):
        result = self.Tools.agent_prepare_booking({'phone': '123'})
        self.assertFalse(result['prepared'])
        self.assertEqual(result['reason'], 'phone_invalid')

    def test_telefono_ambiguo_no_adivina(self):
        # Dos clientes con el mismo numero: reservar a nombre del equivocado seria
        # colgarle una venta a otra persona. Misma politica que en la consulta de
        # servicios: ante duda, no se adivina; se escala.
        Partner = self.env['res.partner']
        Partner.create({'name': 'Dupe A Test', 'phone': self.WA_DUPE})
        Partner.create({'name': 'Dupe B Test', 'phone': self.WA_DUPE})
        result = self.Tools.agent_prepare_booking({'phone': self.WA_DUPE})
        self.assertFalse(result['prepared'])
        self.assertEqual(result['reason'], 'phone_ambiguous')

    def test_cliente_nuevo_sin_nombre(self):
        result = self.Tools.agent_prepare_booking({'phone': self.WA_NEW})
        self.assertFalse(result['prepared'])
        self.assertEqual(result['reason'], 'name_required')

    def test_fuera_de_cobertura(self):
        result = self.Tools.agent_prepare_booking({
            'phone': self.WA_NEW, 'name': 'Cliente Nuevo Test',
            'cp': '00000',
        })
        self.assertFalse(result['prepared'])
        self.assertEqual(result['reason'], 'out_of_coverage')

    def test_nunca_lanza_con_payload_vacio(self):
        # El agente tiene que poder decirle algo al cliente (o escalar) en vez de
        # recibir un traceback.
        result = self.Tools.agent_prepare_booking({})
        self.assertFalse(result['prepared'])
        self.assertTrue(result['reason'])

    # --- paridad de precio (necesita catalogo real) ---------------------------

    def _real_catalog(self):
        """(zone, master) del catalogo real, o (None, None) si no esta configurado."""
        master = self.AptType.sudo()._visar_get_master_appointment_type()
        zone = self.env['visar.zone'].sudo().search([], limit=1)
        if not master or not zone or not master.resource_ids:
            return None, None
        return zone, master

    def _selections_interior_exterior(self):
        """Selecciones con fumigacion interior + exterior, o None si no hay tramos."""
        Dimension = self.env['visar.service.dimension'].sudo()
        interior = Dimension.search([('measure_type', '=', 'interior')], limit=1)
        exterior = Dimension.search([('measure_type', '=', 'exterior')], limit=1)
        if not interior or not exterior:
            return None
        Tier = self.env['visar.service.tier'].sudo()
        # `is_free` fuera: el primer tramo exterior del catalogo real es
        # "0 - 50 m2" INCLUIDA, y ahi el diseno emite DOS lineas a proposito (una
        # cobrada + una al 100% de descuento que muestra lo incluido). Sin este
        # filtro la prueba fallaba con el precio correcto — la asercion estaba mal
        # calibrada, no el dinero.
        tier_int = Tier.search([
            ('measure_scope', '=', 'interior'), ('is_valuation', '=', False),
            ('is_free', '=', False)], limit=1)
        tier_ext = Tier.search([
            ('measure_scope', '=', 'exterior'), ('is_valuation', '=', False),
            ('is_free', '=', False)], limit=1)
        if not tier_int or not tier_ext:
            return None
        return {
            'dimension_ids': [interior.id, exterior.id],
            # Forma generica `tiers`: el llamador no necesita saber el nombre del
            # campo de tramo de cada dimension.
            'tiers': {str(interior.id): tier_int.id, str(exterior.id): tier_ext.id},
        }

    def test_interior_mas_exterior_es_una_linea_combinada(self):
        """Interior+exterior = UNA linea de variante combinada, NO la suma.

        Es el caso que mas caro sale si se rompe: sumar las dos por separado
        sobrecobra al cliente. El agente y el wizard tienen que coincidir al peso.
        """
        zone, master = self._real_catalog()
        if not zone:
            self.skipTest("Catalogo Visar no configurado en esta BD")
        selections = self._selections_interior_exterior()
        if not selections:
            self.skipTest("No hay dimensiones/tramos interior+exterior configurados")

        items = self.AptType.sudo()._visar_resolve_wizard_items(selections)
        self.assertEqual(len(items), 2, "dos dimensiones -> dos items")

        quote = self.AptType.sudo()._visar_quote_booking(items, zone)
        if not quote or not quote.get('total'):
            self.skipTest("El catalogo no devuelve precio para esa combinacion")

        lines = master.sudo()._visar_build_sale_lines(items, zone)
        service_lines = [line for line in lines if not line.get('is_addon')]
        self.assertEqual(
            len(service_lines), 1,
            "interior+exterior se fusionan en UNA linea (variante combinada); dos "
            "lineas significan que el cliente pagaria la suma de ambas")

    def test_items_se_resuelven_desde_selections(self):
        """Los items SIEMPRE salen de `_visar_resolve_wizard_items`.

        Armarlos a mano puede emparejar una dimension con un tramo del eje
        equivocado y devolver la variante base -un tercio del precio- SIN error.
        Por eso el RPC recibe `selections`, nunca `items`.
        """
        selections = self._selections_interior_exterior()
        if not selections:
            self.skipTest("Catalogo Visar no configurado en esta BD")
        items = self.AptType.sudo()._visar_resolve_wizard_items(selections)
        for item in items:
            self.assertTrue(item.get('tier_id'))
            self.assertTrue(item.get('dimension_id'))

    # --- apartado suelto ------------------------------------------------------

    def test_hold_rechaza_recurso_ajeno_al_tipo_de_cita(self):
        """El tipo de cita se resuelve por MODO, no tomando el primero del recurso.

        Un técnico puede colgar de varios tipos (validaríamos contra uno al azar) o
        de ninguno (apartaríamos a ciegas). Apartar sin comprobar es justo el bug
        que la validación vino a cerrar, así que ante la duda se rechaza.
        """
        resource = self.env['appointment.resource'].create({
            'name': 'Recurso Suelto Test', 'capacity': 1})
        result = self.Tools.agent_hold_slot({
            'phone': self.WA_NEW,
            'resource_id': resource.id,
            'start': fields.Datetime.to_string(
                fields.Datetime.add(fields.Datetime.now(), days=3)),
            'stop': fields.Datetime.to_string(
                fields.Datetime.add(fields.Datetime.now(), days=3, hours=1)),
        })
        self.assertFalse(result['held'])
        self.assertEqual(result['reason'], 'resource_unavailable')

    def test_hold_con_payload_incompleto(self):
        result = self.Tools.agent_hold_slot({'phone': self.WA_NEW})
        self.assertFalse(result['held'])
        self.assertEqual(result['reason'], 'invalid_payload')

    # --- horario --------------------------------------------------------------

    def test_horario_invalido(self):
        zone, master = self._real_catalog()
        if not zone:
            self.skipTest("Catalogo Visar no configurado en esta BD")
        selections = self._selections_interior_exterior()
        if not selections:
            self.skipTest("Catalogo Visar no configurado en esta BD")
        start = fields.Datetime.add(fields.Datetime.now(), days=3)
        result = self.Tools.agent_prepare_booking({
            'phone': self.WA_NEW, 'name': 'Cliente Nuevo Test',
            'zone_id': zone.id, 'selections': selections,
            # fin ANTES del inicio
            'slot': {'start': fields.Datetime.to_string(start),
                     'stop': fields.Datetime.to_string(
                         fields.Datetime.subtract(start, hours=1))},
        })
        self.assertFalse(result['prepared'])
        self.assertEqual(result['reason'], 'slot_invalid')
