# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAgentPromptRoutes(TransactionCase):
    """`visar.agent.prompt`: un prompt BASE + una memoria por ruta.

    Lo que se fija aqui:

      * El BASE se lee con `ruta` vacia, **pase lo que pase con las secuencias**.
        Es la prueba de regresion de todo el cambio: el lector de antes buscaba
        con dominio VACIO, asi que la primera memoria de ruta con secuencia baja
        se habria convertido en el prompt base — 1 KB sustituyendo a 20 000, sin
        excepcion y cacheado 15 minutos.
      * Los lectores **nunca levantan**. Si esta RPC falla y el runtime aun no
        tiene nada cacheado, `RuntimeConfigCache.refresh` re-lanza y el servicio
        no le contesta a nadie.
      * "Ausente" y "vacia" son lo mismo para el runtime: una ruta sin registro,
        archivada o en blanco no aparece en el dict.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Prompt = cls.env['visar.agent.prompt']
        # El modulo SIEMBRA cinco memorias (data/visar_agent_prompt_routes.xml),
        # y produccion tiene su base. Se archivan todas dentro de la transaccion
        # para que estas pruebas describan la REGLA y no la siembra.
        cls.Prompt.with_context(active_test=False).search([]).write({'active': False})
        cls.base = cls.Prompt.create({
            'name': "Base de prueba", 'body': "BASE" * 100, 'sequence': 10,
        })

    def _memoria(self, ruta, body="memoria", sequence=20):
        return self.Prompt.create({
            'name': f"Memoria {ruta}", 'ruta': ruta,
            'body': body, 'sequence': sequence,
        })

    # ------------------------------------------------------------------

    def test_el_base_ignora_las_memorias_de_ruta(self):
        """Una memoria con secuencia MENOR que el base no se vuelve el base.

        Es exactamente lo que hacia el `search([])` de antes.
        """
        self._memoria('info', body="memoria corta", sequence=1)
        self.assertEqual(self.Prompt._agent_active_body(), self.base.body)

    def test_memoria_por_ruta(self):
        self._memoria('info', body="lo de informacion")
        self.assertEqual(self.Prompt._agent_route_body('info'), "lo de informacion")
        self.assertIsNone(self.Prompt._agent_route_body('schedule'))

    def test_una_ruta_inventada_no_revienta(self):
        self.assertIsNone(self.Prompt._agent_route_body('no_existe'))

    def test_lo_archivado_no_viaja(self):
        memoria = self._memoria('info')
        memoria.active = False
        self.assertIsNone(self.Prompt._agent_route_body('info'))
        self.assertNotIn('info', self.Prompt._agent_route_memories())

    def test_cuerpo_en_blanco_no_viaja(self):
        """`body` es required, pero "   " si se puede guardar.

        Una memoria en blanco significa "ninguna", no "inyecta un bloque vacio".
        """
        self._memoria('info', body="   ")
        self.assertIsNone(self.Prompt._agent_route_body('info'))
        self.assertNotIn('info', self.Prompt._agent_route_memories())

    def test_no_se_puede_duplicar_una_ruta(self):
        """Las rutas las define el runtime: desde Odoo no se anaden ni se clonan.

        Antes del 29-sep-2026 esto se permitia y se resolvia por `sequence`. El
        problema no era el desempate -funciona- sino que el registro perdedor
        seguia pareciendo editable: se escribia en el y no pasaba nada.
        """
        self._memoria('info', body="la primera")
        with self.assertRaises(ValidationError):
            self._memoria('info', body="la segunda")

    def test_no_se_puede_duplicar_el_prompt_base(self):
        """El caso caro: en produccion habia DOS, y mandaba el que nadie sembro."""
        with self.assertRaises(ValidationError):
            self.Prompt.create({'name': "Otro base", 'body': "x", 'sequence': 1})

    def test_archivar_y_reemplazar_si_se_puede(self):
        """La salida legitima: se guarda la version vieja a la vista, no se borra."""
        vieja = self._memoria('info', body="la vieja")
        vieja.active = False
        nueva = self._memoria('info', body="la nueva")
        self.assertEqual(self.Prompt._agent_route_body('info'), "la nueva")
        # ...y desarchivar la vieja vuelve a chocar, que es el agujero que
        # dejaria contar solo los activos si `active` no estuviera vigilado.
        with self.assertRaises(ValidationError):
            vieja.active = True
        self.assertTrue(nueva.es_vigente)

    def test_el_desempate_sigue_protegiendo_las_filas_viejas(self):
        """`sequence, id` se queda, para los duplicados que la restriccion no vio.

        La restriccion nacio con datos dentro: nada garantiza que no quede un
        duplicado de antes, y el lector no puede elegir al azar. Se insertan por
        SQL justamente porque el ORM ya no deja crearlos.
        """
        self.env.cr.execute("""
            INSERT INTO visar_agent_prompt
                   (name, body, ruta, sequence, active, create_uid, write_uid,
                    create_date, write_date)
            VALUES ('Legado 30', 'la de secuencia 30', 'info', 30, TRUE, 1, 1,
                    NOW(), NOW()),
                   ('Legado 5', 'la de secuencia 5', 'info', 5, TRUE, 1, 1,
                    NOW(), NOW())
        """)
        self.Prompt.invalidate_model()
        self.assertEqual(self.Prompt._agent_route_body('info'), "la de secuencia 5")

    def test_payload_de_runtime(self):
        self._memoria('info')
        self._memoria('schedule')
        payload = self.env['visar.agent.tools'].agent_runtime_config()
        self.assertEqual(
            set(payload), {'generated_at', 'prompt', 'route_prompts', 'llm',
                           'wa_templates', 'correcciones'})
        self.assertIsInstance(payload['route_prompts'], dict)
        self.assertEqual(set(payload['route_prompts']), {'info', 'schedule'})
        # El base no se ve afectado por que existan memorias.
        self.assertEqual(payload['prompt'], self.base.body)

    def test_sin_ningun_registro_no_revienta(self):
        """El caso de una base recien instalada, clavado."""
        self.base.active = False
        payload = self.env['visar.agent.tools'].agent_runtime_config()
        self.assertIsNone(payload['prompt'])
        self.assertEqual(payload['route_prompts'], {})

    def test_lo_lee_el_usuario_del_agente(self):
        """Los metodos NO usan sudo(): la ACL de solo lectura es el limite real.

        Si el campo nuevo se quedara fuera de los permisos, el runtime dejaria de
        poder leer su propio prompt.
        """
        self._memoria('info', body="lo de informacion")
        usuario = self.env.ref('visar_whatsapp_agent.user_whatsapp_agent')
        payload = self.env['visar.agent.tools'].with_user(
            usuario).agent_runtime_config()
        self.assertEqual(payload['route_prompts'].get('info'), "lo de informacion")

    def test_es_vigente_marca_uno_por_ruta(self):
        """Con el catalogo cerrado, «vigente» es todo lo que hay activo."""
        info = self._memoria('info', body="la unica")
        self.assertTrue(info.es_vigente)
        self.assertTrue(self.base.es_vigente)

    def test_los_prompts_no_se_borran(self):
        """Borrar se lleva meses de ajuste sin copia; archivar hace lo mismo."""
        info = self._memoria('info')
        with self.assertRaises(UserError):
            info.unlink()


@tagged('post_install', '-at_install')
class TestAgentRouteMeta(TransactionCase):
    """`ROUTE_META`: los metadatos que la consola pinta en cada ruta.

    Son una COPIA de lo que vive en el runtime (`visar_fastapi/app/odoo/
    tools.py`). Se acepta la duplicacion mientras la sujete una prueba: sin
    ella, retirar una herramienta alla dejaria la pantalla enseniandola aqui
    durante meses, que es el riesgo de "dos front-ends" que este proyecto ya se
    cobro una vez (I-11).

    Se sustituye por `agent_register_capabilities()` -el runtime registrando su
    manifiesto al arrancar- cuando se despliegue esa fase.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Prompt = cls.env['visar.agent.prompt']
        # Mismo motivo que en la clase de arriba: el modulo SIEMBRA cinco
        # memorias, y sin archivarlas `estado` describiria la siembra en vez de
        # la regla — una memoria nueva con la misma secuencia pierde por id y
        # sale 'eclipsada', que es correcto y no es lo que se fija aqui.
        cls.Prompt.with_context(active_test=False).search([]).write({'active': False})

    def _uno(self, ruta):
        return self.Prompt.create({
            'name': f"Memoria {ruta}", 'ruta': ruta, 'body': "x", 'sequence': 20,
        })

    def test_route_meta_cubre_todas_las_rutas(self):
        """Anadir una ruta sin metadatos rompe AQUI, no en la pantalla.

        Es lo unico que sostiene la copia. Si esta prueba se borra, la consola
        pasa a poder mentir en silencio.
        """
        from odoo.addons.visar_whatsapp_agent.models.visar_agent_prompt import (
            ROUTES, ROUTE_META, _TOOLS,
        )
        self.assertEqual(
            set(ROUTE_META), {code for code, _label in ROUTES},
            "cada ruta del Selection necesita su entrada en ROUTE_META")
        for ruta, meta in ROUTE_META.items():
            for nombre in meta.get('tools') or ():
                self.assertIn(
                    nombre, _TOOLS,
                    f"la ruta {ruta} declara una herramienta que no existe")

    def test_todas_las_rutas_se_alcanzan(self):
        """Incluida `info`, que estuvo marcada como muerta y no lo estaba.

        Entre ago-2026 y el 29-sep-2026 la pantalla le decia al consultor, en
        rojo, que lo que escribiera en la memoria de Informacion no cambiaria
        ninguna conversacion. En produccion era la memoria de ruta mas larga de
        las cinco y se llega a ella por el PRIMER boton del menu de bienvenida
        (`MAIN_MENU`, con `menu_inicial` en True por defecto).

        Si algun dia se apaga `menu_inicial`, `info` si queda sin camino y esta
        prueba es la que tiene que obligar a volver aqui.
        """
        for ruta in ('reception', 'info', 'schedule', 'existing', 'other'):
            self.assertTrue(self._uno(ruta).alcanzable, ruta)

    def test_ninguna_ruta_alega_estar_muerta(self):
        """`motivo_muerta` solo se pinta con `alcanzable` en False."""
        for ruta in ('reception', 'info', 'schedule', 'existing', 'other'):
            self.assertFalse(self._uno(ruta).motivo_muerta, ruta)

    def test_agendar_solo_expone_una_herramienta(self):
        """La diferencia que esta pantalla existe para contar.

        Dentro del cuestionario el modelo ve `DIGRESSION_TOOLS` (solo
        `resolve_zone`); fuera ve las cinco. Es lo que hace que una duda a media
        reserva no pueda mover la reserva.
        """
        agendar = self._uno('schedule')
        self.assertEqual(agendar.herramientas_num, 1)
        self.assertIn('resolve_zone', agendar.herramientas)
        self.assertNotIn('quote_service', agendar.herramientas)
        self.assertTrue(agendar.garantias, "y se dice lo que NO puede pasar")

        recepcion = self._uno('reception')
        self.assertEqual(recepcion.herramientas_num, 5)
        self.assertIn('quote_service', recepcion.herramientas)

    def test_el_estado_se_dice_con_palabras(self):
        """El aviso no puede vivir solo en el color de la fila.

        Se reporto justo eso: `info` salia en la lista sin nada que la
        distinguiera. Un `decoration-danger` depende de que el cliente web traiga
        un campo que no se pinta, y aunque funcione deja el aviso en un color que
        hay que saber interpretar. Esta prueba fija que el estado es un VALOR.
        """
        self.assertEqual(self._uno('schedule').estado, 'viva')

        # `eclipsada` se queda aunque el catalogo cerrado ya no permita crear un
        # duplicado por ORM: puede quedar uno de antes de la restriccion, y ese
        # registro TIENE que poder decir en palabras que no se usa. Se fabrica
        # por SQL porque es la unica forma que queda de llegar a este estado.
        self._uno('other').sequence = 5
        self.env.cr.execute("""
            INSERT INTO visar_agent_prompt
                   (name, body, ruta, sequence, active, create_uid, write_uid,
                    create_date, write_date)
            VALUES ('Legado eclipsado', 'x', 'other', 50, TRUE, 1, 1,
                    NOW(), NOW())
            RETURNING id
        """)
        legado = self.Prompt.browse(self.env.cr.fetchone()[0])
        self.Prompt.invalidate_model()
        self.assertEqual(legado.estado, 'eclipsada')

    def test_el_prompt_base_no_tiene_metadatos_de_ruta(self):
        """Con `ruta` vacia los campos quedan en blanco.

        Es lo que permite que el mismo modelo tenga dos formularios: el del
        prompt base no pinta ninguno de estos bloques.
        """
        base = self.Prompt.create({
            'name': "Base", 'body': "BASE", 'sequence': 10,
        })
        self.assertFalse(base.disparador)
        self.assertFalse(base.herramientas)
        self.assertFalse(base.garantias)
        self.assertEqual(base.herramientas_num, 0)

    def test_los_campos_de_consola_no_viajan_al_runtime(self):
        """El contrato RPC no se entera de nada de esto.

        Los metadatos son de UI. Si se colaran en `agent_runtime_config`, el
        runtime pagaria tokens por texto que no es un prompt.
        """
        self._uno('schedule')          # si no, `route_prompts` viaja vacio
        payload = self.env['visar.agent.tools'].agent_runtime_config()
        self.assertEqual(
            set(payload), {'generated_at', 'prompt', 'route_prompts', 'llm',
                           'wa_templates', 'correcciones'})
        self.assertTrue(payload['route_prompts'], "y hay algo que revisar")
        for cuerpo in payload['route_prompts'].values():
            self.assertNotIn('resolve_zone  (lee)', cuerpo)
