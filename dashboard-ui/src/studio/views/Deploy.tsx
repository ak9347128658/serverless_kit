import { useCallback, useEffect, useState } from "react";
import { api, ApiError, errorMessage } from "../api";
import { JobConsole } from "../components/Console";
import { useConfirm, useToast } from "../components/Feedback";
import { EnumSelect, FormRow, JsonInput, Section, Segmented, TextInput, Toggle } from "../components/Forms";
import { PageHeader } from "../components/Misc";
import { DeployedBadge, ServerSelect, useServerChoice } from "../components/ServerPicker";
import { useJob } from "../jobs";
import { useStudio } from "../store";
import { compatProblems, overridesOn, restoreTriggersOn, setEnabledOn, skipTriggersOn, type CompatProblem } from "../compat";
import type { Job, Json, JsonObject } from "../types";
import { asObject, normalizeEvent, asArray } from "../util";
import { relative } from "../../format";

const ACTIONS = [
  { command: "validate", label: "Validate", tone: "", hint: "Check the configuration against the provider." },
  { command: "package", label: "Package", tone: "", hint: "Build the bundle and Terraform without deploying." },
  { command: "plan", label: "Plan", tone: "", hint: "terraform plan: what would change." },
  { command: "deploy", label: "Deploy", tone: "btn-primary", hint: "Build and apply." },
  { command: "info", label: "Info", tone: "", hint: "What is deployed." },
  { command: "remove", label: "Remove", tone: "btn-danger", hint: "Destroy every resource of this stage." },
] as const;

