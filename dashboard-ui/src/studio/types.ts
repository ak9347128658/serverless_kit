/** Shapes of the pskit studio API (see the contract in the Python studio server). */

/** A JSON value as found in the YAML document. */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };

export type FieldType = "string" | "int" | "number" | "bool" | "enum" | "list" | "map" | "secret" | "json" | "code" | "file";

export interface Field {
  key: string;
  label: string;
  type: FieldType;
  required?: boolean;
  default?: Json;
  options?: Json[];
  help?: string;
  placeholder?: string;
  min?: number;
  max?: number;
  /** May be given as `<key>_env: VAR` instead of a literal value. */
  env_ok?: boolean;
  /** Offered in a dropdown; another value can still be typed. */
  suggestions?: Json[];
}

export interface Driver {
  name: string;
  label: string;
  cloud: string;
  fields: Field[];
  notes?: string;
}

export type Capability = "http" | "schedule" | "queue" | "storage" | "topic" | "smtp" | "workflow_native";
export type EventType = "http" | "schedule" | "queue" | "storage" | "topic" | "smtp";
export const EVENT_TYPES: EventType[] = ["http", "schedule", "queue", "storage", "topic", "smtp"];

export interface ProviderMeta {
  name: string;
  display_name: string;
  tier: string;
  default_region: string;
  capabilities: string[];
  runtimes: string[];
  /** Node.js runtimes (nodejs18 … nodejs24) this provider runs; empty = Python only. */
  node_runtimes?: string[];
  default_runtime?: string;
  max_timeout: number;
  /** A storage event may name a storage resource (the service) and another bucket on it. */
  storage_service_buckets?: boolean;
  notes?: string;
  options: Field[];
  regions?: { id: string; label: string }[];
  /** Trigger options this provider rejects (error) or ignores (warning), per event type. */
  trigger_support?: Partial<Record<EventType, TriggerRule[]>>;
}

export interface TriggerRule {
  key: string;
  value: Json;
  level: "error" | "warning";
  message: string;
}

export interface Meta {
  version: string;
  providers: ProviderMeta[];
  events: Partial<Record<EventType, Field[]>>;
  function_fields?: Field[];
  resource_types: string[];
  http_methods: string[];
  auth_modes?: string[];
  workflow: { state_types: string[]; choice_operators: string[]; engines: string[] };
  onprem: {
    deploy_methods: string[];
    deploy_fields: Record<string, Field[]>;
    tuning: Field[];
    services?: string[];
    drivers: { queue: Driver[]; storage: Driver[]; topic: Driver[] };
  };
  handler_templates?: Record<string, string>;
  node_handler_templates?: Record<string, string>;
}

export interface Issue {
  level: "error" | "warning";
  message: string;
  where?: string;
}

export interface ProjectState {
  exists: boolean;
  path?: string;
  raw: JsonObject | null;
  text?: string;
  mtime?: number | null;
  issues?: Issue[];
  notices?: string[];
}

/** `POST /api/functions/<name>/split`: the plan (`dry_run`) or what was done; `project` is the project after it. */
export interface FunctionSplitResult {
  function: string;
  /** Code file the handler was in, e.g. app/handlers.py. */
  from: string;
  /** Its new file, e.g. functions/get_item/handler.py. */
  to: string;
  /** The new handler, e.g. functions.get_item.handler.get_item. */
  handler: string;
  /** Lines of the project's requirements.txt the function's imports name (its own requirements.txt). */
  requirements: string[];
  /** Imports not matched to a requirement (maybe the standard library). */
  unknown_imports: string[];
  /** Module-level helpers copied along. */
  helpers: string[];
  /** The function stays in the source file too: code there still calls it. */
  kept_in_source: boolean;
  /** Human-readable summary. */
  lines: string[];
  project: ProjectState;
}

export interface ValidateResult {
  ok: boolean;
  issues: Issue[];
  yaml?: string;
  functions?: { name: string; generated?: boolean; kind?: string }[];
  engines?: Record<string, string>;
  disabled?: { functions: string[]; workflows: string[] };
}

export interface FileEntry {
  path: string;
  type: "file" | "dir";
  size?: number;
}

export interface FileContent {
  path: string;
  text: string;
  mtime?: number | null;
  language?: string;
}

export interface DiscoverItem {
  name: string;
  id?: string;
  arn?: string;
  region?: string;
  detail?: string;
}

