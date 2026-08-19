# -*- coding: utf-8 -*-
"""Buzón de salida de los avisos por WhatsApp de la app de campo.

Los avisos de "voy en camino" / "ya llegué" / "hay que reagendar" NO se mandan en
línea al pulsar el botón: son efecto secundario de un cambio de etapa, y el técnico
no debe esperar a Meta ni ver fallar la transición porque WhatsApp esté caído. Se
encolan aquí y las manda el cron.

**El transporte vive en `visar.wa.outbox.mixin`** (`visar_base`): encolar, disparar
el cron, reintentar, caducar y avisar en el chatter son lo mismo para estos avisos
y para los del agendado por WhatsApp, y dos copias de esa lógica divergen en cuanto
alguien toca una. Aquí queda lo propio de la app de campo: de qué cuelga el aviso
(una tarea), qué avisos existen y cuánto vale cada uno.

⚠️ Fuera de la ventana de 24 h de Meta un mensaje LIBRE no se entrega, y estos
avisos van siempre a un cliente que nunca escribió (agendó por la web). Hasta que
las plantillas estén aprobadas, estos mensajes **fallarán y caducarán** — el
registro queda aquí y en el chatter. Ver `.context/25-field-app.md`.
"""
from odoo import api, fields, models

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


class VisarWaMessage(models.Model):
    _name = 'visar.wa.message'
    _inherit = ['visar.wa.outbox.mixin']
    _description = "Aviso por WhatsApp al cliente (buzón de salida)"
    _order = 'create_date desc, id desc'
    _rec_name = 'template_key'

    task_id = fields.Many2one(
        'project.task', string="Servicio", required=True, ondelete='cascade',
        index=True)

    # ------------------------------------------------------------------
    # Lo que el mixin pide
    # ------------------------------------------------------------------

    @api.model
    def _visar_wa_keys(self):
        return TEMPLATE_KEYS

    @api.model
    def _visar_wa_ttl_minutes(self, template_key):
        return TTL_MINUTES.get(template_key, DEFAULT_TTL_MINUTES)

    @api.model
    def _visar_wa_cron_xmlid(self):
        return 'visar_field_app.visar_wa_outbox_cron'

    def _visar_wa_chatter(self):
        self.ensure_one()
        return self.task_id

    def _visar_wa_context(self):
        # Solo para trazas del runtime: permite cruzar el envío con la tarea.
        self.ensure_one()
        return {'task_id': self.task_id.id}

    # ------------------------------------------------------------------
    # Encolar
    # ------------------------------------------------------------------

    @api.model
    def _visar_enqueue(self, task, template_key, params, text):
        """Encola un aviso de una tarea. Devuelve el registro o vacío.

        No lanza nunca: encolar un aviso no puede tumbar la transición de etapa que
        lo originó. Si no hay teléfono no se encola nada (y el llamador ya deja la
        nota en el chatter)."""
        _display, e164 = task._visar_client_phone()
        return self._visar_wa_enqueue(
            template_key, e164, text, params=params, values={'task_id': task.id})
