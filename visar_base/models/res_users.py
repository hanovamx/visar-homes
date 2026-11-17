# -*- coding: utf-8 -*-
"""Candado: los usuarios internos no se dan de alta desde Odoo (17-nov-2026).

Cada usuario interno es una licencia de Enterprise, y `admin` (el único interno,
con Ajustes) podía crear los que quisiera. Desde la interfaz, XML-RPC o JSON-RPC
ya no se puede:

  * crear un usuario interno (formulario, duplicar, importar, «Invitar usuarios»,
    «Crear usuario» desde un empleado);
  * convertir un usuario de portal en interno, ni reactivar un interno archivado;
  * meter usuarios a un grupo, o tocar los grupos implicados, de forma que salga
    un interno nuevo.

Los usuarios de portal siguen igual: registro en el sitio, compra, citas y
«Conceder acceso al portal» no pasan por aquí.

**Por qué se mira la petición HTTP y no `env.su`.** Odoo deja a cualquier usuario
con Ajustes «convertirse en superusuario» en modo desarrollador (`/web/become`),
y entonces todo corre con `su`; una acción de servidor también puede llamar
`sudo()`. Así que el candado cierra cualquier escritura que llegue por HTTP, sea
quien sea. Queda abierto lo que no pasa por HTTP: `odoo shell`, scripts, `-u`
por línea de comandos, crons y pruebas. Por ahí da de alta Hanova.

No es una barrera de seguridad contra alguien con Ajustes: puede desinstalar el
módulo, o programar un cron con código. Es un freno para el uso normal.
"""
from odoo import api, models
from odoo.exceptions import UserError
from odoo.http import request

MENSAJE = (
    "Los usuarios internos solo los da de alta Hanova. Desde Odoo no se puede "
    "crear uno, ni convertir un usuario de portal en interno, ni reactivar uno "
    "archivado. Los usuarios de portal sí se pueden crear.")


def _candado_activo():
    return bool(request)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _visar_valores_dan_interno(self, vals):
        """¿Estos valores de `create` dejan al usuario como interno?

        Se revisa ANTES de crear: `auth_signup` manda la invitación por correo
        con `force_send` dentro del mismo `create`, y un rollback no la detiene.
        """
        if 'group_ids' in vals:
            comandos = vals['group_ids']
        else:
            comandos = self.default_get(['group_ids']).get('group_ids', [])
        grupos = self.new({'group_ids': comandos}).group_ids._origin
        return self.env.ref('base.group_user') in grupos.sudo().all_implied_ids

    @api.model_create_multi
    def create(self, vals_list):
        if not _candado_activo():
            return super().create(vals_list)
        if any(self._visar_valores_dan_interno(vals) for vals in vals_list):
            raise UserError(MENSAJE)
        usuarios = super().create(vals_list)
        # Por si otro módulo vuelve interno al usuario dentro del create.
        if usuarios.sudo().filtered(lambda u: not u.share):
            raise UserError(MENSAJE)
        return usuarios

    def write(self, vals):
        if not _candado_activo():
            return super().write(vals)
        externos = self.sudo().filtered(lambda u: u.share or not u.active)
        res = super().write(vals)
        if externos.filtered(lambda u: u.active and not u.share):
            raise UserError(MENSAJE)
        return res


class ResGroups(models.Model):
    _inherit = 'res.groups'

    def _visar_internos_activos(self):
        return set(self.env['res.users'].sudo().search([('share', '=', False)]).ids)

    def _visar_revisar(self, antes):
        if self._visar_internos_activos() - antes:
            raise UserError(MENSAJE)

    @api.model_create_multi
    def create(self, vals_list):
        if not _candado_activo():
            return super().create(vals_list)
        antes = self._visar_internos_activos()
        grupos = super().create(vals_list)
        self._visar_revisar(antes)
        return grupos

    def write(self, vals):
        if not _candado_activo():
            return super().write(vals)
        antes = self._visar_internos_activos()
        res = super().write(vals)
        self._visar_revisar(antes)
        return res
