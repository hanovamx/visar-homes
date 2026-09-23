# -*- coding: utf-8 -*-
"""Inventario por ruta: cada técnico gasta de SU ubicación, no del almacén global.

Hasta el 23-sep-2026 el inventario era decorativo: la hoja de trabajo ofrecía una
lista FIJA de nueve principios activos (Cipermetrina, Fipronil…) que no eran
productos de Odoo, y el catálogo de campo enseñaba lo mismo a todos los técnicos
sin mirar si lo llevaban en la camioneta. Nada se descontaba nunca.

El modelo que implanta esto:

- cada técnico tiene una **ubicación interna propia** (`VHR/Existencias/<nombre>`),
  que es su camioneta;
- lo que la app le ofrece —plaguicidas en la hoja, productos en el catálogo— sale
  de lo que HAY ahí, con la cantidad a la vista;
- al cerrar el servicio lo consumido y lo vendido SALE de esa ubicación de verdad.

**Nunca se bloquea al técnico por el inventario.** Un conteo desfasado es problema
de administración, no del cliente que espera en la puerta: si falta existencia se
avisa y se deja constancia, pero el servicio sigue. Es el mismo criterio con que
`_visar_upsell_domain` decide qué se puede vender.
"""
from markupsafe import Markup

from odoo import _, api, fields, models

# La app de campo es en español para el técnico. La unidad de medida se lee en este
# idioma explícitamente porque el controlador es público: el idioma de la petición lo
# fija el visitante del website, y sin esto una camioneta con cinco mallas dice
# "5 Units". Mismo criterio que `SERVICES_LANG` en el agente.
CAMPO_LANG = 'es_MX'


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    visar_stock_location_id = fields.Many2one(
        'stock.location', string="Ubicación de inventario",
        domain="[('usage', '=', 'internal')]",
        help="La camioneta del técnico: de aquí sale lo que aplica y lo que vende "
             "en campo, y aquí se ve lo que le queda. Por convención es una "
             "ubicación propia bajo el almacén de rutas (VHR/Existencias/<nombre>). "
             "Sin ella el técnico no ve existencias en la app.")

    def _visar_field_location(self):
        """Ubicación de inventario del técnico, o un recordset vacío.

        En sudo: la app de campo corre como público y `stock.location` no es
        legible para ese usuario.
        """
        self.ensure_one()
        return self.sudo().visar_stock_location_id


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Mismo criterio que `visar_upsell_ok` (ver `product_template.py`): un flag
    # propio y NO la categoría, porque la categoría es contable y dejar de ofrecer
    # un producto no debería obligar a moverle las cuentas.
    visar_consumible_ok = fields.Boolean(
        string="Insumo aplicable en campo", default=False,
        help="El técnico puede registrar este producto como CONSUMIDO en la hoja de "
             "trabajo (plaguicidas, cebos, material). Se descuenta de su ubicación "
             "al cerrar el servicio; no se le cobra al cliente.")

    @api.model
    def _visar_consumible_domain(self):
        """Dominio de los insumos registrables en la hoja de trabajo.

        Se exige `is_storable`: un producto que Odoo no cuenta no puede descontarse
        de ninguna ubicación, y ofrecerlo aquí prometería un control de existencias
        que no existe.
        """
        return [
            ('visar_consumible_ok', '=', True),
            ('is_storable', '=', True),
        ]


class StockLocationVisar(models.Model):
    _inherit = 'stock.location'

    def _visar_on_hand(self, products):
        """`{product_id: cantidad a mano}` en esta ubicación (incluye las hijas).

        A mano y no "libre": al técnico le importa lo que trae en la camioneta, no
        lo que alguna reserva vieja tenga apartado. Los productos sin existencia
        NO salen en el diccionario (el llamador decide si los esconde o los pinta
        en cero).
        """
        self.ensure_one()
        if not products:
            return {}
        quants = self.env['stock.quant'].sudo()._read_group(
            [('location_id', 'child_of', self.id),
             ('product_id', 'in', products.ids)],
            groupby=['product_id'], aggregates=['quantity:sum'])
        return {product.id: cantidad for product, cantidad in quants if cantidad}


class ProductProductVisar(models.Model):
    _inherit = 'product.product'

    def _visar_field_stock_label(self, cantidad):
        """Cómo se le dice al técnico cuánto le queda ("5 Unidades", "750 ml")."""
        self.ensure_one()
        entero = float(cantidad).is_integer()
        unidad = self.sudo().with_context(lang=CAMPO_LANG).uom_id.name
        return "%s %s" % (
            ('%d' % cantidad) if entero else ('%.2f' % cantidad).rstrip('0').rstrip('.'),
            unidad or _("unidades"))


