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
}

/** Where an on-prem stage was last deployed (`GET /api/onprem-servers?stage=`). */
export interface OnPremDeployed {
  server: string | null;
  method: string;
  host: string | null;
  label: string;
  endpoint?: string | null;
  deployed_at?: string | null;
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
  method: "local" | "ssh";
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
}

export interface OnPremServicesState {
  catalog: OnPremCatalogItem[];
  services: OnPremService[];
  default_target: OnPremTarget;
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

/** A scaling cluster (`GET /api/onprem-clusters`): Docker Swarm, Kubernetes (k3s) or Nomad on saved servers. */
export type Orchestrator = "swarm" | "kubernetes" | "nomad";

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
}

export interface OnPremCluster {
  name: string;
  orchestrator: Orchestrator;
  orchestrator_label: string;
  master: string;
  advertise: string;
  nodes: ClusterNode[];
  capacity: { cpu: number; memory: number; disk: number };
  alert: { function: string; stage: string; webhook: string; cooldown_minutes: number };
  watch: { enabled: boolean; interval_seconds: number };
  alerts: ClusterAlertEntry[];
  deployments: Record<string, ClusterDeployment>;
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
}

export interface ClusterStatus {
  name: string;
  orchestrator: Orchestrator;
  capacity: { cpu: number; memory: number; disk: number };
  nodes: ClusterNodeStatus[];
  workloads: { name: string; running: number; desired: number }[];
  pending: string[];
  full: boolean;
  reasons: string[];
  orchestrator_error?: string | null;
  checked_at: string;
  alert?: ClusterAlertEntry | { skipped: string; next_after_minutes: number };
  error?: string;
}

export interface OnPremClustersState {
  clusters: OnPremCluster[];
  servers: OnPremServer[];
  orchestrators: { id: Orchestrator; label: string; summary: string; ports: string }[];
  last: Record<string, ClusterStatus>;
}
