# -*- coding: utf-8 -*-
"""Candado de usuarios internos (17-nov-2026): por HTTP no salen internos nuevos."""
from contextlib import contextmanager
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.auth_signup.models.res_users import ResUsers as SignupUsers
from odoo.addons.http_routing.tests.common import MockRequest


@tagged('post_install', '-at_install')
class TestCandadoUsuarios(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, no_reset_password=True))
        cls.admin = cls.env.ref('base.user_admin')
        cls.grupo_interno = cls.env.ref('base.group_user')
        cls.grupo_portal = cls.env.ref('base.group_portal')
        cls.portal = cls.env['res.users'].create({
            'name': 'Cliente portal', 'login': 'candado-portal@example.com',
            'group_ids': [Command.set([cls.grupo_portal.id])]})
        cls.archivado = cls.env['res.users'].create({
            'name': 'Interno archivado', 'login': 'candado-archivado@example.com',
            'active': False})

    @contextmanager
    def _bloqueado(self):
        # AccessError también es UserError: hay que ver que sea el candado.
        with MockRequest(self.env), self.assertRaises(UserError) as cm:
            yield
        self.assertIn('solo los da de alta Hanova', str(cm.exception))

    def _como_admin(self):
        return self.env['res.users'].with_user(self.admin)

    # --- fuera de HTTP (shell, scripts, crons): abierto -----------------------

    def test_fuera_de_http_se_crea_interno(self):
        usuario = self.env['res.users'].create({
            'name': 'Alta por shell', 'login': 'candado-shell@example.com'})
        self.assertFalse(usuario.share)

    # --- por HTTP: crear ------------------------------------------------------

    def test_admin_no_crea_interno(self):
        with self._bloqueado():
            self._como_admin().create({
                'name': 'Nuevo interno', 'login': 'candado-nuevo@example.com'})

    def test_superusuario_por_http_tampoco(self):
        # «Convertirse en superusuario» (/web/become) corre con su=True.
        with self._bloqueado():
            self.env['res.users'].sudo().create({
                'name': 'Nuevo interno', 'login': 'candado-su@example.com'})

    def test_duplicar_interno_no(self):
        with self._bloqueado():
            self.admin.with_user(self.admin).copy({'login': 'candado-copia@example.com'})

    def test_invitar_usuarios_desde_ajustes_no(self):
        with self._bloqueado():
            self._como_admin().web_create_users(['candado-invita@example.com'])

    def test_no_sale_la_invitacion_por_correo(self):
        # auth_signup la manda con force_send dentro del create: hay que frenar antes.
        with patch.object(SignupUsers, '_action_reset_password') as invitar, \
                self._bloqueado():
            self._como_admin().with_context(no_reset_password=False).create({
                'name': 'Con correo', 'login': 'candado-correo@example.com',
                'email': 'candado-correo@example.com'})
        invitar.assert_not_called()

    def test_admin_si_crea_portal(self):
        with MockRequest(self.env):
            usuario = self._como_admin().create({
                'name': 'Portal nuevo', 'login': 'candado-portal2@example.com',
                'group_ids': [Command.set([self.grupo_portal.id])]})
        self.assertTrue(usuario.share)

    # --- por HTTP: escribir ---------------------------------------------------

    def test_portal_a_interno_no(self):
        with self._bloqueado():
            self.portal.with_user(self.admin).write({'group_ids': [
                Command.unlink(self.grupo_portal.id), Command.link(self.grupo_interno.id)]})

    def test_reactivar_interno_no(self):
        with self._bloqueado():
            self.archivado.with_user(self.admin).action_unarchive()

    def test_meter_usuario_al_grupo_interno_no(self):
        sin_grupos = self.env['res.users'].create({
            'name': 'Sin grupos', 'login': 'candado-singrupos@example.com',
            'group_ids': [Command.clear()]})
        self.assertTrue(sin_grupos.share)
        with self._bloqueado():
            self.grupo_interno.with_user(self.admin).write(
                {'user_ids': [Command.link(sin_grupos.id)]})

    def test_ediciones_normales_si(self):
        with MockRequest(self.env):
            self.admin.with_user(self.admin).write({'signature': '<p>Visar</p>'})
            self.portal.with_user(self.admin).write({'name': 'Cliente renombrado'})
            self.portal.with_user(self.admin).action_archive()
        self.assertEqual(self.portal.name, 'Cliente renombrado')
        self.assertFalse(self.portal.active)
