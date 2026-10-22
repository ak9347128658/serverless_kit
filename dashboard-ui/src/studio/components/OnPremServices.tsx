import { useCallback, useEffect, useMemo, useState } from "react";
import { Drawer } from "../../components/Drawer";
import { api, errorMessage } from "../api";
import { useJob } from "../jobs";
import { useStudio } from "../store";
import type { JsonObject, OnPremCatalogItem, OnPremServer, OnPremService, OnPremServicesState, OnPremTarget } from "../types";
import { asObject, NAME_RE, uniqueName } from "../util";
import { JobConsole } from "./Console";
import { Markdown } from "./Markdown";
import { useConfirm, useToast } from "./Feedback";
import { EnumSelect, FormRow, NumberInput, Segmented, TextInput, Toggle } from "./Forms";
import { ServersPanel, ServiceConsole, TerminalDrawer } from "./OnPremMachines";
import type { GuideRef } from "./ResourceGuides";

const GROUPS: { title: string; match: (c: OnPremCatalogItem) => boolean }[] = [
  { title: "Queues & streams", match: (c) => c.roles.includes("queue") || c.roles.includes("stream") },
  { title: "Storage", match: (c) => c.roles.includes("storage") },
  { title: "Databases", match: (c) => c.roles.includes("database") },
  { title: "Secret managers", match: (c) => c.roles.includes("secrets") },
  { title: "Caches & other", match: (c) => c.kind === "service" && (c.roles.includes("cache") || c.roles.includes("custom")) },
  { title: "Install on a server: languages & Docker", match: (c) => c.kind === "runtime" && (c.group || "").startsWith("Languages") },
  { title: "Install on a server: web servers", match: (c) => c.kind === "runtime" && c.group === "Web servers" },
  { title: "Install on a server: process managers & tools", match: (c) => c.kind === "runtime" && ["Process managers", "Tools"].includes(c.group || "") },
];
const ROLE_LABEL: Record<string, string> = {
  queue: "Queue",
  stream: "Stream (consumer group)",
  cache: "Cache",
  database: "Database",
  storage: "Bucket",
  custom: "Service",
  secrets: "Secret manager",
};
// Roles a function can be triggered by (others are only bound as connection values).
const TRIGGER_ROLES = ["queue", "stream", "storage"];

export function OnPremServices({ onGuide }: { onGuide: (ref: GuideRef) => void }) {
  const [state, setState] = useState<OnPremServicesState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<OnPremCatalogItem | null | "pick">(null);
  const [details, setDetails] = useState<string | null>(null);
  const [usage, setUsage] = useState<string | null>(null);
  const [consoleKey, setConsoleKey] = useState<string | null>(null);
  const [terminalKey, setTerminalKey] = useState<string | null>(null);
  const [createServer, setCreateServer] = useState<string | undefined>(undefined);
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
                    <button type="button" className="btn btn-sm btn-primary" onClick={() => setUsage(svc.key)} title="Connect from any program: code, terminal, security">
                      How to use
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => setDetails(svc.key)}>
                      Details
                    </button>
                    {svc.kind === "service" && (
                      <button type="button" className="btn btn-sm" onClick={() => setConsoleKey(svc.key)} title="Run commands: redis-cli, psql, mongosh, rpk, bao, ... or sh in the container">
                        Console
                      </button>
                    )}
                    {svc.kind === "service" && (
                      <button type="button" className="btn btn-sm" onClick={() => setTerminalKey(svc.key)} title="Interactive client in a browser terminal (Docker)">
                        Terminal
                      </button>
                    )}
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
          server={createServer}
          onClose={() => {
            setCreating(null);
            setCreateServer(undefined);
          }}
          onCreated={(svc, attach) => {
            load();
            void addToProject(svc, attach);
          }}
        />
      )}
      {details && <DetailsDrawer serviceKey={details} onClose={() => setDetails(null)} />}
      {usage && <ServiceUsageDrawer serviceKey={usage} onClose={() => setUsage(null)} />}
      {consoleKey && <ServiceConsole serviceKey={consoleKey} onClose={() => setConsoleKey(null)} />}
      {terminalKey && <TerminalDrawer request={{ key: terminalKey }} onClose={() => setTerminalKey(null)} />}
      <ServersPanel
        onInstall={(name) => {
          setCreateServer(name);
          setCreating("pick");
        }}
      />
    </div>
  );
}

