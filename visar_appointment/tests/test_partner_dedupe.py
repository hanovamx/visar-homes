from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_appointment.controllers.appointment import (
    VisarAppointmentController,
)


@tagged('post_install', '-at_install')
class TestPartnerNat10(TransactionCase):
    """La clave `visar_phone_nat10` es la ÚNICA noción de "mismo número" que
    comparten el dedupe de reservas y la búsqueda del agente. Se prueba la regla
    de normalización (últimos 10 dígitos, False si no llegan) y que el campo
    almacenado se calcula igual.
    """

    def test_normalize_variants_collapse(self):
        # Todos los formatos MX del mismo número caen en la misma clave nacional.
        Partner = self.env['res.partner']
        for raw in ('8123415696', '+528123415696', '52 812 341 5696',
                    '5218123415696', '(81) 2341-5696'):
            self.assertEqual(
                Partner._visar_phone_nat10_value(raw), '8123415696',
                "%r debería normalizar a 8123415696" % raw)

    def test_normalize_blank_is_false(self):
        # Sin teléfono -> False (no ''), para que los partners sin número no
        # colisionen todos en la misma clave vacía.
        Partner = self.env['res.partner']
        for raw in ('', None, '   ', '123', '812341569'):  # < 10 dígitos
            self.assertIs(
                Partner._visar_phone_nat10_value(raw), False,
                "%r debería normalizar a False" % raw)

    def test_stored_field_matches_rule(self):
        # El campo almacenado se computa con la misma regla y es buscable.
        legacy = self.env['res.partner'].create({
            'name': 'Legacy', 'phone': '8123415696'})
        formatted = self.env['res.partner'].create({
            'name': 'Formatted', 'phone': '+528123415696'})
        self.assertEqual(legacy.visar_phone_nat10, '8123415696')
        self.assertEqual(formatted.visar_phone_nat10, '8123415696')
        found = self.env['res.partner'].search(
            [('visar_phone_nat10', '=', '8123415696')])
        self.assertIn(legacy, found)
        self.assertIn(formatted, found)

    def test_stored_field_blank_partner(self):
        # Un partner sin teléfono guarda False, no ''.
        p = self.env['res.partner'].create({'name': 'Sin telefono'})
        self.assertIs(p.visar_phone_nat10, False)


