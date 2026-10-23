import { useCallback, useEffect, useState } from "react";
import { Drawer } from "../../components/Drawer";
import { api, errorMessage } from "../api";
import type { Json, JsonObject } from "../types";
import { asArray, asObject, str } from "../util";
import { useConfirm, useToast } from "./Feedback";
import { FormRow, JsonInput, ListEditor, NumberInput, Segmented, TextInput, Toggle } from "./Forms";

const DOMAIN_RE = /^(?!-)[A-Za-z0-9-]{1,63}(\.(?!-)[A-Za-z0-9-]{1,63})+$/;

function envName(resource: string, user: string): string {
  return `SMTP_${resource}_${user}_PASSWORD`.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
}

/** `resources.<key>.options` of an smtp resource: the host's SMTP server for your domains. */
export function SmtpServerOptions({ resourceKey, value, onChange }: { resourceKey: string; value: JsonObject; onChange: (next: JsonObject) => void }) {
  const toast = useToast();
  const set = (k: string, v: Json | undefined) => {
    const next = { ...value };
    if (v === undefined || v === "" || (Array.isArray(v) && !v.length)) delete next[k];
    else next[k] = v;
    onChange(next);
  };
  const domains = asArray(value.domains).map((d) => str(d));
  const users = asObject(value.users);
  const auth = value.auth === true;
  const [newUser, setNewUser] = useState("");
  const [passwords, setPasswords] = useState<Record<string, string>>({});
  const [isSet, setIsSet] = useState<Record<string, boolean>>({});
  const [envFile, setEnvFile] = useState(".env.onprem");
  const vars = Object.values(users).map((u) => str(asObject(u).password_env)).filter(Boolean);
  const varsKey = vars.join(",");

  useEffect(() => {
    if (!varsKey) return;
    api.envFileStatus(varsKey.split(",")).then(
      (r) => {
        setIsSet(r.set);
        setEnvFile(r.file);
      },
      () => undefined,
    );
  }, [varsKey]);

  const addUser = () => {
    const name = newUser.trim();
    if (!/^[A-Za-z0-9._@+-]{1,64}$/.test(name)) return toast("user names: letters, digits and . _ @ + -", "warn");
    set("users", { ...users, [name]: { password_env: envName(resourceKey, name) } });
    setNewUser("");
  };
  const savePassword = async (user: string) => {
    const variable = str(asObject(users[user]).password_env);
    const password = passwords[user] || "";
    if (password.length < 8) return toast("use at least 8 characters", "warn");
    try {
      await api.envFileSet(variable, password);
      setIsSet((s) => ({ ...s, [variable]: true }));
      setPasswords((p) => ({ ...p, [user]: "" }));
      toast(`Saved ${user}'s password to ${envFile} as ${variable}; deploy to apply`, "ok");
    } catch (exc) {
      toast(errorMessage(exc), "err");
    }
  };
  const generate = (user: string) => {
    const bytes = crypto.getRandomValues(new Uint8Array(18));
    setPasswords((p) => ({ ...p, [user]: btoa(String.fromCharCode(...bytes)).replace(/[+/=]/g, "x") }));
  };

  const badDomains = domains.filter((d) => !DOMAIN_RE.test(d));
  return (
    <div className="driver-box card card-pad stack">
      <h3>SMTP server</h3>
      <p className="muted small">
        The on-prem host listens for mail to these domains and runs every function with an <code className="mono">smtp</code> trigger on this resource.
        It never relays mail elsewhere. Stored in <code className="mono">resources.{resourceKey}.options</code>.
      </p>
      <FormRow label="Domains" required error={!domains.length ? "add the domain(s) you receive mail for" : badDomains.length ? `not a domain: ${badDomains.join(", ")}` : null} help="Point their MX record at this server (see the SMTP guide).">
        <ListEditor value={domains} onChange={(v) => set("domains", v)} placeholder="example.com" addLabel="Add domain" />
      </FormRow>
      <Toggle
        checked={value.subdomains !== false}
        onChange={(on) => set("subdomains", on ? undefined : false)}
        label="Also accept subdomains (ann@support.example.com: subdomain support), shown separately in events and logs"
      />
      <div className="row-gap wrap">
        <FormRow label="Port" help="2525 runs without root; redirect 25 to it for internet mail.">
          <NumberInput integer min={1} max={65535} value={value.port === undefined ? undefined : Number(value.port)} placeholder="2525" onChange={(v) => set("port", v)} />
        </FormRow>
        <FormRow label="Max message size (MB)">
          <NumberInput min={1} max={200} value={value.max_size_mb === undefined ? undefined : Number(value.max_size_mb)} placeholder="25" onChange={(v) => set("max_size_mb", v)} />
        </FormRow>
        <FormRow label="Host name (EHLO)">
          <TextInput mono value={str(value.hostname)} placeholder="mx.example.com" onChange={(v) => set("hostname", v)} />
        </FormRow>
      </div>

      <FormRow label="Password (SMTP AUTH)" help="On: senders must log in (apps, devices, scanners that submit mail). Off: other mail servers can deliver to your domains, as they do for an MX.">
        <Segmented
          label="Password"
          value={auth ? "on" : "off"}
          onChange={(v) => set("auth", v === "on" ? true : undefined)}
          options={[
            { value: "off", label: "Disabled: receive from the internet" },
            { value: "on", label: "Enabled: senders log in" },
          ]}
        />
      </FormRow>
      {auth && (
        <div className="card card-pad stack">
          {!Object.keys(users).length && <div className="form-error">Add at least one user.</div>}
          {Object.entries(users).map(([user, body]) => {
            const variable = str(asObject(body).password_env);
            return (
              <div key={user} className="row-gap wrap">
                <b className="mono">{user}</b>
                <span className={`badge ${isSet[variable] ? "" : "badge-warn"}`}>{isSet[variable] ? "password set" : "no password yet"}</span>
                <TextInput mono type="password" value={passwords[user] || ""} placeholder={isSet[variable] ? "new password" : "password"} onChange={(p) => setPasswords((s) => ({ ...s, [user]: p }))} />
                <button type="button" className="btn btn-sm" onClick={() => generate(user)}>
                  Generate
                </button>
                <button type="button" className="btn btn-sm btn-primary" disabled={!passwords[user]} onClick={() => void savePassword(user)}>
                  Save to {envFile}
                </button>
                <span className="muted small mono">{variable}</span>
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  onClick={() => {
                    const next = { ...users };
                    delete next[user];
                    set("users", next);
                  }}
                >
                  Remove
                </button>
              </div>
            );
          })}
          <div className="row-gap">
            <TextInput mono value={newUser} onChange={setNewUser} placeholder="user name, e.g. scanner" />
            <button type="button" className="btn btn-sm" disabled={!newUser.trim()} onClick={addUser}>
              + User
            </button>
          </div>
          <p className="muted small">Passwords go to {envFile} (out of git), never into pskit.yml. Without a certificate below they cross the network in clear text.</p>
        </div>
      )}

      <FormRow label="TLS certificate (STARTTLS)" help="PEM files on the server, e.g. from Let's Encrypt (certbot). Optional; needed for passwords over the internet.">
        <div className="row-gap wrap">
          <TextInput mono value={str(value.tls_cert)} placeholder="/etc/letsencrypt/live/mx.example.com/fullchain.pem" onChange={(v) => set("tls_cert", v)} />
          <TextInput mono value={str(value.tls_key)} placeholder="/etc/letsencrypt/live/mx.example.com/privkey.pem" onChange={(v) => set("tls_key", v)} />
        </div>
      </FormRow>
      {!!value.tls_cert && <Toggle checked={value.require_tls === true} onChange={(on) => set("require_tls", on || undefined)} label="Require STARTTLS before MAIL FROM" />}
      <FormRow label="Allowed senders" help="Optional globs; empty = anyone. e.g. *@partner.com, alerts@*">
        <ListEditor value={asArray(value.allowed_senders).map((x) => str(x))} onChange={(v) => set("allowed_senders", v)} placeholder="*@partner.com" addLabel="Add sender" />
      </FormRow>
      <FormRow label="Allowed IP ranges" help="Optional CIDRs; empty = any address. e.g. 10.0.0.0/8 for a private network.">
        <ListEditor value={asArray(value.allowed_ips).map((x) => str(x))} onChange={(v) => set("allowed_ips", v)} placeholder="10.0.0.0/8" addLabel="Add range" />
      </FormRow>
    </div>
  );
}

