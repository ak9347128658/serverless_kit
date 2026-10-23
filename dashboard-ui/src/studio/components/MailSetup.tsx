import { useEffect, useState } from "react";
import { Drawer } from "../../components/Drawer";
import { api, ApiError, errorMessage } from "../api";
import { useStudio } from "../store";
import type { JsonObject, MailSetup as MailSetupResult } from "../types";
import { asArray, asObject, languageOf, NAME_RE, str, uniqueName } from "../util";
import { useToast } from "./Feedback";
import { FormRow, NumberInput, Segmented, TextInput, Toggle } from "./Forms";
import { useOnPremServers } from "./ServerPicker";

const STATUS: Record<string, { label: string; tone: string }> = {
  ok: { label: "✓ live", tone: "" },
  missing: { label: "missing", tone: "badge-warn" },
  different: { label: "different", tone: "badge-err" },
  keep: { label: "keep as is", tone: "" },
};

function copy(text: string, toast: ReturnType<typeof useToast>) {
  navigator.clipboard.writeText(text).then(
    () => toast("Copied", "ok"),
    () => toast("Copy failed: select the text instead", "warn"),
  );
}

/**
 * Receive mail for your domain: create the smtp resource and its triggers, then the DNS records to add at the
 * domain provider (A, MX, wildcard MX, SPF, DMARC; DKIM explained) with a live check.
 * `resourceKey` opens it for an existing smtp resource (only the DNS part changes nothing in pskit.yml).
 */
