"use client";

export type ClientErrorContext = {
  source: "mutation" | "query" | "route";
  operation?: string;
  digest?: string;
};

function normalizeError(error: unknown): Error {
  return error instanceof Error ? error : new Error("Unknown client error");
}

export function observeClientError(
  error: unknown,
  context: ClientErrorContext
): void {
  const normalized = normalizeError(error);

  window.dispatchEvent(
    new CustomEvent("atlas:client-error", {
      detail: {
        source: context.source,
        operation: context.operation,
        digest: context.digest,
        name: normalized.name
      }
    })
  );
}

export function reportClientError(
  error: unknown,
  context: ClientErrorContext
): void {
  const normalized = normalizeError(error);
  observeClientError(normalized, context);

  if (typeof globalThis.reportError === "function") {
    globalThis.reportError(normalized);
  } else if (process.env.NODE_ENV !== "production") {
    console.error("Atlas client error", context, normalized);
  }
}
