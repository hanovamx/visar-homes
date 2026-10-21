# -*- coding: utf-8 -*-
"""Escalamiento silencioso: una persona toma la charla de WhatsApp (20-oct-2026).

Lo que pidio Visar: al escalar, el cliente lee "Permíteme un momento" (no que lo
va a contactar un asesor), el agente deja de contestarle, y una persona sigue la
MISMA charla desde la app WhatsApp Business. El numero esta en coexistencia
(app + API a la vez), asi que Meta le avisa al runtime de cada mensaje que la
persona manda desde la app y el agente sigue callado.

Aqui vive lo que Visar cambia sin desplegar (Ajustes → Visar), lo que el runtime
lee en `agent_runtime_config['handoff']`, y el estado por contacto que se ve en
Agente WhatsApp → Contactos con su boton «Devolver al agente».

Nace **apagado**: con el interruptor apagado el agente escala como siempre.
"""
import logging

import requests

from odoo import _, api, fields, models
from odoo.addons.visar_base.models.wa_outbox_mixin import (
    BASE_URL_PARAM,
    DEFAULT_BASE_URL,
    SEND_TIMEOUT,
    TOKEN_PARAM,
)
from odoo.exceptions import UserError, ValidationError

from odoo.addons.visar_appointment.models.appointment_wizard_flow import (
    COBERTURA_OFERTA_DEFAULT,
    COBERTURA_OFERTA_PARAM,
)

from .visar_agent_tools import DEFAULT_TZ, TZ_PARAM

_logger = logging.getLogger(__name__)

SILENCIOSO_PARAM = 'visar.handoff.silencioso'
USER_PARAM = 'visar.handoff.user_id'
DIAS_PARAM = 'visar.handoff.dias'
DIAS_DEFAULT = 'ls'
DESDE_PARAM = 'visar.handoff.hora_desde'
DESDE_DEFAULT = 9
HASTA_PARAM = 'visar.handoff.hora_hasta'
HASTA_DEFAULT = 19
ESPERA_PARAM = 'visar.handoff.espera_min'
ESPERA_DEFAULT = 15
DEVOLVER_PARAM = 'visar.handoff.devolver_h'
DEVOLVER_DEFAULT = 12
# Urgente sin horario cercano (21-oct-2026): se escala si lo primero libre cae
# mas de estos dias despues del primer dia que permite la anticipacion minima.
URGENTE_DIAS_PARAM = 'visar.handoff.urgente_dias'
URGENTE_DIAS_DEFAULT = 1
# Al reves a proposito: un booleano de Ajustes con default True no se puede
# guardar apagado (apagarlo BORRA el parametro y vuelve el default). Lo que
# decidio el usuario (20-oct) es el caso por omision: si la persona escribe
# primero desde la app, el agente se aparta.
NO_APARTA_PARAM = 'visar.handoff.persona_no_aparta'
TEXTO_ESPERA_PARAM = 'visar.handoff.texto_espera'
TEXTO_ESPERA_DEFAULT = "Permíteme un momento."
TEXTO_RESPALDO_PARAM = 'visar.handoff.texto_respaldo'
TEXTO_RESPALDO_DEFAULT = "Gracias por esperar. En breve te respondemos por aquí."
TEXTO_FUERA_PARAM = 'visar.handoff.texto_fuera'
TEXTO_FUERA_DEFAULT = "Te respondemos por aquí {cuando}."

DIAS = [
    ('lv', "Lunes a viernes"),
    ('ls', "Lunes a sábado"),
    ('ld', "Todos los días"),
]
DIAS_SEMANA = {'lv': [0, 1, 2, 3, 4], 'ls': [0, 1, 2, 3, 4, 5],
               'ld': [0, 1, 2, 3, 4, 5, 6]}


def _entero(params, clave, default):
    try:
        return int(params.get_param(clave, default))
    except (TypeError, ValueError):
        return default


def visar_handoff_user(env):
    """La persona a cargo de los escalamientos, o un recordset vacio."""
    raw = env['ir.config_parameter'].sudo().get_param(USER_PARAM)
    try:
        user = env['res.users'].sudo().browse(int(raw)).exists() if raw else None
    except (TypeError, ValueError):
        user = None
    if not user or not user.active or user.share:
        return env['res.users'].browse()
    return user


