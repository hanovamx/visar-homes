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
import re

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
    "que afecte al precio. Los metros y el interior/exterior se preguntan "
    "SIEMPRE, aunque aqui aparezcan. El codigo postal solo se toma de aqui "
    "cuando el cliente ELIGE una de sus direcciones.\n"
    "- Puede estar desactualizado. Si algo importa para esta cita, CONFIRMALO en "
    "una frase corta en vez de suponerlo."
)

# Cuantas direcciones se le nombran al cliente. Mas alla de esto no se enumeran:
# en produccion hay un cliente con 31 y otro con 13, y leerle trece colonias a
# alguien que pregunto un precio es peor que no decirle ninguna.
MAX_DOMICILIOS_NOMBRADOS = 4

# ⚠️ LA SALIDA ES OBLIGATORIA. Sin el "o es una direccion nueva", el agente
# acorrala al cliente entre opciones que quizas no incluyen la que quiere: tener
# tres casas con nosotros no impide mudarse, comprar otra o pedirlo para un
# familiar. Y sin la ultima linea, un modelo pequenio da por hecha la primera de
# la lista — que es exactamente lo que el nivel "informa" existe para evitar.
#
# Desde el 9-oct-2026 cada direccion viaja con su codigo postal. Antes solo iba
# la colonia: el cliente contestaba "Contry", el agente sabia cual era pero no
# tenia con que cotizar, y le pedia la direccion entera otra vez.
VARIOS_DOMICILIOS = (
    "- Este cliente tiene VARIAS direcciones con nosotros: %(lista)s.\n"
    "- No adivines para cual es. ANTES de pedirle ninguna direccion ni codigo "
    "postal, preguntale si es para una de ellas, nombrandoselas SOLO por su "
    "colonia (no le digas el codigo postal ni la calle), y ofrece SIEMPRE la "
    "salida de que sea una direccion NUEVA.\n"
    "- Si ELIGE una de ellas, ya tienes su codigo postal: usalo tal cual en "
    "`resolve_zone` y `quote_service`. NO le pidas la direccion, ni la calle, "
    "ni el codigo postal: ya los tenemos.\n"
    "- Si es una direccion NUEVA, pidele donde es como a cualquier cliente.\n"
    "- Que tenga direcciones con nosotros no significa que esta cita sea para "
    "una de ellas. Los metros se piden igual."
)
# Con UNA sola tambien se pregunta (Visar, 9-oct-2026). Antes no se decia nada
# -"nombrarsela invitaria al modelo a darla por buena"- y el cliente de una
# sola casa, que es el caso mas comun, tenia que escribir su direccion entera
# cada vez. La salida de la direccion NUEVA es lo que impide darla por buena.
UN_DOMICILIO = (
    "- Este cliente tiene UNA direccion con nosotros: %(lista)s.\n"
    "- No des por hecho que es para ella. ANTES de pedirle ninguna direccion ni "
    "codigo postal, preguntale si el servicio es para esa direccion, "
    "nombrandosela SOLO por su colonia (no le digas el codigo postal ni la "
    "calle), o si es para una direccion NUEVA.\n"
    "- Si dice que es ESA, ya tienes su codigo postal: usalo tal cual en "
    "`resolve_zone` y `quote_service`. NO le pidas la direccion, ni la calle, "
    "ni el codigo postal: ya los tenemos.\n"
    "- Si es una direccion NUEVA, pidele donde es como a cualquier cliente.\n"
    "- Los metros se piden igual."
)
# Con muchas direcciones no se enumeran, pero el aviso sigue haciendo falta: lo
# que no puede pasar es que el agente suponga.
MUCHOS_DOMICILIOS = (
    "- Este cliente tiene %(cuantas)d direcciones con nosotros, demasiadas para "
    "listarselas.\n"
    "- Preguntale a cual de ellas es, o si es una direccion NUEVA. No supongas "
    "ninguna, y pide el codigo postal y los metros igual."
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
    def _visar_facts_render(self, cliente_facts, domicilios, cps=None):
        """El bloque tal y como lo recibe el modelo. `''` si no hay nada que decir.

        **Las direcciones se nombran aunque no sepamos NADA de ellas**, y esa es la
        correccion del 1-oct-2026: la primera version solo listaba los domicilios
        con datos, asi que un cliente con cuatro direcciones y cero facts —el 100%
        de los casos al desplegar— recibia un bloque VACIO. El valor de
        desambiguar no depende de saber algo del sitio; depende de que haya varios.
        """
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

        aviso = self._visar_facts_aviso_domicilios(domicilios, cps=cps)
        if not lineas and not aviso:
            return ''
        cuerpo = [CABECERA, ""]
        # El aviso va ARRIBA de los datos: si fuera detras, el modelo ya habria
        # leido un tipo de inmueble concreto y es justo lo que no debe dar por
        # bueno sin preguntar.
        if aviso:
            cuerpo.append(aviso)
        cuerpo += lineas
        return "\n".join(cuerpo)

    @api.model
    def _visar_facts_aviso_domicilios(self, domicilios, cps=None):
        """El aviso de las direcciones que ya tiene con nosotros, o `''`.

        Con UNA sola tambien se pregunta si es para esa o para una nueva, pero
        solo si sabemos su colonia y su codigo postal: sin CP el agente no
        tendria con que cotizar y acabaria pidiendole la direccion igual.
        """
        cps = cps or {}
        if not domicilios:
            return ''
        if len(domicilios) == 1:
            nombre = domicilios[0][0]
            if not nombre or not cps.get(nombre):
                return ''
            return UN_DOMICILIO % {'lista': "%s (CP %s)" % (nombre, cps[nombre])}
        nombres = [n for n, _f in domicilios if n]
        if len(domicilios) > MAX_DOMICILIOS_NOMBRADOS or not nombres:
            return MUCHOS_DOMICILIOS % {'cuantas': len(domicilios)}
        return VARIOS_DOMICILIOS % {'lista': ", ".join(
            "%s (CP %s)" % (n, cps[n]) if cps.get(n) else n for n in nombres)}

    @api.model
    def _visar_facts_direccion(self, domicilio):
        """La direccion de un contacto de entrega, por piezas, para el runtime.

        Es la inversa de `sale.order._visar_apply_delivery_address`, que guarda
        la calle como *"Ruiz Cortines No. 123 Int. 4"*. Si la calle no tiene esa
        forma (capturada a mano en Odoo) el numero sale vacio, y el runtime no la
        propone: el cuestionario la pide como siempre.
        """
        calle, numero, interior = (domicilio.street or '').strip(), '', ''
        hallado = re.match(r'^(.*?)\s+No\.\s+(\S+)(?:\s+Int\.\s+(.+))?$', calle)
        if hallado:
            calle, numero, interior = (
                hallado.group(1), hallado.group(2), hallado.group(3) or '')
        cp = ''.join(ch for ch in (domicilio.zip or '') if ch.isdigit())
        return {
            'street': calle.strip(), 'ext_num': numero, 'int_num': interior.strip(),
            'neighborhood': (domicilio.street2 or '').strip(),
            'zip': cp if len(cp) == 5 else '',
            'city': (domicilio.city or '').strip(),
        }

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
        domicilios, direcciones = [], []
        registros = comercial._visar_domicilios_de_servicio()
        etiquetas = [self._visar_facts_etiqueta_domicilio(d) for d in registros]
        for domicilio, nombre in zip(registros, etiquetas):
            # Dos direcciones en la misma colonia no se pueden elegir por colonia:
            # se desempatan con la calle. Sin esto el agente ofreceria dos veces
            # "Las Torres" y el cliente no podria contestar.
            if nombre and etiquetas.count(nombre) > 1 and domicilio.street:
                nombre = "%s (%s)" % (nombre, domicilio.street)
            facts = {r: v for r, v in Fact._visar_facts_de(domicilio).items()
                     if VISAR_FACT_AMBITO_DE.get(r) == 'domicilio'}
            domicilios.append((nombre, facts))
            direcciones.append(self._visar_facts_direccion(domicilio))
        cps = {n: d['zip'] for (n, _f), d in zip(domicilios, direcciones) if n}
        return {
            'block': self._visar_facts_render(cliente_facts, domicilios, cps=cps),
            'cliente': cliente_facts,
            # `direccion` es para el RUNTIME, no para el modelo: es lo que le
            # deja proponer esa direccion al agendar sin volver a pedirla.
            'domicilios': [{'nombre': n, 'facts': f, 'direccion': d}
                           for (n, f), d in zip(domicilios, direcciones)],
            'partner_id': comercial.id,
        }
