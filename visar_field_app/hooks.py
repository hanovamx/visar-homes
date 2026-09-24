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
# --- taxonomía de plagas (2 niveles) ---
# Nivel 1 = CATEGORÍA (el m2m principal de cada área tratada). Nivel 2 = especie,
# en un m2m companion por categoría que solo se muestra si su categoría está
# marcada. Las categorías replican las del wizard de reserva
# (`visar_appointment/views/wizard_templates.xml`, "¿Qué estás viendo en casa?")
# para que lo que el cliente reporta al cotizar y lo que el técnico confirma en
# campo hablen el mismo idioma.
PLAGA_RASTREROS = "Rastreros"
PLAGA_VOLADORES = "Voladores"
PLAGA_ROEDORES = "Roedores"
PLAGA_OTRAS = "Otras plagas"
# Escotilla de salida. OJO: no puede empezar con "Otro"/"Otros" a secas —
# `_otro_conditional` (convención de nombre) elegiría la etiqueta equivocada
# entre esta y "Otras plagas"; por eso su condición se declara EXPLÍCITAMENTE en
# `WORKSHEET_CONDITIONAL` (controllers/main.py) y no por convención.
PLAGA_OTRO = "Otra plaga no en las opciones"
PLAGAS = [PLAGA_RASTREROS, PLAGA_VOLADORES, PLAGA_ROEDORES, PLAGA_OTRAS, PLAGA_OTRO]

# (campo companion, modelo-etiqueta, categoría que lo revela, etiqueta, especies)
# Termitas / Chinches viven como ESPECIES bajo "Otras plagas": en el wizard
# aparecen solo como síntomas (madera dañada, picaduras en hilera), no como
# categorías con especies propias.
#
# Las especies de Rastreros y Voladores las dictó Visar (11-ago-2026) con el
# detalle que usan en campo: la especie manda el plaguicida y la dosis, así que
# "Cucarachas" a secas no servía —la alemana y la americana no se tratan igual—.
# POLILLA está aquí bajo Voladores, no bajo "Otras plagas" como antes: la lista de
# Visar la puso entre los voladores (vuela), y tenerla en dos categorías dejaría al
# técnico eligiendo entre dos opciones idénticas.
PLAGA_ESPECIES = [
    ('x_plaga_rastreros_ids', 'x_visar_plaga_rastrero', PLAGA_RASTREROS,
     "Rastreros — ¿cuáles?", [
         "Cucaracha alemana (chica)", "Cucaracha americana (grande)", "Araña",
         "Garrapata", "Larva (gusano)", "Hormiga", "Pescado de plata", "Alacrán",
         "Cochinilla", "Tijerillas"]),
    ('x_plaga_voladores_ids', 'x_visar_plaga_volador', PLAGA_VOLADORES,
     "Voladores — ¿cuáles?", [
         "Mosca de hogar", "Mosca de fruta", "Mosca de drenaje", "Zancudo",
         "Polilla", "Avispa y avispón"]),
    ('x_plaga_roedores_ids', 'x_visar_plaga_roedor', PLAGA_ROEDORES,
     "Roedores — ¿cuáles?", ["Ratas", "Ratones"]),
    ('x_plaga_otras_ids', 'x_visar_plaga_otra', PLAGA_OTRAS,
     "Otras plagas — ¿cuáles?", ["Termitas", "Chinches de cama"]),
]

# Áreas que SIEMPRE se inspeccionan: la hoja nace con estas tres tarjetas, no se
# pueden eliminar y sus campos son obligatorios (salvo que el cliente no haya
# permitido tratarlas). El técnico agrega las demás con "+ Agregar".
AREAS_FIJAS = ["Cocina", "Baño", "Área de basura"]
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
# --- Tratamientos especializados ---
TIPO_TERMITA = ["Subterránea", "De madera seca", "No identificada"]
ESTRUCTURAS = ["Marcos de puerta o ventana", "Vigas o techo de madera",
               "Pisos de madera", "Muebles", "Muros", "Jardín, tocones o cercas",
               "Otro"]
NIVEL_DANO = ["Leve", "Moderado", "Severo"]
METODO_TERMITA = ["Barrera química perimetral", "Inyección a muro o madera",
                  "Estaciones de cebo", "Aplicación localizada", "Otro"]
EVIDENCIAS_CHINCHE = ["Insectos vivos", "Huevos", "Exuvias (pieles)",
                      "Manchas de excremento", "Manchas de sangre",
                      "Picaduras reportadas por el cliente", "Otro"]
HABITACIONES = ["Recámara principal", "Recámara secundaria", "Sala", "Comedor",
                "Cuarto de servicio", "Otro"]
METODO_CHINCHE = ["Vapor", "Aspirado", "Químico residual", "Polvo desecante",
                  "Otro"]
# --- Diseño de jardín (24-sep-2026) ---
# Tercer servicio que se cotiza a mano. NO es una plaga: se vendía como adicional a
# precio fijo y resultó que el precio depende del levantamiento (superficie, suelo,
# acceso, qué se instala), así que pasa al mismo circuito que termitas y chinches.
TIPO_SUELO = ["Arcilloso", "Arenoso", "Mixto", "Con escombro o relleno", "Otro"]
EXPOSICION_SOL = ["Sol directo", "Media sombra", "Sombra"]
RIEGO_EXISTENTE = ["No hay", "Manguera", "Aspersión", "Goteo", "Otro"]
ELEMENTOS_JARDIN = ["Plantas o arbustos", "Árbol", "Pasto en rollo", "Pasto sembrado",
                    "Piedra decorativa", "Corteza o mulch", "Tierra o sustrato",
                    "Sistema de riego", "Iluminación", "Macetas o jardineras",
                    "Otro"]
UNIDAD_ELEMENTO = ["Piezas", "m²", "Metros lineales", "Bultos o costales"]

# La franja, y no una hora exacta: la app solo sabe capturar fechas, y al cliente
# se le promete una ventana igual que en el agendado.
FRANJA = ["Mañana (9:00 - 13:00)", "Tarde (13:00 - 17:00)"]
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
# Plantilla del COMBO: fumigación + áreas verdes prestadas en una sola visita.
# Lleva los DOS juegos de campos, con los MISMOS nombres `x_` que las plantillas
# individuales — de eso depende que todo lo que está indexado por nombre de campo
# (condicionales, obligatoriedad, maquetas del reporte) siga sirviendo tal cual.
COMBO_NAME = "Fumigación + Mantenimiento de áreas verdes (App v2)"
# Tratamientos que se cotizan a mano (22-sep-2026). Hoja propia y proyecto propio:
# no son fumigación —otro bicho, otro método, otra evidencia— y el tablero de
# gestión se lee mejor separado.
JARDIN_NAME = "Diseño de jardín (App v2)"
TERMITAS_NAME = "Tratamiento antitermita (App v2)"
CHINCHES_NAME = "Tratamiento antichinches (App v2)"
FUM_LINE = "x_visar_area_tratada_v2"
JAR_LINE = "x_visar_labor_jardineria"
VISITA_LINE = "x_visar_zona_evidencia"
# Modelos de línea propios del combo: el m2o `x_worksheet_id` apunta a UN modelo de
# hoja concreto, así que las subfichas del combo no pueden colgar de los modelos de
# línea de las plantillas individuales.
FUM_LINE_COMBO = "x_visar_area_tratada_combo"
JAR_LINE_COMBO = "x_visar_labor_combo"
JARDIN_LINE = "x_visar_elemento_jardin"
TERM_LINE = "x_visar_punto_termita"
CHIN_LINE = "x_visar_zona_chinche"
FACTOR_MODEL = "x_visar_factor_riesgo"
ESTRUCTURA_MODEL = "x_visar_estructura_termita"
EVIDENCIA_MODEL = "x_visar_evidencia_chinche"
HABITACION_MODEL = "x_visar_habitacion_chinche"
PLAGA_MODEL = "x_visar_plaga"
SERVICIO_MODEL = "x_visar_servicio_identificado"

