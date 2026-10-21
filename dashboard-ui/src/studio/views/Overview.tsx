import { useState } from "react";
import { api, errorMessage } from "../api";
import { compatProblems, setEnabledOn, skipTriggersOn } from "../compat";
import { JobConsole } from "../components/Console";
import { useConfirm, useToast } from "../components/Feedback";
import { ComboInput, EnumSelect, FieldForm, FormRow, KeyValueEditor, Section, Segmented, TextInput } from "../components/Forms";
import { useJob } from "../jobs";
import { href } from "../route";
import { InlineIssues, PageHeader } from "../components/Misc";
import { capabilityLabel, ProviderCards } from "../components/ProviderCard";
import { useStudio } from "../store";
import type { Field, Json, JsonObject } from "../types";
import { asObject, getIn, isObject, SERVICE_RE, setIn, str } from "../util";

const PACKAGE_FIELDS: Field[] = [
  { key: "source", label: "Source directory", type: "string", default: ".", help: "Folder bundled into the deployment, relative to pskit.yml." },
  { key: "requirements", label: "Requirements file", type: "string", default: "requirements.txt" },
  { key: "exclude", label: "Exclude globs", type: "list", help: "e.g. tests/**, *.md" },
  { key: "vendor", label: "Vendor dependencies", type: "bool", help: "Force (on) or skip (off) pip install into the bundle. Default: provider decides." },
];

export const MEMORY = [128, 256, 512, 1024, 1536, 2048, 3008, 4096, 8192, 10240];

export function memoryLabel(mb: number): string {
  return mb >= 1024 && mb % 512 === 0 ? `${mb / 1024} GB` : `${mb} MB`;
}

export function timeoutChoices(max?: number): number[] {
  return [3, 10, 30, 60, 120, 300, 600, 900, 1800, 3600].filter((s) => !max || s <= max);
}

export function durationLabel(seconds: number): string {
  if (seconds < 60 || seconds % 60) return `${seconds} s`;
  if (seconds < 3600 || seconds % 3600) return `${seconds / 60} min`;
  return `${seconds / 3600} h`;
}

const ONPREM_SPECIAL = new Set(["deploy", "services", "execution_retention", "workflow_workers", "log_buffer", "log_level", "capture_output"]);

