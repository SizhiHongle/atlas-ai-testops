import type { components } from "./schema";

export type ProblemDetails = components["schemas"]["ProblemDetails"];

export class ApiProblemError extends Error {
  readonly problem: ProblemDetails;

  constructor(problem: ProblemDetails) {
    super(problem.detail);
    this.name = "ApiProblemError";
    this.problem = problem;
  }
}

export function isProblemDetails(value: unknown): value is ProblemDetails {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.status === "number" &&
    typeof candidate.errorCode === "string" &&
    typeof candidate.requestId === "string" &&
    typeof candidate.detail === "string"
  );
}
