# -*- coding: utf-8 -*-
"""Consumo de material en un servicio.

Lo que se gasta ADEMÁS del plaguicida: el guardapolvo instalado, la trampa
colocada, el cebo repuesto. El plaguicida se queda donde estaba —dentro de cada
área tratada de la hoja, porque la dosis depende del área y de la plaga, y así se
imprime en el reporte firmado—; esto es el resto.

**Por qué cuelga de la TAREA y no de la hoja de trabajo:** cada plantilla de hoja
tiene su propio modelo de líneas (el m2o de vuelta apunta a un modelo concreto), así
que meter esto "en la hoja" obligaría a un modelo de consumo por plantilla —seis hoy,
y uno más cada vez que nazca un servicio—. Con un modelo sobre la tarea hay UNO y
sirve para toda hoja presente y futura.

**Recorrido (odómetro): retirado el 24-sep-2026.** Se implantó el día anterior —tres
lecturas (entrar, llegar, cerrar jornada) para medir la gasolina por kilómetros— y
Visar pidió quitarlo del flujo del técnico por ahora. La implementación completa
está en el commit `f2ce6e9` por si se retoma.
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


class ProjectTaskConsumoMaterial(models.Model):
    _inherit = 'project.task'

    visar_consumo_ids = fields.One2many(
        'visar.field.consumo', 'task_id', string="Material consumido")
