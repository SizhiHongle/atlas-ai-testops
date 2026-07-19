export function createRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `atlas-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
