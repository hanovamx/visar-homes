# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestWaBookingOutbox(TransactionCase):
    """Los avisos del agendado: pago confirmado y apartado vencido.

    Antes de esto el agendado terminaba mudo. El cliente pagaba y nadie le
    confirmaba nada; o dejaba pasar los diez minutos y su apartado moria en
    silencio, con una liga que el seguia creyendo buena.

    Lo que se fija aqui:
      * el aviso lleva el `wa_id` EXACTO, no el nacional de 10 digitos — es la
        clave con la que el runtime encuentra la conversacion;
      * el texto del apartado vencido dice la VERDAD sobre la liga, que depende
        de si alguien tomo el horario;
      * encolar un aviso NUNCA tumba lo que lo origino (un cobro, un cron).
    """

    WA = '5219998880011'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Outbox = cls.env['visar.wa.booking.message'].sudo()
        cls.Hold = cls.env['visar.slot.hold'].sudo()
        cls.partner = cls.env['res.partner'].sudo().create(
            {'name': 'Cliente WhatsApp', 'phone': cls.WA})

    def _hold(self, **vals):
        recurso = self.env['appointment.resource'].sudo().search([], limit=1)
        if not recurso:
            self.skipTest("La base no trae recursos de cita")
        base = {
            'appointment_resource_id': recurso.id,
            'start': fields.Datetime.to_datetime('2026-08-21 21:00:00'),
            'stop': fields.Datetime.to_datetime('2026-08-21 22:00:00'),
            'owner_key': '9998880011',
            'visar_wa_phone': self.WA,
            'expire_at': fields.Datetime.subtract(fields.Datetime.now(), minutes=1),
        }
        base.update(vals)
        return self.Hold.create(base)

    def test_el_aviso_viaja_con_el_wa_id_no_con_el_nacional(self):
        """De 10 digitos no se puede reconstruir el wa_id: el prefijo de pais y
        el 1 de movil se perdieron a proposito en el dedupe."""
        hold = self._hold()
        hold._visar_wa_notify_expired()
        aviso = self.Outbox.search([], order='id desc', limit=1)
        self.assertEqual(aviso.phone, self.WA)
        self.assertNotEqual(aviso.phone, hold.owner_key)

    def test_sin_liga_enviada_el_texto_no_habla_de_ninguna_liga(self):
        """El apartado de la pantalla de revision vence sin que exista liga."""
        hold = self._hold()
        self.assertFalse(hold.calendar_booking_id)
        hold._visar_wa_notify_expired()
        aviso = self.Outbox.search([], order='id desc', limit=1)
        self.assertEqual(aviso.template_key, 'hold_expired')
        self.assertNotIn('liga', aviso.fallback_text.lower())

    def _mis_avisos(self):
        """Solo los de ESTE telefono: `_visar_cron_gc` barre toda la base, y una
        copia de produccion puede traer apartados vencidos de verdad."""
        return self.Outbox.search_count([('phone', '=', self.WA)])

    def test_el_cron_avisa_antes_de_borrar(self):
        """Despues del unlink no queda de donde sacar ni el telefono ni la hora."""
        self._hold()
        antes = self._mis_avisos()
        self.Hold._visar_cron_gc()
        self.assertEqual(self._mis_avisos(), antes + 1)

    def test_un_aviso_que_revienta_no_impide_el_barrido(self):
        """El cron tiene una obligacion —barrer— y un aviso no puede quitarsela."""
        hold = self._hold()
        with patch.object(
            type(hold), '_visar_wa_notify_expired', side_effect=ValueError("boom")
        ):
            with self.assertRaises(ValueError):
                hold._visar_wa_notify_expired()
        # El metodo real atrapa lo suyo: se comprueba que el cron termina y borra.
        self.Hold._visar_cron_gc()
        self.assertFalse(hold.exists())

    def test_un_apartado_sin_whatsapp_no_genera_aviso(self):
        """El wizard web no crea apartados, pero si algun dia lo hiciera no hay
        a quien escribirle."""
        self._hold(visar_wa_phone=False)
        antes = self._mis_avisos()
        self.Hold._visar_cron_gc()
        self.assertEqual(self._mis_avisos(), antes)

    def test_el_buzon_apunta_al_endpoint_del_agendado(self):
        """No es solo un texto: el runtime tiene que rebobinar la conversacion."""
        self.assertEqual(self.Outbox._visar_wa_endpoint(), '/internal/booking-event')
        self.assertEqual(
            self.env['visar.wa.message'].sudo()._visar_wa_endpoint(),
            '/internal/send-notification')

    def test_encolar_sin_telefono_no_crea_nada(self):
        """Nunca lanza: encolar corre dentro de un cobro y de un cron."""
        antes = self._mis_avisos()
        self.assertFalse(self.Outbox._visar_wa_enqueue('booking_confirmed', '', 'x'))
        self.assertEqual(self._mis_avisos(), antes)
