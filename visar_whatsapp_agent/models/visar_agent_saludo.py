# -*- coding: utf-8 -*-
"""El saludo del agente, según quién escribe.

Hasta el 13-oct-2026 todo el que abría una conversación recibía el mismo saludo:
"¡Hola! Soy el asistente de …" y las cuatro opciones. Como la conversación caduca
a las 3 horas, un cliente de años lo leía igual que un desconocido, cada vez.

Ahora hay tres niveles, y **los botones no cambian en ninguno** — eso fue una
decisión del usuario, no un descuido: sin el menú, la intención del primer
mensaje la vuelve a adivinar el modelo, que el 3-sep acertaba 2 de 6 «quiero
agendar». Lo único que cambia es el TEXTO que va encima.

  * **nuevo** — nunca había escrito y no es cliente. El runtime pinta su saludo
    de siempre; aquí se devuelve el texto vacío.
  * **conocido** — ya había escrito, o es un cliente (aunque sea su primer
    mensaje: quien reservó por la web ya tiene nombre y servicios con nosotros).
  * **suscriptor** — tiene una suscripción en curso **y una visita con fecha que
    ya conoce**. Se le dice cuál.

## Lo que el saludo NO dice

**Una pre-agenda sin confirmar.** El cron le pone fecha a la visita siguiente,
pero el cliente no lo sabe hasta que alguien se lo confirma
(`visar_visit_preagenda_confirmada`). Anunciársela en un saludo sería darle la
noticia por accidente, y con la fecha todavía sin acordar.

**«¿Quieres agendar tu visita?»** Se pensó y se quitó: el cliente NO puede
agendar una visita de suscripción por el chat —«Agendar» abre una compra
nueva—, así que el saludo estaría ofreciendo algo que no existe. Un suscriptor
sin visita fechada recibe el saludo de conocido.

## Por qué el texto se arma aquí

La misma razón que el bloque de facts y las Correcciones: lo que se le dice al
cliente vive en UN repositorio y se edita en UNA pantalla (Ajustes → Visar). El
runtime recibe el texto ya hecho y solo le pone los botones.

Viaja dentro de `agent_partner_facts`, que el runtime ya llama una vez por
conversación: no hay viaje extra a Odoo.
"""
import logging
import re

from odoo import api, fields, models
from odoo.tools import format_datetime

from odoo.addons.visar_base.models.res_config_settings import visar_con_marca
from odoo.addons.visar_whatsapp_agent.models.visar_agent_tools import (
    DEFAULT_TZ,
    SERVICES_LANG,
    TZ_PARAM,
)

_logger = logging.getLogger(__name__)

SALUDO_ENABLED_PARAM = 'visar.agent.saludo_enabled'
SALUDO_CONOCIDO_PARAM = 'visar.agent.saludo_conocido'
SALUDO_SUSCRIPTOR_PARAM = 'visar.agent.saludo_suscriptor'

SALUDO_CONOCIDO_DEFAULT = "¡Hola de nuevo, {{nombre}}! ¿Cómo te puedo ayudar?"
SALUDO_SUSCRIPTOR_DEFAULT = (
    "¡Hola, {{nombre}}! Tu próxima visita en {{colonia}} es el {{fecha}}. "
    "¿Cómo te puedo ayudar?")

NIVEL_NUEVO = 'nuevo'
NIVEL_CONOCIDO = 'conocido'
NIVEL_SUSCRIPTOR = 'suscriptor'

# El cuerpo de un mensaje con lista admite 1024 caracteres en WhatsApp. Un saludo
# que se acerque a eso no es un saludo; se recorta para que el mensaje LLEGUE.
MAX_SALUDO = 600


