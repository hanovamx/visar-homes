# -*- coding: utf-8 -*-
"""La plaga y el motivo (preventivo/correctivo) se guardan en cuanto se dicen.

Pedido de Visar (16-oct-2026), igual que la dirección: quien lo cuenta en el
chat y no reserva ese día no debería tener que contarlo otra vez.

**Por qué en el lead y no en el cliente.** La plaga se pregunta antes que el
nombre y la dirección, así que muchas veces todavía no hay ficha a la que
colgarla. Y describe un lugar, no a una persona (ver `visar.partner.fact`): un
cliente con dos casas puede tener cucarachas en una y nada en la otra. El lead
de fumigación del pipeline de WhatsApp ya existe por teléfono y por servicio, lo
ve el asesor, y se cierra solo cuando la reserva se paga o caduca.

A los clientes que ya tienen fumigación con Visar `agent_track_lead` no les abre
lead (no son leads). Aquí sí se abre, a propósito y con el visto bueno del
usuario: sin él no habría dónde guardar su plaga. No se les programa recontacto.
"""
import logging

from odoo import api, fields, models

from .visar_agent_tools import WA_STAGE_CERRADO_XMLID, WA_TEAM_XMLID

_logger = logging.getLogger(__name__)

MOTIVOS = [('preventivo', 'Preventivo'), ('correctivo', 'Correctivo')]

# Las plagas que no son del catálogo pero que Visar atiende con una visita de
# valoración. Son las opciones de corte del paso de plagas del cuestionario
# (`VISAR_PLAGA_CUTS` en visar_appointment); "no_se" no es una plaga y no se
# le ofrece a nadie como tal.
PLAGAS_VALORACION = [
    ('termitas', 'Termitas'),
    ('chinches', 'Chinches de cama'),
    ('no_se', 'No sabe qué plaga es'),
]
PLAGAS_VALORACION_A_OFRECER = ('termitas', 'chinches')


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    visar_wa_motivo = fields.Selection(
        MOTIVOS, string="Preventivo o correctivo", tracking=True,
        help="Lo que el cliente dijo por WhatsApp. Se guarda en cuanto lo dice.")
    visar_wa_plaga_ids = fields.Many2many(
        'visar.plaga', 'crm_lead_visar_plaga_rel', 'lead_id', 'plaga_id',
        string="Plagas",
        help="Las plagas del catálogo que el cliente dijo por WhatsApp.")
    visar_wa_plaga_valoracion = fields.Selection(
        PLAGAS_VALORACION, string="Plaga a valorar", tracking=True,
        help="Plaga fuera del catálogo que se atiende con visita de valoración.")
    visar_wa_plaga_at = fields.Datetime(
        string="Plaga dicha el", readonly=True)