# --- páginas del notebook, por servicio ---
# Los CUERPOS viven aquí una sola vez y se componen en las plantillas individuales
# y en la del combo (`_arch`). Así una página no puede quedar distinta entre la
# hoja de fumigación suelta y la mitad de fumigación del combo.
_FUM_BODY_INSPECCION = """
        <group>
          <field name="x_recorrido_completo" required="1" help="Confirma que se revisó todo el inmueble antes de aplicar cualquier producto."/>
          <field name="x_nivel_infestacion" required="1" help="Preventivo: sin infestación visible. Moderado: presencia ocasional. Alto: infestación activa o problema complejo."/>
          <field name="x_factores_riesgo" widget="many2many_tags" help="Selecciona todas las condiciones que apliquen."/>
          <field name="x_factores_riesgo_otro"/>
          <field name="x_foto_inicial" widget="image" required="1" help="Foto general del área con mayor problema antes de iniciar."/>
          <field name="x_descripcion_zona" placeholder="Ej. Cucarachas visibles bajo el fregadero de la cocina"/>
        </group>
      """

_FUM_BODY_EJECUCION = """
        <group>
          <field name="x_foto_ejecucion" widget="image" required="1" help="Foto representativa del tratamiento aplicado."/>
        </group>
        <field name="x_areas_tratadas" help="Llene la información para cada área tratada.">
          <list>
            <field name="x_area"/>
            <field name="x_cliente_no_permitio"/>
            <field name="x_infestacion_activa"/>
            <field name="x_plaga_ids" widget="many2many_tags"/>
            <field name="x_plaguicida_id"/>
            <field name="x_plaguicida_dosis"/>
            <field name="x_trampa_monitoreo"/>
            <field name="x_accion_correctiva"/>
          </list>
          <form>
            <group>
              <field name="x_cliente_no_permitio" help="Marca esto si no se pudo tratar el área. Al marcarlo, los demás campos de esta área dejan de ser obligatorios."/>
              <field name="x_area" required="1"/>
              <field name="x_area_otro"/>
              <field name="x_infestacion_activa"/>
              <field name="x_foto_evidencia" widget="image" required="1" help="Evidencia de la plaga detectada en esta área."/>
              <field name="x_plaga_ids" widget="many2many_tags" required="1" help="Alinea con las plagas reportadas al cotizar."/>
              <field name="x_plaga_rastreros_ids" widget="many2many_tags" required="1"/>
              <field name="x_plaga_voladores_ids" widget="many2many_tags" required="1"/>
              <field name="x_plaga_roedores_ids" widget="many2many_tags" required="1"/>
              <field name="x_plaga_otras_ids" widget="many2many_tags" required="1"/>
              <field name="x_plaga_ids_otro"/>
              <group>
                <field name="x_plaguicida_id" required="1" help="Solo aparece lo que traes cargado en tu ubicación, con la cantidad que te queda."/>
                <field name="x_plaguicida_dosis" placeholder="20" help="En la unidad del producto (la que aparece junto a la existencia)."/>
              </group>
              <field name="x_plaguicida_otro" placeholder="Solo si aplicaste algo que no aparece arriba"/>
              <field name="x_trampa_monitoreo"/>
              <field name="x_accion_correctiva"/>
              <field name="x_accion_correctiva_otro"/>
            </group>
          </form>
        </field>
      """

_JAR_BODY_INSPECCION = """
        <group>
          <field name="x_foto_inicial_jardin" widget="image" required="1" help="Foto general del área antes de iniciar el servicio."/>
          <field name="x_indicaciones_cliente" placeholder="Ej. No cortar las rosas del lado izquierdo" help="Instrucciones específicas para el servicio de hoy."/>
          <field name="x_solicitudes_adicionales" placeholder="Ej. Cliente pregunta por poda de palma" help="Necesidades del cliente que NO son parte del servicio de hoy. Genera seguimiento comercial."/>
        </group>
      """

_JAR_BODY_EJECUCION = """
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
      """

# Cierre de áreas verdes. Incluye `x_comments` (observaciones finales del técnico),
# que es UN solo campo por hoja: en el combo cierra las dos mitades.
_JAR_BODY_CIERRE = """
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
      """

_FUM_BODY_CIERRE = """
        <group>
          <field name="x_comments"/>
        </group>
      """


# --- Tratamientos especializados (22-sep-2026) ------------------------------
# El cierre es común a los dos: la visita de seguimiento se acuerda CON EL CLIENTE
# delante, que es el único momento en que las dos partes están juntas. Con la fecha
# puesta, Odoo crea la visita (ver `models/seguimiento.py`); sin ella, oficina la
# agenda a mano.
def _seg_body(motivo):
    """Los tres campos del acuerdo de seguimiento, con el POR QUÉ propio de cada
    servicio: al técnico de jardín no le sirve leer que la plaga puede volver."""
    return """
          <field name="x_requiere_seguimiento" help="%s"/>
          <field name="x_fecha_seguimiento" help="Acuérdala con el cliente antes de irte: con la fecha puesta, la visita se crea sola y sin costo."/>
          <field name="x_franja_seguimiento"/>""" % motivo


_SEG_BODY = _seg_body(
    "La mayoría de estos tratamientos necesita una segunda visita para confirmar"
    " que la plaga no volvió.")
# El jardín no se revisa por plaga sino por prendimiento: si lo plantado agarró.
_SEG_BODY_JARDIN = _seg_body(
    "El jardín se revisa a las pocas semanas para confirmar que lo plantado"
    " prendió y ajustar el riego.")

_TERM_BODY_INSPECCION = """
        <group>
          <field name="x_tipo_termita" required="1" help="Subterránea: tubos de lodo, humedad. De madera seca: montículos de serrín."/>
          <field name="x_estructuras_afectadas" widget="many2many_tags" required="1" help="Marca todo lo que tenga daño o actividad."/>
          <field name="x_estructuras_otro"/>
          <field name="x_nivel_dano" required="1"/>
          <field name="x_humedad_detectada" help="La humedad es lo que trae de vuelta a la termita subterránea."/>
          <field name="x_foto_inicial" widget="image" required="1" help="Foto del daño más representativo antes de tratar."/>
          <field name="x_descripcion_zona" placeholder="Ej. Tubos de lodo en el marco de la puerta del patio"/>
        </group>
      """

_TERM_BODY_EJECUCION = """
        <group>
          <field name="x_metodo_aplicacion" required="1"/>
          <field name="x_metodo_otro"/>
          <field name="x_foto_ejecucion" widget="image" required="1" help="Foto representativa del tratamiento aplicado."/>
        </group>
        <field name="x_puntos_tratados" help="Agrega cada punto tratado con su evidencia (mínimo uno).">
          <list>
            <field name="x_ubicacion"/>
            <field name="x_perforaciones"/>
          </list>
          <form>
            <group>
              <field name="x_ubicacion" required="1" placeholder="Ej. Marco de puerta del patio, viga del techo"/>
              <field name="x_perforaciones" placeholder="0" help="Cuántas perforaciones se hicieron en este punto (0 si no se perforó)."/>
              <field name="x_foto_evidencia" widget="image" required="1"/>
              <field name="x_observacion" placeholder="Ej. Madera hueca, se recomienda reemplazo"/>
            </group>
          </form>
        </field>
      """

_TERM_BODY_CIERRE = """
        <group>
          <field name="x_indicaciones_cliente" required="1" placeholder="Ej. No lavar la zona tratada por 72 horas; revisar filtraciones del patio."/>
          <field name="x_foto_final" widget="image"/>""" + _SEG_BODY + """
          <field name="x_comments"/>
        </group>
      """

_CHIN_BODY_INSPECCION = """
        <group>
          <field name="x_nivel_infestacion" required="1" help="Preventivo: sin evidencia viva. Moderado: evidencia en un cuarto. Alto: varios cuartos o insectos vivos a la vista."/>
          <field name="x_habitaciones_afectadas" widget="many2many_tags" required="1"/>
          <field name="x_habitaciones_otro"/>
          <field name="x_evidencia_encontrada" widget="many2many_tags" required="1" help="Lo que de verdad viste, no lo que reportó el cliente (eso va en 'picaduras reportadas')."/>
          <field name="x_evidencia_otro"/>
          <field name="x_foto_inicial" widget="image" required="1"/>
          <field name="x_descripcion_zona" placeholder="Ej. Manchas en costuras del colchón de la recámara principal"/>
        </group>
      """

