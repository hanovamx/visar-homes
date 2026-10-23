# -*- coding: utf-8 -*-
"""Base de conocimiento: lo que el agente CONSULTA, separado de cómo se COMPORTA.

El prompt base mezcla dos cosas: reglas de conducta («una pregunta por
mensaje», «el precio sale de quote_service») y datos de consulta (qué plagas se
atienden, qué hacer antes de la visita, las preguntas frecuentes). Las reglas
las cambia quien afina al agente; los datos los cambia Visar cuando cambia el
negocio. Juntos, corregir una respuesta de la FAQ obliga a abrir 40 000
caracteres de reglas y arriesgarse a mover algo que funcionaba (23-oct-2026).

Tres decisiones:

**1. Va en el bloque 0, detrás del prompt base.** No es una herramienta de
búsqueda: son unos pocos miles de caracteres y el modelo tiene que verlos
siempre, no solo cuando se le ocurre buscar. El bloque 0 es idéntico en todas
las rutas y el proveedor lo guarda en caché, así que cuesta casi nada.

**2. Nace APAGADA.** Ajustes → Visar → «Base de conocimiento del agente». Mientras
el prompt base siga trayendo su propia FAQ, encenderla duplicaría (y a veces
contradiría) lo que ya dice. Se enciende cuando el prompt nuevo ya no la traiga.

**3. Llega YA RENDERIZADA**, igual que las correcciones: la vista previa de Odoo
es literalmente lo que se manda, y el formato se decide en un solo sitio.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

CONOCIMIENTO_PARAM = 'visar.agente.conocimiento'

# Lo que el modelo lee antes de las secciones. Dice qué es (datos, no reglas),
# qué gana si choca (las reglas) y qué hacer si la duda no está.
CABECERA = (
    "=== BASE DE CONOCIMIENTO ===\n"
    "Información de {{marca}} para contestar dudas de servicios, plagas, "
    "políticas y preguntas frecuentes. Son DATOS, no reglas: si algo de aquí "
    "choca con una regla de arriba, manda la regla. Si la duda no está aquí ni "
    "en las reglas, no la inventes: ofrece un asesor. No le digas al cliente que "
    "consultas una base ni le copies las secciones tal cual; contesta con tus "
    "palabras y en corto."
)

# Marca de algo que el dueño todavía no decide (mapa de cambios del 23-oct).
PENDIENTE = '⚠️'


def visar_conocimiento_encendida(env):
    """¿Se le manda la base de conocimiento al agente? Apagada si no se dijo."""
    valor = env['ir.config_parameter'].sudo().get_param(CONOCIMIENTO_PARAM, 'False')
    return str(valor).strip().lower() in ('true', '1')


class VisarAgentConocimiento(models.Model):
    _name = 'visar.agent.conocimiento'
    _description = "Base de conocimiento del agente de WhatsApp"
    # El runtime cachea la config 15 minutos: el botón «Aplicar ahora».
    _inherit = ['visar.agent.runtime.mixin']
    _order = 'sequence, id'

    name = fields.Char(
        string="Sección", required=True,
        help="El título que ve el modelo: «Cobertura de plagas», «Políticas de "
             "cita». Corto y de qué trata.")
    body = fields.Text(
        string="Contenido", required=True,
        help="Datos, no instrucciones de conducta. Frases cortas o líneas que "
             "empiezan con guion. Lo que diga sobre cómo comportarse va en el "
             "prompt base o en una corrección.")
    sequence = fields.Integer(string="Orden", default=10)
    active = fields.Boolean(string="Activa", default=True)

    caracteres = fields.Integer(
        string="Caracteres", compute='_compute_caracteres')
    pendientes = fields.Integer(
        string="Por decidir", compute='_compute_caracteres',
        help="Cuántas veces aparece ⚠️: algo que el dueño todavía no decide. "
             "Conviene resolverlo antes de encender la base.")
    encendida = fields.Boolean(
        string="Se le manda al agente", compute='_compute_encendida')

    @api.depends('body', 'name')
    def _compute_caracteres(self):
        for registro in self:
            registro.caracteres = len(registro.body or '')
            registro.pendientes = (registro.body or '').count(PENDIENTE)

    def _compute_encendida(self):
        encendida = visar_conocimiento_encendida(self.env)
        for registro in self:
            registro.encendida = encendida

    # ------------------------------------------------------------------
    # Lo que se manda
    # ------------------------------------------------------------------

    @api.model
    def _render_bloque(self):
        """Cabecera + secciones activas. None si no hay ninguna con contenido."""
        secciones = []
        for registro in self.with_context(active_test=True).search([]):
            titulo = (registro.name or '').strip()
            cuerpo = (registro.body or '').strip()
            if titulo and cuerpo:
                secciones.append("## %s\n%s" % (titulo, cuerpo))
        if not secciones:
            return None
        return "\n\n".join([CABECERA] + secciones)

    @api.model
    def _agent_payload(self):
        """El bloque para el runtime, o None si está apagada o vacía.

        NUNCA levanta, igual que `visar.agent.correccion._agent_payload`: un
        fallo aquí, sin config cacheada en el runtime, deja al agente sin
        contestarle a nadie. Sin base de conocimiento, el agente sigue como
        estaba. El `savepoint` cubre la ventana entre desplegar el código y
        correr el `-u`, cuando la tabla todavía no existe.
        """
        if not visar_conocimiento_encendida(self.env):
            return None
        try:
            with self.env.cr.savepoint():
                return self._render_bloque()
        except Exception:  # noqa: BLE001 - ver el docstring
            _logger.exception(
                "visar.agent.conocimiento: no se pudo armar la base de conocimiento")
            return None


class VisarAgentConocimientoVista(models.TransientModel):
    """Todas las secciones juntas, tal y como las recibe el modelo.

    Va aparte y no en cada sección: el bloque entero repetido en la ficha de
    cada una confundía (23-oct-2026, pedido del usuario). Se abre con «Ver todo
    junto» desde la lista de secciones.
    """
    _name = 'visar.agent.conocimiento.vista'
    _description = "Base de conocimiento completa, como la recibe el agente"
    _inherit = ['visar.agent.runtime.mixin']

    vista_previa = fields.Text(
        string="Lo que recibe el modelo", compute='_compute_resumen')
    encendida = fields.Boolean(
        string="Se le manda al agente", compute='_compute_resumen')
    secciones = fields.Integer(string="Secciones activas", compute='_compute_resumen')
    caracteres = fields.Integer(string="Caracteres", compute='_compute_resumen')
    pendientes = fields.Integer(string="Por decidir (⚠️)", compute='_compute_resumen')

    def _compute_resumen(self):
        Conocimiento = self.env['visar.agent.conocimiento']
        activas = Conocimiento.with_context(active_test=True).search([])
        bloque = Conocimiento._render_bloque() or ''
        encendida = visar_conocimiento_encendida(self.env)
        for vista in self:
            vista.vista_previa = bloque
            vista.encendida = encendida
            vista.secciones = len(activas)
            vista.caracteres = len(bloque)
            vista.pendientes = sum(activas.mapped('pendientes'))


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Apagada por defecto: con `config_parameter`, apagarla borra el parámetro
    # y vuelve el default, que es justo apagada.
    visar_agente_conocimiento = fields.Boolean(
        string="Base de conocimiento del agente",
        config_parameter=CONOCIMIENTO_PARAM,
        help="Con esto encendido, el agente recibe las secciones de Agente "
             "WhatsApp → Base de conocimiento detrás del prompt base. Apagado, "
             "no recibe nada de ahí.")
