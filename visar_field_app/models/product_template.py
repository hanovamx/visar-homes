# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # --- Catálogo de venta en campo (upsell del técnico) ---
    # Es un flag propio y NO la categoría "Upsell" a secas: la categoría es contable
    # (cuentas de ingreso/gasto) y amarrar el catálogo de campo a ella obligaría a
    # mover la contabilidad de un producto para dejar de ofrecerlo. El sembrador lo
    # enciende una sola vez para lo que ya está en esa categoría; a partir de ahí
    # negocio lo administra desde la ficha del producto.
    visar_upsell_ok = fields.Boolean(
        string="Vendible en campo (upsell)", default=False,
        help="El técnico puede agregar este producto durante el servicio, desde la "
             "app de campo. Se cobra en un pedido aparte del servicio contratado.")

    @api.model
    def _visar_upsell_domain(self):
        """Dominio del catálogo ofrecible en campo.

        `recurring_invoice` se excluye SIEMPRE, aunque alguien marque el flag por
        error: un producto de suscripción vendido como extra puntual dejaría al
        cliente con un cobro recurrente que nadie pidió. Es el mismo criterio que
        aplica Odoo nativo en `industry_fsm_sale_subscription`.
        """
        return [
            ('visar_upsell_ok', '=', True),
            ('sale_ok', '=', True),
            ('recurring_invoice', '=', False),
        ]