class VisarAgentPlaga(models.AbstractModel):
    _inherit = 'visar.agent.tools'

    # ------------------------------------------------------------------
    # Catálogo: lo que se atiende con valoración también se dice
    # ------------------------------------------------------------------

    @api.model
    def agent_catalog_snapshot(self):
        """Lo de siempre, más `plagas_valoracion`.

        El agente le dice a quien trae una plaga que Visar no atiende cuáles SÍ
        atiende. Sin estas dos, a quien pregunta por termitas se le diría que
        no, y sí se atienden.
        """
        datos = super().agent_catalog_snapshot()
        datos['plagas_valoracion'] = [
            {'code': code, 'name': name}
            for code, name in PLAGAS_VALORACION
            if code in PLAGAS_VALORACION_A_OFRECER]
        return datos

    # ------------------------------------------------------------------
    # Guardar
    # ------------------------------------------------------------------

    @api.model
    def agent_save_pest(self, payload):
        """Guarda en el lead de fumigación lo que el cliente dijo de su plaga.

        `payload` = {
          "phone":      "5218112345678",
          "motivo":     "preventivo" | "correctivo" | None,
          "plagas":     ["rastreros", ...],          # `code` de visar.plaga
          "valoracion": "termitas" | "chinches" | "no_se" | None,
        }

        El payload es el estado COMPLETO que conoce el runtime, no un parche:
        lo que venga vacío se borra. Así una corrección ("no, son ratas") no deja
        las cucarachas de antes pegadas.

        Devuelve {"lead_id": int|None, "saved": bool, "skipped_reason": str|None}.
        Nunca levanta: guardar esto no le puede costar la respuesta a nadie.
        """
        try:
            with self.env.cr.savepoint():
                return self._agent_save_pest_ahora(payload or {})
        except Exception:  # noqa: BLE001 - ver el docstring
            _logger.exception("agent_save_pest: no se pudo guardar la plaga")
            return {'lead_id': None, 'saved': False, 'skipped_reason': 'error'}

    @api.model
    def _agent_save_pest_ahora(self, payload):
        def saltar(motivo):
            return {'lead_id': None, 'saved': False, 'skipped_reason': motivo}

        nat = self._agent_normalize_phone(payload.get('phone'))
        if len(nat) != 10:
            return saltar('invalid_phone')
        motivo = payload.get('motivo')
        motivo = motivo if motivo in dict(MOTIVOS) else False
        codigos = [str(c) for c in payload.get('plagas') or [] if c]
        plagas = self.env['visar.plaga'].sudo().search([('code', 'in', codigos)])
        valoracion = payload.get('valoracion')
        valoracion = valoracion if valoracion in dict(PLAGAS_VALORACION) else False
        if not (motivo or plagas or valoracion):
            return saltar('empty')

        group = self.env['visar.service.group'].sudo().search(
            [('code', '=', 'fumigacion')], limit=1)
        if not group:
            return saltar('no_group')
        partner = self._agent_find_partner(payload.get('phone'))
        lead, _created, reason = self._agent_open_lead(
            nat, group, partner=partner, phone=payload.get('phone'))
        if reason:
            return saltar(reason)
        lead.write({
            'visar_wa_motivo': motivo,
            'visar_wa_plaga_ids': [(6, 0, plagas.ids)],
            'visar_wa_plaga_valoracion': valoracion,
            'visar_wa_plaga_at': fields.Datetime.now(),
        })
        return {'lead_id': lead.id, 'saved': True, 'skipped_reason': None}

    # ------------------------------------------------------------------
    # Leer: viaja con el contexto del cliente
    # ------------------------------------------------------------------

    @api.model
    def agent_partner_facts(self, payload):
        """Lo de siempre, más `plaga` = {motivo, plagas, valoracion, fecha}.

        Va aunque el teléfono no tenga ficha de cliente: el lead es por
        teléfono, y quien todavía no reservó es justo quien más lo necesita.
        También se añade al bloque que lee el modelo, con la instrucción de
        confirmarla antes de usarla: es de otra conversación.
        """
        datos = super().agent_partner_facts(payload)
        try:
            with self.env.cr.savepoint():
                plaga = self._agent_plaga_guardada((payload or {}).get('phone'))
        except Exception:  # noqa: BLE001 - un dato de contexto no tumba nada
            _logger.exception("agent_partner_facts: no se pudo leer la plaga")
            plaga = None
        if plaga:
            datos['plaga'] = plaga
            linea = self._agent_plaga_bloque(plaga)
            datos['block'] = '\n\n'.join(
                x for x in ((datos.get('block') or '').strip(), linea) if x)
        return datos

    @api.model
    def _agent_plaga_guardada(self, phone):
        """La plaga del lead de fumigación abierto de este teléfono, o None."""
        nat = self._agent_normalize_phone(phone)
        if len(nat) != 10:
            return None
        team = self.env.ref(WA_TEAM_XMLID, raise_if_not_found=False)
        cerrado = self.env.ref(WA_STAGE_CERRADO_XMLID, raise_if_not_found=False)
        if not team:
            return None
        domain = [
            ('visar_wa_phone_norm', '=', nat),
            ('team_id', '=', team.id),
            ('visar_service_group_id.code', '=', 'fumigacion'),
            '|', '|', ('visar_wa_motivo', '!=', False),
            ('visar_wa_plaga_ids', '!=', False),
            ('visar_wa_plaga_valoracion', '!=', False),
        ]
        if cerrado:
            domain.append(('stage_id', '!=', cerrado.id))
        lead = self.env['crm.lead'].sudo().search(
            domain, order='visar_wa_plaga_at desc, id desc', limit=1)
        if not lead:
            return None
        fecha = lead.visar_wa_plaga_at or lead.write_date
        return {
            'motivo': lead.visar_wa_motivo or None,
            'plagas': [{'code': p.code, 'name': p.name,
                        'description': p.description or ''}
                       for p in lead.visar_wa_plaga_ids],
            'valoracion': lead.visar_wa_plaga_valoracion or None,
            'valoracion_nombre': dict(PLAGAS_VALORACION).get(
                lead.visar_wa_plaga_valoracion) or '',
            'fecha': fields.Date.to_string(fecha) if fecha else '',
        }

    @api.model
    def _agent_plaga_bloque(self, plaga):
        """La plaga guardada, en la voz del bloque de contexto."""
        partes = []
        if plaga.get('motivo'):
            partes.append(dict(MOTIVOS)[plaga['motivo']].lower())
        for p in plaga.get('plagas') or []:
            detalle = " (%s)" % p['description'] if p.get('description') else ''
            partes.append("%s%s" % (p['name'], detalle))
        if plaga.get('valoracion_nombre'):
            partes.append(plaga['valoracion_nombre'])
        if not partes:
            return ''
        return (
            "PLAGA QUE DIJO EN UNA CONVERSACION ANTERIOR (%s): %s.\n"
            "Es de otra conversacion: todavia NO cuenta como dicha. En cuanto "
            "el cliente hable de fumigar, tu SIGUIENTE pregunta es confirmarla, "
            "antes que cualquier otra: \"La vez pasada me dijiste que tenias "
            "..., ¿es correcto?\" (nunca \"¿sigue igual?\"). Si dice que si, "
            "llama `registrar_plaga` con esos datos; si no, preguntale que "
            "plaga tiene."
        ) % (plaga.get('fecha') or 'fecha desconocida', '; '.join(partes))