# La preparación del cliente se pregunta aparte y ANTES de ejecutar: si la casa no
# se preparó, el tratamiento falla y el cliente lo reclama como garantía. Dejar
# constancia de qué se encontró protege a las dos partes.
_CHIN_BODY_PREPARACION = """
        <group>
          <field name="x_ropa_lavada" help="Ropa y blancos lavados a alta temperatura antes de la visita."/>
          <field name="x_colchones_despejados" help="Colchones y bases sin ropa de cama."/>
          <field name="x_desorden_retirado" help="Cajas, ropa y objetos bajo camas y muebles retirados."/>
          <field name="x_prep_observacion" placeholder="Ej. El cliente no lavó la ropa; se le explicó el riesgo de reinfestación."/>
        </group>
      """

_CHIN_BODY_EJECUCION = """
        <group>
          <field name="x_metodo_aplicacion" required="1"/>
          <field name="x_metodo_otro"/>
          <field name="x_foto_ejecucion" widget="image" required="1"/>
        </group>
        <field name="x_zonas_tratadas" help="Agrega cada zona o mueble tratado con su evidencia (mínimo uno).">
          <list>
            <field name="x_habitacion"/>
            <field name="x_mueble"/>
          </list>
          <form>
            <group>
              <field name="x_habitacion" required="1"/>
              <field name="x_habitacion_otro"/>
              <field name="x_mueble" required="1" placeholder="Ej. Colchón, base de cama, sillón, rodapié"/>
              <field name="x_foto_evidencia" widget="image" required="1"/>
              <field name="x_observacion" placeholder="Ej. Costuras con huevos; se aplicó vapor y polvo"/>
            </group>
          </form>
        </field>
      """

_CHIN_BODY_CIERRE = """
        <group>
          <field name="x_indicaciones_cliente" required="1" placeholder="Ej. No dormir fuera de la recámara tratada; no lavar el colchón por 7 días."/>
          <field name="x_foto_final" widget="image"/>""" + _SEG_BODY + """
          <field name="x_comments"/>
        </group>
      """


def _arch(pages):
    """Arma el arch del formulario a partir de una lista de (título, cuerpo)."""
    body = ''.join('<page string="%s">%s</page>\n      ' % (title, content)
                   for title, content in pages)
    return """<form create="false" duplicate="false">
  <sheet>
    <notebook>
      %s</notebook>
  </sheet>
</form>""" % body


FUMIGACION_ARCH = _arch([
    ("Inspección inicial", _FUM_BODY_INSPECCION),
    ("Ejecución del tratamiento", _FUM_BODY_EJECUCION),
    ("Cierre", _FUM_BODY_CIERRE),
])

JARDINERIA_ARCH = _arch([
    ("Inspección inicial", _JAR_BODY_INSPECCION),
    ("Ejecución", _JAR_BODY_EJECUCION),
    ("Cierre", _JAR_BODY_CIERRE),
])

# Combo: las dos mitades en el orden en que el técnico trabaja, con el servicio en
# el título de cada página (la app las pinta como encabezados de sección) y UN solo
# cierre — un servicio, una firma, un reporte.
COMBO_ARCH = _arch([
    ("Fumigación — Inspección inicial", _FUM_BODY_INSPECCION),
    ("Fumigación — Ejecución del tratamiento", _FUM_BODY_EJECUCION),
    ("Áreas verdes — Inspección inicial", _JAR_BODY_INSPECCION),
    ("Áreas verdes — Ejecución", _JAR_BODY_EJECUCION),
    ("Cierre", _JAR_BODY_CIERRE),
])

# --- Diseño de jardín ---
# El levantamiento se captura AUNQUE el servicio ya venga cotizado: entre la
# valoración y el día de la obra pueden cambiar las condiciones (llovió, hay
# escombro nuevo, ya no cabe la maquinaria), y eso es lo que explica un ajuste de
# alcance delante del cliente.
_JARD_BODY_LEVANTAMIENTO = """
        <group>
          <field name="x_superficie_m2" required="1" placeholder="0" help="Metros cuadrados que se van a intervenir hoy."/>
          <field name="x_tipo_suelo" required="1" help="Manda la preparación: un suelo con escombro necesita retiro y sustrato antes de plantar."/>
          <field name="x_tipo_suelo_otro"/>
          <field name="x_exposicion_sol" required="1" help="Decide qué especies sobreviven en esta zona."/>
          <field name="x_riego_existente" required="1"/>
          <field name="x_riego_existente_otro"/>
          <field name="x_acceso_maquinaria" help="Desmárcalo si no entra maquinaria: el trabajo se hace a mano y toma más tiempo."/>
          <field name="x_foto_inicial" widget="image" required="1" help="Foto general del área antes de empezar."/>
          <field name="x_descripcion_zona" placeholder="Ej. Patio trasero con escombro de obra y pasto seco en dos terceras partes"/>
        </group>
      """

_JARD_BODY_EJECUCION = """
        <group>
          <field name="x_foto_ejecucion" widget="image" required="1" help="Foto del trabajo en curso."/>
        </group>
        <field name="x_elementos_instalados" help="Agrega cada elemento que instalaste, con su cantidad y su foto.">
          <list>
            <field name="x_elemento"/>
            <field name="x_especie"/>
            <field name="x_cantidad"/>
            <field name="x_unidad"/>
          </list>
          <form>
            <group>
              <field name="x_elemento" required="1"/>
              <field name="x_elemento_otro"/>
              <field name="x_especie" placeholder="Ej. Buganvilia, pasto San Agustín, gravilla blanca"/>
              <field name="x_cantidad" required="1" placeholder="0"/>
              <field name="x_unidad" required="1"/>
              <field name="x_foto_evidencia" widget="image" required="1"/>
              <field name="x_observacion" placeholder="Ej. Se plantó a 40 cm de la barda por la raíz"/>
            </group>
          </form>
        </field>
      """

_JARD_BODY_CIERRE = """
        <group>
          <field name="x_indicaciones_cliente" required="1" placeholder="Ej. Regar cada tercer día los primeros 15 días; no pisar el pasto en rollo una semana."/>
          <field name="x_garantia_explicada" help="Que el cliente sepa qué cubre la garantía de prendimiento y qué no."/>
          <field name="x_area_limpia"/>
          <field name="x_foto_final" widget="image" required="1" help="Foto del resultado, desde el mismo ángulo que la inicial si se puede."/>""" + _SEG_BODY_JARDIN + """
          <field name="x_comments"/>
        </group>
      """

JARDIN_ARCH = _arch([
    ("Levantamiento", _JARD_BODY_LEVANTAMIENTO),
    ("Ejecución del diseño", _JARD_BODY_EJECUCION),
    ("Cierre", _JARD_BODY_CIERRE),
])

TERMITAS_ARCH = _arch([
    ("Inspección inicial", _TERM_BODY_INSPECCION),
    ("Ejecución del tratamiento", _TERM_BODY_EJECUCION),
    ("Cierre", _TERM_BODY_CIERRE),
])

CHINCHES_ARCH = _arch([
    ("Inspección inicial", _CHIN_BODY_INSPECCION),
    ("Preparación del cliente", _CHIN_BODY_PREPARACION),
    ("Ejecución del tratamiento", _CHIN_BODY_EJECUCION),
    ("Cierre", _CHIN_BODY_CIERRE),
])

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


