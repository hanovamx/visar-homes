from odoo import _, api, fields, models

# Producto de la línea que cobra las mensualidades adelantadas de la póliza.
VISAR_ANTICIPO_CODE = 'VISAR-ANTICIPO'


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    visar_generates_visit = fields.Boolean(
        string="Genera visita FSM por periodo",
        help="Si está activo, cada periodo facturado de la póliza crea una visita de "
             "servicio (tarea de Field Service) en el proyecto indicado.",
    )
    visar_fsm_project_id = fields.Many2one(
        'project.project',
        string="Proyecto FSM de la visita",
        domain=[('is_fsm', '=', True)],
        help="Proyecto de Field Service donde se crean las visitas de esta póliza.",
    )

    # ------------------------------------------------------------------
    # Producto de mensualidad anticipada
    # ------------------------------------------------------------------
    @api.model
    def _visar_get_anticipo_template(self, source_product=None):
        """Producto de la línea de mensualidad anticipada; lo crea si no existe.

        Los impuestos se copian del servicio que la línea va a espejear: la línea de
        anticipo debe cobrar exactamente lo mismo que el servicio, y `tax_ids` de la
        línea se recalcula desde `product.taxes_id` (vía posición fiscal) cada vez que
        cambia el partner. Si el producto no llevara impuesto, ese recálculo vaciaría
        el IVA de la línea y el total del carrito caería un 16% sin aviso.
        """
        param = self.env['ir.config_parameter'].sudo().get_param(
            'visar.anticipo_product_tmpl_id')
        if param and param.isdigit():
            tmpl = self.browse(int(param)).exists()
            if tmpl:
                return tmpl
        tmpl = self.sudo().search([('default_code', '=', VISAR_ANTICIPO_CODE)], limit=1)
        if tmpl:
            return tmpl

        if source_product:
            # Se respeta tal cual, incluso si es "sin impuestos": si el servicio no
            # lleva IVA, su anticipo tampoco debe llevarlo o los totales no cuadran.
            taxes = source_product.product_tmpl_id.taxes_id
        else:
            reference = self.sudo().search(
                [('visar_generates_visit', '=', True), ('taxes_id', '!=', False)], limit=1)
            taxes = reference.taxes_id
        # Un solo producto de anticipo para todos los servicios: hoy todos llevan el
        # mismo 16%. Si algún día conviven servicios con tasas distintas, hará falta
        # un producto de anticipo por grupo de impuestos.
        return self.sudo().create({
            'name': _("Mensualidad anticipada (póliza)"),
            'default_code': VISAR_ANTICIPO_CODE,
            'type': 'service',
            'invoice_policy': 'order',
            'list_price': 0.0,
            'sale_ok': True,
            'purchase_ok': False,
            'recurring_invoice': False,
            'visar_generates_visit': False,
            'taxes_id': [(6, 0, taxes.ids)] if taxes else False,
        })
