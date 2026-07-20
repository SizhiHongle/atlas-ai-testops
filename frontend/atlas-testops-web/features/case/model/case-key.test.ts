import { describe, expect, it } from "vitest";

import {
  finalizeCaseKeyInput,
  normalizeCaseKeyInput
} from "./case-key";

describe("case key", () => {
  it("normalizes the prototype-friendly dotted value to the API contract", () => {
    expect(normalizeCaseKeyInput("crm.customer.filter")).toBe(
      "CRM-CUSTOMER-FILTER"
    );
  });

  it("preserves a trailing separator while typing and removes it on submit", () => {
    expect(normalizeCaseKeyInput("crm.customer.")).toBe("CRM-CUSTOMER-");
    expect(finalizeCaseKeyInput("crm.customer.")).toBe("CRM-CUSTOMER");
  });
});
