# -*- coding: utf-8 -*-
from odoo import fields, models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    # Etiqueta el adjunto con el nombre lógico del campo-foto de la hoja de trabajo
    # al que pertenece. Permite galerías multi-foto por campo (App de Campo): varios
    # adjuntos con el mismo (res_model, res_id, visar_photo_key) forman una galería.
    # Vacío = adjunto sin campo asociado (p. ej. la antigua sección "Fotos" general).
    visar_photo_key = fields.Char(index=True)
