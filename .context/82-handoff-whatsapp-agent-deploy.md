# Handoff — Deploy the WhatsApp AI agent (FastAPI runtime + RPC module) on the server

> **Audience:** an agent/operator with **SSH** to the host running the Odoo 19
> instance at **visar.hanova.consulting**. This doc is self-contained; you do
> **not** need the `.context/` history. Deeper background: `27-whatsapp-agent.md`
> (the Odoo module) and, once the FastAPI repo is cloned, its own
> `.context/` + `deploy/RUNBOOK.md`.
>
> **Written:** 2026-07-24. **Not yet executed** — this is the plan for an agent
> to run, not a record of a done deploy.

---

## 0. What you are deploying (and what only a human can do)

A WhatsApp AI agent, split in two halves:
- **`visar_whatsapp_agent`** — an Odoo module: read-only RPC surface
  (`visar.agent.tools`, three typed methods). **Already on `origin/main`** of
  this repo (`hanovamx/visar-homes`); you install it.
- **`visar_fastapi`** — an external FastAPI runtime (LLM + tool loop + pywa
  WhatsApp channel). Lives in its **own repo**: `github.com/igeg1/visar-fastapi`
  (private, personal account for now). You clone + run it.

The FastAPI service replaces the **native Odoo WhatsApp module** that currently
answers inbound manually (no LLM). Phase D repoints Meta's webhook off it.

**You (SSH agent) can do Phases B and C end-to-end. Two Phase-D steps are
human-only:**
- Changing the **Meta App dashboard** webhook (Callback URL + verify token). No
  API access is provisioned here — a human does it in the Meta UI.
- Sending a **WhatsApp message from a real phone** for the final test.
Do everything up to those, then hand back with the exact values the human needs
(the webhook URL and the verify token from the `.env`).

---

## 1. Server facts (confirmed 2026-07-24)

| Item | Value |
|---|---|
| nginx vhost | `/etc/nginx/sites-available/visar.hanova.consulting` (symlinked in sites-enabled) → Odoo `127.0.0.1:8069` |
| TLS | certbot, `/etc/letsencrypt/live/visar.hanova.consulting/` |
| Odoo unit | `odoo.service` — `/opt/odoo/venv/bin/odoo -c /etc/odoo/odoo.conf` |
| Odoo conf / DB | `/etc/odoo/odoo.conf`, DB **`visar-db`**, `proxy_mode=True`, bind `127.0.0.1:8069` |
| addons_path | `/opt/odoo/odoo/addons,/opt/custom` (this repo is `/opt/custom`) |
| Python | `/usr/bin/python3.12` (3.12.3) |

---

## 2. Pre-flight

```bash
# This repo (the Odoo modules) is at /opt/custom and must track hanovamx/visar-homes.
git -C /opt/custom remote -v
git -C /opt/custom log --oneline -3

# The FastAPI repo is PRIVATE on a personal account. Confirm the server can reach it
# BEFORE Phase C. If this 404s / asks for auth, set up access first:
#   - add a read-only deploy key on the server, or use a PAT, or make the repo public.
git ls-remote https://github.com/igeg1/visar-fastapi.git >/dev/null && echo "fastapi repo reachable" || echo "NO ACCESS — provision a deploy key/PAT first"
```

---

## 3. Phase B — Install the read-only RPC module in Odoo

```bash
# Backup first (dev box, but install runs and touches the registry).
sudo -u odoo pg_dump visar-db | gzip > ~/visar-db_$(date +%F).sql.gz

# Pull the module (already on origin/main) and install it (pulls visar_appointment,
# already present). Stop the live service to avoid registry/lock races.
cd /opt/custom && git pull origin main
test -f visar_whatsapp_agent/__manifest__.py && echo "module present"

sudo systemctl stop odoo
sudo -u odoo /opt/odoo/venv/bin/odoo -c /etc/odoo/odoo.conf -d visar-db \
     -i visar_whatsapp_agent --stop-after-init
sudo systemctl start odoo
```

Odoo-19 install gotchas (already fixed in code, listed for triage only):
`res.groups.category_id`→`privilege_id`, `res.users.groups_id`→`group_ids`,
share-user API-key duration cap raised by the module's group. A fresh
`AccessError` on the first `quote_service` is closed by adding the missing model
to `visar_whatsapp_agent/security/ir.model.access.csv` (one line) — see
`27-whatsapp-agent.md`.

### API key for `whatsapp_agent` (issued AS the agent user, not admin)
```bash
sudo -u odoo /opt/odoo/venv/bin/odoo shell -c /etc/odoo/odoo.conf -d visar-db
```
```python
agent = env['res.users'].search([('login', '=', 'whatsapp_agent')], limit=1)
key = env(user=agent.id)['res.users.apikeys']._generate(
    'rpc', 'visar_fastapi', '2035-01-01 00:00:00')   # verify signature if Odoo 19 differs
print('API KEY =>', key)               # shown once — capture it for the .env
env.cr.commit()
```
UI fallback: give `whatsapp_agent` a password, log in as it, Preferences >
Account Security > New API key.

