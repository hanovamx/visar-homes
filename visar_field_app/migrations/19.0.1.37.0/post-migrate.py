# -*- coding: utf-8 -*-
"""Al actualizar a 19.0.1.37.0 (varias rondas de adicionales por visita), cada sello
de efectivo y de liga enviada queda apuntando a la factura que cubría (y el de
efectivo, copiado en esa factura, que es de donde lo lee la comisión).

Hasta ahora había un solo cobro por visita y el sello no guardaba factura. Sin esto,
un sello viejo no sabría de qué ronda es: o cubriría la factura de una ronda nueva
(el técnico vería "pagado" sin haber cobrado) o ninguna (una visita ya pagada
volvería a pedir cobro). Se apunta a la PRIMERA factura de adicionales de la visita,
que es la única que existía. Un sello cuya factura ya no se encuentra (S00316: su
pedido aparte S00318 se borró a mano el 22-sep-2026) se queda sin factura y no
cubre ninguna ronda nueva. Idempotente.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Task = env['project.task'].with_context(active_test=False)
    apuntados, huerfanos = [], []
    for campo_sello, campo_factura in (
            ('visar_upsell_cash_at', 'visar_upsell_cash_move_id'),
            ('visar_upsell_link_sent_at', 'visar_upsell_link_move_id')):
        for tarea in Task.search([(campo_sello, '!=', False), (campo_factura, '=', False)]):
            factura = tarea._visar_upsell_invoices()[:1]
            if factura:
                tarea[campo_factura] = factura
                if campo_sello == 'visar_upsell_cash_at' and not factura.visar_upsell_cash_at:
                    factura.write({
                        'visar_upsell_cash_at': tarea.visar_upsell_cash_at,
                        'visar_upsell_cash_by_id': tarea.visar_upsell_cash_by_id.id,
                    })
                apuntados.append((tarea.id, campo_sello, factura.name))
            else:
                huerfanos.append((tarea.id, campo_sello))
    _logger.info("Rondas de adicionales: sellos apuntados a su factura %s; "
                 "sin factura encontrada %s", apuntados, huerfanos)
