# -*- coding: utf-8 -*-
"""Consumo de material y recorrido de la jornada.

Dos cosas que Visar necesita medir y que la hoja de trabajo no capturaba:

1. **Lo que se gasta además del plaguicida** — el guardapolvo instalado, la trampa
   colocada, el cebo repuesto. El plaguicida se queda donde estaba (dentro de cada
   área tratada: la dosis depende del área y de la plaga, y así se imprime en el
   reporte firmado); esto es el resto.
2. **El recorrido**, que es como se mide la gasolina sin inventar un tanque en el
   inventario. Tres lecturas del odómetro —al iniciar la jornada, al llegar a cada
   servicio y al cerrarla— y cada tramo sale por resta.

**Por qué cuelga de la TAREA y no de la hoja de trabajo:** cada plantilla de hoja
tiene su propio modelo de líneas (el m2o de vuelta apunta a un modelo concreto), así
que meter esto "en la hoja" obligaría a un modelo de consumo por plantilla —seis hoy,
y uno más cada vez que nazca un servicio—. Con un modelo sobre la tarea hay UNO, sirve
para toda hoja presente y futura, y —importante— NO entra en el PDF firmado: lo
aplicado es asunto del cliente, el odómetro del técnico no.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class VisarFieldConsumo(models.Model):
    _name = 'visar.field.consumo'
    _description = "Visar - Material consumido en un servicio"
    _order = 'id'

    task_id = fields.Many2one(
        'project.task', string="Servicio", required=True, ondelete='cascade',
        index=True)
    product_id = fields.Many2one(
        'product.product', string="Material", required=True,
        domain="[('visar_consumible_ok', '=', True), ('is_storable', '=', True)]")
    quantity = fields.Float(string="Cantidad", default=1.0, required=True)
    employee_id = fields.Many2one('hr.employee', string="Técnico")
    uom_name = fields.Char(string="Unidad", related='product_id.uom_id.name',
                           readonly=True)

    @api.constrains('quantity')
    def _check_quantity(self):
        for linea in self:
            if linea.quantity <= 0:
                raise ValidationError(_("La cantidad consumida tiene que ser mayor que cero."))


class VisarFieldSessionOdometro(models.Model):
    _inherit = 'visar.field.session'

    visar_odometer_start = fields.Integer(
        string="Kilometraje al iniciar",
        help="Lectura del odómetro cuando el técnico abre la jornada. Es el ANCLA "
             "de todos los tramos del día: sin ella no se puede medir el trayecto "
             "hasta el primer servicio.")
    visar_odometer_end = fields.Integer(
        string="Kilometraje al terminar",
        help="Lectura al cerrar la jornada. Solo se puede pedir si el técnico "
             "cierra sesión; si abandona la pestaña, se queda en cero y lo único "
             "que se pierde es el trayecto de vuelta (que no es de ningún cliente).")

    def _visar_odometer_last(self):
        """Última lectura registrada en la jornada: el suelo de la siguiente.

        El orden es el real del día: el arranque primero y luego las llegadas por
        hora de llegada.
        """
        self.ensure_one()
        lecturas = [self.visar_odometer_start or 0]
        tareas = self.env['project.task'].sudo().search(
            [('visar_odometer_session_id', '=', self.id),
             ('visar_odometer_arrival', '>', 0)])
        lecturas += tareas.mapped('visar_odometer_arrival')
        return max(lecturas)


class ProjectTaskRecorrido(models.Model):
    _inherit = 'project.task'

    visar_consumo_ids = fields.One2many(
        'visar.field.consumo', 'task_id', string="Material consumido")
    visar_odometer_arrival = fields.Integer(
        string="Kilometraje al llegar", copy=False,
        help="Lectura del odómetro al confirmar la llegada a este servicio.")
    visar_odometer_session_id = fields.Many2one(
        'visar.field.session', string="Jornada", copy=False, index=True,
        help="Jornada en la que se tomó la lectura; es la que da el tramo anterior.")
    visar_km_leg = fields.Float(
        string="Kilómetros del trayecto", compute='_compute_visar_km_leg', store=True,
        help="Distancia recorrida para LLEGAR a este servicio: la lectura de aquí "
             "menos la anterior de la jornada. Se le carga al servicio al que se "
             "iba, que es de quien es el viaje.")

    @api.depends('visar_odometer_arrival', 'visar_odometer_session_id')
    def _compute_visar_km_leg(self):
        for task in self:
            sesion = task.visar_odometer_session_id
            if not task.visar_odometer_arrival or not sesion:
                task.visar_km_leg = 0.0
                continue
            previas = [sesion.visar_odometer_start or 0]
            hermanas = self.sudo().search(
                [('visar_odometer_session_id', '=', sesion.id),
                 ('visar_odometer_arrival', '>', 0),
                 ('visar_odometer_arrival', '<', task.visar_odometer_arrival)])
            previas += hermanas.mapped('visar_odometer_arrival')
            task.visar_km_leg = max(task.visar_odometer_arrival - max(previas), 0)
