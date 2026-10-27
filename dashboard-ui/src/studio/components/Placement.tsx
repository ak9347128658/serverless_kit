import { useEffect, useState } from "react";
import { api } from "../api";
import { useStudio } from "../store";
import type { Json, JsonObject, OnPremCluster, OnPremDeployed, OnPremServer } from "../types";
import { asArray, asObject, getIn, isObject, setIn, str, type Path } from "../util";
import { FormRow, NumberInput, Segmented, TextInput } from "./Forms";

/**
 * provider.options.placement (on premises): groups of functions on different servers, one project.
 *
 *   placement:
 *     default: api                 # functions not listed (and every workflow) run here
 *     groups:
 *       api: {server: app1}        # or {cluster: prod} or {local: true}; only the default group may omit it
 *       gpu: {server: gpu1, functions: [resize, ocr], port: 8081, advertise: http://10.0.0.9:8081}
 */

export const GROUP_RE = /^[a-z][a-z0-9-]{0,20}$/;
const TARGET_KEYS = ["server", "cluster", "local"] as const;

/** Where placement is written: a `providers.onprem.options` override when one already holds it, else provider.options. */
export function placementPath(draft: JsonObject): Path {
  const override = ["providers", "onprem", "options", "placement"];
  return getIn(draft, override) !== undefined ? override : ["provider", "options", "placement"];
}

export interface PlacementGroup {
  name: string;
  body: JsonObject;
  /** Functions listed under the group. */
  listed: string[];
}

export interface Placement {
  path: Path;
  raw: JsonObject;
  default: string;
  groups: PlacementGroup[];
  /** Every listed function -> its group (unlisted functions run on `default`). */
  listedIn: Record<string, string>;
}

/** The project's placement, or null when every function runs on one target (no `groups`). */
export function readPlacement(draft: JsonObject): Placement | null {
  const path = placementPath(draft);
  const raw = asObject(getIn(draft, path));
  const groupsRaw = asObject(raw.groups);
  const names = Object.keys(groupsRaw);
  if (!names.length) return null;
  const listedIn: Record<string, string> = {};
  const groups = names.map((name) => {
    const body = asObject(groupsRaw[name]);
    const listed = (typeof body.functions === "string" ? [body.functions] : asArray(body.functions)).map((f) => str(f)).filter(Boolean);
    for (const fn of listed) if (!(fn in listedIn)) listedIn[fn] = name;
    return { name, body, listed };
  });
  const def = str(raw.default);
  return { path, raw, default: def && names.includes(def) ? def : names[0], groups, listedIn };
}

/** The group a function runs in (its listed group, else the default group); "" without placement. */
export function groupOf(placement: Placement | null, fn: string): string {
  if (!placement) return "";
  return placement.listedIn[fn] || placement.default;
}

/** User functions placement can list: generated `<workflow>-orchestrator` functions always run on the default group. */
export function placeableFunctions(draft: JsonObject): string[] {
  return Object.keys(asObject(draft.functions)).filter((f) => !f.endsWith("-orchestrator"));
}

/** `{server: app1}` -> "app1", `{cluster: prod}` -> "cluster prod", `{local: true}` -> "this machine", `{}` -> "". */
export function targetLabel(body: JsonObject): string {
  if (body.server) return str(body.server);
  if (body.cluster) return `cluster ${clusterName(body.cluster)}`;
  if (body.local) return "this machine";
  return "";
}

function clusterName(v: Json | undefined): string {
  return typeof v === "string" ? v : str(asObject(v).name);
}

function targetValue(body: JsonObject): string {
  if (body.server) return `server:${str(body.server)}`;
  if (body.cluster) return `cluster:${clusterName(body.cluster)}`;
  if (body.local) return "local";
  return "";
}

/** Clean group body: drop empty values (and an empty functions list). */
function clean(body: JsonObject): JsonObject {
  const out: JsonObject = {};
  for (const [k, v] of Object.entries(body)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (Array.isArray(v) && !v.length) continue;
    out[k] = v;
  }
  return out;
}

