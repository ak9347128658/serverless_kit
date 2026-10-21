/** Shapes of the pskit studio API (see the contract in the Python studio server). */

/** A JSON value as found in the YAML document. */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };

export type FieldType = "string" | "int" | "number" | "bool" | "enum" | "list" | "map" | "secret" | "json" | "code";

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
  default_runtime?: string;
  max_timeout: number;
  notes?: string;
  options: Field[];
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
}
