import { useCallback, useEffect, useMemo, useState } from "react";
import { Drawer } from "../../components/Drawer";
import { api, errorMessage } from "../api";
import { useJob } from "../jobs";
import { useStudio } from "../store";
import type { JsonObject, OnPremCatalogItem, OnPremService, OnPremServicesState, OnPremTarget } from "../types";
import { asObject, NAME_RE, uniqueName } from "../util";
import { JobConsole } from "./Console";
import { useConfirm, useToast } from "./Feedback";
import { FormRow, NumberInput, Segmented, TextInput, Toggle } from "./Forms";
import type { GuideRef } from "./ResourceGuides";

const GROUPS: { title: string; match: (c: OnPremCatalogItem) => boolean }[] = [
  { title: "Queues & streams", match: (c) => c.roles.includes("queue") || c.roles.includes("stream") },
  { title: "Storage", match: (c) => c.roles.includes("storage") },
  { title: "Databases", match: (c) => c.roles.includes("database") },
  { title: "Caches & other", match: (c) => c.kind === "service" && (c.roles.includes("cache") || c.roles.includes("custom")) },
  { title: "Server runtimes (SSH)", match: (c) => c.kind === "runtime" },
];
const ROLE_LABEL: Record<string, string> = {
  queue: "Queue",
  stream: "Stream (consumer group)",
  cache: "Cache",
  database: "Database",
  storage: "Bucket",
  custom: "Service",
};
// Roles a function can be triggered by (others are only bound as connection values).
const TRIGGER_ROLES = ["queue", "stream", "storage"];

