# -*- coding: utf-8 -*-
"""Código de descuento o tarjeta de regalo en la liga de pago (REQ-7600).

La liga que manda el agente de WhatsApp abre el portal de la orden
(`/my/orders/<id>?payment_amount=…`), no el carrito. El campo "Tarjeta de regalo
o código de descuento" de Odoo vive solo en el carrito (`website_sale_loyalty`,
sobre `request.cart`), así que en el portal el cliente no tenía dónde capturarlo.
Aquí está la lógica; el controlador (`controllers/portal_cupon.py`) solo la
expone.

Tres cosas que el nativo resuelve en el carrito y el portal no, y que por eso
se hacen aquí:

  * **Aplicar** igual que el carrito: `_try_apply_code` + `_apply_program_reward`
    + `_update_programs_and_rewards`.
  * **Revalidar** antes de cobrar: el carrito lo hace en
    `_validate_transaction_for_order`; la ruta de pago del portal no. Va en
    `payment_transaction.py`.
  * **Total en cero**: el carrito confirma la orden sin pago; el portal no
    enseña botón y la reserva nunca se volvía cita. Antes de esto oficina lo
    resolvía a mano (S00029, 23-nov-2026).

Pólizas fuera (decisión de Luis, 23-nov-2026): un descuento sobre una
suscripción se repetiría cada periodo. Se restringe también por lista de precios
en el programa, pero eso solo no basta: `_visar_poliza_pricelist` cae a la lista
de la zona cuando un plan no tiene la suya.
"""
from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_amount
from odoo.tools.sql import escape_psql

# Intentos fallidos seguidos antes de bloquear el campo, y por cuánto tiempo.
# La ruta es pública: sin tope, la página sería un adivinador de códigos.
CUPON_MAX_FALLOS = 5
CUPON_BLOQUEO = timedelta(hours=1)


