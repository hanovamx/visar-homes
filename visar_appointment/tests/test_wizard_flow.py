# -*- coding: utf-8 -*-
"""El flujo del wizard, ahora que vive en el modelo y no en el controlador.

Lo que se fija aquí es justamente lo que se rompe si alguien decide "publicar la
tabla" en vez de la operación, o reimplementar el flujo del lado del runtime:

  * la poda incluye una regla de PREFIJO (`tier_*`) que NO está en
    `_VISAR_STEP_CLEARS`. Es la razón por la que se expone la operación;
  * los cortes a valoración dependen del MOTIVO: "termitas" corta en correctivo
    y no corta en preventivo. Es la regla que se perdería si el runtime armara
    `selections` por su cuenta;
  * "protección general" activa las tres categorías;
  * la cadena posterior a la dirección (extras → póliza → horario) no rebota al
    paso que se acaba de contestar;
  * el controlador web y el agente recorren EXACTAMENTE el mismo camino.

Las pruebas que necesitan catálogo real (tramos, productos, zonas) se saltan si
la base no lo trae, en vez de dar un falso verde. Es la misma disciplina de
`visar_whatsapp_agent/tests/test_agent_prepare_booking.py`.
"""
from unittest.mock import patch

from odoo.addons.visar_appointment.models.appointment_wizard_flow import (
    VISAR_POLIZA_NONE,
    VISAR_STEP_NAME,
)
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestWizardFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AptType = cls.env['appointment.type'].sudo()
        Group = cls.env['visar.service.group'].sudo()
        Dimension = cls.env['visar.service.dimension'].sudo()

        # Fumigación se busca POR CÓDIGO (`code == 'fumigacion'`), así que la
        # prueba usa el grupo real si existe; si no, lo crea.
        cls.fum_group = Group.search([('code', '=', 'fumigacion')], limit=1)
        if not cls.fum_group:
            cls.fum_group = Group.create({
                'name': 'Fumigacion Test', 'code': 'fumigacion',
                'show_in_wizard': True,
            })
        if not cls.fum_group.dimension_ids.filtered(
                lambda d: d.active and d.measure_type == 'interior'):
            Dimension.create({
                'group_id': cls.fum_group.id, 'name': 'Interior Test',
                'code': 'test_fum_int', 'measure_type': 'interior',
            })
        if not cls.fum_group.dimension_ids.filtered(
                lambda d: d.active and d.measure_type == 'exterior'):
            Dimension.create({
                'group_id': cls.fum_group.id, 'name': 'Exterior Test',
                'code': 'test_fum_ext', 'measure_type': 'exterior',
            })

    # ------------------------------------------------------------------
    # Poda (la regla que no está en la tabla)
    # ------------------------------------------------------------------

    def test_poda_borra_los_tramos_por_prefijo(self):
        """`tier_*` se limpia por PREFIJO, y eso no está en `_VISAR_STEP_CLEARS`.

        Es el motivo por el que lo que se expone hacia el runtime es esta
        operación y no el diccionario: publicando la tabla, el otro lado tendría
        que reimplementar esta regla, que es exactamente la divergencia que el
        cambio viene a cerrar.
        """
        selections = {
            'group_ids': [self.fum_group.id],
            'cobertura': 'ambos',
            'tier_1': 7, 'tier_2': 9,
            'motivo': 'preventivo',
        }
        pruned = self.AptType._visar_wizard_clear_downstream(selections, 'cobertura')
        self.assertNotIn('tier_1', pruned, "Cambiar cobertura debe soltar los tramos")
        self.assertNotIn('tier_2', pruned)
        # Lo que NO depende de cobertura sobrevive.
        self.assertEqual(pruned.get('motivo'), 'preventivo')
        self.assertEqual(pruned.get('group_ids'), [self.fum_group.id])

    def test_poda_respeta_lo_independiente(self):
        """Cambiar interior no invalida exterior: son mediciones independientes."""
        selections = {
            'group_ids': [self.fum_group.id],
            'interior_niveles': '2',
            'exterior_band_id': 4,
            'exterior_rodea': 'si',
        }
        pruned = self.AptType._visar_wizard_clear_downstream(selections, 'interior')
        self.assertNotIn('interior_niveles', pruned)
        self.assertEqual(pruned.get('exterior_band_id'), 4,
                         "Exterior no depende de interior")
        self.assertEqual(pruned.get('exterior_rodea'), 'si')

    def test_poda_de_un_subpaso_de_grupo(self):
        """`group_<id>` se normaliza a 'group' antes de buscar en la tabla."""
        selections = {'group_ids': [self.fum_group.id], 'tier_3': 5,
                      'poliza_plan_id': 8}
        pruned = self.AptType._visar_wizard_clear_downstream(
            selections, 'group_%s' % self.fum_group.id)
        self.assertNotIn('tier_3', pruned)
        self.assertNotIn('poliza_plan_id', pruned,
                         "Cambiar los servicios recotiza la póliza")

    def test_commit_no_muta_el_booking_de_entrada(self):
        """`_visar_wizard_commit` devuelve estado nuevo; no escribe en el que recibe.

        Importa porque el runtime guarda el estado anterior mientras espera la
        respuesta: si se mutara, un paso fallido dejaría el estado a medias.
        """
        booking = {'mode': 'wizard', 'selections': {'group_ids': [1], 'tier_1': 3}}
        original = dict(booking['selections'])
        result = self.AptType._visar_wizard_commit(
            booking, 'cobertura', {'cobertura': 'interior'})
        self.assertEqual(booking['selections'], original,
                         "El booking de entrada no se toca")
        self.assertEqual(result['selections'].get('cobertura'), 'interior')
        self.assertNotIn('tier_1', result['selections'])

    # ------------------------------------------------------------------
    # Normalización de respuestas
    # ------------------------------------------------------------------

    def _booking_fum(self, **selections):
        base = {'group_ids': [self.fum_group.id]}
        base.update(selections)
        return {'mode': 'wizard', 'selections': base}

    def test_proteccion_general_activa_las_tres_categorias(self):
        booking, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(motivo='preventivo'), 'plagas',
            {'servicio_plaga': ['proteccion_general']})
        self.assertIsNone(error)
        self.assertEqual(sorted(booking['selections']['servicio_plaga']),
                         ['rastreros', 'roedores', 'voladores'])
        self.assertEqual(booking['selections']['roedores'], 'si')
        self.assertFalse(booking['selections'].get('requiere_valoracion'))

    def test_termitas_corta_a_valoracion_solo_en_correctivo(self):
        """El corte depende del MOTIVO, y esa es la regla fácil de perder.

        En correctivo el cliente reporta una plaga que hay que ir a ver; en
        preventivo está contratando protección y no hay nada que valorar.
        """
        correctivo, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(motivo='correctivo'), 'plagas',
            {'servicio_plaga': ['termitas']})
        self.assertIsNone(error)
        self.assertTrue(correctivo['selections']['requiere_valoracion'])
        self.assertEqual(correctivo['selections']['motivo_valoracion'], 'termitas')

        # Misma respuesta, rama preventiva: no corta — y como no queda ninguna
        # categoría, es un error de captura, no una valoración.
        preventivo, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(motivo='preventivo'), 'plagas',
            {'servicio_plaga': ['termitas']})
        self.assertIsNotNone(error, "Sin categoría ni corte, hay que volver a preguntar")
        self.assertEqual(error['code'], 'no_plaga')
        self.assertFalse(preventivo['selections'].get('requiere_valoracion'))

    def test_plagas_sin_seleccion_no_avanza(self):
        booking, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(motivo='correctivo'), 'plagas', {'servicio_plaga': []})
        self.assertIsNotNone(error)
        self.assertEqual(error['code'], 'no_plaga')
        self.assertNotIn('servicio_plaga', booking['selections'])

    def test_motivo_invalido_se_rechaza_sin_lanzar(self):
        booking, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(), 'motivo', {'motivo': 'lo que sea'})
        self.assertIsNotNone(error)
        self.assertEqual(error['code'], 'bad_motivo')
        self.assertNotIn('motivo', booking['selections'])

    def test_services_ignora_grupos_no_ofrecidos(self):
        """No se confía en lo que llega: solo grupos realmente ofrecidos.

        El runtime manda lo que tocó el cliente, pero un callback viejo (o mal
        armado) no puede meter un grupo que el wizard no ofrece.
        """
        booking, error = self.AptType._visar_wizard_apply_answer(
            {'mode': 'wizard', 'selections': {}}, 'services',
            {'group_ids': [self.fum_group.id, 999999]})
        self.assertIsNone(error)
        self.assertEqual(booking['selections']['group_ids'], [self.fum_group.id])

    def test_services_vacio_no_avanza(self):
        booking, error = self.AptType._visar_wizard_apply_answer(
            {'mode': 'wizard', 'selections': {}}, 'services', {'group_ids': []})
        self.assertIsNotNone(error)
        self.assertEqual(error['code'], 'no_service')

    def test_paso_desconocido_no_lanza(self):
        """El runtime puede mandar un paso rancio tras un reinicio."""
        booking, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(), 'paso_que_no_existe', {})
        self.assertIsNotNone(error)
        self.assertEqual(error['code'], 'unknown_step')

    # ------------------------------------------------------------------
    # Secuencia
    # ------------------------------------------------------------------

    def test_secuencia_de_fumigacion(self):
        booking = {'mode': 'wizard', 'selections': {}}
        self.assertEqual(self.AptType._visar_wizard_next_step(booking), 'services')

        booking = self._booking_fum()
        self.assertEqual(self.AptType._visar_wizard_next_step(booking), 'motivo')

        booking = self._booking_fum(motivo='correctivo')
        self.assertEqual(self.AptType._visar_wizard_next_step(booking), 'plagas')

        booking = self._booking_fum(motivo='correctivo', servicio_plaga=['rastreros'])
        self.assertEqual(self.AptType._visar_wizard_next_step(booking), 'cobertura')

    def test_el_corte_a_valoracion_se_salta_las_mediciones(self):
        """Decidida la valoración, no se vuelve a preguntar el tamaño."""
        booking = self._booking_fum(
            motivo='correctivo', servicio_plaga=[], requiere_valoracion=True,
            motivo_valoracion='termitas', cobertura='ambos')
        self.assertEqual(self.AptType._visar_wizard_next_step(booking), 'valuation')

    def test_la_secuencia_termina_en_direccion(self):
        """`next_step` no devuelve extras ni póliza: dependen de zona/items.

        Se resuelven AL contestar la dirección, y por eso viven en la cadena
        posterior (`_visar_wizard_step_after`), no en el cuestionario.
        """
        booking = self._booking_fum(motivo='preventivo')
        sequence = self.AptType._visar_wizard_step_sequence(booking)
        self.assertEqual(sequence[0], 'services')
        self.assertIn('address', sequence)
        self.assertNotIn('extras', sequence,
                         "Sin zona ni items todavía no hay extras que ofrecer")
        self.assertNotIn('poliza', sequence)

    # ------------------------------------------------------------------
    # La cadena posterior a la dirección
    # ------------------------------------------------------------------

    def test_la_cadena_no_rebota_al_paso_ya_contestado(self):
        """Contestar extras no hace desaparecer la oferta: hay que seguir de largo.

        Sin arrancar la cadena DESDE el paso contestado, el cliente que dice "no
        quiero extras" vuelve a ver la pregunta de extras, para siempre.
        """
        # Sin zona ni items no hay ofertas, así que la cadena cae a 'schedule'
        # desde cualquier punto: lo que se fija aquí es que nunca se devuelve el
        # paso que se acaba de contestar.
        booking = {'mode': 'wizard', 'selections': {}}
        for step in ('address', 'extras', 'poliza'):
            nxt = self.AptType._visar_wizard_step_after(booking, step)
            self.assertNotEqual(nxt, step,
                                "La cadena no puede devolver el paso ya contestado")
            self.assertEqual(nxt, 'schedule')

    def test_extras_solo_acepta_lo_ofrecido(self):
        """La cantidad la fija la oferta, no el cliente."""
        booking = {'mode': 'wizard', 'selections': {}}
        result, error = self.AptType._visar_wizard_apply_answer(
            booking, 'extras', {'extra_ids': [999999]})
        self.assertIsNone(error)
        self.assertEqual(result['extras_accepted'], [],
                         "Un producto que no se ofreció no entra al pedido")

    # ------------------------------------------------------------------
    # Dirección
    # ------------------------------------------------------------------

    def test_direccion_incompleta_no_avanza(self):
        booking, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(), 'address', {'street': 'Juarez'})
        self.assertIsNotNone(error)
        self.assertEqual(error['code'], 'bad_address')
        self.assertIn('address', error, "El error devuelve lo capturado para repintarlo")

    def test_cp_fuera_de_cobertura_se_rechaza(self):
        booking, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(), 'address', {
                'street': 'Juarez', 'ext_num': '45', 'neighborhood': 'Centro',
                'zip': '00000',
            })
        self.assertIsNotNone(error)
        self.assertEqual(error['code'], 'bad_address')

    # ------------------------------------------------------------------
    # Opciones
    # ------------------------------------------------------------------

    def test_las_opciones_de_plagas_dependen_del_motivo(self):
        """El agente no puede ofrecer termitas en la rama preventiva.

        Si lo hiciera, el cliente elegiría una opción que la normalización
        descarta y el paso se quedaría en bucle.
        """
        correctivo = self.AptType._visar_wizard_step_options(
            self._booking_fum(motivo='correctivo'), 'plagas')
        valores = [o['value'] for o in correctivo['options']]
        self.assertIn('termitas', valores)
        self.assertNotIn('proteccion_general', valores)

        preventivo = self.AptType._visar_wizard_step_options(
            self._booking_fum(motivo='preventivo'), 'plagas')
        valores = [o['value'] for o in preventivo['options']]
        self.assertNotIn('termitas', valores)
        self.assertIn('proteccion_general', valores)

    def test_cada_paso_dice_con_que_clave_se_contesta(self):
        """`answer_key` evita que el runtime mantenga su propio mapa paso → clave.

        Sin esto el otro lado tendría que saber que "plagas" se contesta con
        `servicio_plaga` y "cobertura" con `cobertura` — otra regla duplicada,
        que es justo lo que este refactor vino a eliminar.
        """
        esperado = {
            'services': 'group_ids', 'motivo': 'motivo',
            'plagas': 'servicio_plaga', 'cobertura': 'cobertura',
            'exterior': 'band_id', 'extras': 'extra_ids', 'poliza': 'plan_id',
        }
        booking = self._booking_fum(motivo='correctivo')
        for step, key in esperado.items():
            options = self.AptType._visar_wizard_step_options(booking, step)
            self.assertEqual(options.get('answer_key'), key, step)

        # Los sub-pasos de grupo comparten clave.
        options = self.AptType._visar_wizard_step_options(
            booking, 'group_%s' % self.fum_group.id)
        self.assertEqual(options.get('answer_key'), 'dimension_ids')

        # Medición y texto NO tienen una sola clave: la llevan las secciones y
        # los campos. Se marca explícitamente para que el runtime no adivine.
        for step in ('dimensiones', 'interior', 'address'):
            options = self.AptType._visar_wizard_step_options(booking, step)
            self.assertIsNone(options.get('answer_key'), step)
        interior = self.AptType._visar_wizard_step_options(booking, 'interior')
        self.assertEqual(interior.get('mode_key'), 'interior_mode')

    def _booking_completo(self, **selections):
        """Un booking con el cuestionario contestado hasta la dirección."""
        booking = self._booking_fum(**selections)
        booking.update(zone_id=1, items=[{'dimension_id': 1}],
                       delivery_address={'street': 'X', 'ext_num': '1',
                                         'neighborhood': 'Y', 'zip': '64000'})
        return booking

    def test_lo_contestado_no_se_vuelve_a_preguntar(self):
        """`_visar_wizard_next_pending_step`: el primer paso SIN contestar.

        La marca del tramo final es la PRESENCIA de la clave, no su valor:
        "dije que no" y "no me lo has preguntado" tienen que ser estados
        distintos, o corregir cualquier cosa vuelve a preguntar los extras y la
        póliza aunque no dependieran de lo corregido.

        Se fuerza el prefijo a "contestado" (`next_step` → dirección) porque lo
        que se prueba aquí es el TRAMO FINAL, que es lo que no existía; que el
        prefijo se delega está fijado en la prueba de al lado.
        """
        booking = self._booking_completo(extras_ids=[], poliza_plan_id=False)
        with patch.object(type(self.AptType), '_visar_wizard_next_step',
                          return_value='address'):
            # `poliza_plan_id = False` es "dije que no": contestado.
            self.assertEqual(
                self.AptType._visar_wizard_next_pending_step(booking), 'schedule')

            # Quitar la CLAVE (lo que hace la poda) lo deja pendiente otra vez —
            # siempre que haya póliza que ofrecer para esta configuración.
            sin_poliza = dict(booking)
            sin_poliza['selections'] = {
                k: v for k, v in booking['selections'].items()
                if k != 'poliza_plan_id'}
            esperado = ('poliza' if self.AptType._visar_wizard_poliza_context(
                sin_poliza) else 'schedule')
            self.assertEqual(
                self.AptType._visar_wizard_next_pending_step(sin_poliza), esperado)

    def test_sin_direccion_resuelta_el_pendiente_es_la_direccion(self):
        booking = self._booking_fum(motivo='correctivo')
        self.assertEqual(
            self.AptType._visar_wizard_next_pending_step(booking),
            self.AptType._visar_wizard_next_step(booking))

    def test_contestar_extras_deja_marca_aunque_no_se_acepte_nada(self):
        """"No quiero ningún extra" tiene que ser distinguible de "no se lo he
        preguntado"."""
        booking = dict(self._booking_fum(motivo='correctivo'),
                       zone_id=1, items=[{'dimension_id': 1}])
        booking, error = self.AptType._visar_wizard_apply_answer(
            booking, 'extras', {'extra_ids': []})
        self.assertIsNone(error)
        self.assertIn('extras_ids', booking['selections'])
        self.assertEqual(booking['extras_accepted'], [])

    def test_cambiar_el_servicio_vuelve_a_dejar_pendientes_las_ofertas(self):
        """Extras y póliza se cotizan sobre los items: si cambian, se re-preguntan."""
        selections = dict(self._booking_fum(motivo='correctivo')['selections'],
                          extras_ids=[], poliza_plan_id=3)
        pruned = self.AptType._visar_wizard_clear_downstream(selections, 'services')
        self.assertNotIn('extras_ids', pruned)
        self.assertNotIn('poliza_plan_id', pruned)

    def test_los_pasos_editables_son_los_que_se_preguntaron(self):
        """Lo que se preguntó es exactamente lo que se puede corregir, y va con
        etiqueta: el runtime solo tiene claves (`group_12`)."""
        booking = self._booking_fum(motivo='correctivo')
        pasos = self.AptType._visar_wizard_editable_steps(booking)
        claves = [p['key'] for p in pasos]
        self.assertEqual(
            claves, self.AptType._visar_wizard_step_sequence(booking))
        for paso in pasos:
            self.assertTrue(paso['label'], paso['key'])
            self.assertNotEqual(paso['label'], paso['key'],
                                "%s no tiene etiqueta legible" % paso['key'])

    def test_la_huella_de_agenda_ignora_el_precio(self):
        """Cambiar de tramo cambia lo que cuesta, no quién puede ir.

        Es lo que evita cobrarle al cliente dos toques (día + horario) por haber
        corregido su tramo de m² o su plan de póliza.
        """
        base = {'zone_id': 1, 'items': [
            {'dimension_id': 3, 'appointment_type_id': 1, 'tier_id': 5}]}
        otro_tramo = {'zone_id': 1, 'items': [
            {'dimension_id': 3, 'appointment_type_id': 1, 'tier_id': 9}]}
        otra_dimension = {'zone_id': 1, 'items': [
            {'dimension_id': 4, 'appointment_type_id': 1, 'tier_id': 5}]}
        otra_zona = {'zone_id': 2, 'items': base['items']}

        clave = self.AptType._visar_wizard_schedule_key
        self.assertEqual(clave(base), clave(otro_tramo),
                         "otro tramo: mismo tecnico, el horario sigue valiendo")
        self.assertNotEqual(clave(base), clave(otra_dimension),
                            "otra dimension: puede ser otro tecnico")
        self.assertNotEqual(clave(base), clave(otra_zona),
                            "otra zona: otro pool")

    def test_la_huella_no_depende_del_orden_de_los_items(self):
        """Se compara entre dos llamadas distintas: si el orden la moviera, se
        re-elegiria horario sin motivo."""
        clave = self.AptType._visar_wizard_schedule_key
        uno = {'zone_id': 1, 'items': [{'dimension_id': 3, 'appointment_type_id': 1},
                                       {'dimension_id': 4, 'appointment_type_id': 1}]}
        otro = {'zone_id': 1, 'items': list(reversed(uno['items']))}
        self.assertEqual(clave(uno), clave(otro))

    def test_el_nombre_solo_se_pregunta_si_el_canal_lo_pide(self):
        """El paso existe para WhatsApp y NO para el web.

        En el web la identidad se recoge en el formulario nativo del final; si el
        paso apareciera siempre, el wizard preguntaria dos veces lo mismo. La
        bandera la pone quien sabe (el canal), no el flujo.
        """
        booking = self._booking_fum(motivo='correctivo')
        self.assertFalse(self.AptType._visar_wizard_needs_name(booking),
                         "sin bandera, el paso no existe (es el caso del web)")

        booking = dict(booking, needs_name=True)
        self.assertTrue(self.AptType._visar_wizard_needs_name(booking))
        self.assertEqual(
            self.AptType._visar_wizard_step_after(booking, 'address'),
            VISAR_STEP_NAME,
            "y va justo despues de la direccion")

    def test_el_nombre_ya_dado_no_se_vuelve_a_pedir(self):
        """La cadena no puede rebotar al paso que se acaba de contestar."""
        booking = dict(self._booking_fum(motivo='correctivo'), needs_name=True)
        booking, error = self.AptType._visar_wizard_apply_answer(
            booking, VISAR_STEP_NAME, {'nombre': '  María   López '})
        self.assertIsNone(error)
        self.assertEqual(booking['selections']['nombre'], 'María López',
                         "y de paso se normalizan los espacios")
        self.assertFalse(self.AptType._visar_wizard_needs_name(booking))

    def test_un_nombre_que_no_lo_es_no_avanza(self):
        """Este texto acaba siendo el `res.partner` con el que se factura."""
        booking = dict(self._booking_fum(motivo='correctivo'), needs_name=True)
        for basura in ('', '  ', '7', 'ab', '123456'):
            _booking, error = self.AptType._visar_wizard_apply_answer(
                booking, VISAR_STEP_NAME, {'nombre': basura})
            self.assertEqual((error or {}).get('code'), 'bad_name', repr(basura))

    def test_cambiar_de_servicio_no_borra_el_nombre(self):
        """La poda tumba lo que depende de la respuesta; el nombre no depende de
        nada del cuestionario."""
        booking = dict(self._booking_fum(motivo='correctivo'), needs_name=True)
        booking['selections']['nombre'] = 'María López'
        pruned = self.AptType._visar_wizard_clear_downstream(
            booking['selections'], 'services')
        self.assertEqual(pruned.get('nombre'), 'María López')

    def test_la_poliza_siempre_tiene_salida(self):
        """Un menu sin "no" es una pregunta sin respuesta valida.

        El web deja seguir sin elegir plan (hay boton de continuar); en WhatsApp
        el paso ES el menu, asi que sin esta opcion el cliente se quedaba
        atrapado justo antes de elegir horario. Da igual que haya ofertas o no:
        la salida tiene que estar siempre.
        """
        options = self.AptType._visar_wizard_step_options(
            self._booking_fum(motivo='correctivo'), 'poliza')
        valores = [o['value'] for o in options['options']]
        self.assertIn(VISAR_POLIZA_NONE, valores,
                      "el paso de poliza siempre ofrece no contratarla")
        self.assertEqual(valores[-1], VISAR_POLIZA_NONE,
                         "y va al final, despues de las ofertas")

    def test_no_contratar_poliza_deja_el_plan_vacio(self):
        """Contestar "no" no puede parecerse a no haber contestado."""
        booking = self._booking_fum(motivo='correctivo')
        booking, error = self.AptType._visar_wizard_apply_answer(
            booking, 'poliza', {'plan_id': VISAR_POLIZA_NONE})
        self.assertIsNone(error)
        self.assertFalse(booking['selections'].get('poliza_plan_id'))

    def test_la_periodicidad_se_redacta_en_espanol(self):
        """`billing_period_display_sentence` da "per month": ingles, y traducido
        con el idioma del usuario RPC (en_US). No sirve para hablarle al cliente."""
        Plan = self.env['sale.subscription.plan']
        mensual = Plan.create({'name': "Prueba mensual",
                               'billing_period_unit': 'month',
                               'billing_period_value': 1})
        trimestral = Plan.create({'name': "Prueba trimestral",
                                  'billing_period_unit': 'month',
                                  'billing_period_value': 3})
        anual = Plan.create({'name': "Prueba anual",
                             'billing_period_unit': 'year',
                             'billing_period_value': 1})
        self.assertEqual(
            self.AptType._visar_wizard_plan_period_label(mensual), "al mes")
        self.assertEqual(
            self.AptType._visar_wizard_plan_period_label(trimestral), "cada 3 meses")
        self.assertEqual(
            self.AptType._visar_wizard_plan_period_label(anual), "al año")

    def test_la_descripcion_del_plan_dice_cuanto_y_cada_cuanto(self):
        """Con cuatro planes que hoy se llaman igual (I-15), esta linea es lo
        unico que los distingue en el chat."""
        plan = self.env['sale.subscription.plan'].create({
            'name': "Prueba mensual", 'billing_period_unit': 'month',
            'billing_period_value': 1})
        texto = self.AptType._visar_wizard_poliza_description({
            'period_total': 450.0, 'saving': 150.0,
            'currency_id': self.env.company.currency_id.id,
            'period_label': self.AptType._visar_wizard_plan_period_label(plan),
        })
        self.assertIn("450", texto)
        self.assertIn("al mes", texto)
        self.assertIn("150", texto, "y cuanto se ahorra frente a pagarlo suelto")

    def test_las_opciones_son_serializables(self):
        """Van por JSON-RPC: un recordset colado aquí revienta en el transporte.

        El riesgo es real: `_visar_wizard_dimension_sections` SÍ devuelve
        recordsets (las plantillas web los necesitan), y la versión que sale
        hacia el runtime tiene que ser la serializada.
        """
        def check(step, where, payload):
            for key, value in payload.items():
                self.assertIsInstance(
                    value, (str, int, float, bool, type(None)),
                    "%s.%s.%s no es serializable: %r" % (step, where, key, value))

        booking = self._booking_fum(motivo='correctivo', cobertura='ambos')
        for step in ('services', 'motivo', 'plagas', 'cobertura', 'exterior',
                     'dimensiones', 'interior', 'address', 'extras', 'poliza',
                     'valuation', 'schedule'):
            options = self.AptType._visar_wizard_step_options(booking, step)
            for option in options['options']:
                check(step, 'options', option)
            for section in options.get('sections') or []:
                for key, value in section.items():
                    if key == 'options':
                        for tier in value:
                            check(step, 'sections.options', tier)
                        continue
                    check(step, 'sections', {key: value})
