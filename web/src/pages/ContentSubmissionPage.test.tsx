import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { KnowledgeBase, ReviewSubmission } from "../api/types";
import { ContentSubmissionPage } from "./ContentSubmissionPage";

vi.mock("../api/client", () => ({
  api: {
    listKnowledgeBases: vi.fn(),
    listAvailableParents: vi.fn(),
    listMyContentSubmissions: vi.fn(),
    resubmitRejectedChild: vi.fn()
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

const rejectedSubmission: ReviewSubmission = {
  id: "submission-1",
  submission_kind: "child",
  status: "rejected",
  parent_id: "parent-1",
  parent_revision_id: null,
  child_id: "child-1",
  child_revision_id: "child-revision-1",
  title: "账号登录",
  targets: [
    {
      ...knowledgeBase,
      status: "rejected",
      review_comment: "请补充身份校验说明",
      reviewer: {
        id: "reviewer-1",
        username: "reviewer",
        display_name: "审核管理员"
      },
      reviewed_at: "2026-01-01T00:01:00Z",
      review_decision: "rejected"
    }
  ],
  submitter: {
    id: "user-1",
    username: "author",
    display_name: "投稿人"
  },
  submitted_at: "2026-01-01T00:00:00Z",
  parent_revision: null,
  child_revision: {
    id: "child-revision-1",
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
    internal_notes: null
  }
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.listAvailableParents.mockResolvedValue([]);
  mockedApi.listMyContentSubmissions.mockResolvedValue([rejectedSubmission]);
  mockedApi.resubmitRejectedChild.mockResolvedValue(rejectedSubmission);
});

describe("ContentSubmissionPage rejected submissions", () => {
  it("opens rejected content in place and resubmits the edited revision", async () => {
    render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "我的投稿" }));
    const editButton = await screen.findByRole("button", { name: "编辑重提" });
    fireEvent.click(editButton);

    const dialog = await screen.findByRole("dialog");
    const responseInput = within(dialog).getByRole("textbox", { name: "回复内容" });
    fireEvent.change(responseInput, {
      target: { value: "请先完成身份验证，再联系管理员重置密码。" }
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "重新提交审核" }));

    await waitFor(() =>
      expect(mockedApi.resubmitRejectedChild).toHaveBeenCalledWith(
        "submission-1",
        expect.objectContaining({
          question: "如何找回密码？",
          response_content: "请先完成身份验证，再联系管理员重置密码。"
        }),
        ["knowledge-base-1"]
      )
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