class _CuponRechazado(Exception):
    """Interno: deshace el savepoint y regresa el mensaje para el cliente.

    `fallo` = cuenta para el bloqueo. Solo un código que no existe: lo que se
    quiere frenar es adivinar códigos, no al cliente que repite el suyo.
    """

    def __init__(self, mensaje, fallo=False):
        super().__init__(mensaje)
        self.fallo = fallo


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    visar_cupon_fallos = fields.Integer(
        string="Códigos fallidos en la liga", copy=False, default=0,
        help="Intentos fallidos seguidos de capturar un código en la liga de "
             "pago. Se reinicia al aplicar uno bueno.")
    visar_cupon_bloqueo_hasta = fields.Datetime(
        string="Códigos bloqueados hasta", copy=False,
        help="Tras demasiados intentos fallidos la liga deja de aceptar códigos "
             "hasta esta hora.")

    # ------------------------------------------------------------------
    # Qué puede hacer el cliente
    # ------------------------------------------------------------------
    def _visar_cupon_aplica(self):
        """¿La liga enseña el campo? Solo mientras la orden espera su pago."""
        self.ensure_one()
        return bool(
            self.state in ('draft', 'sent')
            and self.require_payment
            and not self.is_subscription
            and not self.is_expired
            and not self._visar_cupon_cobro_en_curso()
        )

    def _visar_cupon_cobro_en_curso(self):
        """Un cobro ya salió: moverle el total ahora lo descuadraría."""
        self.ensure_one()
        return bool(self.transaction_ids.filtered(
            lambda tx: tx.state in ('pending', 'authorized', 'done')))

    def _visar_cupon_tiene_recompensas(self):
        self.ensure_one()
        return bool(self.order_line.reward_id or self.applied_coupon_ids
                    or self.code_enabled_rule_ids)

    def _visar_cupon_lineas(self):
        """Descuentos que el cliente puso con un código y que puede quitar.

        Las promociones automáticas no: si se quitaran, el carrito las vuelve a
        poner sola y aquí se quedarían quitadas.
        """
        self.ensure_one()
        return self.order_line.filtered(lambda line: line.reward_id and (
            line.coupon_id in self.applied_coupon_ids
            or line.reward_id.program_id in self.code_enabled_rule_ids.program_id
        ))

    def _visar_cupon_puede_confirmar_sin_pago(self):
        """Total en cero POR un código: se confirma sin pasar por el cobro.

        Solo por un código. Una orden en cero por otra razón (un descuento puesto
        a mano en oficina) la confirma oficina, como hasta ahora.
        """
        self.ensure_one()
        return bool(
            self._visar_cupon_aplica()
            and self.order_line.filtered('reward_id')
            and self.currency_id.is_zero(self.amount_total)
        )

    # ------------------------------------------------------------------
    # Acciones (llamadas desde el portal, en sudo)
    # ------------------------------------------------------------------
    def _visar_cupon_aplicar(self, code):
        """Aplica un código como lo haría el carrito.

        Devuelve `(ok, mensaje)`. Nunca deja la orden a medias: todo va en un
        savepoint, y si el código no termina en un descuento se deshace.
        """
        self.ensure_one()
        code = (code or '').strip()
        if not code:
            return False, _("Escribe tu código.")
        if not self._visar_cupon_aplica():
            return False, _("Esta orden ya no acepta códigos.")
        if (self.visar_cupon_bloqueo_hasta
                and self.visar_cupon_bloqueo_hasta > fields.Datetime.now()):
            return False, _(
                "Demasiados intentos. Inténtalo más tarde o escríbenos por "
                "WhatsApp.")
        try:
            with self.env.cr.savepoint():
                aplicado = self._visar_cupon_aplicar_codigo(code)
        except _CuponRechazado as rechazo:
            if rechazo.fallo:
                self._visar_cupon_registrar_fallo()
            return False, str(rechazo)
        except UserError as error:
            return False, error.args[0]

        if self.visar_cupon_fallos:
            self.visar_cupon_fallos = 0
        self.message_post(
            body=_("El cliente aplicó %s desde la liga de pago. Nuevo total: %s.",
                   aplicado, self._visar_cupon_monto(self.amount_total)),
            author_id=self.partner_id.id,
            subtype_xmlid='mail.mt_note',
        )
        return True, _("Código aplicado.")

    def _visar_cupon_aplicar_codigo(self, code):
        """El cuerpo de `/shop/pricelist` + `_apply_reward`, sin carrito.

        Lanza `_CuponRechazado` en cualquier salida que no deje un descuento
        nuevo en la orden. Devuelve cómo nombrar lo aplicado en la nota.
        """
        code = self._visar_cupon_normalizar(code)
        antes = self._visar_cupon_pares()
        status = self._try_apply_code(code)
        if 'error' in status:
            raise _CuponRechazado(status['error'],
                                  fallo=bool(status.get('not_found')))

        # Un código, una recompensa: es lo que la liga sabe aplicar sin
        # preguntar. Varias opciones (o una que pide elegir producto) piden una
        # pantalla de selección que hoy no hace falta: ningún programa de Visar
        # la usa. Se dice en vez de elegir por el cliente.
        varias = _("Ese código tiene varias opciones. Escríbenos por WhatsApp y "
                   "te lo aplicamos.")
        if len(status) > 1:
            raise _CuponRechazado(varias)
        if status:
            coupon, rewards = next(iter(status.items()))
            if len(rewards) != 1 or rewards.multi_product:
                raise _CuponRechazado(varias)
            resultado = self._apply_program_reward(rewards, coupon)
            if 'error' in resultado:
                raise _CuponRechazado(resultado['error'])
        self._update_programs_and_rewards()

        # Por par (recompensa, cupón) y no por línea: un descuento global que
        # reemplaza a otro REUTILIZA la línea vieja, y dos tarjetas del mismo
        # programa comparten recompensa.
        nuevos = self._visar_cupon_pares() - antes
        if not nuevos:
            raise _CuponRechazado(_("Ese código no aplica a esta orden."))
        nuevas = self.order_line.filtered(
            lambda l: (l.reward_id.id, l.coupon_id.id) in nuevos)

        tarjeta = nuevas.coupon_id.filtered(
            lambda c: c.code == code and c.program_id.program_type != 'promo_code')
        if tarjeta:
            # La nota la leen todos los internos: una tarjeta de regalo es dinero,
            # se nombra por sus últimos 4, como hace el portal nativo.
            return _("la tarjeta …%s (%s)", code[-4:], tarjeta[:1].program_id.name)
        return _("el código %s (%s)", code, nuevas[:1].reward_id.program_id.name)

    def _visar_cupon_normalizar(self, code):
        """El código como está guardado, aunque el cliente cambie mayúsculas.

        Odoo busca el código tal cual. Las tarjetas de regalo llevan hexadecimal
        en minúsculas (`044x-1a2b-…`) y los cupones de Visar van en mayúsculas
        (FF2026): el celular o el cliente cambian una por otra y el código bueno
        salía "inválido". Solo se corrige si hay UNA coincidencia sin distinguir
        mayúsculas; si no, se deja como lo escribió.
        """
        fuentes = (
            (self.env['loyalty.rule'].sudo(), [('mode', '=', 'with_code')]),
            (self.env['loyalty.card'].sudo(), []),
        )
        for modelo, dominio in fuentes:
            if modelo.search_count(dominio + [('code', '=', code)], limit=1):
                return code
        for modelo, dominio in fuentes:
            codigos = set(modelo.search(
                dominio + [('code', '=ilike', escape_psql(code))], limit=3,
            ).mapped('code'))
            if len(codigos) == 1:
                return codigos.pop()
        return code

    def _visar_cupon_pares(self):
        return {(line.reward_id.id, line.coupon_id.id)
                for line in self.order_line if line.reward_id}

    def _visar_cupon_registrar_fallo(self):
        fallos = self.visar_cupon_fallos + 1
        if fallos >= CUPON_MAX_FALLOS:
            self.write({
                'visar_cupon_fallos': 0,
                'visar_cupon_bloqueo_hasta': fields.Datetime.now() + CUPON_BLOQUEO,
            })
        else:
            self.visar_cupon_fallos = fallos

    def _visar_cupon_quitar(self, line_id):
        """Quita un descuento puesto con código. Devuelve `(ok, mensaje)`."""
        self.ensure_one()
        if not self._visar_cupon_aplica():
            return False, _("Esta orden ya no acepta cambios.")
        linea = self._visar_cupon_lineas().filtered(lambda l: l.id == line_id)
        if not linea:
            return False, _("Ese descuento ya no está en la orden.")
        nombre = linea.reward_id.program_id.name
        # El `unlink` nativo de la línea de recompensa limpia solo el cupón y la
        # regla del código; recalcular después reordena los demás descuentos.
        linea.unlink()
        self._update_programs_and_rewards()
        self.message_post(
            body=_("El cliente quitó el descuento %s desde la liga de pago. Nuevo "
                   "total: %s.", nombre, self._visar_cupon_monto(self.amount_total)),
            author_id=self.partner_id.id,
            subtype_xmlid='mail.mt_note',
        )
        return True, _("Descuento quitado.")

    def _visar_cupon_confirmar_sin_pago(self):
        """Confirma la cita cuando un código cubrió el total.

        Lo mismo que el carrito hace con un total en cero
        (`shop_payment_validate` → `_validate_order`), más las dos guardias que
        el cobro de la liga ya tiene: el descuento sigue valiendo y el horario
        sigue siendo del cliente. Confirmar dispara todo lo que dispara un pago:
        la cita, la tarea de campo y el WhatsApp de confirmación cuelgan de
        `_action_confirm`, no del cobro.

        Devuelve `(ok, mensaje)`.
        """
        self.ensure_one()
        if not self._visar_cupon_puede_confirmar_sin_pago():
            return False, _("Esta orden no se puede confirmar sin pago.")
        try:
            with self.env.cr.savepoint():
                self._update_programs_and_rewards()
                if not self.currency_id.is_zero(self.amount_total):
                    raise _CuponRechazado(_(
                        "El descuento cambió y la orden quedó con saldo. Revisa el "
                        "total antes de seguir."))
                bookings = self.order_line.calendar_booking_ids
                if (any(not b._visar_ensure_hold_for_payment() for b in bookings)
                        or bookings._filter_unavailable_bookings()):
                    raise _CuponRechazado(_(
                        "El horario que habías apartado ya no está disponible. "
                        "Escríbenos por WhatsApp y elegimos otro."))
                # Mismo contexto que el cobro en línea (`visar_base`): la regla de
                # "lista de precios obligatoria" es para quien confirma a mano en
                # oficina, no para el cliente cerrando su propia reserva.
                self.with_context(visar_confirmacion_por_pago=True)._validate_order()
        except _CuponRechazado as rechazo:
            return False, str(rechazo)
        except UserError as error:
            return False, error.args[0]

        self.message_post(
            body=_("El cliente confirmó desde la liga de pago sin cobro: su código "
                   "cubrió el total."),
            author_id=self.partner_id.id,
            subtype_xmlid='mail.mt_note',
        )
        return True, _("¡Listo! Tu cita quedó confirmada.")

    def _visar_cupon_saldo_insuficiente(self):
        """Lo que `sale_loyalty.action_confirm` revisa antes de confirmar.

        Una tarjeta (o código) que ya se consumió en otra orden deja puntos
        negativos aquí, y la confirmación lanzaría ValidationError.
        """
        self.ensure_one()
        cupones = (self.applied_coupon_ids | self.coupon_point_ids.coupon_id
                   | self.order_line.coupon_id)
        return any(self._get_real_points_for_coupon(c) < 0 for c in cupones)

    def _visar_cupon_avisar_cobro_sin_confirmar(self, tx):
        """Nota y actividad: entró dinero y la orden no se puede confirmar.

        Una sola vez por orden: el post-proceso del pago vuelve a pasar por aquí
        (autorizado → hecho, o el cron) y a oficina le basta un aviso.
        """
        admin = self.env.ref('base.user_admin', raise_if_not_found=False)
        resumen = _("Cobrado y sin confirmar: revisar descuento")
        for orden in self:
            if orden.activity_ids.filtered(lambda a: a.summary == resumen):
                continue
            nota = _(
                "Se cobró %(monto)s (%(ref)s), pero la orden NO se pudo confirmar: "
                "el código o la tarjeta de regalo ya no tiene saldo suficiente "
                "(probablemente se usó en otra orden). Ajusta el descuento y "
                "confirma a mano, o reembolsa.",
                monto=format_amount(self.env, tx.amount, tx.currency_id),
                ref=tx.reference)
            orden.message_post(body=nota, subtype_xmlid='mail.mt_note')
            orden.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=resumen,
                note=nota,
                user_id=(orden.user_id or admin).id,
            )

    def _visar_cupon_refrescar(self):
        """Al abrir la liga, el descuento se recalcula (como el carrito al pintar).

        Si la tarjeta se gastó en otra orden o el código venció, el cliente lo ve
        ANTES de pagar, no al rechazársele el cobro.
        """
        self.ensure_one()
        if not (self._visar_cupon_aplica()
                and self._visar_cupon_tiene_recompensas()):
            return False
        self._update_programs_and_rewards()
        return True

    def _visar_cupon_tope_liga(self, payment_amount, con_descuento):
        """La liga trae el monto de cuando se generó; con descuento, ya no es.

        Dos direcciones, y las dos importan:
          * el total BAJÓ (se aplicó un código): la liga diría más de lo que se
            cobra;
          * el total SUBIÓ (la tarjeta se gastó en otra orden y el recálculo la
            quitó): el monto de la liga queda bajo el anticipo y el portal nativo
            responde 404 ("amount lower than the prepayment") — liga muerta.

        Fuera del rango [anticipo, total] se usa el total. Dentro se respeta: es
        un anticipo que oficina eligió.
        """
        self.ensure_one()
        if not con_descuento or self.state not in ('draft', 'sent'):
            return payment_amount
        try:
            monto = float(payment_amount)
        except (TypeError, ValueError):
            return payment_amount
        cur = self.currency_id
        if (cur.compare_amounts(monto, self.amount_total) > 0
                or cur.compare_amounts(
                    monto, self._get_prepayment_required_amount()) < 0):
            return self.amount_total
        return payment_amount

    def _visar_cupon_monto_liga(self, monto_liga, total_antes):
        """Monto con el que se regresa a la liga tras aplicar o quitar un código.

        Si la liga cobraba el total, ahora cobra el total nuevo. Si era un
        anticipo de oficina, se conserva (topado al total). `None` = sin monto.
        """
        self.ensure_one()
        try:
            monto = float(monto_liga)
        except (TypeError, ValueError):
            return None
        cur = self.currency_id
        if self.state not in ('draft', 'sent') or cur.is_zero(self.amount_total):
            return None
        if cur.compare_amounts(monto, total_antes) >= 0:
            return self.amount_total
        nuevo = min(monto, self.amount_total)
        if cur.compare_amounts(nuevo, self._get_prepayment_required_amount()) < 0:
            return self.amount_total
        return nuevo

    def _visar_cupon_monto(self, monto):
        return format_amount(self.env, monto, self.currency_id)
