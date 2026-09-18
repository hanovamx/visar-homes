# -*- coding: utf-8 -*-
{
    'name': "Visar - Comisiones por empleado",
    'summary': "Planes de comisión sobre hr.employee, sin depender de sale_commission.",
    'description': """
Visar - Comisiones por empleado
===============================
Copia la ESTRUCTURA del módulo Enterprise `sale_commission` (planes, logros,
periodos, metas, ajustes y reporte) pero con **empleados** en lugar de usuarios
de Odoo, para que un vendedor o un técnico cobre comisión sin necesitar —ni
pagar— una cuenta de usuario interno.

Por qué aparte y no extendiendo `sale_commission`:

- Su motor está soldado a `res.users`: la lista de vendedores del plan apunta a
  `res.users` con dominio `share = False` (lo que además descarta usuarios de
  portal, que sí son gratis), y su SQL cruza las ventas por `sale_order.user_id`
  y las facturas por `account_move.invoice_user_id`.
- Es un módulo de pago: modificar su lógica interna se rompe en cada
  actualización de Odoo y compromete el soporte.

Qué se copió y qué no:

- SÍ: plan con vigencia y estado aprobable, periodos autogenerados por
  periodicidad, reglas de logro con filtro por producto/categoría y tasa, modo
  "tasa directa" vs "meta con tabla", ajustes manuales y reporte por persona.
- NO: equipos de venta (Visar vende por persona), maquinaria multimoneda,
  pronóstico y vistas materializadas. El nativo las necesita para millones de
  documentos; Visar lleva ~200 pedidos confirmados al año y con eso el cálculo
  en Python es igual de rápido, legible y comprobable.

Lo que Visar necesita y el nativo NO hace:

- Comisionar sobre lo COBRADO (el nativo solo sabe vendido o facturado), que
  aquí importa porque hay pedidos pagados en línea que no llegan a facturarse.
- Atribuir por LÍNEA: el adicional que vende el técnico en campo vive dentro del
  pedido del servicio, y el nativo reparte el documento completo según su
  vendedor de cabecera.
- Cerrar un periodo para que lo ya pagado no se recalcule.

**La regla de comisión NO viene definida.** El módulo no trae ningún plan
sembrado ni porcentaje por omisión: el motor calcula lo que se configure. Lo que
falta decidir con negocio (porcentaje, base y si la venta directa y el upsell se
pagan igual) se captura en la ficha del plan, sin tocar código.
""",
    'author': "Hanova",
    'website': "https://hanova.mx",
    'category': 'Sales/Commission',
    'version': '19.0.1.0.1',
    'license': 'LGPL-3',
    # visar_field_app trae los dos campos que sostienen la atribución:
    # sale.order.visar_salesperson_employee_id y
    # sale.order.line.visar_upsell_employee_id.
    'depends': ['sale', 'hr', 'visar_field_app'],
    'data': [
        'security/ir.model.access.csv',
        'views/commission_plan_views.xml',
        'views/commission_line_views.xml',
        'views/commission_adjustment_views.xml',
        'views/commission_menu.xml',
    ],
    'installable': True,
    'application': False,
}
