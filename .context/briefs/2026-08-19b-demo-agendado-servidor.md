# Encargo: demo ejecutable del agendado por WhatsApp (para enseñar avance)

> **Objetivo: una liga de pago real, generada recorriendo el mismo cuestionario
> que va a conducir WhatsApp.** No es una maqueta: son los métodos RPC de
> verdad, contra la base de verdad.
>
> **Tiempo estimado: 20–30 min.** Contexto:
> [`33-whatsapp-agendado-design.md`](../33-whatsapp-agendado-design.md) §10.4 y §10.5.

## Antes de nada: qué está y qué no

| Pieza | Estado |
|---|---|
| Apartado de horario, reserva, pedido, **liga de pago**, hand-off | ✅ verificado en servidor (3 rondas, 93 pruebas) |
| **El cuestionario por RPC** (`agent_booking_step`) | ⚠️ escrito, **sin verificar en servidor** — es lo que se prueba aquí |
| Persistencia del estado del runtime (SQLite) | ✅ hecho, 243 pruebas verdes en local |
| **La conversación de WhatsApp que lo conduce** | ❌ **no construido todavía** |

Traducido: **hoy no se puede reservar escribiendo por WhatsApp.** Lo que sí se
puede es recorrer el cuestionario completo por RPC —los mismos métodos que el
runtime va a llamar— y terminar con una liga de pago que abre y cobra. Es la
tubería entera menos la capa de chat.

Para enseñar avance eso es lo que hay, y no es poco: la liga se puede abrir en el
navegador delante de quien sea.

## Preparación

```bash
cd /opt/custom && git pull
sudo systemctl restart odoo     # basta reiniciar: es Python puro, NO hace falta -u
```

> Si algo pide `-u`, **para y avísame**: significa que se coló un cambio que no
> debía. Los módulos tocados son `visar_appointment` y `visar_whatsapp_agent`.

## Parte 1 — el cuestionario, de principio a fin (SOLO LECTURA)

Seguro en cualquier base, incluida una copia. No escribe nada.

`odoo shell -d <BASE>` y pega esto:

```python
Tools = env['visar.agent.tools'].sudo()
CP = '64000'   # cámbialo si sabes de otro que esté en cobertura

def responder(state):
    """Contesta el paso actual eligiendo la primera opción razonable."""
    step, o = state['step'], state['options']
    def primera(sin_valoracion=True):
        for opt in o['options']:
            if sin_valoracion and opt.get('is_valuation'):
                continue
            return opt['value']
        return None
    if step == 'services':   return {'group_ids': [primera()]}
    if step == 'motivo':     return {'motivo': 'correctivo'}
    if step == 'plagas':     return {'servicio_plaga': [primera()]}
    if step == 'cobertura':  return {'cobertura': 'interior'}
    if step.startswith('group_'): return {'dimension_ids': [primera()]}
    if step == 'exterior':   return {'band_id': primera()}
    if step == 'dimensiones':
        return {s['field_name']: s['options'][0]['value'] for s in o['sections']}
    if step == 'interior':
        ans = {'interior_mode': 'sabe'}
        ans.update({s['field_name']: s['options'][0]['value'] for s in o['sections']})
        return ans
    if step == 'address':
        return {'street': 'Ruiz Cortines', 'ext_num': '123',
                'neighborhood': 'Centro', 'zip': CP}
    if step == 'extras':     return {'extra_ids': []}
    if step == 'poliza':     return {'plan_id': False}
    return {}

booking, state = {}, Tools.agent_booking_step({'booking': {}})
for i in range(20):
    print("\n--- PASO %-12s (%s)" % (state['step'], state['options']['kind']))
    print("    %s" % state['options'].get('title'))
    for opt in state['options']['options'][:6]:
        print("      - %s" % opt['label'])
    for s in state['options'].get('sections') or []:
        print("      [%s] %d rangos" % (s['label'], len(s['options'])))
    if state['done'] or state['step'] in ('valuation', 'schedule'):
        break
    state = Tools.agent_booking_step({
        'booking': {'mode': 'wizard', **{k: state[k] for k in
                    ('selections', 'zone_id', 'items', 'delivery_address',
                     'extras_accepted') if state.get(k)}},
        'step': state['step'], 'answer': responder(state)})
    if state['error']:
        print("    ERROR: %s" % state['error']['message']); break

print("\n=== FIN: paso=%s  requiere_valoracion=%s" % (state['step'], state['requires_valuation']))
print("=== selections =", state['selections'])
booking = {'mode': 'wizard', 'selections': state['selections'],
           'zone_id': state['zone_id'], 'items': state['items'],
           'delivery_address': state['delivery_address'],
           'extras_accepted': state['extras_accepted']}
```

