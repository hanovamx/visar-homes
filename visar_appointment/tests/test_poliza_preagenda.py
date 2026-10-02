# -*- coding: utf-8 -*-
"""La pre-agenda de visitas de póliza (paso 2, 1-oct-2026).

Lo que se fija aquí, en orden de qué duele más si se rompe:

* **La cita se crea CALLADA.** Ni una invitación, ni un recordatorio, ni un
  mensaje en el chatter del evento. El cliente no ha confirmado nada todavía, y
  mandarle una invitación a una cita que no pidió es peor que el problema que
  esta pieza resuelve. Dos trampas concretas quedan clavadas aquí porque las dos
  se descubrieron midiendo y ninguna es evidente leyendo el código:
  **`alarm_ids` hay que pasarlo VACÍO explícitamente** (omitirlo es lo que
  dispara `_compute_alarm_ids`), y **`mail_create_nolog` no basta** sin
  `tracking_disable` (`appointment_status` se asienta en un `write` posterior).

* **La cita OCUPA la agenda.** Es la razón de ser de toda la fase: antes de esto
  una visita de póliza «agendada» era una fecha en la tarea, sin cita y sin
  líneas de reserva, así que la web podía vender el mismo hueco a otro cliente.

* **No se sobrevende NUNCA.** Sin capacidad libre la visita se queda sin fecha,
  con actividad y nota. Es la excepción documentada a «nunca se deja sin
  pre-agendar» que el usuario aprobó el 1-oct-2026, y sin esta prueba un
  refactor futuro podría «arreglar» el caso sin hueco justo sobrevendiendo.

* **La serie no deriva**: el ancla de la siguiente visita es la fecha PROPUESTA,
  no la real, así que mover una visita una semana no arrastra el resto del
  contrato.

* **El bloqueo del reagendado depende de su premisa**, no de un literal: una
  visita con cita propia y confirmada se puede mover; sin confirmar, no.
"""
import pytz
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPolizaPreagenda(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env['project.task']
        cls.maestro = cls.env['appointment.type'].sudo(
        )._visar_get_master_appointment_type()
        # **La zona se saca de un CP del catálogo, no de `search([], limit=1)`.**
        # Es lo que hacía fallar 18 de estas pruebas: una visita de póliza resuelve
        # su zona por la cita anterior (que aquí no existe) y, si no, por el CP del
        # domicilio. Con un cliente sin `zip`, `_visar_preagendar` se plantaba —
        # correctamente — en «no se pudo determinar la zona», y no llegaba a crear
        # nada. El CP de verdad es lo que hace que la cadena entera sea real.
        cp = cls.env['visar.zone.cp'].sudo().search(
            [('zone_id', '!=', False)], limit=1)
        cls.zona = cp.zone_id
        if not cls.maestro or not cls.zona:
            cls.skip_todo = True
            return
        cls.skip_todo = False

        cls.proyecto = cls.env['project.project'].create({
            'name': 'Campo (pre-agenda)', 'is_fsm': True,
            'company_id': cls.env.company.id})
        cls.cliente = cls.env['res.partner'].create({
            'name': 'Cliente de poliza (prueba)',
            'zip': cp.name,
            'partner_latitude': 25.67, 'partner_longitude': -100.33})

        empleado = cls.env['hr.employee'].create({'name': 'Tecnico pre-agenda'})
        cls.recurso = cls.env['appointment.resource'].create({
            'name': 'Tecnico pre-agenda',
            'visar_employee_id': empleado.id,
            'capacity': 1,
            'visar_zone_ids': [(6, 0, cls.zona.ids)],
            'visar_service_ids': [(6, 0, cls.maestro.ids)],
        })
        cls.maestro.sudo().resource_ids = [(4, cls.recurso.id)]
        cls.tz = pytz.timezone(cls.maestro.appointment_tz or 'America/Monterrey')

    def setUp(self):
        super().setUp()
        if self.skip_todo:
            self.skipTest("la BD no tiene tipo de cita maestro o catalogo de CP")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _poliza(self, cliente=None):
        """Una póliza mínima pero REAL: lo que hace falta para que el cron la coja.

        **`subscription_state` en `3_progress`, y no es cosmético.** El cron filtra
        por ahí y no por `state`, porque una póliza dada de baja se queda en
        `state='sale'` con `subscription_state='6_churn'` (41 de 68 en `visar-test`)
        y mandarle un técnico a quien canceló es peor que no agendarle nada.

        Una póliza en borrador, que es lo que sale de un `create` pelado, el cron la
        ignora — con razón. Así que sin esta línea las tres pruebas del cron pasarían
        **por el motivo equivocado**: dirían «el cron no agendó la correctiva» cuando
        la verdad es que no agendó NADA. Un falso verde es peor que un fallo.
        """
        plan = self.env['sale.subscription.plan'].sudo().search(
            [('visar_visit_interval_months', '>', 0)], limit=1)
        if not plan:
            self.skipTest("la BD no tiene planes con intervalo de visitas")
        # Una línea con producto RECURRENTE, obligatoria: sin ella
        # `_constraint_subscription_plan` levanta «Please add a recurring product in
        # the subscription or remove the recurring plan» en cuanto se escribe el
        # estado. Es el mismo apaño que ya hace
        # `visar_whatsapp_agent/tests/test_agent_reschedule.py`.
        producto = self.env['product.product'].sudo().search(
            [('recurring_invoice', '=', True)], limit=1)
        if not producto:
            producto = self.env['product.product'].sudo().create({
                'name': 'Poliza de prueba (recurrente)',
                'type': 'service',
                'recurring_invoice': True,
            })
        orden = self.env['sale.order'].sudo().create({
            'partner_id': (cliente or self.cliente).id,
            'plan_id': plan.id,
            'order_line': [(0, 0, {'product_id': producto.id, 'product_uom_qty': 1})],
        })
        # Los dos campos en UNA escritura: la base tiene una restricción de
        # coherencia (`sale_order_sale_subscription_state_coherence_2`) que prohíbe
        # `3_progress` con la orden en borrador, y se comprueba al cerrar la
        # sentencia. Se escriben a mano en vez de confirmar la orden de verdad
        # porque `action_confirm` arrastra generación de tareas FSM y facturación,
        # que no es lo que estas pruebas miden.
        orden.write({'state': 'sale', 'subscription_state': '3_progress'})
        return orden

    def _cliente_en_zona_desierta(self):
        """Un cliente cuyo CP cae en una zona donde NADIE presta el servicio.

        Es la unica forma honesta de forzar «no hay capacidad». Quitarle la zona al
        recurso de la prueba no sirve: el CP del catalogo apunta a una zona real que
        tiene tecnicos de produccion, y la pre-agenda —correctamente— usaria uno de
        ellos. La primera version de estas pruebas fallaba por eso.
        """
        zona = self.env['visar.zone'].sudo().create({'name': 'Zona desierta (prueba)'})
        self.env['visar.zone.cp'].sudo().create({
            'name': '99999', 'zone_id': zona.id})
        return self.env['res.partner'].create({
            'name': 'Cliente en zona desierta (prueba)',
            'zip': '99999',
            'partner_latitude': 25.60, 'partner_longitude': -100.20})

    def _visita(self, orden, due_date, anterior_hora_utc=None, seq=2):
        """Una visita preventiva de la póliza, sin fecha, con propuesta."""
        vals = {
            'name': 'Visita poliza prueba %s' % seq,
            'project_id': self.proyecto.id,
            'partner_id': orden.partner_id.id,
            'visar_subscription_order_id': orden.id,
            'visar_visit_kind': 'preventiva',
            'visar_visit_seq': seq,
            'visar_visit_total': 12,
        }
        visita = self.Task.sudo().with_context(
            visar_poliza_due_sync=True, visar_sin_aviso_ruta=True).create(vals)
        visita.with_context(visar_poliza_due_sync=True).write(
            {'visar_visit_due_date': due_date})
        return visita

    def _anterior(self, orden, inicio_utc):
        """La visita YA hecha de la que se copia la hora y el técnico."""
        visita = self.Task.sudo().with_context(
            visar_poliza_due_sync=True, visar_sin_aviso_ruta=True).create({
                'name': 'Visita poliza prueba 1',
                'project_id': self.proyecto.id,
                'partner_id': orden.partner_id.id,
                'visar_subscription_order_id': orden.id,
                'visar_visit_kind': 'preventiva',
                'visar_visit_seq': 1,
                'visar_visit_total': 12,
                'planned_date_begin': inicio_utc,
                'date_deadline': inicio_utc + relativedelta(hours=1),
                'visar_technician_ids': [(6, 0, self.recurso.visar_employee_id.ids)],
            })
        return visita

    def _en_dias(self, dias, hora_local=15):
        """Un UTC que en hora local cae a `hora_local` dentro de `dias` días."""
        dia = (fields.Datetime.now() + relativedelta(days=dias)).date()
        local = self.tz.localize(
            fields.Datetime.to_datetime('%s %02d:00:00' % (dia, hora_local)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # El silencio — lo primero, porque es lo que no se puede deshacer
    # ------------------------------------------------------------------

    def test_la_cita_se_crea_SIN_alarma(self):
        """`alarm_ids` VACÍO y explícito, que es lo contrario de lo intuitivo.

        `appointment/models/calendar_event.py::_compute_alarm_ids` copia
        `appointment_type_id.reminder_ids` **cuando el campo viene vacío**, así que
        OMITIR `alarm_ids` es justo lo que engancha el recordatorio. Medido el
        1-oct-2026: sin la lista explícita la cita salía con 1 alarma pese a
        `dont_notify`.
        """
        self.maestro.sudo().reminder_ids = [
            (4, self.env['calendar.alarm'].sudo().search([], limit=1).id)]
        cita = self._preagendar_una()
        self.assertFalse(
            cita.alarm_ids,
            "la cita salio con recordatorio: el cliente va a recibir un aviso de "
            "una cita que todavia no ha confirmado")

    def test_la_cita_no_deja_NADA_en_su_chatter(self):
        """`mail_create_nolog` no basta: hace falta `tracking_disable`.

        `appointment_status` lleva `tracking=True` y se asienta en un `write`
        posterior al create (es un computed almacenado). Sin `tracking_disable` ahí
        aparecía un «Reserva confirmada» con cuerpo de correo al cliente — diciendo
        que confirmó algo que nadie le ha preguntado.
        """
        cita = self._preagendar_una()
        self.assertEqual(
            self.env['mail.message'].sudo().search_count(
                [('model', '=', 'calendar.event'), ('res_id', '=', cita.id)]),
            0,
            "algo publico en el chatter de la cita")

    def test_no_se_manda_ni_un_correo_al_cliente(self):
        antes = self.env['mail.mail'].sudo().search_count([])
        self._preagendar_una()
        nuevos = self.env['mail.mail'].sudo().search([], order='id desc', limit=5)
        al_cliente = nuevos.filtered(
            lambda m: m.model == 'calendar.event'
            or self.cliente.email and self.cliente.email in (m.email_to or ''))
        self.assertFalse(al_cliente, "salio correo hacia el cliente")
        self.assertLessEqual(
            self.env['mail.mail'].sudo().search_count([]) - antes, 1,
            "mas de un correo: solo deberia poder salir el de actividad a oficina")

    def test_no_se_crea_ninguna_oportunidad_de_CRM(self):
        """Con `lead_create` ENCENDIDO, que es como está producción.

        `appointment_crm` abre una oportunidad por cada cita cuyo tipo lo lleve, y
        en `visar-db` **los cuatro tipos lo llevan activo** — lo cual se descubrió
        desplegando, no leyendo el código. La primera versión de esta prueba lo daba
        por apagado (como en `visar-test`) y por eso pasaba en verde mientras el
        producto, en producción, habría creado 14 oportunidades en la primera
        corrida del cron.

        Así que aquí se **enciende a propósito**: es la única forma de que la prueba
        valga para la base donde importa.
        """
        self.maestro.sudo().lead_create = True
        antes = self.env['crm.lead'].sudo().with_context(
            active_test=False).search_count([])
        _visita, cita = self._preagendar_una(devolver_visita=True)
        self.assertEqual(
            self.env['crm.lead'].sudo().with_context(
                active_test=False).search_count([]), antes,
            "se abrio una oportunidad de CRM: el embudo se llena de clientes que ya "
            "son clientes y que no han pedido nada")
        self.assertFalse(
            cita.opportunity_id,
            "la cita quedo enganchada a una oportunidad")
        self.assertFalse(
            cita.res_model_id or cita.res_id,
            "`_link_with_lead` escribio res_model/res_id en la cita, y eso es lo "
            "que genera la mail.activity automatica")
        self.assertEqual(
            self.env['mail.message'].sudo().search_count(
                [('model', '=', 'calendar.event'), ('res_id', '=', cita.id)]),
            0,
            "algo publico en el chatter de la cita. Si es el «Meeting linked to "
            "Lead/Opportunity», ojo: `tracking_disable` NO lo para, porque es un "
            "_message_log explicito y no seguimiento de campo")

    def test_una_cita_de_tipo_AJENO_sigue_abriendo_su_oportunidad(self):
        """Lo que queda del miedo original, y sigue siendo lo que importa.

        Esta prueba nació (1-oct-2026) asertando que una cita normal abría su
        oportunidad, para que nadie silenciara la pre-agenda por la vía fácil de
        apagar `lead_create` en el tipo de cita. **El 2-oct cambió el destino de
        esa oportunidad en los tipos de Visar, no el flag:** `visar_crm` suprime
        la que creaba el core —nacía sin teléfono normalizado ni grupo, y por eso
        ningún automatismo la encontraba— y abre la ficha buena desde la ORDEN.

        Lo que NO puede cambiar es el camino de cualquier otro tipo de cita: los
        dos guardias (el contexto de la pre-agenda y el filtro por tipo de
        `visar_crm`) tienen que dejar pasar el `super()`. Si alguien los
        convierte en un `return` seco, lo que falla es esto.
        """
        ajeno = self.env['appointment.type'].sudo().create({
            'name': "Cita ajena a Visar (prueba)",
            'lead_create': True,
        })
        antes = self.env['crm.lead'].sudo().with_context(
            active_test=False).search_count([])
        self.env['calendar.event'].sudo().create({
            'name': 'Cita de un tipo que no es de Visar (prueba)',
            'start': self._en_dias(4, hora_local=12),
            'stop': self._en_dias(4, hora_local=12) + relativedelta(hours=1),
            'allday': False,
            'appointment_type_id': ajeno.id,
            'partner_ids': [(6, 0, self.cliente.ids)],
        })
        self.assertGreater(
            self.env['crm.lead'].sudo().with_context(
                active_test=False).search_count([]), antes,
            "una cita de un tipo ajeno dejó de abrir su oportunidad: algún "
            "guardia se comió el super() en vez de filtrar")

    def test_una_cita_de_tipo_VISAR_ya_no_abre_el_lead_pobre_del_core(self):
        """El cambio del 2-oct-2026, y por qué no se pierde nada.

        El lead que `appointment_crm` creaba traía solo
        `{name, partner_id, type, user_id, description}`: sin
        `visar_wa_phone_norm` y sin `visar_service_group_id`, que son justo la
        pareja por la que buscan los dos automatismos de avance. Medido en
        producción: **18 fichas** que ningún automatismo podía encontrar, y que
        oficina movía a mano. Peor, el equipo salía del cómputo sobre `user_id`,
        o sea **del técnico que quedó como organizador**.

        La oportunidad no desaparece: la abre `visar_crm` desde la orden, con
        identidad completa, y la cita queda enlazada a ella en
        `_make_event_from_paid_booking`. Lo que sí deja de abrir ficha es una
        cita capturada A MANO en el backend sin orden detrás — que es
        exactamente el camino que dejó los 16 leads de «Administrator - reserva
        Valoración técnica» del 1 y 2 de octubre.
        """
        if not self.env.ref('visar_crm.crm_team_whatsapp',
                            raise_if_not_found=False):
            self.skipTest("visar_crm no está instalado: el core sigue mandando")
        self.maestro.sudo().lead_create = True
        antes = self.env['crm.lead'].sudo().with_context(
            active_test=False).search_count([])
        cita = self.env['calendar.event'].sudo().create({
            'name': 'Cita capturada a mano (prueba)',
            'start': self._en_dias(4, hora_local=12),
            'stop': self._en_dias(4, hora_local=12) + relativedelta(hours=1),
            'allday': False,
            'appointment_type_id': self.maestro.id,
            'partner_ids': [(6, 0, self.cliente.ids)],
        })
        self.assertEqual(
            self.env['crm.lead'].sudo().with_context(
                active_test=False).search_count([]), antes,
            "volvió el lead pobre del core: sin teléfono ni grupo, ningún "
            "automatismo lo va a encontrar")
        self.assertFalse(
            cita.res_id,
            "`_link_with_lead` escribió res_model/res_id, que es lo que genera "
            "la actividad automática que no queremos")

    def test_el_camino_PAGADO_sigue_avisando(self):
        """El seguro contra «arreglar» el silencio apagándolo para todos.

        El silencio de la pre-agenda vive en un **contexto**, a propósito. La
        tentación obvia era el `ir.config_parameter` `calendar.block_mail` o quitarle
        los `reminder_ids` al tipo de cita — las dos apagarían también los avisos de
        las citas que el cliente **sí** pidió y pagó, y nadie se enteraría hasta que
        un cliente no se presentara por no haber recibido su recordatorio.

        Así que esta prueba no mira la pre-agenda: mira que una cita creada por el
        camino normal **siga** recibiendo su recordatorio. Si alguien apaga el aviso
        por la vía global, lo que falla es esto.
        """
        alarma = self.env['calendar.alarm'].sudo().search([], limit=1)
        if not alarma:
            self.skipTest("la BD no tiene ninguna alarma de calendario")
        self.maestro.sudo().reminder_ids = [(4, alarma.id)]

        normal = self.env['calendar.event'].sudo().create({
            'name': 'Cita pagada (prueba)',
            'start': self._en_dias(3, hora_local=11),
            'stop': self._en_dias(3, hora_local=11) + relativedelta(hours=1),
            'allday': False,
            'appointment_type_id': self.maestro.id,
            'partner_ids': [(6, 0, self.cliente.ids)],
        })
        self.assertTrue(
            normal.alarm_ids,
            "una cita del camino normal se quedo SIN recordatorio: alguien apago el "
            "aviso por la via global (calendar.block_mail o reminder_ids) en vez de "
            "por el contexto de la pre-agenda, y ahora los clientes que SI pidieron "
            "su cita tampoco reciben el suyo")

    # ------------------------------------------------------------------
    # Lo que la cita tiene que conseguir
    # ------------------------------------------------------------------

    def test_la_cita_OCUPA_la_agenda_del_tecnico(self):
        """La razón de ser de toda la fase.

        Antes de esto «agendar» una visita de póliza era escribir una fecha en la
        tarea: sin cita y sin líneas de reserva, así que no consumía capacidad y la
        web podía vender ese mismo hueco a otro cliente.
        """
        cita = self._preagendar_una()
        self.assertTrue(cita.booking_line_ids,
                        "sin linea de reserva no consume capacidad de nadie")
        usado = cita.appointment_resource_ids[:1]
        self.assertTrue(usado, "la cita no tiene recurso")
        self.assertFalse(
            self.env['appointment.type'].sudo()._visar_resource_free_at(
                self.maestro, usado, cita.start, cita.stop, 1),
            "el hueco sigue libre: la cita no esta ocupando nada")

    def test_la_cita_lleva_al_cliente_para_tener_COORDENADAS(self):
        """Sin partner la parada no tiene coordenadas y el día se lee vacío.

        `_visar_travel_stop_partners` llega al domicilio por `sale_order_line_ids`
        → `visar_fsm_task_ids` → `partner_ids`, y la primera vía no existe en una
        póliza: su cita no la originó ninguna línea.
        """
        cita = self._preagendar_una()
        self.assertIn(self.cliente, cita.partner_ids)

    def test_es_parada_del_dia_para_el_motor_de_rutas(self):
        cita = self._preagendar_una()
        usado = cita.appointment_resource_ids[:1]
        paradas = self.env['appointment.type'].sudo()._visar_travel_stops_by_day(
            usado, self.tz)
        dia = pytz.utc.localize(cita.start).astimezone(self.tz).date()
        franjas = paradas.get((usado.id, dia)) or []
        self.assertTrue([f for f in franjas if f[0] == cita.start],
                        "el motor de rutas no ve la cita como parada del dia")

    def test_la_tarea_queda_enlazada_y_sincronizada(self):
        visita, cita = self._preagendar_una(devolver_visita=True)
        self.assertEqual(visita.visar_visit_event_id, cita)
        self.assertEqual(visita.planned_date_begin, cita.start)
        self.assertTrue(visita.date_deadline,
                        "Odoo DESCARTA planned_date_begin si se escribe sin "
                        "date_deadline, y lo hace en silencio")
        self.assertEqual(visita._visar_ruta_cita(), cita,
                         "_visar_ruta_cita no encuentra la cita por el campo nuevo")

    def test_la_cita_es_del_cliente_de_la_poliza(self):
        """Sin esto el cliente recibe «no encontre esa cita a tu nombre».

        La cita de una pre-agenda no la originó ninguna línea de orden, así que la
        búsqueda normal de `_visar_appointment_partners` devuelve vacío para ella.
        """
        _visita, cita = self._preagendar_una(devolver_visita=True)
        self.assertIn(self.cliente, cita._visar_appointment_partners())

    # ------------------------------------------------------------------
    # La misma hora, el mismo técnico
    # ------------------------------------------------------------------

    def test_respeta_la_hora_de_la_visita_anterior(self):
        orden = self._poliza()
        self._anterior(orden, self._en_dias(-30, hora_local=15))
        visita = self._visita(
            orden, (fields.Datetime.now() + relativedelta(days=5)).date())
        cita = visita._visar_preagendar()
        self.assertTrue(cita, "no se pre-agendo nada")
        local = pytz.utc.localize(cita.start).astimezone(self.tz)
        self.assertEqual(local.hour, 15,
                         "no respeto la hora de siempre del cliente")

    def test_prefiere_al_MISMO_tecnico(self):
        orden = self._poliza()
        self._anterior(orden, self._en_dias(-30, hora_local=15))
        visita = self._visita(
            orden, (fields.Datetime.now() + relativedelta(days=5)).date())
        cita = visita._visar_preagendar()
        self.assertIn(self.recurso, cita.appointment_resource_ids)

    # ------------------------------------------------------------------
    # EL LÍMITE DURO: no sobrevender
    # ------------------------------------------------------------------

    def test_sin_capacidad_no_sobrevende(self):
        """La excepción documentada a «nunca se deja sin pre-agendar».

        Aprobada por el usuario el 1-oct-2026 con la condición de que quedara bien
        documentada. **Si esta prueba desaparece, lo que vuelve es la sobreventa:**
        alguien «arreglando» el caso sin hueco le escribiría una hora a un técnico
        ya ocupado, y entonces dos clientes esperarían al mismo técnico a la vez.

        Se fuerza el caso quitándole al técnico la zona: sin recurso elegible no hay
        capacidad posible, que es la versión más limpia de «no hay hueco».
        """
        orden = self._poliza(cliente=self._cliente_en_zona_desierta())
        visita = self._visita(
            orden, (fields.Datetime.now() + relativedelta(days=5)).date())

        cita = visita._visar_preagendar()

        self.assertFalse(cita, "creo una cita sin capacidad para ella")
        self.assertFalse(visita.planned_date_begin,
                         "le puso fecha sin tener donde ponerla")
        self.assertFalse(visita.visar_visit_event_id)
        actividad = self.env['mail.activity'].sudo().search([
            ('res_model', '=', 'project.task'), ('res_id', '=', visita.id)])
        self.assertTrue(actividad, "nadie en oficina se va a enterar")
        self.assertTrue(actividad.user_id,
                        "una actividad sin responsable no aparece en ninguna bandeja")
        self.assertIn('zona', (actividad.note or '').lower(),
                      "la actividad no dice en que ZONA falto capacidad, y sin eso "
                      "no es accionable")
        self.assertTrue(
            self.env['mail.message'].sudo().search_count([
                ('model', '=', 'sale.order'), ('res_id', '=', orden.id)]),
            "no quedo rastro en el expediente de la poliza")

    def test_el_aviso_de_sin_hueco_no_se_repite(self):
        """El cron corre a diario: sin idempotencia entierra la bandeja de oficina."""
        orden = self._poliza(cliente=self._cliente_en_zona_desierta())
        visita = self._visita(
            orden, (fields.Datetime.now() + relativedelta(days=5)).date())
        # Delta, no conteo absoluto: el pedido ya trae mensajes propios (el
        # seguimiento de `subscription_state` deja uno al confirmarlo).
        notas_antes = self.env['mail.message'].sudo().search_count([
            ('model', '=', 'sale.order'), ('res_id', '=', orden.id)])
        visita._visar_preagendar()
        visita._visar_preagendar()
        visita._visar_preagendar()
        self.assertEqual(
            self.env['mail.activity'].sudo().search_count([
                ('res_model', '=', 'project.task'), ('res_id', '=', visita.id)]),
            1, "una actividad por corrida: la bandeja de oficina se vuelve ruido")
        self.assertEqual(
            self.env['mail.message'].sudo().search_count([
                ('model', '=', 'sale.order'), ('res_id', '=', orden.id)]) - notas_antes,
            1,
            "una nota por corrida en el expediente de la poliza: a las tres semanas "
            "no se puede leer nada mas ahi")

    # ------------------------------------------------------------------
    # Idempotencia y el cron
    # ------------------------------------------------------------------

    def test_no_se_pre_agenda_dos_veces(self):
        visita, cita = self._preagendar_una(devolver_visita=True)
        self.assertFalse(visita._visar_preagendar(),
                         "la volvio a pre-agendar teniendo ya cita")
        self.assertEqual(visita.visar_visit_event_id, cita)

    def test_el_cron_solo_coge_PREVENTIVAS(self):
        """Correctivas y garantías las sigue agendando oficina (decisión del usuario)."""
        orden = self._poliza()
        self._anterior(orden, self._en_dias(-30, hora_local=15))
        refuerzo = self._visita(
            orden, (fields.Datetime.now() + relativedelta(days=5)).date(), seq=0)
        refuerzo.with_context(visar_poliza_due_sync=True).write(
            {'visar_visit_kind': 'correctiva'})
        self.Task.sudo()._visar_cron_preagenda()
        refuerzo.invalidate_recordset()
        self.assertFalse(refuerzo.planned_date_begin,
                         "el cron agendo una correctiva")

    def test_el_cron_ignora_lo_que_esta_fuera_de_vigencia(self):
        orden = self._poliza()
        visita = self._visita(
            orden, (fields.Datetime.now() + relativedelta(days=5)).date())
        visita.with_context(visar_poliza_due_sync=True).write(
            {'visar_visit_due_out_of_term': True})
        self.Task.sudo()._visar_cron_preagenda()
        visita.invalidate_recordset()
        self.assertFalse(visita.planned_date_begin)

    def test_el_cron_no_mira_mas_alla_del_horizonte(self):
        """Más allá del horizonte el nativo no genera huecos que pedir."""
        orden = self._poliza()
        self._anterior(orden, self._en_dias(-30, hora_local=15))
        lejana = self._visita(
            orden, (fields.Datetime.now() + relativedelta(days=300)).date())
        self.Task.sudo()._visar_cron_preagenda()
        lejana.invalidate_recordset()
        self.assertFalse(lejana.planned_date_begin,
                         "pre-agendo una visita de dentro de diez meses")

    # ------------------------------------------------------------------
    # La serie no deriva
    # ------------------------------------------------------------------

    def test_la_propuesta_SOBREVIVE_a_la_preagenda(self):
        """Es el ancla de la siguiente visita: si se borra, el calendario deriva."""
        visita, _cita = self._preagendar_una(devolver_visita=True)
        self.assertTrue(
            visita.visar_visit_due_date,
            "se borro la fecha propuesta al agendar, y con ella el ancla que "
            "impide que la serie se corra contrato adelante")

    def test_la_siguiente_cuelga_de_la_PROPUESTA_no_de_la_fecha_real(self):
        """Decisión del usuario: la serie no se mueve porque una visita se corra.

        Si al cliente se le pre-agenda el día 8 porque el 1 no había hueco, la
        siguiente sigue tocando el 1 del mes que viene, no el 8.
        """
        orden = self._poliza()
        self._anterior(orden, self._en_dias(-30, hora_local=15))
        propuesta = (fields.Datetime.now() + relativedelta(days=5)).date()
        visita = self._visita(orden, propuesta)
        siguiente = self._visita(orden, propuesta + relativedelta(months=1), seq=3)
        antes = siguiente.visar_visit_due_date

        visita._visar_preagendar()
        orden._visar_schedule_visit_due_dates()
        siguiente.invalidate_recordset()

        self.assertEqual(
            siguiente.visar_visit_due_date, antes,
            "la siguiente visita se movio: la serie esta derivando")

    # ------------------------------------------------------------------
    # El bloqueo del reagendado, por su premisa
    # ------------------------------------------------------------------

    def test_sin_confirmar_el_cliente_NO_la_puede_mover(self):
        """La puso un robot y el cliente no sabe que existe.

        Que la pueda mover quien no sabe que la tiene no significa nada, y en
        cambio deja que un tercero con el mismo teléfono la mueva antes de que el
        dueño se entere.
        """
        _visita, cita = self._preagendar_una(devolver_visita=True)
        self.assertEqual(cita._visar_reschedule_blocked(), 'poliza')

    def test_confirmada_SI_se_puede_mover(self):
        """La premisa del bloqueo era «no tienen cita que mover». Ya la tienen."""
        visita, cita = self._preagendar_una(devolver_visita=True)
        visita.with_context(visar_poliza_due_sync=True).write(
            {'visar_visit_preagenda_confirmada': True})
        cita.invalidate_recordset()
        self.assertNotEqual(
            cita._visar_reschedule_blocked(), 'poliza',
            "sigue bloqueada tras confirmarsela al cliente")

    # ------------------------------------------------------------------
    def _preagendar_una(self, devolver_visita=False):
        orden = self._poliza()
        self._anterior(orden, self._en_dias(-30, hora_local=15))
        visita = self._visita(
            orden, (fields.Datetime.now() + relativedelta(days=5)).date())
        cita = visita._visar_preagendar()
        self.assertTrue(cita, "no se pudo pre-agendar: el resto de la prueba no aplica")
        return (visita, cita) if devolver_visita else cita
