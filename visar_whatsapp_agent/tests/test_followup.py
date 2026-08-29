# -*- coding: utf-8 -*-
"""Recontacto de leads frios: la aritmetica, las exclusiones y el cron.

Odoo decide **a quien y cuando**; el runtime decide **que**. Lo que se prueba
aqui es lo primero. La redaccion del mensaje vive en `visar_fastapi` y tiene sus
propias pruebas (`tests/test_followup.py` de aquel lado).

Lo mas caro de equivocar es la aritmetica de la ventana, porque el sintoma de un
fallo ahi **no es un error**: WhatsApp descarta los mensajes libres pasadas 24 h
del ultimo mensaje del cliente, en silencio, y el unico sitio donde se veria
serian las ventas que no ocurrieron. Por eso la validacion es al guardar.
"""
from datetime import datetime

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestFollowupConfig(TransactionCase):
    """La configuracion no puede poder guardarse rota."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env['visar.followup.config']

    def _config(self, **overrides):
        vals = {'name': 'Test', 'delay_minutes': 360,
                'window_start_hour': 6, 'window_end_hour': 18}
        vals.update(overrides)
        return self.Config.create(vals)

    def test_los_valores_de_fabrica_son_validos(self):
        config = self._config()
        self.assertEqual(config.delay_minutes, 360)
        self.assertTrue(config.enabled)

    def test_la_espera_tiene_que_caber_en_el_horario_habil(self):
        """El corazon de la validacion, y la razon de que exista.

        Peor caso = espera + (24 - ventana). Con 13 h de espera y una ventana de
        12 h el peor caso son 25 h: el mensaje no rebotaria, se descartaria sin
        error. Se rechaza al guardar en vez de fallar callado durante meses.
        """
        with self.assertRaises(ValidationError):
            self._config(delay_minutes=13 * 60)

    def test_una_ventana_mas_larga_admite_mas_espera(self):
        """No es un tope inventado: es una relacion entre los dos numeros."""
        config = self._config(delay_minutes=13 * 60,
                              window_start_hour=6, window_end_hour=23)
        self.assertEqual(config.delay_minutes, 13 * 60)

    def test_el_horario_no_puede_cruzar_la_medianoche(self):
        with self.assertRaises(ValidationError):
            self._config(window_start_hour=22, window_end_hour=6)

    def test_la_espera_no_puede_ser_cero(self):
        with self.assertRaises(ValidationError):
            self._config(delay_minutes=0)


@tagged('post_install', '-at_install')
class TestFollowupVentana(TransactionCase):
    """La aritmetica de "cuando toca escribir", en la zona de Visar."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Lead = cls.env['crm.lead']
        cls.config = cls.env['visar.followup.config'].create({
            'name': 'Test', 'delay_minutes': 360,
            'window_start_hour': 6, 'window_end_hour': 18})
        # Monterrey es UTC-6 y no tiene horario de verano desde 2022.
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.agent.timezone', 'America/Monterrey')

    def _due(self, utc_str):
        return self.Lead._visar_wa_followup_due_at(
            fields.Datetime.from_string(utc_str), self.config)

    def test_dentro_del_horario_no_se_mueve(self):
        """10:00 local + 6 h = 16:00 local, que es hora habil."""
        # 16:00 UTC = 10:00 en Monterrey.
        self.assertEqual(self._due('2026-08-28 16:00:00'),
                         datetime(2026, 8, 28, 22, 0))  # 16:00 local

    def test_si_vence_de_madrugada_espera_a_que_abra(self):
        """El caso que el cliente pidio: nada de WhatsApp a las 2 de la manana.

        Ultimo mensaje 21:00 local -> vence 03:00 -> sale a las 06:00.
        """
        # 03:00 UTC = 21:00 del dia anterior en Monterrey.
        due = self._due('2026-08-29 03:00:00')
        self.assertEqual(due, datetime(2026, 8, 29, 12, 0))  # 06:00 local

    def test_si_vence_de_noche_se_va_al_dia_siguiente(self):
        """Ultimo mensaje 14:00 local -> vence 20:00 -> sale manana a las 06:00."""
        # 20:00 UTC = 14:00 en Monterrey.
        due = self._due('2026-08-28 20:00:00')
        self.assertEqual(due, datetime(2026, 8, 29, 12, 0))  # 06:00 del dia 29

    def test_el_peor_caso_cabe_en_24_horas(self):
        """La afirmacion que sostiene toda la validacion, comprobada de verdad.

        El peor caso es la espera que vence justo al cerrar: 12:01 local + 6 h =
        18:01, fuera, y hay que aguantar hasta las 06:00. Total ~18 h, con 6 h de
        margen contra la ventana de 24 h de Meta.
        """
        ultimo = fields.Datetime.from_string('2026-08-28 18:01:00')  # 12:01 local
        due = self._due('2026-08-28 18:01:00')
        self.assertLess((due - ultimo).total_seconds() / 3600.0, 23.0)


