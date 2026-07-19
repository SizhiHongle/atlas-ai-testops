import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSessionQuery } from "../api/auth-queries";
import { PermissionGuard } from "./permission-guard";

vi.mock("../api/auth-queries", () => ({
  useSessionQuery: vi.fn()
}));

const mockedUseSessionQuery = vi.mocked(useSessionQuery);

function sessionQuery(
  overrides: Record<string, unknown>
): ReturnType<typeof useSessionQuery> {
  return {
    data: undefined,
    error: null,
    isError: false,
    isPending: false,
    refetch: vi.fn(),
    ...overrides
  } as unknown as ReturnType<typeof useSessionQuery>;
}

describe("PermissionGuard", () => {
  beforeEach(() => {
    mockedUseSessionQuery.mockReset();
  });

  it("waits for the session before deciding access", () => {
    mockedUseSessionQuery.mockReturnValue(
      sessionQuery({ isPending: true })
    );

    render(
      <PermissionGuard anyOf={["PROJECT_ADMIN"]}>
        <span>restricted action</span>
      </PermissionGuard>
    );

    expect(screen.getByText("正在验证操作权限")).toBeInTheDocument();
    expect(screen.queryByText("restricted action")).not.toBeInTheDocument();
  });

  it("exposes session failures and supports retry", () => {
    const refetch = vi.fn();
    mockedUseSessionQuery.mockReturnValue(
      sessionQuery({
        error: new Error("session unavailable"),
        isError: true,
        refetch
      })
    );

    render(
      <PermissionGuard anyOf={["PROJECT_ADMIN"]}>
        <span>restricted action</span>
      </PermissionGuard>
    );

    expect(screen.getByText("session unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新连接" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("renders only roles authorized by the server session", () => {
    mockedUseSessionQuery.mockReturnValue(
      sessionQuery({
        data: {
          roles: ["RUN_OPERATOR"]
        }
      })
    );

    const { rerender } = render(
      <PermissionGuard anyOf={["PROJECT_ADMIN"]}>
        <span>restricted action</span>
      </PermissionGuard>
    );
    expect(
      screen.getByText("当前身份无权访问此能力")
    ).toBeInTheDocument();

    rerender(
      <PermissionGuard anyOf={["RUN_OPERATOR"]}>
        <span>restricted action</span>
      </PermissionGuard>
    );
    expect(screen.getByText("restricted action")).toBeInTheDocument();
  });
});
