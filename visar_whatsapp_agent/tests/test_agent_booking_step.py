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