@tagged('post_install', '-at_install')
class TestFollowupInteres(TransactionCase):
    """agent_track_interest: ficha para quien NO llego a cotizar."""

    RAW = '9990005566'
    WA = '5219990005566'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Lead = cls.env['crm.lead']
        cls.stage_nuevo = cls.env.ref('visar_crm.crm_stage_wa_nuevo')
        cls.env['visar.followup.config'].search([]).unlink()
        cls.config = cls.env['visar.followup.config'].create({
            'name': 'Test', 'delay_minutes': 360,
            'window_start_hour': 0, 'window_end_hour': 24})

    def test_abre_lead_sin_grupo_cuando_no_se_sabe_el_servicio(self):
        """La diferencia con `agent_track_lead`, y el motivo de que exista.

        Preguntar por cobertura y no volver es perder un cliente igual que
        cotizar y no volver. `agent_track_lead` descarta esto con 'no_group'.
        """
        res = self.Tools.agent_track_interest({
            'phone': self.WA, 'context': {'etapa': 'pregunto'}})
        self.assertIsNone(res['skipped_reason'])
        self.assertTrue(res['created'])
        lead = self.Lead.browse(res['lead_id'])
        self.assertFalse(lead.visar_service_group_id)
        self.assertEqual(lead.stage_id, self.stage_nuevo)

    def test_programa_el_recontacto_con_su_foto(self):
        res = self.Tools.agent_track_interest({
            'phone': self.WA,
            'context': {'etapa': 'cuestionario', 'cp': '64000',
                        'wa_id': self.WA}})
        lead = self.Lead.browse(res['lead_id'])
        self.assertEqual(lead.visar_wa_followup_state, 'scheduled')
        self.assertTrue(lead.visar_wa_followup_due)
        self.assertEqual(lead._visar_wa_followup_data()['cp'], '64000')

    def test_cada_turno_reprograma(self):
        """El recontacto sale 6 h despues del ULTIMO mensaje, no del primero.

        Sin esto, un cliente que estuvo conversando media hora recibiria el
        empujon cinco horas y media despues de irse, no seis.
        """
        primero = self.Tools.agent_track_interest({'phone': self.WA})
        lead = self.Lead.browse(primero['lead_id'])
        lead.visar_wa_followup_due = fields.Datetime.from_string(
            '2020-01-01 00:00:00')
        self.Tools.agent_track_interest({'phone': self.WA})
        self.assertGreater(lead.visar_wa_followup_due,
                           fields.Datetime.from_string('2020-01-02 00:00:00'))

    def test_telefono_invalido_no_abre_nada(self):
        res = self.Tools.agent_track_interest({'phone': '123'})
        self.assertEqual(res['skipped_reason'], 'invalid_phone')

    def test_drop_cancela_el_recontacto(self):
        """Las dos exclusiones que Odoo no puede ver por su cuenta."""
        res = self.Tools.agent_track_interest({'phone': self.WA})
        lead = self.Lead.browse(res['lead_id'])
        salida = self.Tools.agent_drop_followup(
            {'phone': self.WA, 'reason': 'queja'})
        self.assertEqual(salida['dropped'], 1)
        self.assertEqual(lead.visar_wa_followup_state, 'skipped')
        self.assertIn('queja', (lead.visar_wa_followup_skip_reason or '').lower())

    def test_un_lead_cancelado_no_se_reprograma(self):
        """Quien dijo que no, dijo que no. Que vuelva a escribir es otra cosa."""
        res = self.Tools.agent_track_interest({'phone': self.WA})
        lead = self.Lead.browse(res['lead_id'])
        self.Tools.agent_drop_followup({'phone': self.WA, 'reason': 'declino'})
        self.Tools.agent_track_interest({'phone': self.WA})
        self.assertEqual(lead.visar_wa_followup_state, 'skipped')