def _ensure_tag(env, model_name, label, records, prune=False):
    """Crea el modelo-etiqueta y **converge** su catálogo al de `records`.

    Antes solo poblaba el catálogo cuando estaba vacío, así que agregar una opción
    en este archivo no llegaba nunca a una BD donde el modelo ya existía (QA/prod).
    Ahora siempre sincroniza: ver `_sync_tag_records`.
    """
    Model = env['ir.model'].sudo()
    rec = Model.search([('model', '=', model_name)], limit=1)
    if not rec:
        rec = Model.create({
            'name': label, 'model': model_name, 'state': 'manual',
            'field_id': [(0, 0, {'name': 'x_name', 'field_description': 'Nombre',
                                 'ttype': 'char', 'required': True})]})
        _acls(env, model_name, rec.id)
    env.cr.flush()
    _sync_tag_records(env, model_name, records, prune=prune)
    return rec


def _sync_tag_records(env, model_name, records, prune=False):
    """Converge el catálogo de un modelo-etiqueta al orden/contenido de `records`.

    - **Agrega** las etiquetas que falten (por `x_name` exacto).
    - **No toca** las que ya existen: su id vive en los m2m ya capturados.
    - `prune=True` **borra** las que no estén en la lista canónica. Solo para
      catálogos cuyo contenido manda el código (p. ej. la taxonomía de plagas al
      reestructurarla). Borrar una etiqueta solo quita las filas de relación de
      los m2m que la usaban; no rompe la línea que la referenciaba.
    """
    Tag = env[model_name].sudo()
    existing = {t.x_name: t for t in Tag.search([])}
    for value in records:
        if value not in existing:
            Tag.create({'x_name': value})
    if prune:
        stale = Tag.browse([t.id for name, t in existing.items()
                            if name not in records])
        if stale:
            _logger.info("Catálogo %s: se retiran %s etiqueta(s) fuera del canon: %s",
                         model_name, len(stale), stale.mapped('x_name'))
            stale.unlink()


def _sync_selection(env, model_name, field_name, options, prune=False):
    """Converge las opciones de un campo `selection` YA existente.

    `_ensure_field` no toca campos existentes y `_sel` solo corre al crearlos, así
    que sin esto agregar una opción a un catálogo de este archivo no llegaba nunca
    a una BD donde el campo ya existía. Agrega las que falten y reordena todas al
    orden canónico; `prune=True` borra las que sobren.

    Ojo: el `value` almacenado ES la cadena, así que **renombrar** una opción
    huérfana los registros que la tenían — se agrega la nueva y (con `prune`) se
    va la vieja, no hay mapeo posible. Cambiar redacciones es una migración de
    datos aparte, no un cambio de catálogo.
    """
    field = env['ir.model.fields'].sudo().search(
        [('model', '=', model_name), ('name', '=', field_name)], limit=1)
    if not field or field.ttype != 'selection':
        return
    Sel = env['ir.model.fields.selection'].sudo()
    existing = {s.value: s for s in Sel.search([('field_id', '=', field.id)])}
    for index, value in enumerate(options):
        sequence = index * 10
        current = existing.get(value)
        if current:
            if current.sequence != sequence:
                current.write({'sequence': sequence})
        else:
            Sel.create({'field_id': field.id, 'value': value, 'name': value,
                        'sequence': sequence})
    if prune:
        stale = Sel.browse([s.id for value, s in existing.items()
                            if value not in options])
        if stale:
            _logger.info("Selección %s.%s: se retiran %s opción(es) fuera del canon: %s",
                         model_name, field_name, len(stale), stale.mapped('value'))
            stale.unlink()


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
# Juegos de campos por servicio
# ---------------------------------------------------------------------------
# Cada juego se siembra sobre la plantilla que se le pase: la individual y la del
# COMBO. Los nombres de campo `x_` son los MISMOS en ambas (viven en modelos
# distintos, así que no chocan) — de eso depende que las tablas indexadas por
# nombre de campo del controlador (condicionales, obligatoriedad) y las maquetas
# del reporte sirvan igual para la hoja suelta y para la mitad del combo.
#
# Lo ÚNICO que cambia entre plantillas es lo que no puede repetirse en la BD:
#   - el modelo de línea de cada subficha (su m2o apunta a UNA hoja concreta);
#   - los nombres de las tablas de relación m2m.
# Ambas tablas se pasan con nombre EXPLÍCITO y corto: el autogenerado se pasa del
# límite de identificador de Postgres y el m2m desaparece sin error (bug ya vivido
# con `x_plaga_ids`).
def _seed_fumigacion_fields(env, tmpl, line_model, line_label, line_rel, ws_rel):
    ws, wid = tmpl.model_id.model, tmpl.model_id.id

    _ensure_tag(env, FACTOR_MODEL, "Factor de riesgo (Visar)", FACTORES)
    # `prune=True`: la taxonomía de plagas pasó de lista plana a 2 niveles, así que
    # las viejas categorías-especie (Termitas / Polilla / Chinches / Otros) tienen
    # que SALIR del nivel 1 — ahora viven bajo "Otras plagas".
    _ensure_tag(env, PLAGA_MODEL, "Tipo de plaga (Visar)", PLAGAS, prune=True)
    # También `prune=True` en las especies: el código es el canon de esta taxonomía
    # igual que en el nivel 1. Sin esto, cambiar una lista solo AGREGA y la vieja
    # convive con la nueva — el técnico vería "Cucarachas" junto a "Cucaracha
    # alemana (chica)" y "Cucaracha americana (grande)". Ojo: borra también lo que
    # alguien haya agregado a mano por el backend; si mañana negocio quiere
    # mantener el catálogo desde ahí, esto tiene que dejar de podar.
    for _f, tag_model, categoria, label, especies in PLAGA_ESPECIES:
        _ensure_tag(env, tag_model, "Plaga — %s (Visar)" % categoria, especies,
                    prune=True)

    line = _ensure_model(env, line_model, line_label, [
        (0, 0, {'name': 'x_worksheet_id', 'field_description': 'Worksheet',
                'ttype': 'many2one', 'relation': ws, 'required': True,
                'on_delete': 'cascade'}),
        (0, 0, {'name': 'x_sequence', 'field_description': 'Secuencia', 'ttype': 'integer'}),
    ])
    _acls(env, line_model, line.id)
    env.cr.flush()
    lid = line.id

    _ensure_field(env, line_model, lid, 'x_area', 'selection', 'Área', selection=AREAS)
    _ensure_field(env, line_model, lid, 'x_area_otro', 'char', OTRO)
    _ensure_field(env, line_model, lid, 'x_plaga_ids', 'many2many', 'Tipo de plaga',
                  relation=PLAGA_MODEL, relation_table='%s_plaga_rel' % line_rel,
                  column1='area_id', column2='plaga_id')
    _ensure_field(env, line_model, lid, 'x_plaga_ids_otro', 'char',
                  "Especifica qué plaga")
    # Especies por categoría. Tabla de relación EXPLÍCITA y corta: el nombre
    # autogenerado se pasa del límite de identificador de Postgres y el m2m
    # desaparece sin error (bug ya vivido con `x_plaga_ids`).
    for index, (fname, tag_model, _categoria, label, _especies) in enumerate(
            PLAGA_ESPECIES):
        _ensure_field(env, line_model, lid, fname, 'many2many', label,
                      relation=tag_model,
                      relation_table='%s_plesp%d_rel' % (line_rel, index),
                      column1='area_id', column2='especie_id')
    _ensure_field(env, line_model, lid, 'x_infestacion_activa', 'boolean',
                  '¿Se detectó presencia activa de plaga?')
    # Marca de "área fija" (Cocina / Baño / Área de basura): no se puede eliminar
    # y se siembra al crear la hoja. NO va en el arch a propósito — es contabilidad
    # interna, no captura del técnico.
    _ensure_field(env, line_model, lid, 'x_fija', 'boolean', 'Área obligatoria')
    _ensure_field(env, line_model, lid, 'x_cliente_no_permitio', 'boolean',
                  'Cliente NO permitió que se fumigara en esta área')
    # El plaguicida salió de ser una LISTA FIJA de principios activos y pasó a ser un
    # producto real de la ubicación del técnico (23-sep-2026). `x_plaguicida_nombre` y
    # su companion se quedan en el modelo —fuera del arch— porque 29 líneas de
    # producción ya tienen respuesta y esa historia no se tira.
    _ensure_field(env, line_model, lid, 'x_plaguicida_nombre', 'selection',
                  'Plaguicida — nombre (histórico)', selection=PLAGUICIDAS)
    _ensure_field(env, line_model, lid, 'x_plaguicida_nombre_otro', 'char', OTRO)
    _ensure_field(env, line_model, lid, 'x_plaguicida_id', 'many2one',
                  'Plaguicida aplicado', relation='product.product')
    # Escotilla SIN condicional (a diferencia de los demás `_otro`): siempre visible.
    # Si el técnico aplicó algo que no trae cargado, se anota aquí en vez de dejar la
    # línea en blanco; queda como hueco visible para que administración lo cuadre.
    _ensure_field(env, line_model, lid, 'x_plaguicida_otro', 'char',
                  'Otro plaguicida (no cargado en tu ubicación)')
    # Sin unidad en la etiqueta: la unidad la pone el PRODUCTO (ml, g…), y el
    # desplegable ya la enseña al lado de la existencia.
    _ensure_field(env, line_model, lid, 'x_plaguicida_dosis', 'float',
                  'Cantidad aplicada')
    _ensure_field(env, line_model, lid, 'x_trampa_monitoreo', 'boolean',
                  'Trampa de monitoreo colocada')
    _ensure_field(env, line_model, lid, 'x_foto_evidencia', 'binary', 'Fotos de evidencia')
    _ensure_field(env, line_model, lid, 'x_accion_correctiva', 'selection',
                  'Tipo de acción correctiva requerida', selection=ACCION)
    _ensure_field(env, line_model, lid, 'x_accion_correctiva_otro', 'char', OTRO)

    _ensure_field(env, ws, wid, 'x_recorrido_completo', 'boolean',
                  'Recorrido completo por el inmueble realizado')
    _ensure_field(env, ws, wid, 'x_nivel_infestacion', 'selection',
                  'Nivel de infestación', selection=NIVEL)
    _ensure_field(env, ws, wid, 'x_factores_riesgo', 'many2many',
                  'Factores de riesgo detectados', relation=FACTOR_MODEL,
                  relation_table='%s_factor_rel' % ws_rel, column1='worksheet_id',
                  column2='factor_id')
    _ensure_field(env, ws, wid, 'x_factores_riesgo_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_foto_inicial', 'binary', 'Fotos estado inicial zona afectada')
    _ensure_field(env, ws, wid, 'x_descripcion_zona', 'text', 'Descripción de la zona afectada')
    _ensure_field(env, ws, wid, 'x_foto_ejecucion', 'binary', 'Fotos generales durante la ejecución')
    _ensure_field(env, ws, wid, 'x_areas_tratadas', 'one2many', 'Áreas tratadas',
                  relation=line_model, relation_field='x_worksheet_id')
    env.cr.flush()

    # Campos-foto en plural (ahora son galerías multi-foto).
    _relabel_field(env, ws, 'x_foto_inicial', 'Fotos estado inicial zona afectada')
    _relabel_field(env, ws, 'x_foto_ejecucion', 'Fotos generales durante la ejecución')
    _relabel_field(env, line_model, 'x_foto_evidencia', 'Fotos de evidencia')
    # Reetiquetados de la taxonomía de 2 niveles (campos que ya existen en QA/prod).
    _relabel_field(env, line_model, 'x_plaguicida_nombre', 'Plaguicida — nombre (histórico)')
    _relabel_field(env, line_model, 'x_plaguicida_dosis', 'Cantidad aplicada')
    _relabel_field(env, line_model, 'x_plaga_ids', 'Tipo de plaga')
    _relabel_field(env, line_model, 'x_plaga_ids_otro', "Especifica qué plaga")
    _relabel_field(env, line_model, 'x_infestacion_activa',
                   '¿Se detectó presencia activa de plaga?')
    for fname, _tm, _categoria, label, _especies in PLAGA_ESPECIES:
        _relabel_field(env, line_model, fname, label)
    _sync_selection(env, line_model, 'x_area', AREAS)
    _sync_selection(env, line_model, 'x_plaguicida_nombre', PLAGUICIDAS)
    _sync_selection(env, line_model, 'x_accion_correctiva', ACCION)
    _sync_selection(env, ws, 'x_nivel_infestacion', NIVEL)
    _relabel_comments(env, ws, 'Observaciones finales del técnico')


