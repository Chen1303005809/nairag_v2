import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { KnowledgeBase, ReviewQueueItem } from "../api/types";
import { ReviewWorkbenchPage } from "./ReviewWorkbenchPage";

vi.mock("../api/client", () => ({
  api: {
    listReviewQueue: vi.fn(),
    listAssignedReviewKnowledgeBases: vi.fn(),
    listMyReviewHistory: vi.fn(),
    listReviewHistorySubjects: vi.fn(),
    decideReviewTarget: vi.fn(),
    retryReviewTargetIndexing: vi.fn()
  }
}));

const mockedApi = vi.mocked(api);

const knowledgeBase: KnowledgeBase = {
  id: "knowledge-base-1",
  logical_key: "support",
  name: "支持知识库",
  description: null,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const historyItem: ReviewQueueItem = {
  id: "decision-1",
  review_submission_id: "submission-1",
  submission_kind: "child",
  submission_status: "published",
  target_status: "published",
  parent_id: "parent-1",
  parent_revision_id: null,
  child_id: "child-1",
  child_revision_id: "revision-1",
  knowledge_base_id: knowledgeBase.id,
  knowledge_base: knowledgeBase,
  submitter: { id: "author-1", username: "author", display_name: "上传人" },
  reviewer: { id: "reviewer-1", username: "reviewer", display_name: "审核人" },
  review_decision: "approved",
  review_comment: "内容完整",
  parent_revision: null,
  child_revision: {
    id: "revision-1",
    revision_number: 1,
    question: "如何找回密码？",
    response_content: "请联系管理员。",
    question_variants: [],
    follow_up_guidance: null,
    question_type: null,
    business_object: null,
    purpose: null,
    customer_type: null,
    feature_explanation: null,
    example: null,
    internal_notes: null,
    attachments: [],
    web_links: []
  },
  submitted_at: "2026-01-01T00:00:00Z",
  reviewed_at: "2026-01-01T00:01:02Z"
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listReviewQueue.mockResolvedValue([]);
  mockedApi.listAssignedReviewKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.listReviewHistorySubjects.mockResolvedValue([
    { id: "reviewer-1", username: "reviewer", display_name: "审核人", is_self: true }
  ]);
  mockedApi.listMyReviewHistory.mockResolvedValue([historyItem]);
});

afterEach(() => {
  cleanup();
});

describe("ReviewWorkbenchPage history", () => {
  it("shows the current administrator's audit history with uploader and second-precision times", async () => {
    render(<ReviewWorkbenchPage />);

    fireEvent.click((await screen.findAllByRole("tab", { name: "我的审核历史" }))[0]);

    expect(await screen.findByText("上传人（author）")).toBeInTheDocument();
    expect(screen.getByText("审核人（reviewer）")).toBeInTheDocument();
    expect(screen.getByText("内容完整")).toBeInTheDocument();
    await waitFor(() => expect(mockedApi.listMyReviewHistory).toHaveBeenCalled());
  });

  it("shows index failures and retries beside them in audit history", async () => {
    mockedApi.listMyReviewHistory.mockResolvedValue([
      { ...historyItem, target_status: "index_failed", submission_status: "index_failed" }
    ]);
    mockedApi.retryReviewTargetIndexing.mockResolvedValue(undefined);
    render(<ReviewWorkbenchPage />);

    fireEvent.click((await screen.findAllByRole("tab", { name: "我的审核历史" }))[0]);

    await waitFor(() => expect(mockedApi.listMyReviewHistory).toHaveBeenCalled());
    expect(await screen.findByText("索引失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试索引" }));
    await waitFor(() =>
      expect(mockedApi.retryReviewTargetIndexing).toHaveBeenCalledWith(
        "submission-1",
        "knowledge-base-1"
      )
    );
  });

  it("allows selecting a granted history and hides retry actions for another reviewer", async () => {
    mockedApi.listReviewHistorySubjects.mockResolvedValue([
      { id: "reviewer-1", username: "reviewer", display_name: "审核人", is_self: true },
      { id: "reviewer-2", username: "reviewer-two", display_name: "其他审核人", is_self: false }
    ]);
    mockedApi.listMyReviewHistory.mockResolvedValue([
      { ...historyItem, target_status: "index_failed", submission_status: "index_failed" }
    ]);
    render(<ReviewWorkbenchPage />);

    const historyTab = (await screen.findAllByRole("tab", { name: "我的审核历史" }))[0];
    fireEvent.click(historyTab);
    expect(await screen.findByRole("button", { name: "重试索引" })).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByText("审核人（reviewer） · 本人").closest(".ant-select-selection-item") ??
      screen.getByText("审核人（reviewer） · 本人"));
    fireEvent.click(await screen.findByText("其他审核人（reviewer-two）"));

    await waitFor(() =>
      expect(mockedApi.listMyReviewHistory).toHaveBeenLastCalledWith("reviewer-2")
    );
    expect(screen.queryByRole("button", { name: "重试索引" })).not.toBeInTheDocument();
  });

  it("shows the read-only history view to a normal user without loading a review queue", async () => {
    mockedApi.listReviewHistorySubjects.mockResolvedValue([
      { id: "reviewer-2", username: "reviewer-two", display_name: "其他审核人", is_self: false }
    ]);
    render(<ReviewWorkbenchPage historyOnly />);

    expect(await screen.findByRole("heading", { name: "审核历史" })).toBeInTheDocument();
    expect(await screen.findByText("其他审核人（reviewer-two）")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "待审核" })).not.toBeInTheDocument();
    await waitFor(() => expect(mockedApi.listMyReviewHistory).toHaveBeenCalledWith("reviewer-2"));
    expect(mockedApi.listReviewQueue).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "重试索引" })).not.toBeInTheDocument();
  });
});
