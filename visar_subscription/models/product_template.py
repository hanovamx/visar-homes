from odoo import fields, models


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
