# -*- coding: utf-8 -*-
"""El catálogo de plagas: una sola lista para la pregunta de la cita y los add-ons.

Hasta el 8-oct-2026 las plagas vivían escritas tres veces —el cuestionario del
agente, la plantilla de la cita web y el controlador que la recibe— y un add-on
solo podía atarse a una de ellas con una casilla fija («Producto control de
roedores»). Añadir una plaga era un despliegue, y atar un add-on a otra que no
fuera roedores, imposible.

Aquí están las plagas que SÍ se atienden con el tabulador. Las que cortan a
valoración técnica (termitas, chinches, «no estoy seguro») no son de este
catálogo: nunca llegan al paso de extras, así que no hay add-on que atarles.
"""
import re
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Valores que la pregunta de plagas ya usa para otra cosa: «Protección general»
# y los cortes a valoración. Una plaga con uno de estos códigos sería
# indistinguible de esa opción al contestar.
VISAR_PLAGA_CODIGOS_RESERVADOS = (
    'proteccion_general', 'termitas', 'chinches', 'no_se',
)


def _visar_plaga_slug(texto):
    """'Aves y murciélagos' -> 'aves_y_murcielagos'."""
    plano = unicodedata.normalize('NFKD', texto or '')
    plano = ''.join(c for c in plano if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9]+', '_', plano).strip('_')


class VisarPlaga(models.Model):
    _name = 'visar.plaga'
    _description = "Plaga (pregunta de la cita y add-ons)"
    _order = 'sequence, id'

    name = fields.Char(
        "Plaga", required=True,
        help="Como la lee el cliente en la pregunta de plagas. En WhatsApp la "
             "fila admite 24 caracteres.")
    description = fields.Char(
        "Descripción",
        help="El renglón de apoyo bajo el nombre: qué bichos cubre "
             "(«Cucarachas, alacranes, hormigas, arañas»).")
    code = fields.Char(
        "Código", required=True, copy=False,
        help="Identificador interno con el que se guarda la respuesta del "
             "cliente. Se propone solo a partir del nombre; no hace falta "
             "tocarlo.")
    sequence = fields.Integer("Secuencia", default=10)
    active = fields.Boolean(
        "Activo", default=True,
        help="Archivada, la plaga deja de ofrecerse en la pregunta de la cita.")

    _code_uniq = models.Constraint(
        'UNIQUE (code)', "Ya existe una plaga con ese código.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code'):
                vals['code'] = _visar_plaga_slug(vals.get('name'))
        return super().create(vals_list)

    @api.onchange('name')
    def _onchange_name_visar_code(self):
        for plaga in self:
            if not plaga.code and plaga.name:
                plaga.code = _visar_plaga_slug(plaga.name)

    @api.constrains('code')
    def _check_code(self):
        for plaga in self:
            if plaga.code != _visar_plaga_slug(plaga.code):
                raise ValidationError(_(
                    "El código de la plaga «%s» solo admite minúsculas, "
                    "números y guion bajo.", plaga.name))
            if plaga.code in VISAR_PLAGA_CODIGOS_RESERVADOS:
                raise ValidationError(_(
                    "El código «%s» ya lo usa otra opción de la pregunta de "
                    "plagas; elige otro.", plaga.code))

    @api.model
    def _visar_ofrecidas(self):
        """Las plagas que la pregunta de la cita ofrece, en su orden.

        Con `sudo`: las lee el visitante anónimo de la cita web, igual que el
        resto del catálogo del cuestionario.
        """
        return self.sudo().search([])