def _seed_jardineria_fields(env, tmpl, line_model, line_label):
    ws, wid = tmpl.model_id.model, tmpl.model_id.id

    line = _ensure_model(env, line_model, line_label, [
        (0, 0, {'name': 'x_worksheet_id', 'field_description': 'Worksheet',
                'ttype': 'many2one', 'relation': ws, 'required': True,
                'on_delete': 'cascade'}),
        (0, 0, {'name': 'x_sequence', 'field_description': 'Secuencia', 'ttype': 'integer'}),
    ])
    _acls(env, line_model, line.id)
    env.cr.flush()
    lid = line.id

    _ensure_field(env, line_model, lid, 'x_tipo_servicio', 'selection', 'Tipo de servicio',
                  selection=TIPO_SERVICIO)
    _ensure_field(env, line_model, lid, 'x_tipo_servicio_otro', 'char', OTRO)
    _ensure_field(env, line_model, lid, 'x_completado', 'boolean', '¿Se completó?')
    _ensure_field(env, line_model, lid, 'x_observaciones', 'text', 'Observaciones')

    _ensure_field(env, ws, wid, 'x_foto_inicial_jardin', 'binary', 'Fotos estado inicial del jardín')
    _ensure_field(env, ws, wid, 'x_indicaciones_cliente', 'text', 'Indicaciones especiales del cliente')
    _ensure_field(env, ws, wid, 'x_solicitudes_adicionales', 'text', 'Solicitudes adicionales del cliente')
    _ensure_field(env, ws, wid, 'x_labores', 'one2many', 'Labor de jardinería',
                  relation=line_model, relation_field='x_worksheet_id')
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
    _sync_selection(env, line_model, 'x_tipo_servicio', TIPO_SERVICIO)
    _sync_selection(env, ws, 'x_estado_equipo', ESTADO_EQUIPO)
    _relabel_comments(env, ws, 'Observaciones finales del técnico')


def _seed_seguimiento_fields(env, ws, wid):
    """Los tres campos del acuerdo de seguimiento. Iguales en los dos tratamientos:
    `models/seguimiento.py` los lee por nombre, sin saber de qué hoja vienen."""
    _ensure_field(env, ws, wid, 'x_requiere_seguimiento', 'boolean',
                  '¿Requiere visita de seguimiento?')
    _ensure_field(env, ws, wid, 'x_fecha_seguimiento', 'date',
                  'Fecha acordada con el cliente')
    _ensure_field(env, ws, wid, 'x_franja_seguimiento', 'selection',
                  'Horario acordado', selection=FRANJA)


