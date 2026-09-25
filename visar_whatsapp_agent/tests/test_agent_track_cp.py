# -*- coding: utf-8 -*-
"""`agent_track_cp` y el CP del escalamiento (25-sep-2026).

Visar quiere decidir a donde crecer con datos: de que codigos postales les
escribe la gente, y sobre todo de cuales les escriben y no pueden atender. Lo
que se fija aqui es el lado del agente de ese reporte, que son dos cosas:

  * el RPC que el runtime llama cuando el modelo consulta cobertura o cotiza —
    y por que lleva TELEFONO, que es lo unico que hace util el dato;
  * que el CP de un escalamiento deje de quedarse dentro de una nota del
    chatter, donde no se puede contar ni agrupar.

Y una tercera que vale mas que las dos: que nada de esto pueda costarle la
respuesta a un cliente.
"""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

CP_CUBIERTO = '64000'
CP_DESCONOCIDO = '06700'


@tagged('post_install', '-at_install')
class TestAgentTrackCp(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tools = cls.env['visar.agent.tools']
        cls.Interes = cls.env['visar.cp.interes']
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.cp.telefonos_internos', '999000')
        ZoneCp = cls.env['visar.zone.cp'].sudo()
        if not ZoneCp._get_cp_record(CP_CUBIERTO):
            zona = cls.env['visar.zone'].search([], limit=1)
            ZoneCp.create({'name': CP_CUBIERTO, 'zone_id': zona.id,
                           'municipality': 'Monterrey'})
        cls.desconocido_en_catalogo = bool(ZoneCp._get_cp_record(CP_DESCONOCIDO))

    def _fila(self, cp):
        return self.Interes.search([('name', '=', cp)], limit=1)

    # ------------------------------------------------------------------
    # El RPC
    # ------------------------------------------------------------------

    def test_registra_el_cp_con_el_telefono_del_chat(self):
        """El telefono lo pone el RUNTIME, no el modelo.

        Es la razon de que esto sea un metodo aparte y no un parametro de
        `agent_resolve_zone`: el handler de esa tool es neutral de canal a
        proposito, y un numero que el modelo pudiera inventar contaminaria la
        unica columna con la que se decide una expansion.
        """
        result = self.Tools.agent_track_cp({
            'phone': '5218114443333', 'cp': CP_CUBIERTO, 'motivo': 'cotizacion'})
        self.assertTrue(result['cp_id'])
        self.assertEqual(result['cp'], CP_CUBIERTO)
        self.assertTrue(result['served'])
        linea = self._fila(CP_CUBIERTO).line_ids
        self.assertEqual(linea.phone, '8114443333')
        self.assertEqual(linea.origen, 'whatsapp')
        self.assertEqual(linea.motivo, 'cotizacion')

    def test_un_cp_a_medias_no_registra_nada(self):
        result = self.Tools.agent_track_cp({'phone': '5218114443333', 'cp': '64'})
        self.assertIsNone(result['cp_id'])
        self.assertIsNone(result['served'])

    def test_un_payload_vacio_no_levanta(self):
        """El runtime puede mandar cualquier cosa; esto no puede reventar."""
        self.assertIsNone(self.Tools.agent_track_cp({})['cp_id'])
        self.assertIsNone(self.Tools.agent_track_cp(None)['cp_id'])

    def test_un_cp_sin_cobertura_queda_marcado_como_tal(self):
        if self.desconocido_en_catalogo:
            self.skipTest("El CP %s dejo de estar fuera del catalogo." % CP_DESCONOCIDO)
        result = self.Tools.agent_track_cp({
            'phone': '5218114443333', 'cp': CP_DESCONOCIDO})
        self.assertFalse(result['served'])
        self.assertFalse(self._fila(CP_DESCONOCIDO).zone_id)

    # ------------------------------------------------------------------
    # El escalamiento
    # ------------------------------------------------------------------

    def test_el_handoff_registra_el_cp_de_su_contexto(self):
        """Hasta hoy el CP se quedaba DENTRO de la nota del chatter.

        La nota sirve para que el asesor retome la conversacion, pero no se puede
        contar ni graficar: un CP fuera de cobertura escrito en un `<p>` no dice
        de donde conviene abrir zona.
        """
        self.Tools.agent_request_handoff({
            'phone': '5218119998888',
            'reason': 'out_of_coverage',
            'context': {'cp': CP_CUBIERTO, 'servicio': 'Fumigacion'},
        })
        linea = self._fila(CP_CUBIERTO).line_ids
        self.assertEqual(linea.phone, '8119998888')
        self.assertEqual(linea.motivo, 'escalamiento')

    def test_un_handoff_sin_cp_no_crea_filas(self):
        antes = self.Interes.search_count([])
        self.Tools.agent_request_handoff({
            'phone': '5218119998888', 'reason': 'complaint',
            'summary': 'Se queja del servicio'})
        self.assertEqual(self.Interes.search_count([]), antes)

    def test_el_telefono_de_la_suite_de_pruebas_no_cuenta_como_mercado(self):
        """Las pruebas de aceptacion escriben con numeros 999000xxxx.

        Son 177 en produccion. Sin la marca de interno, el reporte naceria
        diciendo que hay 177 personas esperando servicio.
        """
        self.Tools.agent_track_cp({'phone': '5219990001234', 'cp': CP_CUBIERTO})
        fila = self._fila(CP_CUBIERTO)
        self.assertTrue(fila.line_ids.interno)
        self.assertEqual(fila.numeros_distintos, 0)

    # ------------------------------------------------------------------
    # Nunca a costa de la respuesta
    # ------------------------------------------------------------------

    def test_un_fallo_al_registrar_no_tumba_el_escalamiento(self):
        """El asesor tiene que quedar convocado aunque el reporte falle."""
        with patch.object(
                type(self.Interes), '_visar_registrar_ahora',
                side_effect=RuntimeError('Postgres dijo no')):
            result = self.Tools.agent_request_handoff({
                'phone': '5218119998888', 'reason': 'out_of_coverage',
                'context': {'cp': CP_CUBIERTO}})
        self.assertTrue(result['lead_id'])
        self.assertIsNone(result['skipped_reason'])

    def test_un_fallo_al_registrar_no_tumba_el_rpc(self):
        with patch.object(
                type(self.Interes), '_visar_registrar_ahora',
                side_effect=RuntimeError('Postgres dijo no')):
            result = self.Tools.agent_track_cp({
                'phone': '5218114443333', 'cp': CP_CUBIERTO})
        self.assertIsNone(result['cp_id'])
        self.assertEqual(result['cp'], CP_CUBIERTO)

    # ------------------------------------------------------------------
    # La consulta de cobertura NO cambio de contrato
    # ------------------------------------------------------------------

    def test_resolve_zone_sigue_siendo_de_solo_lectura(self):
        """Se dejo a proposito sin registrar nada.

        Anadirle el registro obligaria a pasarle un telefono, y ese es el
        parametro que el modelo no debe poder poner. Quien registra es el
        runtime, que es el unico que sabe de quien es el mensaje.
        """
        antes = self.Interes.search_count([])
        self.Tools.agent_resolve_zone(CP_CUBIERTO)
        self.assertEqual(self.Interes.search_count([]), antes)