@tagged('post_install', '-at_install')
class TestBookingDedupe(TransactionCase):
    """Dedupe por teléfono en la reserva. El core siempre crea un partner nuevo;
    aquí se prueba la lógica pura que decide con cuál canónico fusionarlo
    (`_visar_canonical_partner_for`) y el relleno de blancos
    (`_visar_fill_partner_blanks`), sin depender del contexto HTTP. La marca
    (max id antes del super) se simula pasando `marker` explícito.

    Los tests que BUSCAN por el número usan uno sintético (`9990001122`) en vez
    del 8123415696 real: la BD de test es una copia de producción, donde ese
    número ya tiene 3 partners, y colisionaría con las aserciones exactas.
    """

    # Número nacional sintético (no existe en la copia de producción) y sus
    # formatos legacy raw / +52, que deben caer en la misma clave.
    RAW = '9990001122'
    E164 = '+529990001122'

    def _throwaway(self, **vals):
        # Un partner "desechable" recién creado por el core: sin login.
        return self.env['res.partner'].create(
            {'name': 'Nuevo', 'phone': self.E164, **vals})

    def test_zero_matches_keeps_new(self):
        # 0 coincidencias -> cliente genuinamente nuevo, no se fusiona nada.
        new = self._throwaway()
        canonical = VisarAppointmentController._visar_canonical_partner_for(
            new, marker=new.id - 1)
        self.assertFalse(canonical)

    def test_one_match_reused(self):
        # 1 coincidencia (formato distinto: legacy raw vs +52) -> se reutiliza.
        existing = self.env['res.partner'].create({
            'name': 'Jonathan Velazquez', 'phone': self.RAW})
        new = self._throwaway()
        canonical = VisarAppointmentController._visar_canonical_partner_for(
            new, marker=existing.id)
        self.assertEqual(canonical, existing)

    def test_one_match_fills_blank_email(self):
        # Al reutilizar, un campo en blanco del canónico se rellena.
        existing = self.env['res.partner'].create({
            'name': 'Jonathan', 'phone': self.RAW})  # sin email
        self.assertFalse(existing.email)
        new = self._throwaway(email='jonathan@example.com')
        VisarAppointmentController._visar_fill_partner_blanks(existing, new)
        self.assertEqual(existing.email, 'jonathan@example.com')

    def test_one_match_different_email_still_reuses(self):
        # LA regresión que define el arreglo: aunque el email del formulario sea
        # distinto al guardado, se reutiliza el partner (no se crea otro) y NO se
        # pisa el email guardado.
        existing = self.env['res.partner'].create({
            'name': 'Jonathan', 'phone': self.RAW,
            'email': 'guardado@example.com'})
        new = self._throwaway(email='otro@example.com')
        canonical = VisarAppointmentController._visar_canonical_partner_for(
            new, marker=existing.id)
        self.assertEqual(canonical, existing)
        VisarAppointmentController._visar_fill_partner_blanks(canonical, new)
        self.assertEqual(canonical.email, 'guardado@example.com',
                         "el email guardado no debe sobrescribirse")

    def test_one_match_different_name_preserved_and_logged(self):
        # Nombre distinto -> se conserva el guardado y se registra la discrepancia.
        existing = self.env['res.partner'].create({
            'name': 'Jonathan Velazquez', 'phone': self.RAW})
        new = self._throwaway(name='Jon Velazquez')
        logger = 'odoo.addons.visar_appointment.controllers.appointment'
        with self.assertLogs(logger, level='INFO') as captured:
            VisarAppointmentController._visar_fill_partner_blanks(existing, new)
        self.assertEqual(existing.name, 'Jonathan Velazquez',
                         "el nombre guardado no debe sobrescribirse")
        self.assertTrue(
            any('conserva el nombre guardado' in m for m in captured.output),
            "debe registrarse la discrepancia de nombre para el staff")

    def test_two_matches_reuse_oldest_no_new_partner(self):
        # 2+ coincidencias -> se reutiliza el MÁS antiguo; el desechable se
        # descarta (no crece el desorden). Se registra para el staff.
        older = self.env['res.partner'].create({
            'name': 'Jonathan 1', 'phone': self.RAW})
        newer = self.env['res.partner'].create({
            'name': 'Jonathan 2', 'phone': self.E164})
        new = self._throwaway()
        logger = 'odoo.addons.visar_appointment.controllers.appointment'
        with self.assertLogs(logger, level='INFO') as captured:
            canonical = VisarAppointmentController._visar_canonical_partner_for(
                new, marker=newer.id)
        self.assertEqual(canonical, older, "se reutiliza el más antiguo")
        self.assertTrue(
            any('coincide con' in m for m in captured.output),
            "la ambigüedad preexistente debe registrarse")

    def test_partner_with_login_untouched(self):
        # Un partner ligado a un login (portal/staff reservando para sí) nunca se
        # trata como desechable, aunque su id esté por encima de la marca.
        existing = self.env['res.partner'].create({
            'name': 'Canonico', 'phone': self.RAW})
        user = self.env['res.users'].create({
            'name': 'Cliente Portal', 'login': 'dedupe_portal_test',
            'email': 'dedupe_portal_test@example.com', 'phone': self.E164,
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        canonical = VisarAppointmentController._visar_canonical_partner_for(
            user.partner_id, marker=existing.id)
        self.assertFalse(canonical, "un partner con login no debe fusionarse")

    def test_preexisting_partner_untouched(self):
        # Un partner que ya existía antes de la petición (id <= marca) no es un
        # desechable: no se toca aunque comparta número.
        existing = self.env['res.partner'].create({
            'name': 'Canonico', 'phone': self.RAW})
        also_old = self.env['res.partner'].create({
            'name': 'Tambien viejo', 'phone': self.E164})
        canonical = VisarAppointmentController._visar_canonical_partner_for(
            also_old, marker=also_old.id)  # id <= marker
        self.assertFalse(canonical)
