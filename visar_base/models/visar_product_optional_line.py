# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class VisarProductOptionalLine(models.Model):
    _name = 'visar.product.optional.line'
    _description = "Add-on opcional de producto Visar"
    _order = 'product_tmpl_id, id'

    product_tmpl_id = fields.Many2one(
        'product.template', string="Servicio (producto)",
        required=True, ondelete='cascade', index=True)
    optional_product_id = fields.Many2one(
        'product.template', string="Producto add-on",
        required=True, ondelete='restrict', index=True)
    is_mandatory = fields.Boolean(
        "Obligatorio",
        default=False,
        help="Si está marcado, el add-on se agrega automáticamente a la cita con la cantidad indicada.")
    quantity = fields.Integer(
        "Cantidad", default=1, required=True,
        help="Unidades del add-on (por defecto si el cliente acepta una sugerencia opcional).")

    _sql_constraints = [
        (
            'unique_optional_per_service',
            'unique(product_tmpl_id, optional_product_id)',
            'Cada producto add-on solo puede aparecer una vez por servicio.',
        ),
    ]

    @api.constrains('product_tmpl_id', 'optional_product_id', 'quantity')
    def _check_optional_line(self):
        for line in self:
            if line.optional_product_id and line.product_tmpl_id:
                if line.optional_product_id == line.product_tmpl_id:
                    raise ValidationError(
                        "El producto add-on no puede ser el mismo servicio.")
            if line.quantity < 1:
                raise ValidationError(
                    "La cantidad del add-on '%s' debe ser al menos 1."
                    % (line.optional_product_id.display_name or ''))
