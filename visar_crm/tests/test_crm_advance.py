from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestCrmAdvance(TransactionCase):
    """Avance forward-only por posicion + advance/win desde orden + cron de
    caducidad. Ver .context/32-whatsapp-crm-lead-implementation.md (Fase C/D).
    """

    NAT = '9990001122'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Lead = cls.env['crm.lead']
        cls.team = cls.env.ref('visar_crm.crm_team_whatsapp')
        cls.s_nuevo = cls.env.ref('visar_crm.crm_stage_wa_nuevo')
        cls.s_valor = cls.env.ref('visar_crm.crm_stage_wa_valoracion')
        cls.s_cotiz = cls.env.ref('visar_crm.crm_stage_wa_cotizacion')
        cls.s_prog = cls.env.ref('visar_crm.crm_stage_wa_programado')
        cls.s_cerr = cls.env.ref('visar_crm.crm_stage_wa_cerrado')

        cls.group_fum = cls.env['visar.service.group'].create({
            'name': 'Test Fumigacion', 'code': 'TST_FUM'})
        cls.dim_int = cls.env['visar.service.dimension'].create({
            'name': 'Interior', 'code': 'TST_INT', 'group_id': cls.group_fum.id})
        cls.prod_fum = cls.env['product.template'].create({
            'name': 'Fum Interior', 'visar_is_service': True,
            'visar_dimension_id': cls.dim_int.id}).product_variant_id

        cls.group_mav = cls.env['visar.service.group'].create({
            'name': 'Test Areas Verdes', 'code': 'TST_MAV'})
        cls.dim_jar = cls.env['visar.service.dimension'].create({
            'name': 'Jardin', 'code': 'TST_JAR', 'group_id': cls.group_mav.id})
        cls.prod_mav = cls.env['product.template'].create({
            'name': 'Jardin', 'visar_is_service': True,
            'visar_dimension_id': cls.dim_jar.id}).product_variant_id

    def _lead(self, group, stage=None, nat=None):
        return self.Lead.create({
            'name': 'WA lead', 'type': 'opportunity', 'team_id': self.team.id,
            'stage_id': (stage or self.s_nuevo).id,
            'visar_service_group_id': group.id,
            'visar_wa_phone_norm': nat or self.NAT,
        })

    def _confirmed_order(self, products, phone=None):
        partner = self.env['res.partner'].create({
            'name': 'Cliente', 'phone': phone or self.NAT})
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': p.id, 'product_uom_qty': 1})
                           for p in products],
        })
        order.write({'state': 'sale'})  # dispara el hook de avance
        return order

    # --- forward-only por posicion ----------------------------------------

    def test_advance_forward_only_por_posicion(self):
        lead = self._lead(self.group_fum)
        self.assertTrue(lead._visar_advance_stage(self.s_prog))
        self.assertEqual(lead.stage_id, self.s_prog)
        # No regresa: valoracion esta antes que programado.
        self.assertFalse(lead._visar_advance_stage(self.s_valor))
        self.assertEqual(lead.stage_id, self.s_prog)
        # Sigue avanzando hacia adelante.
        self.assertTrue(lead._visar_advance_stage(self.s_cerr))
        self.assertEqual(lead.stage_id, self.s_cerr)

    def test_advance_ignora_etapa_ajena_y_rescata_desde_stock(self):
        # Una etapa que no es del pipeline no es destino valido.
        stock_new = self.env.ref('crm.stage_lead1')
        lead = self._lead(self.group_fum)
        self.assertFalse(lead._visar_advance_stage(stock_new))
        # Un lead parado en una etapa stock (rank -1) es rescatado al pipeline.
        lead.stage_id = stock_new
        self.assertTrue(lead._visar_advance_stage(self.s_prog))
        self.assertEqual(lead.stage_id, self.s_prog)

    # --- avance desde orden (Servicio programado) -------------------------

    def test_orden_confirmada_avanza_a_programado(self):
        lead = self._lead(self.group_fum)
        self._confirmed_order([self.prod_fum])
        self.assertEqual(lead.stage_id, self.s_prog)

    def test_combo_hace_fan_out_a_ambos_grupos(self):
        lead_fum = self._lead(self.group_fum)
        lead_mav = self._lead(self.group_mav)
        self._confirmed_order([self.prod_fum, self.prod_mav])
        self.assertEqual(lead_fum.stage_id, self.s_prog)
        self.assertEqual(lead_mav.stage_id, self.s_prog)

    def test_avance_con_producto_sin_puntero_inverso(self):
        """Regresion: en el catalogo real el producto NO trae visar_dimension_id;
        el enlace vive del lado de la dimension (dimension.product_tmpl_id). La
        orden confirmada debe resolver el grupo y avanzar igual.
        """
        tmpl = self.env['product.template'].create({
            'name': 'Fum sin puntero inverso', 'visar_is_service': True})
        self.assertFalse(tmpl.visar_dimension_id)
        self.dim_int.product_tmpl_id = tmpl.id
        self.assertEqual(tmpl._visar_service_groups(), self.group_fum)

        lead = self._lead(self.group_fum)
        self._confirmed_order([tmpl.product_variant_id])
        self.assertEqual(lead.stage_id, self.s_prog)

    def test_orden_de_otro_telefono_no_toca_el_lead(self):
        lead = self._lead(self.group_fum)
        self._confirmed_order([self.prod_fum], phone='9990009999')
        self.assertEqual(lead.stage_id, self.s_nuevo)

    # --- won desde la tarea FSM (helper) ----------------------------------

    def test_win_avanza_a_cerrado_e_idempotente(self):
        lead = self._lead(self.group_fum, stage=self.s_prog)
        order = self._confirmed_order([self.prod_fum])  # ya lo dejo en prog (no-op)
        self.Lead._visar_crm_win_order_leads(order)
        self.assertEqual(lead.stage_id, self.s_cerr)
        # Idempotente: un lead ya en Cerrado se excluye del search.
        self.Lead._visar_crm_win_order_leads(order)
        self.assertEqual(lead.stage_id, self.s_cerr)

    # --- Descartadas (19-nov-2026; reemplaza al cron de caducidad) ----------

    def _envejecer(self, lead, dias):
        """create/write_date `dias` atrás (los maneja el ORM; se fuerza por SQL)."""
        antes = fields.Datetime.subtract(fields.Datetime.now(), days=dias)
        self.env.cr.execute(
            "UPDATE crm_lead SET create_date = %s, write_date = %s WHERE id = %s",
            (antes, antes, lead.id))
        lead.invalidate_recordset(['create_date', 'write_date'])

    def test_descarta_la_vieja_y_respeta_la_fresca(self):
        self.env['ir.config_parameter'].sudo().set_param('visar.crm.descartar_dias', '15')
        vieja = self._lead(self.group_fum, nat='9990001111')
        fresca = self._lead(self.group_mav, nat='9990002222')
        self._envejecer(vieja, 20)
        self.Lead._visar_crm_expire_stale_leads()  # el cron llama este nombre
        descartadas = self.env.ref('visar_crm.crm_stage_descartadas')
        self.assertEqual(vieja.stage_id, descartadas)
        self.assertTrue(vieja.active, "descartar no archiva: se ve en su carril")
        self.assertEqual(fresca.stage_id, self.s_nuevo)

    def test_con_cita_no_se_descarta(self):
        self.env['ir.config_parameter'].sudo().set_param('visar.crm.descartar_dias', '15')
        valoracion = self._lead(self.group_fum, stage=self.s_valor, nat='9990004444')
        programado = self._lead(self.group_mav, stage=self.s_prog, nat='9990005555')
        for lead in valoracion | programado:
            self._envejecer(lead, 40)
        self.Lead._visar_crm_descartar_inactivas()
        self.assertEqual(valoracion.stage_id, self.s_valor)
        self.assertEqual(programado.stage_id, self.s_prog)

    def test_cero_dias_no_descarta(self):
        self.env['ir.config_parameter'].sudo().set_param('visar.crm.descartar_dias', '0')
        lead = self._lead(self.group_fum, nat='9990003333')
        self._envejecer(lead, 99)
        self.Lead._visar_crm_descartar_inactivas()
        self.assertEqual(lead.stage_id, self.s_nuevo)

    def test_web_solo_se_descarta_en_nuevo(self):
        self.env['ir.config_parameter'].sudo().set_param('visar.crm.descartar_dias', '15')
        web = self.env.ref('sales_team.salesteam_website_sales')
        # Equipo y etapa en la creación, como los crea la web.
        def web_lead(group, stage, nat):
            return self.Lead.create({
                'name': 'Web lead', 'type': 'opportunity', 'team_id': web.id,
                'stage_id': stage.id, 'visar_service_group_id': group.id,
                'visar_wa_phone_norm': nat})
        carrito = web_lead(self.group_fum, self.s_nuevo, '9990006666')
        cotizada = web_lead(self.group_mav, self.s_cotiz, '9990007777')
        self.assertEqual(cotizada.stage_id, self.s_cotiz)
        for lead in carrito | cotizada:
            self._envejecer(lead, 20)
        self.Lead._visar_crm_descartar_inactivas()
        self.assertEqual(carrito.stage_id, self.env.ref('visar_crm.crm_stage_descartadas'))
        self.assertEqual(cotizada.stage_id, self.s_cotiz, "en web, solo el carrito")

    def test_una_descartada_no_se_reusa(self):
        descartadas = self.env.ref('visar_crm.crm_stage_descartadas')
        vieja = self._lead(self.group_fum, stage=descartadas, nat='9990008888')
        self.assertFalse(self.Lead._visar_open_lead(
            '9990008888', self.group_fum, self.team, self.s_cerr))
        self.assertEqual(self.Lead._visar_descartada_previa(
            '9990008888', self.group_fum, self.team), vieja)
        nueva = self._lead(self.group_fum, nat='9990008888')
        nueva._visar_anotar_previa(vieja)
        self.assertEqual(nueva.visar_descartada_previa_id, vieja)
        # Oficina la arrastra de vuelta: vuelve a ser la abierta.
        nueva.stage_id = self.s_cerr
        vieja.stage_id = self.s_nuevo
        self.assertEqual(self.Lead._visar_open_lead(
            '9990008888', self.group_fum, self.team, self.s_cerr), vieja)