class StockMoveVisar(models.Model):
    _inherit = 'stock.move'

    visar_consumo_task_id = fields.Many2one(
        'project.task', string="Servicio que lo consumió", index=True,
        ondelete='set null', copy=False,
        help="Movimiento generado al cerrar el servicio con lo que la hoja de "
             "trabajo declaró aplicado.")


class ProjectTaskConsumo(models.Model):
    _inherit = 'project.task'

    visar_consumo_move_ids = fields.One2many(
        'stock.move', 'visar_consumo_task_id', string="Consumo de inventario",
        readonly=True)
    visar_consumo_at = fields.Datetime(
        string="Consumo descontado el", readonly=True, copy=False,
        help="Cuándo salió de la ubicación del técnico lo que declaró la hoja. "
             "Se sella una sola vez: cerrar dos veces no descuenta dos veces.")

    # Parejas (producto, cantidad) que una LÍNEA de hoja puede traer. Se buscan por
    # nombre en cada modelo de línea, así que una hoja nueva que use los mismos
    # nombres queda cubierta sin tocar este código.
    _VISAR_CONSUMO_CAMPOS = (
        ('x_plaguicida_id', 'x_plaguicida_dosis'),
        ('x_consumo_producto_id', 'x_consumo_cantidad'),
    )

    def _visar_consumo_location(self):
        """Ubicación virtual a la que sale lo aplicado en el servicio.

        `usage='production'`: el insumo se consume PRODUCIENDO el servicio. No se
        usa "Inventory adjustment" (ensuciaría el informe de ajustes con consumo
        normal) ni "Customers" (no hay venta: al cliente no se le entrega el
        bidón, se le aplica el contenido).
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        Location = self.env['stock.location'].sudo()
        location = Location.search(
            [('usage', '=', 'production'),
             ('name', '=', "Consumo en servicio"),
             ('company_id', 'in', (company.id, False))], limit=1)
        if location:
            return location
        padre = Location.search(
            [('usage', '=', 'view'), ('name', '=', "Virtual Locations"),
             ('company_id', 'in', (company.id, False))], limit=1)
        return Location.create({
            'name': "Consumo en servicio",
            'usage': 'production',
            'location_id': padre.id or False,
            'company_id': company.id,
        })

    def _visar_consumo_lines(self):
        """`{product: cantidad}` declarado por la hoja de trabajo de este servicio.

        Recorre las líneas (one2many) del registro de hoja buscando las parejas de
        `_VISAR_CONSUMO_CAMPOS`. Se ignora lo que no sea un insumo almacenable: un
        id suelto en el POST no puede convertirse en un movimiento de inventario.
        """
        self.ensure_one()
        template = self.worksheet_template_id.sudo()
        model_name = template.model_id.model if template and template.model_id else None
        record = None
        if model_name and model_name in self.env:
            record = self.env[model_name].sudo().search(
                [('x_project_task_id', '=', self.id)], limit=1, order='create_date desc')
        if not record:
            # Sin hoja (o sin llenar) puede haber material capturado igualmente.
            return {
                linea.product_id: sum(
                    o.quantity for o in self.sudo().visar_consumo_ids
                    if o.product_id == linea.product_id)
                for linea in self.sudo().visar_consumo_ids
                if linea.product_id.is_storable and linea.product_id.visar_consumible_ok
                and linea.quantity > 0}
        consumo = {}
        # Sección "Consumo de material" (sobre la tarea, no sobre la hoja: ver
        # `consumo_recorrido.py`).
        for linea in self.sudo().visar_consumo_ids:
            producto = linea.product_id
            if producto.is_storable and producto.visar_consumible_ok and linea.quantity > 0:
                consumo[producto] = consumo.get(producto, 0.0) + linea.quantity
        for campo in record._fields.values():
            if campo.type != 'one2many' or campo.comodel_name not in self.env:
                continue
            lineas = record[campo.name]
            if not lineas:
                continue
            disponibles = lineas._fields
            for f_producto, f_cantidad in self._VISAR_CONSUMO_CAMPOS:
                if f_producto not in disponibles or f_cantidad not in disponibles:
                    continue
                for linea in lineas:
                    producto = linea[f_producto]
                    cantidad = linea[f_cantidad] or 0.0
                    if not producto or cantidad <= 0:
                        continue
                    if not (producto.is_storable and producto.visar_consumible_ok):
                        continue
                    consumo[producto] = consumo.get(producto, 0.0) + cantidad
        return consumo

    def _visar_consumo_post(self, employee):
        """Descuenta de la camioneta del técnico lo que la hoja declaró aplicado.

        **Nunca falla hacia afuera.** Esto corre dentro del cierre del servicio, con
        el cliente delante y la firma ya capturada: un inventario mal configurado no
        puede tumbar el cierre. Cualquier problema se registra en el log y en el
        chatter, y el servicio se cierra igual.

        Idempotente por `visar_consumo_at`: volver a cerrar no descuenta dos veces.
        """
        self.ensure_one()
        if self.visar_consumo_at:
            return self.env['stock.move']
        origen = employee._visar_field_location() if employee else None
        consumo = self._visar_consumo_lines()
        if not consumo:
            return self.env['stock.move']
        if not origen:
            self.message_post(body=_(
                "No se descontó el consumo de inventario: el técnico %s no tiene "
                "ubicación de inventario configurada.", employee.name or ''))
            return self.env['stock.move']
        destino = self._visar_consumo_location()
        Move = self.env['stock.move'].sudo()
        moves = Move.browse()
        faltantes = []
        existencias = origen._visar_on_hand(
            self.env['product.product'].browse([p.id for p in consumo]))
        for producto, cantidad in consumo.items():
            if existencias.get(producto.id, 0.0) < cantidad:
                faltantes.append((producto, cantidad, existencias.get(producto.id, 0.0)))
            moves |= Move.create({
                # Odoo 19 quitó `name` de stock.move: la referencia la computa el
                # albarán (aquí no hay) y lo legible se pone en la descripción.
                'description_picking_manual': _(
                    "Consumo en servicio %s", self.name or ''),
                'product_id': producto.id,
                'product_uom_qty': cantidad,
                'product_uom': producto.uom_id.id,
                'location_id': origen.id,
                'location_dest_id': destino.id,
                'company_id': (self.company_id or self.env.company).id,
                'visar_consumo_task_id': self.id,
            })
        moves._action_confirm()
        moves._action_assign()
        for move in moves:
            move.quantity = move.product_uom_qty
            move.picked = True
        moves._action_done()
        self.visar_consumo_at = fields.Datetime.now()
        self._visar_consumo_note(moves, faltantes, origen)
        return moves

    def _visar_consumo_note(self, moves, faltantes, origen):
        """Deja en el chatter qué salió y qué quedó en descubierto.

        El descubierto se ANOTA, no se impide: el conteo puede estar desfasado y el
        servicio ya se prestó. Administración lo cuadra con esta nota.
        """
        self.ensure_one()
        # `Markup`: `message_post` escapa un str plano y el chatter enseñaría las
        # etiquetas en crudo. Los nombres de producto van por `%s` de Markup, que
        # los escapa de uno en uno.
        lineas = Markup("").join(
            Markup("<li>%s — %s</li>") % (
                move.product_id.display_name,
                move.product_id._visar_field_stock_label(move.product_uom_qty))
            for move in moves)
        cuerpo = Markup("<p>%s</p><ul>%s</ul>") % (
            _("Consumo descontado de %(ubicacion)s:", ubicacion=origen.complete_name),
            lineas)
        if faltantes:
            aviso = Markup("").join(
                Markup("<li>%s: se aplicaron %s y había %s</li>") % (
                    producto.display_name,
                    producto._visar_field_stock_label(pedida),
                    producto._visar_field_stock_label(habia))
                for producto, pedida, habia in faltantes)
            cuerpo += Markup("<p><b>%s</b></p><ul>%s</ul>") % (
                _("Quedó en negativo: el conteo no coincidía con lo aplicado."),
                aviso)
        self.message_post(body=cuerpo)

    # ------------------------------------------------------------------
    # Entrega de lo vendido en campo
    # ------------------------------------------------------------------
    def _visar_entrega_upsell(self, employee):
        """Valida la entrega de los adicionales, SACÁNDOLOS de la camioneta.

        Hasta ahora esto no pasaba: vender en campo creaba un albarán que nadie
        validaba nunca (43 en "Preparado" el 23-sep-2026) y encima desde el almacén
        central, no desde la camioneta del técnico. El producto salía de la casa del
        cliente en la mano del técnico y el inventario no se enteraba.

        Se mueven SOLO las líneas del adicional (`_visar_upsell_lines`), no el
        albarán completo: en el pedido original pueden convivir líneas del servicio
        contratado que no entrega el técnico.

        Igual que el consumo, deja constancia y no se interpone en el cierre.
        """
        self.ensure_one()
        origen = employee._visar_field_location() if employee else None
        if not origen:
            return self.env['stock.move']
        moves = self._visar_upsell_lines().sudo().move_ids.filtered(
            lambda m: m.state not in ('done', 'cancel') and m.product_id.is_storable)
        if not moves:
            return self.env['stock.move']
        moves._do_unreserve()
        moves.write({'location_id': origen.id})
        moves._action_assign()
        for move in moves:
            move.quantity = move.product_uom_qty
            move.picked = True
        moves._action_done()
        self.message_post(body=_(
            "Entrega de adicionales descontada de %(ubicacion)s: %(productos)s",
            ubicacion=origen.complete_name,
            productos=", ".join(
                "%s x%s" % (m.product_id.display_name, int(m.product_uom_qty))
                for m in moves)))
        return moves