@tagged('post_install', '-at_install')
class TestFollowupCron(TransactionCase):
    """A quien se le manda cuando llega la hora, y a quien ya no."""

    WA = '5219990007788'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Lead = cls.env['crm.lead']
        cls.Outbox = cls.env['visar.wa.lead.message']
        cls.stage_prog = cls.env.ref('visar_crm.crm_stage_wa_programado')
        cls.env['visar.followup.config'].search([]).unlink()
        cls.config = cls.env['visar.followup.config'].create({
            'name': 'Test', 'delay_minutes': 360,
            'window_start_hour': 0, 'window_end_hour': 24})

    def _lead_vencido(self, **overrides):
        res = self.Tools.agent_track_interest({
            'phone': self.WA, 'context': {'etapa': 'pregunto', 'wa_id': self.WA}})
        lead = self.Lead.browse(res['lead_id'])
        lead.write(dict({'visar_wa_followup_due': fields.Datetime.from_string(
            '2020-01-01 00:00:00')}, **overrides))
        return lead

    def test_encola_el_recontacto_que_ya_toca(self):
        lead = self._lead_vencido()
        self.Lead._visar_wa_cron_followup()
        self.assertEqual(lead.visar_wa_followup_state, 'queued')
        mensaje = self.Outbox.search([('lead_id', '=', lead.id)])
        self.assertEqual(len(mensaje), 1)
        self.assertEqual(mensaje.template_key, 'lead_followup')
        self.assertEqual(mensaje.phone, self.WA,
                         "el wa_id COMPLETO: con los 10 nacionales no se puede "
                         "mandar nada")

    def test_no_recontacta_a_quien_ya_pago(self):
        """La exclusion mas importante: entre programar y enviar pasan 6 h, y en
        6 h el cliente paga. Se pregunta al ENVIAR, no al programar."""
        lead = self._lead_vencido(stage_id=self.stage_prog.id)
        self.Lead._visar_wa_cron_followup()
        self.assertEqual(lead.visar_wa_followup_state, 'skipped')
        self.assertFalse(self.Outbox.search([('lead_id', '=', lead.id)]))

    def test_no_recontacta_a_quien_ya_atiende_un_asesor(self):
        """Un empujon automatico le pasaria por arriba a la persona."""
        lead = self._lead_vencido(visar_source='whatsapp_handoff')
        self.Lead._visar_wa_cron_followup()
        self.assertEqual(lead.visar_wa_followup_state, 'skipped')

    def test_apagar_el_recontacto_descarta_lo_programado(self):
        lead = self._lead_vencido()
        self.config.enabled = False
        self.Lead._visar_wa_cron_followup()
        self.assertEqual(lead.visar_wa_followup_state, 'skipped')

    def test_no_se_encola_dos_veces(self):
        """'En cola' no es 'Enviado', y tampoco es 'vuelve a intentarlo'."""
        lead = self._lead_vencido()
        self.Lead._visar_wa_cron_followup()
        self.Lead._visar_wa_cron_followup()
        self.assertEqual(len(self.Outbox.search([('lead_id', '=', lead.id)])), 1)

    def test_si_el_aviso_no_llega_el_lead_deja_de_decir_que_si(self):
        """La peor de las dos mentiras posibles.

        Un lead en 'Enviado' con un mensaje que nunca salio lo lee el asesor como
        "el agente ya insistio", y entonces no insiste el.
        """
        lead = self._lead_vencido()
        self.Lead._visar_wa_cron_followup()
        mensaje = self.Outbox.search([('lead_id', '=', lead.id)])
        mensaje._visar_mark_expired()
        self.assertEqual(lead.visar_wa_followup_state, 'skipped')
        self.assertFalse(lead.visar_wa_followup_sent_at)


@tagged('post_install', '-at_install')
class TestFollowupContrato(TransactionCase):
    """El payload que sale de aqui tiene que entrar alla.

    Es el punto exacto donde dos repos se pueden desincronizar sin que nadie se
    entere: Odoo arma el POST y `LeadFollowup` (pydantic, en
    `visar_fastapi/app/outbound.py`) lo valida. Un campo renombrado a un lado
    devuelve un 422 que solo se ve en `last_error` de un buzon que nadie mira.
    """

    WA = '5219990009900'

    def test_el_post_lleva_exactamente_lo_que_el_runtime_pide(self):
        self.env['visar.followup.config'].search([]).unlink()
        self.env['visar.followup.config'].create({
            'name': 'Test', 'delay_minutes': 360,
            'window_start_hour': 0, 'window_end_hour': 24})
        res = self.env['visar.agent.tools'].agent_track_interest({
            'phone': self.WA,
            'context': {'etapa': 'cotizado', 'wa_id': self.WA,
                        'servicio': 'Fumigacion'}})
        lead = self.env['crm.lead'].browse(res['lead_id'])
        lead.visar_wa_followup_due = fields.Datetime.from_string(
            '2020-01-01 00:00:00')
        self.env['crm.lead']._visar_wa_cron_followup()

        mensaje = self.env['visar.wa.lead.message'].search(
            [('lead_id', '=', lead.id)])
        payload = dict(mensaje._visar_wa_context(), **{
            'phone': mensaje.phone,
            'template_key': mensaje.template_key,
            'params': [],
            'fallback_text': mensaje.fallback_text,
        })
        self.assertEqual(set(payload), {
            'phone', 'template_key', 'params', 'fallback_text',
            'lead_id', 'contexto'})
        self.assertEqual(payload['template_key'], 'lead_followup')
        self.assertEqual(payload['contexto']['servicio'], 'Fumigacion')
        self.assertTrue(payload['fallback_text'],
                        "el respaldo NUNCA puede ir vacio: el endpoint lo "
                        "rechaza con 422 y el recontacto no sale")

    def test_el_endpoint_es_el_del_recontacto(self):
        """No reusa `/booking-event`: aquel reenvia texto, este pide redaccion."""
        self.assertEqual(
            self.env['visar.wa.lead.message']._visar_wa_endpoint(),
            '/internal/lead-followup')
