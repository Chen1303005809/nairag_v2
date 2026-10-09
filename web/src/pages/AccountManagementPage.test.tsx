import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { ReviewHistoryAccess, ReviewHistoryPerson, User } from "../api/types";
import { AccountManagementPage } from "./AccountManagementPage";

vi.mock("../api/client", () => ({
  api: {
    listUsers: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
    resetUserPassword: vi.fn(),
    listReviewHistoryTargets: vi.fn(),
    listReviewHistoryAccess: vi.fn(),
    replaceReviewHistoryAccess: vi.fn()
  }
}));

const mockedApi = vi.mocked(api);

const viewer: User = {
  id: "viewer-1",
  username: "viewer",
  display_name: "查看者",
  role: "normal_user",
  is_active: true,
  must_change_password: false,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const reviewer: ReviewHistoryPerson = {
  id: "reviewer-1",
  username: "reviewer",
  display_name: "审核员"
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listUsers.mockResolvedValue([viewer]);
  mockedApi.listReviewHistoryTargets.mockResolvedValue([reviewer]);
  mockedApi.listReviewHistoryAccess.mockResolvedValue([]);
  mockedApi.replaceReviewHistoryAccess.mockResolvedValue([
    {
      reviewer,
      granted_by_user_id: "admin-1",
      granted_at: "2026-01-01T00:00:00Z"
    } satisfies ReviewHistoryAccess
  ]);
});

afterEach(() => {
  cleanup();
});

describe("AccountManagementPage review history access", () => {
  it("lets a system administrator grant a viewer access to a reviewer's history", async () => {
    render(<AccountManagementPage />);

    fireEvent.click(await screen.findByRole("button", { name: "审核历史权限" }));
    expect(await screen.findByText("审核历史权限：查看者")).toBeInTheDocument();
    await waitFor(() => expect(mockedApi.listReviewHistoryTargets).toHaveBeenCalled());

    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByText("审核员（reviewer）"));
    fireEvent.click(screen.getByRole("button", { name: "保存授权" }));

    await waitFor(() =>
      expect(mockedApi.replaceReviewHistoryAccess).toHaveBeenCalledWith(
        "viewer-1",
        ["reviewer-1"]
      )
    );
  });
});