function withKey(body: JsonObject, key: string, value: Json | undefined): JsonObject {
  const next = { ...body };
  if (value === undefined) delete next[key];
  else next[key] = value;
  return next;
}

/** Write `groups` (ordered) and `default` back to the draft. */
function writePlacement(draft: JsonObject, path: Path, def: string, groups: { name: string; body: JsonObject }[]): JsonObject {
  const raw = asObject(getIn(draft, path));
  const out: JsonObject = {};
  for (const [k, v] of Object.entries(raw)) if (k !== "default" && k !== "groups") out[k] = v;
  out.default = def;
  out.groups = Object.fromEntries(groups.map((g) => [g.name, clean(g.body)]));
  return setIn(draft, path, out) as JsonObject;
}

/** Move `fn` into `group` (the default group: unlisted). Returns the new draft. */
export function moveFunction(draft: JsonObject, fn: string, group: string): JsonObject {
  const p = readPlacement(draft);
  if (!p) return draft;
  const groups = p.groups.map((g) => {
    let listed = g.listed.filter((f) => f !== fn);
    if (g.name === group && group !== p.default) listed = [...listed, fn];
    return { name: g.name, body: { ...g.body, functions: listed } };
  });
  return writePlacement(draft, p.path, p.default, groups);
}

/** Keep placement in step with a function rename / delete (`to` null). */
export function renameInPlacement(draft: JsonObject, from: string, to: string | null): JsonObject {
  const p = readPlacement(draft);
  if (!p || !(from in p.listedIn)) return draft;
  const groups = p.groups.map((g) => ({
    name: g.name,
    body: { ...g.body, functions: g.listed.flatMap((f) => (f !== from ? [f] : to ? [to] : [])) },
  }));
  return writePlacement(draft, p.path, p.default, groups);
}

/** Drop `providers.onprem.options` / `providers.onprem` / `providers` when left empty. */
function prune(draft: JsonObject): JsonObject {
  let out: Json = draft;
  for (const path of [["providers", "onprem", "options"], ["providers", "onprem"], ["providers"], ["provider", "options"]]) {
    const v = getIn(out, path);
    if (isObject(v) && !Object.keys(v).length) out = setIn(out, path, undefined);
  }
  return out as JsonObject;
}

function nextGroupName(taken: string[]): string {
  for (const base of ["workers", "gpu", "batch"]) if (!taken.includes(base)) return base;
  let i = 2;
  while (taken.includes(`group-${i}`)) i++;
  return `group-${i}`;
}

/** Saved servers and clusters for the target pickers. */
export function usePlacementTargets(enabled: boolean): { servers: OnPremServer[]; clusters: OnPremCluster[] } {
  const [servers, setServers] = useState<OnPremServer[]>([]);
  const [clusters, setClusters] = useState<OnPremCluster[]>([]);
  useEffect(() => {
    if (!enabled) return;
    api.onpremServers().then(
      (r) => setServers(r.servers),
      () => setServers([]),
    );
    api.clusters().then(
      (r) => setClusters(r.clusters),
      () => setClusters([]),
    );
  }, [enabled]);
  return { servers, clusters };
}

