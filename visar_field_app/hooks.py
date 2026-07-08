# -*- coding: utf-8 -*-
"""Sembrador idempotente de las plantillas de hoja de trabajo (worksheet.template)
de la App de Campo Visar.

Estas plantillas NO pueden vivir en XML puro: cada `worksheet.template` autogenera
un modelo dinámico cuyo nombre incluye el id del template
(`x_project_task_worksheet_template_<id>`), y los campos viven en ese modelo. Por eso
el "sembrador" es este código Python — la fuente de verdad versionada y reproducible.

Uso:
  * En instalación limpia corre solo (wired como `post_init_hook` en el manifest).
  * En una BD donde el módulo YA está instalado (p. ej. producción), ejecutar a mano:

        python odoo-bin shell -c <conf>
        >>> from odoo.addons.visar_field_app.hooks import seed_worksheet_templates
        >>> seed_worksheet_templates(env)
        >>> env.cr.commit()

Es idempotente: busca-o-crea plantillas por NOMBRE, campos por nombre, y reescribe el
arch de la vista al estado canónico. Re-ejecutar es seguro (pero sobrescribe ediciones
manuales del arch hechas en Studio).
"""
import logging

_logger = logging.getLogger(__name__)

# --- catálogos de opciones ---
FACTORES = [
    "Acumulación de basura", "Cajas de cartón almacenadas", "Humedad o filtración visible",
    "Aberturas hacia el exterior", "Resumideros sin protección", "Puertas sin guardapolvo",
    "Alimento expuesto", "Saneamiento inadecuado", "Mantenimiento inadecuado del inmueble", "Otro",
]
PLAGAS = ["Voladores", "Rastreros", "Roedores", "Termitas", "Polilla", "Chinches", "Otros"]
NIVEL = ["Preventivo", "Moderado", "Alto"]
AREAS = ["Cocina", "Baño", "Áreas comunes", "Sala", "Bodega", "Oficina", "Dormitorios",
         "Jardines", "Rampas", "Área de basura", "Bardas", "Otros"]
PLAGUICIDAS = ["Cipermetrina", "Deltametrina", "Lambda-cialotrina", "Fipronil", "Imidacloprid",
               "Ácido bórico", "Brodifacoum", "Bromadiolona", "Otro"]
ACCION = ["No requiere", "Retiro de residuos", "Sellado de aberturas", "Instalación de tapón",
          "Instalación de guardapolvo", "Otro"]
TIPO_SERVICIO = ["Corte de pasto", "Orilleo", "Poda de setos", "Aireación de suelo", "Deshierbe",
                 "Aplicación de herbicida", "Fertilización", "Otro"]
ESTADO_EQUIPO = ["Funcional", "Cabezal desgastado", "Cabezal roto", "Nylon bajo",
                 "Máquina requiere revisión"]
TIPO_INMUEBLE = ["Bodega", "Casa", "Departamento", "Local comercial", "Oficina", "Otro"]
COMPLEJIDAD = ["Básica", "Intermedia", "Severa"]
SERVICIOS_ID = ["Fumigación", "Jardinería", "Riego", "Diseño de jardín", "Termitas",
                "Chinches", "Trabajos en altura", "Mantenimiento especializado", "Otro"]

OTRO = "Especifique cuál otro"
# La plantilla de Visita usa esta redacción de companion (según pliego del cliente).
OTRO_VISITA = "Especifica qué otro"

FUMIGACION_NAME = "Fumigación interior o exterior (App v2)"
JARDINERIA_NAME = "Mantenimiento de áreas verdes (App v2)"
VISITA_NAME = "Visita de valoración técnica (App v2)"
FUM_LINE = "x_visar_area_tratada_v2"
JAR_LINE = "x_visar_labor_jardineria"
VISITA_LINE = "x_visar_zona_evidencia"
FACTOR_MODEL = "x_visar_factor_riesgo"
PLAGA_MODEL = "x_visar_plaga"
SERVICIO_MODEL = "x_visar_servicio_identificado"

