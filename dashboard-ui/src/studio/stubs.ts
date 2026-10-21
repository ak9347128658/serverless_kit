import { api } from "./api";

/** Handler stubs queued by "create on save"; written after pskit.yml is saved. */
const pending = new Map<string, Set<string>>();

export function queueHandlerStub(handler: string, events: string[]): void {
  const set = pending.get(handler) || new Set<string>();
  events.forEach((e) => set.add(e));
  if (!events.length) set.add("invoke");
  pending.set(handler, set);
}

export function forgetHandlerStub(handler: string): void {
  pending.delete(handler);
}

/** Write every queued stub. Returns how many files were changed. Never throws. */
export async function flushHandlerStubs(): Promise<number> {
  let created = 0;
  for (const [handler, events] of Array.from(pending.entries())) {
    try {
      const res = await api.ensureHandler(handler, Array.from(events));
      if (res.created) created++;
      pending.delete(handler);
    } catch {
      // keep it queued; the next save retries
    }
  }
  return created;
}
