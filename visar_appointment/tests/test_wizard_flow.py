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

    def test_el_resumen_de_un_corte_vende_la_visita_y_no_el_servicio(self):
        """REGRESION (visto en `visar-db`, 21-ago-2026).

        Con termitas, la pantalla de revision decia:

            • Fumigación
            • Visita de valoración técnica
            • Visita de valoración técnica
            • *Total: $500.00 MXN*

        El total era correcto; las lineas no. El grupo describe algo que NO se
        esta cobrando, y la valoracion salia dos veces desde que el corte tiene
        sus propios items: esa lista ya produce su etiqueta y el `append`
        explicito la repetia. Es la pantalla que existe para que nadie firme un
        cheque en blanco.
        """
        booking = self._booking_fum(motivo='correctivo')
        booking['selections'].update(requiere_valoracion=True,
                                     motivo_valoracion='termitas')
        resumen = self.AptType._visar_wizard_summary(booking)

        self.assertEqual(resumen['lines'], ['Visita de valoración técnica'])

    def test_el_resumen_normal_sigue_diciendo_que_servicio_es(self):
        """Y sin corte no cambia nada: el grupo es lo que se compra."""
        booking = self._booking_fum(motivo='preventivo')
        resumen = self.AptType._visar_wizard_summary(booking)

        self.assertTrue(resumen['lines'], 'el resumen no puede salir vacio')
        self.assertNotIn('Visita de valoración técnica', resumen['lines'])

    def test_extras_trae_su_propia_salida(self):
        """REGRESION (visto recorriendo la reserva escribiendo, 26-ago-2026).

        Al quitar los botones del cuestionario, el "Listo, Enviar" —que era la
        unica forma de cerrar los extras sin comprar nada— desaparecio. El paso
        quedo sin respuesta valida para quien no queria el add-on: nueve formas
        de decir que no ("no", "ninguno", "nada", "asi esta bien"...) devolvian
        "No entendi", y lo unico que avanzaba era **aceptar el cargo**.

        Es exactamente el mismo agujero que ya se habia tapado en el paso de
        poliza, y por eso se tapa igual: con una fila explicita. La salida es
        dato del paso, no una habilidad del canal.
        """
        opciones = self.AptType._visar_wizard_step_options(
            self._booking_fum(), 'extras')
        salidas = [o for o in opciones['options'] if o['value'] == 0]
        self.assertEqual(len(salidas), 1, "una y solo una fila de rechazo")
        self.assertIn('gracias', (salidas[0]['label'] or '').lower())
        self.assertNotIn('{done}', opciones.get('hint') or '',
                         "la pista no puede mandar a un boton que ya no existe")

    def test_rechazar_los_extras_no_compra_nada(self):
        """El id 0 no existe en `product.product`, asi que el filtro lo tira solo."""
        booking, error = self.AptType._visar_wizard_apply_answer(
            self._booking_fum(), 'extras', {'extra_ids': [0]})
        self.assertIsNone(error)
        self.assertEqual(booking['extras_accepted'], [])
        self.assertIn('extras_ids', booking['selections'],
                      "el paso queda CONTESTADO, no pendiente")

    def test_cobertura_lleva_el_vocabulario_del_cliente(self):
        """"las dos" es como se dice "ambos", y es un paso de PRECIO.

        Las frases viven aqui —son copy de negocio— y no en el runtime, que solo
        las compara. Sin ellas el cliente escribia bien y el sistema repreguntaba.
        """
        opciones = self.AptType._visar_wizard_step_options(
            self._booking_fum(), 'cobertura')
        por_valor = {o['value']: o for o in opciones['options']}
        ambos = por_valor['ambos'].get('keywords') or []
        for frase in ('las dos', 'los dos', 'amba'):
            self.assertIn(frase, ambos)
        self.assertTrue(por_valor['interior'].get('keywords'))
        self.assertTrue(por_valor['exterior'].get('keywords'))

    def test_el_paso_de_jardin_manda_sus_limites(self):
        """Sin ellos, la etiqueta ("101 – 150 m²") no deja contestar con metros."""
        opciones = self.AptType._visar_wizard_step_options(
            self._booking_fum(cobertura='exterior'), 'exterior')
        filas = opciones['options']
        self.assertTrue(filas)
        self.assertTrue(any(f.get('m2_min') is not None for f in filas),
                        "alguna banda tiene que traer limites")
        for fila in filas:
            if fila.get('m2_min') is None:
                # Sin sembrar: se elige por numero, como antes. Nunca 0.0, que
                # se leeria como "de cero en adelante" y se tragaria todo.
                self.assertIsNone(fila.get('m2_max'))

    def test_cada_paso_lleva_la_pista_que_le_toca(self):
        """Qué decirle al cliente en cada paso es negocio, y lo escribe Odoo.

        El runtime tenía UNA línea para todos los pasos de multi-selección
        ("Puedes elegir varias") y no servía para ninguno: en plagas preventivas
        conviene una sola, porque "Protección general" ya cubre las tres; y en
        cobertura lo que hace falta no es una instrucción sino una recomendación
        — que "ambos" no cuesta más si el patio es chico.

        Y se dicen HABLANDO. Estas opciones solo las usa el chat (el wizard web
        tiene sus propias plantillas), así que "selecciona" y *da click en
        "{done}"* describían un widget que dejó de existir: primero las listas
        de WhatsApp (ago-2026) y después los números (sep-2026).
        """
        booking = self._booking_fum(motivo='preventivo')
        pista = lambda step: self.AptType._visar_wizard_step_options(
            booking, step).get('hint') or ''

        self.assertIn('Puedes decirme varios', pista('services'))
        self.assertIn('lo que quieres evitar', pista('plagas'))
        self.assertIn('no hay costo adicional', pista('cobertura'))
        self.assertIn('no los del terreno', pista('interior'))

        # En correctivo el cliente reporta lo que TIENE, y puede ser más de una.
        correctivo = self.AptType._visar_wizard_step_options(
            self._booking_fum(motivo='correctivo'), 'plagas')
        self.assertIn('Puedes decirme varias', correctivo.get('hint') or '')

        # Ninguna pista manda pulsar nada: no hay nada que pulsar.
        for step in ('services', 'plagas', 'cobertura', 'interior'):
            self.assertNotIn('click', pista(step))
            self.assertNotIn('{done}', pista(step))

    def test_el_paso_de_interior_dice_que_se_esta_midiendo(self):
        """"¿De qué tamaño es el área?" no dice si cuenta el terreno.

        `interior` mide la CASA y `dimensiones` mide lo que toque el servicio,
        así que no pueden preguntar lo mismo.
        """
        booking = self._booking_fum(motivo='preventivo')
        interior = self.AptType._visar_wizard_step_options(booking, 'interior')
        directo = self.AptType._visar_wizard_step_options(booking, 'dimensiones')

        self.assertIn('construcción', interior['title'])
        self.assertNotEqual(interior['title'], directo['title'])

    def test_el_tramo_de_valoracion_no_llega_cortado(self):
        """Una fila de WhatsApp son 24 caracteres y el paréntesis no cabe.

        Llegaba "Más de 1,000 m² (valo…", cortado justo donde empezaba lo que
        había que entender. El paréntesis baja al subtítulo, que admite 72.
        """
        tier = self.env['visar.service.tier'].search(
            [('is_valuation', '=', True)], limit=1)
        if not tier:
            self.skipTest('no hay tramo de valoración configurado')

        opciones = self.AptType._visar_wizard_tier_options(tier)
        self.assertEqual(len(opciones), 1)
        self.assertNotIn('(', opciones[0]['label'])
        self.assertIn('valoración', opciones[0]['description'])

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

    def test_la_descripcion_del_plan_empieza_por_el_AHORRO(self):
        """Lo primero que se lee es por que conviene, no cuanto cuesta.

        Y en porcentaje: "ahorras $150" no se puede juzgar sin saber sobre que,
        y obliga al cliente a dividir de cabeza en mitad de la conversacion.
        """
        plan = self.env['sale.subscription.plan'].create({
            'name': "Prueba mensual", 'billing_period_unit': 'month',
            'billing_period_value': 1})
        texto = self.AptType._visar_wizard_poliza_description({
            'period_total': 450.0, 'saving': 150.0, 'saving_percent': 25.0,
            'currency_id': self.env.company.currency_id.id,
            'period_label': self.AptType._visar_wizard_plan_period_label(plan),
        })
        self.assertTrue(texto.startswith("Ahorro del 25%"), texto)
        self.assertIn("450", texto)
        self.assertIn("al mes", texto)

    def test_sin_porcentaje_el_ahorro_se_dice_en_pesos(self):
        """Degradar, nunca callar: un plan sin base con la que comparar sigue
        pudiendo decir lo que ahorra."""
        texto = self.AptType._visar_wizard_poliza_description({
            'period_total': 450.0, 'saving': 150.0,
            'currency_id': self.env.company.currency_id.id,
            'period_label': "al mes",
        })
        self.assertIn("150", texto)

    def test_el_plan_solo_lleva_su_periodicidad_si_hace_falta(self):
        """Cuatro filas con la misma etiqueta (I-15) no se pueden elegir; cuatro
        con la periodicidad pegada cuando ya se distinguen, sobran."""
        etiqueta = self.AptType._visar_wizard_poliza_label
        repetido = {'name': "Póliza", 'period_label': "cada 3 meses"}
        self.assertEqual(etiqueta(repetido, ["Póliza", "Póliza"]),
                         "Póliza (cada 3 meses)")
        self.assertEqual(etiqueta(repetido, ["Póliza", "Otra"]), "Póliza")

    def test_las_opciones_son_serializables(self):
        """Van por JSON-RPC: un recordset colado aquí revienta en el transporte.

        El riesgo es real: `_visar_wizard_dimension_sections` SÍ devuelve
        recordsets (las plantillas web los necesitan), y la versión que sale
        hacia el runtime tiene que ser la serializada.
        """
        escalares = (str, int, float, bool, type(None))

        def check(step, where, payload):
            for key, value in payload.items():
                # Una LISTA de escalares tambien viaja por JSON-RPC, y hace
                # falta: `keywords` son los sinonimos con que el cliente contesta
                # el paso. Se comprueba elemento a elemento en vez de aceptar la
                # lista entera — lo que esta prueba caza es un recordset colado, y
                # un recordset dentro de una lista lo seguiria siendo.
                if isinstance(value, (list, tuple)):
                    for i, item in enumerate(value):
                        self.assertIsInstance(
                            item, escalares,
                            "%s.%s.%s[%s] no es serializable: %r"
                            % (step, where, key, i, item))
                    continue
                self.assertIsInstance(
                    value, escalares,
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

    # ------------------------------------------------------------------
    # La rama de valoración llega a horarios (I-17 / diseño 33 §10.7)
    # ------------------------------------------------------------------
    #
    # El corte a valoración era un paso TERMINAL: nunca se preguntaba la
    # dirección, así que no había zona, no había técnicos y no había ni un día
    # que ofrecer. Los clientes que reportan termitas, chinches o "no sé qué es"
    # -los tres cortes- no podían agendar por WhatsApp.
    #
    # El aviso pasa a ser un paso que se acusa, y solo EN EL CHAT: el web tiene
    # su propia página de aviso y su propio flujo, y ahí no cambia nada.

    def _booking_chat(self, **selections):
        """Como `_booking_fum`, pero con la bandera que pone el canal de chat."""
        booking = self._booking_fum(**selections)
        booking['valuation_inline'] = True
        return booking

    def test_el_web_sigue_cortando_en_valoracion(self):
        """La regresión que importa: sin la bandera, `valuation` es terminal.

        Es lo único que puede romper clientes hoy. Si esto se pone en verde con
        el aviso acusado, el web se movió y el cambio no está acotado.
        """
        booking = self._booking_fum(
            motivo='correctivo', servicio_plaga=[], requiere_valoracion=True,
            motivo_valoracion='termitas')
        self.assertEqual(self.AptType._visar_wizard_next_step(booking), 'valuation')

        # Incluso con el acuse puesto a mano: sin canal que lo pregunte en línea,
        # el acuse no significa nada.
        booking['selections']['valuation_ack'] = True
        self.assertEqual(
            self.AptType._visar_wizard_next_step(booking), 'valuation',
            "El web no debe avanzar: corta a su propio flujo de valoración")

    def test_el_aviso_de_valoracion_no_es_terminal_en_el_chat(self):
        booking = self._booking_chat(
            motivo='correctivo', servicio_plaga=[], requiere_valoracion=True,
            motivo_valoracion='termitas')
        self.assertEqual(self.AptType._visar_wizard_next_step(booking), 'valuation')

        booking, error = self.AptType._visar_wizard_apply_answer(
            booking, 'valuation', {'valuation_ack': 'continuar'})
        self.assertIsNone(error)
        self.assertTrue(booking['selections']['valuation_ack'])
        # Acusado: se sigue a la dirección, sin pasar por mediciones.
        booking['valuation_inline'] = True
        self.assertEqual(self.AptType._visar_wizard_next_step(booking), 'address')

    def test_el_aviso_trae_precio_motivo_y_una_salida(self):
        """Un aviso sin botón es un callejón sin salida: eso era el bug."""
        booking = self._booking_chat(
            motivo='correctivo', servicio_plaga=[], requiere_valoracion=True,
            motivo_valoracion='termitas')
        options = self.AptType._visar_wizard_step_options(booking, 'valuation')
        self.assertEqual(options['kind'], 'single')
        self.assertEqual(options['answer_key'], 'valuation_ack')
        self.assertTrue(options['title'], "El aviso tiene que decir algo")
        self.assertIn('termitas', options['title'].lower(),
                      "El cliente tiene que leer POR QUÉ se le pide una valoración")
        self.assertEqual(len(options['options']), 1,
                         "Una sola salida: continuar")

    def test_el_aviso_del_web_sigue_sin_opciones(self):
        booking = self._booking_fum(
            motivo='correctivo', servicio_plaga=[], requiere_valoracion=True,
            motivo_valoracion='termitas')
        options = self.AptType._visar_wizard_step_options(booking, 'valuation')
        self.assertEqual(options['kind'], 'terminal')
        self.assertEqual(options['options'], [])

    def test_cambiar_de_plaga_suelta_el_acuse(self):
        """Si el corte se cae, el aviso que se acusó ya no aplica."""
        booking = self._booking_chat(
            motivo='correctivo', servicio_plaga=[], requiere_valoracion=True,
            motivo_valoracion='termitas', valuation_ack=True)
        booking, error = self.AptType._visar_wizard_apply_answer(
            booking, 'plagas', {'servicio_plaga': ['rastreros']})
        self.assertIsNone(error)
        self.assertFalse(booking['selections'].get('requiere_valoracion'))
        self.assertNotIn('valuation_ack', booking['selections'],
                         "El acuse muere con el corte que lo motivó")

    def test_acusar_sin_corte_no_marca_nada(self):
        """Contestar un aviso que ya no aplica no es un error, pero no marca."""
        booking = self._booking_chat(motivo='correctivo',
                                     servicio_plaga=['rastreros'])
        booking, error = self.AptType._visar_wizard_apply_answer(
            booking, 'valuation', {'valuation_ack': 'continuar'})
        self.assertIsNone(error)
        self.assertFalse((booking['selections'] or {}).get('valuation_ack'))

    def test_la_huella_de_agenda_distingue_valoracion_de_wizard(self):
        """Sin esto, corregir termitas→cucarachas conservaba el horario apartado.

        La valoración tiene tipo de cita y pool de técnicos propios: el horario
        que valía para una no vale para la otra.
        """
        base = {'zone_id': 1, 'items': []}
        wizard = dict(base, selections={'group_ids': [self.fum_group.id]})
        valoracion = dict(base, selections={
            'group_ids': [self.fum_group.id], 'requiere_valoracion': True})
        self.assertNotEqual(
            self.AptType._visar_wizard_schedule_key(wizard),
            self.AptType._visar_wizard_schedule_key(valoracion))

    def test_valoracion_no_ofrece_extras(self):
        """Se ofrecían y luego `_visar_build_sale_lines` los tiraba.

        El cliente aceptaba unos add-ons que nunca aparecían en el total.
        """
        booking = {
            'mode': 'valuation', 'zone_id': 1,
            'items': [{'dimension_id': False, 'is_valuation': True}],
            'selections': {'group_ids': [self.fum_group.id],
                           'requiere_valoracion': True},
        }
        self.assertEqual(self.AptType._visar_wizard_extras_offers(booking), [])
        self.assertIsNone(self.AptType._visar_wizard_poliza_context(booking))

    def test_los_items_de_valoracion_no_dependen_de_tramos(self):
        """El corte por calificación NUNCA elige tramo: sin esto, `no_items`.

        `_visar_resolve_wizard_items` solo emite items para dimensiones con
        `tier_*`, y el corte existe justamente para no medir. Era el segundo
        motivo por el que la rama no cerraba, y no estaba en I-17.
        """
        selections = {'group_ids': [self.fum_group.id], 'motivo': 'correctivo',
                      'requiere_valoracion': True, 'motivo_valoracion': 'termitas'}
        self.assertFalse(
            self.AptType._visar_resolve_wizard_items(selections),
            "Premisa: el corte por calificación no resuelve items por tramo")

        items = self.AptType._visar_wizard_valuation_items()
        if not items:
            self.skipTest("La base no trae producto/tipo de cita de valoración")
        self.assertEqual(len(items), 1, "Una valoración es UNA visita")
        self.assertTrue(items[0]['is_valuation'])
        self.assertTrue(items[0]['variant_id'])
