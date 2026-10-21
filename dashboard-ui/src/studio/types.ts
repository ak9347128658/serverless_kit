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

export type Capability = "http" | "schedule" | "queue" | "storage" | "topic" | "workflow_native";
export type EventType = "http" | "schedule" | "queue" | "storage" | "topic";
export const EVENT_TYPES: EventType[] = ["http", "schedule", "queue", "storage", "topic"];

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
