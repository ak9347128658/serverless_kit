import { useCallback, useEffect, useMemo, useState } from "react";
import { Drawer } from "../../components/Drawer";
import { api, errorMessage } from "../api";
import { JobConsole } from "../components/Console";
import { useConfirm, useToast } from "../components/Feedback";
import { FormRow, NumberInput, Segmented, TextInput, Toggle } from "../components/Forms";
import { PageHeader } from "../components/Misc";
import { ServerForm } from "../components/OnPremMachines";
import { useJob } from "../jobs";
import { href, navigate } from "../route";
import { useStudio } from "../store";
import type { ClusterNodeStatus, ClusterStatus, JsonObject, OnPremCluster, OnPremClustersState, Orchestrator } from "../types";
import { asObject } from "../util";

const NAME_RE = /^[a-z][a-z0-9-]{0,30}$/;
const REFRESH_MS = 30000;

/** Scaling clusters: a master server plus joined servers running the functions (Swarm, Kubernetes, Nomad). */
export function ClusterView({ rest }: { rest: string[] }) {
  const [state, setState] = useState<OnPremClustersState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const { providerName } = useStudio();

  const load = useCallback(() => {
    api
      .clusters()
      .then((s) => {
        setState(s);
        setError(null);
      })
      .catch((exc: unknown) => setError(errorMessage(exc)));
  }, []);
  useEffect(load, [load]);

  const selected = state?.clusters.find((c) => c.name === rest[0]) || state?.clusters[0] || null;

  return (
    <div className="page">
      <PageHeader
        title="Scaling cluster"
        subtitle={
          <>
            Run this project&apos;s functions on several servers with <b>Docker Swarm</b>, <b>Kubernetes (k3s)</b> or <b>HashiCorp Nomad</b>: pick a master
            server, join more servers whenever you need (also while functions run), watch every server&apos;s CPU, memory and disk, and get an alert
            (one of your functions, e.g. a mail to DevOps, and / or a webhook) when every server is full. Without a cluster the functions run on one
            server as before; resources always stay on one server.
          </>
        }
        actions={
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            + New cluster
          </button>
        }
      />
      {providerName !== "onprem" && (
        <div className="hint-box warn">
          Clusters run on-prem functions. The project&apos;s provider is <b>{providerName || "not set"}</b>: switch it to <b>onprem</b> in{" "}
          <a href={href("overview")}>Overview</a> to deploy to a cluster.
        </div>
      )}
      {error && <div className="form-error">{error}</div>}
      {state && !state.clusters.length && (
        <div className="card card-pad">
          <h3>No cluster yet</h3>
          <ol className="small cluster-steps">
            <li>
              Save your servers once (<a href={href("resources")}>Resources → On-prem services → Servers</a>, or <b>Add a server</b> below).
            </li>
            <li>
              <b>+ New cluster</b>: choose the orchestrator and the master server. pskit installs it over SSH.
            </li>
            <li>
              <b>Use for this project</b>, set the replicas, then deploy. Join more servers any time.
            </li>
          </ol>
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            + New cluster
          </button>
        </div>
      )}
      {state && state.clusters.length > 1 && (
        <div className="row-gap wrap cluster-tabs">
          {state.clusters.map((c) => (
            <a key={c.name} href={href("cluster", c.name)} className={c.name === selected?.name ? "btn btn-sm btn-primary" : "btn btn-sm"}>
              {c.name} · {c.orchestrator_label}
            </a>
          ))}
        </div>
      )}
      {state && selected && <ClusterPanel key={selected.name} cluster={selected} state={state} reload={load} />}
      {creating && state && (
        <CreateClusterDrawer
          state={state}
          onClose={() => setCreating(false)}
          onCreated={(name) => {
            setCreating(false);
            load();
            navigate("cluster", name);
          }}
          onServersChanged={load}
        />
      )}
    </div>
  );
}

