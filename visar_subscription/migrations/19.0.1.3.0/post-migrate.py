import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Pasa del multiplicador en factura a la línea de anticipo real en el pedido.

    Tres cosas, en este orden:
      1. Listas de precios (zona × plan): 12 reglas que sustituyen a las 78 por
         variante de las listas heredadas. No se re-teclea ningún precio: el
         descuento se LEE de las reglas heredadas y los precios siguen viviendo
         solo en las listas de zona.
      2. Planes anuales: tenían 2 periodos en la primera factura, o sea DOS AÑOS
         por adelantado. Casi seguro no intencional; se bajan a 1.
      3. Backfill de líneas de anticipo en las pólizas que esperaban cobrar 2
         periodos y aún no tienen factura posteada.

    Las órdenes CON factura posteada no se tocan: su `next_invoice_date` ya refleja
    lo que produjo el multiplicador, y quitar el override no puede cambiar una
    factura ya contabilizada. No es un olvido — no las "arregles" después.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})

    _visar_build_pricelists(env)
    _visar_fix_annual_plans(env)
    _visar_backfill_anticipo(env)


def _visar_build_pricelists(env):
    created = env['product.pricelist']._visar_sync_poliza_pricelists()
    legacy = env['product.pricelist']._visar_retire_legacy_poliza_pricelists()
    _logger.info(
        "VISAR pólizas: %s listas (zona × plan) sincronizadas; %s listas heredadas "
        "marcadas como no seleccionables (siguen ACTIVAS: hay órdenes que las usan).",
        len(created), len(legacy))
    for pricelist in created:
        _logger.info("  → %s (zona %s, plan %s)", pricelist.name,
                     pricelist.visar_zone_id.code, pricelist.visar_plan_id.name)


def _visar_fix_annual_plans(env):
    """Planes con periodo >= 12 meses no deben cobrar 2 periodos de entrada."""
    plans = env['sale.subscription.plan'].search([
        ('visar_first_invoice_periods', '>', 1),
        ('billing_period_unit', '=', 'month'),
        ('billing_period_value', '>=', 12),
    ])
    for plan in plans:
        _logger.warning(
            "VISAR pólizas: el plan '%s' cobraba %s periodos de %s meses por "
            "adelantado (%s meses). Se baja a 1 periodo.",
            plan.name, plan.visar_first_invoice_periods, plan.billing_period_value,
            plan.visar_first_invoice_periods * plan.billing_period_value)
    plans.write({'visar_first_invoice_periods': 1})


def _visar_backfill_anticipo(env):
    orders = env['sale.order'].search([
        ('state', 'in', ('draft', 'sent', 'sale')),
        ('plan_id.visar_first_invoice_periods', '>', 1),
    ])
    touched, confirmed, skipped = [], [], []
    for order in orders:
        if not order._visar_is_poliza():
            continue
        posted = order.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice' and m.state == 'posted')
        if posted:
            skipped.append(order.name)
            continue
        if not order._visar_sync_anticipo_lines():
            continue
        touched.append(order.name)
        order.message_post(body=(
            "Migración de pólizas: se añadió la línea de mensualidad adelantada que "
            "antes se aplicaba como multiplicador al facturar. El total del pedido "
            "ahora refleja los %s periodos que se cobran de entrada."
            % order._visar_first_invoice_periods()))
        if order.state == 'sale':
            confirmed.append(order.name)

    _logger.info(
        "VISAR pólizas: %s pedidos con línea de anticipo añadida (%s); %s omitidos "
        "por tener factura posteada.",
        len(touched), ', '.join(touched) or '—', len(skipped))
    if confirmed:
        _logger.warning(
            "VISAR pólizas: estos pedidos están CONFIRMADOS y su total cambió al "
            "añadir el anticipo — revisar antes de cobrar: %s", ', '.join(confirmed))
