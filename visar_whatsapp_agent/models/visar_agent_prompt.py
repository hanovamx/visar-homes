# -*- coding: utf-8 -*-
"""Prompt del sistema para el agente LLM, editable sin tocar codigo.

El runtime (`visar_fastapi`) trae el prompt activo por RPC y lo cachea con TTL.
Antes vivia en `prompts.py` (constante `BASE_PROMPT`), que ahora queda solo como
respaldo si Odoo no responde.

Se modela como una LISTA de registros (varios casos de uso, uno activo) aunque el
runtime hoy solo lea el activo: da una UI tipo "plantillas" y deja crecer sin
rediseñar. "Activo" = el primero por `sequence` entre los no archivados.

**Desde ago-2026 hay dos clases de registro**, y las distingue el campo `ruta`:

  * `ruta` vacia  -> el **prompt BASE**. Se inyecta desde el primer mensaje de
    toda conversacion, en todas las rutas. Es el texto largo.
  * `ruta` puesta -> la **memoria de esa ruta**. Se anade DESPUES del base y del
    catalogo, y solo mientras la conversacion este ahi. Son textos cortos.

Vigente = el primero por `sequence, id` **dentro de su ruta**, entre los no
archivados. La regla no cambio; ahora se aplica una vez por ruta en vez de una
sola vez en total.

⚠️ El dominio `[('ruta', '=', False)]` del lector del base NO es adorno. Sin el
esto es un `search([])`, y en cuanto existan memorias de ruta en la tabla una
memoria de 1 KB con `sequence` bajo se convierte en el prompt base, sustituyendo
a los ~20 000 caracteres de produccion. Sin excepcion, sin log, y cacheado 15
minutos: el unico sintoma es que el agente se vuelve vago.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Los ids son los del runtime (`visar_fastapi/app/routing/menu.py`, clase Route):
# son la CLAVE del diccionario que viaja por RPC, no una etiqueta. `reception` es
# el unico sin constante alla: es el estado "todavia sin ruta", y necesita nombre
# para poder editarse.
ROUTES = [
    ('reception', "Recepcion (todavia sin ruta)"),
    ('info', "Informacion: servicios y precios"),
    ('schedule', "Agendar (cuestionario)"),
    ('existing', "Servicio existente"),
    ('other', "Otra cosa / asesor"),
]


# Las cinco herramientas del runtime (`visar_fastapi/app/odoo/tools.py`, `TOOLS`).
# Se resumen en una linea; la descripcion completa que lee el MODELO vive alla y
# no se copia entera a proposito: aqui solo hay que poder mirarla.
_TOOLS = {
    'resolve_zone': ("lee", "Averigua si un CP esta en cobertura y a que zona "
                            "pertenece. Va siempre antes de dar un precio."),
    'quote_service': ("lee", "Cotiza uno o varios servicios para un CP y "
                             "devuelve el desglose y el total."),
    'start_booking': ("entrega", "Entrega la conversacion al cuestionario de "
                                 "agendado, que reserva de punta a punta."),
    'my_services': ("entrega", "Muestra lo que el cliente YA tiene con Visar: "
                               "proxima visita, historial, poliza."),
    'escalate_to_human': ("entrega", "Entrega la conversacion a un asesor y deja "
                                     "registro con el contexto recogido."),
}

# Fuera del cuestionario el modelo ve las cinco (`TOOLS`); dentro solo ve
# `DIGRESSION_TOOLS`. Esa diferencia es la razon de ser de la pantalla.
_TODAS = tuple(_TOOLS)
_SOLO_ZONA = ('resolve_zone',)

# Lo que NO puede pasar en el cuestionario, y no por prompt: no hay camino de
# codigo de una digresion a los cinco metodos que mutan el flujo, y el estado se
# restaura en un `finally`. Ver §14 de `85-motor-de-flujos-agendado.md`.
_GARANTIAS_AGENDAR = (
    "Contestar un paso del cuestionario por el cliente",
    "Elegir fecha, horario o direccion",
    "Dar por confirmada la reserva o mandar la liga de pago",
    "Recalcular el total (lo escribe Odoo, no el modelo)",
)

# Metadatos de cada ruta, para que la consola pueda contar COMO SE LLEGA y QUE SE
# PUEDE HACER sin que nadie tenga que leer el runtime.
#
# ⚠️ Es una copia deliberada de lo que vive en `visar_fastapi`. Lo que la sujeta
# es `test_route_meta_cubre_todas_las_rutas`: anadir una ruta sin metadatos rompe
# la suite en vez de dejar la pantalla mintiendo. Se sustituye por el manifiesto
# del runtime (`agent_register_capabilities`) cuando se despliegue esa fase.
ROUTE_META = {
    'reception': {
        'disparador': "Estado inicial - toda conversacion empieza aqui",
        'cuando': "Es el primer contacto, y el estado al que se vuelve al escribir "
                  "«menu» o «atras». Mientras el modelo solo conteste dudas, la "
                  "conversacion se queda aqui: contestar no exige ninguna tool.",
        'cuando_no': "",
        'tools': _TODAS,
        'garantias': (),
        'alcanzable': True,
    },
    # ⚠️ Estuvo marcada `alcanzable: False` entre ago-2026 y el 29-sep-2026, y
    # era FALSO: la pantalla le decia en rojo al consultor que lo que escribiera
    # aqui no cambiaba ninguna conversacion, mientras en produccion esta era la
    # memoria de ruta mas larga y mas editada de las cinco. Se alcanza por dos
    # caminos vivos, los dos en `_menu_inicial` (`app/config.py`, por defecto
    # True y sin sobrescribir en el `.env` de produccion):
    #   * el boton "Informacion", que es el PRIMERO del menu de bienvenida
    #     (`MAIN_MENU` en `app/routing/menu.py`) -> `agent.py` pone la ruta;
    #   * escribir el nombre de la opcion en el primer mensaje
    #     ("informacion", "info") -> `route_by_name`.
    # Lo que si dejo de existir es la entrada por TEXTO LIBRE: desde que el LLM
    # enruta, "cuanto cuesta fumigar" se contesta en Recepcion sin cambiar de
    # ruta, porque `quote_service` y `resolve_zone` no estan en `ROUTE_TOOLS`.
    'info': {
        'disparador': "El boton «Informacion» del menu de bienvenida, o escribir "
                      "su nombre en el primer mensaje",
        'cuando': "Cuando el cliente ELIGE «Informacion» en el saludo inicial. Ojo: "
                  "una duda de precios escrita en palabras propias («¿cuanto cuesta "
                  "fumigar 120 m2?») NO entra aqui — se contesta en Recepcion, "
                  "porque cotizar no cambia de ruta.",
        'cuando_no': "Dudas de precio formuladas en texto libre (van por Recepcion) · "
                     "agendar · consultar un servicio que ya tiene",
        'tools': _TODAS,
        'garantias': (),
        'alcanzable': True,
    },
    'schedule': {
        'disparador': "El modelo llama start_booking()",
        'cuando': "En cuanto el cliente muestra intencion de reservar o de cerrar: "
                  "«quiero agendar», «si, agendame», «cuando pueden venir», o "
                  "cuando contesta que si a una cotizacion. Cubre tambien la visita "
                  "de valoracion tecnica.",
        'cuando_no': "Quejas · facturas · CP fuera de cobertura · empresas y "
                     "comercios · un «si» a un combo recien ofrecido (eso es "
                     "cambio de canasta: se vuelve a cotizar)",
        'tools': _SOLO_ZONA,
        'garantias': _GARANTIAS_AGENDAR,
        'alcanzable': True,
    },
    'existing': {
        'disparador': "El modelo llama my_services()",
        'cuando': "Cuando pregunta por lo suyo: «¿cuando toca?», «¿ya viene el "
                  "tecnico?», «¿cuantas visitas me quedan?», «mi cita».",
        'cuando_no': "Cotizar · agendar algo nuevo · cambiar o cancelar una cita ya "
                     "agendada (eso va con un asesor)",
        'tools': _TODAS,
        'garantias': ("Inventar fechas o conteos de visitas: los escribe el sistema "
                      "leyendo Odoo, no el modelo",),
        'alcanzable': True,
    },
    'other': {
        'disparador': "El modelo llama escalate_to_human(), o el escape «asesor»",
        'cuando': "Quejas y garantias, errores de cobro y facturas, CP fuera de "
                  "cobertura, clientes no residenciales, cambiar o cancelar una "
                  "cita, o cuando el cliente pide hablar con una persona. Tambien "
                  "si el modelo se queda sin poder ayudar.",
        'cuando_no': "Una duda normal de servicios o precios · agendar",
        'tools': _TODAS,
        'garantias': ("Prometer tiempos de respuesta",),
        'alcanzable': True,
    },
}


class VisarAgentPrompt(models.Model):
    _name = 'visar.agent.prompt'
    _description = "Prompt del agente de WhatsApp"
    # El runtime cachea esto 15 minutos: sin el boton de aplicar, editar un
    # prompt y probarlo en el chat es un cuarto de hora de duda.
    _inherit = ['visar.agent.runtime.mixin']
    _order = 'sequence, id'

    name = fields.Char(
        string="Nombre", required=True,
        help="Nombre del caso de uso, p. ej. 'Atencion a clientes'.")
    body = fields.Text(
        string="Prompt del sistema", required=True,
        help="Texto base del system prompt. El catalogo de servicios se agrega "
             "automaticamente despues de este texto; no hace falta listarlo aqui.")
    # No puede ser `required`: `False` ES el valor del prompt base, y es lo que
    # hace que el registro que ya existia en produccion quede bien colocado sin
    # que la migracion escriba una sola fila.
    # `readonly=True` desde el 29-sep-2026, y no como atributo de vista: el
    # catalogo es CERRADO, y mover un registro de una ruta a otra es otra forma
    # de fabricar el duplicado que `_check_ruta_unica` prohibe. Los `readonly`
    # de Odoo solo atan al cliente web: las semillas XML y las migraciones
    # siguen escribiendo el campo por ORM sin estorbo.
    ruta = fields.Selection(
        ROUTES, string="Ruta", index=True, readonly=True,
        help="Vacio = PROMPT BASE: se inyecta desde el primer mensaje de toda "
             "conversacion. Con ruta = memoria de esa ruta: se anade DESPUES "
             "del base y solo mientras la conversacion este ahi.")
    sequence = fields.Integer(
        string="Secuencia", default=10,
        help="Desempata DENTRO de una misma ruta (y entre los prompts base). "
             "Gana el menor; a igualdad, el id menor, o sea el mas antiguo.")
    active = fields.Boolean(string="Activo", default=True)
    es_vigente = fields.Boolean(
        string="En uso", compute='_compute_es_vigente',
        help="El registro que el runtime lee de verdad para esa ruta (o para el "
             "base). Si hay dos, gana el de menor secuencia.")
    caracteres = fields.Integer(
        string="Caracteres", compute='_compute_caracteres',
        help="Longitud del texto. Las memorias de ruta se pagan en cada "
             "mensaje: conviene tenerlas cortas.")

    # --- Metadatos de la ruta (solo lectura, de `ROUTE_META`) -------------
    #
    # No se almacenan: son constantes del codigo, no datos. Guardarlos obligaria
    # a recalcularlos al desplegar y a migrar cada vez que cambie una linea de
    # texto. En el prompt base (`ruta` vacia) quedan en blanco, que es lo que
    # permite reusar el mismo modelo con dos formularios distintos.
    disparador = fields.Char(
        string="Disparador", compute='_compute_meta_ruta',
        help="Que hace que la conversacion entre en esta ruta.")
    entrada_cuando = fields.Text(
        string="Cuando entra", compute='_compute_meta_ruta')
    entrada_cuando_no = fields.Text(
        string="Cuando NO", compute='_compute_meta_ruta')
    herramientas = fields.Text(
        string="Herramientas", compute='_compute_meta_ruta',
        help="Lo que el modelo puede hacer en esta ruta ademas de escribir.")
    herramientas_num = fields.Integer(
        string="N.o de herramientas", compute='_compute_meta_ruta')
    garantias = fields.Text(
        string="Garantizado por codigo", compute='_compute_meta_ruta',
        help="Lo que NO puede pasar aqui, y no depende del texto del prompt.")
    alcanzable = fields.Boolean(
        string="Alcanzable", compute='_compute_meta_ruta',
        help="Si algun camino del runtime lleva a esta ruta hoy.")
    motivo_muerta = fields.Text(
        string="Por que no se alcanza", compute='_compute_meta_ruta')

    # El estado en PALABRAS, y no solo en color. Un `decoration-danger` sobre la
    # fila depende de que el cliente web traiga un campo que no se pinta, y
    # ademas deja el aviso en un color: quien no lo sepa leer no ve nada. Un
    # campo visible dice "Inalcanzable" y no hay que saber nada para entenderlo.
    estado = fields.Selection(
        [('viva', "En uso"),
         ('inalcanzable', "Inalcanzable"),
         ('eclipsada', "No la usa el runtime")],
        string="Estado", compute='_compute_estado')

    # ------------------------------------------------------------------
    # Calculados (solo para que el admin VEA lo que el runtime hace)
    # ------------------------------------------------------------------

    @api.depends('ruta', 'sequence', 'active')
    def _compute_es_vigente(self):
        """Marca el registro que gana en cada ruta, y el base.

        No se almacena a proposito: un calculado almacenado tendria que
        recalcularse en los HERMANOS al cambiar uno, que es justo lo que sale mal
        en silencio. Son seis busquedas sobre una tabla de media docena de filas,
        y solo al pintar una vista.
        """
        ganadores = set()
        for ruta in [False] + [code for code, _label in ROUTES]:
            record = self.with_context(active_test=True).search(
                [('ruta', '=', ruta)], order='sequence, id', limit=1)
            if record:
                ganadores.add(record.id)
        for record in self:
            record.es_vigente = record.id in ganadores

    @api.depends('body')
    def _compute_caracteres(self):
        for record in self:
            record.caracteres = len(record.body or '')

    @api.depends('ruta')
    def _compute_meta_ruta(self):
        """Vuelca `ROUTE_META` en campos, para que las vistas los pinten.

        Una ruta sin metadatos deja los campos vacios en vez de reventar: la
        consola es informativa y no puede tumbar la edicion de un prompt. Quien
        avisa de ese hueco es la prueba, no la pantalla.
        """
        for record in self:
            meta = ROUTE_META.get(record.ruta) if record.ruta else None
            if not meta:
                record.disparador = False
                record.entrada_cuando = False
                record.entrada_cuando_no = False
                record.herramientas = False
                record.herramientas_num = 0
                record.garantias = False
                record.alcanzable = True
                record.motivo_muerta = False
                continue
            tools = meta.get('tools') or ()
            record.disparador = meta.get('disparador') or False
            record.entrada_cuando = meta.get('cuando') or False
            record.entrada_cuando_no = meta.get('cuando_no') or False
            record.herramientas = "\n".join(
                "%s  (%s)  %s" % (nombre, _TOOLS[nombre][0], _TOOLS[nombre][1])
                for nombre in tools if nombre in _TOOLS) or False
            record.herramientas_num = len(tools)
            record.garantias = "\n".join(
                "- %s" % g for g in (meta.get('garantias') or ())) or False
            record.alcanzable = bool(meta.get('alcanzable', True))
            record.motivo_muerta = meta.get('motivo_muerta') or False

    @api.depends('ruta', 'sequence', 'active')
    def _compute_estado(self):
        """Las dos formas de no servir para nada, con nombres distintos.

        `inalcanzable` es de la RUTA -ningun camino del runtime lleva ahi-;
        `eclipsada` es de este REGISTRO -hay otro con menor secuencia-. Se
        distinguen porque se arreglan de forma distinta: una no la arregla nadie
        editando, la otra se arregla archivando.
        """
        for record in self:
            if not record.alcanzable:
                record.estado = 'inalcanzable'
            elif not record.es_vigente:
                record.estado = 'eclipsada'
            else:
                record.estado = 'viva'

    # ------------------------------------------------------------------
    # El catalogo es CERRADO: seis registros, uno por ruta mas el base
    # ------------------------------------------------------------------
    #
    # Las rutas no son datos, son codigo: cada una existe porque el runtime la
    # pone (`ROUTE_TOOLS` en `app/odoo/tools.py`) y porque tiene herramientas
    # declaradas alla. Una ruta creada desde Odoo no la alcanzaria ninguna
    # conversacion nunca. Un registro DE MAS en una ruta que si existe es peor:
    # no falla, gana por `sequence` y sustituye en silencio al que se estaba
    # editando.
    #
    # Que esto no es teorico lo demuestra produccion: el 29-sep-2026 habia DOS
    # prompts base, y el que mandaba (38 631 caracteres, `sequence` 1) no era el
    # que el modulo habia sembrado (17 485, `sequence` 2). Funcionaba, pero
    # nadie podia saberlo sin mirar la tabla.

    @api.constrains('ruta', 'active')
    def _check_ruta_unica(self):
        """Un solo registro ACTIVO por ruta, y un solo prompt base activo.

        Cuenta solo los activos, y no es dejadez: archivar-y-reemplazar es un
        camino legitimo -se guarda la version vieja a la vista en vez de
        perderla-, y contar los archivados lo prohibiria. El hueco que eso
        abriria -desarchivar el viejo y volver a tener dos candidatos- lo tapa
        `active` estando en el `@api.constrains`: desarchivar es un `write` que
        vuelve a pasar por aqui, y falla en ese momento con el mismo mensaje.

        Un registro archivado no se valida contra nadie: si no, archivar UNO de
        dos duplicados fallaria, que es justo la salida que hay que dejar
        abierta.
        """
        for record in self:
            if not record.active:
                continue
            otros = self.search_count([
                ('ruta', '=', record.ruta or False),
                ('id', '!=', record.id),
            ])
            if not otros:
                continue
            if record.ruta:
                etiqueta = dict(ROUTES).get(record.ruta, record.ruta)
                raise ValidationError(_(
                    "Ya hay una memoria para la ruta «%(ruta)s». Las rutas las "
                    "define el codigo del runtime, asi que no se pueden anadir "
                    "ni duplicar: edita la que ya existe.", ruta=etiqueta))
            raise ValidationError(_(
                "Ya hay un prompt base. Solo puede haber uno: si hubiera dos, el "
                "runtime leeria el de menor secuencia y el otro dejaria de "
                "aplicarse sin avisar. Edita el que ya existe."))

    def unlink(self):
        """No se borra: se archiva.

        Borrar una memoria de ruta no la 'apaga' —el runtime cae a la copia de
        respaldo que lleva dentro (`ROUTE_PROMPTS` en `app/prompts.py`), que es
        del dia del despliegue— y ademas se lleva por delante el texto que un
        consultor fue afinando durante meses, sin copia. Archivar hace lo mismo
        de cara al agente y deja el texto a la vista.
        """
        raise UserError(_(
            "Los prompts no se borran: son un catalogo fijo (el base y una "
            "memoria por ruta). Si quieres que una deje de aplicarse, "
            "desmarca «Activo»; el runtime usara su copia de respaldo y el "
            "texto se queda aqui por si hay que volver."))

    # ------------------------------------------------------------------
    # Lectores para el RPC. NINGUNO puede levantar (ver el `except`).
    # ------------------------------------------------------------------

    @api.model
    def _agent_route_body(self, ruta=False):
        """Cuerpo vigente de una ruta; `ruta` falsy => el PROMPT BASE.

        NUNCA levanta, y no es defensa gratuita: si esta RPC falla y el runtime
        todavia no tiene nada cacheado, `RuntimeConfigCache.refresh` re-lanza y
        el servicio no le contesta a NADIE. Degradar a None es aceptable; fallar,
        no.

        **El `savepoint` es la mitad que importa**, y se comprobo: un `try/except`
        a secas NO basta. Si la columna `ruta` no existe todavia -codigo nuevo en
        el addons_path y `-u` sin correr, que es exactamente lo que pasa entre
        desplegar y actualizar- el SELECT falla y deja la transaccion ABORTADA.
        A partir de ahi cualquier otra consulta revienta con
        `InFailedSqlTransaction`, incluida la de `visar.llm.config` que viene
        despues, y el metodo levanta igual. Con savepoint, la consulta fallida se
        deshace sola y la transaccion sigue sirviendo.

        `active_test=True` se ancla a mano: si algun dia se llega aqui desde un
        contexto que lo apago (una vista de archivados, un `odoo shell`), se
        estaria sirviendo un prompt archivado sin enterarse.
        """
        try:
            with self.env.cr.savepoint():
                record = self.with_context(active_test=True).search(
                    [('ruta', '=', ruta or False)], order='sequence, id', limit=1)
        except Exception:  # noqa: BLE001 - ver el docstring
            _logger.exception(
                "visar.agent.prompt: no se pudo leer la ruta %s", ruta)
            return None
        # `body` es required, asi que no puede venir vacio -pero SI puede venir
        # "   ", y eso significa "ninguna", no "inyecta un bloque en blanco".
        body = (record.body or '').strip() if record else ''
        return body or None

    @api.model
    def _agent_active_body(self):
        """Cuerpo del prompt BASE. Lo llama `agent_runtime_config`."""
        return self._agent_route_body(False)

    @api.model
    def _agent_route_memories(self):
        """{ruta: cuerpo} de las memorias vigentes. Una sola busqueda.

        Las rutas sin registro, archivadas o con el cuerpo en blanco quedan
        AUSENTES del dict (no presentes con None): asi el runtime distingue "no
        configurada" de "configurada y vacia" sin una rama de mas.
        """
        memorias = {}
        try:
            with self.env.cr.savepoint():
                records = self.with_context(active_test=True).search(
                    [('ruta', '!=', False)], order='sequence, id')
        except Exception:  # noqa: BLE001 - igual que `_agent_route_body`
            _logger.exception(
                "visar.agent.prompt: no se pudieron leer las memorias de ruta")
            return {}
        for record in records:
            cuerpo = (record.body or '').strip()
            if cuerpo and record.ruta not in memorias:  # gana el primero
                memorias[record.ruta] = cuerpo
        return memorias