FUMIGACION_ARCH = """<form create="false" duplicate="false">
  <sheet>
    <notebook>
      <page string="Inspección inicial">
        <group>
          <field name="x_recorrido_completo" required="1" help="Confirma que se revisó todo el inmueble antes de aplicar cualquier producto."/>
          <field name="x_nivel_infestacion" required="1" help="Preventivo: sin infestación visible. Moderado: presencia ocasional. Alto: infestación activa o problema complejo."/>
          <field name="x_factores_riesgo" widget="many2many_tags" help="Selecciona todas las condiciones que apliquen."/>
          <field name="x_factores_riesgo_otro"/>
          <field name="x_foto_inicial" widget="image" required="1" help="Foto general del área con mayor problema antes de iniciar."/>
          <field name="x_descripcion_zona" placeholder="Ej. Cucarachas visibles bajo el fregadero de la cocina"/>
        </group>
      </page>
      <page string="Ejecución del tratamiento">
        <group>
          <field name="x_foto_ejecucion" widget="image" required="1" help="Foto representativa del tratamiento aplicado."/>
        </group>
        <field name="x_areas_tratadas" help="Llene la información para cada área tratada.">
          <list>
            <field name="x_area"/>
            <field name="x_plaga_ids" widget="many2many_tags"/>
            <field name="x_infestacion_activa"/>
            <field name="x_plaguicida_nombre"/>
            <field name="x_plaguicida_dosis"/>
            <field name="x_trampa_monitoreo"/>
            <field name="x_accion_correctiva"/>
          </list>
          <form>
            <group>
              <field name="x_area" required="1"/>
              <field name="x_area_otro"/>
              <field name="x_plaga_ids" widget="many2many_tags" required="1" help="Alinea con las plagas reportadas al cotizar."/>
              <field name="x_plaga_ids_otro"/>
              <field name="x_infestacion_activa"/>
              <group>
                <field name="x_plaguicida_nombre" required="1" help="Si no aparece en la lista, selecciona 'Otro' y descríbelo."/>
                <field name="x_plaguicida_dosis" placeholder="20"/>
              </group>
              <field name="x_plaguicida_nombre_otro"/>
              <field name="x_trampa_monitoreo"/>
              <field name="x_foto_evidencia" widget="image" help="Obligatoria si el área quedó marcada como 'Aplicado'."/>
              <field name="x_accion_correctiva"/>
              <field name="x_accion_correctiva_otro"/>
            </group>
          </form>
        </field>
      </page>
      <page string="Cierre">
        <group>
          <field name="x_comments"/>
        </group>
      </page>
    </notebook>
  </sheet>
</form>"""

JARDINERIA_ARCH = """<form create="false" duplicate="false">
  <sheet>
    <notebook>
      <page string="Inspección inicial">
        <group>
          <field name="x_foto_inicial_jardin" widget="image" required="1" help="Foto general del área antes de iniciar el servicio."/>
          <field name="x_indicaciones_cliente" placeholder="Ej. No cortar las rosas del lado izquierdo" help="Instrucciones específicas para el servicio de hoy."/>
          <field name="x_solicitudes_adicionales" placeholder="Ej. Cliente pregunta por poda de palma" help="Necesidades del cliente que NO son parte del servicio de hoy. Genera seguimiento comercial."/>
        </group>
      </page>
      <page string="Ejecución">
        <field name="x_labores" help="Llene la información para cada labor realizada, agregando cada una con el botón de '+ Agregar'.">
          <list>
            <field name="x_tipo_servicio"/>
            <field name="x_completado"/>
            <field name="x_observaciones"/>
          </list>
          <form>
            <group>
              <field name="x_tipo_servicio" required="1"/>
              <field name="x_tipo_servicio_otro"/>
              <field name="x_completado"/>
              <field name="x_observaciones" placeholder="Ej. Seto trasero requiere poda más profunda, agendar visita adicional"/>
            </group>
          </form>
        </field>
      </page>
      <page string="Cierre">
        <group>
          <field name="x_resultado_final" widget="image" required="1" help="Foto general del área al finalizar el servicio."/>
          <field name="x_area_limpia"/>
          <field name="x_residuos_embolsados"/>
          <field name="x_foto_bolsas" widget="image" help="Obligatoria si se recolectaron y embolsaron residuos vegetales."/>
          <field name="x_num_bolsas" placeholder="0" help="Se descuenta del inventario del almacén móvil de la cuadrilla."/>
          <field name="x_foto_bolsas_camioneta" widget="image" help="Evidencia de retiro de residuos de la propiedad del cliente."/>
          <field name="x_estado_equipo" help="Alimenta el mantenimiento preventivo de los equipos de la cuadrilla."/>
          <field name="x_comments"/>
        </group>
      </page>
    </notebook>
  </sheet>
</form>"""

