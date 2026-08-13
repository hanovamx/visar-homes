# -*- coding: utf-8 -*-
"""Buzón de salida de los avisos por WhatsApp al cliente.

Los avisos de "voy en camino" / "ya llegué" / "hay que reagendar" NO se mandan en
línea al pulsar el botón: son efecto secundario de un cambio de etapa, y el técnico
no debe esperar a Meta ni ver fallar la transición porque WhatsApp esté caído. Se
encolan aquí y las manda el cron.

Diseño, y por qué:

* **El cron se dispara al encolar** (`_trigger()`), no solo en su intervalo: el
  aviso llega en segundos sin bloquear la petición del técnico. El intervalo es la
  red de seguridad para los reintentos (mismo patrón que la cola de correo nativa).
* **Todo aviso CADUCA** (`expire_at`). Es lo que separa una cola de mensajes de una
  cola de avisos: reintentar "su técnico va en camino" una hora después es peor que
  no mandarlo. Al caducar se avisa en el chatter para que oficina levante el
  teléfono.
* **El texto viaja con el mensaje** (`fallback_text`) aunque se mande por plantilla:
  es lo que se registra en el chatter, y es lo que se envía mientras las plantillas
  de Meta no estén aprobadas.
* **La plantilla la resuelve el runtime**, no Odoo: aquí solo se manda una CLAVE
  (`template_key`) de un catálogo cerrado. Odoo no puede pedir "manda esta
  plantilla" ni, por tanto, usar el endpoint como relay.

⚠️ Fuera de la ventana de 24 h de Meta un mensaje LIBRE no se entrega, y estos
avisos van siempre a un cliente que nunca escribió (agendó por la web). Hasta que
las plantillas estén aprobadas, estos mensajes **fallarán y caducarán** — el
registro queda aquí y en el chatter. Ver `.context/25-field-app.md`.
"""
import json
import logging

import requests
from markupsafe import Markup

from odoo import api, fields, models, modules

_logger = logging.getLogger(__name__)

# Claves de aviso. El runtime mapea cada una a su plantilla aprobada
# (WA_TEMPLATE_ENROUTE / _ARRIVED / _RESCHEDULE). Catálogo CERRADO a propósito.
TEMPLATE_KEYS = [
    ('enroute', "Técnico en camino"),
    ('arrived', "Técnico llegó"),
    ('reschedule', "Reagendar (cliente no llegó)"),
]

# Vida útil de cada aviso, en minutos. Sale de para qué sirve el mensaje, no de un
# número redondo: el "ya llegué" acompaña una ventana de espera de ~10 min, así que
# entregarlo 20 min tarde no sirve de nada; una reagenda sí aguanta el día.
TTL_MINUTES = {
    'enroute': 30,
    'arrived': 15,
    'reschedule': 24 * 60,
}
DEFAULT_TTL_MINUTES = 30

# Tope de intentos. La caducidad es el límite real; esto solo evita machacar un
# endpoint que contesta 4xx (payload mal, plantilla inexistente) hasta que expire.
MAX_ATTEMPTS = 5

# Cuántos se procesan por pasada del cron.
BATCH_SIZE = 50

# Timeout del POST al runtime. Corto: lo corre el cron, pero no vale la pena
# retener un worker si el runtime no contesta — el reintento ya está resuelto.
SEND_TIMEOUT = 15


