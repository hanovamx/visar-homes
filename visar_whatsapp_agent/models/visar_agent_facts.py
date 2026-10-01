# -*- coding: utf-8 -*-
"""Lo que el agente sabe de quien le escribe, y lo que NO puede hacer con eso.

Lee `visar.partner.fact` del cliente y de **cada uno** de sus domicilios, y lo
devuelve ya renderizado. Que el bloque se arme aquí y no en el runtime es la misma
decisión que con las Correcciones: el texto que gobierna la conducta del modelo
vive en UN repositorio, y la regla de nivel va pegada a los datos en vez de en un
`.py` del otro lado.

## El nivel va en el bloque, no en un comentario

El bloque le dice al modelo, en su cara, que esto **informa** y no contesta. No es
cortesía: el cuestionario pregunta el domicilio TARDE, así que el agente ni sabe
de qué dirección se trata cuando tendría que pre-rellenar; y una entrada de precio
contestada con un dato viejo acaba en una cita cobrada mal. La memoria de la ruta
de información ya lo dice del exterior: *"Nunca supongas que es solo interior: es
un dato de precio."*

Lo que SÍ gana: con varios domicilios el agente **pregunta cuál**, en vez de
suponer. Resuelve la ambigüedad en un mensaje en lugar de arrastrarla hasta el
precio.

## Privacidad

Si el teléfono coincide con más de un cliente **no se manda nada**. Es la política
de `_agent_find_partner` y `_agent_booking_partner`, y aquí pesa más que en
ningún sitio: un fact equivocado no es un dato que falta, es el agente hablándole
a alguien de la casa de otra persona.
"""
import logging

from odoo import api, models

from odoo.addons.visar_base.models.visar_partner_fact import (
    VISAR_FACT_AMBITO_DE,
    VISAR_FACT_ETIQUETA_DE,
)

_logger = logging.getLogger(__name__)

CABECERA = (
    "=== LO QUE YA SABEMOS DE ESTE CLIENTE ===\n"
    "Contexto de conversaciones y visitas anteriores. Sirve para hablarle como a "
    "alguien que ya conoces.\n"
    "REGLAS, y no son negociables:\n"
    "- NO contestes ningun paso del cuestionario con esto, ni des por sabido nada "
    "que afecte al precio. Los metros, el interior/exterior y el codigo postal se "
    "preguntan SIEMPRE, aunque aqui aparezcan.\n"
    "- Puede estar desactualizado. Si algo importa para esta cita, CONFIRMALO en "
    "una frase corta en vez de suponerlo."
)

VARIOS_DOMICILIOS = (
    "- Este cliente tiene VARIAS direcciones con nosotros. No adivines para cual "
    "es: preguntale cual, nombrandolas por su colonia o su calle."
)


class VisarAgentFacts(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    @api.model
    def _visar_facts_etiqueta_domicilio(self, domicilio):
        """Como nombrar una direccion en el chat: por colonia o por calle.

        Nunca la direccion completa: el modelo la repetiria tal cual y leerle su
        calle y su numero a alguien que solo pregunto un precio suena a vigilancia,
        no a servicio.
        """
        return (domicilio.street2 or domicilio.street
                or domicilio.city or domicilio.display_name or '').strip()

    @api.model
    def _visar_facts_render(self, cliente_facts, domicilios):
        """El bloque tal y como lo recibe el modelo. `''` si no hay nada que decir."""
        lineas = []
        for ranura, valor in sorted(cliente_facts.items()):
            lineas.append("- %s: %s" % (
                VISAR_FACT_ETIQUETA_DE.get(ranura, ranura), valor))
        for nombre, facts in domicilios:
            if not facts:
                continue
            detalle = "; ".join(
                "%s: %s" % (VISAR_FACT_ETIQUETA_DE.get(r, r), v)
                for r, v in sorted(facts.items()))
            lineas.append("- Direccion «%s» -> %s" % (nombre, detalle))
        if not lineas:
            return ''
        cuerpo = [CABECERA, ""]
        # El aviso de varias direcciones va ARRIBA de los datos: si fuera detras,
        # el modelo ya habria leido un tipo de inmueble concreto y es justo lo que
        # no debe dar por bueno.
        if len([d for d in domicilios if d[1]]) > 1:
            cuerpo.append(VARIOS_DOMICILIOS)
        cuerpo += lineas
        return "\n".join(cuerpo)

    # ------------------------------------------------------------------
    # El RPC
    # ------------------------------------------------------------------

    @api.model
    def agent_partner_facts(self, payload):
        """Lo que sabemos de quien escribe. Solo LECTURA, nunca levanta.

        `payload` = {"phone": "5218112345678"}

        Devuelve:
            {"block": str,                  # '' = no hay nada que inyectar
             "cliente": {ranura: valor},
             "domicilios": [{"nombre": str, "facts": {ranura: valor}}],
             "partner_id": int|None}

        NUNCA levanta: lo llama el runtime al empezar una conversacion, y un dato
        de contexto no puede costarle la respuesta a nadie. Ante cualquier
        problema devuelve el bloque vacio, que deja al agente exactamente como
        antes de que esto existiera.
        """
        vacio = {'block': '', 'cliente': {}, 'domicilios': [], 'partner_id': None}
        try:
            with self.env.cr.savepoint():
                return self._agent_partner_facts_ahora(payload)
        except Exception:  # noqa: BLE001 - ver el docstring
            _logger.exception(
                "agent_partner_facts: no se pudo leer el contexto de %r",
                (payload or {}).get('phone'))
            return vacio

    @api.model
    def _agent_partner_facts_ahora(self, payload):
        """El trabajo de `agent_partner_facts`, sin la red de seguridad."""
        payload = payload or {}
        vacio = {'block': '', 'cliente': {}, 'domicilios': [], 'partner_id': None}
        # `_agent_find_partner` ya aplica la politica de ambiguedad: mas de un
        # cliente con el mismo numero -> recordset vacio. No se repite aqui.
        partner = self._agent_find_partner(payload.get('phone'))
        if not partner:
            return vacio
        Fact = self.env['visar.partner.fact'].sudo()
        comercial = partner.commercial_partner_id or partner
        cliente_facts = {
            r: v for r, v in Fact._visar_facts_de(comercial).items()
            if VISAR_FACT_AMBITO_DE.get(r) == 'cliente'}
        domicilios = []
        for domicilio in comercial._visar_domicilios_de_servicio():
            facts = {r: v for r, v in Fact._visar_facts_de(domicilio).items()
                     if VISAR_FACT_AMBITO_DE.get(r) == 'domicilio'}
            domicilios.append(
                (self._visar_facts_etiqueta_domicilio(domicilio), facts))
        return {
            'block': self._visar_facts_render(cliente_facts, domicilios),
            'cliente': cliente_facts,
            'domicilios': [{'nombre': n, 'facts': f} for n, f in domicilios],
            'partner_id': comercial.id,
        }
