from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentTrackLead(TransactionCase):
    """agent_track_lead: crea/refresca leads en 'Nuevo', dedupe por (telefono,
    grupo), excluye clientes existentes del grupo y NUNCA avanza de etapa.
    Ver .context/31-whatsapp-crm-lead-mapping.md / 32-...-implementation.md.
    """

    # Telefonos sinteticos (no existen en la copia de produccion) y su forma
    # entrante de WhatsApp (52 pais + 1 movil + 10 digitos).
    RAW_A = '9990001122'
    WA_A = '5219990001122'
    RAW_B = '9990003344'
    WA_B = '5219990003344'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Lead = cls.env['crm.lead']
        cls.team = cls.env.ref('visar_crm.crm_team_whatsapp')
        cls.stage_nuevo = cls.env.ref('visar_crm.crm_stage_wa_nuevo')
        cls.stage_prog = cls.env.ref('visar_crm.crm_stage_wa_programado')

        # Catalogo minimo: dos grupos, una dimension cada uno.
        cls.group_fum = cls.env['visar.service.group'].create({
            'name': 'Test Fumigacion', 'code': 'TST_FUM'})
        cls.dim_int = cls.env['visar.service.dimension'].create({
            'name': 'Interior', 'code': 'TST_INT', 'group_id': cls.group_fum.id})
        cls.group_mav = cls.env['visar.service.group'].create({
            'name': 'Test Areas Verdes', 'code': 'TST_MAV'})
        cls.dim_jar = cls.env['visar.service.dimension'].create({
            'name': 'Jardin', 'code': 'TST_JAR', 'group_id': cls.group_mav.id})

    def _leads_for(self, nat, group):
        return self.Lead.search([
            ('visar_wa_phone_norm', '=', nat),
            ('visar_service_group_id', '=', group.id),
            ('team_id', '=', self.team.id),
        ])

    def test_crea_lead_en_nuevo_con_enriquecimiento(self):
        res = self.Tools.agent_track_lead({
            'phone': self.WA_A, 'service_code': 'TST_INT',
            'quote': {'cp': '64000', 'm2': 120, 'total': 690.0, 'currency': 'MXN'},
        })
        self.assertTrue(res['created'])
        self.assertIsNone(res['skipped_reason'])
        lead = self.Lead.browse(res['lead_id'])
        self.assertEqual(lead.stage_id, self.stage_nuevo)
        self.assertEqual(lead.visar_service_group_id, self.group_fum)
        self.assertEqual(lead.visar_wa_phone_norm, '9990001122')
        self.assertEqual(lead.visar_source, 'whatsapp')
        self.assertEqual(lead.team_id, self.team)
        self.assertAlmostEqual(lead.expected_revenue, 690.0)

    def test_dedupe_por_telefono_y_grupo(self):
        a = self.Tools.agent_track_lead({'phone': self.WA_A, 'service_code': 'TST_INT'})
        b = self.Tools.agent_track_lead({
            'phone': self.WA_A, 'service_code': 'TST_INT',
            'quote': {'total': 800.0, 'currency': 'MXN'}})
        self.assertFalse(b['created'])
        self.assertEqual(a['lead_id'], b['lead_id'])
        self.assertEqual(len(self._leads_for('9990001122', self.group_fum)), 1)
        # El refresco actualiza expected_revenue a la ultima cotizacion.
        self.assertAlmostEqual(self.Lead.browse(b['lead_id']).expected_revenue, 800.0)

    def test_multi_grupo_leads_separados(self):
        # Mismo telefono, distinto grupo -> dos leads independientes.
        fum = self.Tools.agent_track_lead({'phone': self.WA_A, 'service_code': 'TST_INT'})
        mav = self.Tools.agent_track_lead({'phone': self.WA_A, 'service_code': 'TST_JAR'})
        self.assertNotEqual(fum['lead_id'], mav['lead_id'])
        self.assertEqual(len(self._leads_for('9990001122', self.group_fum)), 1)
        self.assertEqual(len(self._leads_for('9990001122', self.group_mav)), 1)

    def test_telefono_invalido_no_crea(self):
        res = self.Tools.agent_track_lead({'phone': '123', 'service_code': 'TST_INT'})
        self.assertEqual(res['skipped_reason'], 'invalid_phone')
        self.assertIsNone(res['lead_id'])

    def test_service_code_desconocido_no_grupo(self):
        res = self.Tools.agent_track_lead({'phone': self.WA_A, 'service_code': 'NOPE'})
        self.assertEqual(res['skipped_reason'], 'no_group')
        self.assertIsNone(res['lead_id'])

    def test_cliente_existente_del_grupo_se_excluye(self):
        # Partner con una orden CONFIRMADA que incluye un servicio del grupo FUM.
        partner = self.env['res.partner'].create({'name': 'Cliente FUM', 'phone': self.RAW_B})
        product = self.env['product.template'].create({
            'name': 'Fumigacion Interior', 'visar_is_service': True,
            'visar_dimension_id': self.dim_int.id}).product_variant_id
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})],
        })
        order.write({'state': 'sale'})

        # Ya es cliente de fumigacion -> no genera lead de fumigacion.
        res_fum = self.Tools.agent_track_lead({'phone': self.WA_B, 'service_code': 'TST_INT'})
        self.assertEqual(res_fum['skipped_reason'], 'existing_customer')
        # Pero SI genera lead de areas verdes (grupo distinto).
        res_mav = self.Tools.agent_track_lead({'phone': self.WA_B, 'service_code': 'TST_JAR'})
        self.assertTrue(res_mav['created'])
        self.assertIsNone(res_mav['skipped_reason'])

    def test_cliente_existente_via_enlace_dimension_producto(self):
        """Regresion: en el catalogo real el producto NO trae visar_dimension_id;
        el enlace vive del lado de la dimension (dimension.product_tmpl_id). La
        exclusion debe resolver el grupo igual, y seguir siendo POR GRUPO.
        """
        partner = self.env['res.partner'].create(
            {'name': 'Cliente MAV', 'phone': self.RAW_B})
        tmpl = self.env['product.template'].create({
            'name': 'Mantenimiento de areas verdes', 'visar_is_service': True})
        self.assertFalse(tmpl.visar_dimension_id)  # sin puntero inverso
        self.dim_jar.product_tmpl_id = tmpl.id     # enlace autoritativo
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': tmpl.product_variant_id.id, 'product_uom_qty': 1})],
        })
        order.write({'state': 'sale'})

        # Ya es cliente de areas verdes -> no genera lead de areas verdes.
        res_mav = self.Tools.agent_track_lead(
            {'phone': self.WA_B, 'service_code': 'TST_JAR'})
        self.assertEqual(res_mav['skipped_reason'], 'existing_customer')
        # Pero SI genera lead de fumigacion (grupo distinto).
        res_fum = self.Tools.agent_track_lead(
            {'phone': self.WA_B, 'service_code': 'TST_INT'})
        self.assertTrue(res_fum['created'])
        self.assertIsNone(res_fum['skipped_reason'])

    def test_producto_que_cubre_dos_dimensiones_excluye_por_grupo(self):
        """Un solo producto ("Fumigacion interior + exterior") cubre DOS
        dimensiones del mismo grupo. Comprarlo excluye el grupo completo, se
        pregunte por la dimension que se pregunte.
        """
        dim_ext = self.env['visar.service.dimension'].create({
            'name': 'Exterior', 'code': 'TST_EXT', 'group_id': self.group_fum.id})
        tmpl = self.env['product.template'].create({
            'name': 'Fumigacion interior + exterior', 'visar_is_service': True})
        self.dim_int.product_tmpl_id = tmpl.id
        dim_ext.product_tmpl_id = tmpl.id
        self.assertEqual(tmpl._visar_service_groups(), self.group_fum)

        partner = self.env['res.partner'].create(
            {'name': 'Cliente FUM', 'phone': self.RAW_B})
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': tmpl.product_variant_id.id, 'product_uom_qty': 1})],
        })
        order.write({'state': 'sale'})

        for code in ('TST_INT', 'TST_EXT'):
            res = self.Tools.agent_track_lead(
                {'phone': self.WA_B, 'service_code': code})
            self.assertEqual(res['skipped_reason'], 'existing_customer', code)

    def test_nunca_avanza_de_nuevo(self):
        # Aunque un lead ya este en una etapa posterior, el agente no lo mueve
        # (ni lo regresa): agent_track_lead solo crea/refresca en 'Nuevo'.
        res = self.Tools.agent_track_lead({'phone': self.WA_A, 'service_code': 'TST_INT'})
        lead = self.Lead.browse(res['lead_id'])
        lead.stage_id = self.stage_prog  # simula avance por automatizacion
        again = self.Tools.agent_track_lead({
            'phone': self.WA_A, 'service_code': 'TST_INT',
            'quote': {'total': 900.0, 'currency': 'MXN'}})
        self.assertFalse(again['created'])
        self.assertEqual(lead.stage_id, self.stage_prog)  # sigue en Programado
        self.assertAlmostEqual(lead.expected_revenue, 900.0)  # pero se enriquecio
