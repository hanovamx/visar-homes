def migrate(cr, version):
    """Antiguo 0 ('sin depósito') -> 1 (1 periodo = comportamiento normal). Los planes
    de póliza con 2 quedan en 2 (2 mensualidades en la 1ª factura). El producto de
    depósito lo archiva el propio data file (active=False), no aquí."""
    cr.execute("""
        UPDATE sale_subscription_plan
        SET visar_first_invoice_periods = 1
        WHERE visar_first_invoice_periods IS NULL
           OR visar_first_invoice_periods = 0
    """)