export interface DiscoverResult {
  supported: boolean;
  reason?: string;
  error?: string;
  items: DiscoverItem[];
}

export type JobStatus = "running" | "succeeded" | "failed" | "cancelled";

export interface JobLine {
  seq: number;
  text: string;
  stream?: string;
}

export interface Job {
  job_id: string;
  command: string;
  label?: string;
  status: JobStatus;
  exit_code?: number | null;
  lines: JobLine[];
  next: number;
  started_at?: string;
}

export interface VsCodeState {
  docker: { available: boolean; version?: string; error?: string };
  image: string;
  image_present?: boolean;
  running: boolean;
  url?: string | null;
  container?: string | null;
  log_job?: string | null;
  python?: string | null;
  node?: string | null;
  target_image?: string;
  running_image?: string | null;
  python_versions?: string[];
  node_versions?: string[];
}

export interface FsEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number | null;
  /** Name looks like a private key (id_*, *.pem, *.key, *.ppk). */
  key?: boolean;
}

export interface FsListing {
  path: string;
  parent: string | null;
  home: string;
  roots: string[];
  entries: FsEntry[];
  truncated?: boolean;
}

export type CheckStatus = "ok" | "missing" | "warning" | "info";

export interface SetupCheck {
  id: string;
  title: string;
  status: CheckStatus;
  detail: string;
  fix?: {
    summary?: string;
    links?: { label: string; url: string }[];
    steps?: { windows?: string[]; macos?: string[]; linux?: string[] };
    commands?: string[];
    action?: string | null;
  } | null;
}

export interface EnvHint {
  name: string;
  help?: string;
  secret?: boolean;
  set?: boolean;
  value?: string | null;
}

export interface SetupResult {
  provider: string;
  ok: boolean;
  checks: SetupCheck[];
  env?: EnvHint[];
  platform?: "windows" | "macos" | "linux";
}

export interface DevEnvStatus {
  venv: { path: string; exists: boolean; python: string | null };
  requirements: { path: string; exists: boolean };
  package_json: boolean;
  node_modules: boolean;
  node: { path: string | null; version: string | null; npm: string | null };
  interpreters: { path: string; version: string }[];
}

export interface CloudResourceRow {
  address: string;
  type: string;
  kind: string;
  name: string;
  id: string;
  status: "deployed" | "kept";
}

export interface TestTrigger {
  index: number;
  type: "http" | "schedule" | "queue" | "storage" | "topic" | "smtp" | "invoke";
  summary: string;
  method?: string;
  path?: string;
  name?: string;
  sample: Json;
  payload_help: string;
  local: string[];
  remote: { supported: boolean; reason: string; pskit: string[]; cli: string[]; console: string[] };
}

export interface TestGuide {
  provider: string;
  stage: string;
  region: string;
  deployed: boolean;
  sdk?: string | null;
  functions: { name: string; description: string; triggers: TestTrigger[] }[];
}

export interface SavedTestEvent {
  name: string;
  event: number;
  payload: Json;
  path?: string;
}

export interface ResourceTestItem {
  key: string;
  type: string;
  name: string;
  external: boolean;
  functions: string[];
  connection_keys: string[];
  env: string[];
  snippets: { python: string; node: string };
  sendable: boolean;
  action?: string | null;
  sample?: Json | null;
  payload_help: string;
  remote: { supported: boolean; reason: string; pskit: string[]; cli: string[]; console: string[] };
  code?: { flavor: string; python: string; node: string; requirements: { python: string[]; node: string[] }; note: string };
  service?: string | null;
  check?: { supported: boolean; where: string; command: string };
}

export interface ResourceCheck {
  key: string;
  type: string;
  name: string;
  ok: boolean;
  detail: string;
  ms: number;
  checked_from: string;
}

export interface ResourceTestGuide {
  provider: string;
  stage: string;
  deployed: boolean;
  resources: ResourceTestItem[];
}

// ------------------------------------------------------------------ on-prem services
export interface OnPremCatalogItem {
  id: string;
  label: string;
  kind: "service" | "runtime";
  image?: string;
  port?: number;
  extra_ports?: number[];
  roles: string[];
  backup?: boolean;
  persistent?: boolean;
  auth?: boolean;
  default_user?: string;
  gui?: string[];
  companion?: string | null;
  gui_logins?: string[];
  notes: string;
  group?: string;
  versions?: string[];
  /** pskit's own UIs for the service: s3, gcs, vault, aws-secrets. */
  browsers?: string[];
  /** Set for databases that can run as a primary with read replicas (null: single server only). */
  replication?: OnPremReplicationInfo | null;
  /** Dump files a new database can start from (.sql, .dump, .archive ...); empty when import is not offered. */
  import_types?: string[];
}

