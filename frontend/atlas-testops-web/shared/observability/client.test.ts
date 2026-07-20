import { afterEach, describe, expect, it, vi } from "vitest";

import {
  observeClientError,
  reportClientError
} from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("client error observability", () => {
  it("observes a handled query or mutation failure without escalating it", () => {
    const reportError = vi.fn();
    const listener = vi.fn();
    vi.stubGlobal("reportError", reportError);
    window.addEventListener("atlas:client-error", listener, { once: true });

    observeClientError(new Error("validation failed"), {
      source: "mutation",
      operation: "case"
    });

    expect(listener).toHaveBeenCalledOnce();
    expect((listener.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({
      source: "mutation",
      operation: "case",
      digest: undefined,
      name: "Error"
    });
    expect(reportError).not.toHaveBeenCalled();
  });

  it("reports an unexpected route failure to the browser error channel", () => {
    const reportError = vi.fn();
    vi.stubGlobal("reportError", reportError);
    const error = new Error("route crashed");

    reportClientError(error, {
      source: "route",
      digest: "route-digest"
    });

    expect(reportError).toHaveBeenCalledWith(error);
  });
});
