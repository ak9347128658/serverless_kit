import { useState } from "react";
import { Chip } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { SearchInput } from "../../components/Inputs";
import { api, errorMessage } from "../api";
import { useConfirm, useToast } from "../components/Feedback";
import { EnumSelect, FieldForm, FormRow, KeyValueEditor, NumberInput, Section, Segmented, TextInput, Toggle } from "../components/Forms";
import { DiffView, InlineIssues, PageHeader } from "../components/Misc";
import { href, navigate } from "../route";
import { providersSupporting, useStudio } from "../store";
import { forgetHandlerStub, queueHandlerStub } from "../stubs";
import { EVENT_TYPES, type EventType, type Json, type JsonObject } from "../types";
import {
  asArray,
  asObject,
  defaultHandler,
  eventSummary,
  eventToRaw,
  functionKind,
  HANDLER_RE,
  NAME_RE,
  normalizeEvent,
  setIn,
  str,
  toYaml,
  uniqueName,
  type NormalEvent,
} from "../util";
import { defaultEvent, EVENT_INFO, TriggerForm } from "./TriggerForms";

interface Row {
  name: string;
  fn: JsonObject;
}

/** Workflows (and nested branches) whose task states call `fn`. */
export function workflowsUsing(draft: JsonObject, fn: string): string[] {
  const found = new Set<string>();
  const walk = (wf: string, def: Json) => {
    for (const st of Object.values(asObject(asObject(def).states))) {
      const s = asObject(st);
      if (s.function === fn) found.add(wf);
      asArray(s.branches).forEach((b) => walk(wf, b));
      if (s.iterator) walk(wf, s.iterator);
    }
  };
  for (const [name, wf] of Object.entries(asObject(draft.workflows))) walk(name, wf);
  return Array.from(found);
}

export function FunctionsView({ rest }: { rest: string[] }) {
  if (rest[0] === "new") return <FunctionWizard name={null} />;
  if (rest[0]) return <FunctionWizard name={rest[0]} />;
  return <FunctionList />;
}

