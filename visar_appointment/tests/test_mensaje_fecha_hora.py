# -*- coding: utf-8 -*-
"""La pantalla "Elegir fecha y hora" tiene su propio texto (25-sep-2026).

Odoo nativo pintaba `message_intro` dos veces: en la página de introducción y otra
vez, bajo "Descripción", en la de fecha y hora. Al cliente le llegaba el mismo
mensaje en dos pasos distintos y dejaba de servir para lo que estaba —ayudarle a
elegir el servicio en el primero—.

Lo que se protege aquí: que cada pantalla use SU texto, y que el de fecha y hora
vacío no pinte nada (sin caer de vuelta en el de introducción, que es justo la
duplicación que se quitó).
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestMensajeFechaHora(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tipo = cls.env['appointment.type'].create({
            'name': "Cita de prueba",
            'message_intro': "<p>Texto de la introducción</p>",
        })

    def _arch_fecha_hora(self):
        """Arch combinado de la pantalla de fecha y hora, con las herencias ya
        aplicadas (es lo que de verdad se renderiza)."""
        vista = self.env.ref('appointment.appointment_info')
        return self.env['ir.ui.view'].browse(vista.id).get_combined_arch()

    def test_el_campo_es_independiente_del_de_introduccion(self):
        self.tipo.visar_message_datetime = "<p>Elige el horario que te acomode</p>"

        self.assertEqual(self.tipo.message_intro, "<p>Texto de la introducción</p>",
                         "escribir el de fecha y hora no toca el de introducción")

    def test_la_pantalla_de_fecha_y_hora_usa_su_propio_texto(self):
        arch = self._arch_fecha_hora()

        self.assertIn('appointment_type.visar_message_datetime', arch)
        self.assertNotIn('appointment_type.message_intro', arch,
                         "el de introducción ya no se pinta en esta pantalla")

    def test_vacio_no_pinta_bloque(self):
        """La condición de ocultado mira el campo NUEVO: sin texto propio, el bloque
        entero (borde incluido) se va, aunque haya introducción escrita."""
        arch = self._arch_fecha_hora()

        self.assertIn('is_html_empty(appointment_type.visar_message_datetime)', arch)
        self.assertNotIn('is_html_empty(appointment_type.message_intro)', arch)

    def test_la_pagina_de_introduccion_conserva_el_suyo(self):
        lista = self.env.ref('appointment.appointments_list_layout')
        arch = self.env['ir.ui.view'].browse(lista.id).get_combined_arch()

        self.assertIn('message_intro', arch,
                      "la introducción sigue mostrando su texto; eso no se tocó")

    def test_el_campo_admite_el_mismo_contenido_que_el_de_introduccion(self):
        """Mismo saneado: un texto que se pega en uno tiene que sobrevivir en el
        otro (los dos se editan con el mismo editor del sitio web)."""
        campo = self.env['appointment.type']._fields['visar_message_datetime']

        self.assertFalse(campo.sanitize_attributes)
        self.assertTrue(campo.translate)
