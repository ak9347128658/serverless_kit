import type { LogsResponse, LogStream } from "../types";
import type {
  DiscoverResult,
  FileContent,
  FileEntry,
  FsListing,
  Job,
  JsonObject,
  Meta,
  ProjectState,
  SetupResult,
  SavedTestEvent,
  TestGuide,
  CloudResourceRow,
  DevEnvStatus,
  ValidateResult,
  VsCodeState,
} from "./types";

const TOKEN_KEY = "pskit-studio-token";

export class ApiError extends Error {
  status: number;
  code?: string;
  body: Record<string, unknown>;

  constructor(status: number, message: string, body: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = typeof body.code === "string" ? body.code : undefined;
    this.body = body;
  }
}

function readSession(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* private mode: the token stays in memory for this page */
  }
}

let memoryToken: string | null = null;

/** Take `#token=...` from the URL (once), keep it in sessionStorage, and remove it from the address bar. */
export function captureToken(): string | null {
  const match = /(?:^#|[#&])token=([A-Za-z0-9_\-.~%]+)/.exec(window.location.hash);
  if (match) {
    memoryToken = decodeURIComponent(match[1]);
    writeSession(TOKEN_KEY, memoryToken);
    // Keep any route in the hash (e.g. `#/functions&token=...`) so links can point at a page.
    let rest = window.location.hash.replace(/[#&]token=[^&]*/, "").replace(/^#?&/, "#");
    if (!rest || rest === "#") rest = "#/";
    const url = window.location.pathname + window.location.search + rest;
    window.history.replaceState(null, "", url);
  }
  return token();
}

export function token(): string | null {
  return memoryToken || readSession(TOKEN_KEY);
}

export const MOCK = import.meta.env.VITE_MOCK === "1";

type Method = "GET" | "POST" | "PUT" | "DELETE";

export interface RequestOptions {
  signal?: AbortSignal;
  query?: Record<string, string | number | undefined | null>;
}

async function request<T>(method: Method, path: string, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  if (MOCK) {
    const { mockRequest } = await import("./mock");
    return mockRequest<T>(method, path, body, opts.query);
  }
  let url = path;
  if (opts.query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query)) {
      if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
    }
    const qs = params.toString();
    if (qs) url += "?" + qs;
  }
  const headers: Record<string, string> = { accept: "application/json" };
  const t = token();
  if (t) headers["x-studio-token"] = t;
  if (body !== undefined) headers["content-type"] = "application/json";
  let resp: Response;
  try {
    resp = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: opts.signal,
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch (exc) {
    if (exc instanceof DOMException && exc.name === "AbortError") throw exc;
    throw new ApiError(0, "pskit studio is not reachable. Is `pskit studio` still running?");
  }
  const text = await resp.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text.slice(0, 300) };
    }
  }
  if (!resp.ok) {
    const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
    const msg = typeof obj.error === "string" ? obj.error : `HTTP ${resp.status}`;
    throw new ApiError(resp.status, msg, obj);
  }
  return data as T;
}

