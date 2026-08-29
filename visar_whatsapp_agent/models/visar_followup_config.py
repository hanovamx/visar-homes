# -*- coding: utf-8 -*-
"""Cuándo el agente vuelve a escribirle a un lead que se enfrió.

El cliente pregunta, cotiza, a veces hasta llega a elegir horario — y se va. Hoy
no pasa nada: el lead se queda en *Nuevo* hasta que el cron de caducidad lo marca
perdido días después, sin que nadie le haya escrito ni una vez. Este modelo es
**cuándo** se le escribe. El **qué** lo redacta el runtime con el modelo, porque
un texto igual para todos no es un recontacto, es una campaña.

## Las dos ventanas, y por qué son dos cosas distintas

* **La espera** (`delay_minutes`, 6 h): cuánto se deja enfriar al cliente antes de
  volver. Demasiado poco es acoso; demasiado y ya compró en otro lado.
* **El horario hábil** (`window_start_hour` – `window_end_hour`): a qué horas es
  aceptable que suene un WhatsApp. Si la espera vence a las 2 de la mañana, se
  guarda para las 6.

## Por qué la validación es `espera < ventana`, y no un número inventado

Meta solo entrega mensajes **libres** —los que no son plantilla aprobada— si el
cliente escribió en las últimas **24 h**. Pasado eso el mensaje no rebota: se
descarta, sin error que el agente pueda ver. Un recontacto configurado a 20 h de
espera *parecería* funcionar y no llegaría nunca.

El peor caso es la espera que vence justo cuando el horario hábil acaba de
cerrar: hay que aguantar toda la noche. Con `V` = largo de la ventana:

    peor caso = espera + (24 − V)

Y exigir que eso quepa en 24 h se reduce a **`espera < V`**: el recontacto tiene
que caber dentro de un día hábil. Con los valores de fábrica (6 h de espera,
ventana de 6:00 a 18:00) el peor caso son 18 h, con 6 h de margen.

Se valida al **guardar**, no al enviar: una configuración que no puede funcionar
no debe poder guardarse. Ver §17 de `.context/85-motor-de-flujos-agendado.md`.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Margen contra la ventana de 24 h de Meta. El peor caso teórico se calcula
# exacto, pero entre que el cron corre cada N minutos y que el envío tarda, un
# recontacto que cuadra "justo" en 24:00 llega tarde. Una hora de colchón.
MARGEN_HORAS = 1.0
VENTANA_META_HORAS = 24.0


class VisarFollowupConfig(models.Model):
    _name = 'visar.followup.config'
    _description = "Recontacto de leads por WhatsApp (ventanas)"
    _order = 'sequence, id'

    name = fields.Char(
        string="Nombre", required=True, default="Recontacto de leads")
    active = fields.Boolean(string="Activo", default=True)
    sequence = fields.Integer(string="Secuencia", default=10)

    enabled = fields.Boolean(
        string="Recontactar leads fríos", default=True,
        help="Apagado, no se programa ni se envía ningún recontacto. Los leads "
             "que ya estuvieran programados se quedan sin enviar.")

    delay_minutes = fields.Integer(
        string="Esperar (minutos)", required=True, default=6 * 60,
        help="Cuánto se espera desde el último mensaje del cliente antes de "
             "volver a escribirle. 360 = 6 horas.")

    window_start_hour = fields.Integer(
        string="No escribir antes de", required=True, default=6,
        help="Hora local (0-23) a partir de la cual es aceptable escribir.")
    window_end_hour = fields.Integer(
        string="No escribir después de", required=True, default=18,
        help="Hora local (1-24) a partir de la cual ya no se escribe. Si la "
             "espera vence pasada esta hora, el recontacto se guarda para el "
             "día siguiente.")

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    @api.model
    def _visar_active(self):
        """La configuración vigente, o un registro vacío si no hay ninguna.

        Mismo criterio que `visar.llm.config`: la primera por secuencia. Sin
        registro no se recontacta a nadie — que es lo correcto: la ausencia de
        configuración no puede significar "manda lo que quieras cuando sea".
        """
        return self.search([], order='sequence, id', limit=1)

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    @api.constrains('delay_minutes', 'window_start_hour', 'window_end_hour')
    def _check_ventanas(self):
        for record in self:
            inicio, fin = record.window_start_hour, record.window_end_hour
            if not (0 <= inicio <= 23):
                raise ValidationError(
                    "La hora de inicio tiene que estar entre 0 y 23.")
            if not (1 <= fin <= 24):
                raise ValidationError(
                    "La hora de fin tiene que estar entre 1 y 24.")
            if inicio >= fin:
                raise ValidationError(
                    "El horario hábil tiene que empezar antes de terminar. Una "
                    "ventana que cruza la medianoche (por ejemplo 22:00 a 06:00) "
                    "no está soportada.")
            if record.delay_minutes < 1:
                raise ValidationError(
                    "La espera tiene que ser de al menos un minuto. Contestarle "
                    "al cliente en el mismo instante en que dejó de escribir no "
                    "es un recontacto.")

            ventana_horas = float(fin - inicio)
            espera_horas = record.delay_minutes / 60.0
            # peor caso = la espera vence justo al cerrar el horario hábil.
            peor_caso = espera_horas + (24.0 - ventana_horas)
            if peor_caso > VENTANA_META_HORAS - MARGEN_HORAS:
                raise ValidationError(
                    "Con esta combinación el recontacto puede tardar hasta %.1f h "
                    "en salir, y WhatsApp solo entrega mensajes libres dentro de "
                    "las 24 h siguientes al último mensaje del cliente: pasado "
                    "ese punto Meta lo descarta en silencio, sin error.\n\n"
                    "La espera (%.1f h) tiene que ser menor que el horario hábil "
                    "(%.1f h). Baja la espera o alarga la ventana."
                    % (peor_caso, espera_horas, ventana_horas))
