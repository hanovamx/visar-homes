# -*- coding: utf-8 -*-
"""Aplicar en el runtime lo que se acaba de guardar, sin esperar al TTL.

El runtime cachea lo que lee de Odoo —el prompt y las memorias de ruta con
`RuntimeConfigCache`, el catalogo y las notas de negocio con `CatalogCache`—
durante 15 minutos. Es lo correcto para servir: mejor contestar con una config
de hace minutos que caerse porque Odoo tardo.

Pero para quien edita es un cuarto de hora de duda. La forma habitual de
resolverlo era abrir una consola y hacer `curl` contra el runtime, que es tanto
como decir que el prompt no se puede editar sin un desarrollador —justo lo que
la fase 2a venia a quitar—. El sintoma tipico es peor que la espera: se cambia
una frase, se prueba en el chat, sale la de antes, y se concluye que el cambio
"no se guardo".

Este boton hace las dos llamadas de refresco y **cuenta lo que paso de verdad**.
Un boton que dijera "aplicado" pase lo que pase seria peor que no tenerlo.

Dos cosas que no son evidentes:

* **El registro ya esta guardado cuando esto corre.** El cliente web guarda el
  formulario en una peticion y llama al boton en OTRA, asi que el runtime lee
  datos ya confirmados. Si algun dia el boton se llamara dentro de la misma
  transaccion del guardado, el runtime leeria lo de antes y el boton mentiria.
* **Odoo se llama a si mismo, en redondo.** El refresco hace que el runtime
  pida la config a Odoo por RPC mientras este proceso esta ocupado atendiendo
  el clic. Con `workers = 2` hay sitio para las dos, pero por eso el tiempo de
  espera es corto y por eso un vencimiento se cuenta como "no se pudo
  confirmar" y no como un fallo: puede que si se aplicara.
"""
import logging

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# El runtime escucha solo en loopback (`--host 127.0.0.1`), asi que no hay
# secreto que proteger aqui: quien puede llegar al puerto ya esta dentro del
# servidor. Se deja como parametro para no hornear el puerto.
VISAR_RUNTIME_URL_PARAM = 'visar_whatsapp_agent.runtime_url'
VISAR_RUNTIME_URL_DEFAULT = 'http://127.0.0.1:8000'

# Que se refresca, y como se llama en castellano para poder contarlo.
_VISAR_REFRESCOS = (
    ('/debug/runtime/refresh', "el prompt y las memorias de ruta"),
    ('/debug/catalog/refresh', "el catalogo y las notas de negocio"),
)

# Corto a proposito: ver la nota del redondel en el docstring.
_VISAR_TIMEOUT = 10


class VisarAgentRuntimeMixin(models.AbstractModel):
    """Da el boton "Aplicar ahora" a los modelos cuya config cachea el runtime."""

    _name = 'visar.agent.runtime.mixin'
    _description = "Aplicar la configuracion en el runtime"

    @api.model
    def _visar_runtime_url(self):
        """La direccion del runtime, del parametro o la de fabrica.

        Borrar el parametro no rompe el boton: `get_param` devuelve el valor por
        defecto cuando la clave falta o esta vacia, y el runtime esta en
        loopback en todos los despliegues. Lo que si deja sin direccion es un
        valor en blanco a base de espacios, y de eso avisa quien llama.
        """
        parametro = self.env['ir.config_parameter'].sudo().get_param(
            VISAR_RUNTIME_URL_PARAM, VISAR_RUNTIME_URL_DEFAULT)
        return (parametro or '').strip().rstrip('/')

    def action_visar_aplicar_ahora(self):
        """Fuerza el refresco de las cachés del runtime y dice como fue."""
        base = self._visar_runtime_url()
        if not base:
            raise UserError(_(
                "No hay direccion del runtime. Ponla en Ajustes → Tecnico → "
                "Parametros del sistema, en la clave %s.",
                VISAR_RUNTIME_URL_PARAM))

        hechos = []
        for ruta, que in _VISAR_REFRESCOS:
            url = base + ruta
            try:
                respuesta = requests.post(url, timeout=_VISAR_TIMEOUT)
                respuesta.raise_for_status()
            except requests.exceptions.Timeout as exc:
                # No es un fallo: el refresco pudo aplicarse y ser la respuesta
                # la que no llego. Decir "aplicado" seria inventar y decir
                # "fallo" tambien, asi que se dice lo unico que se sabe.
                _logger.warning("Aplicar ahora: %s no contesto a tiempo", url)
                raise UserError(_(
                    "El runtime no contesto en %(seg)s segundos al refrescar "
                    "%(que)s. Puede que si se haya aplicado; compruebalo en el "
                    "chat o vuelve a intentarlo.\n\n%(url)s",
                    seg=_VISAR_TIMEOUT, que=que, url=url)) from exc
            except requests.exceptions.RequestException as exc:
                _logger.warning("Aplicar ahora: %s fallo (%s)", url, exc)
                raise UserError(_(
                    "No se pudo aplicar %(que)s: el runtime no respondio.\n\n"
                    "%(url)s\n%(motivo)s\n\nSi el servicio esta caido, la "
                    "configuracion se aplicara sola cuando vuelva (o en 15 "
                    "minutos, cuando caduque su cache).",
                    que=que, url=url, motivo=exc)) from exc
            hechos.append(que)

        _logger.info("Aplicar ahora: %s refrescado desde %s",
                     ", ".join(hechos), base)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Aplicado en el agente"),
                'message': _(
                    "Recargado en el runtime: %s. Una conversacion que este a "
                    "mitad de una pregunta la termina como empezo.",
                    "; ".join(hechos)),
            },
        }