**Qué tiene que pasar:** los pasos avanzan solos hasta `schedule`, cada uno con
su pregunta y sus opciones reales del catálogo, y `selections` sale lleno.

> El script está escrito **sin haberlo podido correr** (no tengo Odoo local). Si
> algo peta por un nombre de campo, arréglalo y **dime qué era** — eso es
> justamente lo que quiero saber.

## Parte 2 — la liga de pago (ESTO SÍ ESCRIBE)

Crea cliente, reserva, pedido y apartado. Dos opciones:

- **Copia (`visar-scratch`)** — seguro, pero la liga **no abre** desde fuera: el
  sitio público sirve `visar-db`.
- **`visar-db`** — la liga **sí abre y se puede enseñar**. Hoy no hay clientes
  reales ahí, así que es asumible; limpia al final (abajo).

Con el `booking` de la Parte 1 todavía en el shell:

```python
TEL = '528112345678'   # un número de prueba, NO el de un cliente real

dias = Tools.agent_available_days({**booking, 'phone': TEL})
print("Días con hueco:", dias['days'][:5], " min_hours:", dias['min_hours'])

slots = Tools.agent_day_slots({**booking, 'phone': TEL, 'date': dias['days'][0]['date']})
print("Horarios:", [s['start'] for s in slots['slots'][:5]])

s = slots['slots'][0]
print("Apartado:", Tools.agent_hold_slot({
    'phone': TEL, 'resource_id': s['resource_ids'][0],
    'start': s['start'], 'stop': s['stop']}))

res = Tools.agent_prepare_booking({
    **booking, 'phone': TEL, 'name': 'Cliente Demo WhatsApp',
    'slot': {'start': s['start'], 'stop': s['stop']}})
print("\n=== LIGA DE PAGO ===\n%s\n" % res.get('payment_url'))
print("Total: %s %s   vence: %s" % (res.get('total'), res.get('currency'), res.get('expire_at')))
```

**Lo que se enseña:** abre `payment_url` en el navegador (sin sesión, en una
ventana privada). Debe cargar el pedido con el importe correcto y su botón de
pago. Esa liga es la que el cliente recibiría por WhatsApp.

`min_hours = 24` significa que **no hay horarios para hoy**: es correcto, no un
fallo.

### Limpieza (si corriste en `visar-db`)

```python
env['visar.slot.hold'].sudo().search([('owner_key','like','%2345678')]).unlink()
env['sale.order'].sudo().browse(res['order_id']).unlink()
env['calendar.booking'].sudo().browse(res['booking_id']).unlink()
env.cr.commit()
```

Si llegaste a **pagar** la liga con el proveedor *Demo*, ya se creó cita y tarea
FSM: bórralas también, o déjalas y avísame — para enseñar avance puede convenir
dejar una reserva completa a la vista.

## Qué me interesa que me reportes

1. ¿Llegó la Parte 1 hasta `schedule` sin errores? ¿Qué pasos salieron y en qué
   orden?
2. ¿Abrió la liga? Importe y captura si puedes.
3. Cualquier cosa que hayas tenido que arreglar del script.
4. **Solo si te sobra tiempo:** que contestar `extras` no vuelva a preguntar
   `extras` (fue un bug que encontré escribiéndolo y quiero saber si quedó bien
   cerrado), y `correctivo → termitas`, que debe cortar a `valuation` saltándose
   las mediciones.

La verificación seria y completa es el otro encargo
([`2026-08-19-verificacion-motor-de-flujos.md`](./2026-08-19-verificacion-motor-de-flujos.md));
esto es la versión corta para poder enseñar algo hoy.
