# -*- coding: utf-8 -*-
"""Las conversaciones de WhatsApp, completas (20-oct-2026).

Hasta hoy no se guardaba ningún mensaje: solo el estado de la reserva, que
caduca a las 3 h (decisión del 13-oct, por privacidad). El usuario pidió
guardarlas para revisar al agente y mejorar sus prompts, y eligió la versión
base de la propuesta del 19-oct: **solo los mensajes**, sin sentimiento ni
señales ni notas.

Una fila por mensaje, de los tres lados:

  * cliente  — lo que escribió, o el botón que tocó ("[botón] Agendar"), o el
               tipo de lo que mandó si no es texto ("[audio]");
  * agente   — lo que contestó el agente, y los avisos que manda Odoo;
  * persona  — lo que alguien escribió desde la app WhatsApp Business.

El runtime los manda en lotes una vez por minuto (`agent_log_messages`), nunca
en el camino de la respuesta. Decisiones del usuario (20-oct): 12 meses, solo
administradores, encendido desde el despliegue. El plazo y el interruptor viven
en Ajustes → Visar; un cron diario borra lo vencido.
"""
import logging
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

GUARDAR_PARAM = 'visar.conversaciones.guardar'
MESES_PARAM = 'visar.conversaciones.meses'
MESES_DEFAULT = 12

QUIEN = [
    ('cliente', "Cliente"),
    ('agente', "Agente"),
    ('persona', "Visar (app)"),
]


def visar_guardar_conversaciones(env):
    """¿Se guardan? Encendido salvo que alguien lo apague (lo eligió el usuario)."""
    return env['ir.config_parameter'].sudo().get_param(GUARDAR_PARAM, 'True') == 'True'


def visar_meses_conversaciones(env):
    try:
        meses = int(env['ir.config_parameter'].sudo().get_param(
            MESES_PARAM, MESES_DEFAULT))
    except (TypeError, ValueError):
        return MESES_DEFAULT
    return max(meses, 1)


class VisarAgentMensaje(models.Model):
    _name = 'visar.agent.mensaje'
    _description = "Mensaje de una conversación de WhatsApp"
    _order = 'fecha, id'
    _rec_name = 'texto'

    contacto_id = fields.Many2one(
        'visar.agent.contacto', string="Contacto", required=True,
        ondelete='cascade', index=True)
    partner_id = fields.Many2one(
        related='contacto_id.partner_id', string="Cliente", store=True)
    fecha = fields.Datetime(string="Fecha", required=True, index=True)
    quien = fields.Selection(QUIEN, string="Quién", required=True)
    texto = fields.Text(string="Mensaje", required=True)
    # El id que le pone el runtime: un lote que se reintenta no se guarda dos
    # veces. (No es una restricción SQL: Odoo 19 no las crea.)
    uid = fields.Char(string="Id del runtime", index=True, readonly=True)

    @api.model
    def _visar_cron_borrar_vencidos(self):
        """Diario: borra lo que pasó del plazo de Ajustes → Visar."""
        limite = fields.Datetime.now() - relativedelta(
            months=visar_meses_conversaciones(self.env))
        vencidos = self.sudo().search([('fecha', '<', limite)])
        if vencidos:
            _logger.info("Conversaciones: se borran %d mensajes anteriores a %s",
                         len(vencidos), limite)
            vencidos.unlink()


class VisarAgentContacto(models.Model):
    _inherit = 'visar.agent.contacto'

    mensaje_ids = fields.One2many(
        'visar.agent.mensaje', 'contacto_id', string="Conversación",
        groups='base.group_system')


class VisarAgentTools(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    @api.model
    def agent_log_messages(self, payload):
        """Guarda un lote de mensajes del runtime.

        `payload` = {"mensajes": [{"uid", "phone", "quien", "texto", "hora"}]},
        con `hora` en segundos desde 1970 (UTC). Un `uid` ya guardado se salta.
        Apagado en Ajustes → Visar no guarda nada (el runtime tampoco manda).
        """
        mensajes = (payload or {}).get('mensajes') or []
        if not visar_guardar_conversaciones(self.env):
            return {'guardados': 0, 'apagado': True}
        Mensaje = self.env['visar.agent.mensaje'].sudo()
        uids = [m.get('uid') for m in mensajes if isinstance(m, dict) and m.get('uid')]
        ya = set(Mensaje.search([('uid', 'in', uids)]).mapped('uid')) if uids else set()
        Contacto = self.env['visar.agent.contacto']
        contactos = {}
        valores = []
        for m in mensajes:
            if not isinstance(m, dict) or m.get('uid') in ya:
                continue
            texto = str(m.get('texto') or '').strip()
            if not texto or m.get('quien') not in dict(QUIEN):
                continue
            phone = m.get('phone')
            if phone not in contactos:
                contactos[phone] = Contacto._visar_buscar_o_crear(phone)
            contacto = contactos[phone]
            if not contacto:
                continue
            try:
                fecha = datetime.fromtimestamp(float(m.get('hora')), timezone.utc) \
                    .replace(tzinfo=None)
            except (TypeError, ValueError, OverflowError, OSError):
                fecha = fields.Datetime.now()
            valores.append({'contacto_id': contacto.id, 'fecha': fecha,
                            'quien': m['quien'], 'texto': texto,
                            'uid': m.get('uid')})
        if valores:
            Mensaje.create(valores)
        return {'guardados': len(valores)}


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Sin `config_parameter`: un booleano así no se puede guardar apagado si su
    # default es encendido (apagarlo borra el parámetro y vuelve el default).
    visar_conversaciones_guardar = fields.Boolean(
        string="Guardar las conversaciones",
        help="Cada mensaje de WhatsApp —del cliente, del agente y de quien "
             "conteste desde la app— se guarda en el contacto. Solo los "
             "administradores las ven. Apagado, no se guarda nada nuevo.")
    visar_conversaciones_meses = fields.Integer(
        string="Meses que se guardan", config_parameter=MESES_PARAM,
        default=MESES_DEFAULT,
        help="Lo más viejo se borra cada noche.")

    @api.model
    def get_values(self):
        res = super().get_values()
        res['visar_conversaciones_guardar'] = visar_guardar_conversaciones(self.env)
        return res

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            GUARDAR_PARAM, 'True' if self.visar_conversaciones_guardar else 'False')

    @api.constrains('visar_conversaciones_meses')
    def _check_visar_conversaciones_meses(self):
        for record in self:
            if record.visar_conversaciones_meses < 1:
                raise ValidationError(_(
                    "Las conversaciones se guardan al menos 1 mes."))
