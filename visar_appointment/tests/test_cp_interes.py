# -*- coding: utf-8 -*-
"""El reporte de códigos postales (25-sep-2026).

Lo que Visar va a decidir con esta pantalla es **a dónde crecer**, así que lo que
más importa fijar no es que se guarde el dato: es que la columna con la que se
decide no mienta. Tres cosas se pueden torcer y las tres costarían una decisión
de negocio equivocada:

* que una persona indecisa cuente como varias;
* que los números del equipo y de las corridas de prueba cuenten como mercado
  (hoy mismo hay 177 teléfonos de la suite de aceptación en producción);
* que una consulta de cobertura se pague con la latencia de una llamada a
  Mapbox, o peor, que un fallo al registrar le tumbe la respuesta al cliente.

Ninguna prueba toca la red: se parchea el geocodificador, igual que
`test_travel_feasibility.py`.
"""
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

_SERVICE = 'odoo.addons.visar_base.models.visar_travel.VisarMapboxService'

# Un CP que sí está en el catálogo (se siembra desde SEPOMEX con zona).
CP_CUBIERTO = '64000'
# CP de la Ciudad de México: no está en el catálogo de Visar, así que de él solo
# se conocen los cinco dígitos hasta que corra el cron.
CP_DESCONOCIDO = '06700'


