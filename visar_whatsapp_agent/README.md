# visar_whatsapp_agent

API de solo lectura que consume el servicio externo `visar_fastapi` (el
runtime FastAPI + LLM del agente de WhatsApp). No tiene interfaz de usuario;
es solo la superficie RPC.

## Que expone

Tres metodos `@api.model` sobre el modelo abstracto `visar.agent.tools`:

| Metodo | Entrada | Devuelve |
|---|---|---|
| `agent_catalog_snapshot()` | — | grupos, dimensiones, tramos y zonas (sin precios ni CPs) |
| `agent_resolve_zone(cp)` | codigo postal | zona y cobertura |
| `agent_quote_service(payload)` | `{cp, items:[{service_code, m2}...]}` | lineas y total |

`agent_quote_service` acepta uno o varios servicios en `items`; cada
`service_code` es un **codigo de dimension** (`FUM_INT`, `FUM_EXT`,
`MAV_JAR`), no de grupo. Tambien acepta la forma corta
`{service_code, cp, m2}` para un solo servicio. Si se manda un grupo con
varias dimensiones, la respuesta trae `needs_clarification: true` y las
opciones, sin total.

**El precio NO se reimplementa.** El metodo construye los mismos `items` que
arma el wizard web y los pasa a `appointment.type._visar_quote_booking()`, el
motor de precios que ya existe. Por eso el total del agente es, por
construccion, identico al de la web e incluye:

- la **variante combinada** de fumigacion interior + exterior (la rejilla
  zona x m2 interior x m2 exterior), que NO es la suma de cotizarlas por
  separado;
- los **descuentos de combo** entre servicios distintos;
- los **add-ons obligatorios** y los tramos incluidos sin cargo.

Por eso el modulo depende de `visar_appointment` (donde vive ese motor), no
solo de `visar_base`.

Ningun metodo acepta nombres de modelo, dominios ni SQL. Es intencional:
acota lo que el LLM puede pedir aunque le metan prompt injection.

## Seguridad

- Grupo **Agente WhatsApp / Solo lectura** con ACLs de solo lectura sobre los
  modelos del catalogo y precios. Nada de ventas, citas ni datos de clientes.
- Usuario `whatsapp_agent` (tipo *share*) en ese grupo.
- Los metodos **no** usan `sudo`: corren como el usuario que llama, asi que
  esas ACLs son el limite real. Principio de minimo privilegio.

## Puesta en marcha

1. Instalar el modulo (depende de `visar_base`).
2. Crear una **API key** para el usuario `whatsapp_agent`:
   Ajustes > Usuarios > whatsapp_agent, o iniciando sesion como el y en
   Preferencias > Seguridad de la cuenta > Nueva API key. **Usar la API key,
   no una contrasena.**
3. En `visar_fastapi/.env`:
   ```
   ODOO_FAKE=false
   ODOO_URL=http://127.0.0.1:8069
   ODOO_DB=<nombre de la base>
   ODOO_USERNAME=whatsapp_agent
   ODOO_API_KEY=<la API key del paso 2>
   ```
4. Editar las notas del negocio para el prompt (opcional):
   Ajustes > Tecnico > Parametros del sistema, clave
   `visar.agent.catalog_notes`.

## Prueba de humo por RPC

```python
import xmlrpc.client
URL, DB, USER, KEY = "http://127.0.0.1:8069", "visar", "whatsapp_agent", "<api-key>"
uid = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common").authenticate(DB, USER, KEY, {})
models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")

snap = models.execute_kw(DB, uid, KEY, "visar.agent.tools", "agent_catalog_snapshot", [])
zone = models.execute_kw(DB, uid, KEY, "visar.agent.tools", "agent_resolve_zone", ["64000"])
quote = models.execute_kw(DB, uid, KEY, "visar.agent.tools", "agent_quote_service",
                          [{"cp": "64000", "items": [
                              {"service_code": "FUM_INT", "m2": 120},
                              {"service_code": "FUM_EXT", "m2": 200}]}])
print(quote["total"], quote["message"])
```

La verificacion importante: correr `agent_quote_service` con los mismos datos
que el wizard web (mismo servicio, CP y m2, y en su caso interior+exterior o
un combo de servicios) y comparar que el total coincide.

## Pendiente (fase siguiente)

- Modelos de configuracion editables por consultores: `visar.llm.config`
  (selector de los cuatro proveedores), `visar.whatsapp.config`,
  `visar.agent.prompt` (para que el prompt base salga de aqui y no del
  codigo de `visar_fastapi`).
- Almacenamiento seguro de credenciales (hoy el token/llave del LLM vive en
  el `.env` del servicio, no en Odoo).
