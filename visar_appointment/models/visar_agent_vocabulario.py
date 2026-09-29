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

Este modelo ES el vocabulario, desde el 29-sep-2026:

    lo que el agente ve  =  la fila de aquí, si la hay
                            las palabras del código, si no

Hasta esa fecha solo **sumaba**, y el código no se podía tocar desde la
pantalla. La razón era buena —el código es lo que afirman las pruebas y lo que
sirve en una base recién creada— pero el precio se vio en producción: los
diccionarios de fábrica eran **invisibles** desde Odoo y esta pantalla llevaba
meses **con cero filas**. Lo único que ofrecía era añadir a ciegas sobre una
lista que no se podía leer, así que nadie la usó nunca.

El código no desaparece: queda como **suelo recuperable**. Se enseña al lado en
`palabras_originales`, el botón «Restaurar valores originales» lo devuelve, y
una ranura nueva que un desarrollador añada sigue funcionando antes de
sembrarse. Archivar una fila también vuelve a los valores de fábrica.

**Se aplica en el siguiente paso, sin reiniciar nada.** `agent_booking_step` es
una llamada RPC viva, no una caché: en cuanto se guarda, el paso siguiente ya
lleva la palabra. La pregunta que el cliente tiene YA en pantalla conserva las
palabras con las que se pintó, porque el runtime guardó sus opciones al
pintarla; se nota a partir de la respuesta siguiente.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.visar_appointment.models.appointment_wizard_flow import (
    _visar_vocab_norm,
)

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
    # Sin `required`: vaciar una opción es una respuesta legítima ahora que la
    # fila manda —significa "esta opción no reconoce ninguna palabra"—, y hay
    # una de fábrica así (`valuation.continuar`, que el autopiloto contesta
    # solo). Con `required` no se habría podido sembrar.
    palabras = fields.Text(
        string="Palabras del cliente",
        help="Una palabra o frase por línea, tal y como la escribe el cliente. "
             "Valen las raíces: 'cucarach' cubre cucaracha y cucarachas. Se "
             "comparan sin acentos y sin distinguir mayúsculas, y solo al "
             "principio de una palabra: 'rata' no coincide dentro de 'barata'.")
    active = fields.Boolean(
        string="Activo", default=True,
        help="Archivar una fila devuelve esa opción a los valores de fábrica en "
             "el siguiente mensaje, sin perder de vista lo que se había "
             "probado. Es la marcha atrás rápida.")
    palabras_efectivas = fields.Text(
        string="Lo que el agente reconoce", compute='_compute_palabras_efectivas',
        help="Exactamente lo que se le manda al agente para esa opción, ya sin "
             "repetidas. Si la fila está archivada, son las de fábrica.")
    palabras_originales = fields.Text(
        string="Valores de fábrica", compute='_compute_palabras_originales',
        help="Las que trae el código para esta opción. No cambian al editar: "
             "son la referencia y lo que devuelve «Restaurar valores "
             "originales».")
    es_original = fields.Boolean(
        string="Sin cambios", compute='_compute_palabras_originales',
        help="Si esta opción sigue exactamente como viene de fábrica.")
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

        La diferencia importa en dos casos y por eso el campo se queda aunque la
        fila ya mande: una palabra repetida (con o sin acentos) se cuenta una
        sola vez, y una fila **archivada** enseña los valores de fábrica, que es
        lo que el agente usará de verdad. Un campo que solo dijera "guardado"
        estaría verde sin estarlo.

        Pasa por el MISMO `_visar_vocabulario` que el cuestionario, no por una
        copia: si la regla cambia, este campo cambia con ella o no sirve.
        """
        Flow = self.env['appointment.type']
        for registro in self:
            if not (registro.paso and registro.opcion):
                registro.palabras_efectivas = ''
                continue
            overlay = ({(registro.paso, registro.opcion):
                        Flow._visar_vocabulario_lineas(registro.palabras)}
                       if registro.active else {})
            registro.palabras_efectivas = '\n'.join(Flow._visar_vocabulario(
                overlay, registro.paso, registro.opcion))

    @api.depends('paso', 'opcion', 'palabras')
    def _compute_palabras_originales(self):
        """Las de fábrica, y si esta fila sigue igual que ellas.

        Se comparan ya normalizadas y sin orden: reordenar líneas o cambiar un
        acento no es un cambio de vocabulario, y marcarlo como "modificado"
        mandaría a alguien a buscar una diferencia que no existe.
        """
        Flow = self.env['appointment.type']
        for registro in self:
            if not (registro.paso and registro.opcion):
                registro.palabras_originales = ''
                registro.es_original = True
                continue
            originales = Flow._visar_vocabulario_originales(
                registro.paso, registro.opcion)
            registro.palabras_originales = '\n'.join(originales)
            actuales = Flow._visar_vocabulario_lineas(registro.palabras)
            registro.es_original = (
                {_visar_vocab_norm(p) for p in actuales}
                == {_visar_vocab_norm(p) for p in originales})

    def action_restaurar_originales(self):
        """Devuelve la opción a los valores que trae el código.

        Es la mitad que hace reversible dejar que la pantalla mande. Sin esto,
        borrar una palabra de fábrica por error sería irreparable sin un
        desarrollador —y el motivo por el que esto sumaba en vez de sustituir.
        """
        Flow = self.env['appointment.type']
        for registro in self:
            registro.palabras = '\n'.join(Flow._visar_vocabulario_originales(
                registro.paso, registro.opcion))
        return True

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    @api.constrains('paso', 'opcion', 'active')
    def _check_una_fila_por_opcion(self):
        """Una sola fila activa por opción.

        Mientras esto solo sumaba, dos filas eran inofensivas —se concatenaban—
        y el docstring lo daba por bueno ("dos consultores pueden estar
        enseñándole palabras a la vez"). Ahora la fila ES la verdad, así que dos
        filas son dos verdades y el agente usaría una de las dos sin decir cuál.
        """
        for registro in self:
            if not registro.active:
                continue
            repetidas = self.search_count([
                ('paso', '=', registro.paso),
                ('opcion', '=', registro.opcion),
                ('id', '!=', registro.id),
            ])
            if repetidas:
                raise ValidationError(_(
                    "Ya hay una fila para «%(opcion)s» en el paso "
                    "«%(paso)s». Edita esa: ahora la fila sustituye a los "
                    "valores de fábrica, así que dos filas serían dos "
                    "vocabularios distintos para la misma opción.",
                    opcion=registro.opcion or '',
                    paso=_VISAR_PASO_ETIQUETAS.get(registro.paso, registro.paso),
                ))

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