/** Alert settings of a service (thresholds in percent / seconds). */
export interface OnPremAlert {
  enabled: boolean;
  function: string;
  stage: string;
  webhook: string;
  cooldown_minutes: number;
  interval_seconds: number;
  cpu: number;
  memory: number;
  disk: number;
  lag_seconds: number;
  down: boolean;
}

export interface OnPremAlertRecord {
  at: string;
  test: boolean;
  message: string;
  sent: string[];
  errors: string[];
}

export interface OnPremMonitorContainer {
  name: string;
  role: "primary" | "replica" | "balancer" | "server";
  server: string;
  container: string;
  state: string;
  cpu?: number | null;
  memory?: number | null;
  memory_usage?: string;
  net_io?: string;
  block_io?: string;
  disk?: number | null;
  disk_total_gb?: number;
}

export interface OnPremMonitorSample {
  key: string;
  service: string;
  at: string;
  containers: OnPremMonitorContainer[];
  database: { connections?: number; size_mb?: number };
  /** Replica name -> seconds behind the primary (null: replication stopped). */
  lag: Record<string, number | null>;
}

export interface ServerMetrics {
  reachable: boolean;
  error?: string;
  cpu?: number | null;
  memory?: number | null;
  memory_mb?: number;
  disk?: number | null;
  disk_gb?: number;
  cpus?: number | null;
  load?: string;
  hostname?: string;
  at?: string;
}

export interface OnPremReplicationInfo {
  /** "Streaming replication (WAL)", "GTID replication (binary log)", "Replica set". */
  method: string;
  /** pgpool-II, ProxySQL, or "" when the driver routes (MongoDB). */
  balancer: string;
  balancer_port: number;
  /** What async and sync mean for this engine. */
  async: string;
  sync: string;
  /** How reads and writes are routed. */
  routing: string;
  /** Every member can run on this machine to try it out. */
  local: boolean;
}

export interface OnPremReplicaMember {
  name: string;
  role: "primary" | "replica" | "balancer";
  /** Saved server name; "" on this machine. */
  server: string;
  address: string;
  port: number;
  container: string;
  // status only
  state?: string;
  replication?: string;
}

export interface OnPremTopology {
  mode: "replicated";
  engine: string;
  sync: "async" | "sync";
  local: boolean;
  replica_set?: string;
  members: OnPremReplicaMember[];
  balancer: (OnPremReplicaMember & { kind: string }) | null;
  // status only: what the primary reports about its replicas
  report?: string;
}

export interface OnPremConsoleInfo {
  service: string;
  available: boolean;
  label: string;
  input: string;
  examples: string[];
  shell: boolean;
  note: string;
}

export interface OnPremConsoleResult {
  output: string;
  exit_code: number;
  command: string;
}

export interface OnPremServer {
  name: string;
  label: string;
  host: string;
  user?: string;
  port?: number;
  auth?: "key" | "agent" | "password";
  identity_file?: string;
  password_file?: string;
  sudo?: boolean;
  description?: string;
  /** Where pskit keeps files on the server (functions in <home>/<service>-<stage>, cluster files in <home>/pskit-cluster). */
  home?: string;
  /** Address the functions' host and services listen on; a specific IP is also its cluster address. */
  bind?: string;
  /** Where data lives: services' data as bind mounts, the functions' data_dir, a cluster master's main-replica data. */
  data_dir?: string;
}

/** Where an on-prem stage was last deployed (`GET /api/onprem-servers?stage=`). */
export interface OnPremDeployed {
  server: string | null;
  /** A scaling cluster deploy: its name (server is then the master). */
  cluster?: string | null;
  method: string;
  host: string | null;
  label: string;
  endpoint?: string | null;
  deployed_at?: string | null;
  /** provider.options.placement: where each group of functions runs (deployment.json `groups`). */
  groups?: Record<string, DeployedGroup> | null;
  /** Load balancer routes of this deployment (deployment.json `balancers`, from provider.options.balancer). */
  balancers?: DeployedBalancerRoute[] | null;
}

