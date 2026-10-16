# -*- coding: utf-8 -*-
"""El cliente confirma (o mueve) por WhatsApp su visita de suscripción pre-agendada.

Es la **Fase B** del paso 2 de pólizas (`.context/35-polizas.md`). La Fase A
(1-oct-2026) le pone fecha, hora y técnico a cada visita preventiva con una cita
real; hasta hoy nadie se lo decía al cliente salvo que oficina llamara. Ahora:

```
día −14   «Su próxima visita es el jueves 20 a las 10:00. ¿Le queda bien?»
          [Sí, confirmo]  [Quiero cambiarla]
día −7    el mismo aviso, si no contestó
día −2    el mismo aviso, si no contestó; después no se insiste
silencio  = la visita se queda como está (lo dice la plantilla)
"Sí"      → la visita queda confirmada
"Cambiar" → otros horarios ESE día → ±3 días → ±7 días → asesor
```

Los días (14, 7, 2), la ventana de horas en que se manda y el interruptor viven en
Ajustes → Visar. Nace **apagado**: encenderlo es mandar mensajes a clientes.

**Avisado = puede moverla.** `_visar_poliza_blocked` (visar_appointment) no deja
mover una pre-agenda que nadie le ha confirmado al cliente, porque la puso un robot
y el cliente no sabe que existe. En cuanto el aviso le llega, sí lo sabe: aquí se
relaja ese bloqueo para las visitas avisadas, y solo para ellas.
"""
import logging
from datetime import timedelta

import pytz
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .visar_agent_tools import DEFAULT_TZ, TZ_PARAM

_logger = logging.getLogger(__name__)

AVISO_ENABLED_PARAM = 'visar.poliza.aviso_enabled'
AVISO_DIAS_PARAM = 'visar.poliza.aviso_dias'
AVISO_DIAS_DEFAULT = '14,7,2'
AVISO_HORA_DESDE_PARAM = 'visar.poliza.aviso_hora_desde'
AVISO_HORA_DESDE_DEFAULT = 9
AVISO_HORA_HASTA_PARAM = 'visar.poliza.aviso_hora_hasta'
AVISO_HORA_HASTA_DEFAULT = 19

# Dos avisos de la misma visita nunca en menos de esto: una pre-agenda creada a
# tres días de la visita entraría en el hito de 7 y al día siguiente en el de 2.
MIN_HORAS_ENTRE_AVISOS = 36

# Cuántas visitas por corrida. El cron es horario: lo que no quepa sale en la
# siguiente, y un lote acotado no retiene un worker.
LOTE = 100

AVISO_SIN_TELEFONO = "Visita de suscripción: avisar por teléfono"


def dias_de_aviso(texto):
    """'14,7,2' -> [14, 7, 2] (descendente, sin repetidos). Lanza si no se lee."""
    dias = set()
    for trozo in (texto or '').replace(' ', '').split(','):
        if not trozo:
            continue
        valor = int(trozo)
        if valor < 1:
            raise ValueError(trozo)
        dias.add(valor)
    return sorted(dias, reverse=True)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_poliza_aviso_enabled = fields.Boolean(
        string="Avisar por WhatsApp las visitas de suscripción",
        config_parameter=AVISO_ENABLED_PARAM,
        help="Antes de cada visita de suscripción que el sistema pre-agendó, se "
             "le manda al cliente la fecha con dos botones: «Sí, confirmo» y "
             "«Quiero cambiarla». Si no contesta, la visita se queda como está.")
    visar_poliza_aviso_dias = fields.Char(
        string="Días antes de la visita",
        config_parameter=AVISO_DIAS_PARAM,
        default=AVISO_DIAS_DEFAULT,
        help="Separados por coma. El primero es el aviso; los demás, "
             "recordatorios si el cliente no contestó. Ej.: 14,7,2")
    visar_poliza_aviso_hora_desde = fields.Integer(
        string="Mandar desde las",
        config_parameter=AVISO_HORA_DESDE_PARAM,
        default=AVISO_HORA_DESDE_DEFAULT)
    visar_poliza_aviso_hora_hasta = fields.Integer(
        string="Mandar hasta las",
        config_parameter=AVISO_HORA_HASTA_PARAM,
        default=AVISO_HORA_HASTA_DEFAULT)

    @api.constrains('visar_poliza_aviso_dias', 'visar_poliza_aviso_hora_desde',
                    'visar_poliza_aviso_hora_hasta')
    def _check_visar_poliza_aviso(self):
        for record in self:
            try:
                dias = dias_de_aviso(record.visar_poliza_aviso_dias
                                     or AVISO_DIAS_DEFAULT)
            except ValueError:
                raise ValidationError(_(
                    "«Días antes de la visita» son números enteros separados por "
                    "coma, por ejemplo 14,7,2."))
            if not dias:
                raise ValidationError(_(
                    "«Días antes de la visita» necesita al menos un día."))
            desde = record.visar_poliza_aviso_hora_desde
            hasta = record.visar_poliza_aviso_hora_hasta
            if not (0 <= desde < hasta <= 24):
                raise ValidationError(_(
                    "Las horas de envío van de 0 a 24, y «desde» antes que "
                    "«hasta»."))


