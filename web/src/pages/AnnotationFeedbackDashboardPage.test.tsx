import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { AnnotationFeedbackPage, AnnotationFeedbackSummary, ManagedKnowledgeBase } from "../api/types";
import { AnnotationFeedbackDashboardPage } from "./AnnotationFeedbackDashboardPage";

vi.mock("../api/client", () => ({
  api: {
    listManagedKnowledgeBases: vi.fn(),
    getAnnotationFeedbackSummary: vi.fn(),
    listAnnotationFeedback: vi.fn(),
    getAnnotationFeedbackDetail: vi.fn()
  }
}));

const mockedApi = vi.mocked(api);

const knowledgeBase: ManagedKnowledgeBase = {
  id: "kb-1",
  logical_key: "support",
  name: "支持知识库",
  description: null,
  is_active: true,
  current_collection_generation: 1,
  current_physical_collection_name: "support-v1",
  reviewer_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const summary: AnnotationFeedbackSummary = {
  completed_review_count: 5,
  annotated_result_count: 11,
  high_score_irrelevant_count: 2,
  low_score_relevant_count: 1,
  normal_count: 6,
  other_count: 2
};

const feedbackPage: AnnotationFeedbackPage = {
  total: 5,
  page: 1,
  page_size: 20,
  items: [
    {
      id: "feedback-1",
      submitted_by: { id: "user-1", username: "agent", display_name: "业务人员" },
      interaction_type: "quick_search",
      queries: ["登录失败怎么办？", "如何重置密码？"],
      target_knowledge_base_id: null,
      target_knowledge_base_name: null,
      high_score_irrelevant_count: 1,
      low_score_relevant_count: 0,
      normal_count: 1,
      other_count: 1,
      searched_at: "2026-08-28T00:00:00Z",
      submitted_at: "2026-08-28T00:01:00Z",
      result_count: 2
    }
  ]
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listManagedKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.getAnnotationFeedbackSummary.mockResolvedValue(summary);
  mockedApi.listAnnotationFeedback.mockResolvedValue(feedbackPage);
});

afterEach(cleanup);

describe("AnnotationFeedbackDashboardPage", () => {
  it("shows Review and result-label distribution beside paginated annotation details", async () => {
    render(<AnnotationFeedbackDashboardPage />);

    expect(await screen.findByText("已完成 Review")).toBeInTheDocument();
    expect(screen.getByText("结果正常（跳过）")).toBeInTheDocument();
    expect(screen.getByText("其他：1")).toBeInTheDocument();
    expect(screen.getByText("快速检索")).toBeInTheDocument();
    await waitFor(() =>
      expect(mockedApi.getAnnotationFeedbackSummary).toHaveBeenCalledWith({
        annotated_from: undefined,
        annotated_to: undefined,
        knowledge_base_id: undefined,
        query_keyword: undefined
      })
    );
    expect(mockedApi.listAnnotationFeedback).toHaveBeenCalledWith({
      annotated_from: undefined,
      annotated_to: undefined,
      knowledge_base_id: undefined,
      query_keyword: undefined,
      feedback_type: undefined,
      page: 1,
      page_size: 20
    });
  });
});
