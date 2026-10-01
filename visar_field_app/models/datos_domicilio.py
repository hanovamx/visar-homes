# -*- coding: utf-8 -*-
"""Sube de la hoja de trabajo al DOMICILIO lo que el técnico ya escribió.

El técnico de la visita de valoración ya captura dos cosas que valen para
siempre y que hoy se quedan sepultadas dentro de la hoja de **una** visita:

    x_tipo_inmueble         Bodega · Casa · Departamento · Local comercial · Oficina
    x_restricciones_acceso  "Portón con candado, mascota agresiva, horario limitado"

Nadie las vuelve a ver salvo que abra esa hoja. Ni oficina al atender una llamada,
ni el agente cuando el cliente escribe otra vez, ni el técnico de la visita
siguiente —que vuelve al mismo portón sin saber del candado—.

Esto las cuelga del **contacto de entrega** (`visar.partner.fact`, ámbito
`domicilio`), que es el registro que `_visar_apply_delivery_address` ya crea y
reutiliza por dirección. No se cuelgan del cliente a propósito: un cliente agenda
para su casa y para su local, y «es un departamento» sobre la persona no es un
dato incompleto, es falso.

**No se inventa nada y no lo escribe ningún modelo.** Es una copia de un dato que
una persona ya escribió, con `origen='hoja'` — y por eso una corrección manual
(`origen='persona'`) nunca se pisa: la derivación corre en CADA guardado de la
hoja, así que sin esa regla lo corregido duraría hasta el siguiente guardado del
técnico.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)

# Campo de la hoja -> ranura del domicilio. Añadir uno es una línea aquí, siempre
# que la ranura exista en `VISAR_FACT_SLOTS` con ámbito `domicilio`.
VISAR_HOJA_A_DOMICILIO = {
    'x_tipo_inmueble': 'tipo_inmueble',
    'x_restricciones_acceso': 'acceso',
}
# El "otro" de un campo de selección: si el técnico eligió Otro y escribió qué,
# vale más su texto que la palabra "Otro".
VISAR_HOJA_OTRO = {
    'x_tipo_inmueble': 'x_tipo_inmueble_otro',
}


class ProjectTaskDatosDomicilio(models.Model):
    _inherit = 'project.task'

    def _visar_domicilio_de_la_visita(self):
        """El contacto de entrega al que pertenece esta visita, o vacío.

        Sale del pedido (`visar_service_partner_id`), que es donde Visar fija la
        dirección de servicio. **No se cae al `partner_id` de la tarea**: ese es
        el CLIENTE, y escribirle datos del lugar es justo el error que este
        módulo existe para no cometer.
        """
        self.ensure_one()
        order = self.sale_order_id.sudo()
        return order.visar_service_partner_id if order else \
            self.env['res.partner'].browse()

    def _visar_datos_domicilio_sync(self):
        """Copia al domicilio lo que la hoja ya sabe. NUNCA levanta.

        Va colgada del guardado de la hoja, con el técnico de pie en casa del
        cliente: si esto fallara, lo que no puede pasar es que se caiga el
        guardado. Un dato de contexto vale muchísimo menos que el parte de un
        servicio.
        """
        self.ensure_one()
        try:
            return self._visar_datos_domicilio_sync_ahora()
        except Exception:  # noqa: BLE001 - un dato de contexto no tumba un guardado
            _logger.exception(
                "No se pudieron subir los datos del domicilio de la tarea %s", self.id)
            return 0

    def _visar_datos_domicilio_sync_ahora(self):
        """El trabajo de `_visar_datos_domicilio_sync`, sin la red de seguridad."""
        self.ensure_one()
        domicilio = self._visar_domicilio_de_la_visita()
        if not domicilio:
            # Sin dirección de servicio no hay dónde colgarlo. Pasa en visitas
            # que no vienen de una reserva, y no es un problema que avisar: no
            # hay nada que el usuario pueda arreglar.
            return 0
        hoja = self._visar_quote_hoja()
        if not hoja:
            return 0
        Fact = self.env['visar.partner.fact'].sudo()
        puestos = 0
        for campo, ranura in VISAR_HOJA_A_DOMICILIO.items():
            if campo not in hoja._fields:
                continue
            valor = hoja[campo]
            if not valor:
                continue
            valor = str(valor).strip()
            otro_campo = VISAR_HOJA_OTRO.get(campo)
            if otro_campo and otro_campo in hoja._fields:
                otro = (hoja[otro_campo] or '').strip()
                # "Otro" a secas no dice nada; lo que el técnico escribió, sí.
                if otro and valor.lower().startswith('otro'):
                    valor = otro
            if Fact._visar_fact_set(domicilio, ranura, valor, origen='hoja'):
                puestos += 1
        if puestos:
            _logger.info(
                "Datos del domicilio %s actualizados desde la hoja de la tarea %s",
                domicilio.id, self.id)
        return puestos
