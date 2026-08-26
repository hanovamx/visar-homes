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

from odoo import api, fields, models

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


class VisarAgentPrompt(models.Model):
    _name = 'visar.agent.prompt'
    _description = "Prompt del agente de WhatsApp"
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
    ruta = fields.Selection(
        ROUTES, string="Ruta", index=True,
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
