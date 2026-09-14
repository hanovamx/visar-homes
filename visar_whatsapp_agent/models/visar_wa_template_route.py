# -*- coding: utf-8 -*-
"""Qué plantilla aprobada de Meta usa cada aviso. Se elige aquí, no en el `.env`.

Hasta septiembre de 2026 el nombre de cada plantilla vivía en el `.env` del
runtime (`WA_TEMPLATE_ENROUTE`, …): cambiarlo pedía acceso al servidor y reiniciar
el runtime, y un nombre mal escrito solo se descubría cuando Meta rechazaba el
envío. Y en producción no había **ninguno** puesto, así que todo salía libre
aunque en el módulo WhatsApp de Odoo había cuatro plantillas aprobadas desde el
11 de agosto.

## Lo que NO cambia: el catálogo cerrado

Odoo sigue mandando al runtime solo la CLAVE de cada aviso (`enroute`,
`reschedule_offer`…), nunca un nombre de plantilla. Lo que se mueve es la
CONFIGURACIÓN: qué plantilla corresponde a cada clave la decide un administrador
aquí, y el runtime la lee por el canal por el que ya lee el prompt
(`agent_runtime_config`). Una petición de envío sigue sin poder elegir plantilla.

## Una fila sin plantilla es el comportamiento de antes

El aviso sale como mensaje libre, igual que cuando el `.env` no la tenía. Por eso
el módulo siembra todas las filas vacías: instalarlo no cambia nada para ningún
cliente, y encender plantillas es una decisión aparte, aviso por aviso.

## Por qué se valida tanto al guardar

El runtime rellena la plantilla con los parámetros que el código ya manda, en
orden. Si la plantilla espera otra cosa —una variable de más, una cabecera que
nadie rellena, un botón que nadie paga— Meta rechaza el envío, el buzón reintenta
cinco veces y deja "el cliente NO fue avisado" en la tarea. Todo eso se sabe al
elegirla, así que se dice al elegirla.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


# El contrato que el código YA cumple hoy, clave por clave: cuántas variables de
# cuerpo manda, qué cabecera rellena y si manda el payload de un botón de
# respuesta rápida. Si cambia lo que manda un aviso, cambia aquí también — y la
# prueba de este modelo lo recuerda.
#
# `lead_followup` no está a propósito: lo redacta el modelo y va SIEMPRE libre
# (ver `.context/86-recontacto-de-leads.md`).
ESPECIFICACION = {
    'enroute': {
        'label': "Técnico en camino",
        'variables': 2, 'header': 'none', 'quick_reply': False,
        'nota': "{{1}} técnico · {{2}} minutos estimados de llegada",
    },
    'arrived': {
        'label': "Técnico llegó",
        'variables': 2, 'header': 'none', 'quick_reply': False,
        'nota': "{{1}} técnico · {{2}} minutos de espera",
    },
    'reschedule': {
        'label': "Reagendar — aviso de contacto",
        'variables': 1, 'header': 'none', 'quick_reply': False,
        'nota': "{{1}} técnico. Aviso pasivo: se usa cuando no hay cita que "
                "ofrecer mover.",
    },
    'reschedule_offer': {
        'label': "Reagendar — el cliente elige horario",
        'variables': 2, 'header': 'none', 'quick_reply': True,
        'nota': "{{1}} técnico · {{2}} horas de antelación · un botón de "
                "respuesta rápida (el runtime manda su payload).",
    },
    'report': {
        'label': "Reporte de servicio firmado",
        'variables': 1, 'header': 'document', 'quick_reply': False,
        'nota': "Cabecera: el PDF del reporte · {{1}} texto que lo acompaña",
    },
    'booking_confirmed': {
        'label': "Cita confirmada (agendado por WhatsApp)",
        'variables': 2, 'header': 'none', 'quick_reply': False,
        'nota': "{{1}} nombre del cliente · {{2}} fecha y hora",
    },
    'hold_expired': {
        'label': "Apartado vencido",
        'variables': 1, 'header': 'none', 'quick_reply': False,
        'nota': "{{1}} fecha y hora que se soltó",
    },
    'hold_expired_link': {
        'label': "Apartado vencido (con liga de pago enviada)",
        'variables': 1, 'header': 'none', 'quick_reply': False,
        'nota': "{{1}} fecha y hora que se soltó",
    },
}

# Se puede asignar una plantilla que Meta todavía revisa: así se deja lista y
# empieza a usarse sola al aprobarse. Pero al runtime SOLO viajan las aprobadas.
ESTADOS_ASIGNABLES = ('approved', 'pending')


class VisarWaTemplateRoute(models.Model):
    _name = 'visar.wa.template.route'
    _inherit = ['visar.agent.runtime.mixin']
    _description = "Visar - Plantilla de WhatsApp de cada aviso"
    _order = 'sequence, id'

    key = fields.Selection(
        selection=[(clave, spec['label']) for clave, spec in ESPECIFICACION.items()],
        string="Aviso", required=True, readonly=True)
    sequence = fields.Integer(default=10)
    template_id = fields.Many2one(
        'whatsapp.template', string="Plantilla", ondelete='set null',
        domain=[('status', 'in', ESTADOS_ASIGNABLES)],
        help="Plantilla aprobada por Meta que se manda para este aviso. Vacío: "
             "se manda como mensaje libre, que solo llega a quien escribió en las "
             "últimas 24 horas.")
    template_status = fields.Selection(
        related='template_id.status', string="Estado en Meta")
    template_lang = fields.Selection(related='template_id.lang_code', string="Idioma")
    nota = fields.Char(string="Qué manda", compute='_compute_nota')

    _key_unique = models.Constraint(
        'unique(key)', "Cada aviso tiene una sola fila de plantilla.")

    @api.depends('key')
    def _compute_nota(self):
        for route in self:
            route.nota = ESPECIFICACION.get(route.key, {}).get('nota', '')

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    @api.constrains('template_id', 'key')
    def _check_plantilla_compatible(self):
        """Rechaza al guardar lo que Meta rechazaría al enviar."""
        for route in self.filtered('template_id'):
            spec = ESPECIFICACION[route.key]
            tpl = route.template_id
            etiqueta = spec['label']

            if tpl.status not in ESTADOS_ASIGNABLES:
                raise ValidationError(_(
                    "«%(tpl)s» está en estado %(estado)s en Meta: solo se pueden "
                    "asignar plantillas aprobadas o en revisión.",
                    tpl=tpl.name, estado=tpl.status))

            variables = len(tpl.variable_ids.filtered(
                lambda v: v.line_type == 'body'))
            if variables != spec['variables']:
                raise ValidationError(_(
                    "«%(tpl)s» tiene %(tiene)s variable(s) en el cuerpo, y el aviso "
                    "«%(aviso)s» manda %(manda)s: %(nota)s.",
                    tpl=tpl.name, tiene=variables, aviso=etiqueta,
                    manda=spec['variables'], nota=spec['nota']))

            if (tpl.header_type or 'none') != spec['header']:
                raise ValidationError(_(
                    "«%(tpl)s» tiene cabecera «%(tiene)s», y el aviso «%(aviso)s» "
                    "necesita «%(necesita)s».",
                    tpl=tpl.name, tiene=tpl.header_type or 'none',
                    aviso=etiqueta, necesita=spec['header']))

            botones = tpl.button_ids.sorted('sequence')
            if spec['quick_reply']:
                if len(botones) != 1 or botones.button_type != 'quick_reply':
                    raise ValidationError(_(
                        "El aviso «%(aviso)s» necesita exactamente un botón de "
                        "respuesta rápida: es el que el cliente pulsa para elegir "
                        "horario, y el runtime manda su payload en cada envío.",
                        aviso=etiqueta))
            elif botones:
                raise ValidationError(_(
                    "«%(tpl)s» tiene botones, y el aviso «%(aviso)s» no manda "
                    "parámetros de botón: Meta rechazaría el envío.",
                    tpl=tpl.name, aviso=etiqueta))

            # La plantilla pertenece a una cuenta, y la cuenta a un número: una
            # plantilla de otra cuenta no se puede mandar desde el número del
            # agente. Solo se comprueba si la config del agente tiene el número.
            config = self.env['visar.whatsapp.config'].sudo().search([], limit=1)
            numero_agente = (config.phone_uid or '').strip()
            numero_tpl = (tpl.wa_account_id.phone_uid or '').strip()
            if numero_agente and numero_tpl and numero_agente != numero_tpl:
                raise ValidationError(_(
                    "«%(tpl)s» es de la cuenta «%(cuenta)s», que no es la del "
                    "número del agente: no se puede mandar desde ese número.",
                    tpl=tpl.name, cuenta=tpl.wa_account_id.name))

    # ------------------------------------------------------------------
    # Lo que viaja al runtime
    # ------------------------------------------------------------------

    @api.model
    def _agent_payload(self):
        """`{clave: {"name", "lang"}}` de los avisos con plantilla APROBADA.

        Una fila vacía, o con una plantilla aún en revisión, pausada o rechazada,
        simplemente no aparece: para el runtime eso es "mensaje libre", el
        comportamiento de antes. Que falte una clave nunca es un error.
        """
        payload = {}
        for route in self.sudo().search([('template_id', '!=', False)]):
            tpl = route.template_id
            if tpl.status == 'approved' and tpl.template_name:
                payload[route.key] = {
                    'name': tpl.template_name,
                    'lang': tpl.lang_code or 'es_MX',
                }
        return payload

    # ------------------------------------------------------------------
    # Estado en Meta
    # ------------------------------------------------------------------

    @api.model
    def _visar_cron_sync_templates(self):
        """Refresca desde Meta el estado de las plantillas asignadas.

        El webhook de Meta de este número llega al RUNTIME, no a Odoo, así que
        `whatsapp.template.status` solo cambia cuando alguien pulsa *Sync*. Sin
        este cron, una plantilla que Meta pausa seguiría viajando al runtime como
        aprobada, y una recién aprobada no empezaría a usarse nunca.
        """
        plantillas = self.sudo().search([('template_id', '!=', False)]).mapped(
            'template_id')
        for tpl in plantillas:
            try:
                with self.env.cr.savepoint():
                    tpl.button_sync_template()
            except Exception:  # noqa: BLE001 - una plantilla no tumba el cron
                _logger.warning(
                    "No se pudo sincronizar la plantilla %s desde Meta",
                    tpl.template_name, exc_info=True)
