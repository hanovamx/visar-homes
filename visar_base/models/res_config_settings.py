# -*- coding: utf-8 -*-
"""Ajustes globales de Visar que hoy solo existían como parámetros del sistema.

Los cuatro valores de aquí abajo ya gobernaban producción; lo único que faltaba
era una pantalla. Estaban en *Ajustes técnicos → Parámetros del sistema*, que es
donde se ponen las cosas que nadie debe tocar — y estas sí hay que tocarlas: son
decisiones de negocio (¿cuánto tiempo aguanto un horario sin cobrar?, ¿cuánto
traslado le presupuesto al técnico?) que hoy exigen un desarrollador.

**Por qué aquí y no en el módulo del agente de WhatsApp.** El apartado y el
traslado NO son del chat: el asistente web de agendado usa exactamente los mismos
dos números. Colgarlos del menú "Agente WhatsApp" diría que cambiarlos solo
afecta al chat, y no es verdad. Lo que sí es del agente —las ventanas de
recontacto— vive en su módulo.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Cotas de cordura. No son gustos: fuera de ellas el sistema se comporta mal de
# formas que el usuario no puede ver desde esta pantalla.
MIN_HOLD_MINUTES = 1
MAX_HOLD_MINUTES = 120
MAX_TRAVEL_MINUTES = 120
# Cuánto MÁS ancho es el radio del día que el presupuesto entre paradas. El
# umbral de agrupación NO se guarda suelto: se deriva de `visar.travel.minutes`
# más este margen, porque los dos números miden lo mismo (minutos de coche) para
# fines distintos y guardarlos por separado es invitarlos a divergir. Confirmado
# con Visar el 4-sep-2026: 20 + 10 = 30.
DEFAULT_CLUSTER_MARGIN = 10


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    visar_combo_corte_factor = fields.Float(
        string="Factor combo (legacy)",
        config_parameter='visar.combo_corte_factor',
        default=0.5,
        help="Respaldo si no hay reglas de combo activas. Precio = list_price × factor.")
    visar_valuation_product_tmpl_id = fields.Many2one(
        'product.template',
        string="Producto valoración técnica",
        domain="[('visar_is_valuation', '=', True)]",
        config_parameter='visar.valuation_product_tmpl_id',
        help="Producto usado en el flujo de valoración técnica.")

    # ------------------------------------------------------------------
    # Agendado
    # ------------------------------------------------------------------

    # Lo que caduca es el APARTADO, no la liga. Decisión de agosto 2026: mientras
    # nadie más haya tomado el lugar, la liga que ya se envió sigue cobrando y el
    # cliente recibe "se acabó el apartado, pero el horario sigue ahí". Cobrar y
    # descubrir después que no hay cita es el peor final posible, y matar una liga
    # buena para llegar a él sería elegirlo a propósito.
    visar_slot_hold_minutes = fields.Integer(
        string="Minutos de apartado",
        config_parameter='visar.slot_hold_minutes',
        default=10,
        help="Cuánto tiempo se le guarda el horario al cliente mientras paga. Al "
             "vencer, el horario vuelve al inventario y se le avisa por WhatsApp. "
             "La liga de pago NO se cancela: si al cliente le sigue interesando y "
             "nadie tomó el lugar, pagarla confirma la cita igual.")

    # Presupuesto ENTRE paradas, no radio de servicio: son estos minutos MÁS el
    # hueco que ya exista antes del horario candidato. Un trayecto de 40 min es
    # perfectamente ofrecible si el técnico tiene la mañana libre por delante; lo
    # que no se puede es comerse el traslado de la cita siguiente.
    visar_travel_minutes = fields.Integer(
        string="Minutos de traslado entre servicios",
        config_parameter='visar.travel.minutes',
        default=20,
        help="Traslado que se le presupuesta al técnico entre una parada y la "
             "siguiente. No es un radio máximo: a este presupuesto se le suma el "
             "hueco que haya antes del horario. Los horarios a los que no le da "
             "tiempo de llegar simplemente no se le ofrecen al cliente.")

    # El radio del DIA, que es otra pregunta que el presupuesto de arriba. Aquél
    # dice si al técnico le da tiempo de llegar desde la parada de al lado; éste,
    # si vale la pena vender ese día — un servicio a las 9:00 en San Nicolás y
    # otro a las 12:00 en García caben de sobra en el presupuesto y aun así son
    # una mañana entera en la carretera. Ver §5.7 del diseño 33.
    visar_travel_cluster_minutes = fields.Integer(
        string="Radio de agrupación del día",
        config_parameter='visar.travel.cluster_minutes',
        default=lambda self: self._visar_default_cluster_minutes(),
        help="Qué tan lejos pueden quedar entre sí los servicios de un mismo "
             "día. A diferencia del traslado entre servicios, este NO suma el "
             "hueco: si el técnico ya tiene trabajo ese día, el domicilio nuevo "
             "tiene que estar dentro de este radio de TODAS sus paradas. Un día "
             "sin trabajo acepta cualquier zona, y al reservarlo queda tomado "
             "por ella. Vacío = se deriva del traslado entre servicios + 10.")

    @api.model
    def _visar_default_cluster_minutes(self):
        """El default DERIVADO del presupuesto entre paradas.

        No es `default=30` a secas a propósito: horneado ahí, el 30 se
        desengancha de `visar.travel.minutes` y el día que alguien suba el
        presupuesto a 25 el radio del día se queda en 30 sin que nadie se entere.
        """
        return self.env['appointment.type'].sudo()._visar_travel_minutes() \
            + DEFAULT_CLUSTER_MARGIN

    # Las DOS puntas: para poder mover una cita tienen que faltar al menos estas
    # horas, y el horario nuevo tiene que estar igual de lejos. Un cambio de
    # última hora desordena la ruta del técnico se pida desde donde se pida.
    visar_reschedule_min_hours = fields.Integer(
        string="Antelación mínima para reagendar",
        config_parameter='visar.reschedule.min_hours',
        default=24,
        help="Horas que tienen que faltar para la cita actual —y para el horario "
             "nuevo— para que el cliente pueda moverla él mismo desde WhatsApp. "
             "Por debajo de eso, el cambio lo hace un asesor.")

    # Sin tope, una cita rebota por la agenda comiéndose huecos que otros
    # clientes habrían usado. Al llegar al límite hace falta una persona, que es
    # quien puede juzgar si el motivo lo merece.
    visar_reschedule_max_times = fields.Integer(
        string="Cambios permitidos por cita",
        config_parameter='visar.reschedule.max_times',
        default=2,
        help="Cuántas veces puede el cliente mover la MISMA cita por su cuenta. "
             "Agotados, se le pasa a un asesor. Cancelar nunca está disponible: "
             "el servicio ya está cobrado.")

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    @api.constrains('visar_slot_hold_minutes', 'visar_travel_minutes',
                    'visar_travel_cluster_minutes')
    def _check_visar_agendado(self):
        """Rechaza los valores que romperían el agendado en silencio.

        Un apartado de 0 minutos vence antes de que el cliente abra la liga; uno
        de 8 horas bloquea la agenda con gente que ya se fue. Un traslado mayor
        que el bloque de una hora deja el presupuesto por encima del servicio y
        vacía el calendario sin decir por qué.
        """
        for record in self:
            minutos = record.visar_slot_hold_minutes
            if minutos is not None and not (MIN_HOLD_MINUTES <= minutos <= MAX_HOLD_MINUTES):
                raise ValidationError(
                    "El apartado tiene que estar entre %s y %s minutos. Con menos "
                    "el horario se suelta antes de que al cliente le dé tiempo de "
                    "pagar; con más se bloquea la agenda con clientes que ya se "
                    "fueron." % (MIN_HOLD_MINUTES, MAX_HOLD_MINUTES))
            traslado = record.visar_travel_minutes
            if traslado is not None and not (0 <= traslado <= MAX_TRAVEL_MINUTES):
                raise ValidationError(
                    "El traslado tiene que estar entre 0 y %s minutos. Un "
                    "presupuesto mayor que el bloque de servicio deja al "
                    "calendario sin horarios que ofrecer."
                    % MAX_TRAVEL_MINUTES)
            radio = record.visar_travel_cluster_minutes
            if radio is not None and radio and not (0 <= radio <= MAX_TRAVEL_MINUTES):
                raise ValidationError(
                    "El radio de agrupación tiene que estar entre 0 y %s "
                    "minutos." % MAX_TRAVEL_MINUTES)
            if radio and traslado and radio < traslado:
                raise ValidationError(
                    "El radio de agrupación del día (%s min) no puede ser menor "
                    "que el traslado entre servicios (%s min). El radio no suma "
                    "huecos, así que por debajo del presupuesto rechazaría "
                    "horarios que el técnico sí alcanza, y el presupuesto "
                    "dejaría de decidir nada." % (radio, traslado))

    @api.constrains('visar_reschedule_min_hours', 'visar_reschedule_max_times')
    def _check_visar_reagenda(self):
        """Los dos números que deciden quién puede mover una cita.

        El tope de 168 h (una semana) no es un gusto: por encima, la antelación
        exigida supera el horizonte de días que el agente llega a ofrecer, y el
        cliente vería "no puedes moverla" para toda cita que exista.
        """
        for record in self:
            horas = record.visar_reschedule_min_hours
            if horas is not None and not (0 <= horas <= 168):
                raise ValidationError(
                    "La antelación para reagendar tiene que estar entre 0 y 168 "
                    "horas (una semana). Más allá, ninguna cita sería movible.")
            veces = record.visar_reschedule_max_times
            if veces is not None and not (0 <= veces <= 10):
                raise ValidationError(
                    "Los cambios permitidos por cita tienen que estar entre 0 y "
                    "10. Con 0, nadie puede reagendar desde el chat.")
