# -*- coding: utf-8 -*-
"""El vocabulario del cliente, editable sin desplegar.

`appointment_wizard_flow` publica en cada opción del cuestionario una lista de
`keywords`: las palabras con las que el CLIENTE pide esa opción, que casi nunca
son las de la etiqueta. Nadie escribe "Correctivo"; escribe que tiene alacranes.

Esas listas nacieron como constantes de Python, y sus propios comentarios ya
decían lo que se esperaba de ellas —*"es copy de negocio: lo edita un consultor
cuando el chat le enseñe una palabra que no está"*—. Pero para editarlas hacía
falta un desarrollador, un bump de versión, un `-u` y reiniciar odoo. El
8-sep-2026 tres de los cuatro fallos reportados desde producción fueron una
palabra que faltaba, y cada uno costó un despliegue.

Este modelo es la capa de encima:

    lo que el agente ve  =  las palabras del código  +  las de aquí

**Solo suma, nunca quita.** El código es el piso: es lo que afirman las pruebas
y lo que sobrevive a una base nueva. Si una palabra del código clasifica mal,
eso es un arreglo de código con su prueba; quitarla desde una pantalla dejaría
el repositorio verde mientras producción hace otra cosa.

**Se aplica en el siguiente paso, sin reiniciar nada.** `agent_booking_step` es
una llamada RPC viva, no una caché: en cuanto se guarda, el paso siguiente ya
lleva la palabra. La pregunta que el cliente tiene YA en pantalla conserva las
palabras con las que se pintó, porque el runtime guardó sus opciones al
pintarla; se nota a partir de la respuesta siguiente.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Etiquetas de los pasos PARA EL CONSULTOR, que no son las que ve el cliente:
# aquí hace falta saber de qué pregunta se habla, no cómo se le formula. Las
# claves salen de `_VISAR_VOCABULARIO_BASE`, así que una ranura nueva sin
# etiqueta se ve igual (con su clave) en vez de desaparecer del desplegable.
_VISAR_PASO_ETIQUETAS = {
    'services': "Servicio",
    'motivo': "Preventivo o correctivo",
    'plagas': "Plagas",
    'cobertura': "Interior o exterior",
    'valuation': "Valoración técnica",
    'extras': "Extras",
    'poliza': "Póliza",
}


class VisarAgentVocabulario(models.Model):
    """Palabras del cliente que un consultor añade a una opción del cuestionario."""

    _name = 'visar.agent.vocabulario'
    _description = "Vocabulario del cliente (agente de WhatsApp)"
    _order = 'paso, opcion'

    paso = fields.Selection(
        selection='_visar_selection_paso',
        string="Paso", required=True,
        help="La pregunta del cuestionario a la que pertenece la opción.")
    opcion = fields.Char(
        string="Opción", required=True,
        help="La clave de la opción dentro del paso. En 'Servicio' es el "
             "código del grupo (fumigacion, corte); en 'Extras' y 'Póliza', "
             "la salida se llama no_gracias.")
    palabras = fields.Text(
        string="Palabras del cliente", required=True,
        help="Una palabra o frase por línea, tal y como la escribe el cliente. "
             "Valen las raíces: 'cucarach' cubre cucaracha y cucarachas. Se "
             "comparan sin acentos y sin distinguir mayúsculas, y solo al "
             "principio de una palabra: 'rata' no coincide dentro de 'barata'.")
    active = fields.Boolean(
        string="Activo", default=True,
        help="Archivar una fila deja de aplicar sus palabras en el siguiente "
             "mensaje. Es la forma de apagar una palabra que resultó mala idea "
             "sin perder de vista que se probó.")
    palabras_efectivas = fields.Text(
        string="Lo que el agente reconoce", compute='_compute_palabras_efectivas',
        help="Las del código más las de esta pantalla, ya sin repetidas. Es "
             "exactamente lo que se le manda al agente para esa opción.")
    opciones_validas = fields.Char(
        string="Opciones de este paso", compute='_compute_opciones_validas')

    # ------------------------------------------------------------------
    # Selección y ayudas del formulario
    # ------------------------------------------------------------------

    @api.model
    def _visar_selection_paso(self):
        pasos = self.env['appointment.type']._visar_vocabulario_pasos()
        return [(paso, _VISAR_PASO_ETIQUETAS.get(paso, paso)) for paso in pasos]

    @api.depends('paso')
    def _compute_opciones_validas(self):
        """Las claves que admite el paso elegido, para no tener que adivinarlas."""
        Flow = self.env['appointment.type']
        for registro in self:
            claves = Flow._visar_vocabulario_claves(registro.paso) if registro.paso else []
            registro.opciones_validas = ', '.join(claves)

    @api.depends('paso', 'opcion', 'palabras', 'active')
    def _compute_palabras_efectivas(self):
        """Lo que el agente va a reconocer de verdad, no lo que se escribió aquí.

        Existe para que no haya que creerse la fusión: una palabra que ya venía
        en el código no aparece dos veces, una repetida tampoco, y una fila
        archivada se ve que no cuenta. Un campo que solo dijera "guardado"
        estaría verde sin estarlo.

        Suma las demás filas de la misma opción, porque el agente también las
        suma: dos consultores pueden estar enseñándole palabras a la vez.
        """
        Flow = self.env['appointment.type']
        for registro in self:
            if not (registro.paso and registro.opcion):
                registro.palabras_efectivas = ''
                continue
            otras = self.search([
                ('paso', '=', registro.paso),
                ('opcion', '=', registro.opcion),
                ('id', '!=', registro._origin.id or 0),
            ])
            anadidas = []
            for fila in otras:
                anadidas += Flow._visar_vocabulario_lineas(fila.palabras)
            if registro.active:
                anadidas += Flow._visar_vocabulario_lineas(registro.palabras)
            registro.palabras_efectivas = '\n'.join(Flow._visar_vocabulario(
                {(registro.paso, registro.opcion): anadidas},
                registro.paso, registro.opcion))

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    @api.constrains('paso', 'opcion')
    def _check_opcion(self):
        """La opción tiene que existir, o las palabras no se aplicarían nunca.

        Es la única validación que hace falta y la que más importa: una clave
        mal escrita se guardaría sin protestar, no haría nada, y el consultor
        seguiría creyendo que el agente ya conoce la palabra. Fallar al guardar
        es mucho más barato que fallar en la conversación.
        """
        Flow = self.env['appointment.type']
        for registro in self:
            claves = Flow._visar_vocabulario_claves(registro.paso)
            if (registro.opcion or '').strip() not in claves:
                raise ValidationError(_(
                    "La opción «%(opcion)s» no existe en el paso «%(paso)s». "
                    "Las de ese paso son: %(claves)s.",
                    opcion=registro.opcion or '',
                    paso=_VISAR_PASO_ETIQUETAS.get(registro.paso, registro.paso),
                    claves=', '.join(claves) or _("ninguna"),
                ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('opcion'):
                vals['opcion'] = vals['opcion'].strip()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('opcion'):
            vals['opcion'] = vals['opcion'].strip()
        return super().write(vals)

    @api.depends('paso', 'opcion')
    def _compute_display_name(self):
        for registro in self:
            paso = _VISAR_PASO_ETIQUETAS.get(registro.paso, registro.paso or '')
            registro.display_name = f"{paso} / {registro.opcion or ''}"
