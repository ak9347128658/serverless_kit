// Type declarations of the pskit Node.js runtime (package "pskit").
//
//   import type { Event, Context } from "pskit";
//   import { Response, BatchResult } from "pskit";
//
//   export async function hello(event: Event, ctx: Context) {
//     return { message: `Hello, ${event.http?.query.name ?? "world"}!` };
//   }

// Uses Node.js types (Buffer, NodeJS.ProcessEnv): add @types/node to devDependencies.

export type EventTypeName = "http" | "schedule" | "queue" | "storage" | "topic" | "invoke" | "workflow";

export declare const EventType: {
  readonly HTTP: "http";
  readonly SCHEDULE: "schedule";
  readonly QUEUE: "queue";
  readonly STORAGE: "storage";
  readonly TOPIC: "topic";
  readonly INVOKE: "invoke";
  readonly WORKFLOW: "workflow";
  readonly ALL: readonly EventTypeName[];
};

/** Case-insensitive, read-only HTTP headers. Multi-value headers are joined with ", ". */
export declare class Headers implements Iterable<[string, string]> {
  constructor(raw?: { [key: string]: unknown } | Array<[string, unknown]> | Headers | null);
  get(key: string): string | undefined;
  get<T>(key: string, fallback: T): string | T;
  has(key: string): boolean;
  keys(): IterableIterator<string>;
  values(): IterableIterator<string>;
  entries(): IterableIterator<[string, string]>;
  [Symbol.iterator](): IterableIterator<[string, string]>;
  readonly size: number;
  toObject(): { [key: string]: string };
}

export interface HttpRequestInit {
  method?: string;
  path?: string;
  headers?: { [key: string]: unknown } | Array<[string, unknown]> | Headers;
  query?: { [key: string]: string };
  multiQuery?: { [key: string]: string[] };
  pathParams?: { [key: string]: string };
  body?: Buffer | string | null;
  sourceIp?: string | null;
  route?: string | null;
}

export declare class HttpRequest {
  constructor(init?: HttpRequestInit);
  method: string;
  path: string;
  headers: Headers;
  query: { [key: string]: string };
  multiQuery: { [key: string]: string[] };
  pathParams: { [key: string]: string };
  body: Buffer;
  sourceIp: string | null;
  /** The matched route template, e.g. `/users/{id}`. */
  route: string | null;
  readonly contentType: string;
  readonly cookies: { [key: string]: string };
  text(encoding?: BufferEncoding): string;
  /** Parse the body as JSON; throws BadRequest (HTTP 400) unless a fallback is given. */
  json<T = any>(): T;
  json<T = any, F = T>(fallback: F): T | F;
  form(): { [key: string]: string };
  static decodeBody(body: unknown, isBase64?: boolean): Buffer;
}

/** One message / object notification of a queue, topic or storage event. */
export declare class Record {
  constructor(init?: Partial<Record>);
  id: string | null;
  body: any;
  attributes: { [key: string]: any };
  source: string | null;
  bucket: string | null;
  key: string | null;
  size: number | null;
  action: "created" | "deleted" | null;
  raw: any;
  text(): string;
  json<T = any>(): T;
  json<T = any, F = T>(fallback: F): T | F;
}

export interface EventInit {
  type: EventTypeName | string;
  provider?: string;
  function?: string;
  http?: HttpRequest | null;
  records?: Record[];
  data?: any;
  time?: Date | string | number;
  source?: string | null;
  raw?: any;
}

export declare class Event implements Iterable<Record> {
  constructor(init: EventInit);
  type: EventTypeName | string;
  provider: string;
  function: string;
  /** Set for `http` events. */
  http: HttpRequest | null;
  /** Queue messages, topic messages or storage objects. */
  records: Record[];
  /** Schedule input, invoke() payload or workflow task input. */
  data: any;
  time: Date;
  source: string | null;
  raw: any;
  readonly isHttp: boolean;
  readonly isBatch: boolean;
  /** HTTP: the parsed body; batches: the first record's body; otherwise `data`. */
  json<T = any>(): T;
  json<T = any, F = T>(fallback: F): T | F;
  [Symbol.iterator](): Iterator<Record>;
  toDict(): { [key: string]: any };
  toWire(): { [key: string]: any };
}

export declare class Logger {
  constructor(name?: string, fields?: { [key: string]: any });
  readonly name: string;
  child(fields: { [key: string]: any }): Logger;
  debug(message: string, fields?: { [key: string]: any }): void;
  info(message: string, fields?: { [key: string]: any }): void;
  warning(message: string, fields?: { [key: string]: any }): void;
  warn(message: string, fields?: { [key: string]: any }): void;
  error(message: string, fields?: { [key: string]: any } | Error, error?: unknown): void;
  exception(message: string, error: unknown, fields?: { [key: string]: any }): void;
  critical(message: string, fields?: { [key: string]: any }): void;
}

