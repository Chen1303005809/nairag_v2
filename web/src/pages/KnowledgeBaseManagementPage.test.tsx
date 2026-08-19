import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { ManagedKnowledgeBase, ReviewerAssignment, User } from "../api/types";
import { KnowledgeBaseManagementPage } from "./KnowledgeBaseManagementPage";

vi.mock("../api/client", () => ({
  api: {
    listManagedKnowledgeBases: vi.fn(),
    listUsers: vi.fn(),
    listKnowledgeBaseReviewers: vi.fn(),
    createKnowledgeBase: vi.fn(),
    updateKnowledgeBase: vi.fn(),
    assignKnowledgeBaseReviewer: vi.fn(),
    unassignKnowledgeBaseReviewer: vi.fn()
  }
}));

const mockedApi = vi.mocked(api);

const knowledgeBase: ManagedKnowledgeBase = {
  id: "knowledge-base-1",
  logical_key: "support",
  name: "支持知识库",
  description: null,
  is_active: true,
  current_collection_generation: 1,
  current_physical_collection_name: "nairag_support_g1",
  reviewer_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const normalUser: User = {
  id: "user-1",
  username: "new-reviewer",
  display_name: "新审查管理员",
  role: "normal_user",
  is_active: true,
  must_change_password: false,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const reviewerUser: User = { ...normalUser, role: "review_admin" };

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listManagedKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.listUsers.mockResolvedValue([normalUser]);
  mockedApi.listKnowledgeBaseReviewers.mockResolvedValue([] as ReviewerAssignment[]);
});

describe("KnowledgeBaseManagementPage reviewer authorization", () => {
  it("refreshes reviewer candidates after a user is promoted", async () => {
    let currentUsers: User[] = [normalUser];
    mockedApi.listUsers.mockImplementation(async () => currentUsers);

    render(<KnowledgeBaseManagementPage />);
    await waitFor(() => expect(mockedApi.listUsers).toHaveBeenCalledWith(false));

    currentUsers = [reviewerUser];
    fireEvent.click(await screen.findByRole("button", { name: "审查授权" }));

    await waitFor(() => expect(mockedApi.listKnowledgeBaseReviewers).toHaveBeenCalledWith(knowledgeBase.id));
    await waitFor(() => expect(mockedApi.listUsers).toHaveBeenCalledTimes(2));

    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(await screen.findByText("新审查管理员（new-reviewer）")).toBeInTheDocument();
  });
});