function ClusterPanel({ cluster, state, reload }: { cluster: OnPremCluster; state: OnPremClustersState; reload: () => void }) {
  const [status, setStatus] = useState<ClusterStatus | null>(state.last[cluster.name] || null);
  const [loading, setLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [joining, setJoining] = useState(false);
  const job = useJob(jobId);
  const confirm = useConfirm();
  const toast = useToast();

  const refresh = useCallback(
    (check = false) => {
      setLoading(true);
      (check ? api.clusterCheck(cluster.name) : api.clusterStatus(cluster.name))
        .then((s) => {
          setStatus(s);
          setStatusError(null);
          if (check && s.alert) {
            const a = s.alert;
            if ("skipped" in a) toast(`Alert skipped: cooldown (next in ${a.next_after_minutes} min)`, "info");
            else if (a.errors.length) toast(`Alert: ${a.errors.join("; ")}`, "err");
            else toast(`Alert sent to ${a.sent.join(", ")}`, "ok");
            reload();
          }
        })
        .catch((exc: unknown) => setStatusError(errorMessage(exc)))
        .finally(() => setLoading(false));
    },
    [cluster.name, reload, toast],
  );

  useEffect(() => {
    refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) refresh();
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (job.status === "succeeded" || job.status === "failed") {
      reload();
      refresh();
    }
  }, [job.status, reload, refresh]);

  const startJob = async (action: "join" | "leave" | "scale" | "remove", body: JsonObject = {}) => {
    try {
      const { job_id } = await api.clusterAction(cluster.name, action, body);
      setJobId(job_id);
    } catch (exc) {
      toast(errorMessage(exc), "err");
    }
  };

  const leave = async (server: string) => {
    const ok = await confirm({
      title: `Remove ${server} from ${cluster.name}?`,
      body: "Its replicas move to the other servers first, then its agent stops. The saved server is kept.",
      confirmLabel: "Remove server",
      danger: true,
    });
    if (ok === "confirm") void startJob("leave", { server });
  };

  const forget = async () => {
    const ok = await confirm({
      title: `Forget cluster ${cluster.name}?`,
      body: "pskit stops managing it. The servers and anything running on them are left as they are (remove the deployments first with Deploy → Remove).",
      confirmLabel: "Forget",
      danger: true,
    });
    if (ok === "confirm") void startJob("remove");
  };

  const nodes: ClusterNodeStatus[] =
    status?.nodes ||
    cluster.nodes.map((n) => ({ ...n, reachable: false, ready: false, state: "…", availability: "", over: [], full: false }) as ClusterNodeStatus);

  return (
    <>
      <div className="card card-pad">
        <div className="row-between wrap">
          <div>
            <h3>
              {cluster.name} <span className="muted small">· {cluster.orchestrator_label}</span>
            </h3>
            <div className="muted small">
              master <b className="mono">{cluster.master}</b> ({cluster.advertise}) · {cluster.nodes.length} server(s)
              {status && <> · checked {new Date(status.checked_at).toLocaleTimeString()}</>}
            </div>
          </div>
          <span className="row-gap wrap">
            <button type="button" className="btn btn-sm" disabled={loading} onClick={() => refresh()}>
              {loading ? "Checking…" : "Refresh"}
            </button>
            <button type="button" className="btn btn-sm" disabled={loading} onClick={() => refresh(true)} title="Check now and send the alert if every server is full">
              Check &amp; alert
            </button>
            <button type="button" className="btn btn-sm btn-danger" onClick={() => void forget()}>
              Forget
            </button>
          </span>
        </div>
        <ProjectBinding cluster={cluster} />
      </div>

      {statusError && <div className="form-error">{statusError}</div>}
      {status && <CapacityBanner status={status} onJoin={() => setJoining(true)} />}

      <div className="card card-pad">
        <div className="row-between">
          <div>
            <h3>Servers</h3>
            <p className="muted small">
              A server is <b>full</b> at {cluster.capacity.cpu}% CPU, {cluster.capacity.memory}% memory or {cluster.capacity.disk}% disk (change below). Refreshes
              every 30 s.
            </p>
          </div>
          <button type="button" className="btn btn-primary" onClick={() => setJoining(true)}>
            + Join server
          </button>
        </div>
        <table className="table cluster-nodes">
          <thead>
            <tr>
              <th>Server</th>
              <th>State</th>
              <th>CPU</th>
              <th>Memory</th>
              <th>Disk</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => (
              <tr key={n.server} className={n.full ? "node-full" : undefined}>
                <td>
                  <b className="mono">{n.server}</b> {n.role === "master" && <span className="badge badge-accent">master</span>}
                  <div className="muted small mono">
                    {n.node} · {n.address}
                    {n.cpus ? ` · ${n.cpus} CPU` : ""}
                    {n.memory_mb ? ` · ${Math.round(n.memory_mb / 1024)} GB` : ""}
                    {n.load ? ` · load ${n.load}` : ""}
                  </div>
                </td>
                <td>
                  {!n.reachable ? (
                    <span className="badge badge-err" title={n.error}>
                      offline
                    </span>
                  ) : n.availability && n.availability !== "Active" ? (
                    <span className="badge badge-warn">{n.availability.toLowerCase()}</span>
                  ) : (
                    <span className={`badge ${n.ready ? "badge-ok" : "badge-warn"}`}>{(n.state || "").toLowerCase()}</span>
                  )}
                  {n.full && <span className="badge badge-err">full</span>}
                  {!n.reachable && n.error && <div className="form-error small">{n.error}</div>}
                </td>
                <td>
                  <Meter value={n.cpu} limit={cluster.capacity.cpu} />
                </td>
                <td>
                  <Meter value={n.memory} limit={cluster.capacity.memory} />
                </td>
                <td>
                  <Meter value={n.disk} limit={cluster.capacity.disk} suffix={n.disk_gb ? ` of ${n.disk_gb} GB` : ""} />
                </td>
                <td>
                  {n.role !== "master" && (
                    <button type="button" className="btn btn-sm btn-danger" onClick={() => void leave(n.server)}>
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {status?.orchestrator_error && <div className="form-error small">Master: {status.orchestrator_error}</div>}
      </div>

      <Workloads cluster={cluster} status={status} onScale={(stack, replicas) => void startJob("scale", { stack, replicas })} />
      {jobId && <JobConsole job={job} />}
      <AlertSettings cluster={cluster} onSaved={reload} />
      {joining && (
        <JoinDrawer
          cluster={cluster}
          state={state}
          onClose={() => setJoining(false)}
          onJoin={(server, advertise) => {
            setJoining(false);
            void startJob("join", { server, advertise });
          }}
          onServersChanged={reload}
        />
      )}
    </>
  );
}

function Meter({ value, limit, suffix = "" }: { value?: number | null; limit: number; suffix?: string }) {
  if (value === undefined || value === null) return <span className="muted">–</span>;
  const tone = value >= limit ? "meter-err" : value >= limit * 0.8 ? "meter-warn" : "meter-ok";
  return (
    <div className={`meter ${tone}`} title={`${value}% (full at ${limit}%)${suffix}`}>
      <div className="meter-bar">
        <div className="meter-fill" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
        <div className="meter-limit" style={{ left: `${limit}%` }} />
      </div>
      <span className="meter-text mono small">
        {Math.round(value)}%{suffix}
      </span>
    </div>
  );
}

function CapacityBanner({ status, onJoin }: { status: ClusterStatus; onJoin: () => void }) {
  if (!status.full)
    return (
      <div className="hint-box">
        ✓ Capacity left on {status.nodes.filter((n) => n.reachable && !n.full).length} of {status.nodes.length} server(s).
      </div>
    );
  return (
    <div className="hint-box warn cluster-full">
      <b>⚠ The cluster is full:</b> {status.reasons.join("; ")}.
      {status.pending.length > 0 && (
        <ul className="small">
          {status.pending.slice(0, 5).map((p) => (
            <li key={p} className="mono">
              {p}
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn btn-sm btn-primary" onClick={onJoin}>
        + Join a server now
      </button>
    </div>
  );
}

/** `provider.options.cluster` of the open project: deploy its functions here, with how many replicas. */
function ProjectBinding({ cluster }: { cluster: OnPremCluster }) {
  const { draft, edit } = useStudio();
  const options = asObject(asObject(draft.provider).options);
  const raw = options.cluster;
  const current = typeof raw === "string" ? { name: raw } : asObject(raw);
  const used = current.name === cluster.name;
  const set = (patch: JsonObject | null) => {
    if (patch === null) edit(["provider", "options", "cluster"], undefined);
    else edit(["provider", "options", "cluster"], { ...(used ? current : {}), name: cluster.name, ...patch });
  };
  const num = (key: string, fallback: number) => (typeof current[key] === "number" ? (current[key] as number) : fallback);
  return (
    <div className="cluster-binding">
      <Toggle
        checked={used}
        onChange={(on) => set(on ? { replicas: num("replicas", 2) } : null)}
        label={
          <>
            Deploy this project&apos;s functions to <b>{cluster.name}</b> (<code className="mono">provider.options.cluster</code>)
          </>
        }
      />
      {used && (
        <div className="row-gap wrap">
          <FormRow label="Web replicas" help="Copies behind the load balancer (plus 1 main replica on the master)." inline>
            <NumberInput value={num("replicas", 2)} min={1} max={500} integer onChange={(v) => set({ replicas: v ?? 2 })} />
          </FormRow>
          <FormRow label="CPU per replica" help="Cores each replica reserves." inline>
            <NumberInput value={num("cpu", 0.25)} min={0.05} max={64} step={0.05} onChange={(v) => set({ cpu: v ?? 0.25 })} />
          </FormRow>
          <FormRow label="Memory per replica (MB)" inline>
            <NumberInput value={num("memory", 256)} min={32} max={262144} integer onChange={(v) => set({ memory: v ?? 256 })} />
          </FormRow>
          <span className="muted small">
            Save, then <a href={href("deploy")}>Deploy</a>. Reservations decide how many replicas fit on a server; when none fit, the cluster counts as full.
          </span>
        </div>
      )}
    </div>
  );
}

function Workloads({ cluster, status, onScale }: { cluster: OnPremCluster; status: ClusterStatus | null; onScale: (stack: string, replicas: number) => void }) {
  const [scale, setScale] = useState<Record<string, number>>({});
  const deployments = Object.entries(cluster.deployments);
  return (
    <div className="card card-pad">
      <h3>Deployed functions</h3>
      {!deployments.length && <p className="muted small">Nothing deployed here yet: turn on “Deploy this project&apos;s functions” above, save and deploy.</p>}
      {deployments.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Deployment</th>
              <th>Endpoint</th>
              <th>Running</th>
              <th>Web replicas</th>
            </tr>
          </thead>
          <tbody>
            {deployments.map(([stack, dep]) => {
              const live = (status?.workloads || []).filter((w) => w.name.startsWith(stack));
              return (
                <tr key={stack}>
                  <td>
                    <b className="mono">{stack}</b>
                    <div className="muted small mono">{dep.image}</div>
                  </td>
                  <td className="mono small">
                    <a href={dep.endpoint} target="_blank" rel="noreferrer">
                      {dep.endpoint}
                    </a>
                  </td>
                  <td className="small">
                    {live.length
                      ? live.map((w) => (
                          <div key={w.name} className={w.running < w.desired ? "warn-text" : undefined}>
                            {w.name.slice(stack.length).replace(/^[_/-]/, "") || "web"}: {w.running}/{w.desired}
                          </div>
                        ))
                      : "–"}
                  </td>
                  <td>
                    <span className="row-gap">
                      <NumberInput value={scale[stack] ?? dep.replicas} min={0} max={500} integer onChange={(v) => setScale((s) => ({ ...s, [stack]: v ?? 0 }))} />
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={(scale[stack] ?? dep.replicas) === dep.replicas}
                        onClick={() => onScale(stack, scale[stack] ?? dep.replicas)}
                        title="Until the next deploy; keep it in Web replicas above"
                      >
                        Scale
                      </button>
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AlertSettings({ cluster, onSaved }: { cluster: OnPremCluster; onSaved: () => void }) {
  const { draft } = useStudio();
  const functions = Object.keys(asObject(draft.functions));
  const [capacity, setCapacity] = useState(cluster.capacity);
  const [alert, setAlert] = useState(cluster.alert);
  const [watch, setWatch] = useState(cluster.watch);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const dirty = useMemo(
    () => JSON.stringify([capacity, alert, watch]) !== JSON.stringify([cluster.capacity, cluster.alert, cluster.watch]),
    [capacity, alert, watch, cluster],
  );

  const save = async () => {
    setBusy(true);
    try {
      await api.clusterSettings(cluster.name, { capacity, alert, watch } as unknown as JsonObject);
      toast("Saved", "ok");
      onSaved();
    } catch (exc) {
      toast(errorMessage(exc), "err");
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    try {
      const r = await api.clusterTestAlert(cluster.name);
      if (r.errors.length) toast(r.errors.join("; "), "err");
      else toast(`Test alert sent to ${r.sent.join(", ")}`, "ok");
      onSaved();
    } catch (exc) {
      toast(errorMessage(exc), "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card card-pad">
      <h3>When every server is full</h3>
      <p className="muted small">
        Optional. pskit calls your function with <code className="mono">event.data</code> = <code className="mono">{"{type: \"pskit.cluster.capacity\", cluster, message, subject, reasons, nodes, ...}"}</code>{" "}
        (send a mail to DevOps from it) and / or POSTs the same JSON to a webhook (Slack, Teams, ...). At most once per cooldown.
      </p>
      <div className="row-gap wrap">
        <FormRow label="CPU full at %" inline>
          <NumberInput value={capacity.cpu} min={10} max={100} integer onChange={(v) => setCapacity({ ...capacity, cpu: v ?? 85 })} />
        </FormRow>
        <FormRow label="Memory full at %" inline>
          <NumberInput value={capacity.memory} min={10} max={100} integer onChange={(v) => setCapacity({ ...capacity, memory: v ?? 85 })} />
        </FormRow>
        <FormRow label="Disk full at %" inline>
          <NumberInput value={capacity.disk} min={10} max={100} integer onChange={(v) => setCapacity({ ...capacity, disk: v ?? 90 })} />
        </FormRow>
      </div>
      <div className="row-gap wrap">
        <FormRow label="Alert function" help="A function of this project, deployed (any on-prem stage).">
          <select className="input" value={alert.function} onChange={(e) => setAlert({ ...alert, function: e.target.value })}>
            <option value="">— none —</option>
            {functions.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </FormRow>
        <FormRow label="Stage" help="Where that function is deployed (empty: any).">
          <TextInput value={alert.stage} onChange={(v) => setAlert({ ...alert, stage: v })} placeholder="any" mono />
        </FormRow>
        <FormRow label="Webhook (optional)">
          <TextInput value={alert.webhook} onChange={(v) => setAlert({ ...alert, webhook: v })} placeholder="https://hooks.slack.com/services/…" mono />
        </FormRow>
        <FormRow label="Cooldown (minutes)" inline>
          <NumberInput value={alert.cooldown_minutes} min={1} max={10080} integer onChange={(v) => setAlert({ ...alert, cooldown_minutes: v ?? 30 })} />
        </FormRow>
      </div>
      <div className="row-gap wrap">
        <Toggle checked={watch.enabled} onChange={(on) => setWatch({ ...watch, enabled: on })} label="Watch while the studio runs" />
        {watch.enabled && (
          <FormRow label="every (seconds)" inline>
            <NumberInput value={watch.interval_seconds} min={15} max={86400} integer onChange={(v) => setWatch({ ...watch, interval_seconds: v ?? 60 })} />
          </FormRow>
        )}
        <span className="muted small">
          Around the clock: <code className="mono">pskit cluster watch {cluster.name}</code> on an always-on machine (cron / systemd).
        </span>
      </div>
      <div className="row-gap">
        <button type="button" className="btn btn-primary" disabled={!dirty || busy} onClick={() => void save()}>
          Save
        </button>
        <button type="button" className="btn" disabled={busy || dirty || (!cluster.alert.function && !cluster.alert.webhook)} onClick={() => void test()}>
          Send test alert
        </button>
      </div>
      {cluster.alerts.length > 0 && (
        <>
          <h4>Recent alerts</h4>
          <ul className="cluster-alerts small">
            {[...cluster.alerts].reverse().map((a) => (
              <li key={a.at + a.message}>
                <span className="mono muted">{new Date(a.at).toLocaleString()}</span> {a.test && <span className="badge badge-info">test</span>} {a.message}
                {a.sent.length > 0 && <span className="muted"> → {a.sent.join(", ")}</span>}
                {a.errors.length > 0 && <div className="form-error">{a.errors.join("; ")}</div>}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function ServerChoice({
  state,
  value,
  onChange,
  exclude,
  onServersChanged,
}: {
  state: OnPremClustersState;
  value: string;
  onChange: (v: string) => void;
  exclude: Set<string>;
  onServersChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const free = state.servers.filter((s) => !exclude.has(s.name));
  return (
    <>
      <span className="row-gap wrap">
        <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">— pick a saved server —</option>
          {free.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name} · {s.label}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn-sm" onClick={() => setAdding(true)}>
          + Add a server
        </button>
      </span>
      {adding && (
        <ServerForm
          initial={null}
          taken={state.servers.map((s) => s.name)}
          onClose={() => setAdding(false)}
          onSaved={(name) => {
            setAdding(false);
            onServersChanged();
            onChange(name);
          }}
        />
      )}
    </>
  );
}

function inCluster(state: OnPremClustersState): Set<string> {
  return new Set(state.clusters.flatMap((c) => c.nodes.map((n) => n.server)));
}

function CreateClusterDrawer({
  state,
  onClose,
  onCreated,
  onServersChanged,
}: {
  state: OnPremClustersState;
  onClose: () => void;
  onCreated: (name: string) => void;
  onServersChanged: () => void;
}) {
  const [name, setName] = useState(state.clusters.length ? "" : "prod");
  const [orchestrator, setOrchestrator] = useState<Orchestrator>("swarm");
  const [master, setMaster] = useState("");
  const [advertise, setAdvertise] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const job = useJob(jobId);
  const info = state.orchestrators.find((o) => o.id === orchestrator);
  const nameError = name && !NAME_RE.test(name) ? "lowercase letter first; lowercase letters, digits and -" : null;
  const taken = state.clusters.some((c) => c.name === name);

  useEffect(() => {
    if (job.status === "succeeded") onCreated(name);
  }, [job.status, name, onCreated]);

  const create = async () => {
    setError(null);
    try {
      const { job_id } = await api.clusterCreate({ name, orchestrator, master, advertise: advertise || undefined });
      setJobId(job_id);
    } catch (exc) {
      setError(errorMessage(exc));
    }
  };

  return (
    <Drawer
      open
      title="New scaling cluster"
      onClose={onClose}
      actions={
        <button
          type="button"
          className="btn btn-primary"
          disabled={!name || !!nameError || taken || !master || job.status === "running"}
          onClick={() => void create()}
        >
          {job.status === "running" ? "Installing…" : "Create"}
        </button>
      }
    >
      <FormRow label="Name" required error={nameError || (taken ? "already used" : null)}>
        <TextInput value={name} onChange={setName} placeholder="prod" mono />
      </FormRow>
      <FormRow label="Orchestrator" help={info ? `${info.summary} Open between servers: ${info.ports}.` : undefined}>
        <Segmented
          label="Orchestrator"
          value={orchestrator}
          onChange={setOrchestrator}
          options={state.orchestrators.map((o) => ({ value: o.id, label: o.label }))}
        />
      </FormRow>
      <FormRow label="Master server" required help="Runs the control plane and the main replica (schedules, queues, logs, data). Root or passwordless sudo.">
        <ServerChoice state={state} value={master} onChange={setMaster} exclude={inCluster(state)} onServersChanged={onServersChanged} />
      </FormRow>
      <FormRow label="Cluster address (optional)" help="The master's IP the other servers reach (e.g. its private IP). Default: its SSH host.">
        <TextInput value={advertise} onChange={setAdvertise} placeholder="10.0.0.5" mono />
      </FormRow>
      {error && <div className="form-error">{error}</div>}
      {jobId && <JobConsole job={job} />}
    </Drawer>
  );
}

function JoinDrawer({
  cluster,
  state,
  onClose,
  onJoin,
  onServersChanged,
}: {
  cluster: OnPremCluster;
  state: OnPremClustersState;
  onClose: () => void;
  onJoin: (server: string, advertise: string) => void;
  onServersChanged: () => void;
}) {
  const [server, setServer] = useState("");
  const [advertise, setAdvertise] = useState("");
  return (
    <Drawer
      open
      title={`Join a server to ${cluster.name}`}
      onClose={onClose}
      actions={
        <button type="button" className="btn btn-primary" disabled={!server} onClick={() => onJoin(server, advertise)}>
          Join
        </button>
      }
    >
      <p className="muted small">
        pskit installs {cluster.orchestrator_label} on the server over SSH, joins it to the master, and copies the deployed function images to it. Running
        functions keep running; new replicas can start there right away.
      </p>
      <FormRow label="Server" required>
        <ServerChoice state={state} value={server} onChange={setServer} exclude={inCluster(state)} onServersChanged={onServersChanged} />
      </FormRow>
      <FormRow label="Cluster address (optional)" help="This server's IP in the cluster network. Default: its SSH host.">
        <TextInput value={advertise} onChange={setAdvertise} placeholder="10.0.0.6" mono />
      </FormRow>
    </Drawer>
  );
}