/** "Where functions run": one target for everything, or groups of functions on different servers / clusters. */
export function PlacementEditor() {
  const { draft, replace, issuesAt } = useStudio();
  const placement = readPlacement(draft);
  const { servers, clusters } = usePlacementTargets(true);
  const functions = placeableFunctions(draft);
  const path = placement?.path || placementPath(draft);
  // Validation reports the merged config: always provider.options.placement, even for a providers.onprem override.
  const issuePath = ["provider", "options", "placement"];
  const groupIssues = (name: string) => issuesAt([...issuePath, "groups", name]);
  const errors = issuesAt(issuePath)
    .filter((i) => !(placement?.groups || []).some((g) => groupIssues(g.name).includes(i)))
    .map((i) => i.message);

  const setMode = (split: boolean) => {
    if (split && !placement) {
      replace(writePlacement(draft, path, "main", [{ name: "main", body: {} }]));
    } else if (!split && placement) {
      replace(prune(setIn(draft, path, undefined) as JsonObject));
    }
  };

  const update = (def: string, groups: { name: string; body: JsonObject }[]) => replace(writePlacement(draft, path, def, groups));
  const groupsNow = () => (placement ? placement.groups.map((g) => ({ name: g.name, body: { ...g.body } })) : []);

  return (
    <div className="placement">
      <div className="row-gap wrap">
        <b>Where functions run</b>
        <Segmented
          label="Where functions run"
          value={placement ? "split" : "one"}
          onChange={(v) => setMode(v === "split")}
          options={[
            { value: "one", label: "All functions on one target" },
            { value: "split", label: "Split functions across servers" },
          ]}
        />
        <code className="mono small muted">provider.options.placement</code>
      </div>
      {!placement ? (
        <p className="muted small">
          Every function runs on the machine or cluster picked above. Split them to put, say, GPU functions on a GPU server and the API on another; each
          server then installs only what its functions need.
        </p>
      ) : (
        <>
          <p className="muted small">
            Every group is its own host with only its functions (and the requirements of their <code className="mono">functions/&lt;name&gt;/</code>{" "}
            folders). Functions call each other, queues, buckets, workflows and HTTP routes across servers automatically. Workflows run on the default group
            (their steps run where their functions are), and logs and invocation counts of every group show up on the default group. Functions not listed in
            any group run on the default group.
          </p>
          {errors.length > 0 && (
            <div className="hint-box warn">
              {errors.map((e, i) => (
                <div key={i}>{e}</div>
              ))}
            </div>
          )}
          <div className="placement-groups">
            {placement.groups.map((g) => (
              <GroupCard
                key={g.name}
                group={g}
                placement={placement}
                functions={functions}
                servers={servers}
                clusters={clusters}
                issues={groupIssues(g.name).map((i) => i.message)}
                onRename={(to) => {
                  const groups = groupsNow().map((x) => (x.name === g.name ? { ...x, name: to } : x));
                  update(placement.default === g.name ? to : placement.default, groups);
                }}
                onBody={(body) => update(placement.default, groupsNow().map((x) => (x.name === g.name ? { name: x.name, body } : x)))}
                onDefault={() => update(g.name, groupsNow())}
                onRemove={() => {
                  const groups = groupsNow().filter((x) => x.name !== g.name);
                  if (!groups.length) {
                    setMode(false);
                    return;
                  }
                  update(placement.default === g.name ? groups[0].name : placement.default, groups);
                }}
                onToggleFunction={(fn, on) => {
                  // A function lives in one group: checking it here takes it out of the others.
                  const groups = groupsNow().map((x) => {
                    const listed = asArray(x.body.functions).map((f) => str(f)).filter((f) => f !== fn);
                    if (on && x.name === g.name) listed.push(fn);
                    return { name: x.name, body: { ...x.body, functions: listed } };
                  });
                  update(placement.default, groups);
                }}
              />
            ))}
          </div>
          <div className="row-gap">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                const taken = placement.groups.map((g) => g.name);
                const name = nextGroupName(taken);
                const body: JsonObject = servers.length ? { server: servers[0].name } : { local: true };
                update(placement.default, [...groupsNow(), { name, body }]);
              }}
            >
              + Add group
            </button>
            <span className="muted small">Two groups on one server each need their own port.</span>
          </div>
        </>
      )}
    </div>
  );
}