class VisarWaPolizaMessage(models.Model):
    """Buzón del aviso de visita de suscripción. Transporte: el mixin de siempre."""

    _name = 'visar.wa.poliza.message'
    _inherit = ['visar.wa.outbox.mixin']
    _description = "Aviso por WhatsApp de visita de suscripción (buzón de salida)"
    _order = 'create_date desc, id desc'
    _rec_name = 'template_key'

    task_id = fields.Many2one(
        'project.task', string="Visita", ondelete='cascade', index=True)
    event_id = fields.Many2one(
        'calendar.event', string="Cita", ondelete='set null', index=True)
    partner_id = fields.Many2one('res.partner', string="Cliente", index=True)
    hito = fields.Integer(string="Días antes", readonly=True)

    @api.model
    def _visar_wa_keys(self):
        return [('poliza_confirmar', "Visita de suscripción: confirmar o cambiar")]

    @api.model
    def _visar_wa_ttl_minutes(self, template_key):
        # Lo que quede de la ventana de horas lo cubre de sobra; un aviso de
        # ayer sobre la visita de dentro de una semana no es urgente, pero
        # tampoco puede llegar a las 3 de la mañana del día siguiente.
        return 6 * 60

    @api.model
    def _visar_wa_cron_xmlid(self):
        return 'visar_whatsapp_agent.visar_wa_poliza_outbox_cron'

    @api.model
    def _visar_wa_endpoints(self):
        # No es solo un texto: el tap de un botón tiene que saber de qué cita
        # se habla (`event_id`) y aterrizar en el flujo que la confirma o mueve.
        return {'poliza_confirmar': '/internal/booking-event'}

    def _visar_wa_context(self):
        self.ensure_one()
        return {'event_id': self.event_id.id or None,
                'task_id': self.task_id.id or None}

    def _visar_wa_chatter(self):
        self.ensure_one()
        return self.task_id

    def _visar_attempt_send(self):
        """Al ENTREGARSE, la visita queda avisada: desde ahí el cliente la mueve."""
        enviado = super()._visar_attempt_send()
        if enviado and self.task_id:
            tarea = self.task_id.sudo()
            if not tarea.visar_visit_aviso_at:
                tarea.visar_visit_aviso_at = fields.Datetime.now()
            tarea.message_post(
                body=_("Se le mandó al cliente por WhatsApp el aviso de esta "
                       "visita (%(hito)s días antes): confirmarla o cambiarla.",
                       hito=self.hito),
                subtype_xmlid='mail.mt_note')
        return enviado


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_visit_aviso_at = fields.Datetime(
        string="Avisada por WhatsApp", readonly=True, copy=False,
        help="Cuándo se le mandó al cliente el primer aviso de esta visita. "
             "Desde ese momento el cliente puede moverla él mismo por WhatsApp.")
    visar_visit_aviso_ultimo_at = fields.Datetime(
        string="Último aviso", readonly=True, copy=False)
    visar_visit_aviso_hito = fields.Integer(
        string="Último aviso (días antes)", readonly=True, copy=False)
    visar_visit_respuesta = fields.Selection([
        ('confirmo', "Confirmó por WhatsApp"),
        ('cambio', "Pidió cambiarla"),
    ], string="Respuesta del cliente", readonly=True, copy=False)
    visar_visit_respuesta_at = fields.Datetime(
        string="Respondió el", readonly=True, copy=False)

    # ------------------------------------------------------------------
    # El cron
    # ------------------------------------------------------------------

    @api.model
    def _visar_poliza_aviso_config(self):
        params = self.env['ir.config_parameter'].sudo()
        try:
            dias = dias_de_aviso(params.get_param(AVISO_DIAS_PARAM)
                                 or AVISO_DIAS_DEFAULT)
        except ValueError:
            dias = dias_de_aviso(AVISO_DIAS_DEFAULT)

        def entero(clave, defecto):
            try:
                return int(params.get_param(clave) or defecto)
            except (TypeError, ValueError):
                return defecto

        return {
            'enabled': params.get_param(AVISO_ENABLED_PARAM) in ('True', 'true', '1'),
            'dias': dias or dias_de_aviso(AVISO_DIAS_DEFAULT),
            'desde': entero(AVISO_HORA_DESDE_PARAM, AVISO_HORA_DESDE_DEFAULT),
            'hasta': entero(AVISO_HORA_HASTA_PARAM, AVISO_HORA_HASTA_DEFAULT),
        }

    @api.model
    def _visar_poliza_aviso_ahora_local(self):
        """(ahora en la zona de Visar, esa zona)."""
        tz_info = pytz.timezone(self.env['ir.config_parameter'].sudo().get_param(
            TZ_PARAM, DEFAULT_TZ))
        return pytz.utc.localize(fields.Datetime.now()).astimezone(tz_info), tz_info

    @api.model
    def _visar_poliza_hito(self, dias_faltan, dias):
        """El hito que toca con `dias_faltan` para la visita, o None.

        El más chico de los que ya llegaron: con 10 días por delante y avisos a
        14/7/2, toca el de 14; con 5, el de 7. Con 0 (es hoy) ya no se avisa.
        """
        if dias_faltan < 1:
            return None
        llegados = [d for d in dias if dias_faltan <= d]
        return min(llegados) if llegados else None

    @api.model
    def _visar_cron_poliza_avisos(self):
        """Encola el aviso de las visitas pre-agendadas a las que les toca. Idempotente.

        La idempotencia vive AQUÍ y no en el buzón (el mixin crea un registro cada
        vez que se le llama): el hito ya mandado queda en la propia visita, y se
        marca en la misma pasada en que se encola.
        """
        config = self._visar_poliza_aviso_config()
        if not config['enabled']:
            return 0
        ahora_local, tz_info = self._visar_poliza_aviso_ahora_local()
        if not (config['desde'] <= ahora_local.hour < config['hasta']):
            return 0
        hoy = ahora_local.date()
        tope = max(config['dias'])
        ahora = fields.Datetime.now()
        hasta_utc = ahora + timedelta(days=tope + 1)

        visitas = self.sudo().search([
            ('visar_subscription_order_id', '!=', False),
            ('visar_visit_event_id', '!=', False),
            ('visar_visit_preagendada', '=', True),
            ('visar_visit_preagenda_confirmada', '=', False),
            ('visar_visit_respuesta', '!=', 'confirmo'),
            ('visar_visit_event_id.start', '>', ahora),
            ('visar_visit_event_id.start', '<=', hasta_utc),
            ('state', 'not in', ('1_canceled', '1_done')),
            ('visar_subscription_order_id.subscription_state', '=', '3_progress'),
        ], limit=LOTE, order='id')

        encolados = 0
        for visita in visitas:
            try:
                with self.env.cr.savepoint():
                    if visita._visar_poliza_avisar(hoy, tz_info, config['dias']):
                        encolados += 1
            except Exception:  # noqa: BLE001 - una visita no tumba el cron
                _logger.exception("Aviso de póliza: falló la visita %s", visita.id)
        if encolados:
            _logger.info("Aviso de póliza: %s aviso(s) encolado(s)", encolados)
        return encolados

    def _visar_poliza_avisar(self, hoy, tz_info, dias):
        """Encola el aviso de ESTA visita si le toca. True si se encoló."""
        self.ensure_one()
        cita = self.visar_visit_event_id
        inicio_local = pytz.utc.localize(cita.start).astimezone(tz_info)
        hito = self._visar_poliza_hito((inicio_local.date() - hoy).days, dias)
        if hito is None:
            return False
        if self.visar_visit_aviso_hito and hito >= self.visar_visit_aviso_hito:
            return False  # ese hito (o uno anterior) ya salió
        if self.visar_visit_aviso_ultimo_at and (
                fields.Datetime.now() - self.visar_visit_aviso_ultimo_at
                < timedelta(hours=MIN_HORAS_ENTRE_AVISOS)):
            return False

        cliente = self.visar_subscription_order_id.partner_id
        telefono = self._visar_poliza_telefono(cliente)
        if not telefono:
            # Decisión del 1-oct-2026: se pre-agenda igual y oficina confirma
            # por teléfono. La actividad es idempotente.
            self._visar_preagenda_actividad(
                AVISO_SIN_TELEFONO,
                _("No se pudo avisar por WhatsApp de la visita del %(cuando)s: "
                  "%(cliente)s no tiene un teléfono válido. Hay que "
                  "confirmársela por teléfono.",
                  cuando=self._visar_preagenda_cuando(cita),
                  cliente=cliente.display_name or _("el cliente")))
            self.write({'visar_visit_aviso_hito': hito})
            return False

        nombre = (cliente.name or '').split()[0] if cliente.name else ''
        cuando = self.env['visar.agent.tools']._agent_window_label(
            cita.start, cita.stop)
        texto = _(
            "Hola%(nombre)s, tu próxima visita de suscripción está programada "
            "para el *%(cuando)s*.\n\n¿Te queda bien ese día y horario? Si "
            "necesitas cambiarla, dime y te muestro las opciones.\n\nSi no "
            "respondes, mantenemos la cita tal como está.",
            nombre=(' ' + nombre) if nombre else '', cuando=cuando)
        self.env['visar.wa.poliza.message'].sudo()._visar_wa_enqueue(
            'poliza_confirmar', telefono, texto, params=[nombre or cliente.name or '', cuando],
            values={'task_id': self.id, 'event_id': cita.id,
                    'partner_id': cliente.id, 'hito': hito})
        # El hito se marca al ENCOLAR (es lo que hace idempotente el cron);
        # `visar_visit_aviso_at` —lo que deja moverla— al ENTREGAR.
        self.write({'visar_visit_aviso_hito': hito,
                    'visar_visit_aviso_ultimo_at': fields.Datetime.now()})
        return True

    @api.model
    def _visar_poliza_telefono(self, cliente):
        """El número con lada de país, solo dígitos, o ''. El nacional no sirve."""
        if not cliente:
            return ''
        bruto = cliente.phone_sanitized or cliente.phone or ''
        digitos = ''.join(c for c in bruto if c.isdigit())
        if len(digitos) == 10:
            digitos = '52' + digitos
        return digitos if len(digitos) >= 11 else ''


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    def _visar_poliza_blocked(self):
        """Una pre-agenda AVISADA por WhatsApp ya la puede mover el cliente.

        El bloqueo existe porque la puso un robot y el cliente no sabe que existe
        (`visar_appointment`). Cuando el aviso le llega, sí lo sabe: el aviso le
        ofrece justamente cambiarla. Las no avisadas siguen igual.
        """
        motivo = super()._visar_poliza_blocked()
        if motivo != 'poliza':
            return motivo
        visitas = self._visar_reschedule_tasks().filtered('visar_subscription_order_id')
        pendientes = visitas.filtered(
            lambda t: t.visar_visit_preagendada
            and not t.visar_visit_preagenda_confirmada)
        if pendientes and all(t.visar_visit_aviso_at for t in pendientes):
            return None
        return motivo


