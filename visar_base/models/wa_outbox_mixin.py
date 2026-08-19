# -*- coding: utf-8 -*-
"""Transporte compartido de los avisos por WhatsApp al cliente.

Aquí no hay ningún aviso concreto: hay **cómo se manda uno**. Encolar, disparar
el cron, reintentar, caducar y avisar en el chatter cuando no se pudo entregar es
el mismo mecanismo para el técnico que va en camino y para la cita que acaba de
confirmarse, y la única diferencia entre los dos es a qué registro cuelgan.

Nació en `visar_field_app` (`visar.wa.message`, avisos de la app de campo). Al
necesitar los mismos avisos para el agendado por WhatsApp había dos salidas:
copiar ~150 líneas de transporte, o subir el transporte a un mixin y dejar que
cada módulo ponga lo suyo. Se eligió lo segundo por lo de siempre: dos copias de
una regla divergen en cuanto alguien toca una.

**Lo que el modelo concreto tiene que poner** (todo lo demás sale de aquí):

  * su ancla — el `Many2one` al registro del que cuelga el aviso;
  * `_visar_wa_keys()` — su catálogo CERRADO de claves de aviso;
  * `_visar_wa_ttl_minutes(key)` — cuánto vale cada aviso;
  * `_visar_wa_cron_xmlid()` — su cron;
  * `_visar_wa_chatter()` — dónde dejar la nota si el aviso no llegó (puede
    devolver vacío: entonces solo queda el log).

Tres decisiones heredadas, y por qué:

* **El cron se dispara al encolar** (`_trigger()`), no solo en su intervalo: el
  aviso llega en segundos sin bloquear la petición que lo originó. El intervalo
  es la red de seguridad para los reintentos (mismo patrón que la cola de correo
  nativa).
* **Todo aviso CADUCA.** Es lo que separa una cola de mensajes de una cola de
  avisos: reintentar "su técnico va en camino" una hora después es peor que no
  mandarlo. Al caducar se avisa en el chatter para que oficina lo sepa.
* **La plantilla la resuelve el runtime**, no Odoo: aquí solo viaja una CLAVE de
  un catálogo cerrado. Odoo no puede pedir "manda esta plantilla" ni, por tanto,
  usar el endpoint interno como relay.

⚠️ Fuera de la ventana de 24 h de Meta un mensaje LIBRE no se entrega. Para los
avisos de la app de campo eso es lo normal (el cliente agendó por la web y nunca
escribió); para los del agendado por WhatsApp casi nunca aplica, porque el
cliente acaba de escribir.
"""
import json
import logging

import requests
from markupsafe import Markup

from odoo import api, fields, models, modules

_logger = logging.getLogger(__name__)

# Parámetros del runtime. El nombre lo hereda de `visar_field_app`, que fue quien
# los creó: renombrarlos obligaría a reconfigurar el servidor a cambio de nada.
BASE_URL_PARAM = 'visar_field.agent_base_url'
TOKEN_PARAM = 'visar_field.agent_token'
DEFAULT_BASE_URL = 'http://127.0.0.1:8000'

DEFAULT_TTL_MINUTES = 30

# Tope de intentos. La caducidad es el límite real; esto solo evita machacar un
# endpoint que contesta 4xx (payload mal, plantilla inexistente) hasta que expire.
MAX_ATTEMPTS = 5

# Cuántos se procesan por pasada del cron.
BATCH_SIZE = 50

# Timeout del POST al runtime. Corto: lo corre el cron, pero no vale la pena
# retener un worker si el runtime no contesta — el reintento ya está resuelto.
SEND_TIMEOUT = 15


