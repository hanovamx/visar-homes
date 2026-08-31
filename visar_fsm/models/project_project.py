# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProjectProject(models.Model):
    _inherit = 'project.project'

    # Consolidación de servicios externos que se prestan en UNA sola visita.
    #
    # El ruteo servicio -> proyecto es configuración (`product.template.project_id`),
    # así que la regla de combo también tiene que serlo: no hay nombres ni ids de
    # proyecto en el código. Dos proyectos que apuntan al MISMO proyecto combinado
    # declaran "si la cita trae trabajo de ambos, es un solo servicio externo".
    # Dar de alta un tercer servicio combinable = marcar este campo en su proyecto.
    visar_fsm_combined_project_id = fields.Many2one(
        'project.project',
        string="Proyecto de servicios combinados",
        domain="[('is_fsm', '=', True), ('id', '!=', id)]",
        help="Cuando una misma cita incluye servicios de este proyecto Y de otro "
             "que apunta al mismo proyecto combinado, todo el trabajo se agenda "
             "como UN solo servicio externo en ese proyecto (una hoja de trabajo, "
             "una firma, un reporte). Vacío = este proyecto nunca se combina.")

    @api.constrains('visar_fsm_combined_project_id')
    def _check_visar_fsm_combined_project(self):
        """El destino es un punto final: ni a sí mismo, ni encadenado.

        Con una cadena (A -> B -> C) el ruteo dejaría de tener una respuesta única
        —¿la tarea va a B o a C?— y el proyecto anfitrión de una cita dependería del
        orden en que se leyeran las líneas. Mejor prohibirlo al configurar que
        depurarlo cuando el técnico ve la hoja equivocada.
        """
        for project in self:
            target = project.visar_fsm_combined_project_id
            if not target:
                continue
            if target == project:
                raise ValidationError(_(
                    "El proyecto de servicios combinados no puede ser el proyecto "
                    "mismo (%s).", project.display_name))
            if target.visar_fsm_combined_project_id:
                raise ValidationError(_(
                    "«%(target)s» ya apunta a otro proyecto de servicios "
                    "combinados. Los proyectos combinados no se encadenan: elige "
                    "uno que sea destino final.",
                    target=target.display_name))
            if self.search_count([('visar_fsm_combined_project_id', '=', project.id)]):
                raise ValidationError(_(
                    "«%(project)s» ya es el destino de otros proyectos, así que no "
                    "puede apuntar a su vez a uno combinado.",
                    project=project.display_name))

    # ------------------------------------------------------------------
    # Regla de consolidación (compartida por venta puntual y póliza)
    # ------------------------------------------------------------------
    def _visar_combined_target(self):
        """Proyecto combinado USABLE de este proyecto, o vacío.

        Un combinado archivado, que dejó de ser FSM o de otra compañía no puede
        recibir tareas: mejor dos servicios externos que uno roto.
        """
        self.ensure_one()
        combined = self.visar_fsm_combined_project_id
        if (not combined or not combined.active or not combined.is_fsm
                or (combined.company_id
                    and combined.company_id != self.company_id)):
            return self.browse()
        return combined

    @api.model
    def _visar_effective_projects(self, projects):
        """{id de proyecto origen: proyecto EFECTIVO} para el trabajo de UNA visita.

        El proyecto efectivo es el propio, salvo que la visita traiga trabajo de
        **dos o más** proyectos distintos que apunten al mismo combinado: una
        fumigación sola no debe caer en "Servicios combinados" (recibiría la hoja
        combinada y se le exigiría captura de áreas verdes que nadie contrató).

        Es el primitivo que comparten los DOS caminos que crean servicios externos —
        la confirmación de un pedido puntual (`visar_fsm/models/sale_order_fsm.py`) y
        la visita por periodo de una póliza (`visar_subscription`)—, que llegan al
        proyecto por campos distintos (`product.template.project_id` vs
        `visar_fsm_project_id`) pero obedecen la MISMA regla. Tenerla dos veces sería
        que el combo se consolidara en una venta y no en la póliza del mismo cliente.

        Sigue siendo configuración pura: ningún nombre ni id de proyecto en código.
        """
        sources_by_combined = {}
        for project in projects:
            combined = project._visar_combined_target()
            if combined:
                sources_by_combined.setdefault(combined.id, set()).add(project.id)
        active_combined = {
            combined_id for combined_id, source_ids in sources_by_combined.items()
            if len(source_ids) > 1
        }
        effective = {}
        for project in projects:
            combined = project._visar_combined_target()
            effective[project.id] = (
                combined if combined.id in active_combined else project)
        return effective