def _seed_jardin_fields(env, tmpl):
    """Campos de la hoja de Diseño de jardín.

    Sin catálogo de plaguicidas ni de plagas: aquí no se aplica nada, se INSTALA.
    Lo que se instala se anota como texto (especie, cantidad, unidad) y no como
    producto de inventario a propósito — el catálogo de Visar no tiene dadas de
    alta las plantas, y obligar a darlas de alta para poder cerrar una hoja
    frenaría la obra. Lo que SÍ está en inventario (sustrato, tubería de riego)
    se descuenta por la tarjeta "Consumo de material" de la visita, que funciona
    con cualquier hoja.
    """
    ws, wid = tmpl.model_id.model, tmpl.model_id.id

    line = _ensure_model(env, JARDIN_LINE, "Elemento instalado (Diseño de jardín)", [
        (0, 0, {'name': 'x_worksheet_id', 'field_description': 'Worksheet',
                'ttype': 'many2one', 'relation': ws, 'required': True,
                'on_delete': 'cascade'}),
        (0, 0, {'name': 'x_sequence', 'field_description': 'Secuencia',
                'ttype': 'integer'}),
    ])
    _acls(env, JARDIN_LINE, line.id)
    env.cr.flush()
    lid = line.id
    _ensure_field(env, JARDIN_LINE, lid, 'x_elemento', 'selection', 'Elemento',
                  selection=ELEMENTOS_JARDIN)
    _ensure_field(env, JARDIN_LINE, lid, 'x_elemento_otro', 'char', OTRO)
    _ensure_field(env, JARDIN_LINE, lid, 'x_especie', 'char', 'Especie o material')
    _ensure_field(env, JARDIN_LINE, lid, 'x_cantidad', 'float', 'Cantidad')
    _ensure_field(env, JARDIN_LINE, lid, 'x_unidad', 'selection', 'Unidad',
                  selection=UNIDAD_ELEMENTO)
    _ensure_field(env, JARDIN_LINE, lid, 'x_foto_evidencia', 'binary',
                  'Fotos del elemento instalado')
    _ensure_field(env, JARDIN_LINE, lid, 'x_observacion', 'char', 'Observación')

    _ensure_field(env, ws, wid, 'x_superficie_m2', 'float',
                  'Superficie a intervenir (m²)')
    _ensure_field(env, ws, wid, 'x_tipo_suelo', 'selection', 'Tipo de suelo',
                  selection=TIPO_SUELO)
    _ensure_field(env, ws, wid, 'x_tipo_suelo_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_exposicion_sol', 'selection',
                  'Exposición al sol', selection=EXPOSICION_SOL)
    _ensure_field(env, ws, wid, 'x_riego_existente', 'selection',
                  'Riego existente', selection=RIEGO_EXISTENTE)
    _ensure_field(env, ws, wid, 'x_riego_existente_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_acceso_maquinaria', 'boolean',
                  '¿Hay acceso para maquinaria?')
    _ensure_field(env, ws, wid, 'x_foto_inicial', 'binary',
                  'Fotos del área antes de empezar')
    _ensure_field(env, ws, wid, 'x_descripcion_zona', 'text',
                  'Descripción del área')
    _ensure_field(env, ws, wid, 'x_foto_ejecucion', 'binary',
                  'Fotos durante la ejecución')
    _ensure_field(env, ws, wid, 'x_elementos_instalados', 'one2many',
                  'Elementos instalados', relation=JARDIN_LINE,
                  relation_field='x_worksheet_id')
    _ensure_field(env, ws, wid, 'x_indicaciones_cliente', 'text',
                  'Cuidados que se le explicaron al cliente')
    _ensure_field(env, ws, wid, 'x_garantia_explicada', 'boolean',
                  '¿Se explicó la garantía de prendimiento?')
    _ensure_field(env, ws, wid, 'x_area_limpia', 'boolean',
                  '¿Se entregó el área limpia?')
    _ensure_field(env, ws, wid, 'x_foto_final', 'binary', 'Fotos del resultado')
    _seed_seguimiento_fields(env, ws, wid)
    _relabel_comments(env, ws, "Observaciones finales del técnico")


def _seed_termitas_fields(env, tmpl):
    ws, wid = tmpl.model_id.model, tmpl.model_id.id
    _ensure_tag(env, ESTRUCTURA_MODEL, "Estructura afectada — termitas (Visar)",
                ESTRUCTURAS, prune=True)

    line = _ensure_model(env, TERM_LINE, "Punto tratado (Termitas)", [
        (0, 0, {'name': 'x_worksheet_id', 'field_description': 'Worksheet',
                'ttype': 'many2one', 'relation': ws, 'required': True,
                'on_delete': 'cascade'}),
        (0, 0, {'name': 'x_sequence', 'field_description': 'Secuencia',
                'ttype': 'integer'}),
    ])
    _acls(env, TERM_LINE, line.id)
    env.cr.flush()
    lid = line.id
    _ensure_field(env, TERM_LINE, lid, 'x_ubicacion', 'char', 'Ubicación del punto')
    _ensure_field(env, TERM_LINE, lid, 'x_perforaciones', 'integer',
                  'Perforaciones realizadas')
    _ensure_field(env, TERM_LINE, lid, 'x_foto_evidencia', 'binary',
                  'Fotos de evidencia')
    _ensure_field(env, TERM_LINE, lid, 'x_observacion', 'char', 'Observación')

    _ensure_field(env, ws, wid, 'x_tipo_termita', 'selection', 'Tipo de termita',
                  selection=TIPO_TERMITA)
    # Tabla de relación EXPLÍCITA y corta: la autogenerada se pasa del límite de
    # identificador de Postgres y el m2m desaparece sin error (bug ya vivido).
    _ensure_field(env, ws, wid, 'x_estructuras_afectadas', 'many2many',
                  'Estructuras afectadas', relation=ESTRUCTURA_MODEL,
                  relation_table='x_term_estruct_rel', column1='ws_id',
                  column2='estructura_id')
    _ensure_field(env, ws, wid, 'x_estructuras_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_nivel_dano', 'selection', 'Nivel de daño',
                  selection=NIVEL_DANO)
    _ensure_field(env, ws, wid, 'x_humedad_detectada', 'boolean',
                  '¿Se detectó humedad o filtración?')
    _ensure_field(env, ws, wid, 'x_foto_inicial', 'binary',
                  'Fotos del daño antes de tratar')
    _ensure_field(env, ws, wid, 'x_descripcion_zona', 'text',
                  'Descripción de la zona afectada')
    _ensure_field(env, ws, wid, 'x_metodo_aplicacion', 'selection',
                  'Método aplicado', selection=METODO_TERMITA)
    _ensure_field(env, ws, wid, 'x_metodo_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_foto_ejecucion', 'binary',
                  'Fotos durante la ejecución')
    _ensure_field(env, ws, wid, 'x_puntos_tratados', 'one2many',
                  'Puntos tratados', relation=TERM_LINE,
                  relation_field='x_worksheet_id')
    _ensure_field(env, ws, wid, 'x_indicaciones_cliente', 'text',
                  'Indicaciones que se le dieron al cliente')
    _ensure_field(env, ws, wid, 'x_foto_final', 'binary', 'Fotos finales')
    _seed_seguimiento_fields(env, ws, wid)
    _relabel_comments(env, ws, "Observaciones finales del técnico")