@tagged('post_install', '-at_install')
class TestCpInteres(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Interes = cls.env['visar.cp.interes']
        cls.Linea = cls.env['visar.cp.interes.linea']
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.cp.telefonos_internos', '999000')
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.cp.retencion_dias', '730')
        # El catálogo real trae 1,080 CP; la prueba no depende de que uno
        # concreto esté sembrado, así que se asegura el que usa.
        ZoneCp = cls.env['visar.zone.cp'].sudo()
        if not ZoneCp._get_cp_record(CP_CUBIERTO):
            zona = cls.env['visar.zone'].search([], limit=1)
            ZoneCp.create({'name': CP_CUBIERTO, 'zone_id': zona.id,
                           'municipality': 'Monterrey'})
        cls.cp_desconocido_previo = ZoneCp._get_cp_record(CP_DESCONOCIDO)

    def setUp(self):
        super().setUp()
        if self.cp_desconocido_previo:
            self.skipTest(
                "El CP %s dejó de estar fuera del catálogo." % CP_DESCONOCIDO)

    def _fila(self, cp):
        return self.Interes.search([('name', '=', cp)], limit=1)

    # ------------------------------------------------------------------
    # La unidad que se cuenta: una persona, un día, un canal
    # ------------------------------------------------------------------

    def test_la_misma_persona_el_mismo_dia_cuenta_una_vez(self):
        """Tres mensajes de la misma persona son UN interesado, no tres.

        Es la regla que sostiene la decisión: con `consultas` en la columna
        principal, un indeciso que pregunta seis veces se lee como seis
        prospectos y una zona sin mercado parecería tenerlo.
        """
        for _ in range(3):
            self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        fila = self._fila(CP_DESCONOCIDO)
        self.assertEqual(fila.consultas, 1)
        self.assertEqual(fila.numeros_distintos, 1)

    def test_dos_personas_cuentan_dos(self):
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218112222222')
        fila = self._fila(CP_DESCONOCIDO)
        self.assertEqual(fila.numeros_distintos, 2)
        self.assertEqual(fila.consultas, 2)

    def test_el_mismo_numero_en_los_dos_canales_cuenta_dos_consultas(self):
        """Un número distinto por canal: son dos hechos distintos.

        La misma persona que pregunta por WhatsApp y luego entra a la web es UN
        interesado (`numeros_distintos` = 1), pero las dos consultas se guardan
        porque la mezcla de canales es parte de lo que se quiere ver.
        """
        self.Interes._visar_registrar(
            CP_DESCONOCIDO, phone='5218111111111', origen='whatsapp')
        self.Interes._visar_registrar(
            CP_DESCONOCIDO, phone='5218111111111', origen='web')
        fila = self._fila(CP_DESCONOCIDO)
        self.assertEqual(fila.consultas, 2)
        self.assertEqual(fila.numeros_distintos, 1)

    def test_una_reserva_web_sin_telefono_no_inventa_interesados(self):
        """El paso de la dirección del web no pide teléfono.

        Sin esto, cada visita anónima sumaría un "número distinto" y el reporte
        diría que hay gente que no se puede identificar ni contactar.
        """
        for _ in range(3):
            self.Interes._visar_registrar(CP_DESCONOCIDO, origen='web')
        fila = self._fila(CP_DESCONOCIDO)
        self.assertEqual(fila.numeros_distintos, 0)
        # Las tres se colapsan en un renglón: el teléfono se guarda vacío (no
        # NULL) justamente para que la restricción de unicidad las dedupee.
        self.assertEqual(fila.consultas, 1)

    def test_un_cp_a_medias_no_se_registra(self):
        """Tres dígitos es el cliente escribiendo todavía, no un dato."""
        self.Interes._visar_registrar('067', phone='5218111111111')
        self.assertFalse(self._fila('067'))
        self.assertFalse(self.Interes.search([('name', '=', '')]))

    def test_la_restriccion_del_dia_vive_en_la_base(self):
        """No basta con buscar antes de crear: eso es código, y dos mensajes casi
        simultáneos pueden pasar los dos por ahí.

        Vale la pena afirmarlo aparte porque Odoo 19 **ignora**
        `_sql_constraints` (solo avisa en el log): escrita a la antigua, la
        restricción no existiría en la base y nada lo diría.
        """
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        fila = self._fila(CP_DESCONOCIDO)
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            self.Linea.create({
                'interes_id': fila.id, 'phone': '8111111111',
                'origen': 'whatsapp', 'fecha': fields.Date.today()})
            self.env.flush_all()

    # ------------------------------------------------------------------
    # Cobertura
    # ------------------------------------------------------------------

    def test_un_cp_del_catalogo_queda_con_su_zona_y_sin_geocodificar(self):
        self.Interes._visar_registrar(CP_CUBIERTO, phone='5218111111111')
        fila = self._fila(CP_CUBIERTO)
        self.assertTrue(fila.zone_id)
        self.assertTrue(fila.served)
        self.assertEqual(fila.geo_status, 'catalogo')

    def test_un_cp_desconocido_queda_sin_cobertura_y_pendiente(self):
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        fila = self._fila(CP_DESCONOCIDO)
        self.assertFalse(fila.zone_id)
        self.assertFalse(fila.served)
        self.assertEqual(fila.geo_status, 'pendiente')
        # Municipio vacío a propósito: de un CP que no está en el catálogo NO se
        # sabe el municipio, y ponerle uno sería inventarlo.
        self.assertFalse(fila.municipality)

    def test_si_el_cp_entra_al_catalogo_la_fila_deja_de_estar_sin_cobertura(self):
        """Eso es exactamente una expansión, y no puede requerir tocar a mano.

        Si la fila se quedara marcada como fuera de cobertura después de abrir la
        zona, la pantalla seguiría pidiendo expandirse a donde ya se atiende.
        """
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        self.assertFalse(self._fila(CP_DESCONOCIDO).served)
        zona = self.env['visar.zone'].search([], limit=1)
        self.env['visar.zone.cp'].sudo().create({
            'name': CP_DESCONOCIDO, 'zone_id': zona.id, 'municipality': 'Monterrey'})
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218112222222')
        fila = self._fila(CP_DESCONOCIDO)
        self.assertTrue(fila.served)
        self.assertEqual(fila.zone_id, zona)
        self.assertEqual(fila.geo_status, 'catalogo')

    # ------------------------------------------------------------------
    # Hasta dónde llegó (forward-only, como las etapas del pipeline)
    # ------------------------------------------------------------------

    def test_el_motivo_avanza_y_no_retrocede(self):
        """Cotizar y luego volver a preguntar cobertura no borra la cotización."""
        self.Interes._visar_registrar(
            CP_CUBIERTO, phone='5218111111111', motivo='cobertura')
        self.Interes._visar_registrar(
            CP_CUBIERTO, phone='5218111111111', motivo='cotizacion')
        self.Interes._visar_registrar(
            CP_CUBIERTO, phone='5218111111111', motivo='cobertura')
        linea = self._fila(CP_CUBIERTO).line_ids
        self.assertEqual(len(linea), 1)
        self.assertEqual(linea.motivo, 'cotizacion')

    def test_llegar_a_la_direccion_gana_a_cotizar(self):
        self.Interes._visar_registrar(
            CP_CUBIERTO, phone='5218111111111', motivo='cotizacion')
        self.Interes._visar_registrar(
            CP_CUBIERTO, phone='5218111111111', motivo='agendado')
        self.assertEqual(self._fila(CP_CUBIERTO).line_ids.motivo, 'agendado')

    # ------------------------------------------------------------------
    # Números internos
    # ------------------------------------------------------------------

    def test_un_telefono_de_la_suite_de_pruebas_es_interno_y_no_cuenta(self):
        """Los 177 números de la suite empiezan con 999000 y no son de nadie."""
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5219990000123')
        fila = self._fila(CP_DESCONOCIDO)
        self.assertTrue(fila.line_ids.interno)
        self.assertEqual(fila.numeros_distintos, 0)
        self.assertEqual(fila.consultas, 0)

    def test_un_empleado_es_interno_y_no_cuenta(self):
        """Un técnico probando el agente no es un prospecto de esa zona."""
        self.env['hr.employee'].sudo().create({
            'name': 'Técnico que prueba', 'mobile_phone': '+52 811 777 7777'})
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218117777777')
        fila = self._fila(CP_DESCONOCIDO)
        self.assertTrue(fila.line_ids.interno)
        self.assertEqual(fila.numeros_distintos, 0)

    def test_un_cliente_normal_no_es_interno(self):
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218113334444')
        self.assertFalse(self._fila(CP_DESCONOCIDO).line_ids.interno)

    def test_los_prefijos_internos_se_ajustan_sin_desplegar(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'visar.cp.telefonos_internos', '999000,8115550')
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218115550000')
        self.assertTrue(self._fila(CP_DESCONOCIDO).line_ids.interno)

    # ------------------------------------------------------------------
    # Geocodificación: en el cron, nunca en la conversación
    # ------------------------------------------------------------------

    def test_registrar_no_llama_a_mapbox(self):
        """Es la regla que protege el tiempo de respuesta del cliente.

        Geocodificar al registrar le sumaría la latencia de una llamada remota a
        cada consulta de cobertura, que es justo lo que el §5.3 del diseño 33
        pasó tres revisiones evitando.
        """
        with patch('%s._visar_mapbox_geocode_feature' % _SERVICE) as remoto:
            self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        remoto.assert_not_called()

    def test_el_cron_pone_municipio_y_estado(self):
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        lugar = {'lat': 19.41, 'lng': -99.16, 'text': CP_DESCONOCIDO,
                 'municipality': 'Ciudad de México', 'state': ''}
        with patch('%s._visar_mapbox_geocode_lugar' % _SERVICE, return_value=lugar):
            self.Interes._visar_cron_geocode()
        fila = self._fila(CP_DESCONOCIDO)
        self.assertEqual(fila.municipality, 'Ciudad de México')
        # La Ciudad de México no trae `region` en la respuesta de Mapbox: se
        # queda vacío en vez de inventarle un estado.
        self.assertFalse(fila.state_name)
        self.assertEqual(fila.geo_status, 'resuelto')
        self.assertAlmostEqual(fila.lat, 19.41, places=4)

    def test_si_mapbox_contesta_otro_cp_no_se_guarda_su_municipio(self):
        """Un municipio equivocado es PEOR que ninguno.

        Preguntando "CP 06700, México" en prosa, Mapbox contestó una calle de
        **Mérida**: el reporte habría dicho que hay demanda en Yucatán y Visar
        habría mirado a abrir zona en la ciudad de otro. Se pide por código y se
        comprueba que la respuesta sea ese código.
        """
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        otro = {'lat': 20.98, 'lng': -89.62, 'text': '97000',
                'municipality': 'Mérida', 'state': 'Yucatán'}
        with patch('%s._visar_mapbox_geocode_lugar' % _SERVICE, return_value=otro):
            self.Interes._visar_cron_geocode()
        fila = self._fila(CP_DESCONOCIDO)
        self.assertEqual(fila.geo_status, 'sin_datos')
        self.assertFalse(fila.municipality)
        self.assertFalse(fila.state_name)

    def test_mapbox_sin_respuesta_deja_sin_datos_y_no_inventa(self):
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        with patch('%s._visar_mapbox_geocode_lugar' % _SERVICE, return_value=None):
            self.Interes._visar_cron_geocode()
        fila = self._fila(CP_DESCONOCIDO)
        self.assertEqual(fila.geo_status, 'sin_datos')
        self.assertFalse(fila.municipality)
        self.assertFalse(fila.state_name)

    def test_mapbox_caido_no_tumba_la_corrida(self):
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        with patch('%s._visar_mapbox_geocode_lugar' % _SERVICE,
                   side_effect=RuntimeError('Mapbox caido')):
            self.Interes._visar_cron_geocode()
        self.assertEqual(self._fila(CP_DESCONOCIDO).geo_status, 'sin_datos')

    def test_el_cron_no_vuelve_a_pagar_un_cp_del_catalogo(self):
        """Un CP con zona ya trae municipio: pedirlo a Mapbox sería pagarlo dos veces."""
        self.Interes._visar_registrar(CP_CUBIERTO, phone='5218111111111')
        with patch('%s._visar_mapbox_geocode_lugar' % _SERVICE) as remoto:
            self.Interes._visar_cron_geocode()
        remoto.assert_not_called()

    # ------------------------------------------------------------------
    # Nunca a costa de la respuesta al cliente
    # ------------------------------------------------------------------

    def test_un_fallo_al_registrar_no_levanta(self):
        """El reporte vale menos que la contestación que el cliente espera."""
        with patch.object(
                type(self.Interes), '_visar_registrar_ahora',
                side_effect=RuntimeError('Postgres dijo no')):
            fila = self.Interes._visar_registrar(
                CP_DESCONOCIDO, phone='5218111111111')
        self.assertFalse(fila)

    def test_tras_un_fallo_de_postgres_la_transaccion_sigue_usable(self):
        """El `savepoint` es la mitad que importa.

        Sin él una consulta fallida deja el cursor abortado y **cualquier**
        consulta posterior levanta: el `try/except` no salvaría la respuesta, solo
        cambiaría de sitio la explosión.

        El fallo tiene que ser **de Postgres**, no una excepción de Python: con un
        `side_effect` esta prueba pasaba igual quitando el savepoint, o sea que
        estaba en verde por la razón equivocada (comprobado mutando el código el
        25-sep-2026). Aquí se desactiva la búsqueda previa para que el segundo
        registro del mismo día choque de verdad con la restricción de unicidad.
        """
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        with patch.object(type(self.Linea), 'search',
                          return_value=self.Linea.browse()), \
                mute_logger('odoo.sql_db'):
            self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        # Si el cursor hubiera quedado abortado, esto levantaría.
        self.assertTrue(self.env['visar.zone'].search([], limit=1))
        self.assertEqual(self._fila(CP_DESCONOCIDO).consultas, 1)

    # ------------------------------------------------------------------
    # Retención
    # ------------------------------------------------------------------

    def test_la_retencion_borra_lo_viejo_y_cuadra_los_contadores(self):
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        fila = self._fila(CP_DESCONOCIDO)
        vieja = self.Linea.create({
            'interes_id': fila.id, 'phone': '8112222222', 'origen': 'whatsapp',
            'fecha': fields.Date.subtract(fields.Date.today(), days=900)})
        self.assertEqual(fila.numeros_distintos, 2)
        borradas = self.Interes._visar_cron_retencion()
        self.assertEqual(borradas, 1)
        self.assertFalse(vieja.exists())
        self.assertEqual(fila.numeros_distintos, 1)

    def test_la_ventana_de_retencion_se_ajusta_sin_desplegar(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'visar.cp.retencion_dias', '30')
        self.Interes._visar_registrar(CP_DESCONOCIDO, phone='5218111111111')
        fila = self._fila(CP_DESCONOCIDO)
        self.Linea.create({
            'interes_id': fila.id, 'phone': '8112222222', 'origen': 'whatsapp',
            'fecha': fields.Date.subtract(fields.Date.today(), days=45)})
        self.assertEqual(self.Interes._visar_cron_retencion(), 1)
        self.assertEqual(fila.numeros_distintos, 1)


@tagged('post_install', '-at_install')
class TestCpInteresEnElCuestionario(TransactionCase):
    """El paso de la dirección registra el CP en los DOS canales.

    El enganche va en `_visar_wizard_resolve_address` y no en
    `_visar_wizard_answer_address` porque el controlador del sitio web llama al
    primero por su cuenta y nunca entra al segundo. Estas pruebas son lo que
    impide que alguien "suba" el enganche y deje fuera, sin enterarse, todas las
    reservas web — que son justamente las que dicen dónde se presta servicio.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Flow = cls.env['appointment.type'].sudo()
        cls.Interes = cls.env['visar.cp.interes']
        ZoneCp = cls.env['visar.zone.cp'].sudo()
        if not ZoneCp._get_cp_record(CP_CUBIERTO):
            zona = cls.env['visar.zone'].search([], limit=1)
            ZoneCp.create({'name': CP_CUBIERTO, 'zone_id': zona.id,
                           'municipality': 'Monterrey'})

    def _direccion(self, cp):
        return {'street': 'Calle Prueba', 'ext_num': '100',
                'neighborhood': 'Centro', 'zip': cp}

    def _fila(self, cp):
        return self.Interes.search([('name', '=', cp)], limit=1)

    def test_el_web_registra_el_cp_como_origen_web(self):
        """Sin contexto se asume web: es de donde vienen los llamadores que no lo ponen."""
        zone, _address, error = self.Flow._visar_wizard_resolve_address(
            self._direccion(CP_CUBIERTO))
        self.assertFalse(error)
        self.assertTrue(zone)
        linea = self._fila(CP_CUBIERTO).line_ids
        self.assertEqual(linea.origen, 'web')
        self.assertEqual(linea.motivo, 'agendado')
        self.assertFalse(linea.phone)

    def test_el_agente_registra_el_cp_con_telefono_y_canal(self):
        """El contexto lo pone `agent_booking_step` con el teléfono del chat."""
        self.Flow.with_context(
            visar_cp_phone='5218116665555', visar_cp_origen='whatsapp',
        )._visar_wizard_resolve_address(self._direccion(CP_CUBIERTO))
        linea = self._fila(CP_CUBIERTO).line_ids
        self.assertEqual(linea.origen, 'whatsapp')
        self.assertEqual(linea.phone, '8116665555')

    def test_un_cp_sin_cobertura_en_la_direccion_tambien_se_registra(self):
        """Es el dato más caro del reporte: no preguntó, LLEGÓ HASTA LA DIRECCIÓN."""
        if self.env['visar.zone.cp'].sudo()._get_cp_record(CP_DESCONOCIDO):
            self.skipTest("El CP %s dejó de estar fuera del catálogo." % CP_DESCONOCIDO)
        zone, _address, error = self.Flow._visar_wizard_resolve_address(
            self._direccion(CP_DESCONOCIDO))
        self.assertFalse(zone)
        self.assertTrue(error)
        fila = self._fila(CP_DESCONOCIDO)
        self.assertTrue(fila)
        self.assertFalse(fila.served)
        self.assertEqual(fila.line_ids.motivo, 'cobertura')

    def test_una_direccion_incompleta_no_registra_nada(self):
        """Falta la calle: el cliente está escribiendo, no hay dato que guardar."""
        antes = self.Interes.search_count([])
        _zone, _address, error = self.Flow._visar_wizard_resolve_address(
            {'zip': CP_CUBIERTO, 'ext_num': '100', 'neighborhood': 'Centro'})
        self.assertTrue(error)
        self.assertEqual(self.Interes.search_count([]), antes)