/** One placement group of a deployed on-prem stage. */
export interface DeployedGroup {
  endpoint: string;
  /** Deploy method: local, ssh, scp or docker. */
  deploy?: string | null;
  functions: string[];
  server?: string | null;
  cluster?: string | null;
}

export interface DeployedBalancerRoute {
  name: string;
  key: string;
  path?: string | null;
  url: string;
  owner?: string;
}

export interface OnPremServerTest {
  name: string;
  ok: boolean;
  error?: string;
  facts?: Record<string, string>;
}

export interface OnPremTerminal {
  name: string;
  port: number;
  title: string;
  url?: string;
  status?: string;
}

export interface ResourcePage {
  key: string;
  type: string;
  name: string;
  offset: number;
  limit: number;
  supported: boolean;
  total: number | null;
  note?: string;
  items: {
    id?: string;
    key?: string;
    size?: number;
    body?: string;
    truncated?: boolean;
    attributes?: Record<string, unknown>;
    receives?: number | null;
    enqueued_at?: number | null;
    leased?: boolean;
    delivered_to?: string[];
  }[];
}

export interface OnPremTarget {
  /** Absent for a cluster target ({cluster, server?}). */
  method?: "local" | "ssh";
  /** A scaling cluster: the resource runs on one of its servers (`server`, or the one with the most free memory). */
  cluster?: string;
  host?: string;
  user?: string;
  port?: number;
  identity_file?: string;
  sudo?: boolean;
  /** key: the .pem below; agent: the SSH agent / default key; password: sshpass (studio saves it in .pskit/ssh). */
  auth?: "key" | "agent" | "password";
  password?: string;
  password_file?: string;
  /** A saved SSH server (Servers): its connection is used. */
  server?: string;
}

export interface OnPremService {
  key: string;
  service: string;
  kind: "service" | "runtime";
  role?: string;
  target: OnPremTarget;
  target_label: string;
  image?: string;
  container?: string;
  volume?: string;
  port?: number;
  address?: string;
  attach?: string[];
  env_file?: string;
  env?: Record<string, string>;
  resource?: JsonObject;
  notes?: string[];
  created_at?: string;
  backups?: { path: string; size: number; at: string }[];
  // status only
  state?: string;
  health?: string | null;
  ready?: boolean;
  started_at?: string;
  error?: string;
  version?: string[];
  reachable_from_here?: boolean;
  gui?: { label: string; url: string; port: number; login: string; kind: string; container?: string }[] | Record<string, unknown>;
  companion?: string | null;
  gui_logins?: string[];
  /** A replicated database: primary, read replicas and load balancer. */
  topology?: OnPremTopology;
}

export interface OnPremServicesState {
  catalog: OnPremCatalogItem[];
  services: OnPremService[];
  default_target: OnPremTarget;
  /** Set when pskit.yml names a saved server that no longer exists. */
  default_target_problem?: string;
  env_file: string;
}

export interface GuideIndex {
  available: boolean;
  clouds: { id: string; title: string; pages: { id: string; title: string }[] }[];
}

export interface GuidePage {
  cloud: string;
  page: string;
  title: string;
  markdown: string;
}

/** What a `.pskit-settings` file holds (names only, never secrets). */
export interface SettingsSummary {
  created_at?: string;
  pskit_version?: string;
  source_root?: string;
  files: string[];
  /** SSH key files from outside the project, by their path on the exporting machine. */
  keys: string[];
  warnings: string[];
  /** `<store>.<group>` (e.g. `onprem-servers.servers`) -> entry names. */
  entries?: Record<string, string[]>;
}

/** `POST /api/settings-bundle/preview`: what an import would change. */
export interface SettingsPreview extends SettingsSummary {
  mode: "merge" | "replace";
  changes: { path: string; action: "add" | "replace" }[];
  stores: Record<string, { add: string[]; replace: string[]; keep?: string[]; remove?: string[] }>;
  key_files: { source: string; path: string }[];
}

export interface SettingsImportResult extends SettingsPreview {
  written: string[];
  backup: string | null;
}

/** A scaling cluster (`GET /api/onprem-clusters`): Docker Swarm, Kubernetes (k3s) or Nomad on saved servers. */
export type Orchestrator = "swarm" | "kubernetes" | "nomad";

/** `POST /api/onprem-clusters/<name>/<action>`: each one runs as a job. */
export type ClusterAction = "join" | "leave" | "scale" | "remove" | "balancer-set" | "balancer-remove" | "balancer-sync" | "master-serves";