VISITA_ARCH = """<form create="false" duplicate="false">
  <sheet>
    <notebook>
      <page string="Inspección inicial">
        <group>
          <field name="x_tipo_inmueble" required="1"/>
          <field name="x_tipo_inmueble_otro"/>
        </group>
        <field name="x_zonas_evidencia" help="Agrega cada zona inspeccionada con su descripción y foto (mínimo una).">
          <list>
            <field name="x_zona"/>
          </list>
          <form>
            <group>
              <field name="x_zona" required="1" placeholder="Ej. Patio trasero, techo, cuarto de lavado" help="Describe brevemente la zona antes de la foto."/>
              <field name="x_imagen_zona" widget="image" required="1"/>
            </group>
          </form>
        </field>
        <group>
          <field name="x_complejidad" required="1"/>
          <field name="x_servicios_identificados" widget="many2many_tags" required="1" help="Marca todos los servicios que aplican a esta valoración."/>
          <field name="x_servicios_identificados_otro"/>
          <field name="x_descripcion_problema" required="1"/>
          <field name="x_factores_condiciones"/>
          <field name="x_restricciones_acceso" placeholder="Ej. Portón con candado, mascota agresiva, horario limitado"/>
        </group>
      </page>
      <page string="Informe">
        <group>
          <field name="x_num_habitaciones" placeholder="0"/>
          <field name="x_superficie_m2" required="1" placeholder="0.00"/>
          <field name="x_materiales_especiales" placeholder="Ej. Andamio, taladro de inyección"/>
          <field name="x_num_visitas" required="1" placeholder="1"/>
          <field name="x_resumen_hallazgos" required="1" help="Este texto puede reutilizarse al armar la cotización formal."/>
        </group>
      </page>
    </notebook>
  </sheet>
</form>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sel(options):
    return [(0, 0, {'value': o, 'name': o, 'sequence': i * 10})
            for i, o in enumerate(options)]


def _acls(env, model_name, model_id):
    Access = env['ir.model.access'].sudo()
    for suffix, group, perms in [
            ('user', 'project.group_project_user', (1, 0, 0, 0)),
            ('mgr', 'project.group_project_manager', (1, 1, 1, 1))]:
        try:
            gid = env.ref(group).id
        except ValueError:
            continue
        name = '%s_%s' % (model_name, suffix)
        if not Access.search([('name', '=', name)], limit=1):
            Access.create({
                'name': name, 'model_id': model_id, 'group_id': gid,
                'perm_read': perms[0], 'perm_write': perms[1],
                'perm_create': perms[2], 'perm_unlink': perms[3]})


def _ensure_model(env, model_name, label, extra_fields):
    Model = env['ir.model'].sudo()
    rec = Model.search([('model', '=', model_name)], limit=1)
    if rec:
        return rec
    return Model.create({
        'name': label, 'model': model_name, 'state': 'manual',
        'field_id': [(0, 0, {'name': 'x_name', 'field_description': 'Nombre',
                             'ttype': 'char', 'required': True})] + extra_fields})


def _ensure_tag(env, model_name, label, records):
    Model = env['ir.model'].sudo()
    rec = Model.search([('model', '=', model_name)], limit=1)
    if not rec:
        rec = Model.create({
            'name': label, 'model': model_name, 'state': 'manual',
            'field_id': [(0, 0, {'name': 'x_name', 'field_description': 'Nombre',
                                 'ttype': 'char', 'required': True})]})
        _acls(env, model_name, rec.id)
    env.cr.flush()
    Tag = env[model_name].sudo()
    if Tag.search_count([]) == 0:
        for value in records:
            Tag.create({'x_name': value})
    return rec


def _ensure_field(env, model_name, model_id, name, ttype, string, **kw):
    Fields = env['ir.model.fields'].sudo()
    if Fields.search([('model', '=', model_name), ('name', '=', name)], limit=1):
        return
    vals = {'model_id': model_id, 'model': model_name, 'name': name,
            'field_description': string, 'ttype': ttype, 'state': 'manual'}
    if 'selection' in kw:
        vals['selection_ids'] = _sel(kw.pop('selection'))
    vals.update(kw)
    Fields.create(vals)


def _write_arch(env, ws_model, arch):
    view = env['ir.ui.view'].sudo().search(
        [('model', '=', ws_model), ('type', '=', 'form'), ('mode', '=', 'primary')],
        limit=1)
    if view:
        view.write({'arch': arch})


def _relabel_field(env, model, name, label):
    """Reetiqueta un campo YA existente (idempotente).

    `_ensure_field` no toca campos existentes, así que este helper es lo que aplica
    los cambios de etiqueta en BD donde el campo ya se creó (p. ej. producción)."""
    field = env['ir.model.fields'].sudo().search(
        [('model', '=', model), ('name', '=', name)], limit=1)
    if field and field.field_description != label:
        field.write({'field_description': label})


def _relabel_comments(env, ws_model, label):
    _relabel_field(env, ws_model, 'x_comments', label)


def _get_template(env, name):
    Template = env['worksheet.template'].sudo()
    tmpl = Template.search([('name', '=', name)], limit=1)
    if not tmpl:
        tmpl = Template.create({'name': name, 'res_model': 'project.task'})
    return tmpl


# ---------------------------------------------------------------------------
# Fumigación interior o exterior (App v2)
# ---------------------------------------------------------------------------
def _seed_fumigacion(env):
    tmpl = _get_template(env, FUMIGACION_NAME)
    ws, wid = tmpl.model_id.model, tmpl.model_id.id

    _ensure_tag(env, FACTOR_MODEL, "Factor de riesgo (Visar)", FACTORES)
    _ensure_tag(env, PLAGA_MODEL, "Plaga (Visar)", PLAGAS)

    line = _ensure_model(env, FUM_LINE, "Área tratada (Fumigación v2)", [
        (0, 0, {'name': 'x_worksheet_id', 'field_description': 'Worksheet',
                'ttype': 'many2one', 'relation': ws, 'required': True,
                'on_delete': 'cascade'}),
        (0, 0, {'name': 'x_sequence', 'field_description': 'Secuencia', 'ttype': 'integer'}),
    ])
    _acls(env, FUM_LINE, line.id)
    env.cr.flush()
    lid = line.id

    _ensure_field(env, FUM_LINE, lid, 'x_area', 'selection', 'Área', selection=AREAS)
    _ensure_field(env, FUM_LINE, lid, 'x_area_otro', 'char', OTRO)
    _ensure_field(env, FUM_LINE, lid, 'x_plaga_ids', 'many2many', 'Plaga a controlar',
                  relation=PLAGA_MODEL, relation_table='x_area_plaga_rel',
                  column1='area_id', column2='plaga_id')
    _ensure_field(env, FUM_LINE, lid, 'x_plaga_ids_otro', 'char', OTRO)
    _ensure_field(env, FUM_LINE, lid, 'x_infestacion_activa', 'boolean',
                  'Infestación activa en esta área')
    _ensure_field(env, FUM_LINE, lid, 'x_plaguicida_nombre', 'selection',
                  'Plaguicida — nombre', selection=PLAGUICIDAS)
    _ensure_field(env, FUM_LINE, lid, 'x_plaguicida_nombre_otro', 'char', OTRO)
    _ensure_field(env, FUM_LINE, lid, 'x_plaguicida_dosis', 'float', 'Plaguicida — dosis (ml)')
    _ensure_field(env, FUM_LINE, lid, 'x_trampa_monitoreo', 'boolean',
                  'Trampa de monitoreo colocada')
    _ensure_field(env, FUM_LINE, lid, 'x_foto_evidencia', 'binary', 'Fotos de evidencia')
    _ensure_field(env, FUM_LINE, lid, 'x_accion_correctiva', 'selection',
                  'Tipo de acción correctiva requerida', selection=ACCION)
    _ensure_field(env, FUM_LINE, lid, 'x_accion_correctiva_otro', 'char', OTRO)

    _ensure_field(env, ws, wid, 'x_recorrido_completo', 'boolean',
                  'Recorrido completo por el inmueble realizado')
    _ensure_field(env, ws, wid, 'x_nivel_infestacion', 'selection',
                  'Nivel de infestación', selection=NIVEL)
    _ensure_field(env, ws, wid, 'x_factores_riesgo', 'many2many',
                  'Factores de riesgo detectados', relation=FACTOR_MODEL,
                  relation_table='x_ws_factor_rel', column1='worksheet_id',
                  column2='factor_id')
    _ensure_field(env, ws, wid, 'x_factores_riesgo_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_foto_inicial', 'binary', 'Fotos estado inicial zona afectada')
    _ensure_field(env, ws, wid, 'x_descripcion_zona', 'text', 'Descripción de la zona afectada')
    _ensure_field(env, ws, wid, 'x_foto_ejecucion', 'binary', 'Fotos generales durante la ejecución')
    _ensure_field(env, ws, wid, 'x_areas_tratadas', 'one2many', 'Áreas tratadas',
                  relation=FUM_LINE, relation_field='x_worksheet_id')
    env.cr.flush()

    # Campos-foto en plural (ahora son galerías multi-foto).
    _relabel_field(env, ws, 'x_foto_inicial', 'Fotos estado inicial zona afectada')
    _relabel_field(env, ws, 'x_foto_ejecucion', 'Fotos generales durante la ejecución')
    _relabel_field(env, FUM_LINE, 'x_foto_evidencia', 'Fotos de evidencia')
    _relabel_comments(env, ws, 'Observaciones finales del técnico')
    _write_arch(env, ws, FUMIGACION_ARCH)
    tmpl._generate_qweb_report_template()
    _logger.info("Seeded worksheet template %s (%s)", FUMIGACION_NAME, ws)
    return tmpl


# ---------------------------------------------------------------------------
# Mantenimiento de áreas verdes (App v2)
# ---------------------------------------------------------------------------
def _seed_jardineria(env):
    tmpl = _get_template(env, JARDINERIA_NAME)
    ws, wid = tmpl.model_id.model, tmpl.model_id.id

    line = _ensure_model(env, JAR_LINE, "Labor de jardinería", [
        (0, 0, {'name': 'x_worksheet_id', 'field_description': 'Worksheet',
                'ttype': 'many2one', 'relation': ws, 'required': True,
                'on_delete': 'cascade'}),
        (0, 0, {'name': 'x_sequence', 'field_description': 'Secuencia', 'ttype': 'integer'}),
    ])
    _acls(env, JAR_LINE, line.id)
    env.cr.flush()
    lid = line.id

    _ensure_field(env, JAR_LINE, lid, 'x_tipo_servicio', 'selection', 'Tipo de servicio',
                  selection=TIPO_SERVICIO)
    _ensure_field(env, JAR_LINE, lid, 'x_tipo_servicio_otro', 'char', OTRO)
    _ensure_field(env, JAR_LINE, lid, 'x_completado', 'boolean', '¿Se completó?')
    _ensure_field(env, JAR_LINE, lid, 'x_observaciones', 'text', 'Observaciones')

    _ensure_field(env, ws, wid, 'x_foto_inicial_jardin', 'binary', 'Fotos estado inicial del jardín')
    _ensure_field(env, ws, wid, 'x_indicaciones_cliente', 'text', 'Indicaciones especiales del cliente')
    _ensure_field(env, ws, wid, 'x_solicitudes_adicionales', 'text', 'Solicitudes adicionales del cliente')
    _ensure_field(env, ws, wid, 'x_labores', 'one2many', 'Labor de jardinería',
                  relation=JAR_LINE, relation_field='x_worksheet_id')
    _ensure_field(env, ws, wid, 'x_resultado_final', 'binary', 'Resultado final del jardín')
    _ensure_field(env, ws, wid, 'x_area_limpia', 'boolean', 'Área limpia y en orden')
    _ensure_field(env, ws, wid, 'x_residuos_embolsados', 'boolean',
                  'Residuos vegetales recolectados y embolsados')
    _ensure_field(env, ws, wid, 'x_foto_bolsas', 'binary', 'Fotos de bolsas de residuos generadas')
    _ensure_field(env, ws, wid, 'x_num_bolsas', 'integer', 'Número de bolsas de residuos generadas')
    _ensure_field(env, ws, wid, 'x_foto_bolsas_camioneta', 'binary', 'Fotos de bolsas dentro de la camioneta')
    _ensure_field(env, ws, wid, 'x_estado_equipo', 'selection', 'Estado del equipo de jardinería',
                  selection=ESTADO_EQUIPO)
    env.cr.flush()

    # Campos-foto en plural (ahora son galerías multi-foto).
    _relabel_field(env, ws, 'x_foto_inicial_jardin', 'Fotos estado inicial del jardín')
    _relabel_field(env, ws, 'x_foto_bolsas', 'Fotos de bolsas de residuos generadas')
    _relabel_field(env, ws, 'x_foto_bolsas_camioneta', 'Fotos de bolsas dentro de la camioneta')
    _relabel_comments(env, ws, 'Observaciones finales del técnico')
    _write_arch(env, ws, JARDINERIA_ARCH)
    tmpl._generate_qweb_report_template()
    _logger.info("Seeded worksheet template %s (%s)", JARDINERIA_NAME, ws)
    return tmpl


# ---------------------------------------------------------------------------
# Visita de valoración técnica (App v2)
# ---------------------------------------------------------------------------
def _seed_visita(env):
    tmpl = _get_template(env, VISITA_NAME)
    ws, wid = tmpl.model_id.model, tmpl.model_id.id

    _ensure_tag(env, SERVICIO_MODEL, "Servicio identificado (Visar)", SERVICIOS_ID)

    line = _ensure_model(env, VISITA_LINE, "Zona de evidencia (Visita)", [
        (0, 0, {'name': 'x_worksheet_id', 'field_description': 'Worksheet',
                'ttype': 'many2one', 'relation': ws, 'required': True,
                'on_delete': 'cascade'}),
        (0, 0, {'name': 'x_sequence', 'field_description': 'Secuencia', 'ttype': 'integer'}),
    ])
    _acls(env, VISITA_LINE, line.id)
    env.cr.flush()
    lid = line.id

    _ensure_field(env, VISITA_LINE, lid, 'x_zona', 'char', 'Zona')
    _ensure_field(env, VISITA_LINE, lid, 'x_imagen_zona', 'binary', 'Imagen de la zona')

    _ensure_field(env, ws, wid, 'x_tipo_inmueble', 'selection', 'Tipo de inmueble',
                  selection=TIPO_INMUEBLE)
    _ensure_field(env, ws, wid, 'x_tipo_inmueble_otro', 'char', OTRO_VISITA)
    _ensure_field(env, ws, wid, 'x_zonas_evidencia', 'one2many', 'Evidencia de las zonas',
                  relation=VISITA_LINE, relation_field='x_worksheet_id')
    _ensure_field(env, ws, wid, 'x_complejidad', 'selection', 'Complejidad estimada',
                  selection=COMPLEJIDAD)
    _ensure_field(env, ws, wid, 'x_servicios_identificados', 'many2many',
                  'Servicios identificados', relation=SERVICIO_MODEL,
                  relation_table='x_visita_serv_rel', column1='worksheet_id',
                  column2='servicio_id')
    _ensure_field(env, ws, wid, 'x_servicios_identificados_otro', 'char', OTRO_VISITA)
    _ensure_field(env, ws, wid, 'x_descripcion_problema', 'text',
                  'Descripción detallada del problema encontrado')
    _ensure_field(env, ws, wid, 'x_factores_condiciones', 'text',
                  'Factores de riesgo o condiciones especiales')
    _ensure_field(env, ws, wid, 'x_restricciones_acceso', 'text',
                  'Restricciones o condiciones especiales de acceso')
    _ensure_field(env, ws, wid, 'x_num_habitaciones', 'integer',
                  'Número de habitaciones o espacios a tratar')
    _ensure_field(env, ws, wid, 'x_superficie_m2', 'float',
                  'Superficie del área específica a tratar (m²)')
    _ensure_field(env, ws, wid, 'x_materiales_especiales', 'char',
                  'Materiales o insumos especiales requeridos')
    _ensure_field(env, ws, wid, 'x_num_visitas', 'integer',
                  'Número estimado de visitas para resolver el problema')
    _ensure_field(env, ws, wid, 'x_resumen_hallazgos', 'text',
                  'Resumen de hallazgos comunicado al cliente')
    env.cr.flush()

    _write_arch(env, ws, VISITA_ARCH)
    tmpl._generate_qweb_report_template()
    _logger.info("Seeded worksheet template %s (%s)", VISITA_NAME, ws)
    return tmpl


def seed_worksheet_templates(env):
    """Crea/actualiza las plantillas de la App de Campo. Idempotente."""
    _seed_fumigacion(env)
    _seed_jardineria(env)
    _seed_visita(env)


def post_init_hook(env):
    seed_worksheet_templates(env)
