import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { KnowledgeBase, ReviewQueueItem } from "../api/types";
import { ReviewWorkbenchPage } from "./ReviewWorkbenchPage";

vi.mock("../api/client", () => ({
  api: {
    listReviewQueue: vi.fn(),
    listAssignedReviewKnowledgeBases: vi.fn(),
    listMyReviewHistory: vi.fn(),
    decideReviewTarget: vi.fn()
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
  mockedApi.listMyReviewHistory.mockResolvedValue([historyItem]);
});

describe("ReviewWorkbenchPage history", () => {
  it("shows the current administrator's audit history with uploader and second-precision times", async () => {
    render(<ReviewWorkbenchPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "我的审核历史" }));

    expect(await screen.findByText("上传人（author）")).toBeInTheDocument();
    expect(screen.getByText("审核人（reviewer）")).toBeInTheDocument();
    expect(screen.getByText("内容完整")).toBeInTheDocument();
    await waitFor(() => expect(mockedApi.listMyReviewHistory).toHaveBeenCalled());
  });
});