class VisarAgentPoliza(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    @api.model
    def agent_poliza_confirm(self, payload):
        """El cliente tocó «Sí, confirmo». `payload` = {phone, event_id}.

        ESCRIBE (sudo acotado): marca confirmadas las visitas de esa cita. Misma
        comprobación de pertenencia que el reagendado: el `event_id` viaja por el
        chat.
        """
        event, error = self._agent_reschedule_event(payload)
        if error:
            return {'ok': False, 'reason': error,
                    'message': "No encontre esa visita a tu nombre."}
        visitas = event.sudo().visar_visit_task_ids.filtered(
            'visar_subscription_order_id')
        if not visitas:
            return {'ok': False, 'reason': 'not_found',
                    'message': "Esa cita no es una visita de suscripcion."}
        ahora = fields.Datetime.now()
        for visita in visitas:
            visita.write({'visar_visit_preagenda_confirmada': True,
                          'visar_visit_respuesta': 'confirmo',
                          'visar_visit_respuesta_at': ahora})
            visita.message_post(
                body=Markup(_("✅ El cliente <b>confirmó por WhatsApp</b> la "
                              "visita.")),
                subtype_xmlid='mail.mt_note')
        return {'ok': True, 'reason': None,
                'when_label': self._agent_window_label(event.start, event.stop),
                'message': "Visita confirmada."}

    @api.model
    def agent_poliza_change(self, payload):
        """El cliente tocó «Quiero cambiarla»: se apunta, para que oficina lo vea.

        No mueve nada: el cambio lo hace el reagendado. Si el cliente lo deja a
        medias, la visita sigue donde estaba y los recordatorios siguen saliendo.
        """
        event, error = self._agent_reschedule_event(payload)
        if error:
            return {'ok': False, 'reason': error}
        visitas = event.sudo().visar_visit_task_ids.filtered(
            'visar_subscription_order_id')
        ahora = fields.Datetime.now()
        for visita in visitas.filtered(lambda t: t.visar_visit_respuesta != 'confirmo'):
            visita.write({'visar_visit_respuesta': 'cambio',
                          'visar_visit_respuesta_at': ahora})
            visita.message_post(
                body=_("El cliente pidió por WhatsApp cambiar la visita."),
                subtype_xmlid='mail.mt_note')
        return {'ok': bool(visitas), 'reason': None if visitas else 'not_found'}
