# -*- coding: utf-8 -*-
"""Correcciones: arreglos de conducta que ganan al prompt, sin tocar el prompt.

Son las cosas pequenas que se ven en produccion y hay que quitar ya —*"no
saludes con Holi"*, *"no digas «estimado cliente»"*—. Escribirlas en el prompt
base (38 000 caracteres) significa releerlo, encontrar el sitio, y arriesgarse a
mover algo que funcionaba; y muchas veces el comportamiento **lo causa** el
propio prompt, asi que no hay una frase que borrar: hay que contradecirla.

Esto es esa contradiccion, con tres decisiones que la hacen funcionar con un
modelo pequenio:

**1. Van al FINAL, y eso es posicion, no retorica.** El bloque 0 (base +
catalogo) tiene que ser identico byte a byte en todas las rutas porque es el
unico que el proveedor marca para cache; todo lo que cambia va detras. Resulta
que detras es tambien donde la recencia ayuda mas. Dos razones distintas, misma
respuesta.

**2. El modelo NUNCA ve el ambito.** Las correcciones de otras rutas no se le
mandan: se filtran aqui, y a la conversacion solo llega la lista que aplica.
Explicarle a un modelo pequenio que ignore la mitad de una lista es pedirle lo
contrario de lo que se quiere.

**3. Hay un TOPE, y es el mecanismo, no un adorno.** Quince correcciones siguen
leyendose como excepciones; cuarenta son otro prompt, y ahi degradan todas,
incluidas las que ya funcionaban. Al llegar al tope hay que retirar una, y
retirar una es lo que obliga a preguntarse por que sigue ahi despues de tres
meses. Sin el tope, esto se convierte en un segundo prompt que no mantiene
nadie.

**Lo que esto NO es:** una garantia. Una correccion que contradice un prompt de
38 000 caracteres la sigue un modelo pequenio *casi* siempre, no siempre. Cuando
una correccion lleva tiempo y no puede fallar, su sitio es el prompt base o la
memoria de su ruta. Esta pantalla es la sala de espera, no el destino.

**El bloque lo renderiza Odoo, no el runtime**, y viaja ya montado por ruta
—`{ruta: bloque}`, espejo exacto de `route_prompts`—. Asi la cabecera de
precedencia existe en UN solo sitio y la vista previa es literalmente lo que se
manda. Repartirla entre los dos repos seria el problema de "dos front-ends" que
este proyecto ya se cobro una vez (I-11).
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.visar_whatsapp_agent.models.visar_agent_prompt import ROUTES

_logger = logging.getLogger(__name__)

# Cuantas correcciones activas se admiten a la vez. Ver la decision 3 del
# docstring: el numero importa menos que el hecho de que exista un muro.
MAX_ACTIVAS = 15

# La cabecera. Dice tres cosas y ninguna sobra: que son posteriores (por eso
# ganan), que ganan si contradicen, y que no se mencionan al cliente —sin esa
# ultima linea, un modelo pequenio contesta "tienes razon, no debo decir Holi".
CABECERA = (
    "=== CORRECCIONES VIGENTES ===\n"
    "Estas reglas se escribieron DESPUES de todo lo anterior y corrigen errores "
    "observados en conversaciones reales. Si alguna contradice algo dicho mas "
    "arriba, GANA ESTA. No las menciones ni las expliques al cliente: aplicalas "
    "y ya."
)

AMBITOS = [
    ('global', "Todas las rutas"),
    ('ruta', "Solo una ruta"),
]


class VisarAgentCorreccion(models.Model):
    _name = 'visar.agent.correccion'
    _description = "Correccion de conducta del agente de WhatsApp"
    # El runtime cachea la config 15 minutos: sin el boton de aplicar, escribir
    # una correccion y probarla en el chat es un cuarto de hora de duda.
    _inherit = ['visar.agent.runtime.mixin']
    _order = 'sequence, id'

    texto = fields.Char(
        string="Correccion", required=True,
        help="UNA instruccion, en una linea, en imperativo. Mejor decir que "
             "hacer que solo que no hacer: «En vez de «Holi», saluda con "
             "«Hola»» funciona mejor que «No digas «Holi»», porque una "
             "prohibicion a secas deja al modelo sin alternativa.")
    motivo = fields.Text(
        string="Que se observo",
        help="Opcional. La conversacion o el comportamiento que motivo esta "
             "correccion. No se le manda al agente: es para quien la lea "
             "dentro de tres meses y tenga que decidir si ya sobra.")
    ambito = fields.Selection(
        AMBITOS, string="Se aplica en", required=True, default='global',
        help="Las de una sola ruta no se le mandan al modelo en las demas: "
             "ni las ve.")
    ruta = fields.Selection(
        ROUTES, string="Ruta",
        help="Solo cuando el ambito es «Solo una ruta».")
    # Por defecto NO entra en el cuestionario, y no es prudencia de mas: las
    # reglas de la ruta `schedule` son de carga -"no contestes el paso", "no
    # termines con una pregunta"- y una correccion global bienintencionada las
    # rompe. El caso real: alguien escribe "termina preguntando si necesita algo
    # mas" para arreglar una queja de sequedad, y dentro del cuestionario el
    # agente pregunta justo encima de la pregunta del sistema. Es exactamente el
    # fallo de la doble pregunta que la memoria de esa ruta dedica tres parrafos
    # a evitar.
    en_cuestionario = fields.Boolean(
        string="Tambien durante el agendado", default=False,
        help="Por defecto NO. Dentro del cuestionario hay reglas que sostienen "
             "el flujo (no contestar el paso, no terminar preguntando) y una "
             "correccion general puede romperlas sin querer. Marcalo solo si "
             "esta correccion tiene sentido ahi.")
    sequence = fields.Integer(
        string="Orden", default=10,
        help="El orden en que se le presentan al modelo. Solo importa para "
             "leerlas; no hay prioridad entre ellas.")
    active = fields.Boolean(string="Activa", default=True)

    dias = fields.Integer(
        string="Dias activa", compute='_compute_dias',
        help="Cuanto lleva puesta. Una correccion vieja es senal de que su "
             "sitio ya es el prompt base o la memoria de su ruta.")
    rutas_donde_aplica = fields.Char(
        string="Rutas", compute='_compute_rutas_donde_aplica')
    vista_previa = fields.Text(
        string="Lo que recibe el modelo", compute='_compute_vista_previa',
        help="El bloque completo, tal y como se le manda. Incluye las demas "
             "correcciones que comparten ruta con esta.")

    # ------------------------------------------------------------------
    # Calculados
    # ------------------------------------------------------------------

    @api.depends('create_date')
    def _compute_dias(self):
        hoy = fields.Date.context_today(self)
        for registro in self:
            if registro.create_date:
                registro.dias = (hoy - registro.create_date.date()).days
            else:
                registro.dias = 0

    @api.depends('ambito', 'ruta', 'en_cuestionario')
    def _compute_rutas_donde_aplica(self):
        etiquetas = dict(ROUTES)
        for registro in self:
            registro.rutas_donde_aplica = ', '.join(
                etiquetas.get(r, r) for r in registro._rutas()) or _("ninguna")

    @api.depends('texto', 'ambito', 'ruta', 'en_cuestionario', 'active', 'sequence')
    def _compute_vista_previa(self):
        """El bloque de verdad, no una aproximacion.

        Pasa por el MISMO `_render` que el payload: si algun dia cambia la
        cabecera o el formato, esta pantalla cambia con el o no sirve de nada.

        Se arma a mano en vez de llamar a `_agent_payload` porque un registro
        que todavia no se ha guardado -o que se acaba de editar sin guardar- no
        esta en la busqueda, y la vista previa se ensenaria SIN la correccion
        que se esta escribiendo. Es el unico momento en que alguien la mira.
        """
        for registro in self:
            rutas = registro._rutas()
            if not rutas or not registro.active or not (registro.texto or '').strip():
                registro.vista_previa = _(
                    "Tal y como esta, esta correccion no se le manda al modelo "
                    "en ninguna ruta.")
                continue
            # La primera ruta donde aplica basta: el texto es el mismo en
            # todas, solo cambia la compania.
            ruta = rutas[0]
            hermanas = self.search([('id', '!=', registro._origin.id or 0)])
            textos = []
            for otra in hermanas:
                texto = (otra.texto or '').strip()
                if texto and ruta in otra._rutas():
                    textos.append((otra.sequence, otra.id, texto))
            textos.append((registro.sequence, registro._origin.id or 0,
                           registro.texto.strip()))
            textos.sort(key=lambda t: (t[0], t[1]))
            registro.vista_previa = self._render([t[2] for t in textos])

    # ------------------------------------------------------------------
    # Reglas
    # ------------------------------------------------------------------

    def _rutas(self):
        """Las rutas donde esta correccion se le manda al modelo."""
        self.ensure_one()
        if self.ambito == 'ruta':
            return [self.ruta] if self.ruta else []
        rutas = [code for code, _label in ROUTES]
        if not self.en_cuestionario:
            rutas = [r for r in rutas if r != 'schedule']
        return rutas

    @api.constrains('texto')
    def _check_una_linea(self):
        """Una correccion, una linea.

        No es estetica: un parrafo dentro de una lista numerada deja de leerse
        como una regla y empieza a leerse como prosa, que es justo lo que el
        prompt base ya es. Si hace falta un parrafo, el sitio es el prompt.
        """
        for registro in self:
            if '\n' in (registro.texto or ''):
                raise ValidationError(_(
                    "Una correccion es UNA linea. Si necesitas varias, son "
                    "varias correcciones —o, si de verdad hace falta un "
                    "parrafo, su sitio es el prompt base."))

    @api.constrains('ambito', 'ruta')
    def _check_ruta(self):
        for registro in self:
            if registro.ambito == 'ruta' and not registro.ruta:
                raise ValidationError(_(
                    "Elige la ruta, o pon el ambito en «Todas las rutas»."))

    @api.constrains('active')
    def _check_tope(self):
        """El muro. Ver la decision 3 del docstring del modulo."""
        if not any(self.mapped('active')):
            return
        activas = self.search_count([])
        if activas > MAX_ACTIVAS:
            raise ValidationError(_(
                "Ya hay %(tope)s correcciones activas, que es el maximo.\n\n"
                "No es un limite tecnico: a partir de ahi dejan de leerse como "
                "excepciones y empiezan a degradar a las que ya funcionaban. "
                "Si esta hace falta, retira otra —y si ninguna sobra, es que "
                "el arreglo ya no es una correccion: toca editar el prompt "
                "base o la memoria de su ruta.",
                tope=MAX_ACTIVAS))

    # ------------------------------------------------------------------
    # Lector del RPC. NO puede levantar.
    # ------------------------------------------------------------------

    @api.model
    def _render(self, textos):
        """Cabecera + lista numerada. El unico sitio donde se decide el formato."""
        lineas = [CABECERA, ""]
        lineas += ["%d. %s" % (i, t) for i, t in enumerate(textos, 1)]
        return "\n".join(lineas)

    @api.model
    def _agent_payload(self):
        """{ruta: bloque} de las correcciones vigentes. Solo rutas con alguna.

        NUNCA levanta, por la misma razon que los lectores de
        `visar.agent.prompt`: si esto falla y el runtime no tiene nada cacheado,
        `RuntimeConfigCache.refresh` re-lanza y el servicio deja de contestarle
        a todo el mundo. Degradar a {} es aceptable; fallar, no.

        El `savepoint` es la mitad que importa: si la tabla no existe todavia
        -codigo nuevo en el addons_path y `-u` sin correr, que es exactamente la
        ventana entre desplegar y actualizar- el SELECT deja la transaccion
        ABORTADA y revienta todo lo que venga despues, incluido el resto de
        `agent_runtime_config`.
        """
        por_ruta = {}
        try:
            with self.env.cr.savepoint():
                registros = self.with_context(active_test=True).search([])
        except Exception:  # noqa: BLE001 - ver el docstring
            _logger.exception(
                "visar.agent.correccion: no se pudieron leer las correcciones")
            return {}
        for registro in registros:
            texto = (registro.texto or '').strip()
            if not texto:
                continue
            for ruta in registro._rutas():
                por_ruta.setdefault(ruta, []).append(texto)
        return {ruta: self._render(textos) for ruta, textos in por_ruta.items()}