def _seed_chinches_fields(env, tmpl):
    ws, wid = tmpl.model_id.model, tmpl.model_id.id
    _ensure_tag(env, EVIDENCIA_MODEL, "Evidencia — chinches (Visar)",
                EVIDENCIAS_CHINCHE, prune=True)
    _ensure_tag(env, HABITACION_MODEL, "Habitación — chinches (Visar)",
                HABITACIONES, prune=True)

    line = _ensure_model(env, CHIN_LINE, "Zona tratada (Chinches)", [
        (0, 0, {'name': 'x_worksheet_id', 'field_description': 'Worksheet',
                'ttype': 'many2one', 'relation': ws, 'required': True,
                'on_delete': 'cascade'}),
        (0, 0, {'name': 'x_sequence', 'field_description': 'Secuencia',
                'ttype': 'integer'}),
    ])
    _acls(env, CHIN_LINE, line.id)
    env.cr.flush()
    lid = line.id
    _ensure_field(env, CHIN_LINE, lid, 'x_habitacion', 'selection', 'Habitación',
                  selection=HABITACIONES)
    _ensure_field(env, CHIN_LINE, lid, 'x_habitacion_otro', 'char', OTRO)
    _ensure_field(env, CHIN_LINE, lid, 'x_mueble', 'char', 'Mueble o zona tratada')
    _ensure_field(env, CHIN_LINE, lid, 'x_foto_evidencia', 'binary',
                  'Fotos de evidencia')
    _ensure_field(env, CHIN_LINE, lid, 'x_observacion', 'char', 'Observación')

    _ensure_field(env, ws, wid, 'x_nivel_infestacion', 'selection',
                  'Nivel de infestación', selection=NIVEL)
    _ensure_field(env, ws, wid, 'x_habitaciones_afectadas', 'many2many',
                  'Habitaciones afectadas', relation=HABITACION_MODEL,
                  relation_table='x_chin_hab_rel', column1='ws_id',
                  column2='habitacion_id')
    _ensure_field(env, ws, wid, 'x_habitaciones_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_evidencia_encontrada', 'many2many',
                  'Evidencia encontrada', relation=EVIDENCIA_MODEL,
                  relation_table='x_chin_evid_rel', column1='ws_id',
                  column2='evidencia_id')
    _ensure_field(env, ws, wid, 'x_evidencia_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_foto_inicial', 'binary',
                  'Fotos de la evidencia encontrada')
    _ensure_field(env, ws, wid, 'x_descripcion_zona', 'text',
                  'Descripción de lo encontrado')
    _ensure_field(env, ws, wid, 'x_ropa_lavada', 'boolean',
                  'Ropa y blancos lavados')
    _ensure_field(env, ws, wid, 'x_colchones_despejados', 'boolean',
                  'Colchones y bases despejados')
    _ensure_field(env, ws, wid, 'x_desorden_retirado', 'boolean',
                  'Objetos retirados de camas y muebles')
    _ensure_field(env, ws, wid, 'x_prep_observacion', 'char',
                  'Observación sobre la preparación')
    _ensure_field(env, ws, wid, 'x_metodo_aplicacion', 'selection',
                  'Método aplicado', selection=METODO_CHINCHE)
    _ensure_field(env, ws, wid, 'x_metodo_otro', 'char', OTRO)
    _ensure_field(env, ws, wid, 'x_foto_ejecucion', 'binary',
                  'Fotos durante la ejecución')
    _ensure_field(env, ws, wid, 'x_zonas_tratadas', 'one2many', 'Zonas tratadas',
                  relation=CHIN_LINE, relation_field='x_worksheet_id')
    _ensure_field(env, ws, wid, 'x_indicaciones_cliente', 'text',
                  'Indicaciones que se le dieron al cliente')
    _ensure_field(env, ws, wid, 'x_foto_final', 'binary', 'Fotos finales')
    _seed_seguimiento_fields(env, ws, wid)
    _relabel_comments(env, ws, "Observaciones finales del técnico")


def _finish_template(env, tmpl, arch, name):
    """Escribe el arch canónico y regenera el reporte QWeb de la plantilla."""
    _write_arch(env, tmpl.model_id.model, arch)
    tmpl._generate_qweb_report_template()
    _logger.info("Seeded worksheet template %s (%s)", name, tmpl.model_id.model)
    return tmpl


# ---------------------------------------------------------------------------
# Plantillas
# ---------------------------------------------------------------------------
def _seed_fumigacion(env):
    tmpl = _get_template(env, FUMIGACION_NAME)
    _seed_fumigacion_fields(env, tmpl, FUM_LINE, "Área tratada (Fumigación v2)",
                            line_rel='x_area', ws_rel='x_ws')
    return _finish_template(env, tmpl, FUMIGACION_ARCH, FUMIGACION_NAME)


def _seed_jardineria(env):
    tmpl = _get_template(env, JARDINERIA_NAME)
    _seed_jardineria_fields(env, tmpl, JAR_LINE, "Labor de jardinería")
    return _finish_template(env, tmpl, JARDINERIA_ARCH, JARDINERIA_NAME)


# ---------------------------------------------------------------------------
# Fumigación + Mantenimiento de áreas verdes (App v2) — el COMBO
# ---------------------------------------------------------------------------
def _seed_combo(env):
    """Plantilla de la visita combinada: los dos juegos de campos en una hoja.

    Misma semilla que las individuales, con los mismos nombres de campo; solo
    cambian los modelos de línea y las tablas de relación (no pueden repetirse en
    la BD). Los catálogos de etiquetas (plagas, factores) SE COMPARTEN: son los
    mismos registros que ya usa la hoja de fumigación suelta.
    """
    tmpl = _get_template(env, COMBO_NAME)
    _seed_fumigacion_fields(env, tmpl, FUM_LINE_COMBO, "Área tratada (Combo v2)",
                            line_rel='x_cmb', ws_rel='x_wsc')
    _seed_jardineria_fields(env, tmpl, JAR_LINE_COMBO, "Labor de jardinería (Combo)")
    return _finish_template(env, tmpl, COMBO_ARCH, COMBO_NAME)


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

    _sync_selection(env, ws, 'x_tipo_inmueble', TIPO_INMUEBLE)
    _sync_selection(env, ws, 'x_complejidad', COMPLEJIDAD)
    _write_arch(env, ws, VISITA_ARCH)
    tmpl._generate_qweb_report_template()
    _logger.info("Seeded worksheet template %s (%s)", VISITA_NAME, ws)
    return tmpl


def _seed_termitas(env):
    tmpl = _get_template(env, TERMITAS_NAME)
    _seed_termitas_fields(env, tmpl)
    return _finish_template(env, tmpl, TERMITAS_ARCH, TERMITAS_NAME)


def _seed_jardin(env):
    tmpl = _get_template(env, JARDIN_NAME)
    _seed_jardin_fields(env, tmpl)
    return _finish_template(env, tmpl, JARDIN_ARCH, JARDIN_NAME)


def _seed_chinches(env):
    tmpl = _get_template(env, CHINCHES_NAME)
    _seed_chinches_fields(env, tmpl)
    return _finish_template(env, tmpl, CHINCHES_ARCH, CHINCHES_NAME)


def seed_worksheet_templates(env):
    """Crea/actualiza las plantillas de la App de Campo. Idempotente."""
    _seed_fumigacion(env)
    _seed_jardineria(env)
    _seed_visita(env)
    combo = _seed_combo(env)
    wire_combined_project(env, combo)
    wire_quoted_service_projects(
        env, _seed_termitas(env), _seed_chinches(env), _seed_jardin(env))


# ======================================================================
# Plantilla del proyecto de servicios combinados
# ======================================================================
# Parámetro donde `visar_fsm` guarda el id del proyecto anfitrión del combo.
COMBINED_PROJECT_PARAM = 'visar.fsm_project_combinados_id'
# Plantilla nativa genérica ("Default Worksheet"): la que Odoo le pone SOLO a
# cualquier proyecto FSM al nacer.
NATIVE_TEMPLATE_XMLID = 'industry_fsm_report.fsm_worksheet_template'


def wire_combined_project(env, combo_template=None):
    """Apunta el proyecto de servicios combinados a la plantilla fusionada.

    Excepción DELIBERADA a "la asignación de plantilla no se automatiza" (ver
    `.context/40-decisions.md`): ese criterio protege una elección humana, y aquí
    no hay ninguna — el proyecto lo crea el código y no existe para otra cosa.
    Dejarlo sin plantilla propia no es neutral: el técnico abriría el combo con una
    hoja de un solo campo y el PDF caería al render genérico.

    Por eso NO basta con "solo si está vacío": `_compute_worksheet_template_id`
    (industry_fsm_report) le pone la plantilla nativa genérica a todo proyecto FSM
    al crearse, así que ese guardia nunca dispararía. Se reescribe si está vacío o
    si sigue en la nativa; una elección distinta hecha a mano se respeta.
    """
    combo_template = combo_template or env['worksheet.template'].sudo().search(
        [('name', '=', COMBO_NAME)], limit=1)
    if not combo_template:
        return env['project.project'].sudo().browse()

    raw_id = env['ir.config_parameter'].sudo().get_param(COMBINED_PROJECT_PARAM)
    project = (env['project.project'].sudo().browse(int(raw_id)).exists()
               if raw_id and raw_id.isdigit() else None)
    if not project:
        _logger.info("Sin proyecto de servicios combinados (%s): plantilla del "
                     "combo sin asignar", COMBINED_PROJECT_PARAM)
        return env['project.project'].sudo().browse()

    native = env.ref(NATIVE_TEMPLATE_XMLID, raise_if_not_found=False)
    current = project.worksheet_template_id
    if current and current != native and current != combo_template:
        _logger.info("Proyecto %s conserva su plantilla elegida a mano (%s)",
                     project.name, current.name)
        return project
    if current != combo_template:
        project.write({'worksheet_template_id': combo_template.id})
        _logger.info("Proyecto %s -> plantilla %s", project.name, COMBO_NAME)
    return project


