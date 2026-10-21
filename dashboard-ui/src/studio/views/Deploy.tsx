import { useCallback, useEffect, useState } from "react";
import { api, ApiError, errorMessage } from "../api";
import { JobConsole } from "../components/Console";
import { useConfirm, useToast } from "../components/Feedback";
import { EnumSelect, FormRow, JsonInput, Section, Segmented, TextInput } from "../components/Forms";
import { PageHeader } from "../components/Misc";
import { useJob } from "../jobs";
import { useStudio } from "../store";
import type { Job, Json } from "../types";
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
  const { state, dirty, draft, save } = useStudio();
  const toast = useToast();
  const confirm = useConfirm();
  const [jobId, setJobId] = useState<string | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const job = useJob(jobId);
  const target = state.target || { provider: "aws", stage: "dev" };

  const loadHistory = useCallback(() => {
    api.jobs().then((r) => setHistory(r.jobs || []), () => undefined);
  }, []);
  useEffect(loadHistory, [loadHistory]);
  useEffect(() => {
    if (job.status !== "running" && job.status !== "idle") loadHistory();
  }, [job.status, loadHistory]);

  const run = async (command: string, args?: Record<string, unknown>) => {
    if (dirty) {
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
        title: command === "remove" ? `Remove ${typed}?` : `Deploy to ${typed}?`,
        body:
          command === "remove"
            ? "Every function, trigger and managed resource of this stage is destroyed. External resources are left alone."
            : "This is a production stage.",
        typed,
        confirmLabel: command === "remove" ? "Remove" : "Deploy",
        danger: true,
      });
      if (ok !== "confirm") return;
    } else if (command === "deploy") {
      const ok = await confirm({ title: `Deploy to ${target.provider}/${target.stage}?`, confirmLabel: "Deploy" });
      if (ok !== "confirm") return;
    }
    try {
      const r = await api.startJob(command, target.provider, target.stage, args);
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
      <div className="action-grid">
        {ACTIONS.map((a) => (
          <button key={a.command} type="button" className={`action-card btn ${a.tone}`} disabled={running} onClick={() => void run(a.command)} title={a.hint}>
            <b>{a.label}</b>
            <span className="small">{a.hint}</span>
          </button>
        ))}
      </div>
      <JobConsole job={job} onCancel={jobId ? () => void api.cancelJob(jobId).catch((exc: unknown) => toast(errorMessage(exc), "err")) : undefined} />

      <InvokePanel functions={functions} disabled={running} onRun={(args) => void run("invoke", args)} />
      {workflows.length > 0 && <WorkflowRunPanel workflows={workflows} disabled={running} onRun={(args) => void run("workflow-run", args)} />}

      <Section title="Recent jobs" defaultOpen={history.length > 0}>
        {!history.length ? (
          <p className="muted">No jobs yet.</p>
        ) : (
          <ul className="job-list">
            {history.map((h) => (
              <li key={h.job_id}>
                <button type="button" className={`job-item${h.job_id === jobId ? " active" : ""}`} onClick={() => setJobId(h.job_id)}>
                  <span className={`badge badge-${h.status === "succeeded" ? "ok" : h.status === "running" ? "info" : h.status === "cancelled" ? "off" : "err"}`}>{h.status}</span>
                  <code className="mono">pskit {h.command}</code>
                  <span className="muted small grow">{relative(h.started_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
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
            options={["default", "http", "schedule", "queue", "storage", "topic", "invoke"].map((k) => ({ value: k, label: k }))}
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