function FunctionList() {
  const { draft, state, edit, replace, issuesAt, provider } = useStudio();
  const confirm = useConfirm();
  const toast = useToast();
  const [q, setQ] = useState("");
  const generated = new Set((state.validation?.functions || []).filter((f) => f.generated).map((f) => f.name));
  const rows: Row[] = Object.entries(asObject(draft.functions)).map(([name, fn]) => ({ name, fn: asObject(fn) }));
  const needle = q.trim().toLowerCase();
  const visible = rows.filter(
    (r) =>
      !needle ||
      [r.name, str(r.fn.handler), str(r.fn.description), ...asArray(r.fn.events).map(eventSummary)].join(" ").toLowerCase().includes(needle),
  );

  const remove = async (name: string) => {
    const usedBy = workflowsUsing(draft, name);
    const answer = await confirm({
      title: `Delete function ${name}?`,
      body: usedBy.length ? (
        <p>
          <b>Used by workflow{usedBy.length > 1 ? "s" : ""} {usedBy.join(", ")}</b>. Those workflows won't validate until you
          point their task states at another function. The handler code is not deleted.
        </p>
      ) : (
        <p>The function and its triggers are removed from pskit.yml. The handler code is not deleted.</p>
      ),
      confirmLabel: "Delete",
      danger: true,
    });
    if (answer !== "confirm") return;
    let raw: Json = setIn(draft, ["functions", name], undefined);
    for (const [key, res] of Object.entries(asObject(draft.resources))) {
      const list = asArray(asObject(res).functions);
      if (list.includes(name)) raw = setIn(raw, ["resources", key, "functions"], list.filter((f) => f !== name));
    }
    replace(raw as JsonObject);
    forgetHandlerStub(str(asObject(asObject(draft.functions)[name]).handler));
    toast(`Deleted ${name} (not saved yet)`, "info");
  };

  const columns: Column<Row>[] = [
    {
      key: "name",
      header: "Function",
      sortValue: (r) => r.name,
      render: (r) => (
        <div className="cell-name">
          <a href={href("functions", r.name)}>
            <b>{r.name}</b>
          </a>
          {r.fn.description ? <span className="muted small">{str(r.fn.description)}</span> : null}
          {issuesAt(["functions", r.name]).some((i) => i.level === "error") && <span className="badge badge-err">error</span>}
        </div>
      ),
    },
    { key: "kind", header: "Kind", sortValue: (r) => functionKind(r.fn), render: (r) => <span className="badge badge-accent">{functionKind(r.fn)}</span> },
    {
      key: "triggers",
      header: "Triggers",
      render: (r) => {
        const evs = asArray(r.fn.events);
        return evs.length ? (
          <span className="chips">
            {evs.map((e, i) => (
              <Chip key={i} accent={normalizeEvent(e)?.type === "http"}>
                {eventSummary(e)}
              </Chip>
            ))}
          </span>
        ) : (
          <span className="muted">direct invoke</span>
        );
      },
    },
    {
      key: "runtime",
      header: "Runtime",
      render: (r) => {
        const prov = asObject(draft.provider);
        const part = (own: Json | undefined, inherited: string, unit = "") =>
          own !== undefined && own !== null && own !== "" ? (
            <b>{str(own) + unit}</b>
          ) : (
            <span className="muted" title="inherited from provider">{inherited + unit}</span>
          );
        return (
          <span className="mono small nowrap">
            {part(r.fn.runtime, str(prov.runtime, provider?.default_runtime || "python3.12"))} · {part(r.fn.memory, str(prov.memory, "256"), " MB")} ·{" "}
            {part(r.fn.timeout, str(prov.timeout, "30"), " s")}
          </span>
        );
      },
    },
    { key: "handler", header: "Handler", sortValue: (r) => str(r.fn.handler), render: (r) => <span className="mono small">{str(r.fn.handler)}</span> },
    {
      key: "actions",
      header: "",
      className: "nowrap",
      render: (r) => (
        <span className="row-gap row-actions" onClick={(e) => e.stopPropagation()}>
          <a className="btn btn-sm" href={href("code", handlerFile(str(r.fn.handler)))} title="Open handler code">
            Code
          </a>
          <button type="button" className="btn btn-sm" onClick={() => duplicate(r)}>
            Duplicate
          </button>
          <button type="button" className="btn btn-sm btn-danger" onClick={() => void remove(r.name)}>
            Delete
          </button>
        </span>
      ),
    },
  ];

  const duplicate = (r: Row) => {
    const name = uniqueName(`${r.name}-copy`, Object.keys(asObject(draft.functions)));
    edit(["functions", name], { ...r.fn, events: asArray(r.fn.events).filter((e) => normalizeEvent(e)?.type !== "http") }, { coalesce: false });
    toast(`Created ${name} (HTTP routes were not copied, they must be unique)`, "info");
    navigate("functions", name);
  };

  return (
    <div className="page">
      <PageHeader
        title="Functions"
        subtitle="Each function is a handler plus the triggers that call it."
        actions={
          <a className="btn btn-primary" href={href("functions", "new")}>
            + Create function
          </a>
        }
      />
      <div className="toolbar">
        <SearchInput value={q} onChange={setQ} placeholder="Search name, handler, trigger…" />
      </div>
      <div className="card">
        <DataTable
          columns={columns}
          rows={visible}
          rowKey={(r) => r.name}
          onRowClick={(r) => navigate("functions", r.name)}
          emptyTitle={rows.length ? "No function matches" : "No functions yet"}
          emptyHint={
            rows.length ? undefined : (
              <a className="btn btn-primary" href={href("functions", "new")}>
                Create your first function
              </a>
            )
          }
        />
      </div>
      {generated.size > 0 && (
        <p className="muted small">
          Plus {generated.size} generated function{generated.size > 1 ? "s" : ""} ({Array.from(generated).join(", ")}) that run embedded
          workflows. pskit adds those itself.
        </p>
      )}
    </div>
  );
}

export function handlerFile(handler: string): string {
  if (!HANDLER_RE.test(handler)) return "";
  const module = handler.replace(":", ".").split(".").slice(0, -1).join("/");
  return `${module}.py`;
}

// --------------------------------------------------------------------- wizard

type Step = "basics" | "triggers" | "settings" | "review";
const STEPS: { id: Step; label: string }[] = [
  { id: "basics", label: "Basics" },
  { id: "triggers", label: "Triggers" },
  { id: "settings", label: "Environment & permissions" },
  { id: "review", label: "Review" },
];

