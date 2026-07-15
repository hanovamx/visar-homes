def migrate(cr, version):
    """Renombra la columna del plan preservando los valores existentes:
    visar_anticipo_services (nº de servicios de depósito) -> visar_first_invoice_periods
    (nº de mensualidades en la 1ª factura). Los planes de póliza que tenían 2 quedan
    en 2 (que ahora significa 'cobra 2 meses de entrada'), sin perder configuración."""
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sale_subscription_plan'
          AND column_name = 'visar_anticipo_services'
    """)
    has_old = cr.fetchone()
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'sale_subscription_plan'
          AND column_name = 'visar_first_invoice_periods'
    """)
    has_new = cr.fetchone()
    if has_old and not has_new:
        cr.execute("""
            ALTER TABLE sale_subscription_plan
            RENAME COLUMN visar_anticipo_services TO visar_first_invoice_periods
        """)