function CreateDrawer({
  state,
  initial,
  server,
  onClose,
  onCreated,
}: {
  state: OnPremServicesState;
  initial: OnPremCatalogItem | null;
  server?: string;
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
  const [target, setTarget] = useState<OnPremTarget>(server || state.default_target.method === "ssh" ? { method: "ssh" } : { method: "local" });
  const [servers, setServers] = useState<OnPremServer[]>([]);
  const [savedServer, setSavedServer] = useState<string>(server || state.default_target.server || "");
  const [version, setVersion] = useState("");
  useEffect(() => {
    api.onpremServers().then((r) => setServers(r.servers), () => undefined);
  }, []);
  const [ssh, setSsh] = useState<OnPremTarget>({ method: "ssh", user: "root", port: 22, ...(state.default_target.method === "ssh" ? state.default_target : {}) });
  const [port, setPort] = useState<number | undefined>(undefined);
  const [address, setAddress] = useState("");
  const [image, setImage] = useState("");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [bind, setBind] = useState("");
  const [withGui, setWithGui] = useState(true);
  const [guiLogin, setGuiLogin] = useState<"password" | "none">("password");
  const [guiPassword, setGuiPassword] = useState("");
  const [sshAuth, setSshAuth] = useState<"key" | "agent" | "password">(
    state.default_target.auth || (state.default_target.password_file ? "password" : state.default_target.identity_file ? "key" : "agent"),
  );
  const [sshPassword, setSshPassword] = useState("");
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
    setKey(
      uniqueName(
        c.kind === "runtime" ? c.id : c.roles[0] === "database" ? `${c.id}-db` : c.roles[0] === "storage" ? "files" : c.roles[0] === "secrets" ? "secrets" : c.id === "kafka" ? "events" : "jobs",
        taken,
      ),
    );
    setVersion("");
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

  const passwordError =
    password && !/^[A-Za-z0-9!#%*+,.:=?@^_~-]{8,128}$/.test(password) ? "8-128 characters: letters, digits and !#%*+,.:=?@^_~-" : null;
  const loginError = login && !/^[A-Za-z][A-Za-z0-9_.-]{2,63}$/.test(login) ? "3-64 characters, starting with a letter" : null;
  const keyError = !NAME_RE.test(key) ? "letters, digits, - and _; start with a letter" : taken.includes(key) ? "already used" : null;
  const sshError =
    target.method === "ssh" && savedServer
      ? null
      : target.method === "ssh" && !(ssh.host || "").trim()
      ? "the server's host name or IP"
      : target.method === "ssh" && sshAuth === "key" && !(ssh.identity_file || "").trim()
        ? "the path of the private key, or pick SSH agent / default key"
        : target.method === "ssh" && sshAuth === "password" && !sshPassword && !ssh.password_file
          ? "the SSH password"
          : null;
  const guiPasswordError =
    guiPassword && !/^[A-Za-z0-9!#%*+,.:=?@^_~-]{8,128}$/.test(guiPassword) ? "8-128 characters: letters, digits and !#%*+,.:=?@^_~-" : null;

  const submit = async () => {
    if (!item || keyError || sshError || passwordError || loginError || guiPasswordError) return;
    setSubmitError(null);
    const sshTarget: OnPremTarget = { ...ssh, method: "ssh", auth: sshAuth };
    if (sshAuth !== "key") delete sshTarget.identity_file;
    if (sshAuth === "password" && sshPassword) sshTarget.password = sshPassword;
    if (sshAuth !== "password") delete sshTarget.password_file;
    const chosen: OnPremTarget = target.method !== "ssh" ? { method: "local" } : savedServer ? { method: "ssh", server: savedServer } : sshTarget;
    const body: JsonObject = { key, service: item.id, target: chosen as unknown as JsonObject };
    if (item.kind === "runtime" && version) body.version = version;
    if (role) body.role = role;
    if (port) body.port = port;
    if (address.trim()) body.address = address.trim();
    if (image.trim()) body.image = image.trim();
    if (login.trim()) body.user = login.trim();
    if (password) body.password = password;
    if (bind) body.bind = bind;
    if (item.companion && withGui) {
      body.gui = true;
      body.gui_login = guiLogin;
      if (guiLogin === "password" && guiPassword) body.gui_password = guiPassword;
    }
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
          <button
            type="button"
            className="btn btn-primary"
            disabled={!!keyError || !!sshError || !!passwordError || !!loginError || !!guiPasswordError || running || job.status === "succeeded"}
            onClick={() => void submit()}
          >
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
                        {(c.companion || (c.gui && c.gui.length > 0)) && (
                          <span className="small">web UI: {c.companion || (c.gui || []).join(", ")}</span>
                        )}
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
            <FormRow label="Server" help="A saved server (Servers below), or a new connection typed here.">
              <EnumSelect
                value={savedServer}
                onChange={setSavedServer}
                options={[{ value: "", label: "New connection…" }, ...servers.map((s) => ({ value: s.name, label: `${s.name} (${s.label})` }))]}
              />
            </FormRow>
          )}
          {item.kind === "runtime" && (item.versions || []).length > 0 && (
            <FormRow label="Version" help={`Default ${(item.versions || [])[0]}. Any version number works (e.g. 1.23.4 for Go).`}>
              <div className="row-gap">
                <Segmented
                  label="Version"
                  value={(item.versions || []).includes(version) ? version : version ? "other" : (item.versions || [])[0]}
                  onChange={(v) => setVersion(v === "other" ? "" : v)}
                  options={(item.versions || []).map((v) => ({ value: v, label: v }))}
                />
                <TextInput mono value={version} placeholder="other version" onChange={setVersion} />
              </div>
            </FormRow>
          )}
          {target.method === "ssh" && !savedServer && (
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
              <FormRow label="Log in with">
                <Segmented
                  label="Log in with"
                  value={sshAuth}
                  onChange={(v) => setSshAuth(v as "key" | "agent" | "password")}
                  options={[
                    { value: "key", label: "Key file (.pem)" },
                    { value: "agent", label: "SSH agent / default key" },
                    { value: "password", label: "Password" },
                  ]}
                />
              </FormRow>
              {sshAuth === "key" && (
                <FormRow label="Private key (.pem)" help="The key file you log in with (AWS .pem, Azure .pem, id_ed25519, ...).">
                  <TextInput mono value={ssh.identity_file || ""} placeholder="~/.ssh/server.pem" onChange={(v) => setSsh((s) => ({ ...s, identity_file: v }))} />
                </FormRow>
              )}
              {sshAuth === "agent" && (
                <p className="muted small">
                  No file: OpenSSH uses your SSH agent, <code className="mono">~/.ssh/id_*</code> and <code className="mono">~/.ssh/config</code>, exactly
                  like <code className="mono">ssh {ssh.user || "user"}@{ssh.host || "host"}</code> in a terminal.
                </p>
              )}
              {sshAuth === "password" && (
                <FormRow
                  label="SSH password"
                  help="Saved in .pskit/ssh/ (out of git, owner-only) and sent with sshpass, which must be installed here (Linux / macOS). On Windows, install a key once instead: pskit resources onprem ssh-key user@host, then pick Key file."
                >
                  <TextInput mono type="password" value={sshPassword} placeholder={ssh.password_file ? "saved: " + ssh.password_file : ""} onChange={setSshPassword} />
                </FormRow>
              )}
              <Toggle checked={!!ssh.sudo} onChange={(b) => setSsh((s) => ({ ...s, sudo: b }))} label="Use sudo (the SSH user is not root and not in the docker group)" />
            </div>
          )}
          {item.kind === "service" && (
            <>
              <FormRow label="Port" help={`Published on the server. Default ${item.port}${item.extra_ports?.length ? ` (also ${item.extra_ports.join(", ")})` : ""}.`}>
                <NumberInput integer min={1} max={65535} value={port} placeholder={String(item.port || "")} onChange={setPort} />
              </FormRow>
              <div className="driver-box card card-pad">
                <h3>Security</h3>
                {item.auth === false ? (
                  <p className="muted small">
                    {item.label} runs without authentication in this setup. Keep it on 127.0.0.1 (this machine only) or a private network, and open the
                    port only to the servers that need it.
                  </p>
                ) : (
                  <>
                    <FormRow label="User" error={loginError} help={`Created in the service. Default: ${item.default_user || "pskit"}.`}>
                      <TextInput mono value={login} placeholder={item.default_user || "pskit"} onChange={setLogin} invalid={!!loginError} />
                    </FormRow>
                    <FormRow label="Password" error={passwordError} help="Empty: a strong random one. Stored only in the env file and .pskit/, never in pskit.yml.">
                      <div className="row-gap">
                        <TextInput mono type={showPassword ? "text" : "password"} value={password} placeholder="generated" onChange={setPassword} invalid={!!passwordError} />
                        <button type="button" className="btn btn-sm" onClick={() => setPassword(generatePassword())}>
                          Generate
                        </button>
                        <Toggle checked={showPassword} onChange={setShowPassword} label="Show" />
                      </div>
                    </FormRow>
                  </>
                )}
                <FormRow label="Listen on" help="127.0.0.1: only programs on that machine can connect. 0.0.0.0: other servers too (limit it with the firewall).">
                  <Segmented
                    label="Listen on"
                    value={bind || (target.method === "ssh" ? "0.0.0.0" : "127.0.0.1")}
                    onChange={setBind}
                    options={[
                      { value: "127.0.0.1", label: "This machine only (127.0.0.1)" },
                      { value: "0.0.0.0", label: "Network (0.0.0.0)" },
                    ]}
                  />
                </FormRow>
              </div>
              {(item.companion || (item.gui && item.gui.length > 0)) && (
                <FormRow
                  label="Web UI"
                  help={
                    item.companion
                      ? `${item.companion} runs next to it on a private Docker network, on the same interface as the service.`
                      : "Built in; How to use shows its address and login."
                  }
                >
                  {item.companion ? (
                    <Toggle checked={withGui} onChange={setWithGui} label={`Also start ${item.companion}`} />
                  ) : (
                    <span className="muted small">{(item.gui || []).join(", ")}</span>
                  )}
                </FormRow>
              )}
              {item.companion && withGui && (item.gui_logins || []).includes("none") && (
                <div className="driver-box card card-pad">
                  <FormRow label="Web UI login" help="A password protects the UI on an open port. No login is fine on 127.0.0.1, a private network or behind an SSH tunnel.">
                    <Segmented
                      label="Web UI login"
                      value={guiLogin}
                      onChange={(v) => setGuiLogin(v as "password" | "none")}
                      options={[
                        { value: "password", label: "With password" },
                        { value: "none", label: "Without password" },
                      ]}
                    />
                  </FormRow>
                  {guiLogin === "password" ? (
                    <FormRow label="Web UI password" error={guiPasswordError} help="Empty: generated (shown in How to use and the env file).">
                      <div className="row-gap">
                        <TextInput mono type={showPassword ? "text" : "password"} value={guiPassword} placeholder="generated" onChange={setGuiPassword} invalid={!!guiPasswordError} />
                        <button type="button" className="btn btn-sm" onClick={() => setGuiPassword(generatePassword())}>
                          Generate
                        </button>
                      </div>
                    </FormRow>
                  ) : (
                    (bind || (target.method === "ssh" ? "0.0.0.0" : "127.0.0.1")) !== "127.0.0.1" && (
                      <div className="hint-box">
                        Anyone who can reach the UI's port can use it, and the UI holds the service's credentials. Open the port only to your IP, or keep it closed and use{" "}
                        <code className="mono">pskit resources onprem tunnel {key || "<key>"} --remote-port &lt;ui port&gt;</code>.
                      </div>
                    )
                  )}
                </div>
              )}
              {item.companion && withGui && !(item.gui_logins || []).includes("none") && (
                <p className="muted small">{item.companion} has no login of its own: it asks for the service's user and password.</p>
              )}
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

function generatePassword(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789_-.";
  const bytes = new Uint32Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => chars[b % chars.length]).join("");
}

/** Connect to a service from any program: details, Python / Node.js clients, terminal, security. */
function ServiceUsageDrawer({ serviceKey, onClose }: { serviceKey: string; onClose: () => void }) {
  const [doc, setDoc] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    api
      .onpremUsage(serviceKey, reveal)
      .then((d) => setDoc(d.markdown))
      .catch((exc: unknown) => setError(errorMessage(exc)));
  }, [serviceKey, reveal]);
  return (
    <Drawer
      open
      title={`How to use ${serviceKey}`}
      onClose={onClose}
      actions={
        doc ? (
          <button type="button" className="btn" onClick={() => void navigator.clipboard?.writeText(doc)}>
            Copy guide
          </button>
        ) : undefined
      }
    >
      <div className="row-gap">
        <Toggle checked={reveal} onChange={setReveal} label="Show passwords and URLs" />
        <span className="muted small">
          Terminal: <code className="mono">pskit resources onprem usage {serviceKey}</code>
        </span>
      </div>
      {error && <div className="form-error">{error}</div>}
      {!doc && !error && <p className="muted">Loading…</p>}
      {doc && <Markdown text={doc} />}
    </Drawer>
  );
}

function DetailsDrawer({ serviceKey, onClose }: { serviceKey: string; onClose: () => void }) {
  const [info, setInfo] = useState<OnPremService | null>(null);
  const [reveal, setReveal] = useState(false);
  const [logs, setLogs] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guiJob, setGuiJob] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const job = useJob(guiJob);
  useEffect(() => {
    setError(null);
    api
      .onpremStatus(serviceKey, reveal)
      .then(setInfo)
      .catch((exc: unknown) => setError(errorMessage(exc)));
  }, [serviceKey, reveal, attempt]);
  useEffect(() => {
    if (job.status === "succeeded" || job.status === "failed") setAttempt((a) => a + 1);
  }, [job.status]);
  const links = Array.isArray(info?.gui) ? info.gui : [];
  const hasCompanion = links.some((g) => g.kind === "companion");
  const [addLogin, setAddLogin] = useState<"password" | "none">("password");
  const startGui = async (action: "add" | "remove" | "check") => {
    try {
      const started =
        action === "remove" ? api.onpremGuiRemove(serviceKey) : action === "check" ? api.onpremGuiCheck(serviceKey) : api.onpremGui(serviceKey, { login: addLogin });
      setGuiJob((await started).job_id);
    } catch (exc) {
      setError(errorMessage(exc));
    }
  };
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
          {info.reachable_from_here === false && (
            <div className="hint-box">
              This machine cannot reach {info.address}:{info.port} (the server's firewall probably blocks it). Scripts here would time out. Run{" "}
              <code className="mono">pskit resources onprem tunnel {serviceKey}</code> to reach it through SSH, open the port to your IP, or go through
              the pskit host with the pskit client (<b>Resources → How to use</b> on the resource, section 7).
            </div>
          )}
          {info.port && (
            <FormRow label="Endpoint">
              <code className="mono">
                {info.address}:{info.port}
              </code>
            </FormRow>
          )}
          {(links.length > 0 || info.companion) && (
            <FormRow label="Web UI" help="Does not open? Check web UI tests the container, the port, the server and this machine, and names the fix (firewall / NSG, bind, tunnel).">
              <div className="stack-xs">
                {links.map((g) => (
                  <div key={g.url} className="row-gap wrap">
                    <a className="btn btn-sm btn-primary" href={g.url} target="_blank" rel="noreferrer noopener">
                      Open {g.label}
                    </a>
                    <code className="mono small">{g.url}</code>
                    <span className="muted small">login: {g.login}</span>
                  </div>
                ))}
                <div className="row-gap wrap">
                  {links.length > 0 && (
                    <button type="button" className="btn btn-sm" disabled={job.status === "running"} onClick={() => void startGui("check")}>
                      Check web UI
                    </button>
                  )}
                  {info.companion &&
                    (!hasCompanion ? (
                      <>
                        {(info.gui_logins || []).includes("none") && (
                          <Segmented
                            label="Login"
                            value={addLogin}
                            onChange={(v) => setAddLogin(v as "password" | "none")}
                            options={[
                              { value: "password", label: "With password" },
                              { value: "none", label: "Without password" },
                            ]}
                          />
                        )}
                        <button type="button" className="btn btn-sm" disabled={job.status === "running"} onClick={() => void startGui("add")}>
                          Add web UI
                        </button>
                      </>
                    ) : (
                      <button type="button" className="btn btn-sm btn-danger" disabled={job.status === "running"} onClick={() => void startGui("remove")}>
                        Remove web UI
                      </button>
                    ))}
                </div>
                {guiJob && <JobConsole job={job} />}
              </div>
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
