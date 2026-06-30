# -*- coding: utf-8 -*-
"""Proyectos FSM y asignación de productos Visar al confirmar pedidos."""

_FSM_PROJECT_MAP = [
    ('fumigacion', 'Fumigación', 'visar.fsm_project_fumigacion_id'),
    ('corte', 'Mantenimiento Áreas Verdes', 'visar.fsm_project_corte_id'),
]
_FSM_PROJECT_VALUATION = ('Valoraciones / Inspecciones', 'visar.fsm_project_valoracion_id')


def _visar_get_or_create_fsm_project(env, name, param_key):
    """Busca o crea un proyecto FSM por nombre; guarda el ID en ir.config_parameter."""
    Project = env['project.project'].sudo()
    Param = env['ir.config_parameter'].sudo()
    company = env.company

    existing_id = Param.get_param(param_key)
    if existing_id and existing_id.isdigit():
        project = Project.browse(int(existing_id)).exists()
        if project:
            return project

    project = Project.search([
        ('name', '=', name),
        ('is_fsm', '=', True),
        ('company_id', '=', company.id),
    ], limit=1)
    if not project:
        project = Project.create({
            'name': name,
            'is_fsm': True,
            'allow_timesheets': False,
            'company_id': company.id,
        })
    Param.set_param(param_key, str(project.id))
    return project


def _visar_setup_fsm_projects(env):
    """Crea los proyectos FSM y asigna service_tracking + project_id a los productos Visar."""
    ProductTemplate = env['product.template'].sudo()
    Dimension = env['visar.service.dimension'].sudo()

    project_by_prefix = {}
    for prefix, name, param_key in _FSM_PROJECT_MAP:
        project = _visar_get_or_create_fsm_project(env, name, param_key)
        project_by_prefix[prefix] = project

    val_name, val_param = _FSM_PROJECT_VALUATION
    valuation_project = _visar_get_or_create_fsm_project(env, val_name, val_param)

    for dimension in Dimension.search([('product_tmpl_id', '!=', False)]):
        code = dimension.code or ''
        prefix = code.split('_')[0] if '_' in code else code
        project = project_by_prefix.get(prefix)
        if not project:
            continue
        tmpl = dimension.product_tmpl_id
        vals = {}
        if tmpl.service_tracking != 'task_global_project':
            vals['service_tracking'] = 'task_global_project'
        if not tmpl.project_id or tmpl.project_id.id != project.id:
            vals['project_id'] = project.id
        if vals:
            tmpl.write(vals)

    for tmpl in ProductTemplate.search([('visar_is_valuation', '=', True)]):
        vals = {}
        if tmpl.service_tracking != 'task_global_project':
            vals['service_tracking'] = 'task_global_project'
        if not tmpl.project_id or tmpl.project_id.id != valuation_project.id:
            vals['project_id'] = valuation_project.id
        if vals:
            tmpl.write(vals)


def post_init_hook(env):
    _visar_setup_fsm_projects(env)