export function MailSetup({ resourceKey, onClose }: { resourceKey?: string; onClose: () => void }) {
  const { draft, replace, state, providerName } = useStudio();
  const toast = useToast();
  const resources = asObject(draft.resources);
  const existing = resourceKey ? asObject(resources[resourceKey]) : null;
  const options = asObject(existing?.options);
  const firstDomain = str(asArray(options.domains)[0]);
  const stage = state.target?.stage || "dev";
  const { servers, deployed } = useOnPremServers(providerName === "onprem", stage);

  // Where mail is received: `inbound.kavion.ai` is stored as domains: [inbound.kavion.ai]; the zone is kavion.ai.
  const [domain, setDomain] = useState(() => (firstDomain ? firstDomain.split(".").slice(-2).join(".") : ""));
  const [mode, setMode] = useState<"subdomain" | "domain">(() => (firstDomain && firstDomain.split(".").length <= 2 ? "domain" : "subdomain"));
  const [sub, setSub] = useState(() => (firstDomain && firstDomain.split(".").length > 2 ? firstDomain.split(".").slice(0, -2).join(".") : "inbound"));
  const [subdomains, setSubdomains] = useState(options.subdomains !== false);
  const [mx, setMx] = useState(str(options.hostname));
  const [machine, setMachine] = useState("");
  const [ip, setIp] = useState("");
  const [port, setPort] = useState<number | undefined>(options.port === undefined ? 2525 : Number(options.port));
  const [auth, setAuth] = useState(options.auth === true);
  const [verify, setVerify] = useState(options.verify_senders !== false);
  const [block, setBlock] = useState(existing ? options.block_spoofed === true : true);
  const functions = Object.keys(asObject(draft.functions));
  const [attach, setAttach] = useState<string[]>([]);
  const [newFn, setNewFn] = useState(existing ? "" : uniqueName("on-mail", functions));
  const [key, setKey] = useState(resourceKey || uniqueName("inbound-mail", Object.keys(resources)));
  const [result, setResult] = useState<MailSetupResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState(!!resourceKey);

  const receive = mode === "subdomain" && sub.trim() ? `${sub.trim().toLowerCase()}.${domain.trim().toLowerCase()}` : domain.trim().toLowerCase();
  const mxName = mx.trim() || (domain.trim() ? `mx.${domain.trim().toLowerCase()}` : "");

  const ask = async (check: boolean, portCheck = false) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.mailSetup({
        domain: domain.trim(),
        subdomain: mode === "subdomain" ? sub.trim() : "",
        mx: mxName,
        ip: machine === "ip" ? ip.trim() : "",
        server: machine && machine !== "ip" ? machine : "",
        stage,
        subdomains,
        check,
        port: port || 2525,
        port_check: portCheck,
      });
      setResult(r);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : errorMessage(exc));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (resourceKey && domain) void ask(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const keyError = !NAME_RE.test(key) ? "letters, digits, - and _" : !resourceKey && key in resources ? "already exists" : null;
  const fnError = newFn && (!NAME_RE.test(newFn) || newFn in asObject(draft.functions)) ? "a new, unused function name" : null;

  const create = async () => {
    const opts: JsonObject = { ...options, domains: [receive], hostname: mxName, port: port || 2525 };
    if (!subdomains) opts.subdomains = false;
    else delete opts.subdomains;
    if (auth) opts.auth = true;
    else delete opts.auth;
    if (!verify) opts.verify_senders = false;
    else delete opts.verify_senders;
    if (block) opts.block_spoofed = true;
    else delete opts.block_spoofed;
    const nextResources = { ...resources, [key]: { ...(existing || {}), type: "smtp", options: opts } };
    const nextFunctions: JsonObject = { ...asObject(draft.functions) };
    const trigger = { smtp: { resource: key } };
    for (const fn of attach) {
      const body = asObject(nextFunctions[fn]);
      const events = asArray(body.events);
      if (!events.some((e) => str(asObject(asObject(e).smtp).resource) === key || str(asObject(e).smtp) === key)) {
        nextFunctions[fn] = { ...body, events: [...events, trigger] };
      }
    }
    const language = languageOf(str(asObject(draft.provider).runtime, "python3.12"));
    if (newFn) {
      const module = newFn.replace(/-/g, "_");
      nextFunctions[newFn] = { handler: `inbox_handlers.${module}.handle`, description: `Runs for mail to @${receive}`, events: [trigger] };
      const source = str(asObject(draft.package).source, ".").replace(/\/$/, "");
      const base = source && source !== "." ? `${source}/inbox_handlers` : "inbox_handlers";
      const path = `${base}/${module}.${language === "node" ? "js" : "py"}`;
      try {
        await api.file(path);
      } catch {
        try {
          const stub = await api.mailHandlerStub(language, newFn);
          await api.writeFile(path, stub.text);
          if (language !== "node") {
            await api.file(`${base}/__init__.py`).catch(() => api.writeFile(`${base}/__init__.py`, ""));
          }
          toast(`Wrote ${path}`, "ok");
        } catch (exc) {
          toast(`Could not write ${path}: ${errorMessage(exc)}`, "warn");
        }
      }
    }
    replace({ ...draft, resources: nextResources, functions: nextFunctions });
    setCreated(true);
    toast(`Added ${key} for @${receive}${attach.length || newFn ? ` and its triggers` : ""}. Save, deploy, then add the DNS records below.`, "ok");
    void ask(true);
  };

  const machineLabel = deployed?.host ? `Where this stage is deployed (${deployed.label})` : "Where this stage is deployed";
  return (
    <Drawer open title={resourceKey ? `Mail domain: ${resourceKey}` : "Receive mail for your domain"} onClose={onClose}>
      <div className="stack">
        <p className="muted small">
          People send mail to an address at your domain; DNS tells their mail servers to deliver it to this project's SMTP server; every function attached
          to it runs with the mail (sender, recipient with subdomain, body, attachments, SPF / DKIM / DMARC results).
        </p>
        {error && <div className="form-error">{error}</div>}

        <h3>1. Your domain</h3>
        <FormRow label="Domain" required help="The domain you own at your domain provider (GoDaddy, Cloudflare, Namecheap, Route 53 ...).">
          <div className="row-gap">
            <TextInput mono value={domain} onChange={setDomain} placeholder="kavion.ai" />
            <button type="button" className="btn btn-sm" disabled={!domain.trim() || busy} onClick={() => void ask(true)}>
              Look up its DNS
            </button>
          </div>
        </FormRow>
        <FormRow label="Receive mail at" help="A subdomain keeps your company's mail (Microsoft 365, Google ...) where it is; only the subdomain's mail comes here.">
          <Segmented
            label="Receive mail at"
            value={mode}
            onChange={(v) => setMode(v as "subdomain" | "domain")}
            options={[
              { value: "subdomain", label: `A subdomain: anything@${sub || "inbound"}.${domain || "kavion.ai"}` },
              { value: "domain", label: `The whole domain: anything@${domain || "kavion.ai"}` },
            ]}
          />
        </FormRow>
        {mode === "subdomain" && (
          <FormRow label="Subdomain">
            <TextInput mono value={sub} onChange={setSub} placeholder="inbound" />
          </FormRow>
        )}
        <Toggle checked={subdomains} onChange={setSubdomains} label={`Also anything@<name>.${receive || "…"} (support., sales. …): the subdomain is shown separately in events and logs`} />
        {result && result.current_mx.length > 0 && (
          <div className="small muted">
            Mail for {result.domain} goes to <span className="mono">{result.current_mx.join(", ")}</span> today.
          </div>
        )}
        {(result?.warnings || []).map((w, i) => (
          <div key={i} className={`hint-box ${w.includes("ALL mail") ? "err" : "warn"}`}>
            {w}
          </div>
        ))}

        <h3>2. Mail server</h3>
        <FormRow label="Machine" help="The public IP of this machine goes into the A record.">
          <div className="row-gap wrap">
            <select className="select" value={machine} onChange={(e) => setMachine(e.target.value)} aria-label="Machine">
              <option value="">{machineLabel}</option>
              {servers.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name} ({s.host})
                </option>
              ))}
              <option value="ip">Enter the IP…</option>
            </select>
            {machine === "ip" && <TextInput mono value={ip} onChange={setIp} placeholder="13.68.248.150" />}
          </div>
        </FormRow>
        <div className="row-gap wrap">
          <FormRow label="Mail server name" help="Gets an A record; the MX record points to it.">
            <TextInput mono value={mx} onChange={setMx} placeholder={mxName || "mx.kavion.ai"} />
          </FormRow>
          <FormRow label="SMTP port" help="2525 runs without root; open 25 in the firewall and redirect it here.">
            <NumberInput integer min={1} max={65535} value={port} onChange={setPort} />
          </FormRow>
        </div>

        <h3>3. Security</h3>
        <Toggle checked={verify} onChange={setVerify} label="Check every sender's SPF, DKIM and DMARC (results in event.data.authentication and the logs)" />
        <Toggle checked={block} onChange={setBlock} label={`Block spoofed mail: DMARC fails with p=reject/quarantine, or From: @${domain || "your domain"} without SPF / DKIM passing`} />
        <FormRow label="Password (SMTP AUTH)" help="Off for mail from the internet (mail servers never log in). On for a private server your apps or devices log in to; add users in the resource afterwards.">
          <Segmented
            label="Password"
            value={auth ? "on" : "off"}
            onChange={(v) => setAuth(v === "on")}
            options={[
              { value: "off", label: "Off: receive from the internet" },
              { value: "on", label: "On: senders log in" },
            ]}
          />
        </FormRow>

        {!resourceKey && (
          <>
            <h3>4. Run these functions</h3>
            <div className="check-grid">
              {functions.map((f) => (
                <label key={f} className="check">
                  <input type="checkbox" checked={attach.includes(f)} onChange={(e) => setAttach(e.target.checked ? [...attach, f] : attach.filter((x) => x !== f))} />
                  {f}
                </label>
              ))}
            </div>
            <FormRow label="New function" error={fnError} help="Creates the function, its smtp trigger and a ready handler file (inbox_handlers/…) that logs the mail. Empty: none.">
              <TextInput mono value={newFn} onChange={setNewFn} placeholder="on-mail" />
            </FormRow>
            <FormRow label="Resource key" error={keyError}>
              <TextInput mono value={key} onChange={setKey} />
            </FormRow>
            <button type="button" className="btn btn-primary" disabled={!domain.trim() || !!keyError || !!fnError || busy || created} onClick={() => void create()}>
              {created ? "Added: save and deploy" : `Add the mail server for @${receive || "…"}`}
            </button>
          </>
        )}
        {resourceKey && (
          <button type="button" className="btn btn-primary" disabled={!domain.trim() || busy} onClick={() => void create()}>
            Apply these settings to {resourceKey}
          </button>
        )}

        <h3>{resourceKey ? "4" : "5"}. DNS records at your domain provider</h3>
        <p className="muted small">
          Add these in the DNS settings of <b>{domain || "your domain"}</b>. Name is what most providers call Host or Name (<code className="mono">@</code> = the
          domain itself). Changes take minutes to a few hours. Then <b>Check DNS</b> until the required ones are live.
        </p>
        <div className="row-gap wrap">
          <button type="button" className="btn btn-sm btn-primary" disabled={!domain.trim() || busy} onClick={() => void ask(true)}>
            {busy ? "Checking…" : "Check DNS"}
          </button>
          <button type="button" className="btn btn-sm" disabled={!domain.trim() || busy} onClick={() => void ask(true, true)} title="TCP connect from this machine (your network may block outgoing port 25)">
            Check DNS and ports
          </button>
          {result && <span className={`badge ${result.ready ? "" : "badge-warn"}`}>{result.ready ? "required records are live" : "not ready yet"}</span>}
        </div>
        {result && (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Name / Host</th>
                  <th>Value / Points to</th>
                  <th>Priority</th>
                  <th>Live</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {result.records.map((r, i) => (
                  <tr key={i} title={r.why}>
                    <td>
                      <b>{r.type}</b>
                      {!r.required && <div className="muted small">recommended</div>}
                    </td>
                    <td className="mono small">{r.name}</td>
                    <td className="mono small">{r.value}</td>
                    <td className="small">{r.priority ?? ""}</td>
                    <td>
                      {r.status ? <span className={`badge ${STATUS[r.status]?.tone || ""}`}>{STATUS[r.status]?.label || r.status}</span> : null}
                      {r.found && r.found.length > 0 && r.status !== "ok" && <div className="mono small muted">{r.found.join(" · ")}</div>}
                    </td>
                    <td>
                      <button type="button" className="btn btn-sm" onClick={() => copy(r.value, toast)}>
                        Copy
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <ul className="small">
              {result.records.map((r, i) => (
                <li key={i}>
                  <b>
                    {r.type} {r.name}
                  </b>
                  : {r.why}
                </li>
              ))}
              {result.notes.map((n, i) => (
                <li key={`n${i}`} className="muted">
                  {n}
                </li>
              ))}
            </ul>
            {(result.port_25 || result.port_server) && (
              <div className="small">
                Port 25 on {result.ip}: <b>{result.port_25}</b>
                {result.port_server && (
                  <>
                    {" "}
                    · port {port}: <b>{result.port_server}</b>
                  </>
                )}
              </div>
            )}
            <p className="muted small">
              Test without DNS: Resources → {key} → Mailbox → Send test mail, or <code className="mono">pskit trigger -f {newFn || attach[0] || "<function>"} -e 0</code>. Full
              guide: docs/smtp/README.md.
            </p>
          </>
        )}
      </div>
    </Drawer>
  );
}