class VisarWaMessage(models.Model):
    _name = 'visar.wa.message'
    _description = "Aviso por WhatsApp al cliente (buzón de salida)"
    _order = 'create_date desc, id desc'
    _rec_name = 'template_key'

    task_id = fields.Many2one(
        'project.task', string="Servicio", required=True, ondelete='cascade',
        index=True)
    template_key = fields.Selection(
        TEMPLATE_KEYS, string="Aviso", required=True, index=True)
    # Se guarda el número tal como estaba al encolar: si mañana cambia la ficha del
    # cliente, el registro sigue diciendo a dónde se mandó (auditoría) y un reintento
    # no acaba en un número distinto sin que nadie lo note.
    phone = fields.Char(string="Teléfono (E.164)", required=True)
    params_json = fields.Char(
        string="Parámetros", default='[]',
        help="Parámetros del cuerpo de la plantilla, en orden (JSON).")
    fallback_text = fields.Text(
        string="Texto", required=True,
        help="Texto legible del aviso. Es lo que se registra en el chatter y lo que "
             "se envía mientras no haya plantilla aprobada en el runtime.")
    state = fields.Selection([
        ('pending', "Pendiente"),
        ('sent', "Enviado"),
        ('expired', "Caducado"),
        ('failed', "Fallido"),
        ('cancelled', "Cancelado"),
    ], string="Estado", default='pending', required=True, index=True)
    attempts = fields.Integer(string="Intentos", default=0, readonly=True)
    last_error = fields.Char(string="Último error", readonly=True)
    expire_at = fields.Datetime(string="Caduca", required=True, index=True)
    sent_at = fields.Datetime(string="Enviado el", readonly=True)
    mode_used = fields.Char(
        string="Modo", readonly=True,
        help="'template' (plantilla aprobada) o 'free' (mensaje libre, sujeto a la "
             "ventana de 24 h de Meta).")

    # ------------------------------------------------------------------
    # Encolar
    # ------------------------------------------------------------------
    @api.model
    def _visar_enqueue(self, task, template_key, params, text):
        """Encola un aviso y pide al cron que corra ya. Devuelve el registro o vacío.

        No lanza nunca: encolar un aviso no puede tumbar la transición de etapa que
        lo originó. Si no hay teléfono no se encola nada (y el llamador ya deja la
        nota en el chatter)."""
        _display, e164 = task._visar_client_phone()
        if not e164:
            return self.browse()
        ttl = TTL_MINUTES.get(template_key, DEFAULT_TTL_MINUTES)
        record = self.sudo().create({
            'task_id': task.id,
            'template_key': template_key,
            'phone': e164,
            'params_json': json.dumps([str(p) for p in (params or [])]),
            'fallback_text': text,
            'expire_at': fields.Datetime.add(fields.Datetime.now(), minutes=ttl),
        })
        record._visar_trigger_cron()
        return record

    def _visar_trigger_cron(self):
        """Pide una corrida inmediata del cron (sin bloquear esta petición)."""
        cron = self.env.ref('visar_field_app.visar_wa_outbox_cron',
                            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _visar_cron_send_pending(self):
        """Entrada del cron: caduca lo vencido y manda lo que sigue vigente."""
        now = fields.Datetime.now()
        stale = self.sudo().search([
            ('state', '=', 'pending'), ('expire_at', '<=', now)])
        stale._visar_mark_expired()

        batch = self.sudo().search([
            ('state', '=', 'pending'), ('expire_at', '>', now)], limit=BATCH_SIZE)
        # Un commit por aviso: si el runtime falla a media tanda, lo ya enviado no se
        # revierte (ni se reenvía en la pasada siguiente). En pruebas NO se commitea
        # —cerraría la transacción del test— igual que hace la cola de correo nativa.
        auto_commit = not modules.module.current_test
        for message in batch:
            message._visar_attempt_send()
            if auto_commit:
                self.env.cr.commit()

    def _visar_mark_expired(self):
        """Marca caducados y avisa en el chatter: nadie le dijo nada al cliente."""
        for message in self:
            message.write({'state': 'expired'})
            message._visar_warn_not_delivered("caducó sin poder entregarse")

    def _visar_warn_not_delivered(self, reason):
        """Nota en el chatter cuando un aviso NO llegó.

        Es el único camino por el que oficina se entera, así que se dispara en LOS DOS
        finales malos: caducidad e intentos agotados. Si solo se avisara al caducar,
        un aviso que agota intentos (p. ej. 502 constante por falta de plantilla
        aprobada) se quedaría `failed` en silencio."""
        self.ensure_one()
        self.task_id.message_post(
            body=Markup(
                "⚠️ El aviso de WhatsApp «%s» <b>no se pudo entregar</b>: %s "
                "(%s intento(s), último error: %s). El cliente NO fue avisado — "
                "conviene llamarle."
            ) % (dict(TEMPLATE_KEYS).get(self.template_key, self.template_key),
                 reason, self.attempts, self.last_error or 'sin detalle'),
            subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # Envío
    # ------------------------------------------------------------------
    def _visar_attempt_send(self):
        """Un intento de envío. Nunca lanza: registra el fallo y deja el reintento."""
        self.ensure_one()
        base, token = self.task_id._visar_report_whatsapp_config()
        if not token:
            self._visar_register_failure("Falta visar_field.agent_token")
            return False
        payload = {
            'phone': self.phone,
            'template_key': self.template_key,
            'params': json.loads(self.params_json or '[]'),
            'fallback_text': self.fallback_text,
            'task_id': self.task_id.id,
        }
        try:
            response = requests.post(
                '%s/internal/send-notification' % base, json=payload,
                headers={'X-Visar-Token': token}, timeout=SEND_TIMEOUT)
        except requests.RequestException as exc:
            self._visar_register_failure(str(exc)[:200])
            return False
        if response.status_code != 200:
            self._visar_register_failure(
                self.task_id._visar_http_detail(response))
            return False
        try:
            mode = (response.json() or {}).get('mode') or ''
        except ValueError:
            mode = ''
        self.write({
            'state': 'sent',
            'attempts': self.attempts + 1,
            'sent_at': fields.Datetime.now(),
            'mode_used': mode,
            'last_error': False,
        })
        _logger.info("Aviso %s de la tarea %s enviado (%s)",
                     self.template_key, self.task_id.id, mode or '?')
        return True

    def _visar_register_failure(self, detail):
        """Anota el fallo. Se queda 'pending' para reintentar, salvo que se agoten
        los intentos (entonces 'failed', y el barrido de caducidad avisará)."""
        self.ensure_one()
        attempts = self.attempts + 1
        vals = {'attempts': attempts, 'last_error': (detail or '')[:200]}
        exhausted = attempts >= MAX_ATTEMPTS
        if exhausted:
            vals['state'] = 'failed'
        self.write(vals)
        _logger.warning("Aviso %s de la tarea %s falló (intento %s): %s",
                        self.template_key, self.task_id.id, attempts, detail)
        if exhausted:
            self._visar_warn_not_delivered("se agotaron los intentos")

    # ------------------------------------------------------------------
    # Acciones manuales (vista de oficina)
    # ------------------------------------------------------------------
    def action_visar_retry(self):
        """Reintentar a mano un aviso fallido/caducado (renueva la caducidad).

        Es deliberadamente manual: reenviar un aviso viejo solo tiene sentido si
        alguien confirma que sigue siendo verdad."""
        for message in self:
            message.write({
                'state': 'pending', 'attempts': 0, 'last_error': False,
                'expire_at': fields.Datetime.add(
                    fields.Datetime.now(),
                    minutes=TTL_MINUTES.get(message.template_key,
                                            DEFAULT_TTL_MINUTES)),
            })
        self._visar_trigger_cron()
        return True

    def action_visar_cancel(self):
        return self.filtered(lambda m: m.state == 'pending').write(
            {'state': 'cancelled'})
