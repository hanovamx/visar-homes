# -*- coding: utf-8 -*-
"""Días sin servicio: festivos y días que Visar decide no trabajar (13-nov-2026).

Hasta hoy no había dónde decirlo. El festivo del calendario de trabajo del recurso
(lo nativo) **no** lo respetaba la agenda de Visar —probado en `visar-test`: se
seguía ofreciendo el día— y `hr_holidays` no está instalado.

```
oficina   Citas → Configuración → Días sin servicio: fecha, motivo, todo el día
          o solo un horario («24-dic, desde las 14:00»)
sistema   ese hueco no se ofrece a NADIE: web, agente de WhatsApp (agendar y
          reagendar), botón «Agendar» de oficina y pre-agenda de suscripciones
          (que pasa la visita al siguiente día con hueco)
```

**Un solo punto:** `appointment.type._get_resources_remaining_capacity` devuelve
cero en un hueco que cae en un día sin servicio. Es el punto por el que pasan
todos los caminos, incluida la validación final al enviar el formulario de cita.

**Festivos oficiales precargados** (Ley Federal del Trabajo, art. 74): un cron los
siembra para el año en curso y el siguiente. Oficina los puede archivar si ese día
sí trabaja; archivado no se vuelve a sembrar. Y puede añadir los que quiera
(«mañana no abrimos»).

**Las citas que ya existían ese día no se tocan** (decisión del 13-nov-2026): el
registro dice cuántas hay y abre la lista; oficina decide si las mueve.
"""
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import MO, relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

TZ_DEFECTO = 'America/Monterrey'


def festivos_oficiales(anio):
    """[(fecha, motivo)] de descanso obligatorio en México (LFT, art. 74)."""
    dias = [
        (date(anio, 1, 1), "Año Nuevo"),
        (date(anio, 2, 1) + relativedelta(weekday=MO(1)), "Día de la Constitución"),
        (date(anio, 3, 1) + relativedelta(weekday=MO(3)), "Natalicio de Benito Juárez"),
        (date(anio, 5, 1), "Día del Trabajo"),
        (date(anio, 9, 16), "Día de la Independencia"),
        (date(anio, 11, 1) + relativedelta(weekday=MO(3)), "Día de la Revolución"),
        (date(anio, 12, 25), "Navidad"),
    ]
    # Transmisión del Poder Ejecutivo Federal: cada seis años (2024, 2030…).
    if anio >= 2024 and (anio - 2024) % 6 == 0:
        dias.append((date(anio, 10, 1), "Transmisión del Poder Ejecutivo Federal"))
    return sorted(dias)


