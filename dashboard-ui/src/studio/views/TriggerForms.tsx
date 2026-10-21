import { useState } from "react";
import { EnumSelect, FieldForm, FormRow, JsonInput, ListEditor, NumberInput, Segmented, TextInput, Toggle } from "../components/Forms";
import { DiscoverModal } from "../components/Misc";
import { useStudio } from "../store";
import type { EventType, Field, Json, JsonObject } from "../types";
import { asArray, asObject, describeCron, isObject, pathParams, str, validCron, type NormalEvent } from "../util";

export const EVENT_INFO: Record<EventType, { label: string; icon: string; blurb: string }> = {
  http: { label: "HTTP endpoint", icon: "⇄", blurb: "Call the function with an HTTP request (API route)." },
  schedule: { label: "Schedule", icon: "⏱", blurb: "Run on a timer: every N minutes or a cron expression." },
  queue: { label: "Queue", icon: "☰", blurb: "Process messages from a queue in batches, with retries." },
  storage: { label: "Storage", icon: "▣", blurb: "React to files created or deleted in a bucket." },
  topic: { label: "Topic", icon: "✺", blurb: "Receive published messages (fan-out pub/sub)." },
};

export function defaultEvent(type: EventType, name: string): NormalEvent {
  switch (type) {
    case "http":
      return { type, config: { method: "GET", path: `/${name.replace(/_/g, "-")}` } };
    case "schedule":
      return { type, config: { rate: "5 minutes" } };
    case "queue":
      return { type, config: { name: `${name}-jobs`, batch_size: 10 } };
    case "storage":
      return { type, config: { bucket: `${name}-uploads`, events: ["created"] } };
    default:
      return { type, config: { name: `${name}-events` } };
  }
}

export function TriggerForm({ event, onChange, fnName }: { event: NormalEvent; onChange: (next: NormalEvent) => void; fnName: string }) {
  const set = (key: string, value: Json | undefined) => {
    const config = { ...event.config };
    if (value === undefined || value === "") delete config[key];
    else config[key] = value;
    onChange({ ...event, config });
  };
  switch (event.type) {
    case "http":
      return <HttpForm config={event.config} set={set} />;
    case "schedule":
      return <ScheduleForm config={event.config} set={set} replace={(c) => onChange({ ...event, config: c })} />;
    default:
      return <SourceForm type={event.type} config={event.config} set={set} replace={(c) => onChange({ ...event, config: c })} fnName={fnName} />;
  }
}

// --------------------------------------------------------------------- HTTP