export const api = {
  health: () => request<{ ok: boolean; version: string; root: string }>("GET", "/api/health"),
  meta: () => request<Meta>("GET", "/api/meta"),
  project: () => request<ProjectState>("GET", "/api/project"),
  validate: (raw: JsonObject, provider?: string, stage?: string, signal?: AbortSignal) =>
    request<ValidateResult>("POST", "/api/project/validate", { raw, provider, stage }, { signal }),
  parse: (text: string) => request<{ raw: JsonObject | null; issues: ValidateResult["issues"] }>("POST", "/api/project/parse", { text }),
  save: (raw: JsonObject, mtime?: number | null, force?: boolean) =>
    request<ProjectState>("PUT", "/api/project", { raw, mtime, force }),
  init: (service: string, provider: string, template: string, force?: boolean, language?: string) =>
    request<ProjectState>("POST", "/api/project/init", { service, provider, template, force, language }),
  tree: () => request<{ entries: FileEntry[] }>("GET", "/api/files/tree"),
  file: (path: string) => request<FileContent>("GET", "/api/files", undefined, { query: { path } }),
  writeFile: (path: string, text: string, mtime?: number | null) =>
    request<{ path: string; mtime: number }>("PUT", "/api/files", { path, text, mtime }),
  ensureHandler: (handler: string, events: string[]) =>
    request<{ path: string; created: boolean; line?: number }>("POST", "/api/handlers/ensure", { handler, events }),
  discover: (provider: string, kind: string, region?: string) =>
    request<DiscoverResult>("GET", "/api/discover", undefined, { query: { provider, kind, region } }),
  startJob: (command: string, provider?: string, stage?: string, args?: Record<string, unknown>) =>
    request<{ job_id: string }>("POST", "/api/jobs", { command, provider, stage, args }),
  job: (id: string, since: number) =>
    request<Job>("GET", `/api/jobs/${encodeURIComponent(id)}`, undefined, { query: { since } }),
  jobs: () => request<{ jobs: Job[] }>("GET", "/api/jobs"),
  cancelJob: (id: string) => request<Job>("POST", `/api/jobs/${encodeURIComponent(id)}/cancel`, {}),
  clearJobs: () => request<{ removed: number }>("DELETE", "/api/jobs"),
  deleteJob: (id: string) => request<{ removed: number }>("DELETE", `/api/jobs/${encodeURIComponent(id)}`),
  cloudResources: (provider: string, stage: string) =>
    request<{ deployed: CloudResourceRow[]; kept: CloudResourceRow[]; reason?: string }>("GET", "/api/cloud-resources", undefined, {
      query: { provider, stage },
    }),
  testGuide: (provider: string, stage: string) => request<TestGuide>("GET", "/api/test-guide", undefined, { query: { provider, stage } }),
  testEvents: () => request<Record<string, SavedTestEvent[]>>("GET", "/api/test-events"),
  saveTestEvents: (events: Record<string, SavedTestEvent[]>) => request<Record<string, SavedTestEvent[]>>("PUT", "/api/test-events", { events }),
  browse: (path?: string) => request<FsListing>("GET", "/api/fs", undefined, { query: { path } }),
  setup: (provider: string) => request<SetupResult>("GET", "/api/setup", undefined, { query: { provider } }),
  setEnv: (name: string, value: string) => request<{ name: string; set: boolean }>("POST", "/api/setup/env", { name, value }),
  awsProfile: (body: { profile: string; access_key_id: string; secret_access_key: string; region?: string; session_token?: string }) =>
    request<Record<string, unknown>>("POST", "/api/setup/aws-profile", body),
  login: (action: string) => request<{ job_id: string }>("POST", "/api/setup/login", { action }),
  sshTest: (deploy: JsonObject) => request<{ job_id: string }>("POST", "/api/setup/ssh-test", { deploy }),
  logs: (provider: string, stage: string, p: { since?: number; limit?: number; level?: string; function?: string; q?: string; source?: string }) =>
    request<LogsResponse>("GET", "/api/logs", undefined, { query: { provider, stage, ...p } }),
  logStreams: (provider: string, stage: string, fn?: string) =>
    request<{ supported: boolean; reason?: string; can_delete?: boolean; items: LogStream[] }>("GET", "/api/logs/streams", undefined, {
      query: { provider, stage, function: fn },
    }),
  deleteLogs: (provider: string, stage: string, body: { ids?: string[]; all?: boolean; function?: string }) =>
    request<{ deleted: number; errors?: string[] }>("POST", "/api/logs/delete", body, { query: { provider, stage } }),
  vscode: () => request<VsCodeState>("GET", "/api/vscode"),
  vscodeStart: (image?: string, python?: string, node?: string) => request<{ job_id: string }>("POST", "/api/vscode/start", { image, python, node }),
  env: () => request<DevEnvStatus>("GET", "/api/env"),
  envSetup: (body: { python?: string; recreate?: boolean; language?: string }) => request<{ job_id: string }>("POST", "/api/env/setup", body),
  vscodeStop: () => request<VsCodeState>("POST", "/api/vscode/stop", {}),
};

export function errorMessage(exc: unknown): string {
  if (exc instanceof ApiError) return exc.message;
  if (exc instanceof Error) return exc.message;
  return String(exc);
}
