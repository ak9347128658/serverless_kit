# E-mail triggers (SMTP) on premises

Run a function whenever mail arrives for your domain: `support@example.com`, `ann@sales.example.com`,
`invoices@billing.example.com`. The on-prem host runs its own SMTP server, stores each message, and calls every
function whose `smtp` trigger matches. The function gets the sender, the recipients split into *local part*,
*subdomain* and *domain*, the subject, the text and HTML body, every header and every attachment. Each message is
also written to the logs.

> **On premises only.** The other providers reject `smtp` triggers when you validate. On AWS the equivalent is SES
> receipt rules; pskit does not generate them yet.

## Contents

0. [Set up your domain in the studio (step by step: kavion.ai)](#set-up-your-domain-in-the-studio)
1. [How it works](#how-it-works)
2. [Quick start](#quick-start)
3. [What the handler receives](#what-the-handler-receives)
4. [Subdomains](#subdomains)
5. [DNS: point your domain at the server](#dns-point-your-domain-at-the-server)
6. [Port 25, firewalls and the cloud](#port-25-firewalls-and-the-cloud)
7. [Password (SMTP AUTH): on or off](#password-smtp-auth-on-or-off)
8. [TLS (STARTTLS)](#tls-starttls)
9. [Testing](#testing)
9a. [Who really sent it: SPF, DKIM, DMARC](#who-really-sent-it-spf-dkim-dmarc)
10. [Studio](#studio)
11. [Security](#security)
12. [Options reference](#options-reference)
13. [Troubleshooting](#troubleshooting)

## Set up your domain in the studio

*Resources → **+ Mail domain (SMTP)*** does everything below in one form. The example uses `kavion.ai`, whose company
mail is on Microsoft 365.

1. **Your domain.** Type `kavion.ai` and click **Look up its DNS**. pskit shows where its mail goes today
   (`kavion-ai.mail.protection.outlook.com`, Microsoft 365).
2. **Receive mail at.** Keep **A subdomain** (`inbound`), so mail for `anything@inbound.kavion.ai` comes to pskit and
   `@kavion.ai` stays at Microsoft 365. If you pick *The whole domain*, a red warning explains that changing
   kavion.ai's MX moves **all** company mail to pskit. Leave *Also anything@<name>.inbound.kavion.ai* on to get
   `support.inbound.kavion.ai`, `sales.inbound.kavion.ai` ..., each with its own `subdomain` in the event.
3. **Mail server.** Pick the machine: where the stage is deployed, a saved server (its IP goes into the A record), or
   type an IP. The mail server name defaults to `mx.kavion.ai`, and the port to 2525.
4. **Security.** Leave *Check every sender* (SPF, DKIM, DMARC) and *Block spoofed mail* on. Leave the password **off**
   for mail from the internet.
5. **Run these functions.** Tick existing functions, or keep *New function* `on-mail`. pskit adds the function, its
   `smtp` trigger and `inbox_handlers/on_mail.py` (or `.js`), a handler that already logs the sender, recipient,
   subdomain, checks and attachments. Then **Add the mail server**, **Save**, and **Deploy**.
6. **DNS records.** Add the rows of the table at your domain provider. For `kavion.ai` on the server
   `13.68.248.150`:

   | Type | Name / Host | Value | Priority | |
   |---|---|---|---|---|
   | A | `mx` | `13.68.248.150` | | required |
   | MX | `inbound` | `mx.kavion.ai` | 10 | required |
   | MX | `*.inbound` | `mx.kavion.ai` | 10 | for subdomains |
   | TXT | `inbound` | `v=spf1 -all` | | recommended: nobody may send *as* @inbound.kavion.ai |
   | TXT | `_dmarc.inbound` | `v=DMARC1; p=reject; adkim=s; aspf=s` | | recommended |

   `kavion.ai`'s own MX, SPF (`include:spf.protection.outlook.com ...`) and DKIM records stay as they are.
   **Check DNS** shows each row as *✓ live*, *missing* or *different* (with what is there now). *Check DNS and ports*
   also tries TCP 25 and the SMTP port from your machine.
7. **Open port 25** on the server (cloud firewall) and redirect it to 2525 (see [Port 25](#port-25-firewalls-and-the-cloud)).
8. **Test.** Use *Resources → inbound-mail → Mailbox → Send test mail* (no DNS needed). Once DNS is live, send a
   real mail from Outlook or Gmail to `hello@inbound.kavion.ai` and watch it in the Mailbox and in *Logs*.

**DNS setup** on the resource's row opens the records and the check again at any time.

**Why no DKIM record?** DKIM is a signature the *sending* server adds with its private key; receivers check it with
the public key in the sender's DNS (`<selector>._domainkey.<domain>`). To receive mail you publish nothing.
pskit checks the DKIM of every mail it receives, and SPF and DMARC too (see
[Who really sent it](#who-really-sent-it-spf-dkim-dmarc)). Your outgoing mail is signed by your mail provider
(Microsoft 365 / Google), which gives you its own DKIM record.

## How it works

```
 sender (Gmail, Outlook, your app)
        │ 1. "where does mail for example.com go?"  DNS MX example.com  ->  mx.example.com
        │ 2. "what is mx.example.com?"               DNS A  mx.example.com ->  203.0.113.10
        ▼
 ┌──────────────────────── your server 203.0.113.10 ────────────────────────┐
 │  :25 ─(firewall / redirect)─► pskit host SMTP server :2525               │
 │      EHLO · STARTTLS · AUTH (optional) · MAIL FROM · RCPT TO · DATA      │
 │      refuses recipients outside your domains (never an open relay)       │
 │                         │ 3. message stored  <data_dir>/smtp/<server>/   │
 │                         │    then "250 Ok" to the sender                 │
 │                         ▼                                                │
 │      4. parsed: from, to/cc, subdomain, domain, text, html, attachments  │
 │      5. logged (sender, recipients, subdomain, subject, files, preview)  │
 │      6. every function with a matching `smtp` trigger runs               │
 │         support-desk  (subdomains: [support])                            │
 │         archive       (all mail)                                         │
 └──────────────────────────────────────────────────────────────────────────┘
```

The message is stored *before* the sender is told it was accepted, so a crash or restart never loses mail. Mail
that was not delivered to functions yet is delivered when the host starts again. Each message records which
functions ran and whether they failed, and you can run the functions again from the mailbox.

![How mail finds a server: MX and A records, SPF and DKIM](smtp_flow.jpg)

The sketch above shows the general flow. To deliver to `support@outlook.com`, the sending server asks DNS for the
**MX** record of the domain (the mail server's name), then for that name's **A** record (its IP address), and
connects to it. The receiving side can check the sender with **SPF** (a TXT record listing the servers allowed to
send for a domain) and **DKIM** (the sender signs with a private key; the public key is published in DNS). pskit is
the *receiving* server in that picture.

## Quick start

`pskit.yml`:

```yaml
service: helpdesk
provider:
  name: onprem
  options:
    host: 0.0.0.0
    deploy: {server: VM_01}        # or method: local

resources:
  inbound-mail:
    type: smtp
    options:
      domains: [example.com]       # mail to example.com and its subdomains
      port: 2525

functions:
  support-desk:
    handler: app.mail.support
    events:
      - smtp: {resource: inbound-mail, subdomains: [support]}
  archive:
    handler: app.mail.archive
    events:
      - smtp: inbound-mail          # shorthand: every message
```

`app/mail.py`:

```python
import base64
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def support(event, ctx):
    mail = event.data
    to = mail["recipient"]                     # the recipient that matched this trigger
    log.info("ticket from %s to %s (subdomain %s, domain %s): %s",
             mail["from"]["address"], to["address"], to["subdomain"], to["domain"], mail["subject"])
    for att in mail["attachments"]:
        data = base64.b64decode(att["content"])
        Path("/var/tickets", att["filename"] or "attachment.bin").write_bytes(data)
    return {"ticket": mail["id"]}


def archive(event, ctx):
    log.info("%s -> %s: %s (%d files)", event.data["from"]["address"],
             ", ".join(r["address"] for r in event.data["recipients"]), event.data["subject"],
             len(event.data["attachments"]))
```

Node.js:

```js
exports.support = async (event, ctx) => {
  const mail = event.data;
  console.log(`from ${mail.from.address} to ${mail.recipient.address} subdomain=${mail.recipient.subdomain}`);
  for (const att of mail.attachments) {
    const bytes = Buffer.from(att.content, "base64");
    console.log(`attachment ${att.filename} ${att.content_type} ${bytes.length} bytes`);
  }
};
```

Then `pskit deploy`, and test it without DNS:

```bash
pskit trigger -f support-desk -e 0 -d '{"to": ["help@support.example.com"], "subject": "Printer broken",
  "text": "It jams.", "attachments": [{"filename": "photo.txt", "content": "..."}]}'
pskit logs -f support-desk
```

## What the handler receives

`event.type` is `"smtp"` and `event.data` is:

| Field | Example | Notes |
|---|---|---|
| `id` | `20261023110921-3f2a9c1d` | Mailbox id (Studio *Mailbox*, admin API) |
| `resource` | `inbound-mail` | The SMTP server |
| `received_at`, `size` | ISO time, bytes | |
| `envelope` | `{mail_from, rcpt_to, helo, remote_ip, authenticated_user, tls}` | What the sending server said; `rcpt_to` holds *all* recipients, Bcc included |
| `recipients` | `[{address, name, local, host, subdomain, domain}]` | Every envelope recipient, split |
| `matched`, `recipient` | the recipients this trigger accepted, and the first one | Use `recipient["subdomain"]` to route |
| `from` | `{address: "ann@client.org", name: "Ann", local, host, subdomain, domain}` | From the `From:` header |
| `to`, `cc`, `reply_to` | lists like `from` | From the headers (Bcc is not in the headers) |
| `subject`, `date`, `message_id`, `in_reply_to`, `references` | | |
| `text`, `html` | | The plain-text and HTML bodies (empty when missing) |
| `headers` | `{"received": "...", "dkim-signature": "...", ...}` | Lower-case names; repeated headers joined with `, ` |
| `attachments` | `[{filename, content_type, size, sha256, content_id, inline, content}]` | `content` is base64; inline images have `inline: true` and a `content_id` |

With **several recipients** the function runs **once per message** (not once per recipient). `matched` lists the
recipients it accepted.

**Logs.** Each message produces one line from the `smtp` source:

```
mail 20261023110921-3f2a9c1d from ann@client.org to help@support.example.com (subdomain=support domain=example.com)
subject='Printer broken' size=4.1 KB attachments=1 [photo.jpg image/jpeg 2.3 KB] body='It jams when ...'
```

The log entry also has structured `fields`: from, to, subdomains, domains, cc, subject, a body preview, attachments
(name, type, size, sha256), remote_ip, authenticated_user and tls. Each function that runs gets its own
`mail <id> -> <function>` line. View them with `pskit logs`, in Studio *Logs* or on the dashboard.

## Subdomains

`domains: [example.com]` with `subdomains: true` (the default) accepts `x@example.com`, `x@support.example.com` and
`x@eu.sales.example.com`. Each address is split against the domain you configured:

| Address | `local` | `subdomain` | `domain` |
|---|---|---|---|
| `ann@example.com` | `ann` | *(empty)* | `example.com` |
| `ann@support.example.com` | `ann` | `support` | `example.com` |
| `ann@eu.sales.example.com` | `ann` | `eu.sales` | `example.com` |

With several domains the longest match wins. For example, `domains: [example.com, mail.example.org]` splits
`x@eu.mail.example.org` into subdomain `eu` and domain `mail.example.org`.

Route by subdomain in the trigger (`subdomains: [support, "eu.*"]`, where `""` means the domain itself) or by address
(`recipients: ["*@support.example.com", "orders-*@example.com"]`). Without a filter the function gets all mail.

Subdomains need DNS too: see the wildcard MX record below.

## DNS: point your domain at the server

You need two records at your DNS provider (Cloudflare, GoDaddy, Namecheap, Route 53, Azure DNS, Google Cloud DNS ...).
Example for the server `203.0.113.10` and the domain `example.com`:

| Type | Name / Host | Value / Points to | Priority | Why |
|---|---|---|---|---|
| **A** | `mx` (that is, `mx.example.com`) | `203.0.113.10` | | The mail server's address |
| **MX** | `@` (`example.com`) | `mx.example.com` | `10` | "Mail for example.com goes to mx.example.com" |
| **MX** | `*` (`*.example.com`) | `mx.example.com` | `10` | Mail for *every* subdomain (`support.example.com` ...) |
| MX | `support` | `mx.example.com` | `10` | Alternative to the wildcard: one subdomain only |

Rules:

* An MX record must point to a **name**, never to an IP address, and that name needs an A (or AAAA) record.
* If your domain already receives mail elsewhere (Google Workspace, Microsoft 365), **don't replace its MX
  record**. Use a subdomain instead, such as `MX inbound.example.com -> mx.example.com`, and send to
  `anything@inbound.example.com`. Changing `@`'s MX moves all of your company's mail to this server.
* On Cloudflare, set the `mx` A record to **DNS only** (grey cloud). Cloudflare's proxy does not carry SMTP.
* Records take minutes to hours to spread (their TTL).

Check:

```bash
dig MX example.com +short             # 10 mx.example.com.
dig A mx.example.com +short           # 203.0.113.10
dig MX support.example.com +short     # 10 mx.example.com. (wildcard)
nslookup -type=mx example.com         # Windows
```

**SPF, DKIM and DMARC** protect *sending*: they tell receivers which servers may send as `example.com`. pskit only
receives mail, so they are optional for this setup. If this domain sends no mail at all, publishing
`TXT @ "v=spf1 -all"` and `TXT _dmarc "v=DMARC1; p=reject"` stops others from forging it.

pskit checks the SPF, DKIM and DMARC of every mail it **receives** (next section), so `From:` can be trusted.

A **PTR (reverse DNS)** record is not needed to *receive* mail.

## Port 25, firewalls and the cloud

Other mail servers always deliver to **port 25**. The host listens on `port` (default **2525**, because ports below
1024 need root). Pick one of these:

1. **Redirect 25 to 2525** on the server (recommended):

   ```bash
   sudo iptables -t nat -A PREROUTING -p tcp --dport 25 -j REDIRECT --to-port 2525
   sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save   # keep it after reboot
   ```

2. **`port: 25`** with `deploy: {install: systemd, sudo: true}`, so the host runs as root.

3. **Docker deploy** (`method: docker`): the SMTP port is published by the container. Publish 25 to it from your
   proxy or use option 1 on the host.

Then open the port in the cloud's firewall:

| Cloud | Where | Rule |
|---|---|---|
| Azure | VM -> Networking -> Inbound port rules | Allow TCP 25 (and 2525 for tests) from Any |
| AWS | EC2 -> Security group -> Inbound rules | Custom TCP 25, source 0.0.0.0/0 |
| GCP | VPC network -> Firewall | Ingress tcp:25 to the VM's tag |
| ufw on the server | `sudo ufw allow 25/tcp` | |

Clouds block *outgoing* port 25 to stop spam. That does not matter here: pskit only receives.

## Password (SMTP AUTH): on or off

| | **Disabled** (default) | **Enabled** (`auth: true`) |
|---|---|---|
| Who sends | Other mail servers (Gmail, Outlook ...) delivering to your MX | Your apps, devices, scanners, monitoring that log in first |
| Login | None. Internet mail servers never log in to an MX | `AUTH PLAIN` / `AUTH LOGIN` with a user from `users` |
| Use it for | Receiving mail for your domain | A private submission server (e.g. `alerts@ops.example.com`) |

```yaml
resources:
  device-mail:
    type: smtp
    options:
      domains: [ops.example.com]
      port: 2587
      auth: true
      users:
        scanner: {password_env: SMTP_SCANNER_PASSWORD}   # the password lives in .env.onprem
      tls_cert: /etc/letsencrypt/live/mx.example.com/fullchain.pem
      tls_key: /etc/letsencrypt/live/mx.example.com/privkey.pem
      require_tls: true
```

Passwords are never written in `pskit.yml`. They live in the env file (`provider.options.env_file`, default
`.env.onprem`). In Studio, open *Resources → the smtp resource* and switch **Password** to *Enabled*. Add users,
then **Generate** or type a password, and **Save to .env.onprem**. Deploy to apply it.

To receive internet mail *and* accept logged-in apps, use two smtp resources on two ports.

## TLS (STARTTLS)

Give the server a certificate and it offers `STARTTLS`; senders then encrypt the connection. With Let's Encrypt on
the server:

```bash
sudo certbot certonly --standalone -d mx.example.com      # needs port 80 free for a minute
```

Then set `tls_cert` and `tls_key` to the `fullchain.pem` and `privkey.pem` paths. The host must be able to read
them, so run it as root (systemd) or copy them with the right owner. `require_tls: true` refuses mail sent without
STARTTLS. When `auth` is on, AUTH is offered only after STARTTLS unless there is no certificate at all (a private
network).

## Who really sent it: SPF, DKIM, DMARC

Anyone can write `From: ceo@kavion.ai` in a mail. With `verify_senders` (on by default) pskit checks every message
the way Gmail and Outlook do, and puts the results in `event.data["authentication"]`:

```python
{
  "spf":   {"result": "pass", "domain": "partner.org", "reason": "198.51.100.7 is pass for partner.org's SPF record"},
  "dkim":  [{"result": "pass", "domain": "partner.org", "selector": "s1", "reason": "signature verified"}],
  "dmarc": {"result": "pass", "policy": "reject", "domain": "partner.org", "aligned": {"spf": True, "dkim": True}},
  "summary": "spf=pass(partner.org) dkim=pass(partner.org) dmarc=pass(p=reject)",
}
```

* **SPF**: is the connecting IP allowed by the sender domain's `v=spf1` record (`ip4`, `ip6`, `a`, `mx`, `include`,
  `redirect`, `exists`; at most 10 lookups)? Results: `pass`, `fail`, `softfail`, `neutral`, `none`,
  `temperror` (DNS did not answer), `permerror` (a broken record).
* **DKIM**: does each `DKIM-Signature` verify with the key at `<s>._domainkey.<d>` (rsa-sha256 / rsa-sha1, simple
  and relaxed)? A changed body or signed header gives `fail`. Ed25519 keys are reported `neutral`.
* **DMARC**: did SPF or DKIM pass *for the domain in `From:`* (relaxed or strict alignment), and what does that
  domain's `_dmarc` record ask (`p=none / quarantine / reject`, `sp=` for subdomains)?

```python
def handle(event, ctx):
    mail = event.data
    auth = mail["authentication"]
    if auth["dmarc"]["result"] != "pass" and mail["from"]["domain"] == "partner.org":
        return {"ignored": "not really from partner.org"}
```

**`block_spoofed: true`** (on in the setup form) stops a mail before any function runs when:

* DMARC fails and the sender's domain asks for `quarantine` or `reject`, or
* `From:` is one of **this server's own domains** (`ceo@kavion.ai` writing to `pay@inbound.kavion.ai`) and neither
  SPF nor DKIM passed for it.

Blocked mail stays in the Mailbox with status *blocked* and the reason. The results are checked once per message
and kept with it (re-running functions does not ask DNS again). They also appear in the log line
(`[spf=pass(...) dkim=pass(...) dmarc=pass(p=reject)]`). The host asks the DNS servers in `/etc/resolv.conf`
(override with `PSKIT_DNS_SERVERS=1.1.1.1,8.8.8.8`). Mail sent through the admin API (`pskit trigger`, *Send test
mail*) reports `spf=none`.

## Testing

**Without DNS or port 25**, the host stores and delivers the message as if its SMTP server had received it:

```bash
pskit trigger -f support-desk -e 0 -d '{"from": "ann@client.org", "to": ["help@support.example.com"],
  "subject": "Hi", "text": "Body", "attachments": [{"filename": "a.txt", "content": "hello"}]}'
```

**Locally**, without deploying (the handler gets the same parsed `event.data`):

```bash
pskit invoke -f support-desk -t smtp -d '{"to": ["help@support.example.com"], "text": "hi"}'
```

**Over real SMTP** (from any machine that can reach the port):

```bash
# swaks (apt install swaks)
swaks --server 203.0.113.10:2525 --from ann@client.org --to help@support.example.com \
      --header "Subject: Printer" --body "It jams" --attach @photo.jpg
```

```python
import smtplib
from email.message import EmailMessage

msg = EmailMessage()
msg["From"], msg["To"], msg["Subject"] = "ann@client.org", "help@support.example.com", "Printer"
msg.set_content("It jams")
msg.add_attachment(open("photo.jpg", "rb").read(), maintype="image", subtype="jpeg", filename="photo.jpg")
with smtplib.SMTP("203.0.113.10", 2525) as s:
    # s.starttls(); s.login("scanner", "...")    # when TLS / auth are on
    s.send_message(msg)
```

**End to end**: once DNS is set, send a normal mail from Gmail or Outlook to `help@support.example.com`.

## Studio

* **Resources → + Add resource → type smtp**. Set the domains, subdomains, port, max size, **Password**
  (enabled or disabled, with users and passwords), TLS certificate, allowed senders and allowed IP ranges.
* **Functions → trigger "E-mail (SMTP)"**. Pick the server (or click **+ New SMTP server**), then optional recipient
  patterns and subdomains.
* **Resources → Mailbox** (after deploy) shows every received mail: time, sender, recipients with their
  subdomain and domain, subject, number of files, and which functions ran. Open a mail to see the
  recipient table, text, sandboxed HTML and headers, and to download attachments or the `.eml`. You can also run
  the functions again, delete one mail, empty the mailbox, or **Send test mail**. It pages 25 mails at a time and
  filters by status (delivered, failed, no function, pending).
* **Test**: the smtp trigger's sample payload goes to a matching address.
* **Logs**: source `smtp` plus each function's own lines.

## Security

* **No open relay.** Recipients outside `domains` get `550 Relaying denied`. pskit never forwards mail anywhere.
* **Size limit** (`max_size_mb`, default 25 MB). Bigger messages are refused with `552`.
* **`allowed_senders`** (globs on the envelope sender) and **`allowed_ips`** (CIDR ranges) narrow who may deliver.
  Use them for internal-only servers.
* Failed logins are logged with the client address and slowed down. Passwords are compared in constant time.
* HTML bodies are shown in a sandboxed iframe (no scripts) in Studio. Treat attachments as untrusted in your
  handlers.
* Everything stays on your server: `<data_dir>/smtp/<server>/<id>.eml` and `<id>.json`. Clean up with the
  mailbox's *Delete all* or the admin API.

## Options reference

`resources.<key>` with `type: smtp`, settings under `options:`:

| Option | Default | Meaning |
|---|---|---|
| `domains` | *(required)* | Domains this server receives mail for |
| `subdomains` | `true` | Also accept their subdomains |
| `port` | `2525` | Listening port (25 needs root; see above) |
| `bind` | `0.0.0.0` | Address to listen on (`127.0.0.1` for local tests) |
| `hostname` | the machine's name | Name in the greeting and `Received:` header (use your MX name) |
| `max_size_mb` | `25` | Largest message accepted |
| `auth` | `false` | Require SMTP AUTH (PLAIN / LOGIN) |
| `users` | | `{name: {password_env: VARIABLE}}`; the password is in the env file |
| `tls_cert`, `tls_key` | | PEM paths on the server; enable STARTTLS |
| `require_tls` | `false` | Refuse mail before STARTTLS |
| `allowed_senders` | anyone | Globs such as `*@partner.com` |
| `allowed_ips` | anyone | CIDRs such as `10.0.0.0/8` |
| `verify_senders` | `true` | Check SPF, DKIM and DMARC; results in `event.data.authentication` |
| `block_spoofed` | `false` | Do not run functions for spoofed mail (see above); status *blocked* |

The trigger, under `functions.<name>.events`:

```yaml
- smtp: inbound-mail                                   # all mail of that server
- smtp: {resource: inbound-mail, subdomains: [support, "eu.*"], recipients: ["*@support.example.com"]}
```

Admin API on the host (with the admin token), used by `pskit trigger` and Studio:
`GET /__pskit/mail`, `GET|POST|DELETE /__pskit/mail/<server>`, `GET|DELETE /__pskit/mail/<server>/<id>`,
`GET .../<id>/raw`, `GET .../<id>/attachments/<n>` and `POST .../<id>/redeliver`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `cannot listen on 0.0.0.0:25: Permission denied` (host logs) | Ports below 1024 need root: use 2525 + the iptables redirect, or systemd with sudo |
| Gmail says "address not found" / bounces | `dig MX` shows no record or the wrong one; the MX must name a host with an A record |
| Connection times out from outside | The cloud firewall / security group or ufw does not allow TCP 25 |
| `550 Relaying denied` | The recipient's domain is not in `domains` (or it is a subdomain and `subdomains: false`) |
| `530 Authentication required` | `auth: true`: the client must log in (or turn the password off for MX use) |
| Mail arrives but no function runs (status *No function*) | No trigger's `recipients` / `subdomains` matched: check the mailbox's recipient table |
| A function failed (status *failed*) | Read its error in the mailbox or `pskit logs -f <fn>`, fix it, deploy, then **Run functions again** |
| Mail for subdomains bounces | Add the wildcard `MX *` record (or one per subdomain) |
| Status *blocked* | `block_spoofed`: the reason is in the Mailbox; the sender must fix their SPF / DKIM, or turn it off |
| `spf=temperror` in every mail | The server cannot reach DNS: set `PSKIT_DNS_SERVERS` in the env file |
| Company mail stopped arriving at Microsoft 365 / Google | The root domain's MX was changed: put it back and receive on a subdomain |
