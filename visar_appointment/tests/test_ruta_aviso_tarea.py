# -*- coding: utf-8 -*-
"""El aviso de ruta cuando oficina asigna a mano (24-sep-2026).

Las reglas de traslado decidían qué horarios ve el cliente y **nada más**: una
tarea asignada o movida a mano dentro de Odoo no las consultaba ni para avisar.
Lo que se fija aquí es lo que Visar pidió, que tiene dos mitades igual de
importantes:

* que el aviso **aparezca** cuando la asignación rompe una de las dos reglas;
* que **no bloquee nada** — la tarea se guarda con su técnico y su fecha, porque
  una ruta apretada puede ser exactamente lo que oficina quiso.

Y una tercera que es la que evita que el aviso se convierta en ruido: cuándo
**no** habla (lo que ya pasó, lo que nace de una reserva, lo que no se puede
comprobar).

Ninguna prueba toca la red: se parchea la matriz de Mapbox, igual que
`test_travel_feasibility.py`.
"""
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

_SERVICE = 'odoo.addons.visar_base.models.visar_travel.VisarMapboxService'


@tagged('post_install', '-at_install')
class TestAvisoDeRutaEnLaTarea(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('visar.travel.enabled', '1')
        cls.env['ir.config_parameter'].sudo().set_param('visar.travel.minutes', '20')
        cls.env['ir.config_parameter'].sudo().set_param(
            'visar.travel.cluster_minutes', '')

        cls.proyecto = cls.env['project.project'].create({
            'name': 'Campo (prueba ruta)', 'is_fsm': True,
            # `is_fsm` exige compañía (restricción nativa).
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Cliente lejano', 'partner_latitude': 25.65,
            'partner_longitude': -100.40})
        cls.vecino = cls.env['res.partner'].create({
            'name': 'Cliente vecino', 'partner_latitude': 25.70,
            'partner_longitude': -100.30})
        empleado = cls.env['hr.employee'].create({'name': 'Tecnico de prueba'})
        cls.recurso = cls.env['appointment.resource'].create(
            {'name': 'Tecnico de prueba', 'visar_employee_id': empleado.id})
        cls.empleado = empleado
        cls.tipo = cls.env['appointment.type'].sudo(
        )._visar_get_master_appointment_type()
        cls.tipo.resource_ids = [(4, cls.recurso.id)]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _manana(self, hora, dias=1):
        """Un datetime UTC a las `hora`, `dias` desde hoy (negativo = pasado)."""
        base = fields.Datetime.add(fields.Datetime.now(), days=dias)
        return base.replace(hour=hora, minute=0, second=0, microsecond=0)

    def _parada(self, hora, dias=1):
        """Una cita del técnico ese día a esa hora, con su línea de agenda."""
        inicio = self._manana(hora, dias=dias)
        evento = self.env['calendar.event'].create({
            'name': 'Parada previa',
            'start': inicio,
            'stop': fields.Datetime.add(inicio, hours=1),
            'partner_ids': [(4, self.vecino.id)],
            'appointment_type_id': self.tipo.id,
        })
        self.env['appointment.booking.line'].create({
            'calendar_event_id': evento.id,
            'appointment_resource_id': self.recurso.id,
            'capacity_reserved': 1,
        })
        return evento

    def _tarea(self, hora=None, tecnico=True):
        vals = {'name': 'Servicio asignado a mano',
                'project_id': self.proyecto.id,
                'partner_id': self.cliente.id}
        if hora is not None:
            inicio = self._manana(hora)
            vals['planned_date_begin'] = inicio
            vals['date_deadline'] = fields.Datetime.add(inicio, hours=1)
        if tecnico:
            vals['visar_technician_ids'] = [(6, 0, self.empleado.ids)]
        return self.env['project.task'].create(vals)

    def _matriz(self, minutos):
        """Mapbox contesta `minutos` en los dos sentidos, para toda parada."""
        def falso(_self, coords, depart_at=None):
            n = len(coords)
            return [[0 if i == j else minutos * 60 for j in range(n)]
                    for i in range(n)]
        return patch(_SERVICE + '._visar_mapbox_matrix', falso)

    # ------------------------------------------------------------------
    # Avisa
    # ------------------------------------------------------------------

    def test_avisa_cuando_el_tecnico_no_alcanza_a_llegar(self):
        self._parada(15)                      # termina a las 16:00 UTC
        with self._matriz(40):                # 40 min de camino, margen 20
            tarea = self._tarea(hora=16)      # pegada a la anterior
        self.assertTrue(tarea.visar_ruta_aviso)
        self.assertIn('no alcanza a llegar', tarea.visar_ruta_aviso)
        self.assertIn('40 min', tarea.visar_ruta_aviso)

    def test_avisa_cuando_el_dia_queda_en_otra_zona(self):
        """Tres horas de hueco: el presupuesto da de sobra y la zona no."""
        self._parada(15)
        with self._matriz(40):
            tarea = self._tarea(hora=20)
        self.assertIn('otra zona', tarea.visar_ruta_aviso)

    def test_el_aviso_NO_bloquea_la_asignacion(self):
        """Lo que Visar pidió: se guarda igual. Un aviso que estorba se apaga."""
        self._parada(15)
        with self._matriz(40):
            tarea = self._tarea(hora=16)
        self.assertEqual(tarea.visar_technician_ids, self.empleado)
        self.assertEqual(tarea.planned_date_begin, self._manana(16))

    def test_queda_nota_en_el_chatter(self):
        self._parada(15)
        with self._matriz(40):
            tarea = self._tarea(hora=16)
        cuerpos = '\n'.join(tarea.message_ids.mapped('body'))
        self.assertIn('ruta', cuerpos.lower())

    def test_cambiar_de_dia_lo_resuelve_y_se_dice(self):
        self._parada(15)
        with self._matriz(40):
            tarea = self._tarea(hora=16)
            self.assertTrue(tarea.visar_ruta_aviso)
            # Otro día: ahí el técnico no tiene ninguna otra parada.
            libre = fields.Datetime.add(self._manana(16), days=3)
            tarea.write({'planned_date_begin': libre,
                         'date_deadline': fields.Datetime.add(libre, hours=1)})
        self.assertFalse(tarea.visar_ruta_aviso)
        self.assertIn('ya cuadra',
                      '\n'.join(tarea.message_ids.mapped('body')).lower())

    def test_mover_la_CITA_refresca_el_aviso_de_su_tarea(self):
        """Si no, el aviso se queda hablando del horario viejo."""
        evento = self._parada(15)
        with self._matriz(40):
            tarea = self._tarea(hora=16)
            self.assertTrue(tarea.visar_ruta_aviso)
            # La parada previa se va a otro día: ya no estorba.
            otro = fields.Datetime.add(evento.start, days=2)
            evento.write({'start': otro,
                          'stop': fields.Datetime.add(otro, hours=1)})
            tarea.invalidate_recordset()
        self.assertFalse(tarea.visar_ruta_aviso)

    # ------------------------------------------------------------------
    # Se calla
    # ------------------------------------------------------------------

    def test_no_avisa_si_la_ruta_cuadra(self):
        self._parada(15)
        with self._matriz(10):
            tarea = self._tarea(hora=16)
        self.assertFalse(tarea.visar_ruta_aviso)

    def test_no_avisa_de_lo_que_ya_paso(self):
        """Un aviso sobre el servicio de la semana pasada no tiene arreglo.

        La parada vecina es de AYER a propósito: sin el candado del pasado, esta
        misma asignación sí sacaría aviso (son los 40 min contra un margen de
        20), así que la prueba mide el candado y no la falta de datos.
        """
        self._parada(15, dias=-1)
        ayer = self._manana(16, dias=-1)
        with self._matriz(40):
            tarea = self._tarea()
            tarea.write({'planned_date_begin': ayer,
                         'date_deadline': fields.Datetime.add(ayer, hours=1)})
        self.assertFalse(tarea.visar_ruta_aviso)

    def test_no_avisa_sin_tecnico_asignado(self):
        self._parada(15)
        with self._matriz(40):
            tarea = self._tarea(hora=16, tecnico=False)
        self.assertFalse(tarea.visar_ruta_aviso)

    def test_no_avisa_sin_coordenadas(self):
        """Degradar, nunca inventar (§5.4): sin dirección no se sabe."""
        self._parada(15)
        self.cliente.write({'partner_latitude': 0.0, 'partner_longitude': 0.0})
        with self._matriz(40):
            tarea = self._tarea(hora=16)
        self.assertFalse(tarea.visar_ruta_aviso)

    def test_no_avisa_con_el_filtro_apagado(self):
        self.env['ir.config_parameter'].sudo().set_param('visar.travel.enabled', '0')
        self.addCleanup(self.env['ir.config_parameter'].sudo().set_param,
                        'visar.travel.enabled', '1')
        self._parada(15)
        with self._matriz(40):
            tarea = self._tarea(hora=16)
        self.assertFalse(tarea.visar_ruta_aviso)

    def test_una_tarea_de_oficina_no_tiene_ruta(self):
        self.proyecto.is_fsm = False
        self.addCleanup(self.proyecto.write, {'is_fsm': True})
        self._parada(15)
        with self._matriz(40):
            tarea = self._tarea(hora=16)
        self.assertFalse(tarea.visar_ruta_aviso)

    def test_mapbox_caido_no_inventa_un_aviso(self):
        self._parada(15)
        with patch(_SERVICE + '._visar_mapbox_matrix',
                   lambda _self, coords, depart_at=None: None):
            tarea = self._tarea(hora=16)
        self.assertFalse(tarea.visar_ruta_aviso)

    def test_renombrar_la_tarea_no_gasta_una_llamada(self):
        """Solo el técnico, las fechas y el cliente cambian la respuesta."""
        self._parada(15)
        with self._matriz(10):
            tarea = self._tarea(hora=16)
        llamadas = []
        with patch(_SERVICE + '._visar_mapbox_matrix',
                   lambda _self, coords, depart_at=None: llamadas.append(1)):
            tarea.write({'name': 'Otro nombre'})
        self.assertFalse(llamadas)
