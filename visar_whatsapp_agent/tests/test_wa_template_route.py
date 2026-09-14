# -*- coding: utf-8 -*-
"""Qué plantilla usa cada aviso: lo que viaja al runtime y lo que se rechaza.

Dos familias, y las dos existen por un fallo concreto:

  * **Lo que viaja.** Una fila vacía, o con una plantilla que Meta aún no aprueba,
    tiene que ser INDISTINGUIBLE de lo de antes para el runtime: mensaje libre.
    Si una plantilla en revisión viajara, el runtime la mandaría y Meta la
    rechazaría: el buzón reintenta cinco veces y deja "el cliente NO fue avisado".
  * **Lo que se rechaza al guardar.** El runtime rellena la plantilla con los
    parámetros que el código ya manda. Una variable de más, una cabecera que nadie
    rellena o un botón sin payload solo se descubrían en producción, cuando Meta
    devolvía error. Aquí se descubren al elegir.
"""
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.visar_whatsapp_agent.models.visar_wa_template_route import (
    ESPECIFICACION,
)


@tagged('post_install', '-at_install')
class TestPlantillasDeAvisos(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Route = cls.env['visar.wa.template.route'].sudo()
        cls.cuenta = cls.env['whatsapp.account'].sudo().create({
            'name': 'Cuenta prueba plantillas',
            'app_uid': 'app-prueba', 'app_secret': 'secreto',
            'account_uid': 'waba-prueba', 'phone_uid': 'numero-prueba',
            'token': 'token-prueba',
            # El modulo nativo exige a quien avisar de los mensajes entrantes.
            'notify_user_ids': [(6, 0, cls.env.ref('base.user_admin').ids)],
        })

    def _plantilla(self, nombre, body, status='approved', header='none', botones=()):
        return self.env['whatsapp.template'].sudo().create({
            'name': nombre,
            'template_name': nombre,
            'template_type': 'utility',
            'lang_code': 'es_MX',
            'wa_account_id': self.cuenta.id,
            'model_id': self.env['ir.model']._get('res.partner').id,
            'phone_field': 'phone',
            'body': body,
            'header_type': header,
            'status': status,
            'button_ids': [(0, 0, {'button_type': tipo, 'name': texto})
                           for tipo, texto in botones],
        })

    def _fila(self, clave):
        return self.Route.search([('key', '=', clave)])

    # --- La siembra ---------------------------------------------------

    def test_hay_una_fila_por_aviso_y_ninguna_con_plantilla(self):
        """Instalar el módulo no puede cambiarle nada a ningún cliente."""
        self.assertEqual(set(self.Route.search([]).mapped('key')),
                         set(ESPECIFICACION))
        # Una BD de prueba puede traer asignaciones: la siembra de fábrica es
        # lo que dice el XML, y ese no asigna ninguna.
        for clave in ESPECIFICACION:
            fila = self.env.ref('visar_whatsapp_agent.wa_template_route_%s' % clave)
            self.assertEqual(fila.key, clave)

    def test_el_recontacto_de_leads_no_es_asignable(self):
        """Lo redacta el modelo: va SIEMPRE libre, por diseño."""
        self.assertNotIn('lead_followup', ESPECIFICACION)

    # --- Lo que viaja al runtime --------------------------------------

    def test_sin_plantillas_no_viaja_nada(self):
        self.Route.search([]).write({'template_id': False})
        self.assertEqual(self.Route._agent_payload(), {})

    def test_una_aprobada_viaja_con_nombre_e_idioma(self):
        self.Route.search([]).write({'template_id': False})
        tpl = self._plantilla('prueba_en_camino',
                              "Su técnico {{1}} llega en {{2}} minutos.")
        self._fila('enroute').template_id = tpl
        self.assertEqual(self.Route._agent_payload(), {
            'enroute': {'name': 'prueba_en_camino', 'lang': 'es_MX'}})

    def test_una_en_revision_se_asigna_pero_NO_viaja(self):
        """Así se deja lista y empieza a usarse sola cuando Meta la aprueba."""
        self.Route.search([]).write({'template_id': False})
        tpl = self._plantilla('prueba_pendiente',
                              "Su técnico {{1}} llega en {{2}} minutos.",
                              status='pending')
        self._fila('enroute').template_id = tpl
        self.assertEqual(self.Route._agent_payload(), {})
        tpl.status = 'approved'
        self.assertIn('enroute', self.Route._agent_payload())

    def test_una_pausada_por_meta_deja_de_viajar(self):
        """El cron de sincronización baja el estado; el payload lo respeta."""
        self.Route.search([]).write({'template_id': False})
        tpl = self._plantilla('prueba_pausada',
                              "Su técnico {{1}} llega en {{2}} minutos.")
        self._fila('enroute').template_id = tpl
        tpl.status = 'paused'
        self.assertEqual(self.Route._agent_payload(), {})

    def test_la_config_del_runtime_trae_las_plantillas(self):
        """Viaja por el canal que ya existe, no por una RPC nueva."""
        config = self.env['visar.agent.tools'].sudo().agent_runtime_config()
        self.assertIn('wa_templates', config)
        self.assertEqual(config['wa_templates'], self.Route._agent_payload())

    # --- Lo que se rechaza al guardar ---------------------------------

    def test_variables_de_mas_se_rechazan(self):
        """`reschedule` manda UN parámetro: el técnico."""
        tpl = self._plantilla('prueba_dos_vars',
                              "Su técnico {{1}} llega en {{2}} minutos.")
        with self.assertRaises(ValidationError):
            self._fila('reschedule').template_id = tpl

    def test_una_rechazada_no_se_puede_asignar(self):
        tpl = self._plantilla('prueba_rechazada', "Su técnico {{1}} acudió.",
                              status='rejected')
        with self.assertRaises(ValidationError):
            self._fila('reschedule').template_id = tpl

    def test_el_reporte_exige_cabecera_de_documento(self):
        tpl = self._plantilla('prueba_reporte_sin_pdf', "Reporte: {{1}}")
        with self.assertRaises(ValidationError):
            self._fila('report').template_id = tpl

    def test_reschedule_offer_exige_el_boton_de_respuesta_rapida(self):
        """Es el que el cliente pulsa; sin él la invitación no lleva a ningún sitio."""
        cuerpo = "Su técnico {{1}} acudió. Reprograme con {{2}} horas."
        sin_boton = self._plantilla('prueba_oferta_sin_boton', cuerpo)
        with self.assertRaises(ValidationError):
            self._fila('reschedule_offer').template_id = sin_boton
        con_boton = self._plantilla('prueba_oferta_con_boton', cuerpo,
                                    botones=[('quick_reply', 'Elegir nuevo horario')])
        self._fila('reschedule_offer').template_id = con_boton
        self.assertEqual(self._fila('reschedule_offer').template_id, con_boton)

    def test_un_boton_en_un_aviso_que_no_lo_manda_se_rechaza(self):
        """El runtime no manda parámetros de botón para `enroute`: Meta rechazaría."""
        tpl = self._plantilla('prueba_en_camino_con_boton',
                              "Su técnico {{1}} llega en {{2}} minutos.",
                              botones=[('quick_reply', 'Gracias')])
        with self.assertRaises(ValidationError):
            self._fila('enroute').template_id = tpl

    def test_una_plantilla_de_otra_cuenta_se_rechaza(self):
        """Una plantilla pertenece a una cuenta, y no se manda desde otro número."""
        self.env['visar.whatsapp.config'].sudo().search([]).write({'active': False})
        self.env['visar.whatsapp.config'].sudo().create({
            'name': 'Config prueba', 'phone_uid': 'otro-numero'})
        tpl = self._plantilla('prueba_otra_cuenta',
                              "Su técnico {{1}} llega en {{2}} minutos.")
        with self.assertRaises(ValidationError):
            self._fila('enroute').template_id = tpl

    def test_la_especificacion_cuadra_con_lo_que_manda_la_invitacion(self):
        """Si alguien cambia los parámetros de un aviso, esta prueba lo recuerda.

        `reschedule_offer` manda [técnico, horas] desde `visar_field_app`; si ese
        módulo no está, no hay nada que contrastar.
        """
        if not hasattr(self.env['project.task'], '_visar_msg_reschedule_offer'):
            self.skipTest("visar_field_app no está instalado")
        tarea = self.env['project.task'].create({'name': 'Tarea especificación'})
        _texto, params = tarea._visar_msg_reschedule_offer()
        self.assertEqual(len(params), ESPECIFICACION['reschedule_offer']['variables'])

    # --- El cron de estado --------------------------------------------

    def test_el_cron_sobrevive_a_un_error_de_meta(self):
        """Una plantilla que Meta no devuelve no deja sin sincronizar a las demás."""
        from unittest.mock import patch

        self.Route.search([]).write({'template_id': False})
        a = self._plantilla('prueba_sync_a', "Su técnico {{1}} llega en {{2}} minutos.")
        b = self._plantilla('prueba_sync_b', "Su técnico {{1}} ya llegó, {{2}} minutos.")
        self._fila('enroute').template_id = a
        self._fila('arrived').template_id = b

        llamadas = []

        def falso_sync(tpl_self):
            llamadas.append(tpl_self.template_name)
            if tpl_self.template_name == 'prueba_sync_a':
                raise RuntimeError("Meta no contesta")

        Template = type(self.env['whatsapp.template'])
        with patch.object(Template, 'button_sync_template', falso_sync):
            self.Route._visar_cron_sync_templates()
        self.assertEqual(sorted(llamadas), ['prueba_sync_a', 'prueba_sync_b'])