export interface ClusterNode {
  server: string;
  role: "master" | "worker";
  node: string;
  address: string;
  joined_at?: string;
}

export interface ClusterAlertEntry {
  at: string;
  test: boolean;
  message: string;
  sent: string[];
  errors: string[];
}

export interface ClusterDeployment {
  service: string;
  stage: string;
  image: string;
  replicas: number;
  port: number;
  endpoint: string;
  deployed_at: string;
  /** Its own port on the load balancer (`options.cluster.balancer_port`); else the load balancer's port. */
  balancer_port?: number;
}

/** nginx on a saved server outside the cluster, spreading each deployment's HTTP over the cluster's servers. */
export interface ClusterBalancer {
  server: string;
  address: string;
  port: number;
  set_at?: string;
  /** Each deployment's URL on the load balancer. */
  urls: Record<string, string>;
  /** One of `OnPremBalancersState.algorithms` (missing on load balancers set up before 2.3: the default). */
  algorithm?: string;
  /** Saved server -> weight (1..100) for weighted algorithms. */
  weights?: Record<string, number>;
}

export interface ClusterBalancerStatus extends ClusterBalancer {
  reachable: boolean;
  error?: string;
  cpu?: number | null;
  memory?: number | null;
  disk?: number | null;
  disk_gb?: number;
  load?: string;
  /** The `pskit-lb` container: running, exited, missing, unknown. */
  state: string;
  running: boolean;
}

export interface OnPremCluster {
  name: string;
  orchestrator: Orchestrator;
  orchestrator_label: string;
  master: string;
  /** The master takes requests next to the other servers; false: it is only a backup while they are down. */
  master_serves: boolean;
  advertise: string;
  nodes: ClusterNode[];
  capacity: { cpu: number; memory: number; disk: number };
  alert: { function: string; stage: string; webhook: string; cooldown_minutes: number };
  watch: { enabled: boolean; interval_seconds: number };
  alerts: ClusterAlertEntry[];
  deployments: Record<string, ClusterDeployment>;
  balancer?: ClusterBalancer | null;
  /** Named load balancers attached to this cluster, with each deployment's URL there. */
  balancers?: (NamedBalancer & { urls: Record<string, string> })[];
  created_at?: string;
}

export interface ClusterNodeStatus {
  server: string;
  role: string;
  node: string;
  address: string;
  reachable: boolean;
  error?: string;
  hostname?: string;
  cpu?: number | null;
  memory?: number | null;
  disk?: number | null;
  memory_mb?: number;
  disk_gb?: number;
  cpus?: number | null;
  load?: string;
  ready: boolean;
  state: string;
  availability: string;
  over: string[];
  full: boolean;
  /** Keys of the on-prem services running on this server. */
  resources?: string[];
}

export interface ClusterResource {
  key: string;
  service: string;
  kind: string;
  server: string;
  address?: string;
  port?: number;
}

export interface ClusterStatus {
  name: string;
  orchestrator: Orchestrator;
  capacity: { cpu: number; memory: number; disk: number };
  nodes: ClusterNodeStatus[];
  balancer?: ClusterBalancerStatus | null;
  workloads: { name: string; running: number; desired: number }[];
  resources?: ClusterResource[];
  pending: string[];
  full: boolean;
  reasons: string[];
  orchestrator_error?: string | null;
  checked_at: string;
  alert?: ClusterAlertEntry | { skipped: string; next_after_minutes: number };
  error?: string;
}

/** One request (or other event) a replica handled: `GET /api/onprem-clusters/<name>/requests`. */
export interface ClusterRequest {
  seq: number;
  ts: string;
  function: string | null;
  level: string;
  request_id?: string | null;
  event_type?: string | null;
  method?: string | null;
  path?: string | null;
  status?: number | null;
  duration_ms?: number | null;
  error?: string | null;
  replica?: string | null;
  node?: string | null;
  /** The saved server that ran it (from the node). */
  server: string;
}

/** One server of a deployment: where its port answers, and how many HTTP requests this page counted. */
export interface ClusterBalanceServer {
  server: string;
  role: string;
  address: string;
  url: string;
  /** `web` on Swarm/k3s (any node) and on Nomad workers; `main` on the Nomad master. */
  serves: "web" | "main" | string;
  requests: number;
  share: number;
  /** With a load balancer: `primary` (shares requests) or `backup` (only while every primary is down). */
  balancer?: "primary" | "backup" | "";
}