function FunctionWizard({ name: existing }: { name: string | null }) {
  const studio = useStudio();
  const { draft, meta, provider, providerName, replace, issuesAt } = studio;
  const toast = useToast();
  const confirm = useConfirm();
  const functions = asObject(draft.functions);
  const original = existing ? asObject(functions[existing]) : null;
  const [name, setName] = useState(existing || uniqueName("my-function", Object.keys(functions)));
  const [fn, setFn] = useState<JsonObject>(() => (original ? { ...original } : { handler: defaultHandler("my-function") }));
  const [handlerTouched, setHandlerTouched] = useState(!!existing);
  const [events, setEvents] = useState<NormalEvent[]>(() =>
    asArray(original?.events)
      .map(normalizeEvent)
      .filter((e): e is NormalEvent => !!e),
  );
  const [openEvent, setOpenEvent] = useState<number | null>(events.length ? 0 : null);
  const [step, setStep] = useState<Step>("basics");
  const [stubNow, setStubNow] = useState(true);
  const [busy, setBusy] = useState(false);

  if (existing && !original) {
    return (
      <div className="page">
        <PageHeader title="Function not found" subtitle={`There is no function named ${existing}.`} />
        <a className="btn" href={href("functions")}>
          ← Back to functions
        </a>
      </div>
    );
  }

  const nameTaken = name !== existing && name in functions;
  const nameError = !NAME_RE.test(name) ? "start with a letter; letters, digits, - and _ (max 63)" : nameTaken ? "a function with this name exists" : null;
  const handler = str(fn.handler);
  const handlerError = HANDLER_RE.test(handler) ? null : "use package.module.function, e.g. app.handlers.process";
  const routes = new Set<string>();
  for (const [other, cfg] of Object.entries(functions)) {
    if (other === existing) continue;
    for (const ev of asArray(asObject(cfg).events)) {
      const n = normalizeEvent(ev);
      if (n?.type === "http") routes.add(`${str(n.config.method, "GET").toUpperCase()} ${str(n.config.path, "/").replace(/\/$/, "") || "/"}`);
    }
  }
  const eventErrors = events.map((ev) => {
    if (!studio.supports(ev.type)) return `${provider?.display_name || providerName} has no ${EVENT_INFO[ev.type].label.toLowerCase()} triggers`;
    if (ev.type === "http") {
      const key = `${str(ev.config.method, "GET").toUpperCase()} ${str(ev.config.path, "/").replace(/\/$/, "") || "/"}`;
      if (routes.has(key)) return `route ${key} is already used by another function`;
    }
    if (ev.type === "schedule" && !ev.config.rate && !ev.config.cron) return "pick a rate or a cron expression";
    if ((ev.type === "queue" || ev.type === "topic") && !ev.config.name && !ev.config.resource) return "a name or resource is required";
    if (ev.type === "storage" && !ev.config.bucket && !ev.config.resource) return "a bucket or resource is required";
    return null;
  });
  const blocking = !!nameError || !!handlerError || eventErrors.some(Boolean);

  const built = (): JsonObject => {
    const out: JsonObject = {};
    for (const [k, v] of Object.entries(fn)) if (v !== undefined && v !== "" && k !== "events") out[k] = v;
    if (events.length) out.events = events.map(eventToRaw);
    return out;
  };

  const updateName = (n: string) => {
    setName(n);
    if (!handlerTouched) setFn((f) => ({ ...f, handler: defaultHandler(n) }));
  };

  const setField = (key: string, value: Json | undefined) =>
    setFn((f) => {
      const next = { ...f };
      if (value === undefined || value === "") delete next[key];
      else next[key] = value;
      return next;
    });

  const apply = async () => {
    if (blocking) {
      toast("Fix the highlighted fields first", "err");
      return;
    }
    let raw: Json = draft;
    const body = built();
    if (existing && existing !== name) {
      const usedBy = workflowsUsing(draft, existing);
      const ok = await confirm({
        title: `Rename ${existing} to ${name}?`,
        body: usedBy.length ? `Workflow task states in ${usedBy.join(", ")} and resource bindings are updated too.` : "Resource bindings that list it are updated too.",
        confirmLabel: "Rename",
      });
      if (ok !== "confirm") return;
      raw = setIn(raw, ["functions", existing], undefined);
      raw = renameReferences(raw as JsonObject, existing, name);
    }
    // Keep the function's position when editing in place.
    if (existing === name) raw = setIn(raw, ["functions", name], body);
    else raw = setIn(raw, ["functions"], { ...asObject((raw as JsonObject).functions), [name]: body });
    replace(raw as JsonObject);
    const types = events.map((e) => e.type);
    if (!existing || original?.handler !== body.handler) {
      if (stubNow) {
        setBusy(true);
        try {
          const res = await api.ensureHandler(str(body.handler), types);
          toast(res.created ? `Created handler stub in ${res.path}` : `Handler already exists in ${res.path}`, "ok");
        } catch (exc) {
          queueHandlerStub(str(body.handler), types);
          toast(`Couldn't write the handler stub now (${errorMessage(exc)}); it will be created on save`, "warn");
        } finally {
          setBusy(false);
        }
      } else {
        queueHandlerStub(str(body.handler), types);
      }
    }
    toast(existing ? `Updated ${name} (not saved yet)` : `Created ${name} (not saved yet)`, "ok");
    navigate("functions");
  };

  const before = toYaml({ functions: existing && original ? { [existing]: original } : {} });
  const after = toYaml({ functions: { [name]: built() } });
  const stepIndex = STEPS.findIndex((s) => s.id === step);
  const fieldIssues = (key: string) => (existing ? issuesAt(["functions", existing, key]).map((i) => i.message) : []);

  return (
    <div className="page">
      <PageHeader
        title={existing ? `Edit ${existing}` : "Create function"}
        subtitle={`Target: ${provider?.display_name || providerName}. Triggers the provider can't run are disabled.`}
        actions={
          <>
            <a className="btn" href={href("functions")}>
              Cancel
            </a>
            {existing && (
              <a className="btn" href={href("code", handlerFile(handler))}>
                Open code
              </a>
            )}
            <button type="button" className="btn btn-primary" disabled={blocking || busy} onClick={() => void apply()}>
              {existing ? "Apply changes" : "Create function"}
            </button>
          </>
        }
      />
      <ol className="stepper">
        {STEPS.map((s, i) => (
          <li key={s.id} className={s.id === step ? "active" : i < stepIndex ? "done" : ""}>
            <button type="button" onClick={() => setStep(s.id)}>
              <span className="step-num">{i + 1}</span>
              {s.label}
              {s.id === "basics" && (nameError || handlerError) && <span className="dot-err" aria-label="has errors" />}
              {s.id === "triggers" && eventErrors.some(Boolean) && <span className="dot-err" aria-label="has errors" />}
            </button>
          </li>
        ))}
      </ol>

      {step === "basics" && (
        <Section title="Basics">
          <div className="form">
            <FormRow label="Function name" required error={nameError} htmlFor="fname">
              <TextInput id="fname" mono autoFocus={!existing} value={name} invalid={!!nameError} onChange={updateName} />
            </FormRow>
            <FormRow label="Description" htmlFor="fdesc">
              <TextInput id="fdesc" value={str(fn.description)} onChange={(v) => setField("description", v)} placeholder="What does it do?" />
            </FormRow>
            <FormRow
              label="Handler"
              required
              htmlFor="fhandler"
              error={[...(handlerError ? [handlerError] : []), ...fieldIssues("handler")]}
              help={handlerTouched ? undefined : "Derived from the name until you edit it."}
            >
              <TextInput
                id="fhandler"
                mono
                value={handler}
                invalid={!!handlerError}
                onChange={(v) => {
                  setHandlerTouched(true);
                  setField("handler", v);
                }}
              />
            </FormRow>
            <div className="form-grid">
              <FormRow label="Runtime" htmlFor="frt">
                <EnumSelect
                  id="frt"
                  value={str(fn.runtime)}
                  allowEmpty
                  emptyLabel={`inherit (${str(asObject(draft.provider).runtime, provider?.default_runtime || "python3.12")})`}
                  options={provider?.runtimes.length ? provider.runtimes : ["python3.10", "python3.11", "python3.12", "python3.13"]}
                  onChange={(v) => setField("runtime", v || undefined)}
                />
              </FormRow>
              <FormRow label="Memory (MB)" htmlFor="fmem">
                <NumberInput id="fmem" integer min={64} max={32768} value={typeof fn.memory === "number" ? fn.memory : undefined} placeholder={`inherit (${str(asObject(draft.provider).memory, "256")})`} onChange={(v) => setField("memory", v)} />
              </FormRow>
              <FormRow label="Timeout (s)" htmlFor="fto" help={provider ? `max ${provider.max_timeout}s` : undefined} error={fieldIssues("timeout")}>
                <NumberInput id="fto" integer min={1} max={provider?.max_timeout || 3600} value={typeof fn.timeout === "number" ? fn.timeout : undefined} placeholder={`inherit (${str(asObject(draft.provider).timeout, "30")})`} onChange={(v) => setField("timeout", v)} />
              </FormRow>
              <FormRow label="Architecture">
                <Segmented
                  label="Architecture"
                  value={str(fn.architecture, "inherit")}
                  onChange={(v) => setField("architecture", v === "inherit" ? undefined : v)}
                  options={[
                    { value: "inherit", label: "inherit" },
                    { value: "x86_64", label: "x86_64" },
                    { value: "arm64", label: "arm64" },
                  ]}
                />
              </FormRow>
            </div>
          </div>
        </Section>
      )}

      {step === "triggers" && (
        <>
          <Section title="Add a trigger" hint="what calls this function">
            <div className="trigger-grid">
              {EVENT_TYPES.map((type: EventType) => {
                const ok = studio.supports(type);
                const info = EVENT_INFO[type];
                return (
                  <button
                    key={type}
                    type="button"
                    className="trigger-card"
                    disabled={!ok}
                    title={ok ? info.blurb : `Not on ${provider?.display_name}. Supported on: ${providersSupporting(meta, type).join(", ")}`}
                    onClick={() => {
                      setEvents((evs) => [...evs, defaultEvent(type, name)]);
                      setOpenEvent(events.length);
                    }}
                  >
                    <span className="trigger-icon" aria-hidden="true">
                      {info.icon}
                    </span>
                    <b>{info.label}</b>
                    <span className="muted small">{ok ? info.blurb : `Not supported on ${provider?.display_name}`}</span>
                  </button>
                );
              })}
            </div>
          </Section>
          {!events.length && <p className="muted pad">No triggers. The function can still be called with invoke() or from a workflow.</p>}
          {events.map((ev, i) => (
            <Section
              key={i}
              title={
                <span className="row-gap">
                  <span aria-hidden="true">{EVENT_INFO[ev.type].icon}</span>
                  {EVENT_INFO[ev.type].label}
                  <span className="muted mono small">{eventSummary(eventToRaw(ev))}</span>
                  {eventErrors[i] && <span className="badge badge-err">fix</span>}
                </span>
              }
              defaultOpen={openEvent === i}
              actions={
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  onClick={() => {
                    setEvents((evs) => evs.filter((_, j) => j !== i));
                    setOpenEvent(null);
                  }}
                >
                  Remove
                </button>
              }
            >
              {eventErrors[i] && <div className="form-error block">{eventErrors[i]}</div>}
              {existing && <InlineIssues issues={issuesAt(["functions", existing, "events", i])} />}
              <TriggerForm event={ev} fnName={name} onChange={(next) => setEvents((evs) => evs.map((e, j) => (j === i ? next : e)))} />
            </Section>
          ))}
        </>
      )}

      {step === "settings" && (
        <>
          <Section title="Environment variables" hint="merged over the provider-wide ones">
            <KeyValueEditor value={asObject(fn.environment)} keyPattern={/^[A-Za-z_][A-Za-z0-9_]*$/} onChange={(v) => setField("environment", v)} />
          </Section>
          <Section title="Concurrency & tags" defaultOpen={fn.concurrency !== undefined || !!fn.tags}>
            <div className="form">
              <FormRow label="Reserved concurrency / max instances" help="Leave empty for the provider default.">
                <NumberInput integer min={0} value={typeof fn.concurrency === "number" ? fn.concurrency : undefined} onChange={(v) => setField("concurrency", v)} />
              </FormRow>
              <FormRow label="Tags">
                <KeyValueEditor value={asObject(fn.tags)} secrets={false} keyPlaceholder="tag" addLabel="Add tag" onChange={(v) => setField("tags", v)} />
              </FormRow>
            </div>
          </Section>
          <IamSection value={asObject(fn.iam)} onChange={(v) => setField("iam", v)} />
          {(meta.function_fields || []).length > 0 && (
            <Section title="Provider-specific options" defaultOpen={!!fn.options} hint="functions.<name>.options">
              <FieldForm
                fields={meta.function_fields || []}
                value={asObject(fn.options)}
                idPrefix="fopt"
                onChange={(k, v) => setField("options", (() => {
                  const o = { ...asObject(fn.options) };
                  if (v === undefined) delete o[k];
                  else o[k] = v;
                  return Object.keys(o).length ? o : undefined;
                })())}
              />
            </Section>
          )}
        </>
      )}

      {step === "review" && (
        <Section title="Changes to pskit.yml">
          <DiffView before={before} after={after} />
          <div className="review-foot">
            <Toggle checked={stubNow} onChange={setStubNow} label="Create the handler stub in the code now (otherwise when you save)" />
            {blocking && <div className="form-error">Some steps have errors. Fix them before creating the function.</div>}
          </div>
        </Section>
      )}

      <div className="wizard-nav">
        <button type="button" className="btn" disabled={stepIndex === 0} onClick={() => setStep(STEPS[stepIndex - 1].id)}>
          ← Back
        </button>
        {stepIndex < STEPS.length - 1 ? (
          <button type="button" className="btn btn-primary" onClick={() => setStep(STEPS[stepIndex + 1].id)}>
            Next: {STEPS[stepIndex + 1].label} →
          </button>
        ) : (
          <button type="button" className="btn btn-primary" disabled={blocking || busy} onClick={() => void apply()}>
            {existing ? "Apply changes" : "Create function"}
          </button>
        )}
      </div>
    </div>
  );
}

