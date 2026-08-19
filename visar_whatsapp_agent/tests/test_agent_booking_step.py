# -*- coding: utf-8 -*-
"""`agent_booking_step`: el runtime conduce el cuestionario sin saber sus reglas.

Lo que se fija aqui es que el RPC **no decide nada por su cuenta**. Todo lo que
devuelve -que paso sigue, que opciones son validas, como quedo `selections`-
sale de los mismos metodos de modelo que usa el wizard web
(`visar_appointment/models/appointment_wizard_flow.py`).

Si alguna de estas pruebas empieza a fallar porque el agente "necesita" su
propia regla, la respuesta correcta es bajar la regla al modelo, no duplicarla
aqui: es el riesgo de "dos front-ends" del diseno 33 §11, que ya se cobro una
vez (I-11, el web cobrando 2,400 donde la cotizacion decia 1,900).
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentBookingStep(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools'].sudo()
        cls.AptType = cls.env['appointment.type'].sudo()
        Group = cls.env['visar.service.group'].sudo()
        cls.fum_group = Group.search([('code', '=', 'fumigacion')], limit=1)
        if not cls.fum_group:
            cls.fum_group = Group.create({
                'name': 'Fumigacion Test', 'code': 'fumigacion',
                'show_in_wizard': True,
            })

    def _booking_fum(self, **selections):
        base = {'group_ids': [self.fum_group.id]}
        base.update(selections)
        return {'mode': 'wizard', 'selections': base}

    def test_ask_vuelve_a_preguntar_sin_aplicar_nada(self):
        """Corregir empieza por volver a PREGUNTAR. La poda corre al contestar."""
        booking = self._booking_fum(motivo='correctivo',
                                    servicio_plaga=['rastreros'])
        state = self.Tools.agent_booking_step({'booking': booking,
                                               'ask': 'motivo'})
        self.assertEqual(state['step'], 'motivo')
        self.assertIsNone(state['error'])
        self.assertEqual(state['selections'].get('servicio_plaga'), ['rastreros'],
                         "preguntar no borra nada")

    def test_ask_solo_admite_pasos_de_la_secuencia(self):
        """Si el runtime pudiera pedir cualquier paso, estaria inventando
        secuencia — que es justo lo que este RPC existe para impedir."""
        booking = self._booking_fum(motivo='correctivo')
        state = self.Tools.agent_booking_step({'booking': booking,
                                               'ask': 'paso_que_no_existe'})
        self.assertNotEqual(state['step'], 'paso_que_no_existe')

    def test_el_estado_publica_los_pasos_editables(self):
        state = self.Tools.agent_booking_step({
            'booking': self._booking_fum(motivo='correctivo')})
        self.assertTrue(state['steps'])
        self.assertEqual([p['key'] for p in state['steps']], state['sequence'])
        self.assertTrue(state['schedule_key'])

    def test_un_telefono_desconocido_pide_nombre(self):
        """La bandera se calcula en CADA llamada, no viaja en el estado.

        Es un hecho del mundo -¿existe ya este cliente?- que puede cambiar entre
        dos mensajes: si alguien lo da de alta en Odoo a media conversacion, el
        paso tiene que dejar de aparecer solo.
        """
        Partner = self.env['res.partner'].sudo()
        nuevo = '5219998887766'
        self.assertTrue(self.Tools._agent_booking_needs_name(nuevo))

        Partner.create({'name': 'Cliente Nuevo', 'phone': nuevo})
        self.assertFalse(self.Tools._agent_booking_needs_name(nuevo),
                         "una vez dado de alta, deja de preguntarse")

    def test_sin_telefono_no_se_pregunta_el_nombre(self):
        """Sin clave no se puede afirmar que sea alguien nuevo, y el paso seria
        una pregunta gratis. `agent_prepare_booking` ya rechaza el numero malo."""
        self.assertFalse(self.Tools._agent_booking_needs_name(None))
        self.assertFalse(self.Tools._agent_booking_needs_name('123'))

    def test_el_paso_del_nombre_sobrevive_a_la_direccion(self):
        """REGRESION: la direccion REHACE el booking y se llevaba la bandera.

        `_visar_wizard_answer_address` no muta el estado: devuelve un dict nuevo
        con zona, items y direccion resueltos. Cualquier clave que no este en ese
        contrato se pierde ahi — y el paso del nombre va justo DESPUES de la
        direccion, asi que desaparecia exactamente donde tenia que aparecer.

        El Odoo falso del runtime conservaba la clave, asi que sus pruebas pasaban
        en verde. Se encontro recorriendo el cuestionario contra la base.
        """
        nuevo = '5219998887744'
        state = self.Tools.agent_booking_step({
            'booking': {'mode': 'wizard', 'selections': {},
                        'zone_id': 1, 'items': [{'dimension_id': 1}]},
            'step': 'nombre',
            'answer': {'nombre': 'Maria Lopez'},
            'phone': nuevo,
        })
        self.assertIsNone(state['error'])
        self.assertEqual(state['selections'].get('nombre'), 'Maria Lopez')
        self.assertNotEqual(state['step'], 'nombre',
                            "contestado, no se vuelve a preguntar")

    def test_el_nombre_llega_a_la_reserva_como_una_respuesta_mas(self):
        """El runtime no tiene que saber que la clave se llama `nombre`.

        Lo manda dentro de `selections`, igual que el resto del cuestionario, y
        `agent_prepare_booking` lo recoge de ahi. Sin esto un cliente nuevo
        contestaba todo y al final recibia "Falta el nombre del cliente."
        """
        result = self.Tools.agent_prepare_booking({
            'phone': '5219998887755',
            'selections': {'nombre': 'Maria Lopez'},
        })
        # Falla mas adelante (sin cobertura ni servicio), pero YA NO por el nombre.
        self.assertNotEqual(result.get('reason'), 'name_required')

    def test_el_paso_que_devuelve_es_el_del_modelo(self):
        """El RPC no secuencia: pregunta."""
        state = self.Tools.agent_booking_step({
            'booking': {'mode': 'wizard', 'selections': {}},
            'step': 'services',
            'answer': {'group_ids': [self.fum_group.id]},
        })
        self.assertIsNone(state['error'])
        self.assertEqual(state['selections']['group_ids'], [self.fum_group.id])
        expected = self.AptType._visar_wizard_next_step(
            {'mode': 'wizard', 'selections': state['selections']})
        self.assertEqual(state['step'], expected)

    def test_devuelve_las_opciones_del_paso_nuevo(self):
        """El runtime nunca inventa opciones: las recibe ya resueltas."""
        state = self.Tools.agent_booking_step({
            'booking': self._booking_fum(motivo='correctivo'),
            'step': 'plagas',
            'answer': {'servicio_plaga': ['rastreros']},
        })
        self.assertIsNone(state['error'])
        self.assertEqual(state['step'], 'cobertura')
        self.assertEqual(state['options']['kind'], 'single')
        self.assertEqual([o['value'] for o in state['options']['options']],
                         ['interior', 'exterior', 'ambos'])

    def test_sin_paso_solo_consulta_el_estado(self):
        """Retomar una conversacion estacionada no toca el estado.

        El runtime pierde el hilo (reinicio, cliente que vuelve horas despues) y
        necesita preguntar "¿en que iba?" sin aplicar nada.
        """
        booking = self._booking_fum(motivo='correctivo')
        state = self.Tools.agent_booking_step({'booking': booking})
        self.assertIsNone(state['error'])
        self.assertEqual(state['step'], 'plagas')
        self.assertEqual(state['selections'], booking['selections'])

    def test_un_error_no_mueve_el_paso(self):
        """Respuesta invalida: se vuelve a preguntar LO MISMO, con el motivo."""
        state = self.Tools.agent_booking_step({
            'booking': self._booking_fum(),
            'step': 'motivo',
            'answer': {'motivo': 'ninguno'},
        })
        self.assertIsNotNone(state['error'])
        self.assertEqual(state['step'], 'motivo')
        self.assertEqual(state['error']['code'], 'bad_motivo')
        self.assertTrue(state['error']['message'])

    def test_nunca_lanza_con_payload_basura(self):
        """El agente tiene que poder contestarle al cliente, no recibir un traceback."""
        for payload in ({}, {'booking': None}, {'booking': {}, 'step': 'no_existe'},
                        {'booking': {'selections': None}, 'step': 'motivo',
                         'answer': None}):
            state = self.Tools.agent_booking_step(payload)
            self.assertIn('step', state)
            self.assertIn('options', state)

    def test_el_corte_a_valoracion_viaja_en_la_respuesta(self):
        """`requires_valuation` le dice al runtime que cambie de rama.

        Sin esto tendria que deducirlo mirando `selections`, que es justo la
        regla que no debe conocer.
        """
        state = self.Tools.agent_booking_step({
            'booking': self._booking_fum(motivo='correctivo'),
            'step': 'plagas',
            'answer': {'servicio_plaga': ['termitas']},
        })
        self.assertIsNone(state['error'])
        self.assertTrue(state['requires_valuation'])
        self.assertEqual(state['step'], 'valuation')

    def test_el_estado_devuelto_alimenta_la_llamada_siguiente(self):
        """Round-trip: lo que sale de una llamada entra en la siguiente.

        Es el contrato con el runtime — y `selections` viaja tal cual hasta
        `agent_prepare_booking`, que resuelve los items con
        `_visar_resolve_wizard_items`. El runtime nunca arma `items` (diseno 33
        §7.1: emparejar mal un tramo cobra un tercio del precio SIN error).
        """
        state = self.Tools.agent_booking_step({
            'booking': {'mode': 'wizard', 'selections': {}},
            'step': 'services',
            'answer': {'group_ids': [self.fum_group.id]},
        })
        booking = {'mode': 'wizard', 'selections': state['selections']}
        state2 = self.Tools.agent_booking_step({
            'booking': booking,
            'step': state['step'],
            'answer': {'motivo': 'preventivo'},
        })
        self.assertIsNone(state2['error'])
        self.assertEqual(state2['selections']['motivo'], 'preventivo')
        self.assertEqual(state2['selections']['group_ids'], [self.fum_group.id])
        self.assertNotIn('items', state2['selections'],
                         "Los items NUNCA los arma el runtime")
