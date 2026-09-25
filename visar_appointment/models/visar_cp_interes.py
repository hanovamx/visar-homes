# -*- coding: utf-8 -*-
"""De qué códigos postales nos escribe la gente, y de cuáles no podemos atender.

Visar quiere decidir **a dónde crecer** con datos en vez de con intuición: si de
un municipio que no cubrimos escriben treinta números distintos en un trimestre,
eso es un argumento; si escribe uno, no lo es.

Hoy ese dato se tira. Un CP fuera de cobertura deja un lead de escalamiento y el
código postal se queda **dentro de una nota del chatter**, donde no se puede
contar, agrupar ni graficar. Un CP con cobertura ni eso: se calcula el precio y
el CP se olvida.

Tres decisiones que explican la forma del modelo:

1. **Manda `numeros_distintos`, no `consultas`.** Una persona indecisa que
   pregunta seis veces no es un mercado. Por eso la unidad guardada es
   (CP, teléfono, día, origen) y no "cada vez que se mencionó un CP".

2. **Se geocodifica, pero nunca en la conversación.** El catálogo `visar.zone.cp`
   solo trae los CP que se atienden (1,080 filas, todas con zona), así que de un
   CP fuera de cobertura no se sabe ni el municipio: solo cinco dígitos, que en
   una pantalla no dicen nada. La fila nace `pendiente` y un cron la resuelve
   contra Mapbox. Geocodificar al registrar le sumaría la latencia de una
   llamada remota a la respuesta del cliente, que es justo lo que el §5.3 del
   diseño 33 pasó tres revisiones evitando.

3. **Los números internos se marcan, no se borran a mano.** El equipo y las
   suites de prueba escriben al agente, y sus números inflarían exactamente la
   columna que se usa para decidir. La pantalla los esconde por omisión y los
   contadores no los cuentan, pero quedan guardados: saber que una fila era del
   equipo es más útil que no tener la fila.

**Qué esperar al principio.** Mientras el agente no tenga tráfico real la
pantalla estará casi vacía. Se construye para que el dato empiece a juntarse
desde el primer cliente, no para que hoy diga algo.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Motivos ORDENADOS por avance: hasta dónde llegó el interés de ese CP ese día.
# Se rankea por posición, igual que las etapas del pipeline de WhatsApp
# (`crm_lead._visar_advance_stage`), y por la misma razón: la fila se actualiza
# sola varias veces en una conversación (cobertura -> cotización -> agendado) y
# sin orden el último en escribir borraría lo que ya se había conseguido.
VISAR_CP_MOTIVOS = (
    ('cobertura', "Preguntó por cobertura"),
    ('escalamiento', "Se canalizó a un asesor"),
    ('cotizacion', "Recibió una cotización"),
    ('agendado', "Llegó a capturar dirección"),
)
VISAR_CP_MOTIVO_RANK = {clave: i for i, (clave, _etiqueta) in enumerate(VISAR_CP_MOTIVOS)}

# Prefijos de teléfono que se consideran internos aunque no sean de un empleado.
# Los 177 "teléfonos" de la suite de aceptación empiezan con 999000 y no
# pertenecen a nadie: sin esto, el reporte nacería con 177 interesados falsos.
VISAR_CP_PREFIJOS_PARAM = 'visar.cp.telefonos_internos'
VISAR_CP_PREFIJOS_DEFAULT = '999000'

# Días que se guardan los teléfonos. Es la contrapartida de guardar el número de
# una persona: se ajusta sin desplegar.
VISAR_CP_RETENCION_PARAM = 'visar.cp.retencion_dias'
VISAR_CP_RETENCION_DEFAULT = 730

# CP por corrida del cron de geocodificación. Tope para no gastar la cuota de
# Mapbox de golpe si un día entran muchos CP nuevos.
VISAR_CP_GEO_LOTE_PARAM = 'visar.cp.geocode_lote'
VISAR_CP_GEO_LOTE_DEFAULT = 20


class VisarCpInteres(models.Model):
    """Un código postal del que alguien preguntó, con cuánta gente y cuándo."""

    _name = 'visar.cp.interes'
    _description = "Visar: interés por código postal"
    # Lo primero que se quiere ver es dónde hay más gente esperando.
    _order = 'numeros_distintos desc, consultas desc, name'

    name = fields.Char(
        string="Código postal", required=True, index=True, readonly=True,
        help="Código postal de 5 dígitos tal y como lo escribió el cliente.")
    zone_id = fields.Many2one(
        'visar.zone', string="Zona Visar", index=True, readonly=True,
        help="Zona que atiende este CP. Vacía = fuera de cobertura.")
    served = fields.Boolean(
        string="Con cobertura", compute='_compute_served', store=True,
        help="Verdadero si el CP está en el catálogo de zonas atendidas.")
    municipality = fields.Char(string="Municipio", readonly=True)
    state_name = fields.Char(string="Estado", readonly=True)
    lat = fields.Float(string="Latitud", digits=(10, 7), readonly=True)
    lng = fields.Float(string="Longitud", digits=(10, 7), readonly=True)
    geo_status = fields.Selection(
        selection=[
            ('catalogo', "Del catálogo"),
            ('pendiente', "Por geocodificar"),
            ('resuelto', "Geocodificado"),
            ('sin_datos', "Mapbox no lo encontró"),
        ],
        string="Ubicación", default='pendiente', required=True, readonly=True,
        help="'Del catálogo' = el CP se atiende y sus datos salen de "
             "visar.zone.cp. Los demás valores son del cron que geocodifica los "
             "CP que no están en el catálogo.")

    line_ids = fields.One2many(
        'visar.cp.interes.linea', 'interes_id', string="Interesados")

    # Los contadores se ALMACENAN porque son las columnas por las que se ordena y
    # se grafica: sin store no se puede poner `numeros_distintos desc` en _order
    # ni agrupar por municipio sumándolos.
    consultas = fields.Integer(
        string="Consultas", compute='_compute_totales', store=True,
        help="Días distintos en que alguien preguntó por este CP, sumando "
             "canales. No cuenta los números internos.")
    numeros_distintos = fields.Integer(
        string="Números distintos", compute='_compute_totales', store=True,
        help="Cuánta GENTE distinta preguntó. Es la columna que importa para "
             "decidir una expansión: un indeciso que pregunta seis veces no es "
             "un mercado.")
    primera = fields.Date(
        string="Primera vez", compute='_compute_totales', store=True)
    ultima = fields.Date(
        string="Última vez", compute='_compute_totales', store=True)

    # Odoo 19 IGNORA `_sql_constraints` (solo avisa en el log al cargar): la
    # restricción se declara así o no existe en la base.
    _cp_uniq = models.Constraint(
        'UNIQUE (name)', "Ya hay una fila para ese código postal.")

    @api.depends('zone_id')
    def _compute_served(self):
        for registro in self:
            registro.served = bool(registro.zone_id)

    @api.depends('line_ids', 'line_ids.phone', 'line_ids.fecha', 'line_ids.interno')
    def _compute_totales(self):
        """Los totales salen SOLO de las líneas que no son internas.

        Un teléfono vacío (una reserva web, que no pide número en ese paso)
        cuenta como consulta pero no como número distinto: contarlo inventaría
        un interesado por cada visita anónima.
        """
        for registro in self:
            lineas = registro.line_ids.filtered(lambda l: not l.interno)
            fechas = [l.fecha for l in lineas if l.fecha]
            registro.consultas = len(lineas)
            registro.numeros_distintos = len({l.phone for l in lineas if l.phone})
            registro.primera = min(fechas) if fechas else False
            registro.ultima = max(fechas) if fechas else False

    @api.depends('name', 'municipality')
    def _compute_display_name(self):
        for registro in self:
            partes = [registro.name or '']
            if registro.municipality:
                partes.append(registro.municipality)
            registro.display_name = ', '.join(p for p in partes if p)

    # ------------------------------------------------------------------
    # Registro (el único camino de entrada)
    # ------------------------------------------------------------------

    @api.model
    def _visar_registrar(self, cp, phone=None, origen='whatsapp', motivo='cobertura'):
        """Anota que alguien preguntó por `cp`. Nunca lanza, nunca bloquea.

        Es el único camino de entrada, y lo llaman cuatro sitios por los que YA
        pasaba un código postal: la consulta de cobertura del agente, su
        cotización, el escalamiento por CP fuera de zona, y el paso de la
        dirección del cuestionario (web y WhatsApp).

        **El `savepoint` es la mitad que importa**, y ya se aprendió en
        `visar_agent_prompt._visar_prompt_seguro`: sin él, una consulta que falla
        deja el cursor de Postgres abortado y **cualquier** consulta posterior
        levanta, así que el `try/except` no salvaría la respuesta al cliente —
        solo cambiaría de sitio la explosión. Un reporte que no se pudo escribir
        vale mucho menos que la contestación que el cliente está esperando.

        Devuelve el registro del CP, o un recordset vacío si no se pudo.
        """
        try:
            with self.env.cr.savepoint():
                return self.sudo()._visar_registrar_ahora(cp, phone, origen, motivo)
        except Exception:  # noqa: BLE001 - el reporte nunca tumba la respuesta
            _logger.exception(
                "visar.cp.interes: no se pudo registrar el CP %r (origen=%s)",
                cp, origen)
            return self.browse()

    @api.model
    def _visar_registrar_ahora(self, cp, phone=None, origen='whatsapp',
                               motivo='cobertura'):
        """El trabajo de `_visar_registrar`, sin la red de seguridad."""
        ZoneCp = self.env['visar.zone.cp'].sudo()
        normalizado = ZoneCp._normalize_cp(cp)
        if len(normalizado) != 5:
            # Un CP a medias no es un dato: es el cliente escribiendo todavía.
            return self.browse()
        if motivo not in VISAR_CP_MOTIVO_RANK:
            motivo = 'cobertura'
        if origen not in ('whatsapp', 'web'):
            origen = 'whatsapp'

        registro = self.search([('name', '=', normalizado)], limit=1)
        if not registro:
            registro = self.create(self._visar_vals_cp(normalizado, ZoneCp))
        elif not registro.zone_id:
            # El CP pudo entrar al catálogo DESPUÉS de la primera consulta: eso
            # es exactamente una expansión, y la fila tiene que dejar de contar
            # como fuera de cobertura sin que nadie la toque a mano.
            actuales = self._visar_vals_cp(normalizado, ZoneCp)
            if actuales.get('zone_id'):
                registro.write(actuales)

        nat = self.env['res.partner']._visar_phone_nat10_value(phone) or ''
        hoy = fields.Date.context_today(self)
        Linea = self.env['visar.cp.interes.linea'].sudo()
        linea = Linea.search([
            ('interes_id', '=', registro.id),
            ('phone', '=', nat),
            ('fecha', '=', hoy),
            ('origen', '=', origen),
        ], limit=1)
        if linea:
            linea._visar_avanzar_motivo(motivo)
        else:
            Linea.create({
                'interes_id': registro.id,
                'phone': nat,
                'origen': origen,
                'motivo': motivo,
                'fecha': hoy,
            })
        return registro

    @api.model
    def _visar_vals_cp(self, normalizado, ZoneCp=None):
        """Valores de una fila nueva: lo que el catálogo ya sabe de ese CP.

        Un CP del catálogo trae municipio y no necesita geocodificarse (queda en
        'catalogo'). Uno que no está queda 'pendiente' con solo sus cinco
        dígitos, que es todo lo que se sabe de él hasta que corra el cron.
        """
        ZoneCp = ZoneCp if ZoneCp is not None else self.env['visar.zone.cp'].sudo()
        cp_record = ZoneCp._get_cp_record(normalizado)
        zone = cp_record.zone_id
        vals = {
            'name': normalizado,
            'zone_id': zone.id if zone else False,
            'municipality': cp_record.municipality or '',
        }
        if zone:
            vals.update({
                'state_name': 'Nuevo León',
                'geo_status': 'catalogo',
                'lat': cp_record.visar_centroid_lat or 0.0,
                'lng': cp_record.visar_centroid_lng or 0.0,
            })
        else:
            vals['geo_status'] = 'pendiente'
        return vals

    # ------------------------------------------------------------------
    # Geocodificación (cron)
    # ------------------------------------------------------------------

    @api.model
    def _visar_geocode_lote(self):
        """Cuántos CP resuelve una corrida del cron."""
        valor = self.env['ir.config_parameter'].sudo().get_param(
            VISAR_CP_GEO_LOTE_PARAM)
        try:
            return max(1, int(valor))
        except (TypeError, ValueError):
            return VISAR_CP_GEO_LOTE_DEFAULT

    @api.model
    def _visar_cron_geocode(self):
        """Pone municipio y estado a los CP que no están en el catálogo.

        Fuera de la petición a propósito: es lo que permite que registrar un CP
        cueste una consulta local y no una llamada a Mapbox. Un CP que no
        resuelve queda en 'sin_datos' con sus cinco dígitos — degradar, nunca
        inventar un municipio.
        """
        pendientes = self.sudo().search(
            [('geo_status', '=', 'pendiente')], limit=self._visar_geocode_lote())
        if not pendientes:
            return 0
        Mapbox = self.env['visar.mapbox.service'].sudo()
        resueltos = 0
        for registro in pendientes:
            try:
                # Los cinco dígitos SOLOS y `types='postcode'`. Con la consulta
                # en prosa ("CP 06700, México") Mapbox contestó *una calle de
                # Mérida* y el reporte habría dicho que hay demanda en Yucatán
                # (visto el 25-sep-2026, contra Mapbox de verdad).
                lugar = Mapbox._visar_mapbox_geocode_lugar(
                    registro.name, types='postcode')
            except Exception:  # noqa: BLE001 - un CP no puede tumbar la corrida
                _logger.exception(
                    "visar.cp.interes: falló el geocode del CP %s", registro.name)
                lugar = None
            if not lugar:
                registro.write({'geo_status': 'sin_datos'})
                continue
            # Y se comprueba que contestaron lo que se preguntó. Un municipio
            # equivocado es PEOR que ninguno: manda a Visar a abrir zona en la
            # ciudad de otro.
            if (lugar.get('text') or '') != registro.name:
                _logger.warning(
                    "visar.cp.interes: Mapbox contestó %r al preguntar por el CP "
                    "%s; se deja sin ubicar.", lugar.get('text'), registro.name)
                registro.write({'geo_status': 'sin_datos'})
                continue
            registro.write({
                'lat': lugar['lat'],
                'lng': lugar['lng'],
                'municipality': lugar.get('municipality') or registro.municipality or '',
                'state_name': lugar.get('state') or '',
                'geo_status': 'resuelto',
            })
            resueltos += 1
        _logger.info(
            "visar.cp.interes: %s de %s CP geocodificados; quedan %s pendientes.",
            resueltos, len(pendientes),
            self.sudo().search_count([('geo_status', '=', 'pendiente')]))
        return resueltos

    # ------------------------------------------------------------------
    # Retención
    # ------------------------------------------------------------------

    @api.model
    def _visar_retencion_dias(self):
        valor = self.env['ir.config_parameter'].sudo().get_param(
            VISAR_CP_RETENCION_PARAM)
        try:
            return max(1, int(valor))
        except (TypeError, ValueError):
            return VISAR_CP_RETENCION_DEFAULT

    @api.model
    def _visar_cron_retencion(self):
        """Borra los renglones más viejos que la ventana de retención.

        Es la contrapartida de guardar el teléfono de una persona: pasado el
        plazo se va el número. El CP se queda (no es dato personal), pero sus
        contadores bajan, porque contar gente que ya no se puede contactar sería
        contar un mercado que no existe.
        """
        limite = fields.Date.subtract(
            fields.Date.context_today(self), days=self._visar_retencion_dias())
        viejas = self.env['visar.cp.interes.linea'].sudo().search(
            [('fecha', '<', limite)])
        cuantas = len(viejas)
        if cuantas:
            viejas.unlink()
            _logger.info(
                "visar.cp.interes: %s renglones borrados por retención (antes de %s).",
                cuantas, limite)
        return cuantas


class VisarCpInteresLinea(models.Model):
    """Un interesado en un CP, un día, por un canal."""

    _name = 'visar.cp.interes.linea'
    _description = "Visar: interesado por código postal"
    _order = 'fecha desc, id desc'

    interes_id = fields.Many2one(
        'visar.cp.interes', string="Código postal", required=True,
        ondelete='cascade', index=True)
    # Se guarda vacío (no NULL) a propósito: Postgres considera distintos dos
    # NULL, así que con NULL la restricción de unicidad no dedupearía las
    # reservas web, que en el paso de la dirección todavía no piden teléfono.
    phone = fields.Char(
        string="Teléfono", default='', index=True,
        help="Número nacional de 10 dígitos. Vacío en las reservas web, que no "
             "piden teléfono en el paso de la dirección.")
    origen = fields.Selection(
        selection=[('whatsapp', "WhatsApp"), ('web', "Sitio web")],
        string="Origen", required=True, default='whatsapp')
    motivo = fields.Selection(
        selection=list(VISAR_CP_MOTIVOS), string="Hasta dónde llegó",
        required=True, default='cobertura')
    fecha = fields.Date(
        string="Fecha", required=True, index=True,
        default=lambda self: fields.Date.context_today(self))
    interno = fields.Boolean(
        string="Interno", readonly=True,
        help="Número del equipo o de una corrida de prueba. No cuenta en los "
             "totales, pero se guarda: saber que la fila era del equipo es más "
             "útil que no tener la fila.")

    # La red de seguridad de la unidad que se cuenta: una persona, un día, un
    # canal. `_visar_registrar_ahora` ya busca antes de crear, pero esa
    # comprobación vive en código y esta vive en la base, que es la que aguanta
    # dos escrituras a la vez. Declarada con `models.Constraint` porque Odoo 19
    # ignora `_sql_constraints`.
    _interesado_uniq = models.Constraint(
        'UNIQUE (interes_id, phone, fecha, origen)',
        "Ese número ya quedó registrado hoy para ese código postal.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['interno'] = self._visar_es_interno(vals.get('phone'))
        return super().create(vals_list)

    def _visar_avanzar_motivo(self, motivo):
        """Sube el motivo solo si es un AVANCE (mismo criterio que el pipeline).

        Sin esto, el paso de la dirección y una segunda consulta de cobertura en
        la misma conversación se pisarían: la fila acabaría diciendo que el
        cliente solo preguntó, cuando había llegado a cotizar.
        """
        self.ensure_one()
        nuevo = VISAR_CP_MOTIVO_RANK.get(motivo, -1)
        actual = VISAR_CP_MOTIVO_RANK.get(self.motivo, -1)
        if nuevo > actual:
            self.motivo = motivo
        return self

    # ------------------------------------------------------------------
    # Marca de interno
    # ------------------------------------------------------------------

    @api.model
    def _visar_prefijos_internos(self):
        """Prefijos que marcan un número como interno, editables sin desplegar."""
        valor = self.env['ir.config_parameter'].sudo().get_param(
            VISAR_CP_PREFIJOS_PARAM, VISAR_CP_PREFIJOS_DEFAULT)
        return [p.strip() for p in (valor or '').split(',') if p.strip()]

    @api.model
    def _visar_es_interno(self, phone):
        """¿Es un número del equipo o de una corrida de prueba?

        Dos caminos, porque hacen falta los dos: los 177 números de la suite de
        aceptación NO son empleados (empiezan con 999000 y no existen), y un
        técnico que le escribe al agente para probarlo SÍ es empleado pero su
        número no tiene nada de particular.

        Solo se miran los teléfonos de TRABAJO del empleado. El personal
        (`private_phone`) está restringido a RR.HH. a propósito y no hace falta
        para esto.
        """
        nat = self.env['res.partner']._visar_phone_nat10_value(phone) or ''
        if not nat:
            # Una reserva web anónima no es interna: simplemente no trae número.
            return False
        if any(nat.startswith(prefijo) for prefijo in self._visar_prefijos_internos()):
            return True
        partner = self.env['res.partner'].sudo().search(
            [('visar_phone_nat10', '=', nat), ('employee_ids', '!=', False)], limit=1)
        if partner:
            return True
        Partner = self.env['res.partner']
        empleados = self.env['hr.employee'].sudo().search_read(
            [], ['work_phone', 'mobile_phone'])
        for empleado in empleados:
            for campo in ('work_phone', 'mobile_phone'):
                if Partner._visar_phone_nat10_value(empleado.get(campo)) == nat:
                    return True
        return False
