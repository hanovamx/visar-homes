# -*- coding: utf-8 -*-
"""Quien le ha ESCRITO al agente. Una fila por telefono.

Hoy no existe ninguna lista de con quien ha hablado el agente. El estado de las
conversaciones vive en un SQLite del runtime (`app/conversation/sqlite_store.py`),
que no es consultable desde Odoo, se borra con la retencion y no tiene pantalla.
Lo unico que llega a Odoo es un lead de CRM cuando alguien cotiza, y un CP en el
reporte de expansion: de quien pregunto una duda y se fue no queda nada.

**Solo cuenta lo que ENTRA.** Un aviso saliente (en camino, llegada, reagenda) no
crea contacto: Odoo le escribe a clientes que nunca han abierto una conversacion,
y meterlos aqui convertiria esta lista en "a quien le hemos escrito", que ya se
puede sacar de las tareas. Lo que no existe en ninguna parte es quien escribio
**el**.

## Tres decisiones

**1. La clave es el telefono, no el cliente.** La mayoria de quien escribe no es
`res.partner` y puede no llegar a serlo nunca: el agente crea el cliente solo al
**cerrar una reserva** (`_agent_booking_partner`), asi que quien pregunta un
precio y se va deja un lead de CRM y nada mas. Por eso la fila nace con el
`nat10` -los ultimos 10 digitos, la misma clave que usa `_agent_find_partner`- y
`partner_id` puede quedar vacio para siempre sin que eso sea un error.

**2. El cliente se vuelve a resolver, no se fija una vez.** Alguien escribe hoy
como desconocido y reserva la semana que viene: si el enlace se decidiera solo al
crear la fila, esta lista envejeceria mintiendo. Se re-resuelve en cada mensaje
que entra, y un cron nocturno recoge a los que ya no escriben pero si se hicieron
clientes por la web.

**3. La politica de AMBIGUEDAD es la del resto del modulo.** Si dos `res.partner`
comparten el numero **no se adivina**: `partner_id` se queda vacio y la fila lo
dice (`partner_ambiguo`). Es la misma regla de `_agent_find_partner` y de
`_agent_booking_partner`, y por el mismo motivo -equivocarse aqui es ensenar
datos de otra persona-.

## Los numeros internos se marcan, no se borran

El equipo y las suites de prueba le escriben al agente. Los 177 "telefonos" de la
suite de aceptacion empiezan con 999000 y no son de nadie: sin marcarlos, esta
lista naceria con 177 contactos falsos. Se reutiliza **el mismo** juicio que el
reporte de codigos postales (`visar.cp.interes.linea._visar_es_interno`) en vez de
escribir otro: dos definiciones de "interno" divergen en cuanto alguien toque una.

## Que NO hay aqui todavia

La ruta que se guarda es **donde estaba la conversacion cuando llego el mensaje**,
no las transiciones. Para el tablero de "a que ruta se entra mas" hace falta un
registro por mensaje, que es una tabla aparte y bastante mas grande; este modelo
se disena para no estorbarla. Y no hay facts del cliente: eso se decidio dejar
para despues, y con el agente escribiendo en ranuras cerradas, no prosa libre.
"""
import logging

from odoo import api, fields, models

from odoo.addons.visar_whatsapp_agent.models.visar_agent_prompt import ROUTES

_logger = logging.getLogger(__name__)