export function OnPremServices({ onGuide }: { onGuide: (ref: GuideRef) => void }) {
  const [state, setState] = useState<OnPremServicesState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<OnPremCatalogItem | null | "pick">(null);
  const [details, setDetails] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const { draft, replace } = useStudio();
  const confirm = useConfirm();
  const toast = useToast();

  const load = useCallback(() => {
    api
      .onpremServices()
      .then((s) => {
        setState(s);
        setError(null);
      })
      .catch((exc: unknown) => setError(errorMessage(exc)));
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    if (job.status === "succeeded" || job.status === "failed") load();
  }, [job.status, load]);

  const inProject = (key: string) => key in asObject(draft.resources) || key in asObject(asObject(asObject(draft.providers).onprem).resources);

  const addToProject = async (svc: OnPremService, attach?: string[]) => {
    try {
      const { raw } = await api.onpremApply(draft, svc.key, attach);
      replace(raw);
      toast(`Added ${svc.key} to pskit.yml${attach && attach.length ? ` with triggers on ${attach.join(", ")}` : ""} (not saved yet)`, "ok");
    } catch (exc) {
      toast(errorMessage(exc), "err");
    }
  };

  const backup = async (svc: OnPremService) => {
    try {
      setJobId((await api.onpremBackup(svc.key)).job_id);
    } catch (exc) {
      toast(errorMessage(exc), "err");
    }
  };

  const remove = async (svc: OnPremService) => {
    const runtime = svc.kind === "runtime";
    const answer = await confirm({
      title: `Remove ${svc.key}?`,
      body: runtime
        ? `${svc.service} stays installed on ${svc.target_label}; pskit only forgets it.`
        : `Stops and deletes the ${svc.service} container on ${svc.target_label}. Its data volume is kept unless you choose "Delete data too". The resource and its triggers are removed from pskit.yml (not saved yet).`,
      confirmLabel: runtime ? "Forget" : "Remove, keep data",
      extra: runtime || !svc.volume ? undefined : [{ label: "Delete data too", value: "delete-data", danger: true }],
      danger: true,
    });
    if (answer !== "confirm" && answer !== "delete-data") return;
    try {
      setJobId((await api.onpremRemove(svc.key, answer === "delete-data")).job_id);
      if (!runtime && inProject(svc.key)) replace((await api.onpremUnapply(draft, svc.key)).raw);
    } catch (exc) {
      toast(errorMessage(exc), "err");
    }
  };

  return (
    <div className="card card-pad onprem-services">
      <div className="row-between">
        <div>
          <h3>On-prem services</h3>
          <p className="muted small">
            One click starts Redis, Kafka, RabbitMQ, NATS, PostgreSQL, MinIO and more with Docker, on this machine or any SSH server (the
            functions' server or another one), and adds it as a resource. Passwords go to <code className="mono">{state?.env_file || ".env.onprem"}</code>,
            which the host loads; never into pskit.yml.
          </p>
        </div>
        <span className="row-gap">
          <button type="button" className="btn" onClick={() => onGuide({ cloud: "onprem", page: "services" })}>
            Guide
          </button>
          <button type="button" className="btn btn-primary" onClick={() => setCreating("pick")} disabled={!state}>
            + Create service
          </button>
        </span>
      </div>
      {error && <div className="form-error">{error}</div>}
      {state && !state.services.length && <p className="muted">Nothing created yet.</p>}
      {state && state.services.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Key</th>
              <th>Service</th>
              <th>Used as</th>
              <th>Where</th>
              <th>Endpoint</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {state.services.map((svc) => (
              <tr key={svc.key}>
                <td>
                  <b className="mono">{svc.key}</b>
                </td>
                <td>{state.catalog.find((c) => c.id === svc.service)?.label || svc.service}</td>
                <td>{svc.role ? ROLE_LABEL[svc.role] || svc.role : "runtime"}</td>
                <td className="small">{svc.target_label}</td>
                <td className="mono small">{svc.port ? `${svc.address}:${svc.port}` : "—"}</td>
                <td>
                  <span className="row-gap">
                    <button type="button" className="btn btn-sm" onClick={() => setDetails(svc.key)}>
                      Details
                    </button>
                    {svc.kind === "service" && !inProject(svc.key) && (
                      <button type="button" className="btn btn-sm" onClick={() => void addToProject(svc)} title="Add the resource (and its triggers) to pskit.yml">
                        Add to pskit.yml
                      </button>
                    )}
                    {state.catalog.find((c) => c.id === svc.service)?.backup && (
                      <button type="button" className="btn btn-sm" onClick={() => void backup(svc)}>
                        Backup
                      </button>
                    )}
                    <button type="button" className="btn btn-sm btn-danger" onClick={() => void remove(svc)}>
                      Remove
                    </button>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {jobId && !creating && <JobConsole job={job} onCancel={() => setJobId(null)} />}
      {creating && state && (
        <CreateDrawer
          state={state}
          initial={creating === "pick" ? null : creating}
          onClose={() => setCreating(null)}
          onCreated={(svc, attach) => {
            load();
            void addToProject(svc, attach);
          }}
        />
      )}
      {details && <DetailsDrawer serviceKey={details} onClose={() => setDetails(null)} />}
    </div>
  );
}

function CreateDrawer({
  state,
  initial,
  onClose,
  onCreated,
}: {
  state: OnPremServicesState;
  initial: OnPremCatalogItem | null;
  onClose: () => void;
  onCreated: (svc: OnPremService, attach: string[]) => void;
}) {
  const { draft } = useStudio();
  const toast = useToast();
  const [item, setItem] = useState<OnPremCatalogItem | null>(initial);
  const taken = useMemo(
    () => [...Object.keys(asObject(draft.resources)), ...state.services.map((s) => s.key)],
    [draft.resources, state.services],
  );
  const [key, setKey] = useState("");
  const [role, setRole] = useState("");
  const [target, setTarget] = useState<OnPremTarget>(state.default_target.method === "ssh" ? state.default_target : { method: "local" });
  const [ssh, setSsh] = useState<OnPremTarget>({ method: "ssh", user: "root", port: 22, ...(state.default_target.method === "ssh" ? state.default_target : {}) });
  const [port, setPort] = useState<number | undefined>(undefined);
  const [address, setAddress] = useState("");
  const [image, setImage] = useState("");
  const [attach, setAttach] = useState<string[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const job = useJob(jobId);
  const functions = Object.keys(asObject(draft.functions));

  const choose = (c: OnPremCatalogItem) => {
    setItem(c);
    setRole(c.roles[0] || "");
    setPort(undefined);
    setImage("");
    setKey(uniqueName(c.kind === "runtime" ? c.id : c.roles[0] === "database" ? `${c.id}-db` : c.roles[0] === "storage" ? "files" : c.id === "kafka" ? "events" : "jobs", taken));
    if (c.kind === "runtime") setTarget(ssh);
  };
  useEffect(() => {
    if (initial) choose(initial);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (job.status !== "succeeded" || !item) return;
    api
      .onpremServices()
      .then((s) => {
        const svc = s.services.find((x) => x.key === key);
        if (svc && svc.kind === "service") onCreated(svc, TRIGGER_ROLES.includes(role) ? attach : []);
        else toast(`${item.label} is installed`, "ok");
      })
      .catch((exc: unknown) => toast(errorMessage(exc), "err"));
  }, [job.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const keyError = !NAME_RE.test(key) ? "letters, digits, - and _; start with a letter" : taken.includes(key) ? "already used" : null;
  const sshError = target.method === "ssh" && !(ssh.host || "").trim() ? "the server's host name or IP" : null;

  const submit = async () => {
    if (!item || keyError || sshError) return;
    setSubmitError(null);
    const body: JsonObject = { key, service: item.id, target: (target.method === "ssh" ? { ...ssh, method: "ssh" } : { method: "local" }) as unknown as JsonObject };
    if (role) body.role = role;
    if (port) body.port = port;
    if (address.trim()) body.address = address.trim();
    if (image.trim()) body.image = image.trim();
    if (TRIGGER_ROLES.includes(role)) body.attach = attach;
    try {
      setJobId((await api.onpremCreate(body)).job_id);
    } catch (exc) {
      setSubmitError(errorMessage(exc));
    }
  };

  const running = job.status === "running";
  return (
    <Drawer
      open
      title={item ? `Create ${item.label}` : "Create a service"}
      onClose={onClose}
      actions={
        item && (
          <button type="button" className="btn btn-primary" disabled={!!keyError || !!sshError || running || job.status === "succeeded"} onClick={() => void submit()}>
            {running ? "Creating…" : job.status === "succeeded" ? "Created" : item.kind === "runtime" ? "Install" : "Create"}
          </button>
        )
      }
    >
      {!item && (
        <div className="catalog">
          {GROUPS.map((g) => {
            const items = state.catalog.filter(g.match);
            return (
              items.length > 0 && (
                <div key={g.title} className="catalog-group">
                  <h4>{g.title}</h4>
                  <div className="catalog-grid">
                    {items.map((c) => (
                      <button key={c.id} type="button" className="catalog-card" onClick={() => choose(c)}>
                        <b>{c.label}</b>
                        <span className="muted small">{c.notes || c.image}</span>
                        {c.port && <span className="mono small">port {c.port}</span>}
                      </button>
                    ))}
                  </div>
                </div>
              )
            );
          })}
        </div>
      )}
      {item && (
        <div className="form">
          <p className="muted small">
            {item.notes}{" "}
            <button type="button" className="btn btn-sm" onClick={() => setItem(null)}>
              Pick another
            </button>
          </p>
          {item.kind === "service" && (
            <FormRow label="Resource key" required error={keyError} help="How handlers and triggers refer to it: resource(&quot;key&quot;).">
              <TextInput mono value={key} onChange={setKey} invalid={!!keyError} />
            </FormRow>
          )}
          {item.roles.length > 1 && (
            <FormRow label="Use as">
              <Segmented label="Use as" value={role} onChange={setRole} options={item.roles.map((r) => ({ value: r, label: ROLE_LABEL[r] || r }))} />
            </FormRow>
          )}
          <FormRow
            label="Where"
            help={
              target.method === "ssh"
                ? "Any server you can reach with OpenSSH: the functions' server or a different one. Docker is installed there when missing."
                : "Docker on this machine. Functions deployed elsewhere cannot reach 127.0.0.1; use an SSH server for them."
            }
          >
            <Segmented
              label="Where"
              value={target.method}
              onChange={(m) => setTarget(m === "ssh" ? ssh : { method: "local" })}
              options={[
                { value: "local", label: "This machine", disabled: item.kind === "runtime" },
                { value: "ssh", label: "SSH server" },
              ]}
            />
          </FormRow>
          {target.method === "ssh" && (
            <div className="driver-box card card-pad">
              <FormRow label="Host" required error={sshError}>
                <TextInput mono value={ssh.host || ""} placeholder="10.0.0.5" onChange={(v) => setSsh((s) => ({ ...s, host: v }))} />
              </FormRow>
              <FormRow label="User">
                <TextInput mono value={ssh.user || ""} placeholder="ubuntu" onChange={(v) => setSsh((s) => ({ ...s, user: v }))} />
              </FormRow>
              <FormRow label="SSH port">
                <NumberInput integer min={1} max={65535} value={ssh.port} onChange={(v) => setSsh((s) => ({ ...s, port: v }))} />
              </FormRow>
              <FormRow label="Private key (.pem)" help="Empty: the SSH agent or your default key.">
                <TextInput mono value={ssh.identity_file || ""} placeholder="~/.ssh/server.pem" onChange={(v) => setSsh((s) => ({ ...s, identity_file: v }))} />
              </FormRow>
              <Toggle checked={!!ssh.sudo} onChange={(b) => setSsh((s) => ({ ...s, sudo: b }))} label="Use sudo (the SSH user is not root and not in the docker group)" />
            </div>
          )}
          {item.kind === "service" && (
            <>
              <FormRow label="Port" help={`Published on the server. Default ${item.port}${item.extra_ports?.length ? ` (also ${item.extra_ports.join(", ")})` : ""}.`}>
                <NumberInput integer min={1} max={65535} value={port} placeholder={String(item.port || "")} onChange={setPort} />
              </FormRow>
              {TRIGGER_ROLES.includes(role) && (
                <FormRow label="Trigger functions" help="Adds a queue / storage event on resource: key to each selected function.">
                  <div className="check-grid">
                    {functions.map((f) => (
                      <label key={f} className="check">
                        <input type="checkbox" checked={attach.includes(f)} onChange={(e) => setAttach((a) => (e.target.checked ? [...a, f] : a.filter((x) => x !== f)))} />
                        {f}
                      </label>
                    ))}
                    {!functions.length && <span className="muted">No functions yet.</span>}
                  </div>
                </FormRow>
              )}
              <details className="advanced">
                <summary>Advanced</summary>
                <FormRow label="Address functions use" help="Default: the SSH host, or 127.0.0.1 here. Set a private IP or DNS name if different.">
                  <TextInput mono value={address} onChange={setAddress} placeholder={target.method === "ssh" ? ssh.host || "" : "127.0.0.1"} />
                </FormRow>
                <FormRow label="Docker image">
                  <TextInput mono value={image} onChange={setImage} placeholder={item.image || ""} />
                </FormRow>
              </details>
            </>
          )}
          {submitError && <div className="form-error">{submitError}</div>}
          {jobId && <JobConsole job={job} />}
        </div>
      )}
    </Drawer>
  );
}

function DetailsDrawer({ serviceKey, onClose }: { serviceKey: string; onClose: () => void }) {
  const [info, setInfo] = useState<OnPremService | null>(null);
  const [reveal, setReveal] = useState(false);
  const [logs, setLogs] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    api
      .onpremStatus(serviceKey, reveal)
      .then(setInfo)
      .catch((exc: unknown) => setError(errorMessage(exc)));
  }, [serviceKey, reveal]);
  const ok = info?.state === "running" || info?.state === "installed";
  return (
    <Drawer open title={`Service ${serviceKey}`} onClose={onClose}>
      {error && <div className="form-error">{error}</div>}
      {!info && !error && <p className="muted">Checking…</p>}
      {info && (
        <div className="form">
          <p>
            <span className={`badge ${ok ? (info.ready === false ? "badge-warn" : "badge-ok") : "badge-err"}`}>
              {info.state}
              {info.ready === false ? " · not ready" : info.ready ? " · ready" : ""}
            </span>{" "}
            <span className="muted small">
              {info.service} on {info.target_label}
              {info.started_at ? ` · started ${info.started_at.slice(0, 19).replace("T", " ")}` : ""}
            </span>
          </p>
          {info.error && <div className="form-error">{info.error}</div>}
          {info.port && (
            <FormRow label="Endpoint">
              <code className="mono">
                {info.address}:{info.port}
              </code>
            </FormRow>
          )}
          {info.env && Object.keys(info.env).length > 0 && (
            <FormRow label={`Credentials (${info.env_file})`} help="Handlers and drivers read these variables; the host loads the file at start.">
              <div className="stack-xs">
                {Object.entries(info.env).map(([k, v]) => (
                  <code key={k} className="mono small">
                    {k}={v}
                  </code>
                ))}
                <Toggle checked={reveal} onChange={setReveal} label="Show values" />
              </div>
            </FormRow>
          )}
          {info.resource && (
            <FormRow label="Resource in pskit.yml">
              <pre className="md-code">{JSON.stringify(info.resource, null, 2)}</pre>
            </FormRow>
          )}
          {info.backups && info.backups.length > 0 && (
            <FormRow label="Backups">
              <ul className="small">
                {info.backups
                  .slice()
                  .reverse()
                  .map((b) => (
                    <li key={b.path} className="mono">
                      {b.path} ({b.size} bytes, {b.at})
                    </li>
                  ))}
              </ul>
            </FormRow>
          )}
          {info.container && (
            <FormRow label="Container logs">
              <div className="stack-xs">
                <button type="button" className="btn btn-sm" onClick={() => api.onpremLogs(serviceKey).then((r) => setLogs(r.text)).catch((exc: unknown) => setError(errorMessage(exc)))}>
                  {logs === null ? "Show logs" : "Refresh"}
                </button>
                {logs !== null && <pre className="md-code console-log">{logs || "(empty)"}</pre>}
              </div>
            </FormRow>
          )}
        </div>
      )}
    </Drawer>
  );
}