export function DeployView() {
  const { state, dirty, draft, save, meta, replace } = useStudio();
  const toast = useToast();
  const confirm = useConfirm();
  const [jobId, setJobId] = useState<string | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [deleteData, setDeleteData] = useState(true);
  const job = useJob(jobId);
  const target = state.target || { provider: "aws", stage: "dev" };
  const targetMeta = meta.providers.find((p) => p.name === target.provider);
  const problems = targetMeta ? compatProblems(draft, targetMeta) : [];
  const onprem = target.provider === "onprem";
  const { servers, deployed, server: machine, setServer: setMachine, reload: reloadDeployed } = useServerChoice(onprem, target.stage);
  // A finished deploy / remove changes where the stage runs: show it and keep the picker on it.
  const finished = job.status !== "running" && job.status !== "idle";
  useEffect(() => {
    if (onprem && finished) reloadDeployed();
  }, [onprem, finished, jobId, reloadDeployed]);

  /** Switch every problem off on the target provider only. Returns the new draft. */
  const disableAll = (): JsonObject => {
    let raw = draft;
    for (const p of problems) {
      if (p.kind === "trigger" && p.fn && p.types) raw = skipTriggersOn(raw, target.provider, p.fn, p.types);
      else if (p.wf) raw = setEnabledOn(raw, target.provider, "workflows", p.wf, false);
    }
    return raw;
  };

  const clearHistory = async () => {
    try {
      const r = await api.clearJobs();
      if (jobId && history.some((h) => h.job_id === jobId && h.status !== "running")) setJobId(null);
      loadHistory();
      toast(r.removed ? `Cleared ${r.removed} job${r.removed === 1 ? "" : "s"}` : "Nothing to clear", "info");
    } catch (exc) {
      toast(errorMessage(exc), "err");
    }
  };

  const removeJob = async (id: string) => {
    try {
      await api.deleteJob(id);
      if (id === jobId) setJobId(null);
      loadHistory();
    } catch (exc) {
      toast(errorMessage(exc), "err");
    }
  };

  const loadHistory = useCallback(() => {
    api.jobs().then((r) => setHistory(r.jobs || []), () => undefined);
  }, []);
  useEffect(loadHistory, [loadHistory]);
  useEffect(() => {
    if (job.status !== "running" && job.status !== "idle") loadHistory();
  }, [job.status, loadHistory]);

  const run = async (command: string, args?: Record<string, unknown>) => {
    let fixed: JsonObject | null = null;
    if (problems.length && ["validate", "package", "plan", "deploy"].includes(command)) {
      const answer = await confirm({
        title: `Not available on ${targetMeta?.display_name || target.provider}`,
        body: (
          <div className="stack">
            <ul className="bullets">
              {problems.map((p) => (
                <li key={p.key}>{p.message}</li>
              ))}
            </ul>
            <p className="small">
              <code className="mono">pskit {command}</code> fails until these are fixed. Disabling them writes{" "}
              <code className="mono">providers.{target.provider}</code> overrides, so the other clouds keep them.
            </p>
          </div>
        ),
        confirmLabel: `Disable on ${targetMeta?.display_name || target.provider} and ${command}`,
        extra: [{ label: `${command[0].toUpperCase()}${command.slice(1)} anyway`, value: "anyway" }],
      });
      if (!answer) return;
      if (answer === "confirm") fixed = disableAll();
    }
    if (fixed) {
      // Commands read pskit.yml from disk: save the overrides (and any other pending edit) first.
      try {
        if (!(await save(false, fixed))) return;
        toast(`Disabled on ${targetMeta?.display_name || target.provider} and saved pskit.yml`, "ok");
      } catch (exc) {
        toast(`Save failed: ${errorMessage(exc)}`, "err");
        return;
      }
    } else if (dirty) {
      const answer = await confirm({
        title: "Save first?",
        body: "Commands run against pskit.yml on disk, which doesn't have your latest changes.",
        confirmLabel: "Save and run",
        extra: [{ label: "Run without saving", value: "skip" }],
      });
      if (!answer) return;
      if (answer === "confirm") {
        try {
          if (!(await save())) return;
        } catch (exc) {
          toast(`Save failed: ${errorMessage(exc)}`, "err");
          return;
        }
      }
    }
    const prod = /^prod/i.test(target.stage);
    if (command === "remove" || (command === "deploy" && prod)) {
      const typed = `${target.provider}/${target.stage}`;
      const ok = await confirm({
        title: command === "remove" ? `Remove ${typed}${onprem && machine ? ` on ${machine}` : ""}?` : `Deploy to ${typed}${onprem && machine ? ` on ${machine}` : ""}?`,
        body:
          command === "remove"
            ? deleteData
              ? "Every function, trigger and managed resource of this stage is destroyed, including buckets, queues and topics and their data. External resources are left alone."
              : "Functions, triggers and gateways are destroyed. Managed buckets, queues and topics are kept (with their data) and pskit stops managing them."
            : "This is a production stage.",
        typed,
        confirmLabel: command === "remove" ? "Remove" : "Deploy",
        danger: true,
      });
      if (ok !== "confirm") return;
    } else if (command === "deploy") {
      const where = !onprem ? "" : machine === "local" ? " on this machine" : machine ? ` on ${machine}` : deployed ? ` on ${deployed.label}` : "";
      const moving = onprem && deployed && machine && machine !== (deployed.server || "") && !(machine === "local" && deployed.method === "local");
      const ok = await confirm({
        title: `Deploy to ${target.provider}/${target.stage}${where}?`,
        body: moving ? <p>This stage is deployed on <b>{deployed.label}</b> now. Deploying it on <b>{machine === "local" ? "this machine" : machine}</b> moves it there.</p> : undefined,
        confirmLabel: "Deploy",
      });
      if (ok !== "confirm") return;
    }
    try {
      const withMachine = onprem && machine && ["package", "plan", "deploy", "remove", "info"].includes(command) ? { ...(args || {}), server: machine } : args;
      const r = await api.startJob(command, target.provider, target.stage, withMachine);
      setJobId(r.job_id);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 409) toast("Another deploy-type job is running. Wait for it or cancel it.", "warn");
      else toast(errorMessage(exc), "err");
    }
  };

  const running = job.status === "running";
  const functions = Object.keys(asObject(draft.functions));
  const workflows = Object.keys(asObject(draft.workflows));

  return (
    <div className="page">
      <PageHeader
        title="Deploy"
        subtitle={
          <>
            Runs pskit in the project folder as <b>{target.provider}</b> / <b>{target.stage}</b> (change in the top bar). Credentials come from your shell
            environment, as with the CLI.
          </>
        }
      />
      {dirty && <div className="hint-box warn">You have unsaved changes. Commands use pskit.yml on disk; you'll be asked to save first.</div>}
      <CompatPanel provider={target.provider} problems={problems} onDisableAll={() => replace(disableAll())} />
      {onprem && (
        <div className="card card-pad row-gap wrap deploy-machine">
          <b>Machine</b>
          <ServerSelect
            value={machine}
            onChange={setMachine}
            servers={servers}
            deployed={deployed}
            label="Machine"
            emptyLabel={deployed ? `Same as the last deploy: ${deployed.label}` : "From pskit.yml (provider.options.deploy)"}
          />
          <DeployedBadge deployed={deployed} />
          <span className="muted small">
            The stage remembers the machine of its last deploy: Test, Logs and <code className="mono">pskit trigger</code> / <code className="mono">pskit logs</code> use it, and the next deploy goes there unless you pick another (<code className="mono">--server local</code> for this machine).{" "}
            Saved servers come from <b>Resources → On-prem services → Servers</b>. The same as <code className="mono">pskit deploy --server NAME</code>; to make it permanent per stage set{" "}
            <code className="mono">provider.options.deploy.server</code>. Use one stage per machine to keep their deployments apart.
          </span>
        </div>
      )}
      <div className="action-grid">
        {ACTIONS.map((a) => (
          <button key={a.command} type="button" className={`action-card btn ${a.tone}`} disabled={running} onClick={() => void run(a.command, a.command === "remove" && !deleteData ? { keep_resources: true } : undefined)} title={a.hint}>
            <b>{a.label}</b>
            <span className="small">{a.hint}</span>
          </button>
        ))}
      </div>
      <div className="row-gap remove-switch">
        <Toggle
          checked={deleteData}
          onChange={setDeleteData}
          label={deleteData ? "Remove also deletes buckets, queues and topics" : "Remove keeps buckets, queues and topics (and their data)"}
        />
        <span className="muted small">Applies to the Remove action. External resources are never deleted.</span>
      </div>
      <JobConsole job={job} onCancel={jobId ? () => void api.cancelJob(jobId).catch((exc: unknown) => toast(errorMessage(exc), "err")) : undefined} />

      <InvokePanel functions={functions} disabled={running} onRun={(args) => void run("invoke", args)} />
      {workflows.length > 0 && <WorkflowRunPanel workflows={workflows} disabled={running} onRun={(args) => void run("workflow-run", args)} />}

      <Section
        title="Recent jobs"
        defaultOpen={history.length > 0}
        hint={history.length ? `${history.length}` : undefined}
        actions={
          history.some((h) => h.status !== "running") ? (
            <button type="button" className="btn btn-sm" onClick={() => void clearHistory()} title="Forget every finished job (running jobs are kept)">
              Clear finished
            </button>
          ) : undefined
        }
      >
        {!history.length ? (
          <p className="muted">No jobs yet.</p>
        ) : (
          <ul className="job-list">
            {history.map((h) => (
              <li key={h.job_id} className="row-gap">
                <button type="button" className={`job-item grow${h.job_id === jobId ? " active" : ""}`} onClick={() => setJobId(h.job_id)}>
                  <span className={`badge badge-${h.status === "succeeded" ? "ok" : h.status === "running" ? "info" : h.status === "cancelled" ? "off" : "err"}`}>{h.status}</span>
                  <code className="mono">{JOB_COMMANDS.has(h.command) ? `pskit ${h.command}` : h.label || h.command}</code>
                  <span className="muted small grow">{relative(h.started_at)}</span>
                </button>
                {h.status !== "running" && (
                  <button type="button" className="btn btn-ghost btn-sm" aria-label={`Remove job ${h.command}`} title="Remove from the list" onClick={() => void removeJob(h.job_id)}>
                    ✕
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

const JOB_COMMANDS = new Set(["validate", "package", "plan", "deploy", "info", "remove", "invoke", "workflow-run"]);

/** What can't run on the target provider, and the per-provider overrides already in place. */
function CompatPanel({ provider, problems, onDisableAll }: { provider: string; problems: CompatProblem[]; onDisableAll: () => void }) {
  const { draft, meta, replace } = useStudio();
  const name = meta.providers.find((p) => p.name === provider)?.display_name || provider;
  const overrides = overridesOn(draft, provider);
  if (!problems.length && !overrides.length) return null;
  return (
    <Section title={problems.length ? `${problems.length} item${problems.length > 1 ? "s" : ""} won't run on ${name}` : `Switched off on ${name}`} hint={`providers.${provider}`}>
      {problems.length > 0 && (
        <>
          <ul className="compat-list">
            {problems.map((p) => (
              <li key={p.key}>
                <span className="badge badge-err">unsupported</span>
                <span className="grow">{p.message}</span>
                {p.kind === "trigger" && p.fn && p.types && (
                  <>
                    <button type="button" className="btn btn-sm" onClick={() => replace(skipTriggersOn(draft, provider, p.fn as string, p.types as string[]))}>
                      Disable trigger on {name}
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => replace(setEnabledOn(draft, provider, "functions", p.fn as string, false))}>
                      Disable function on {name}
                    </button>
                  </>
                )}
                {p.wf && (
                  <button type="button" className="btn btn-sm" onClick={() => replace(setEnabledOn(draft, provider, "workflows", p.wf as string, false))}>
                    Disable workflow on {name}
                  </button>
                )}
              </li>
            ))}
          </ul>
          <div className="row-gap">
            <button type="button" className="btn btn-primary btn-sm" onClick={onDisableAll}>
              Disable all of them on {name}
            </button>
            <span className="muted small">Other providers keep them. Save, then deploy.</span>
          </div>
        </>
      )}
      {overrides.length > 0 && (
        <>
          <p className="muted small">Already switched off on {name} (other providers are not affected):</p>
          <ul className="compat-list">
            {overrides.map((o) => (
              <li key={`${o.section}:${o.name}:${o.what}`}>
                <span className="badge badge-off">{o.what}</span>
                <span className="grow mono small">
                  {o.section}.{o.name}
                </span>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() =>
                    replace(o.what === "disabled" ? setEnabledOn(draft, provider, o.section, o.name, true) : restoreTriggersOn(draft, provider, o.name))
                  }
                >
                  {o.what === "disabled" ? "Enable again" : "Use the normal triggers"}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  );
}

function InvokePanel({ functions, disabled, onRun }: { functions: string[]; disabled: boolean; onRun: (args: Record<string, unknown>) => void }) {
  const { draft } = useStudio();
  const [fn, setFn] = useState(functions[0] || "");
  const [kind, setKind] = useState("default");
  const [data, setData] = useState<Json | undefined>(undefined);
  const [path, setPath] = useState("");
  const first = normalizeEvent(asArray(asObject(asObject(asObject(draft.functions)[fn]).events))[0] ?? null);
  return (
    <Section title="Invoke a function locally" defaultOpen={false} hint="pskit invoke">
      <div className="form">
        <FormRow label="Function">
          <EnumSelect value={fn} options={functions} onChange={setFn} />
        </FormRow>
        <FormRow label="Event" help={first ? `Default: the function's first trigger (${first.type}).` : "Default: direct invoke."}>
          <Segmented
            label="Event type"
            value={kind}
            onChange={setKind}
            options={["default", "http", "schedule", "queue", "storage", "topic", "smtp", "invoke"].map((k) => ({ value: k, label: k }))}
          />
        </FormRow>
        {(kind === "http" || (kind === "default" && first?.type === "http")) && (
          <FormRow label="Path" help="Including a query string, e.g. /users/1?verbose=1">
            <TextInput mono value={path} onChange={setPath} placeholder={String(first?.config.path || "/")} />
          </FormRow>
        )}
        <FormRow label="Payload (JSON)">
          <JsonInput value={data} onChange={setData} rows={4} placeholder='{"hello": "world"}' />
        </FormRow>
        <div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={disabled || !fn}
            onClick={() => onRun({ function: fn, type: kind === "default" ? undefined : kind, data: data === undefined ? undefined : data, path: path || undefined })}
          >
            Invoke
          </button>
        </div>
      </div>
    </Section>
  );
}

function WorkflowRunPanel({ workflows, disabled, onRun }: { workflows: string[]; disabled: boolean; onRun: (args: Record<string, unknown>) => void }) {
  const [wf, setWf] = useState(workflows[0]);
  const [data, setData] = useState<Json | undefined>(undefined);
  return (
    <Section title="Run a workflow locally" defaultOpen={false} hint="pskit workflow run --history">
      <div className="form">
        <FormRow label="Workflow">
          <EnumSelect value={wf} options={workflows} onChange={setWf} />
        </FormRow>
        <FormRow label="Input (JSON)">
          <JsonInput value={data} onChange={setData} rows={4} placeholder='{"order_id": 1}' />
        </FormRow>
        <div>
          <button type="button" className="btn btn-primary" disabled={disabled || !wf} onClick={() => onRun({ workflow: wf, data: data === undefined ? undefined : data })}>
            Run
          </button>
        </div>
      </div>
    </Section>
  );
}