/** How HTTP for one deployment is spread across the cluster. Servers with zero requests are included. */
export interface ClusterBalance {
  orchestrator: string;
  mode: "mesh" | "per-server" | "balancer";
  spreads: boolean;
  note: string;
  port: number;
  servers: ClusterBalanceServer[];
  balancer_url?: string;
}

export interface ClusterRequests {
  stack: string;
  stacks: string[];
  items: ClusterRequest[];
  next: number;
  servers: Record<string, number>;
  balance?: ClusterBalance;
}

export interface OnPremClustersState {
  clusters: OnPremCluster[];
  servers: OnPremServer[];
  orchestrators: { id: Orchestrator; label: string; summary: string; ports: string }[];
  last: Record<string, ClusterStatus>;
}

/** How a load balancer picks a server: static = a fixed rule, dynamic = from live load per request. */
export type BalancerKind = "static" | "dynamic";

/** One load balancing algorithm (`GET /api/onprem-balancers` → `algorithms`), in display order. */
export interface LbAlgorithm {
  name: string;
  label: string;
  kind: BalancerKind;
  summary: string;
  pros: string[];
  cons: string[];
  best_for: string;
}

/** A named load balancer (OpenResty on a saved server) shared by projects and clusters by path. */
export interface NamedBalancer {
  name: string;
  server: string;
  address: string;
  port: number;
  algorithm: string;
  algorithm_label: string;
  kind: BalancerKind;
  url: string;
  weights: Record<string, number>;
  clusters: string[];
  created_at?: string | null;
}

export interface BalancerBackend {
  address: string;
  port: number;
  weight: number;
  backup: boolean;
  server: string;
}

/** One route on a load balancer: `{url}/<path>/` (or its own port) to a deployment's servers. */
export interface BalancerRoute {
  key: string;
  path?: string;
  listen?: number;
  owner: string;
  /** `project` or `cluster:<name>`. */
  source: string;
  service: string;
  stage: string;
  algorithm: string | null;
  weights: Record<string, number>;
  backends: BalancerBackend[];
  updated_at?: string;
  updated_from?: string;
  url: string;
  effective_algorithm: string;
  /** No algorithm of its own: uses the load balancer's. */
  inherits: boolean;
  /** Owned by a deployment of this project (else another project's route on the same server). */
  mine: boolean;
}

export interface NamedBalancerStatus extends NamedBalancer {
  reachable?: boolean;
  error?: string;
  cpu?: number | null;
  memory?: number | null;
  disk?: number | null;
  disk_gb?: number;
  load?: string;
  /** The OpenResty container: running, exited, missing, unknown. */
  state: string;
  running: boolean;
  routes: BalancerRoute[];
  server_settings?: JsonObject;
  /** The server runs other settings (port / algorithm) than the saved ones: Sync rewrites them. */
  drift?: boolean;
}

export interface OnPremBalancersState {
  balancers: NamedBalancer[];
  algorithms: LbAlgorithm[];
  default_algorithm: string;
  servers: OnPremServer[];
  clusters: { name: string; nodes: string[]; balancer: string | null }[];
}

/** `POST /api/onprem-balancers/<name>/<action>`: each one runs as a job. */
export type BalancerAction = "update" | "attach" | "detach" | "sync" | "remove" | "route-remove";

/** One machine a firewall rule allows traffic from / to. `name` may be `users`, `internet` or `admin` (the machine running pskit). */
export interface FirewallSource {
  name: string;
  address: string;
}

/** `required`: it does not work without it; `feature`: needed for that feature; `optional`: pskit falls back to an SSH tunnel. */
export type FirewallLevel = "required" | "feature" | "optional";

export interface FirewallRule {
  server: string;
  address: string;
  direction: "inbound" | "outbound";
  proto: "tcp" | "udp";
  /** One port or several, e.g. `8080` or `443, 80`. */
  port: string;
  sources: FirewallSource[];
  why: string;
  level: FirewallLevel;
}

export interface FirewallServer {
  server: string;
  address: string;
  rules: FirewallRule[];
  /** Ready-to-run `ufw` commands for this server. */
  ufw: string[];
}

/** `GET /api/onprem-firewall` */
export interface OnPremFirewall {
  servers: FirewallServer[];
}