def _sin_marcador(texto, marcador, patron):
    """Quita un marcador SIN valor junto con la palabra que lo introduce.

    «¡Hola de nuevo, {{nombre}}!» sin nombre es «¡Hola de nuevo!», no «¡Hola de
    nuevo, !». Lo que no case con el patrón se queda sin el marcador y nada más.
    """
    texto = re.sub(patron, '', texto)
    return texto.replace(marcador, '')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_agent_saludo_enabled = fields.Boolean(
        string="Saludar según el cliente",
        config_parameter=SALUDO_ENABLED_PARAM,
        help="Apagado, todos reciben el saludo de siempre. Encendido, quien ya "
             "había escrito o ya es cliente recibe el suyo, y quien tiene una "
             "suscripción con visita agendada, la fecha de esa visita. Los "
             "botones son los mismos para todos.")
    visar_agent_saludo_conocido = fields.Char(
        string="Saludo a quien ya nos conoce",
        config_parameter=SALUDO_CONOCIDO_PARAM,
        default=SALUDO_CONOCIDO_DEFAULT,
        help="{{nombre}} es el nombre de pila del cliente; si no lo sabemos se "
             "quita junto con su coma. {{marca}} es el nombre de la marca.")
    visar_agent_saludo_suscriptor = fields.Char(
        string="Saludo a quien tiene suscripción",
        config_parameter=SALUDO_SUSCRIPTOR_PARAM,
        default=SALUDO_SUSCRIPTOR_DEFAULT,
        help="Solo se usa si el cliente tiene una visita con fecha que ya "
             "conoce. {{fecha}} es el día de esa visita y {{colonia}} dónde es. "
             "Sin {{fecha}} en el texto se usa el de fábrica.")


