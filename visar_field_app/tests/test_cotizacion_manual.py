# -*- coding: utf-8 -*-
"""Tratamientos que se cotizan a mano, pedidos desde la hoja de trabajo (22-sep-2026).

Lo que se protege: que la hoja pida UNA cotización por servicio marcado y la retire
si se desmarca antes de cotizar; que el descuento de la valoración se aplique al
cotizar —también si el servicio es otro día— y nunca dos veces entre la visita y
sus cotizaciones; y los dos caminos: hacerlo en la misma visita o agendarlo aparte.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_field_app.models.upsell_servicio import PARAM_PRODUCTO_CREDITO

CP = '99902'


@tagged('post_install', '-at_install')
class TestCotizacionManual(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company = env.company
        cls.lista = env['product.pricelist'].create({
            'name': 'Lista cotizacion', 'company_id': company.id})
        cls.zona = env['visar.zone'].create({
            'name': 'Zona cotizacion', 'code': 'ZCQ', 'pricelist_id': cls.lista.id})
        env['visar.zone.cp'].create({'name': CP, 'zone_id': cls.zona.id})
        cls.cliente = env['res.partner'].create({'name': 'Cliente termitas', 'zip': CP})
        cls.tecnico = env['hr.employee'].create({'name': 'Tecnico que cotiza'})
        cls.proyecto = env['project.project'].create({
            'name': 'FSM valoraciones cotiza', 'is_fsm': True, 'allow_billable': True,
            'company_id': company.id})
        cls.valoracion = env['product.product'].create({
            'name': 'Valoracion cotiza', 'type': 'service', 'invoice_policy': 'order',
            'list_price': 500.0, 'taxes_id': [(6, 0, [])], 'visar_is_valuation': True})
        cls.termitas = env['product.template'].create({
            'name': 'Tratamiento antitermita prueba', 'type': 'service',
            'invoice_policy': 'order', 'list_price': 0.0, 'taxes_id': [(6, 0, [])],
            'visar_quote_trigger': 'Termitas'})
        cls.descuento = env['product.product'].create({
            'name': 'Descuento cotiza', 'type': 'service', 'invoice_policy': 'order',
            'list_price': 0.0, 'taxes_id': [(6, 0, [])]})
        env['ir.config_parameter'].sudo().set_param(PARAM_PRODUCTO_CREDITO, cls.descuento.id)

    def _visita(self, pagada=True):
        pedido = self.env['sale.order'].create({
            'partner_id': self.cliente.id, 'pricelist_id': self.lista.id,
            'order_line': [(0, 0, {'product_id': self.valoracion.id, 'product_uom_qty': 1})]})
        pedido.action_confirm()
        if pagada:
            factura = pedido._create_invoices()
            factura.action_post()
            self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=factura.ids).create({})._create_payments()
        inicio = datetime.now().replace(microsecond=0)
        tarea = self.env['project.task'].create({
            'name': 'Valoracion con termitas', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id, 'sale_line_id': pedido.order_line[0].id,
            'planned_date_begin': inicio, 'date_deadline': inicio + timedelta(hours=1)})
        tarea._visar_set_stage(2)
        tarea.write({'visar_arrived_at': inicio, 'visar_service_start': inicio})
        return tarea

    def _hoja_marca(self, tarea, *nombres):
        """La hoja de la visita con esos servicios marcados."""
        with patch.object(type(tarea), '_visar_quote_identified_names',
                          lambda self: {n.lower() for n in nombres}):
            return tarea._visar_quote_requests_sync(self.tecnico)

    def _cotizar(self, cotizacion, precio):
        linea = cotizacion._visar_quote_service_lines()
        cotizacion.write({'order_line': [(1, linea.id, {'price_unit': precio})]})

    def _credito(self, pedido):
        return pedido.order_line.filtered('visar_valuation_credit')

    # ------------------------------------------------------------------
    def test_la_hoja_pide_una_cotizacion_por_servicio_marcado(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')

        self.assertEqual(len(cot), 1)
        self.assertEqual(cot.state, 'draft')
        self.assertEqual(cot.order_line.product_id.product_tmpl_id, self.termitas)
        self.assertEqual(cot.visar_quote_origin_task_id, tarea)
        self.assertEqual(cot.visar_quote_origin_order_id, tarea.sale_order_id)
        self.assertNotEqual(cot, tarea.sale_order_id, "cotización aparte")
        self.assertTrue(cot.activity_ids, "actividad para quien cotiza")

        self.assertFalse(self._hoja_marca(tarea, 'Termitas'), "guardar otra vez no duplica")
        self.assertEqual(len(tarea.visar_quote_order_ids), 1)

    def test_la_cotizacion_hereda_la_oportunidad_del_pedido_de_la_visita(self):
        """BUG-3 (2-oct-2026): esta cotización no tocaba el CRM en absoluto.

        El técnico marca «termitas», se crea una cotización de verdad con su
        actividad... y la ficha del cliente no se enteraba. Era uno de los dos
        flujos que creaban `sale.order` sin pasar por ningún sitio que escribiera
        `opportunity_id`.

        Se arregla con `_visar_inherit_crm_from`, un gancho declarado en
        `visar_base` **a propósito**: este módulo depende de `visar_fsm`, no de
        `visar_crm` ni de `sale_crm`, y solo necesita copiar un campo — no
        conocer el embudo. Si `visar_crm` no está instalado, es un no-op y la
        prueba se salta.
        """
        if 'opportunity_id' not in self.env['sale.order']._fields:
            self.skipTest("sin sale_crm no hay oportunidad que heredar")
        tarea = self._visita()
        ficha = self.env['crm.lead'].create({
            'name': 'Ficha de la visita', 'type': 'opportunity',
            'partner_id': self.cliente.id})
        tarea.sale_order_id.sudo().write({'opportunity_id': ficha.id})

        cot = self._hoja_marca(tarea, 'Termitas')

        self.assertEqual(
            cot.opportunity_id, ficha,
            "la cotización de termitas no llegó a la ficha del cliente: se ve "
            "en el pedido y no en el CRM, que es el bug que esto arregla")

    def test_sin_oportunidad_en_el_origen_no_se_inventa_ninguna(self):
        if 'opportunity_id' not in self.env['sale.order']._fields:
            self.skipTest("sin sale_crm no hay oportunidad que heredar")
        tarea = self._visita()
        self.assertFalse(tarea.sale_order_id.opportunity_id)
        cot = self._hoja_marca(tarea, 'Termitas')
        self.assertFalse(cot.opportunity_id)

    def test_un_servicio_sin_producto_de_cotizacion_no_pide_nada(self):
        tarea = self._visita()
        self.assertFalse(self._hoja_marca(tarea, 'Riego'))

    def test_desmarcar_antes_de_cotizar_la_cancela(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._hoja_marca(tarea)
        self.assertEqual(cot.state, 'cancel')

    def test_desmarcar_ya_cotizada_no_la_toca(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        self._hoja_marca(tarea)
        self.assertEqual(cot.state, 'draft')

    def test_al_cotizar_entra_el_descuento_de_la_valoracion(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self.assertFalse(self._credito(cot), "en $0 no hay nada que descontar")

        self._cotizar(cot, 3000.0)
        self.assertEqual(self._credito(cot).price_unit, -500.0)
        self.assertEqual(cot.amount_total, 2500.0)

        self._cotizar(cot, 300.0)
        self.assertEqual(self._credito(cot).price_unit, -300.0, "nunca más que el servicio")

    def test_sin_valoracion_pagada_no_hay_descuento(self):
        tarea = self._visita(pagada=False)
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        self.assertFalse(self._credito(cot))

    def test_el_descuento_es_uno_entre_la_cotizacion_y_la_visita(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        self.assertTrue(self._credito(cot))

        # Un servicio vendido en la visita ya no lleva descuento: lo tiene la cotización.
        credito, _linea = tarea._visar_valuation_credit_for(
            cot._visar_quote_service_lines(), propios=self.env['sale.order.line'])
        self.assertEqual(credito, 0.0)

    def test_agendar_despues(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        with self.assertRaises(UserError):
            cot.action_visar_quote_schedule_later()  # todavía en $0

        self._cotizar(cot, 3000.0)
        cot.action_visar_quote_schedule_later()

        self.assertEqual(cot.visar_quote_path, 'agendar')
        self.assertEqual(cot.state, 'sent')
        self.assertEqual(cot.amount_total, 2500.0)
        self.assertFalse(cot.activity_ids, "la actividad de cotizar se cerró")
        self.assertEqual(tarea.sale_order_id.order_line.product_id, self.valoracion,
                         "el pedido de la valoración no se toca")

    def test_hacer_en_la_misma_visita(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)

        cot.action_visar_quote_in_visit()

        self.assertEqual(cot.state, 'cancel')
        self.assertEqual(cot.visar_quote_path, 'en_visita')
        self.assertEqual(tarea._visar_upsell_state(), 'borrador',
                         "el técnico lo ve en su carrito para generar el cobro")
        abiertas = tarea._visar_upsell_open_lines()
        self.assertIn(self.termitas, abiertas.product_id.product_tmpl_id)
        self.assertEqual(self._credito(tarea.sale_order_id).price_unit, -500.0,
                         "el descuento pasa al pedido de la visita, una sola vez")

        tarea._visar_upsell_confirm(self.tecnico)
        self.assertEqual(tarea._visar_upsell_invoice().amount_total, 2500.0)

    def test_en_la_misma_visita_solo_si_el_tecnico_sigue_ahi(self):
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        tarea._visar_set_stage(3)  # ya se cerró la visita
        with self.assertRaises(UserError):
            cot.action_visar_quote_in_visit()
        self.assertEqual(cot.state, 'draft', "sigue disponible para agendar después")

    def test_lee_la_hoja_real_de_valoracion(self):
        """Sin simulación: el campo "Servicios identificados" de la hoja de valoración."""
        Plantilla = self.env['worksheet.template']
        plantilla = Plantilla.search([], limit=50).filtered(
            lambda t: t.model_id.model in self.env
            and 'x_servicios_identificados' in self.env[t.model_id.model]._fields)[:1]
        if not plantilla:
            self.skipTest("Esta BD no tiene la hoja de valoración con Servicios identificados")
        Hoja = self.env[plantilla.model_id.model]
        Servicio = self.env[Hoja._fields['x_servicios_identificados'].comodel_name]
        termitas = Servicio.search([('x_name', '=ilike', 'termitas')], limit=1) \
            or Servicio.create({'x_name': 'Termitas'})
        tarea = self._visita()
        tarea.worksheet_template_id = plantilla
        Hoja.create({'x_project_task_id': tarea.id,
                     'x_servicios_identificados': [(6, 0, termitas.ids)]})

        cot = tarea._visar_quote_requests_sync(self.tecnico)

        self.assertEqual(cot.order_line.product_id.product_tmpl_id, self.termitas)

    def test_agendar_despues_avisa_al_cliente_con_el_monto(self):
        self.cliente.phone = '5218190005566'
        tarea = self._visita()
        cot = self._hoja_marca(tarea, 'Termitas')
        self._cotizar(cot, 3000.0)
        cot.action_visar_quote_schedule_later()

        aviso = self.env['visar.wa.message'].search([
            ('quote_order_id', '=', cot.id), ('template_key', '=', 'quote_ready')])
        self.assertEqual(len(aviso), 1)
        self.assertEqual(aviso.task_id, tarea, "cuelga de la visita que la pidió")
        self.assertIn('2,500.00', aviso.params_json, "el monto ya con el descuento")
        self.assertEqual(aviso._visar_wa_context().get('quote_id'), cot.id)


@tagged('post_install', '-at_install')
class TestCotizacionHuecos(TestCotizacionManual):
    """Cuando la hoja pide algo y NO nace cotización, alguien se entera.

    El circuito tenía tres salidas mudas, y las tres acababan igual: el técnico
    marcaba el servicio, la app decía "guardado", y nadie descubría que no había
    cotización hasta que el cliente preguntaba —o nunca—. Le costó al usuario una
    noche de depuración con la visita 678, buscando un disparador mal escrito
    cuando el problema era que la visita había perdido su pedido.

    Cubre los tres huecos, la idempotencia (la hoja se guarda muchas veces) y que
    un aviso no pueda tumbar el guardado del técnico.
    """

    def _hoja_real(self, tarea, *nombres, otro=''):
        """Como `_hoja_marca`, pero pasando por los lectores que usa el aviso.

        `_hoja_marca` parchea `_visar_quote_identified_names`, que ya no es de
        donde salen los nombres del aviso: ese necesita el texto SIN pasar a
        minúsculas, para poder enseñarlo tal y como se escribió.
        """
        with patch.object(type(tarea), '_visar_quote_identified_display',
                          lambda self: list(nombres)), \
                patch.object(type(tarea), '_visar_quote_identified_otro',
                             lambda self: otro):
            return tarea._visar_quote_requests_sync(self.tecnico)

    def _avisos(self, tarea):
        return tarea.activity_ids.filtered(
            lambda a: (a.summary or '').startswith("Revisar la hoja"))

    # --- ② el nombre no coincide ------------------------------------------

    def test_un_nombre_que_no_casa_deja_aviso(self):
        tarea = self._visita()
        self.assertFalse(self._hoja_real(tarea, 'Alacranes (prueba)'))
        avisos = self._avisos(tarea)
        self.assertEqual(len(avisos), 1)
        self.assertIn('Alacranes (prueba)', avisos.summary)

    def test_el_aviso_dice_que_nombres_SI_estan_configurados(self):
        """Lo que convierte el aviso en algo accionable en vez de una queja."""
        tarea = self._visita()
        self._hoja_real(tarea, 'Alacranes (prueba)')
        self.assertIn('Termitas', self._avisos(tarea).note)

    def test_el_nombre_se_ensena_tal_y_como_se_escribio(self):
        """En minúsculas parecería otro texto, y es lo que se compara a ojo."""
        tarea = self._visita()
        self._hoja_real(tarea, 'ALACRANES')
        self.assertIn('ALACRANES', self._avisos(tarea).summary)

    def test_un_servicio_que_SI_casa_no_deja_aviso(self):
        tarea = self._visita()
        self.assertTrue(self._hoja_real(tarea, 'Termitas'))
        self.assertFalse(self._avisos(tarea))

    # --- ① la visita no tiene pedido --------------------------------------

    def test_sin_pedido_detras_deja_aviso(self):
        """El caso de la visita 678: le cambiaron el cliente y perdió su línea.

        Antes esto era un `return` mudo —el primer `if` del método— y no dejaba
        ni una línea de log.
        """
        tarea = self.env['project.task'].create({
            'name': 'Valoracion sin pedido', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id})
        self.assertFalse(self._hoja_real(tarea, 'Termitas'))
        avisos = self._avisos(tarea)
        self.assertEqual(len(avisos), 1)
        self.assertIn('sin pedido', avisos.summary)
        self.assertIn('cliente', avisos.note, "y se explica la causa más común")

    def test_sin_pedido_y_sin_nada_marcado_no_avisa(self):
        """Una hoja que no pidió cotización no tiene nada que revisar."""
        tarea = self.env['project.task'].create({
            'name': 'Valoracion vacia', 'project_id': self.proyecto.id,
            'partner_id': self.cliente.id})
        self._hoja_real(tarea)
        self.assertFalse(self._avisos(tarea))

    # --- ③ el texto libre --------------------------------------------------

    def test_el_texto_escrito_a_mano_deja_aviso(self):
        tarea = self._visita()
        self._hoja_real(tarea, otro='alacranes en la azotea')
        avisos = self._avisos(tarea)
        self.assertEqual(len(avisos), 1)
        self.assertIn('a mano', avisos.summary)
        self.assertIn('alacranes en la azotea', avisos.note)

    def test_el_texto_del_tecnico_va_ESCAPADO(self):
        """Ese campo lo teclea el técnico; el `note` de una actividad es HTML.

        Sin escapar, cualquiera con un teléfono podría meter etiquetas en el
        backend de Visar.
        """
        tarea = self._visita()
        self._hoja_real(tarea, otro='<b>ojo</b> aqui')
        nota = self._avisos(tarea).note
        self.assertNotIn('<b>ojo</b>', nota)
        self.assertIn('&lt;b&gt;', nota)

    def test_marcado_y_texto_libre_dejan_un_aviso_cada_uno(self):
        tarea = self._visita()
        self._hoja_real(tarea, 'Alacranes', otro='y tambien ratas')
        self.assertEqual(len(self._avisos(tarea)), 2)

    # --- idempotencia y robustez ------------------------------------------

    def test_guardar_otra_vez_no_duplica_el_aviso(self):
        """La hoja se guarda muchas veces: hay borrador, y se puede reabrir.

        Sin la clave de idempotencia, cada guardado dejaría otra actividad
        idéntica hasta enterrar la bandeja de quien cotiza, que es la forma más
        rápida de que un aviso útil deje de leerse.
        """
        tarea = self._visita()
        for _ in range(4):
            self._hoja_real(tarea, 'Alacranes (prueba)')
        self.assertEqual(len(self._avisos(tarea)), 1)

    def test_arreglar_el_nombre_hace_nacer_la_cotizacion(self):
        """El aviso tiene salida: se corrige y se vuelve a guardar."""
        tarea = self._visita()
        self.assertFalse(self._hoja_real(tarea, 'Alacranes (prueba)'))
        self.termitas.visar_quote_trigger = 'Alacranes (prueba)'
        self.assertTrue(self._hoja_real(tarea, 'Alacranes (prueba)'))

    def test_un_aviso_roto_no_tumba_el_guardado(self):
        """Va colgado del guardado con el técnico en casa del cliente."""
        tarea = self._visita()
        with patch.object(type(tarea), '_visar_quote_revisar_ahora',
                          side_effect=RuntimeError("boom")):
            cot = self._hoja_real(tarea, 'Termitas')
        self.assertTrue(cot, "la cotización nació igual")

    def test_el_aviso_va_a_quien_cotiza(self):
        usuario = self.env['res.users'].create({
            'name': 'Quien cotiza', 'login': 'cotiza_huecos@test.local'})
        self.env['ir.config_parameter'].sudo().set_param(
            'visar_field.cotizacion_responsable_id', usuario.id)
        tarea = self._visita()
        self._hoja_real(tarea, 'Alacranes (prueba)')
        self.assertEqual(self._avisos(tarea).user_id, usuario)


@tagged('post_install', '-at_install')
class TestDatosDelDomicilio(TestCotizacionManual):
    """Lo que la hoja sabe del LUGAR se sube al domicilio, no al cliente.

    El técnico ya captura el tipo de inmueble y las restricciones de acceso, y hoy
    se quedan sepultados dentro de la hoja de UNA visita: nadie los vuelve a ver
    salvo que abra esa hoja. Ni oficina al atender una llamada, ni el agente
    cuando el cliente escribe otra vez, ni el técnico de la visita siguiente —que
    vuelve al mismo portón sin saber del candado—.

    Lo importante del diseño: van al **domicilio** y no al cliente. Un cliente
    agenda para su casa y para su local, y «es un departamento» sobre la persona
    no es un dato incompleto, es falso.
    """

    def _con_domicilio(self, calle='Calle Prueba No. 1', colonia='Las Torres'):
        tarea = self._visita()
        domicilio = self.env['res.partner'].create({
            'name': 'Domicilio de servicio', 'type': 'delivery',
            'parent_id': self.cliente.commercial_partner_id.id,
            'street': calle, 'street2': colonia, 'zip': CP})
        tarea.sale_order_id._visar_set_service_shipping(domicilio)
        return tarea, domicilio

    def _hoja_dice(self, tarea, **campos):
        """Una hoja falsa que responde a los campos que pida la derivación."""
        class HojaFalsa:
            _fields = dict.fromkeys(campos, True)

            def __init__(self, datos):
                self._datos = datos

            def __getitem__(self, clave):
                return self._datos.get(clave)

            def __bool__(self):
                return True

        return patch.object(type(tarea), '_visar_quote_hoja',
                            lambda self: HojaFalsa(campos))

    # ------------------------------------------------------------------

    def test_el_tipo_de_inmueble_sube_al_domicilio(self):
        tarea, domicilio = self._con_domicilio()
        with self._hoja_dice(tarea, x_tipo_inmueble='Departamento'):
            tarea._visar_datos_domicilio_sync()
        Fact = self.env['visar.partner.fact']
        self.assertEqual(
            Fact._visar_facts_de(domicilio).get('tipo_inmueble'), 'Departamento')

    def test_NO_sube_al_cliente(self):
        """El punto entero: el cliente puede tener casa y local."""
        tarea, _domicilio = self._con_domicilio()
        with self._hoja_dice(tarea, x_tipo_inmueble='Departamento'):
            tarea._visar_datos_domicilio_sync()
        Fact = self.env['visar.partner.fact']
        self.assertFalse(Fact._visar_facts_de(self.cliente.commercial_partner_id))

    def test_el_acceso_sube_tambien(self):
        tarea, domicilio = self._con_domicilio()
        with self._hoja_dice(tarea, x_restricciones_acceso='Portón con candado'):
            tarea._visar_datos_domicilio_sync()
        self.assertEqual(
            self.env['visar.partner.fact']._visar_facts_de(domicilio).get('acceso'),
            'Portón con candado')

    def test_si_eligio_Otro_vale_lo_que_escribio(self):
        """«Otro» a secas no dice nada; lo que el técnico escribió, sí."""
        tarea, domicilio = self._con_domicilio()
        with self._hoja_dice(tarea, x_tipo_inmueble='Otro',
                             x_tipo_inmueble_otro='Casa en condominio'):
            tarea._visar_datos_domicilio_sync()
        self.assertEqual(
            self.env['visar.partner.fact']._visar_facts_de(domicilio).get(
                'tipo_inmueble'),
            'Casa en condominio')

    def test_dos_domicilios_del_mismo_cliente_no_se_mezclan(self):
        tarea1, dom1 = self._con_domicilio('Casa No. 1', 'Las Torres')
        with self._hoja_dice(tarea1, x_tipo_inmueble='Casa'):
            tarea1._visar_datos_domicilio_sync()
        tarea2, dom2 = self._con_domicilio('Local No. 2', 'Centro')
        with self._hoja_dice(tarea2, x_tipo_inmueble='Local comercial'):
            tarea2._visar_datos_domicilio_sync()
        Fact = self.env['visar.partner.fact']
        self.assertEqual(Fact._visar_facts_de(dom1).get('tipo_inmueble'), 'Casa')
        self.assertEqual(Fact._visar_facts_de(dom2).get('tipo_inmueble'),
                         'Local comercial')

    def test_una_correccion_a_mano_no_se_pisa(self):
        """La derivación corre en CADA guardado de la hoja."""
        tarea, domicilio = self._con_domicilio()
        self.env['visar.partner.fact']._visar_fact_set(
            domicilio, 'acceso', 'Hay que llamar al portero', origen='persona')
        with self._hoja_dice(tarea, x_restricciones_acceso='Portón con candado'):
            tarea._visar_datos_domicilio_sync()
        self.assertEqual(
            self.env['visar.partner.fact']._visar_facts_de(domicilio).get('acceso'),
            'Hay que llamar al portero')

    def test_sin_domicilio_de_servicio_no_hace_nada(self):
        """Pasa en visitas que no vienen de una reserva, y no es un problema."""
        tarea = self._visita()
        with self._hoja_dice(tarea, x_tipo_inmueble='Casa'):
            self.assertEqual(tarea._visar_datos_domicilio_sync(), 0)

    def test_un_fallo_no_tumba_el_guardado(self):
        """Va colgado del guardado, con el técnico en casa del cliente."""
        tarea, _domicilio = self._con_domicilio()
        with patch.object(type(tarea), '_visar_datos_domicilio_sync_ahora',
                          side_effect=RuntimeError("boom")):
            self.assertEqual(tarea._visar_datos_domicilio_sync(), 0)

    def test_un_campo_vacio_no_escribe_nada(self):
        tarea, domicilio = self._con_domicilio()
        with self._hoja_dice(tarea, x_tipo_inmueble=False,
                             x_restricciones_acceso=''):
            tarea._visar_datos_domicilio_sync()
        self.assertFalse(
            self.env['visar.partner.fact']._visar_facts_de(domicilio))