export declare function getLogger(name?: string, fields?: { [key: string]: any }): Logger;

export interface ContextInit {
  requestId?: string;
  function?: string;
  provider?: string;
  service?: string;
  stage?: string;
  region?: string;
  memoryMb?: number | null;
  deadline?: number | null;
  raw?: any;
  remaining?: () => number;
}

export declare class Context {
  constructor(init?: ContextInit);
  requestId: string;
  function: string;
  provider: string;
  service: string;
  stage: string;
  region: string;
  memoryMb: number | null;
  /** Unix epoch seconds when the platform kills this invocation, if known. */
  deadline: number | null;
  raw: any;
  readonly env: NodeJS.ProcessEnv;
  /** JSON-lines logger bound to function, request_id and provider. */
  readonly logger: Logger;
  remainingTimeMs(): number | null;
}

export interface CookieOptions {
  maxAge?: number | null;
  path?: string;
  httpOnly?: boolean;
  secure?: boolean;
  sameSite?: "Lax" | "Strict" | "None" | string;
}

export declare class Response {
  constructor(status?: number, body?: any, headers?: { [key: string]: string });
  status: number;
  body: any;
  headers: { [key: string]: string };
  cookies: string[];
  static json(data: any, status?: number, headers?: { [key: string]: string }): Response;
  static text(text: string, status?: number, headers?: { [key: string]: string }): Response;
  static html(html: string, status?: number, headers?: { [key: string]: string }): Response;
  static binary(data: Buffer | Uint8Array, contentType?: string, status?: number, headers?: { [key: string]: string }): Response;
  static redirect(location: string, status?: number): Response;
  static noContent(): Response;
  setCookie(name: string, value: string, options?: CookieOptions): this;
  readonly contentType: string;
  render(): { status: number; headers: { [key: string]: string }; body: Buffer };
  renderForGateway(): { status: number; headers: { [key: string]: string }; body: string; isBase64: boolean };
}

export declare function toResponse(result: unknown): Response;

/** Return from a batch handler to retry only the records that failed. */
export declare class BatchResult {
  constructor(failed?: string[]);
  failed: string[];
  fail(record: Record | { id?: string | null } | string): void;
  static of(failed: Iterable<Record | string>): BatchResult;
  readonly ok: boolean;
  raiseIfFailed(): void;
}

export declare class BatchFailure extends Error {
  failedIds: string[];
}

export declare class Resource {
  name: string;
  type: string;
  nativeName: string;
  external: boolean;
  connection: { [key: string]: string };
  get(key: string): string | null;
  get<T>(key: string, fallback: T): string | T;
  require(key: string): string;
  readonly arn: string | null;
  readonly url: string | null;
  readonly id: string | null;
}

/** Every resource bound to this function (`resources:` in pskit.yml). */
export declare function resources(env?: NodeJS.ProcessEnv): { [name: string]: Resource };
/** The binding of resource `name`. */
export declare function resource(name: string, env?: NodeJS.ProcessEnv): Resource;

/** Call another function of this service; resolves to its result. `timeout` in seconds. */
export declare function invoke<T = any>(
  fn: string,
  payload?: any,
  options?: { async?: boolean; timeout?: number },
): Promise<T>;

export declare class InvokeError extends Error {}

/**
 * Register handler modules in memory (platforms without a filesystem, e.g. Cloudflare Workers),
 * keyed by module path: `registerModules({ "src/handlers": handlers })` (dotted keys work too).
 */
export declare function registerModules(modules: { [modulePath: string]: any }): void;
/** Register the bundle manifest (pskit_manifest.json contents) where it can't be read from disk. */
export declare function registerManifest(manifest: { [key: string]: any }): void;

export declare function eventFromWire(wire: { [key: string]: any }): Event;
export declare function parseTime(value: unknown): Date;

export interface HttpErrorOptions {
  status?: number;
  details?: any;
  headers?: { [key: string]: string };
}

export declare class PskitError extends Error {}
export declare class HandlerNotFound extends PskitError {}

/** Throw from an HTTP handler to answer with `{"error": message, "status": status}`. */
export declare class HttpError extends Error {
  constructor(message?: string, options?: HttpErrorOptions);
  status: number;
  details: any;
  headers: { [key: string]: string };
  toBody(): { error: string; status: number; details?: any };
}
export declare class BadRequest extends HttpError {}
export declare class Unauthorized extends HttpError {}
export declare class Forbidden extends HttpError {}
export declare class NotFound extends HttpError {}
export declare class MethodNotAllowed extends HttpError {}
export declare class Conflict extends HttpError {}
export declare class UnprocessableEntity extends HttpError {}
export declare class TooManyRequests extends HttpError {}

/** A handler: plain values become JSON 200, `null` 204, strings text/plain, Buffers binary. */
export type Handler<R = any> = (event: Event, context: Context) => R | Promise<R>;

export declare const version: string;