class VisarWaOutboxMixin(models.AbstractModel):
    _name = 'visar.wa.outbox.mixin'
    _description = "Visar - Buzón de salida de avisos por WhatsApp (mixin)"
    _order = 'create_date desc, id desc'

    template_key = fields.Selection(
        selection='_visar_wa_keys', string="Aviso", required=True, index=True)
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
    # Lo que pone el modelo concreto
    # ------------------------------------------------------------------

    @api.model
    def _visar_wa_keys(self):
        """Catálogo CERRADO de claves de aviso: [(clave, etiqueta)]."""
        return []

    @api.model
    def _visar_wa_ttl_minutes(self, template_key):
        """Vida útil del aviso, en minutos. Sale de PARA QUÉ sirve el mensaje."""
        return DEFAULT_TTL_MINUTES

    @api.model
    def _visar_wa_cron_xmlid(self):
        """xmlid del cron que manda los pendientes de este modelo."""
        return None

    @api.model
    def _visar_wa_endpoint(self):
        """Ruta del runtime a la que se manda. Misma respuesta para todas.

        Los avisos de la app de campo son solo un texto (`/send-notification`);
        los del agendado además **cambian lo que el cliente puede hacer después**
        y por eso van a `/booking-event`, que primero toca la conversación y luego
        envía. Decirle "¿elegimos otro horario?" solo sirve si el "sí" siguiente
        aterriza en el flujo de agendado y no en el menú principal.
        """
        return '/internal/send-notification'


    def _visar_wa_chatter(self):
        """Registro donde dejar la nota si el aviso NO llegó. Puede venir vacío."""
        self.ensure_one()
        return self.browse()

    def _visar_wa_context(self):
        """Datos extra para el payload del runtime (p. ej. `task_id`)."""
        self.ensure_one()
        return {}

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------

    @api.model
    def _visar_wa_config(self):
        """`(base_url, token)` del runtime del agente. Token vacío ⇒ no configurado."""
        params = self.env['ir.config_parameter'].sudo()
        base = (params.get_param(BASE_URL_PARAM) or DEFAULT_BASE_URL).rstrip('/')
        return base, (params.get_param(TOKEN_PARAM) or '').strip()

    # ------------------------------------------------------------------
    # Encolar
    # ------------------------------------------------------------------

    @api.model
    def _visar_wa_enqueue(self, template_key, phone, text, params=None, values=None):
        """Encola un aviso y pide al cron que corra ya. Devuelve el registro o vacío.

        **No lanza nunca**: encolar un aviso no puede tumbar la operación que lo
        originó — ni la transición de etapa del técnico, ni el cobro que acaba de
        confirmar una cita. Sin teléfono no se encola nada.
        """
        if not (phone or '').strip():
            return self.browse()
        record = self.sudo().create(dict(values or {}, **{
            'template_key': template_key,
            'phone': phone.strip(),
            'params_json': json.dumps([str(p) for p in (params or [])]),
            'fallback_text': text,
            'expire_at': fields.Datetime.add(
                fields.Datetime.now(),
                minutes=self._visar_wa_ttl_minutes(template_key)),
        }))
        record._visar_trigger_cron()
        return record

    def _visar_trigger_cron(self):
        """Pide una corrida inmediata del cron (sin bloquear esta petición)."""
        xmlid = self._visar_wa_cron_xmlid()
        cron = self.env.ref(xmlid, raise_if_not_found=False) if xmlid else False
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
        etiqueta = dict(self._visar_wa_keys()).get(
            self.template_key, self.template_key)
        _logger.warning(
            "Aviso %s (%s,%s) NO entregado: %s (%s intento(s), ultimo error: %s)",
            self.template_key, self._name, self.id, reason, self.attempts,
            self.last_error or 'sin detalle')
        destino = self._visar_wa_chatter()
        if not destino:
            return
        destino.message_post(
            body=Markup(
                "⚠️ El aviso de WhatsApp «%s» <b>no se pudo entregar</b>: %s "
                "(%s intento(s), último error: %s). El cliente NO fue avisado — "
                "conviene llamarle."
            ) % (etiqueta, reason, self.attempts, self.last_error or 'sin detalle'),
            subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # Envío
    # ------------------------------------------------------------------

    def _visar_attempt_send(self):
        """Un intento de envío. Nunca lanza: registra el fallo y deja el reintento."""
        self.ensure_one()
        base, token = self._visar_wa_config()
        if not token:
            self._visar_register_failure("Falta %s" % TOKEN_PARAM)
            return False
        payload = dict(self._visar_wa_context(), **{
            'phone': self.phone,
            'template_key': self.template_key,
            'params': json.loads(self.params_json or '[]'),
            'fallback_text': self.fallback_text,
        })
        try:
            response = requests.post(
                base + self._visar_wa_endpoint(), json=payload,
                headers={'X-Visar-Token': token}, timeout=SEND_TIMEOUT)
        except requests.RequestException as exc:
            self._visar_register_failure(str(exc)[:200])
            return False
        if response.status_code != 200:
            self._visar_register_failure(self._visar_http_detail(response))
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
        _logger.info("Aviso %s (%s,%s) enviado (%s)",
                     self.template_key, self._name, self.id, mode or '?')
        return True

    @api.model
    def _visar_http_detail(self, response):
        """Detalle legible de una respuesta HTTP fallida, acotado."""
        try:
            data = response.json()
        except ValueError:
            data = None
        detalle = (data or {}).get('detail') if isinstance(data, dict) else None
        return ('HTTP %s: %s' % (response.status_code,
                                 detalle or (response.text or '')[:150]))[:200]

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
        _logger.warning("Aviso %s (%s,%s) fallo (intento %s): %s",
                        self.template_key, self._name, self.id, attempts, detail)
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
                    minutes=message._visar_wa_ttl_minutes(message.template_key)),
            })
        self._visar_trigger_cron()
        return True

    def action_visar_cancel(self):
        return self.filtered(lambda m: m.state == 'pending').write(
            {'state': 'cancelled'})