# ======================================================================
# Proyectos de los tratamientos que se cotizan a mano
# ======================================================================
# Un proyecto por tratamiento (decisión de Visar, 22-sep-2026): son plagas
# distintas, con hoja distinta, y el tablero se lee mejor separado. Sin proyecto,
# una cotización pagada crea la cita pero NUNCA la visita del técnico: el producto
# no sabe dónde nacer.
# Servicios que se cotizan a mano y nacen como su propia visita al pagarse. Se
# llamaba TRATAMIENTOS hasta el 24-sep-2026, cuando entró el diseño de jardín: no
# es un tratamiento, pero recorre exactamente el mismo circuito.
SERVICIOS_COTIZADOS = [
    # (nombre del proyecto, nombre EXACTO del producto, nombre de la plantilla)
    ("Tratamiento antitermita", "Tratamiento antitermita", TERMITAS_NAME),
    ("Tratamiento antichinches", "Tratamiento antichinches de cama", CHINCHES_NAME),
    ("Diseño de jardín", "Diseño e instalación de áreas verdes", JARDIN_NAME),
]


def wire_quoted_service_projects(env, *_plantillas):
    """Crea el proyecto FSM de cada tratamiento, le pone su hoja y engancha el
    producto. Idempotente y respetuoso con lo elegido a mano.

    Misma excepción deliberada que el combo (`wire_combined_project`): el criterio
    de "la asignación de plantilla no se automatiza" protege una elección humana, y
    aquí el proyecto lo crea el código y no existe para otra cosa. Una plantilla
    distinta puesta a mano se respeta; la nativa genérica no cuenta como elección.
    """
    Project = env['project.project'].sudo()
    Template = env['product.template'].sudo()
    Worksheet = env['worksheet.template'].sudo()
    native = env.ref(NATIVE_TEMPLATE_XMLID, raise_if_not_found=False)
    creados = Project.browse()
    for nombre_proyecto, nombre_producto, nombre_hoja in SERVICIOS_COTIZADOS:
        hoja = Worksheet.search([('name', '=', nombre_hoja)], limit=1)
        producto = Template.with_context(active_test=False).search(
            [('name', '=', nombre_producto)], limit=1)
        if not producto:
            _logger.info("Sin producto '%s': no se crea su proyecto", nombre_producto)
            continue
        project = Project.with_context(active_test=False).search(
            [('name', '=', nombre_proyecto)], limit=1)
        if not project:
            project = Project.create({
                'name': nombre_proyecto, 'is_fsm': True, 'allow_billable': True,
                'company_id': env.company.id})
            _logger.info("Proyecto FSM creado: %s", nombre_proyecto)
        creados |= project
        actual = project.worksheet_template_id
        if hoja and (not actual or actual == native or actual == hoja):
            if actual != hoja:
                project.write({'worksheet_template_id': hoja.id})
                _logger.info("Proyecto %s -> plantilla %s", nombre_proyecto, nombre_hoja)
        # El producto solo se engancha si NADIE lo configuró: cambiar a qué
        # proyecto va un servicio ya vendido movería las visitas de sitio.
        #
        # La segunda condición cubre un estado A MEDIAS que trae producción (lo tenía
        # "Diseño e instalación de áreas verdes" el 24-sep-2026): `service_tracking`
        # puesto en "crear tarea en proyecto" pero SIN proyecto. Eso no es una
        # elección humana que respetar, es una configuración rota —al pagarse no
        # puede crear la visita en ningún lado—, así que se completa.
        a_medias = (producto.service_tracking == 'task_global_project'
                    and not producto.project_id)
        if (producto.service_tracking == 'no' and not producto.project_id) or a_medias:
            producto.write({'service_tracking': 'task_global_project',
                            'project_id': project.id})
            _logger.info("Producto %s -> crea visita en %s", nombre_producto,
                         nombre_proyecto)
    return creados


# ======================================================================
# Etapa "Pendiente de firma" — RETIRADA del flujo (archivada)
# ======================================================================
# Existió entre En ejecución y Completado; se retiró porque el tramo
# hoja→firma es de segundos/minutos y no aporta a gestión. La firma sigue
# gated por `visar_worksheet_saved_at` (Req 6). El xmlid se conserva para
# reconciliar tareas viejas que aún la tengan.
SIGN_STAGE_XMLID = 'visar_stage_pending_signature'
SIGN_STAGE_NAME_ES = "Pendiente de firma"


def _fsm_projects(env):
    return env['project.project'].sudo().search([('is_fsm', '=', True)])


def archive_signature_stage(env):
    """Archiva **Pendiente de firma** y mueve tareas abiertas a En ejecución.

    Idempotente. No crea la etapa si no existe. El gate de firma (sello
    `visar_worksheet_saved_at`) no depende de esta etapa.
    """
    Stage = env['project.task.type'].sudo().with_context(active_test=False)
    stage = env.ref('visar_field_app.%s' % SIGN_STAGE_XMLID, raise_if_not_found=False)
    if not stage:
        projects = _fsm_projects(env)
        stage = Stage.with_context(lang='es_MX').search([
            ('name', '=', SIGN_STAGE_NAME_ES),
            ('project_ids', 'in', projects.ids),
        ], limit=1) if projects else Stage.browse()
    if not stage:
        _logger.info("Etapa '%s' no existe; nada que archivar", SIGN_STAGE_NAME_ES)
        return stage

    in_progress = env.ref(
        'industry_fsm.planning_project_stage_2', raise_if_not_found=False)
    if in_progress:
        tasks = env['project.task'].sudo().search([('stage_id', '=', stage.id)])
        if tasks:
            tasks.write({'stage_id': in_progress.id})
            _logger.info(
                "Movidas %s tarea(s) de '%s' → En ejecución",
                len(tasks), SIGN_STAGE_NAME_ES)

    if stage.active:
        stage.write({'active': False})
        _logger.info("Etapa '%s' archivada (id %s)", SIGN_STAGE_NAME_ES, stage.id)
    else:
        _logger.info("Etapa '%s' ya estaba archivada (id %s)", SIGN_STAGE_NAME_ES, stage.id)
    return stage


def seed_signature_stage(env):
    """Deprecated alias: ya no se siembra; se archiva (ver `archive_signature_stage`)."""
    return archive_signature_stage(env)


# ======================================================================
# Catálogo de venta en campo (upsell)
# ======================================================================
# Negocio ya tenía curada una categoría "Upsell" con lo que se ofrece como extra.
# El sembrador la usa como SEMILLA del flag `visar_upsell_ok` — una sola vez, no
# como criterio permanente: la categoría es contable, y si mañana quieren dejar de
# ofrecer un producto no deberían tener que moverle las cuentas.
UPSELL_CATEG_NAME = "Upsell"


def seed_upsell_catalog(env):
    """Enciende `visar_upsell_ok` en los productos de la categoría "Upsell".

    Idempotente y NO destructivo: solo enciende, nunca apaga. Si alguien quitó a
    mano un producto del catálogo de campo, re-ejecutar el sembrador no lo revive.
    """
    categ = env['product.category'].sudo().search(
        [('name', '=', UPSELL_CATEG_NAME)], limit=1)
    if not categ:
        _logger.info("Sin categoría '%s': catálogo de upsell sin sembrar",
                     UPSELL_CATEG_NAME)
        return env['product.template'].sudo().browse()

    products = env['product.template'].sudo().search([
        ('categ_id', 'child_of', categ.id),
        ('sale_ok', '=', True),
        ('recurring_invoice', '=', False),
        ('visar_upsell_ok', '=', False),
    ])
    if products:
        products.write({'visar_upsell_ok': True})
    _logger.info("Catálogo de upsell: %s producto(s) marcados vendibles en campo",
                 len(products))
    return products


def post_init_hook(env):
    seed_worksheet_templates(env)
    archive_signature_stage(env)
    seed_upsell_catalog(env)