def visar_handoff_config(env):
    """Lo que viaja al runtime en `agent_runtime_config['handoff']`."""
    params = env['ir.config_parameter'].sudo()
    dias = params.get_param(DIAS_PARAM, DIAS_DEFAULT)
    return {
        'silencioso': params.get_param(SILENCIOSO_PARAM, 'False') == 'True',
        'dias': DIAS_SEMANA.get(dias, DIAS_SEMANA[DIAS_DEFAULT]),
        'desde': _entero(params, DESDE_PARAM, DESDE_DEFAULT),
        'hasta': _entero(params, HASTA_PARAM, HASTA_DEFAULT),
        'tz': params.get_param(TZ_PARAM) or DEFAULT_TZ,
        'espera_min': _entero(params, ESPERA_PARAM, ESPERA_DEFAULT),
        'devolver_h': _entero(params, DEVOLVER_PARAM, DEVOLVER_DEFAULT),
        'urgente_dias': _entero(params, URGENTE_DIAS_PARAM, URGENTE_DIAS_DEFAULT),
        'persona_primero': params.get_param(NO_APARTA_PARAM, 'False') != 'True',
        'texto_espera': (params.get_param(TEXTO_ESPERA_PARAM) or '').strip()
                        or TEXTO_ESPERA_DEFAULT,
        'texto_respaldo': (params.get_param(TEXTO_RESPALDO_PARAM) or '').strip()
                          or TEXTO_RESPALDO_DEFAULT,
        'texto_fuera': (params.get_param(TEXTO_FUERA_PARAM) or '').strip()
                       or TEXTO_FUERA_DEFAULT,
    }


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_handoff_silencioso = fields.Boolean(
        string="Escalar en silencio",
        config_parameter=SILENCIOSO_PARAM,
        help="Al escalar, el cliente lee «Permíteme un momento» y el agente deja "
             "de contestarle hasta que una persona termine. Apagado, el agente "
             "dice que lo contactará un asesor y sigue contestando.")
    visar_handoff_user_id = fields.Many2one(
        'res.users', string="Persona a cargo",
        config_parameter=USER_PARAM,
        domain=[('share', '=', False)],
        help="Recibe la actividad de cada escalamiento, con la conversación. "
             "Debe ser quien tiene el teléfono con WhatsApp Business: es desde "
             "ahí donde se contesta.")
    visar_handoff_dias = fields.Selection(
        DIAS, string="Días de atención",
        config_parameter=DIAS_PARAM, default=DIAS_DEFAULT)
    visar_handoff_hora_desde = fields.Integer(
        string="Desde las", config_parameter=DESDE_PARAM, default=DESDE_DEFAULT)
    visar_handoff_hora_hasta = fields.Integer(
        string="Hasta las", config_parameter=HASTA_PARAM, default=HASTA_DEFAULT)
    visar_handoff_espera_min = fields.Integer(
        string="Minutos antes del aviso de espera",
        config_parameter=ESPERA_PARAM, default=ESPERA_DEFAULT,
        help="Si nadie le contesta en este tiempo, el agente le manda un solo "
             "mensaje para que sepa que sigue en espera.")
    visar_handoff_devolver_h = fields.Integer(
        string="Horas para devolver al agente",
        config_parameter=DEVOLVER_PARAM, default=DEVOLVER_DEFAULT,
        help="Sin mensajes de la persona en este tiempo, el agente vuelve a "
             "contestar. Fuera de horario el reloj espera a que abra la oficina.")
    visar_handoff_urgente_dias = fields.Integer(
        string="Días de más para un cliente urgente",
        config_parameter=URGENTE_DIAS_PARAM, default=URGENTE_DIAS_DEFAULT,
        help="Si el cliente lo quiere pronto y el primer día libre cae más de "
             "estos días después del primero que permite la anticipación "
             "mínima, se escala a una persona.")
    # No son del escalamiento, pero viven en el mismo modelo de Ajustes del agente.
    visar_cobertura_oferta_interior = fields.Char(
        string="Si la plaga está solo adentro",
        config_parameter=COBERTURA_OFERTA_PARAM % 'interior',
        default=COBERTURA_OFERTA_DEFAULT['interior'],
        help="Lo que pregunta el agente en vez de «¿Dónde fumigamos?» cuando el "
             "cliente ya dijo que la plaga está solo en el interior (cocina, "
             "baño…). Vacío, el texto de siempre.")
    visar_cobertura_oferta_exterior = fields.Char(
        string="Si la plaga está solo afuera",
        config_parameter=COBERTURA_OFERTA_PARAM % 'exterior',
        default=COBERTURA_OFERTA_DEFAULT['exterior'],
        help="Lo mismo cuando dijo que está solo en el exterior (jardín, patio…).")
    visar_handoff_persona_no_aparta = fields.Boolean(
        string="Si alguien escribe primero desde la app, el agente sigue contestando",
        config_parameter=NO_APARTA_PARAM,
        help="Apagado (lo normal), cuando alguien escribe desde la app WhatsApp "
             "Business a un cliente que nadie escaló, el agente se aparta igual, "
             "para que no hablen los dos encima.")
    visar_handoff_texto_espera = fields.Char(
        string="Mensaje al escalar", config_parameter=TEXTO_ESPERA_PARAM,
        default=TEXTO_ESPERA_DEFAULT)
    visar_handoff_texto_respaldo = fields.Char(
        string="Mensaje si nadie contesta", config_parameter=TEXTO_RESPALDO_PARAM,
        default=TEXTO_RESPALDO_DEFAULT)
    visar_handoff_texto_fuera = fields.Char(
        string="Mensaje fuera de horario", config_parameter=TEXTO_FUERA_PARAM,
        default=TEXTO_FUERA_DEFAULT,
        help="{cuando} se cambia por «hoy a partir de las 9:00», «mañana a "
             "partir de las 9:00» o «el lunes a partir de las 9:00».")

    @api.constrains('visar_handoff_hora_desde', 'visar_handoff_hora_hasta',
                    'visar_handoff_espera_min', 'visar_handoff_devolver_h',
                    'visar_handoff_urgente_dias', 'visar_handoff_texto_fuera')
    def _check_visar_handoff(self):
        for record in self:
            desde = record.visar_handoff_hora_desde
            hasta = record.visar_handoff_hora_hasta
            if not (0 <= desde < hasta <= 24):
                raise ValidationError(_(
                    "El horario de atención va de 0 a 24, y «desde» antes que "
                    "«hasta»."))
            if record.visar_handoff_espera_min < 1:
                raise ValidationError(_(
                    "Los minutos antes del aviso de espera son al menos 1."))
            if record.visar_handoff_devolver_h < 1:
                raise ValidationError(_(
                    "Las horas para devolver al agente son al menos 1."))
            # 0 no se puede guardar: Ajustes borra el parametro y vuelve el 1.
            if record.visar_handoff_urgente_dias < 1:
                raise ValidationError(_(
                    "Los días de más para un cliente urgente son al menos 1."))
            texto = record.visar_handoff_texto_fuera or ''
            try:
                texto.format(cuando='x')
            except (KeyError, IndexError, ValueError):
                raise ValidationError(_(
                    "En «Mensaje fuera de horario» la única llave permitida es "
                    "{cuando}."))