export function OverviewView() {
  const studio = useStudio();
  const { draft, meta, edit, replace, provider, providerName, issuesAt } = studio;
  const confirm = useConfirm();
  const toast = useToast();
  const prov = asObject(draft.provider);
  const issues = (p: (string | number)[]) => issuesAt(p).map((i) => i.message);

  const switchProvider = async (name: string) => {
    if (name === providerName) return;
    const next = meta.providers.find((p) => p.name === name);
    if (!next) return;
    const problems = compatProblems(draft, next);
    const lost = problems.map((p) => p.message);
    const answer = await confirm({
      title: `Switch to ${next.display_name}?`,
      body: (
        <div className="stack">
          {lost.length ? (
            <>
              <p>These won't run on {next.display_name}:</p>
              <ul className="bullets">
                {lost.slice(0, 12).map((l) => (
                  <li key={l}>{l}</li>
                ))}
                {lost.length > 12 && <li>…and {lost.length - 12} more</li>}
              </ul>
              <p className="small">
                <b>Switch &amp; disable them on {next.display_name}</b> keeps them for the other clouds: the unsupported triggers
                and workflows are switched off only under <code className="mono">providers.{name}</code>. Turn them back on from
                the Deploy page or the function itself.
              </p>
            </>
          ) : (
            <p>Every function, trigger and workflow is supported on {next.display_name}.</p>
          )}
          <p className="muted small">
            The current provider options move to <code className="mono">providers.{providerName}</code> so switching back
            restores them. Handler code doesn't change.
          </p>
        </div>
      ),
      confirmLabel: lost.length ? `Switch & disable on ${next.display_name}` : "Switch provider",
      extra: lost.length ? [{ label: "Switch only (fix later)", value: "plain" }] : undefined,
    });
    if (answer !== "confirm" && answer !== "plain") return;
    let raw: Json = draft;
    if (answer === "confirm") {
      for (const p of problems) {
        if (p.kind === "trigger" && p.fn && p.types) raw = skipTriggersOn(raw as JsonObject, name, p.fn, p.types);
        else if (p.wf) raw = setEnabledOn(raw as JsonObject, name, "workflows", p.wf, false);
      }
    }
    const oldOptions = prov.options;
    const oldRegion = prov.region;
    if (isObject(oldOptions) && Object.keys(oldOptions).length) raw = setIn(raw, ["providers", providerName, "options"], oldOptions);
    if (oldRegion) raw = setIn(raw, ["providers", providerName, "region"], oldRegion);
    const parked = asObject(getIn(draft, ["providers", name]));
    raw = setIn(raw, ["provider", "name"], name);
    raw = setIn(raw, ["provider", "options"], isObject(parked.options) ? parked.options : undefined);
    raw = setIn(raw, ["provider", "region"], str(parked.region) || next.default_region || undefined);
    const runtime = str(prov.runtime);
    if (runtime && next.runtimes.length && !next.runtimes.includes(runtime)) raw = setIn(raw, ["provider", "runtime"], undefined);
    replace(raw as JsonObject);
    studio.setTarget(name);
    toast(`Provider is now ${next.display_name}`, "ok");
  };

  const options = asObject(prov.options);
  const optionFields = (provider?.options || []).filter((f) => providerName !== "onprem" || !ONPREM_SPECIAL.has(f.key));
  const serviceName = str(draft.service);

  return (
    <div className="page">
      <PageHeader title="Overview & provider" subtitle="Service-wide settings. Every function inherits these unless it overrides them." />

      <Section title="Service">
        <FormRow
          label="Service name"
          required
          htmlFor="svc"
          error={[...(SERVICE_RE.test(serviceName) ? [] : ["2–32 lowercase letters, digits or dashes"]), ...issues(["service"])]}
        >
          <TextInput id="svc" mono value={serviceName} onChange={(v) => edit(["service"], v.toLowerCase())} />
        </FormRow>
      </Section>

      <Section title="Provider" hint={provider ? `${provider.display_name} · ${provider.tier}` : undefined}>
        <ProviderCards providers={meta.providers} value={providerName} onSelect={(n) => void switchProvider(n)} />
        {provider?.notes && <p className="muted small provider-notes">{provider.notes}</p>}
      </Section>

      <Section title="Defaults for every function">
        <div className="form-grid">
          <FormRow label="Region" htmlFor="region" help={provider ? `default: ${provider.default_region || "none"}` : undefined} error={issues(["provider", "region"])}>
            {provider?.regions && provider.regions.length ? (
              <ComboInput
                id="region"
                value={str(prov.region)}
                options={provider.regions.map((r) => ({ value: r.id, label: r.label }))}
                emptyLabel={`default (${provider.default_region || "provider default"})`}
                placeholder={provider.default_region}
                onChange={(v) => edit(["provider", "region"], v || undefined)}
              />
            ) : (
              <TextInput id="region" mono value={str(prov.region)} placeholder={provider?.default_region || "not used"} onChange={(v) => edit(["provider", "region"], v || undefined)} />
            )}
          </FormRow>
          <FormRow label="Stage" htmlFor="stage" help="dev, staging, prod… (lowercase, ≤ 16)" error={issues(["provider", "stage"])}>
            <ComboInput
              id="stage"
              value={str(prov.stage)}
              options={["dev", "test", "staging", "prod"]}
              emptyLabel="default (dev)"
              placeholder="dev"
              onChange={(v) => edit(["provider", "stage"], v.toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 16) || undefined)}
            />
          </FormRow>
          <FormRow label="Runtime" htmlFor="runtime" error={issues(["provider", "runtime"])}>
            <EnumSelect
              id="runtime"
              value={str(prov.runtime)}
              allowEmpty
              emptyLabel={`default (${provider?.default_runtime || "python3.12"})`}
              options={provider?.runtimes.length ? provider.runtimes : ["python3.9", "python3.10", "python3.11", "python3.12", "python3.13"]}
              onChange={(v) => edit(["provider", "runtime"], v || undefined)}
            />
          </FormRow>
          <FormRow label="Memory (MB)" htmlFor="mem" help="64–32768" error={issues(["provider", "memory"])}>
            <ComboInput
              id="mem"
              numeric
              value={typeof prov.memory === "number" ? String(prov.memory) : ""}
              options={MEMORY.map((m) => ({ value: String(m), label: memoryLabel(m) }))}
              emptyLabel="default (256 MB)"
              onChange={(v) => edit(["provider", "memory"], v === "" ? undefined : Number(v))}
            />
          </FormRow>
          <FormRow label="Timeout (s)" htmlFor="timeout" help={provider ? `max ${provider.max_timeout}s on ${provider.display_name}` : undefined} error={issues(["provider", "timeout"])}>
            <ComboInput
              id="timeout"
              numeric
              value={typeof prov.timeout === "number" ? String(prov.timeout) : ""}
              options={timeoutChoices(provider?.max_timeout).map((s) => ({ value: String(s), label: durationLabel(s) }))}
              emptyLabel="default (30 s)"
              onChange={(v) => edit(["provider", "timeout"], v === "" ? undefined : Number(v))}
            />
          </FormRow>
          <FormRow label="Architecture">
            <Segmented
              label="Architecture"
              value={str(prov.architecture, "x86_64")}
              onChange={(v) => edit(["provider", "architecture"], v === "x86_64" ? undefined : v)}
              options={[
                { value: "x86_64", label: "x86_64" },
                { value: "arm64", label: "arm64" },
              ]}
            />
          </FormRow>
          <FormRow label="Log retention (days)" htmlFor="logret">
            <ComboInput
              id="logret"
              numeric
              value={typeof prov.log_retention_days === "number" ? String(prov.log_retention_days) : ""}
              options={[1, 3, 7, 14, 30, 60, 90, 180, 365].map((d) => ({ value: String(d), label: `${d} day${d === 1 ? "" : "s"}` }))}
              emptyLabel="default (14 days)"
              onChange={(v) => edit(["provider", "log_retention_days"], v === "" ? undefined : Number(v))}
            />
          </FormRow>
        </div>
      </Section>

      <Section title="Environment variables" hint="available to every function" defaultOpen={Object.keys(asObject(prov.environment)).length > 0}>
        <KeyValueEditor value={asObject(prov.environment)} keyPattern={/^[A-Za-z_][A-Za-z0-9_]*$/} onChange={(v) => edit(["provider", "environment"], v, { coalesce: true })} />
        <InlineIssues issues={issuesAt(["provider", "environment"])} />
      </Section>

      <Section title="Tags" defaultOpen={Object.keys(asObject(prov.tags)).length > 0}>
        <KeyValueEditor value={asObject(prov.tags)} secrets={false} keyPlaceholder="tag" addLabel="Add tag" onChange={(v) => edit(["provider", "tags"], v)} />
      </Section>

      {optionFields.length > 0 && (
        <Section title={`${provider?.display_name || providerName} options`} hint="provider.options">
          <FieldForm
            fields={optionFields}
            value={options}
            idPrefix="popt"
            errorsFor={(k) => issues(["provider", "options", k])}
            onChange={(k, v) => edit(["provider", "options", k], v)}
          />
        </Section>
      )}

      {providerName === "onprem" && <OnPremSection />}

      <Section title="Packaging" defaultOpen={false} hint="package">
        <FieldForm fields={PACKAGE_FIELDS} value={asObject(draft.package)} idPrefix="pkg" onChange={(k, v) => edit(["package", k], v)} />
      </Section>
    </div>
  );
}