function GroupCard({
  group,
  placement,
  functions,
  servers,
  clusters,
  issues,
  onRename,
  onBody,
  onDefault,
  onRemove,
  onToggleFunction,
}: {
  group: PlacementGroup;
  placement: Placement;
  functions: string[];
  servers: OnPremServer[];
  clusters: OnPremCluster[];
  issues: string[];
  onRename: (to: string) => void;
  onBody: (body: JsonObject) => void;
  onDefault: () => void;
  onRemove: () => void;
  onToggleFunction: (fn: string, on: boolean) => void;
}) {
  const isDefault = placement.default === group.name;
  const [name, setName] = useState(group.name);
  useEffect(() => setName(group.name), [group.name]);
  const taken = placement.groups.some((g) => g.name === name && g.name !== group.name);
  const nameError = !GROUP_RE.test(name) ? "a lowercase letter, then letters, digits or - (max 21)" : taken ? "another group has this name" : null;
  const commitName = () => {
    if (name !== group.name && !nameError) onRename(name);
  };
  const body = group.body;
  const target = targetValue(body);
  const setTarget = (v: string) => {
    const next: JsonObject = {};
    for (const [k, val] of Object.entries(body)) if (!(TARGET_KEYS as readonly string[]).includes(k)) next[k] = val;
    if (v === "local") next.local = true;
    else if (v.startsWith("server:")) next.server = v.slice(7);
    else if (v.startsWith("cluster:")) {
      const c = v.slice(8);
      // Keep {name, replicas} when the same cluster stays picked.
      next.cluster = isObject(body.cluster) && clusterName(body.cluster) === c ? body.cluster : c;
    }
    onBody(next);
  };
  const serverKnown = !body.server || servers.some((s) => s.name === str(body.server));
  const clusterKnown = !body.cluster || clusters.some((c) => c.name === clusterName(body.cluster));
  const implicit = isDefault ? functions.filter((f) => !(f in placement.listedIn)) : [];
  return (
    <div className={`placement-group card card-pad${isDefault ? " is-default" : ""}`}>
      <div className="row-gap wrap">
        <label className="row-gap placement-default" title="Functions not listed in any group, and every workflow, run on the default group">
          <input type="radio" name="placement-default" checked={isDefault} onChange={onDefault} />
          <span className="small">default</span>
        </label>
        <TextInput mono value={name} invalid={!!nameError} onChange={setName} onBlur={commitName} />
        {isDefault && <span className="badge badge-accent">default group</span>}
        <span className="grow" />
        <button type="button" className="btn btn-sm btn-danger" onClick={onRemove} title="Its functions move to the default group">
          Remove group
        </button>
      </div>
      {nameError && <div className="form-error">{nameError}</div>}
      <div className="form">
        <div className="form-grid">
          <FormRow
            label="Runs on"
            help={isDefault && !target ? "The machine or cluster picked above (provider.options.deploy, or --server)." : undefined}
            error={!isDefault && !target ? "pick a server, cluster or this machine" : null}
          >
            <select className="select" value={target} onChange={(e) => setTarget(e.target.value)} aria-label={`Where group ${group.name} runs`}>
              {(isDefault || !target) && <option value="">{isDefault ? "The deploy target above" : "Pick…"}</option>}
              <option value="local">This machine (local)</option>
              {servers.length > 0 && (
                <optgroup label="Saved servers">
                  {servers.map((s) => (
                    <option key={s.name} value={`server:${s.name}`}>
                      {s.name} ({s.label})
                    </option>
                  ))}
                </optgroup>
              )}
              {!serverKnown && <option value={target}>{str(body.server)} (not saved here)</option>}
              {clusters.length > 0 && (
                <optgroup label="Scaling clusters">
                  {clusters.map((c) => (
                    <option key={c.name} value={`cluster:${c.name}`}>
                      cluster {c.name} ({c.nodes.length} server{c.nodes.length === 1 ? "" : "s"})
                    </option>
                  ))}
                </optgroup>
              )}
              {!clusterKnown && <option value={target}>cluster {clusterName(body.cluster)} (not found)</option>}
            </select>
          </FormRow>
          <FormRow label="Port" help="Optional; needed when two groups share a server.">
            <NumberInput integer min={1} max={65535} value={typeof body.port === "number" ? body.port : undefined} onChange={(v) => onBody(withKey(body, "port", v))} />
          </FormRow>
          <FormRow label="Advertise URL" help="Optional: the address the other groups call, when it differs from the server's.">
            <TextInput mono value={str(body.advertise)} placeholder="http://10.0.0.9:8080" onChange={(v) => onBody(withKey(body, "advertise", v || undefined))} />
          </FormRow>
        </div>
        <FormRow label="Functions" help={isDefault ? "Plus every function not listed in another group." : "Checking a function moves it here from its group."}>
          {!functions.length ? (
            <span className="muted small">No functions yet.</span>
          ) : (
            <div className="placement-fns">
              {functions.map((fn) => {
                const where = placement.listedIn[fn];
                const here = where === group.name;
                const elsewhere = where && !here ? where : "";
                const viaDefault = isDefault && !where;
                return (
                  <label key={fn} className={`placement-fn${here || viaDefault ? " on" : ""}`} title={elsewhere ? `In group ${elsewhere}` : viaDefault ? "Not listed: runs on the default group" : undefined}>
                    <input type="checkbox" checked={here || viaDefault} disabled={viaDefault} onChange={(e) => onToggleFunction(fn, e.target.checked)} />
                    <span className="mono">{fn}</span>
                    {elsewhere && <span className="muted small">· {elsewhere}</span>}
                  </label>
                );
              })}
            </div>
          )}
          {isDefault && implicit.length > 0 && <div className="form-help">Not listed anywhere (run here): {implicit.join(", ")}</div>}
        </FormRow>
        {issues.map((e, i) => (
          <div key={i} className="form-error">
            {e}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Small badge: the group (and its server) a function runs on. */
export function GroupBadge({ placement, fn }: { placement: Placement | null; fn: string }) {
  if (!placement) return null;
  const group = groupOf(placement, fn);
  const body = placement.groups.find((g) => g.name === group)?.body || {};
  const where = targetLabel(body);
  return (
    <span className="badge badge-info" title={`Placement group ${group}${where ? ` on ${where}` : ""}`}>
      {group}
      {where ? ` · ${where}` : ""}
    </span>
  );
}

/** "Runs on" select: the placement group a function runs in. */
export function RunsOnSelect({ placement, value, onChange, id }: { placement: Placement; value: string; onChange: (group: string) => void; id?: string }) {
  return (
    <select id={id} className="select" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Runs on">
      {placement.groups.map((g) => {
        const where = targetLabel(g.body);
        return (
          <option key={g.name} value={g.name}>
            {g.name}
            {where ? ` (${where})` : ""}
            {g.name === placement.default ? " · default" : ""}
          </option>
        );
      })}
    </select>
  );
}

/** After a deploy: each placement group's host and the load balancer URLs (deployment.json `groups` / `balancers`). */
export function DeployedGroups({ deployed }: { deployed: OnPremDeployed | null }) {
  const groups = Object.entries(deployed?.groups || {});
  const balancers = deployed?.balancers || [];
  if (!groups.length && !balancers.length) return null;
  return (
    <div className="deployed-groups">
      {groups.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Group</th>
                <th>Where</th>
                <th>Endpoint</th>
                <th>Functions</th>
              </tr>
            </thead>
            <tbody>
              {groups.map(([name, g]) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td className="small">{g.cluster ? `cluster ${g.cluster}` : g.server || (g.deploy === "local" || !g.deploy ? "this machine" : g.deploy)}</td>
                  <td className="mono small">
                    {g.endpoint ? (
                      <a href={g.endpoint} target="_blank" rel="noreferrer">
                        {g.endpoint}
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <span className="chips">
                      {(g.functions || []).map((f) => (
                        <span key={f} className="chip">
                          {f}
                        </span>
                      ))}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {balancers.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Load balancer</th>
                <th>URL</th>
              </tr>
            </thead>
            <tbody>
              {balancers.map((b) => (
                <tr key={`${b.name}:${b.key}`}>
                  <td className="mono">{b.name}</td>
                  <td className="mono small">
                    <a href={b.url} target="_blank" rel="noreferrer">
                      {b.url}
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
