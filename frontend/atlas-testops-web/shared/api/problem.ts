export type ApiProblem = {
  type?: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  errorCode?: string;
  requestId?: string;
  violations?: Array<{
    field: string;
    message: string;
    code?: string;
  }>;
};

export function isApiProblem(value: unknown): value is ApiProblem {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ApiProblem>;
  return (
    typeof candidate.title === "string" &&
    typeof candidate.status === "number" &&
    typeof candidate.detail === "string"
  );
}

export class ApiProblemError extends Error {
  readonly problem: ApiProblem;

  constructor(problem: ApiProblem) {
    super(problem.detail);
    this.name = "ApiProblemError";
    this.problem = problem;
  }

  get status(): number {
    return this.problem.status;
  }
}

export function toApiError(error: unknown, fallbackMessage: string): Error {
  if (isApiProblem(error)) return new ApiProblemError(error);
  return error instanceof Error ? error : new Error(fallbackMessage);
}