class VisarDiaSinServicio(models.Model):
    _name = 'visar.dia.sin.servicio'
    _description = "Visar - Día sin servicio (festivo o día que no se trabaja)"
    _order = 'fecha, hora_desde, id'
    _rec_name = 'motivo'

    fecha = fields.Date(required=True, index=True)
    motivo = fields.Char(required=True)
    todo_el_dia = fields.Boolean(
        string="Todo el día", default=True,
        help="Desmarcado: solo el horario indicado queda sin servicio; el resto "
             "del día se agenda normal.")
    hora_desde = fields.Float(
        string="Sin servicio desde", default=0.0,
        help="Hora local de Monterrey. Ej.: 14:00 para «hasta las 2 se trabaja».")
    hora_hasta = fields.Float(string="Hasta", default=24.0)
    oficial = fields.Boolean(
        string="Festivo oficial", readonly=True,
        help="Lo sembró el sistema (Ley Federal del Trabajo). Si ese día sí se "
             "trabaja, archívelo: no se vuelve a crear.")
    active = fields.Boolean(default=True)
    dia_semana = fields.Char(string="Día", compute='_compute_dia_semana')
    citas_count = fields.Integer(
        string="Citas ya agendadas", compute='_compute_citas',
        help="Citas que ya estaban en ese horario. No se mueven solas: oficina "
             "decide qué hacer con ellas.")

    @api.constrains('todo_el_dia', 'hora_desde', 'hora_hasta')
    def _check_horas(self):
        for dia in self:
            if not dia.todo_el_dia and not (0 <= dia.hora_desde < dia.hora_hasta <= 24):
                raise ValidationError(_(
                    "El horario sin servicio va de 0 a 24, y «desde» antes que "
                    "«hasta»."))

    @api.depends('fecha')
    def _compute_dia_semana(self):
        nombres = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado",
                   "domingo"]
        for dia in self:
            dia.dia_semana = nombres[dia.fecha.weekday()] if dia.fecha else ''

    # ------------------------------------------------------------------
    # El hueco, en UTC
    # ------------------------------------------------------------------

    @api.model
    def _visar_tz(self):
        tz = self.env['ir.config_parameter'].sudo().get_param(
            'visar.agent.timezone') or TZ_DEFECTO
        try:
            return pytz.timezone(tz)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(TZ_DEFECTO)

    def _visar_intervalo_utc(self, tz=None):
        """(inicio, fin) en UTC naive del hueco sin servicio de este registro."""
        self.ensure_one()
        tz = tz or self._visar_tz()
        desde, hasta = (0.0, 24.0) if self.todo_el_dia else (self.hora_desde, self.hora_hasta)
        base = datetime.combine(self.fecha, time())

        def a_utc(horas):
            local = tz.localize(base + timedelta(hours=horas))
            return local.astimezone(pytz.utc).replace(tzinfo=None)
        return a_utc(desde), a_utc(hasta)

    @api.model
    def _visar_intervalos(self, desde_utc=None, hasta_utc=None):
        """[(inicio, fin)] UTC de los días sin servicio activos (de ese rango)."""
        dominio = []
        if desde_utc:
            dominio.append(('fecha', '>=', (desde_utc - timedelta(days=1)).date()))
        if hasta_utc:
            dominio.append(('fecha', '<=', (hasta_utc + timedelta(days=1)).date()))
        tz = self._visar_tz()
        return [dia._visar_intervalo_utc(tz)
                for dia in self.sudo().search(dominio)]

    @api.model
    def _visar_choca(self, inicio_utc, fin_utc, intervalos=None):
        """¿El hueco [inicio, fin) toca algún día sin servicio?"""
        if intervalos is None:
            intervalos = self._visar_intervalos(inicio_utc, fin_utc)
        return any(i < fin_utc and f > inicio_utc for i, f in intervalos)

    # ------------------------------------------------------------------
    # Las citas que ya estaban
    # ------------------------------------------------------------------

    def _visar_citas_domain(self):
        self.ensure_one()
        inicio, fin = self._visar_intervalo_utc()
        return [('appointment_type_id', '!=', False),
                ('start', '<', fin), ('stop', '>', inicio)]

    def _compute_citas(self):
        Event = self.env['calendar.event'].sudo()
        for dia in self:
            dia.citas_count = (Event.search_count(dia._visar_citas_domain())
                               if dia.fecha else 0)

    def action_ver_citas(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Citas del %s", self.fecha.strftime('%d/%m/%Y')),
            'res_model': 'calendar.event',
            'view_mode': 'list,form',
            'domain': self._visar_citas_domain(),
        }

    # ------------------------------------------------------------------
    # Festivos oficiales
    # ------------------------------------------------------------------

    @api.model
    def _visar_cron_festivos(self):
        """Siembra los festivos oficiales del año en curso y del siguiente.

        Idempotente, y respeta a oficina: si ya existe el de esa fecha —aunque
        esté ARCHIVADO porque ese día sí se trabaja— no se vuelve a crear.
        """
        hoy = fields.Date.context_today(self)
        existentes = set(self.with_context(active_test=False).sudo().search([
            ('oficial', '=', True),
            ('fecha', '>=', date(hoy.year, 1, 1)),
        ]).mapped('fecha'))
        nuevos = [{'fecha': fecha, 'motivo': motivo, 'oficial': True}
                  for anio in (hoy.year, hoy.year + 1)
                  for fecha, motivo in festivos_oficiales(anio)
                  if fecha not in existentes]
        self.sudo().create(nuevos)
        return len(nuevos)

    def unlink(self):
        """Un festivo oficial se ARCHIVA en vez de borrarse: borrado, el cron lo
        volvería a sembrar y el día que sí se trabaja dejaría de ofrecerse."""
        oficiales = self.filtered('oficial')
        oficiales.write({'active': False})
        return super(VisarDiaSinServicio, self - oficiales).unlink()
