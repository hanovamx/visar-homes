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

        NO se filtra por TIPO: un producto de servicio (una poda que el técnico
        detecta en sitio) se vende en campo igual que una malla. Lo único que se
        exige es que se pueda COBRAR ahí mismo, que es lo que el flujo promete.

        `recurring_invoice` se excluye SIEMPRE, aunque alguien marque el flag por
        error: un producto de suscripción vendido como extra puntual dejaría al
        cliente con un cobro recurrente que nadie pidió. Es el mismo criterio que
        aplica Odoo nativo en `industry_fsm_sale_subscription`.

        `invoice_policy='order'` por la misma razón, medida en `visar-test`
        (17-sep-2026): un producto que factura por ENTREGA (o por horas
        capturadas) confirma su pedido y se queda en "Nada que facturar", así que
        `_visar_upsell_confirm` no emite factura y el técnico se queda sin liga de
        pago, con el cobro a medias delante del cliente. Los dos productos de
        upsell que existen hoy ya facturan por pedido.
        """
        return [
            ('visar_upsell_ok', '=', True),
            ('sale_ok', '=', True),
            ('recurring_invoice', '=', False),
            ('invoice_policy', '=', 'order'),
        ]