function renameReferences(raw: JsonObject, from: string, to: string): JsonObject {
  const fixStates = (def: Json): Json => {
    const d = asObject(def);
    const states: JsonObject = {};
    for (const [k, st] of Object.entries(asObject(d.states))) {
      const s = { ...asObject(st) };
      if (s.function === from) s.function = to;
      if (Array.isArray(s.branches)) s.branches = s.branches.map(fixStates);
      if (s.iterator) s.iterator = fixStates(s.iterator);
      states[k] = s;
    }
    return { ...d, states };
  };
  let out: Json = raw;
  for (const [wf, def] of Object.entries(asObject(raw.workflows))) out = setIn(out, ["workflows", wf], fixStates(def));
  for (const [key, res] of Object.entries(asObject(raw.resources))) {
    const list = asArray(asObject(res).functions);
    if (list.includes(from)) out = setIn(out, ["resources", key, "functions"], list.map((f) => (f === from ? to : f)));
  }
  return out as JsonObject;
}

function IamSection({ value, onChange }: { value: JsonObject; onChange: (v: JsonObject | undefined) => void }) {
  const statements = asArray(value.statements).map(asObject);
  const commit = (next: JsonObject[], role = value.role) => {
    const out: JsonObject = {};
    if (next.length) out.statements = next;
    if (role) out.role = role;
    onChange(Object.keys(out).length ? out : undefined);
  };
  return (
    <Section title="Permissions (IAM)" defaultOpen={false} hint="advanced">
      <div className="form">
        <FormRow label="Existing role" help="Use an existing role / service account instead of the one pskit creates.">
          <TextInput mono value={str(value.role)} placeholder="arn:aws:iam::123456789012:role/my-role" onChange={(r) => commit(statements, r || undefined)} />
        </FormRow>
        {statements.map((st, i) => (
          <div key={i} className="iam-row card card-pad">
            <div className="row-gap">
              <Segmented
                label="Effect"
                value={str(st.effect, "Allow")}
                onChange={(e) => commit(statements.map((s, j) => (j === i ? { ...s, effect: e } : s)))}
                options={[
                  { value: "Allow", label: "Allow" },
                  { value: "Deny", label: "Deny" },
                ]}
              />
              <span className="grow" />
              <button type="button" className="btn btn-sm btn-danger" onClick={() => commit(statements.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
            <FormRow label="Actions" help="comma separated, e.g. s3:GetObject, dynamodb:Query">
              <TextInput
                mono
                value={asArray(st.actions).map((a) => str(a)).join(", ")}
                onChange={(t) => commit(statements.map((s, j) => (j === i ? { ...s, actions: t.split(",").map((x) => x.trim()).filter(Boolean) } : s)))}
              />
            </FormRow>
            <FormRow label="Resources" help="comma separated ARNs; empty = *">
              <TextInput
                mono
                value={asArray(st.resources).map((a) => str(a)).join(", ")}
                onChange={(t) => {
                  const list = t.split(",").map((x) => x.trim()).filter(Boolean);
                  commit(
                    statements.map((s, j) => {
                      if (j !== i) return s;
                      const { resources: _old, ...restSt } = s;
                      void _old;
                      return list.length ? { ...restSt, resources: list } : restSt;
                    }),
                  );
                }}
              />
            </FormRow>
          </div>
        ))}
        <button type="button" className="btn btn-sm" onClick={() => commit([...statements, { effect: "Allow", actions: [] }])}>
          + Add statement
        </button>
      </div>
    </Section>
  );
}