function OnPremSection() {
  const { draft, meta, edit, issuesAt } = useStudio();
  const options = asObject(getIn(draft, ["provider", "options"]));
  const rawDeploy = options.deploy;
  const deploy: JsonObject = typeof rawDeploy === "string" ? { method: rawDeploy } : asObject(rawDeploy);
  const method = str(deploy.method, "local");
  const methods = meta.onprem.deploy_methods.length ? meta.onprem.deploy_methods : ["local", "ssh", "docker"];
  const deployFields = meta.onprem.deploy_fields[method] || [];
  const services = asObject(options.services);
  const kinds = (meta.onprem.services && meta.onprem.services.length ? meta.onprem.services : ["queue", "storage", "topic"]).filter((k) =>
    ["queue", "storage", "topic"].includes(k),
  );
  const setDeploy = (next: JsonObject) => {
    const clean: JsonObject = {};
    for (const [k, v] of Object.entries(next)) if (v !== undefined && v !== "" && v !== null) clean[k] = v;
    const keys = Object.keys(clean);
    if (!keys.length || (keys.length === 1 && keys[0] === "method")) {
      edit(["provider", "options", "deploy"], !clean.method || clean.method === "local" ? undefined : clean.method);
    } else {
      edit(["provider", "options", "deploy"], clean);
    }
  };
  return (
    <>
      <Section title="On-premises deployment" hint="provider.options.deploy">
        <FormRow label="Where the host runs" help={method === "local" ? "This machine." : method === "ssh" ? "A server reached over SSH (scp + process or systemd)." : "A Docker container, locally or on a server over SSH."}>
          <Segmented
            label="Deploy method"
            value={method}
            onChange={(m) => setDeploy({ ...deploy, method: m })}
            options={methods.map((m) => ({ value: m, label: m === "local" ? "This machine" : m === "ssh" ? "SSH server" : "Docker" }))}
          />
        </FormRow>
        {deployFields.length > 0 && (
          <FieldForm
            fields={deployFields}
            value={deploy}
            idPrefix={`dep-${method}`}
            errorsFor={(k) => issuesAt(["provider", "options", "deploy", k]).map((i) => i.message)}
            onChange={(k, v) => setDeploy({ ...deploy, [k]: v === undefined ? "" : v, method })}
          />
        )}
        {(method === "ssh" || (method === "docker" && !!str(deploy.host))) && <SshTest deploy={deploy} />}
        <p className="muted small">
          Missing ssh, scp or Docker on this machine? <a href={href("setup")}>Setup &amp; credentials</a> checks them and shows how
          to install them.
        </p>
      </Section>
      <Section title="Built-in services" hint="provider.options.services" defaultOpen={Object.keys(services).length > 0}>
        <p className="muted small">
          By default queues, buckets and topics are folders the host creates. Point one at a folder you already have, or
          attach an external queue/bucket per resource on the Resources page.
        </p>
        {kinds.map((kind) => {
          const raw = services[kind];
          const body: JsonObject = typeof raw === "string" ? { use: raw } : asObject(raw);
          const use = str(body.use, "builtin");
          return (
            <FormRow key={kind} label={capabilityLabel(kind)} error={issuesAt(["provider", "options", "services", kind]).map((i) => i.message)}>
              <div className="row-gap wrap">
                <Segmented
                  label={`${kind} service`}
                  value={use}
                  onChange={(u) => edit(["provider", "options", "services", kind], u === "builtin" ? undefined : { use: u, path: str(body.path) })}
                  options={[
                    { value: "builtin", label: "Built-in" },
                    { value: "directory", label: "Existing folder" },
                  ]}
                />
                {use === "directory" && (
                  <TextInput mono value={str(body.path)} placeholder="/var/pskit/queues" onChange={(p) => edit(["provider", "options", "services", kind], { use: "directory", path: p })} />
                )}
              </div>
            </FormRow>
          );
        })}
      </Section>
      {meta.onprem.tuning.length > 0 && (
        <Section title="Host tuning" defaultOpen={false} hint="logs, executions, concurrency">
          <FieldForm
            fields={meta.onprem.tuning}
            value={options}
            idPrefix="tune"
            errorsFor={(k) => issuesAt(["provider", "options", k]).map((i) => i.message)}
            onChange={(k, v) => edit(["provider", "options", k], v)}
          />
        </Section>
      )}
    </>
  );
}

function SshTest({ deploy }: { deploy: JsonObject }) {
  const toast = useToast();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const host = str(deploy.host);
  const start = () =>
    api.sshTest(deploy).then(
      (r) => setJobId(r.job_id),
      (exc: unknown) => toast(errorMessage(exc), "err"),
    );
  return (
    <div className="stack-xs">
      <div className="row-gap">
        <button
          type="button"
          className="btn"
          disabled={!host || job.status === "running"}
          title={host ? `ssh ${str(deploy.user, "pskit")}@${host} echo pskit-ok` : "Set the SSH host first"}
          onClick={() => void start()}
        >
          Test SSH connection
        </button>
        {job.status === "succeeded" && <span className="badge badge-ok">connected</span>}
        {job.status === "failed" && <span className="badge badge-err">failed: see the log</span>}
      </div>
      {jobId && <JobConsole job={job} />}
    </div>
  );
}
