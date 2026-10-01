# -*- coding: utf-8 -*-
"""Lo que Visar sabe de un cliente y de cada uno de sus domicilios.

Nace de dos necesidades que resultaron ser la misma: que **oficina** tenga a mano
el contexto de un cliente sin abrir la hoja de una visita, y que el **agente**
hable distinto con quien ya es cliente.

## Dos anclas, y no es un detalle

Un cliente agenda para sitios distintos: su casa, el departamento de su mamá, su
local. En producción ya pasa —82 domicilios para 23 clientes el 1-oct-2026, y uno
de ellos con seis—. Por eso un dato como «es un departamento» **no es del
cliente**: es del DOMICILIO, y colgarlo del cliente que además tiene una casa no
es un dato incompleto, es un dato FALSO.

El sitio correcto ya existía: `_visar_apply_delivery_address` crea (y reutiliza)
un `res.partner` hijo con `type='delivery'` por cada dirección, deduplicado por
(cliente, calle, CP). Así que:

  * ámbito **domicilio** -> el contacto de entrega. Tipo de inmueble, cómo se
    entra, mascotas, qué plaga vuelve, quién autoriza.
  * ámbito **cliente** -> el cliente de arriba. Lo que es de la persona y no del
    lugar: cuándo le conviene.

`_check_ambito` lo hace cumplir, y no por pulcritud: es la única forma de que no
se pueda escribir «Departamento» en un cliente que tiene tres direcciones.

## Ranuras CERRADAS, no texto libre

Las ranuras las define el código. Una ranura se gana su sitio solo si saber el
dato **cambia lo que el agente dice o hace**; si no, es un campo de CRM y su
sitio es la ficha, no esto.

Y lo que deliberadamente **no** es ranura: metros, interior/exterior, CP o zona.
Son entradas de PRECIO y se preguntan siempre. Ver `VISAR_FACT_NIVEL`.

## El código no sobrescribe a una persona

Un fact de origen `hoja` se deriva solo de lo que el técnico escribió en la hoja
de valoración. Si alguien lo corrigió a mano (`persona`), la derivación **no lo
pisa**: es la misma regla que el cron de contactos y que el Vocabulario, y por el
mismo motivo —una corrección manual es la señal más fuerte que hay—.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# ⚠️ EL NIVEL, escrito donde no se puede ignorar.
#
# Un fact **informa el tono**: el agente puede saberlo y mencionarlo. NO contesta
# ningún paso del cuestionario, y jamás toca una entrada de precio.
#
# No es prudencia de más, son dos razones:
#
#   1. El domicilio se pregunta TARDE —es un paso del cuestionario—, así que
#      cuando el agente empieza a hablar todavía no sabe de qué dirección se
#      trata. Los facts de domicilio no están disponibles cuando habría que
#      pre-rellenar, aunque quisiéramos.
#   2. La memoria de la ruta de información dice, literalmente: *"Pregunta SIEMPRE
#      si también quiere el EXTERIOR. Nunca supongas que es solo interior: es un
#      dato de precio."* Un fact viejo que dijera "solo interior" se saltaría una
#      pregunta que determina el precio, y acabaríamos cobrando mal una cita.
#
# Lo que SÍ gana el agente: puede preguntar mejor en vez de suponer. A un cliente
# con seis domicilios le pregunta "¿es para la casa de X o para el local de Y?",
# que resuelve la ambigüedad en un mensaje en vez de arrastrarla hasta el precio.
VISAR_FACT_NIVEL = 'informa'

VISAR_FACT_AMBITOS = [
    ('domicilio', "Del domicilio"),
    ('cliente', "Del cliente"),
]

# (clave, etiqueta, ámbito, ayuda). Añadir una ranura es una línea aquí.
VISAR_FACT_SLOTS = [
    ('tipo_inmueble', "Tipo de inmueble", 'domicilio',
     "Casa, departamento, local, bodega, oficina. Lo escribe el técnico en la "
     "hoja de valoración y se sube aquí solo."),
    ('acceso', "Cómo se entra", 'domicilio',
     "Portón con candado, hay que avisar al portero, perro suelto en el patio. "
     "Sale de \"Restricciones de acceso\" de la hoja."),
    ('mascotas', "Mascotas", 'domicilio',
     "Si hay y cuáles. Cambia las instrucciones previas a la visita."),
    ('plaga_recurrente', "Plaga que vuelve", 'domicilio',
     "Qué bicho reaparece en este domicilio. Es la conversación que el cliente "
     "ya tuvo, y no tener que repetirla vale mucho."),
    ('quien_es', "Quién autoriza", 'domicilio',
     "Dueño, inquilino o administrador. Cambia quién puede aprobar un trabajo "
     "en ESTA dirección."),
    ('preferencia_horario', "Cuándo le conviene", 'cliente',
     "Mañanas, tardes, sábados. Es de la persona, no del lugar."),
]

VISAR_FACT_AMBITO_DE = {clave: ambito for clave, _e, ambito, _h in VISAR_FACT_SLOTS}
VISAR_FACT_ETIQUETA_DE = {clave: etiqueta for clave, etiqueta, _a, _h in VISAR_FACT_SLOTS}

VISAR_FACT_ORIGENES = [
    ('hoja', "De la hoja de trabajo"),
    ('persona', "Escrito por una persona"),
    ('agente', "Propuesto por el agente"),
]


class VisarPartnerFact(models.Model):
    _name = 'visar.partner.fact'
    _description = "Dato conocido de un cliente o de un domicilio"
    _order = 'partner_id, slot'

    partner_id = fields.Many2one(
        'res.partner', string="Cliente o domicilio", required=True, index=True,
        ondelete='cascade')
    slot = fields.Selection(
        [(c, e) for c, e, _a, _h in VISAR_FACT_SLOTS],
        string="Dato", required=True, index=True)
    value = fields.Char(string="Valor", required=True)
    ambito = fields.Selection(
        VISAR_FACT_AMBITOS, string="Ámbito", compute='_compute_ambito',
        help="Lo decide la ranura, no quien escribe: un tipo de inmueble es del "
             "domicilio aunque se capture hablando con el cliente.")
    origen = fields.Selection(
        VISAR_FACT_ORIGENES, string="Origen", required=True, default='persona',
        help="Un dato derivado de la hoja se actualiza solo. Uno escrito por una "
             "persona NO se sobrescribe nunca: una corrección manual es la señal "
             "más fuerte que hay.")
    active = fields.Boolean(string="Activo", default=True)

    @api.depends('slot')
    def _compute_ambito(self):
        for registro in self:
            registro.ambito = VISAR_FACT_AMBITO_DE.get(registro.slot) or False

    @api.depends('slot', 'value')
    def _compute_display_name(self):
        for registro in self:
            registro.display_name = "%s: %s" % (
                VISAR_FACT_ETIQUETA_DE.get(registro.slot, registro.slot or ''),
                registro.value or '')

    # ------------------------------------------------------------------
    # Reglas
    # ------------------------------------------------------------------

    @api.constrains('partner_id', 'slot')
    def _check_ambito(self):
        """Un dato del domicilio NO puede colgarse del cliente, ni al revés.

        Es la regla que da sentido a todo el modelo. Un cliente con tres
        direcciones y «Departamento» escrito en su ficha no tiene un dato
        incompleto: tiene un dato FALSO, y el agente lo repetiría con la misma
        confianza que uno bueno.
        """
        for registro in self:
            ambito = VISAR_FACT_AMBITO_DE.get(registro.slot)
            partner = registro.partner_id
            etiqueta = VISAR_FACT_ETIQUETA_DE.get(registro.slot, registro.slot)
            if ambito == 'domicilio' and partner.type != 'delivery':
                raise ValidationError(_(
                    "«%(dato)s» es un dato del DOMICILIO y «%(quien)s» no es una "
                    "dirección de servicio.\n\n"
                    "Un cliente puede agendar para su casa y para un local, así "
                    "que este dato tiene que ir en la dirección concreta — si no, "
                    "el agente lo repetiría para las dos.",
                    dato=etiqueta, quien=partner.display_name or ''))
            if ambito == 'cliente' and partner != partner.commercial_partner_id:
                raise ValidationError(_(
                    "«%(dato)s» es un dato de la PERSONA, así que va en el "
                    "cliente y no en una de sus direcciones.",
                    dato=etiqueta, quien=partner.display_name or ''))

    @api.constrains('slot', 'partner_id', 'active')
    def _check_una_vez(self):
        """Un valor por ranura y por cliente/domicilio.

        Dos filas de la misma ranura serían dos verdades, y el agente leería una
        de las dos sin decir cuál. Los archivados no cuentan: archivar es la
        marcha atrás.
        """
        for registro in self:
            if not registro.active:
                continue
            if self.search_count([('partner_id', '=', registro.partner_id.id),
                                  ('slot', '=', registro.slot),
                                  ('id', '!=', registro.id)]):
                raise ValidationError(_(
                    "Ya hay un valor de «%(dato)s» para %(quien)s. Edita ese: dos "
                    "filas serían dos verdades.",
                    dato=VISAR_FACT_ETIQUETA_DE.get(registro.slot, registro.slot),
                    quien=registro.partner_id.display_name or ''))

    # ------------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------------

    @api.model
    def _visar_fact_set(self, partner, slot, value, origen='persona'):
        """Pone (o actualiza) un dato. Nunca levanta; devuelve el registro o vacío.

        **Un origen `hoja` no sobrescribe a una `persona`.** La derivación corre
        en cada guardado de la hoja, así que sin esa regla una corrección manual
        duraría hasta el siguiente guardado del técnico — el fallo de «lo edité y
        se perdió», pero automatizado.
        """
        valor = (value or '').strip()
        if not partner or slot not in VISAR_FACT_AMBITO_DE or not valor:
            return self.browse()
        try:
            with self.env.cr.savepoint():
                existente = self.sudo().search(
                    [('partner_id', '=', partner.id), ('slot', '=', slot)], limit=1)
                if existente:
                    if origen == 'hoja' and existente.origen == 'persona':
                        return existente
                    if existente.value != valor or existente.origen != origen:
                        existente.write({'value': valor, 'origen': origen})
                    return existente
                return self.sudo().create({
                    'partner_id': partner.id, 'slot': slot,
                    'value': valor, 'origen': origen})
        except Exception:  # noqa: BLE001 - un dato de contexto no tumba nada
            return self.browse()

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    @api.model
    def _visar_facts_de(self, partner):
        """{ranura: valor} de ese cliente o domicilio. Solo los activos."""
        if not partner:
            return {}
        return {f.slot: f.value for f in self.sudo().search(
            [('partner_id', '=', partner.id)], order='slot')}


class ResPartnerFacts(models.Model):
    _inherit = 'res.partner'

    visar_fact_ids = fields.One2many(
        'visar.partner.fact', 'partner_id', string="Lo que sabemos",
        help="Contexto que el agente lee cuando esta persona escribe. Cada dato "
             "vive donde le toca: los del lugar en la dirección de servicio, los "
             "de la persona en el cliente.")

    def _visar_domicilios_de_servicio(self):
        """Las direcciones de servicio de este cliente, para leerles sus datos.

        Se parte del cliente comercial y no de `self`: si quien escribe resultó
        ser un contacto hijo, sus hermanos son los domicilios.
        """
        self.ensure_one()
        comercial = self.commercial_partner_id or self
        return self.env['res.partner'].sudo().search([
            ('parent_id', '=', comercial.id), ('type', '=', 'delivery'),
        ], order='id')