### Smoke-test the key (no FastAPI needed)
```bash
sudo -u odoo /opt/odoo/venv/bin/python - <<'PY'
import xmlrpc.client
URL, DB, USER, KEY = "http://127.0.0.1:8069", "visar-db", "whatsapp_agent", "<API-KEY>"
uid = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common").authenticate(DB, USER, KEY, {})
m = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")
q = m.execute_kw(DB, uid, KEY, "visar.agent.tools", "agent_quote_service",
    [{"cp": "64000", "items": [{"service_code": "FUM_INT", "m2": 120},
                               {"service_code": "FUM_EXT", "m2": 200}]}])
print("total:", q.get("total"), "| msg:", q.get("message"))
PY
```
Expect a single **combined** total for interior+exterior (not the sum of the two).

---

## 4. Phase C — Deploy the FastAPI runtime

```bash
# 1. Clone the runtime repo (see pre-flight for private-repo access).
sudo git clone https://github.com/igeg1/visar-fastapi.git /opt/visar_fastapi
sudo chown -R odoo:odoo /opt/visar_fastapi

# 2. venv + deps (the [whatsapp] extra pulls pywa).
/usr/bin/python3.12 -m venv /opt/visar_fastapi/.venv
/opt/visar_fastapi/.venv/bin/pip install -U pip
/opt/visar_fastapi/.venv/bin/pip install -e "/opt/visar_fastapi[whatsapp]"

# 3. Write /opt/visar_fastapi/.env  (template in §4.1). Lock it down.
sudo chown odoo:odoo /opt/visar_fastapi/.env && sudo chmod 600 /opt/visar_fastapi/.env

# 4. systemd unit (also in the repo at deploy/visar-fastapi.service).
sudo cp /opt/visar_fastapi/deploy/visar-fastapi.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now visar-fastapi.service
curl -s 127.0.0.1:8000/health    # expect whatsapp_enabled:true, odoo_fake:false, catalog_loaded:true

# 5. nginx: add the webhook location, test, reload (block in §4.2).
sudo nginx -t && sudo systemctl reload nginx
```

### 4.1 `/opt/visar_fastapi/.env`
```
WHATSAPP_ENABLED=true
WA_PHONE_ID=<Phone number ID>
WA_TOKEN=<access token — prefer a permanent System-User token, not the 24h dashboard one>
WA_APP_ID=<Application ID>
WA_APP_SECRET=<Application secret>
WA_VERIFY_TOKEN=<invent a random string; the human pastes the SAME one in Meta>
WA_WEBHOOK_PATH=/whatsapp/webhook

ODOO_FAKE=false
ODOO_URL=http://127.0.0.1:8069
ODOO_DB=visar-db
ODOO_USERNAME=whatsapp_agent
ODOO_API_KEY=<key from Phase B>

LLM_PROVIDER=anthropic_api_key
LLM_MODEL=claude-haiku-4-5
ANTHROPIC_API_KEY=<Anthropic key>

HOST=127.0.0.1
PORT=8000
```
(The **WABA ID** is not used by this code.)

### 4.2 nginx location — paste inside the `server{ listen 443 ... }` of the vhost, BEFORE `location /`
```nginx
location = /whatsapp/webhook {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 60s;
}
```
Exact match (`location =`) so only this path leaves Odoo; everything else still
routes to `127.0.0.1:8069`.

---

## 5. Phase D — Meta webhook (human) + test

Hand the human these two values from the `.env`:
- **Callback URL:** `https://visar.hanova.consulting/whatsapp/webhook`
- **Verify token:** the `WA_VERIFY_TOKEN` you set.

Human, in Meta App dashboard → WhatsApp → Configuration → Webhook:
1. Set Callback URL + Verify token above → **Verify** (must go green; pywa answers
   the GET handshake — watch `journalctl -u visar-fastapi -f`).
2. Subscribe the **`messages`** field. (This takes inbound off the native module.)
3. From a phone, message the number: `¿cuánto cuesta fumigar 120 m2 en el CP 64000?`
   → expect an LLM, WhatsApp-formatted quote from the real pricing engine.

---

## 6. Verification
- `systemctl status visar-fastapi` active; `journalctl -u visar-fastapi -f` clean.
- `curl 127.0.0.1:8000/health` → `whatsapp_enabled:true`, `odoo_fake:false`, `catalog_loaded:true`.
- Odoo RPC smoke test (§3) returns a combined total.
- After the human does Phase D: Meta handshake green; a real message returns a
  correct quote with **no** Markdown leaks (`**`, `#`, `[text](url)`).

## 7. Rollback
- **Webhook:** human repoints Meta's Callback URL back to the native module (or clears it).
- **FastAPI:** `sudo systemctl disable --now visar-fastapi`; remove the nginx
  `location` block and `systemctl reload nginx`.
- **Odoo module:** read-only and additive; uninstall from Apps or restore the
  Phase-B backup if ever needed.

## 8. Known follow-ups (not blockers)
- `WA_TOKEN` longevity: use a permanent System-User token so it doesn't expire in 24h.
- `_generate` API-key signature: verify against Odoo 19 if the shell call errors; UI fallback works.
- When `igeg1/visar-fastapi` transfers to `hanovamx`, update the clone remote on the server.