function HttpForm({ config, set }: { config: JsonObject; set: (k: string, v: Json | undefined) => void }) {
  const { meta } = useStudio();
  const methods = meta.http_methods.length ? meta.http_methods : ["GET", "POST", "PUT", "PATCH", "DELETE", "ANY"];
  const auths = meta.auth_modes && meta.auth_modes.length ? meta.auth_modes : ["none", "iam", "jwt", "key"];
  const path = str(config.path, "/");
  const pathOk = /^\/?([\w.~-]+|\{[A-Za-z_]\w*\})?(\/([\w.~-]+|\{[A-Za-z_]\w*\}))*(\/\{[A-Za-z_]\w*\+\})?\/?$/.test(path.trim() || "/");
  const params = pathParams(path);
  const cors = config.cors;
  const corsOn = cors === true || isObject(cors);
  const corsObj = asObject(cors);
  return (
    <div className="form">
      <FormRow label="Route" required error={pathOk ? null : "use segments like /users/{id} or /files/{path+}"}>
        <div className="route-input">
          <EnumSelect value={str(config.method, "GET")} options={methods} onChange={(m) => set("method", m)} />
          <TextInput mono value={path} invalid={!pathOk} placeholder="/users/{id}" onChange={(p) => set("path", p.startsWith("/") ? p : `/${p}`)} />
        </div>
      </FormRow>
      {params.length > 0 && (
        <div className="hint-box">
          Path parameters: {params.map((p) => (
            <code key={p} className="mono chip">
              {p}
            </code>
          ))}{" "}
          → <code className="mono">event.http.path_params["{params[0]}"]</code>
        </div>
      )}
      <FormRow label="Auth" help="none = public. iam / jwt / key are enforced by the provider's gateway where supported.">
        <Segmented label="Auth" value={str(config.auth, "none")} onChange={(a) => set("auth", a === "none" ? undefined : a)} options={auths.map((a) => ({ value: a, label: a }))} />
      </FormRow>
      {str(config.auth) === "jwt" && (
        <FormRow label="Authorizer" help='e.g. {"issuer": "https://…", "audience": ["api"]}'>
          <JsonInput value={config.authorizer} onChange={(v) => set("authorizer", v)} rows={4} />
        </FormRow>
      )}
      <FormRow label="CORS">
        <Toggle checked={corsOn} label={corsOn ? "enabled" : "off"} onChange={(on) => set("cors", on ? true : undefined)} />
      </FormRow>
      {corsOn && (
        <FormRow label="Allowed origins" help="Empty = any origin (*).">
          <ListEditor
            value={asArray(corsObj.origins).map((o) => str(o))}
            placeholder="https://app.example.com"
            addLabel="Add origin"
            onChange={(origins) => {
              const { origins: _old, ...others } = corsObj;
              void _old;
              if (origins) set("cors", { ...others, origins });
              else set("cors", Object.keys(others).length ? others : true);
            }}
          />
        </FormRow>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- schedule

const RATE_PRESETS = ["1 minute", "5 minutes", "15 minutes", "1 hour", "6 hours", "1 day"];
const CRON_PRESETS = [
  { label: "Every day 09:00", value: "0 9 * * *" },
  { label: "Weekdays 08:30", value: "30 8 * * 1-5" },
  { label: "Every hour", value: "0 */1 * * *" },
  { label: "1st of month", value: "0 0 1 * *" },
];
const ZONES = ["UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles", "Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney"];

function ScheduleForm({
  config,
  set,
  replace,
}: {
  config: JsonObject;
  set: (k: string, v: Json | undefined) => void;
  replace: (c: JsonObject) => void;
}) {
  const mode = config.cron ? "cron" : "rate";
  const rate = str(config.rate, "5 minutes");
  const m = /^\s*(\d+)\s+(minute|minutes|hour|hours|day|days)\s*$/i.exec(rate);
  const amount = m ? Number(m[1]) : 5;
  const unit = m ? m[2].replace(/s$/, "").toLowerCase() : "minute";
  const cron = str(config.cron);
  const setRate = (n: number | undefined, u: string) => {
    const count = n && n > 0 ? Math.floor(n) : 1;
    set("rate", `${count} ${u}${count === 1 ? "" : "s"}`);
  };
  return (
    <div className="form">
      <FormRow label="Run">
        <Segmented
          label="Schedule type"
          value={mode}
          onChange={(v) => {
            const { rate: _r, cron: _c, ...rest } = config;
            void _r;
            void _c;
            replace(v === "cron" ? { ...rest, cron: "0 9 * * *" } : { ...rest, rate: "5 minutes" });
          }}
          options={[
            { value: "rate", label: "Every…" },
            { value: "cron", label: "Cron expression" },
          ]}
        />
      </FormRow>
      {mode === "rate" ? (
        <FormRow label="Interval" help="Rates run every N minutes, hours or days from deploy time.">
          <div className="row-gap wrap">
            <span className="muted">every</span>
            <NumberInput integer min={1} value={amount} onChange={(n) => setRate(n, unit)} />
            <EnumSelect value={unit} options={["minute", "hour", "day"]} onChange={(u) => setRate(amount, u)} />
            <span className="chips">
              {RATE_PRESETS.map((p) => (
                <button key={p} type="button" className={rate === p ? "chip chip-accent chip-btn" : "chip chip-btn"} onClick={() => set("rate", p)}>
                  {p}
                </button>
              ))}
            </span>
          </div>
        </FormRow>
      ) : (
        <FormRow
          label="Cron"
          help={validCron(cron) ? `→ ${describeCron(cron)}` : "5 fields: minute hour day-of-month month day-of-week"}
          error={cron && !validCron(cron) ? "a cron expression has exactly 5 fields" : null}
        >
          <div className="stack-xs">
            <TextInput mono value={cron} invalid={!!cron && !validCron(cron)} placeholder="0 9 * * 1-5" onChange={(c) => set("cron", c)} />
            <span className="chips">
              {CRON_PRESETS.map((p) => (
                <button key={p.value} type="button" className={cron === p.value ? "chip chip-accent chip-btn" : "chip chip-btn"} onClick={() => set("cron", p.value)}>
                  {p.label}
                </button>
              ))}
            </span>
          </div>
        </FormRow>
      )}
      {mode === "cron" && (
        <FormRow label="Timezone" help="IANA name; the cron is evaluated in this zone.">
          <TextInput mono value={str(config.timezone)} placeholder="UTC" list="pskit-zones" onChange={(z) => set("timezone", z === "UTC" ? undefined : z)} />
          <datalist id="pskit-zones">
            {ZONES.map((z) => (
              <option key={z} value={z} />
            ))}
          </datalist>
        </FormRow>
      )}
      <FormRow label="Input" help="JSON passed as event.data on every run (optional).">
        <JsonInput value={config.input} onChange={(v) => set("input", v)} rows={3} placeholder='{"report": "daily"}' />
      </FormRow>
      <FormRow label="Enabled">
        <Toggle checked={config.enabled !== false} onChange={(b) => set("enabled", b ? undefined : false)} label={config.enabled === false ? "paused" : "active"} />
      </FormRow>
    </div>
  );
}

// --------------------------------------------------------------------- queue / storage / topic

type SourceMode = "new" | "resource" | "existing";

const HANDLED = new Set(["name", "bucket", "resource", "existing", "events"]);

function SourceForm({
  type,
  config,
  set,
  replace,
  fnName,
}: {
  type: "queue" | "storage" | "topic";
  config: JsonObject;
  set: (k: string, v: Json | undefined) => void;
  replace: (c: JsonObject) => void;
  fnName: string;
}) {
  const { draft, meta, providerName, state } = useStudio();
  const [browse, setBrowse] = useState(false);
  const nameKey = type === "storage" ? "bucket" : "name";
  const resources = Object.entries(asObject(draft.resources)).filter(([, r]) => str(asObject(r).type) === type);
  const mode: SourceMode = config.resource ? "resource" : config.existing ? "existing" : "new";
  const noun = type === "storage" ? "bucket" : type;
  const target = state.target?.provider || providerName;
  const region = str(asObject(draft.provider).region) || meta.providers.find((p) => p.name === target)?.default_region;
  const fields: Field[] = (meta.events[type] || []).filter((f) => !HANDLED.has(f.key));

  const setMode = (m: SourceMode) => {
    const { resource: _r, existing: _e, ...rest } = config;
    void _r;
    void _e;
    if (m === "resource") {
      const first = resources[0]?.[0];
      const { [nameKey]: _n, ...noName } = rest;
      void _n;
      replace({ ...noName, resource: first || "" });
    } else if (m === "existing") {
      replace({ ...rest, existing: true, [nameKey]: str(rest[nameKey]) });
    } else {
      replace({ ...rest, [nameKey]: str(rest[nameKey]) || `${fnName}-${type === "storage" ? "uploads" : type === "queue" ? "jobs" : "events"}` });
    }
  };

  return (
    <div className="form">
      <FormRow label="Source">
        <Segmented
          label={`${noun} source`}
          value={mode}
          onChange={setMode}
          options={[
            { value: "new", label: `New ${noun}`, title: "pskit creates it on deploy" },
            { value: "resource", label: "From Resources", disabled: !resources.length, title: resources.length ? "Use a resources: entry" : `No ${noun} resources defined yet` },
            { value: "existing", label: `Existing ${noun}`, title: "Already exists in the account; pskit only attaches the trigger" },
          ]}
        />
      </FormRow>
      {mode === "resource" ? (
        <FormRow label="Resource" required help="Name, managed/external and on-prem driver come from the Resources page.">
          <EnumSelect
            value={str(config.resource)}
            options={resources.map(([k, r]) => ({ value: k, label: `${k}${asObject(r).external ? " (external)" : ""}` }))}
            onChange={(k) => set("resource", k)}
          />
        </FormRow>
      ) : (
        <FormRow
          label={type === "storage" ? "Bucket name" : `${type[0].toUpperCase()}${type.slice(1)} name`}
          required
          help={mode === "new" ? "pskit prefixes it with service and stage when it creates it." : "The exact name in the account."}
        >
          <div className="row-gap">
            <TextInput mono value={str(config[nameKey])} placeholder={`${fnName}-${noun}`} onChange={(v) => set(nameKey, v)} />
            {mode === "existing" && (
              <button type="button" className="btn" onClick={() => setBrowse(true)} title={`List ${noun}s in the ${target} account`}>
                Browse account…
              </button>
            )}
          </div>
        </FormRow>
      )}
      {type === "storage" && (
        <FormRow label="Fire on">
          <div className="row-gap">
            {["created", "deleted"].map((ev) => {
              const list = asArray(config.events).map((e) => str(e));
              const current = list.length ? list : ["created"];
              const on = current.includes(ev);
              return (
                <label key={ev} className="check">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={(e) => {
                      const next = e.target.checked ? [...current.filter((x) => x !== ev), ev] : current.filter((x) => x !== ev);
                      set("events", next.length ? next.sort() : ["created"]);
                    }}
                  />
                  {ev}
                </label>
              );
            })}
          </div>
        </FormRow>
      )}
      {fields.length > 0 && <FieldForm fields={fields} value={config} onChange={set} idPrefix={`ev-${type}`} compact />}
      <DiscoverModal
        open={browse}
        provider={target}
        kind={type}
        region={region}
        onClose={() => setBrowse(false)}
        onPick={(item) => replace({ ...config, existing: true, [nameKey]: item.name })}
      />
    </div>
  );
}