class VisarAgentContacto(models.Model):
    _inherit = 'visar.agent.contacto'

    visar_atencion = fields.Selection(
        [('escalado', "Esperando a una persona"),
         ('persona', "Lo atiende una persona")],
        string="Quién atiende", readonly=True, index=True,
        help="Vacío: el agente. Con el escalamiento silencioso encendido, el "
             "agente no le contesta mientras esto tenga valor.")
    visar_atencion_desde = fields.Datetime(string="Desde", readonly=True)
    visar_atencion_motivo = fields.Char(string="Motivo del escalamiento", readonly=True)

    @api.model
    def _visar_buscar_o_crear(self, phone, crear=True):
        """El contacto de `phone` (por sus 10 digitos), creandolo si hace falta.

        Sin sumar al contador de mensajes: eso es de `_visar_registrar`, que
        corre cuando el mensaje ENTRA.
        """
        nat = self.env['res.partner']._visar_phone_nat10_value(phone) or ''
        if not nat:
            return self.browse()
        contacto = self.sudo().with_context(active_test=False).search(
            [('name', '=', nat)], limit=1)
        if contacto or not crear:
            return contacto
        partner, ambiguo = self._visar_resolver_partner(nat)
        return self.sudo().create({
            'name': nat, 'phone': phone, 'mensajes': 0,
            'partner_id': partner.id or False, 'partner_ambiguo': ambiguo,
        })

    @api.model
    def _visar_atencion(self, phone, estado, motivo=None):
        """Anota quien atiende a `phone`. Crea el contacto si no existe.

        `estado`: 'escalado', 'persona' o 'devuelto' (= vuelve el agente).
        """
        contacto = self._visar_buscar_o_crear(phone, crear=estado != 'devuelto')
        if not contacto:
            return self.browse()
        if estado == 'devuelto':
            contacto.write({'visar_atencion': False, 'visar_atencion_desde': False})
            return contacto
        vals = {'visar_atencion': estado}
        if not contacto.visar_atencion:
            vals['visar_atencion_desde'] = fields.Datetime.now()
        if motivo:
            vals['visar_atencion_motivo'] = motivo
        contacto.write(vals)
        return contacto

    def action_visar_devolver_al_agente(self):
        """Boton «Devolver al agente»: el runtime vuelve a contestarle."""
        params = self.env['ir.config_parameter'].sudo()
        base = (params.get_param(BASE_URL_PARAM) or DEFAULT_BASE_URL).rstrip('/')
        token = (params.get_param(TOKEN_PARAM) or '').strip()
        if not token:
            raise UserError(_(
                "No está configurada la conexión con el agente (token vacío en "
                "Ajustes técnicos). Pídele a soporte que la revise."))
        for contacto in self.filtered('visar_atencion'):
            try:
                respuesta = requests.post(
                    f"{base}/internal/handoff-release",
                    json={'phone': contacto.phone or contacto.name},
                    headers={'X-Visar-Token': token}, timeout=SEND_TIMEOUT)
            except requests.RequestException as exc:
                raise UserError(_(
                    "No se pudo hablar con el agente: %s. Inténtalo en un "
                    "momento.", exc)) from exc
            if respuesta.status_code != 200:
                raise UserError(_(
                    "El agente no aceptó devolver la conversación (HTTP %s). "
                    "Inténtalo en un momento.", respuesta.status_code))
            contacto.write({'visar_atencion': False, 'visar_atencion_desde': False})
        return True
