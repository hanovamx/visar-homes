# -*- coding: utf-8 -*-
"""La visita de seguimiento se acuerda en la puerta (22-sep-2026).

Un tratamiento de termitas o chinches casi siempre necesita una segunda visita para
confirmar que la plaga no volvió. Va incluida: no se cobra.

**La fecha la acuerdan el técnico y el cliente en el momento**, que es el único rato
en que los dos están juntos, y el técnico la captura en el cierre de su hoja
(`x_requiere_seguimiento`, `x_fecha_seguimiento`, `x_franja_seguimiento`). Con eso,
Odoo crea la visita en el mismo proyecto, con los mismos técnicos y sin cargo. Las
alternativas se descartaron por lo que cuestan: dejarlo en una nota obliga a oficina
a llamar otra vez, y mandarlo al agente por WhatsApp le pide al cliente que elija
horario cuando ya lo acordó de viva voz.

Sin fecha (el cliente no la quiso fijar), no se crea nada: queda la marca en la hoja
y la nota en el chatter para que oficina lo agende.

La franja y no una hora exacta: la app solo captura fechas, y al cliente se le
promete una ventana, igual que en el agendado.
"""
import logging
from datetime import timedelta

import pytz

from markupsafe import Markup

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

CAMPO_REQUIERE = 'x_requiere_seguimiento'
CAMPO_FECHA = 'x_fecha_seguimiento'
CAMPO_FRANJA = 'x_franja_seguimiento'
# Hora local de inicio de cada franja, y cuánto dura la visita de revisión.
FRANJAS = {'Mañana': (9, 4), 'Tarde': (13, 4)}
TZ_PARAM = 'visar.agent.timezone'
TZ_DEFECTO = 'America/Monterrey'


class ProjectTask(models.Model):
    _inherit = 'project.task'

    visar_followup_origin_task_id = fields.Many2one(
        'project.task', string="Seguimiento de", readonly=True, copy=False,
        index='btree_not_null',
        help="Visita de tratamiento que acordó esta revisión con el cliente.")
    visar_followup_task_ids = fields.One2many(
        'project.task', 'visar_followup_origin_task_id',
        string="Visita de seguimiento", readonly=True)

    # ------------------------------------------------------------------
    def _visar_followup_request(self):
        """(requiere, fecha, franja) de la hoja de esta visita.

        Se lee por NOMBRE de campo: las dos hojas de tratamiento los declaran
        iguales (`hooks.py::_seed_seguimiento_fields`), y una hoja que no los tenga
        —fumigación, jardinería— simplemente no pide seguimiento.
        """
        self.ensure_one()
        plantilla = self.sudo().worksheet_template_id
        modelo = plantilla.model_id.model if plantilla else False
        if not modelo or modelo not in self.env:
            return False, False, ''
        Hoja = self.env[modelo].sudo()
        if CAMPO_REQUIERE not in Hoja._fields:
            return False, False, ''
        hoja = Hoja.search([('x_project_task_id', '=', self.id)], limit=1,
                           order='create_date desc')
        if not hoja:
            return False, False, ''
        return bool(hoja[CAMPO_REQUIERE]), hoja[CAMPO_FECHA], hoja[CAMPO_FRANJA] or ''

    def _visar_followup_window(self, fecha, franja):
        """(inicio, fin) en UTC de la franja acordada, o (False, False)."""
        if not fecha:
            return False, False
        etiqueta = (franja or '').split(' ')[0]
        hora, duracion = FRANJAS.get(etiqueta, FRANJAS['Mañana'])
        nombre_tz = self.env['ir.config_parameter'].sudo().get_param(
            TZ_PARAM, TZ_DEFECTO)
        try:
            zona = pytz.timezone(nombre_tz)
        except pytz.UnknownTimeZoneError:  # pragma: no cover - configuración rota
            zona = pytz.timezone(TZ_DEFECTO)
        local = zona.localize(fields.Datetime.to_datetime(
            "%s %02d:00:00" % (fields.Date.to_string(fecha), hora)))
        inicio = local.astimezone(pytz.utc).replace(tzinfo=None)
        return inicio, inicio + timedelta(hours=duracion)

    def _visar_followup_sync(self, employee=None):
        """Crea (o reagenda) la visita de seguimiento que dice la hoja. Idempotente.

        Una por visita de tratamiento. Si el técnico corrige la fecha antes de
        cerrar, la visita se mueve; si desmarca el seguimiento, la visita se
        cancela mientras nadie la haya empezado — ya empezada es trabajo de
        oficina y no se toca.
        """
        self.ensure_one()
        requiere, fecha, franja = self._visar_followup_request()
        seguimiento = self.sudo().visar_followup_task_ids[:1]
        if not requiere or not fecha:
            if seguimiento and not seguimiento.visar_enroute_at \
                    and seguimiento.stage_id == self._visar_fsm_stage(0):
                seguimiento.unlink()
            return self.browse()
        inicio, fin = self._visar_followup_window(fecha, franja)
        vals = {'planned_date_begin': inicio, 'date_deadline': fin}
        if seguimiento:
            if seguimiento.planned_date_begin != inicio:
                seguimiento.sudo().write(vals)
            return seguimiento
        vals.update({
            'name': _("Seguimiento — %s", self.name or ''),
            'project_id': self.project_id.id,
            'partner_id': self.partner_id.id,
            'visar_followup_origin_task_id': self.id,
            'description': Markup(
                "<p>Revisión incluida del tratamiento, acordada con el cliente "
                "durante la visita <b>%s</b>. Sin cargo.</p>") % (self.name or ''),
        })
        if self.visar_technician_ids:
            vals['visar_technician_ids'] = [(6, 0, self.visar_technician_ids.ids)]
        if self.user_ids:
            vals['user_ids'] = [(6, 0, self.user_ids.ids)]
        seguimiento = self.env['project.task'].sudo().create(vals)
        quien = employee.name if employee else _("el técnico")
        self.message_post(body=Markup(
            "<p>Se acordó con el cliente una <b>visita de seguimiento</b> para el "
            "%s (%s), sin cargo. %s la capturó en la hoja; la visita ya está "
            "creada: <b>%s</b>.</p>") % (
                fields.Date.to_string(fecha), franja or "sin franja", quien,
                seguimiento.name), subtype_xmlid='mail.mt_note')
        return seguimiento