class VisarAgentContacto(models.Model):
    _name = 'visar.agent.contacto'
    _description = "Contacto del agente de WhatsApp"
    _order = 'ultimo_mensaje desc, id desc'
    _rec_name = 'name'

    # Los ultimos 10 digitos. MISMA clave que `_agent_find_partner` y
    # `res.partner.visar_phone_nat10`: si esta lista usara otra normalizacion,
    # diria que un contacto no es cliente mientras el agente lo saluda por su
    # nombre.
    name = fields.Char(
        string="Telefono", required=True, index=True, readonly=True,
        help="Los ultimos 10 digitos del numero, que es como se identifica a un "
             "cliente en todo el modulo.")
    phone = fields.Char(
        string="Numero completo", readonly=True,
        help="El ultimo numero con el que llego un mensaje, tal y como lo manda "
             "WhatsApp (con lada de pais).")
    partner_id = fields.Many2one(
        'res.partner', string="Cliente", index=True, ondelete='set null',
        help="Vacio no es un error: la mayoria de quien escribe no es cliente "
             "todavia. Se vuelve a resolver en cada mensaje y cada noche.")
    partner_ambiguo = fields.Boolean(
        string="Numero repetido", readonly=True,
        help="Mas de un cliente tiene este numero. No se elige ninguno a "
             "proposito: equivocarse aqui significa ensenar los datos de otra "
             "persona. Hay que unir los duplicados en Contactos.")
    primer_mensaje = fields.Datetime(string="Primer mensaje", readonly=True)
    ultimo_mensaje = fields.Datetime(string="Ultimo mensaje", readonly=True, index=True)
    mensajes = fields.Integer(
        string="Mensajes recibidos", readonly=True, default=0,
        help="Cuantos mensajes ha mandado. Solo cuenta lo que ENTRA: los avisos "
             "que Odoo le manda no suman.")
    ultima_ruta = fields.Selection(
        ROUTES, string="Ultima ruta", readonly=True,
        help="Donde estaba la conversacion cuando llego su ultimo mensaje.")
    interno = fields.Boolean(
        string="Interno", readonly=True,
        help="Numero del equipo o de una corrida de prueba. Se marca en vez de "
             "borrarse: saber que una fila era del equipo es mas util que no "
             "tener la fila. La pantalla los esconde por omision.")
    active = fields.Boolean(string="Activo", default=True)

    _sql_constraints = [
        # ⚠️ Odoo 19 NO crea estas restricciones (hay constancia de otras
        # `_sql_constraints` de Visar que no existen en la base). Se deja
        # declarada porque documenta la intencion, pero lo que de verdad
        # garantiza una fila por telefono es `_visar_registrar_ahora`, que busca
        # antes de crear, y el indice de `name`.
        ('visar_agent_contacto_uniq', 'unique(name)',
         "Ya hay un contacto con ese telefono."),
    ]

    @api.depends('partner_id', 'name')
    def _compute_display_name(self):
        for registro in self:
            registro.display_name = (
                registro.partner_id.name or registro.name or '')

    # ------------------------------------------------------------------
    # El unico camino de entrada
    # ------------------------------------------------------------------

    @api.model
    def _visar_registrar(self, phone, ruta=None):
        """Anota que entro un mensaje de `phone`. Nunca lanza, nunca bloquea.

        **El `savepoint` es la mitad que importa**, y ya se aprendio dos veces en
        este modulo: sin el, una consulta que falla deja el cursor de Postgres
        ABORTADO y cualquier consulta posterior levanta, asi que el `try/except`
        no salvaria la respuesta al cliente — solo cambiaria de sitio la
        explosion. Una lista de contactos vale mucho menos que la contestacion
        que el cliente esta esperando.

        Devuelve el registro, o un recordset vacio si no se pudo.
        """
        try:
            with self.env.cr.savepoint():
                return self.sudo()._visar_registrar_ahora(phone, ruta)
        except Exception:  # noqa: BLE001 - la lista nunca tumba la respuesta
            _logger.exception(
                "visar.agent.contacto: no se pudo registrar el mensaje de %r",
                phone)
            return self.browse()

    @api.model
    def _visar_registrar_ahora(self, phone, ruta=None):
        """El trabajo de `_visar_registrar`, sin la red de seguridad."""
        Partner = self.env['res.partner']
        nat = Partner._visar_phone_nat10_value(phone) or ''
        if not nat:
            # Sin numero no hay contacto que contar. Pasa en las pruebas y en
            # cualquier canal que no traiga telefono.
            return self.browse()
        if ruta not in dict(ROUTES):
            ruta = False

        ahora = fields.Datetime.now()
        registro = self.with_context(active_test=False).search(
            [('name', '=', nat)], limit=1)
        partner, ambiguo = self._visar_resolver_partner(nat)
        vals = {
            'phone': phone or False,
            'ultimo_mensaje': ahora,
            'ultima_ruta': ruta,
            'partner_id': partner.id or False,
            'partner_ambiguo': ambiguo,
        }
        if registro:
            registro.write(dict(vals, mensajes=(registro.mensajes or 0) + 1))
            return registro
        return self.create(dict(
            vals,
            name=nat,
            primer_mensaje=ahora,
            mensajes=1,
            # Se calcula UNA vez, al crear: es un juicio sobre el numero, no
            # sobre el mensaje, y recalcularlo en cada mensaje costaria un
            # `search_read` de todos los empleados por mensaje recibido.
            interno=self.env['visar.cp.interes.linea'].sudo()._visar_es_interno(phone),
        ))

    @api.model
    def _visar_resolver_partner(self, nat):
        """(partner, ambiguo) para un `nat10`. Nunca elige entre varios.

        Misma politica que `_agent_find_partner` y `_agent_booking_partner`, y no
        por simetria: si esta pantalla resolviera la ambiguedad a su manera,
        ensenaria un nombre distinto del que el agente usa al contestar.
        """
        Partner = self.env['res.partner'].sudo()
        if not nat:
            return Partner.browse(), False
        encontrados = Partner.search([('visar_phone_nat10', '=', nat)])
        if len(encontrados) == 1:
            return encontrados, False
        if len(encontrados) > 1:
            return Partner.browse(), True
        return Partner.browse(), False

    # ------------------------------------------------------------------
    # Enlace tardio
    # ------------------------------------------------------------------

    @api.model
    def _visar_cron_resolver_partners(self):
        """Vuelve a buscar el cliente de los contactos que no lo tienen.

        Sin esto la lista envejece mintiendo: quien escribio una vez y reservo
        por la web DESPUES se queda como «sin cliente» para siempre, porque el
        enlace solo se rehace cuando entra otro mensaje suyo — y justo esa
        persona ya no escribe.

        Solo mira los que faltan (sin cliente, o marcados como ambiguos por si
        alguien unio los duplicados). Los que ya tienen cliente no se tocan: si
        alguien lo corrigio a mano, esa correccion manda.
        """
        pendientes = self.with_context(active_test=False).search(
            ['|', ('partner_id', '=', False), ('partner_ambiguo', '=', True)])
        enlazados = 0
        for contacto in pendientes:
            partner, ambiguo = self._visar_resolver_partner(contacto.name)
            if partner == contacto.partner_id and ambiguo == contacto.partner_ambiguo:
                continue
            contacto.write({'partner_id': partner.id or False,
                            'partner_ambiguo': ambiguo})
            if partner:
                enlazados += 1
        if enlazados:
            _logger.info(
                "visar.agent.contacto: %d contacto(s) enlazados a su cliente",
                enlazados)
        return enlazados