class VisarAgentSaludo(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    # ------------------------------------------------------------------
    # El RPC: el saludo viaja con el contexto del cliente
    # ------------------------------------------------------------------

    @api.model
    def agent_partner_facts(self, payload):
        """Lo de siempre, más `saludo` = {"nivel": str, "texto": str}.

        `texto` vacío significa "pinta tu saludo de siempre": es lo que recibe
        un desconocido, lo que se manda con el ajuste apagado y lo que queda si
        algo falla. Un runtime viejo ignora la clave.
        """
        datos = super().agent_partner_facts(payload)
        datos['saludo'] = self._agent_saludo(
            (payload or {}).get('phone'), datos.get('partner_id'))
        return datos

    @api.model
    def _agent_saludo(self, phone, partner_id=None):
        """El saludo para este teléfono. NUNCA levanta."""
        nuevo = {'nivel': NIVEL_NUEVO, 'texto': ''}
        try:
            with self.env.cr.savepoint():
                return self._agent_saludo_ahora(phone, partner_id)
        except Exception:  # noqa: BLE001 - un saludo no le cuesta la respuesta a nadie
            _logger.exception(
                "agent_partner_facts: no se pudo armar el saludo de %r", phone)
            return nuevo

    @api.model
    def _agent_saludo_ahora(self, phone, partner_id=None):
        """El trabajo de `_agent_saludo`, sin la red de seguridad."""
        nuevo = {'nivel': NIVEL_NUEVO, 'texto': ''}
        params = self.env['ir.config_parameter'].sudo()
        if not params.get_param(SALUDO_ENABLED_PARAM):
            return nuevo
        partner = self.env['res.partner'].sudo().browse(partner_id or [])
        if not partner and not self._agent_ya_habia_escrito(phone):
            return nuevo

        nombre = self._agent_saludo_nombre(partner)
        visita = self._agent_saludo_visita(partner) if partner else None
        if visita:
            plantilla = (params.get_param(SALUDO_SUSCRIPTOR_PARAM) or '').strip()
            # Sin {{fecha}} el texto ya no dice lo que este nivel existe para
            # decir, y mandarlo seria saludar como suscriptor sin serlo.
            if '{{fecha}}' not in plantilla:
                plantilla = SALUDO_SUSCRIPTOR_DEFAULT
            texto = plantilla.replace('{{fecha}}', visita['fecha'])
            if visita['colonia']:
                texto = texto.replace('{{colonia}}', visita['colonia'])
            else:
                texto = _sin_marcador(
                    texto, '{{colonia}}', r'\s+en\s+\{\{colonia\}\}')
            nivel = NIVEL_SUSCRIPTOR
        else:
            texto = ((params.get_param(SALUDO_CONOCIDO_PARAM) or '').strip()
                     or SALUDO_CONOCIDO_DEFAULT)
            nivel = NIVEL_CONOCIDO

        if nombre:
            texto = texto.replace('{{nombre}}', nombre)
        else:
            texto = _sin_marcador(texto, '{{nombre}}', r',?\s*\{\{nombre\}\}')
        # Los marcadores de suscriptor no significan nada en el de conocido: si
        # alguien los escribio ahi, se van en vez de llegarle al cliente.
        for marcador, patron in (('{{fecha}}', r'\s+el\s+\{\{fecha\}\}'),
                                 ('{{colonia}}', r'\s+en\s+\{\{colonia\}\}')):
            texto = _sin_marcador(texto, marcador, patron)
        texto = (visar_con_marca(self.env, texto) or '').strip()[:MAX_SALUDO]
        if not texto:
            return nuevo
        return {'nivel': nivel, 'texto': texto}

    # ------------------------------------------------------------------
    # Las tres preguntas
    # ------------------------------------------------------------------

    @api.model
    def _agent_ya_habia_escrito(self, phone):
        """¿Hay constancia de un mensaje ANTERIOR de este teléfono?

        ⚠️ El runtime tiene que preguntar esto ANTES de anotar el mensaje que
        acaba de llegar (`agent_track_inbound`): después, todo el mundo «ya
        había escrito». Ese orden lo cuida `VisarAgent.handle_message`.
        """
        nat = self.env['res.partner']._visar_phone_nat10_value(phone) or ''
        if not nat:
            return False
        return bool(self.env['visar.agent.contacto'].sudo().with_context(
            active_test=False).search_count(
                [('name', '=', nat), ('mensajes', '>', 0)], limit=1))

    @api.model
    def _agent_saludo_nombre(self, partner):
        """El nombre de pila, o '' si no hay uno que se pueda decir.

        Una empresa no tiene nombre de pila: «¡Hola de nuevo, Restaurante!» es
        peor que no decir ninguno.
        """
        if not partner or partner.is_company:
            return ''
        piezas = (partner.name or '').split()
        primero = piezas[0] if piezas else ''
        # Un telefono o un correo guardado como nombre no se le dice a nadie.
        if not primero or not primero.replace('.', '').isalpha():
            return ''
        return primero[:1].upper() + primero[1:].lower() \
            if primero.isupper() or primero.islower() else primero

    @api.model
    def _agent_saludo_visita(self, partner):
        """La próxima visita de suscripción que el cliente YA conoce, o None.

        Devuelve {"fecha": "22 de octubre", "colonia": str}.

        Tres filtros, y los tres son la diferencia entre un saludo útil y uno
        que mete la pata:

          * la suscripción está **en curso** (`3_progress`);
          * la visita tiene fecha de hoy en adelante y no está cerrada;
          * **no es una pre-agenda sin confirmar** (ver el docstring del módulo).
        """
        Task = self.env['project.task'].sudo()
        if 'visar_subscription_order_id' not in Task._fields:
            return None
        comercial = partner.commercial_partner_id or partner
        polizas = self.env['sale.order'].sudo().search([
            ('partner_id', 'child_of', comercial.id),
            ('is_subscription', '=', True),
            ('subscription_state', '=', '3_progress'),
        ])
        if not polizas:
            return None
        hoy = fields.Datetime.start_of(fields.Datetime.now(), 'day')
        visita = Task.search([
            '|', ('visar_subscription_order_id', 'in', polizas.ids),
            ('sale_line_id.order_id', 'in', polizas.ids),
            ('project_id.is_fsm', '=', True),
            ('planned_date_begin', '>=', hoy),
            ('stage_id.fold', '=', False),
            '|', ('visar_visit_preagendada', '=', False),
            ('visar_visit_preagenda_confirmada', '=', True),
        ], order='planned_date_begin asc', limit=1)
        if not visita:
            return None
        # Mismo huso e idioma que la lista de servicios: la fecha la lee un
        # cliente, y las 18:00 de Monterrey ya son el dia siguiente en UTC.
        tz = self.env['ir.config_parameter'].sudo().get_param(TZ_PARAM, DEFAULT_TZ)
        poliza = visita.visar_subscription_order_id or visita.sale_line_id.order_id
        domicilio = poliza.visar_service_partner_id \
            if 'visar_service_partner_id' in poliza._fields else False
        return {
            # Solo el DIA. La hora es una ventana y vive en «Servicio
            # existente»; en un saludo, prometer una hora es prometer de más.
            'fecha': format_datetime(
                self.env, visita.planned_date_begin, tz=tz,
                dt_format="d 'de' MMMM", lang_code=SERVICES_LANG),
            # Solo la COLONIA, nunca la calle: es la regla del bloque de facts.
            'colonia': (domicilio.street2 or '').strip() if domicilio else '',
        }