// ----------------------------------------------------------------------------- mailbox

interface MailMeta {
  id: string;
  received_at: string;
  size: number;
  status: string;
  envelope: { mail_from: string; rcpt_to: string[]; remote_ip: string; authenticated_user?: string | null; tls?: boolean };
  deliveries: Record<string, { ok: boolean; error?: string }>;
  summary?: { from: string; to: string[]; subject: string; subdomains: string[]; domains: string[]; attachments: number };
}
interface Address {
  address: string;
  name: string;
  local: string;
  subdomain: string;
  domain: string;
}
interface MailDetail extends MailMeta {
  mail: {
    from: Address;
    to: Address[];
    cc: Address[];
    recipients: Address[];
    subject: string;
    date: string;
    text: string;
    html: string;
    headers: Record<string, string>;
    attachments: { filename: string; content_type: string; size: number; inline: boolean }[];
  };
}

function size(n: number): string {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

function save(name: string, contentType: string, b64: string) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: contentType }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

/** Mail received by an smtp resource on the deployed host: who, which subdomain, files, which functions ran. */
export function Mailbox({ provider, stage, resourceKey, onClose }: { provider: string; stage: string; resourceKey: string; onClose: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const call = useCallback(<T,>(op: string, extra: Record<string, unknown> = {}) => api.resourceMail<T>(provider, stage, resourceKey, op, extra), [provider, stage, resourceKey]);
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ items: MailMeta[]; total: number } | null>(null);
  const [open, setOpen] = useState<MailDetail | null>(null);
  const [view, setView] = useState<"text" | "html" | "headers">("text");
  const [error, setError] = useState<string | null>(null);
  const [compose, setCompose] = useState(false);
  const [payload, setPayload] = useState<Json | undefined>({
    from: "Customer <customer@client.org>",
    to: ["support@example.com"],
    subject: "Test from the studio",
    text: "Hello!",
    attachments: [{ filename: "note.txt", content_type: "text/plain", content: "attached file" }],
  });
  const limit = 25;

  const load = useCallback(() => {
    setError(null);
    call<{ items: MailMeta[]; total: number }>("list", { offset, limit, status }).then(setPage, (e) => setError(errorMessage(e)));
  }, [call, offset, status]);
  useEffect(load, [load]);

  const show = (id: string) =>
    call<MailDetail>("get", { id }).then(
      (d) => {
        setOpen(d);
        setView(d.mail.text ? "text" : d.mail.html ? "html" : "headers");
      },
      (e) => setError(errorMessage(e)),
    );
  const act = async (op: string, id?: string, done?: string) => {
    try {
      await call(op, id ? { id } : {});
      if (done) toast(done, "ok");
      if (op === "delete" || op === "purge") setOpen(null);
      else if (id) void show(id);
      load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  const download = async (op: "attachment" | "raw", id: string, index: number, name: string) => {
    try {
      const r = await call<{ content: string; content_type: string }>(op, { id, index });
      save(name, r.content_type, r.content);
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  const send = async () => {
    try {
      const r = await call<MailMeta>("send", { payload });
      toast(`Sent test mail ${r.id}: ${r.status}`, r.status === "delivered" ? "ok" : "warn");
      setCompose(false);
      load();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <Drawer open title={`Mailbox: ${resourceKey}`} onClose={onClose}>
      <div className="stack">
        {error && <div className="form-error">{error}</div>}
        <div className="row-gap wrap">
          <Segmented
            label="Status"
            value={status}
            onChange={(v) => {
              setStatus(v);
              setOffset(0);
            }}
            options={[
              { value: "", label: "All" },
              { value: "delivered", label: "Delivered" },
              { value: "failed", label: "Failed" },
              { value: "unmatched", label: "No function" },
              { value: "received", label: "Pending" },
            ]}
          />
          <button type="button" className="btn btn-sm btn-primary" onClick={() => setCompose((v) => !v)}>
            Send test mail
          </button>
          <button type="button" className="btn btn-sm" onClick={load}>
            Refresh
          </button>
          <button
            type="button"
            className="btn btn-sm btn-danger"
            disabled={!page?.total}
            onClick={async () => {
              if ((await confirm({ title: `Delete all ${page?.total} mails of ${resourceKey}?`, confirmLabel: "Delete all", danger: true })) === "confirm") void act("purge", undefined, "Mailbox emptied");
            }}
          >
            Delete all
          </button>
        </div>
        {compose && (
          <div className="card card-pad stack">
            <p className="muted small">Delivered by the host like a mail its SMTP server received (works even when port 25 is closed). Attachments: content (text) or content_base64.</p>
            <JsonInput value={payload} onChange={setPayload} rows={10} />
            <button type="button" className="btn btn-sm btn-primary" onClick={() => void send()}>
              Send
            </button>
          </div>
        )}
        <table className="table">
          <thead>
            <tr>
              <th>Received</th>
              <th>From</th>
              <th>To (subdomain · domain)</th>
              <th>Subject</th>
              <th>Files</th>
              <th>Functions</th>
            </tr>
          </thead>
          <tbody>
            {(page?.items || []).map((m) => (
              <tr key={m.id} className="clickable" onClick={() => void show(m.id)}>
                <td className="small">{new Date(m.received_at).toLocaleString()}</td>
                <td className="mono small">{m.summary?.from || m.envelope.mail_from}</td>
                <td className="mono small">
                  {(m.summary?.to || m.envelope.rcpt_to).join(", ")}
                  {m.summary && (
                    <div className="muted">
                      {m.summary.subdomains.map((s) => s || "(none)").join(", ")} · {m.summary.domains.join(", ")}
                    </div>
                  )}
                </td>
                <td className="small">{m.summary?.subject || ""}</td>
                <td className="small">{m.summary?.attachments ?? ""}</td>
                <td>
                  <span className={`badge ${m.status === "delivered" ? "" : m.status === "failed" ? "badge-err" : "badge-warn"}`}>{m.status}</span>{" "}
                  <span className="muted small">{Object.keys(m.deliveries || {}).join(", ")}</span>
                </td>
              </tr>
            ))}
            {page && !page.items.length && (
              <tr>
                <td colSpan={6} className="muted">
                  No mail yet. Send a test mail, or one to an address at your domain once DNS points here.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="row-gap">
          <button type="button" className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
            ← Newer
          </button>
          <span className="muted small">
            {page ? `${page.total ? offset + 1 : 0}-${Math.min(offset + limit, page.total)} of ${page.total}` : "…"}
          </span>
          <button type="button" className="btn btn-sm" disabled={!page || offset + limit >= page.total} onClick={() => setOffset(offset + limit)}>
            Older →
          </button>
        </div>
        {open && (
          <div className="card card-pad stack">
            <div className="row-between wrap">
              <b>{open.mail.subject || "(no subject)"}</b>
              <span className="row-gap">
                <button type="button" className="btn btn-sm" onClick={() => void act("redeliver", open.id, "Delivered again")}>
                  Run functions again
                </button>
                <button type="button" className="btn btn-sm" onClick={() => void download("raw", open.id, 0, `${open.id}.eml`)}>
                  Download .eml
                </button>
                <button type="button" className="btn btn-sm btn-danger" onClick={() => void act("delete", open.id, "Deleted")}>
                  Delete
                </button>
              </span>
            </div>
            <div className="small">
              From <span className="mono">{open.mail.from.name ? `${open.mail.from.name} <${open.mail.from.address}>` : open.mail.from.address}</span> · {open.mail.date}
            </div>
            <table className="table">
              <thead>
                <tr>
                  <th>Recipient</th>
                  <th>Local part</th>
                  <th>Subdomain</th>
                  <th>Domain</th>
                </tr>
              </thead>
              <tbody>
                {open.mail.recipients.map((r) => (
                  <tr key={r.address}>
                    <td className="mono small">{r.address}</td>
                    <td className="mono small">{r.local}</td>
                    <td className="mono small">{r.subdomain || <span className="muted">(none)</span>}</td>
                    <td className="mono small">{r.domain}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="small muted">
              Sent from {open.envelope.remote_ip}
              {open.envelope.tls ? " over TLS" : ""}
              {open.envelope.authenticated_user ? ` as ${open.envelope.authenticated_user}` : ""} · {size(open.size)}
            </div>
            {Object.entries(open.deliveries || {}).map(([fn, d]) => (
              <div key={fn} className={d.ok ? "small" : "form-error"}>
                {fn}: {d.ok ? "ran" : d.error}
              </div>
            ))}
            {open.mail.attachments.length > 0 && (
              <div className="row-gap wrap">
                {open.mail.attachments.map((a, i) => (
                  <button key={i} type="button" className="btn btn-sm" onClick={() => void download("attachment", open.id, i, a.filename || `attachment-${i}`)}>
                    📎 {a.filename || "(unnamed)"} · {a.content_type} · {size(a.size)}
                  </button>
                ))}
              </div>
            )}
            <Segmented
              label="View"
              value={view}
              onChange={(v) => setView(v as typeof view)}
              options={[
                { value: "text", label: "Text", disabled: !open.mail.text },
                { value: "html", label: "HTML", disabled: !open.mail.html },
                { value: "headers", label: "Headers" },
              ]}
            />
            {view === "text" && <pre className="mono small pre-wrap">{open.mail.text}</pre>}
            {view === "html" && <iframe title="HTML body" sandbox="" className="mail-html" srcDoc={open.mail.html} style={{ width: "100%", minHeight: 300, border: "1px solid var(--border)" }} />}
            {view === "headers" && (
              <pre className="mono small pre-wrap">
                {Object.entries(open.mail.headers)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join("\n")}
              </pre>
            )}
          </div>
        )}
      </div>
    </Drawer>
  );
}
