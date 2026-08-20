import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { EditableContentEntry, KnowledgeBase, ReviewSubmission } from "../api/types";
import { ContentSubmissionPage } from "./ContentSubmissionPage";

vi.mock("../api/client", () => ({
  api: {
    listKnowledgeBases: vi.fn(),
    listAvailableParents: vi.fn(),
    listMyContentSubmissions: vi.fn(),
    listEditableContentEntries: vi.fn(),
    createChildRevision: vi.fn(),
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
    display_name: "上传人"
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
    question_type: "功能故障类",
    business_object: "基础知识与算法",
    purpose: "内部培训",
    customer_type: "个人客户",
    feature_explanation: null,
    example: null,
    internal_notes: null
  }
};

const editableEntry: EditableContentEntry = {
  child_id: "child-1",
  parent_id: "parent-1",
  parent_name: "账号登录",
  is_primary: false,
  knowledge_bases: [knowledgeBase],
  parent_revision: null,
  child_revision: rejectedSubmission.child_revision!
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.listAvailableParents.mockResolvedValue([]);
  mockedApi.listMyContentSubmissions.mockResolvedValue([rejectedSubmission]);
  mockedApi.listEditableContentEntries.mockResolvedValue([]);
  mockedApi.createChildRevision.mockResolvedValue(rejectedSubmission);
  mockedApi.resubmitRejectedChild.mockResolvedValue(rejectedSubmission);
});

afterEach(() => {
  cleanup();
});

describe("ContentSubmissionPage", () => {
  it("uses category labels and compact option layout", async () => {
    render(<ContentSubmissionPage />);

    expect(await screen.findByRole("heading", { name: "问题大类" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "问题小类" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "新建问题大类及问题小类" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "问题类型" })).toBeInTheDocument();
    const supplementaryFields = screen.getByRole("button", { name: /可补充说明/ });
    expect(supplementaryFields).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /业务字段/ })).not.toBeInTheDocument();
    expect(document.querySelector(".content-form-grid")).toBeInTheDocument();
    expect(document.querySelector(".knowledge-base-options")).toBeInTheDocument();

    fireEvent.click(supplementaryFields);
    expect(await screen.findByRole("textbox", { name: "功能说明" })).toBeInTheDocument();
  });

  it("opens rejected content in place and resubmits the edited revision", async () => {
    render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "我的上传" }));
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

  it("submits a revision for a published ordinary child entry", async () => {
    mockedApi.listEditableContentEntries.mockResolvedValue([editableEntry]);
    render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "修改已发布内容" }));
    fireEvent.click(await screen.findByRole("button", { name: "修改并提交审核" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: "回复内容" }), {
      target: { value: "请先确认身份信息，再联系管理员重置密码。" }
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "提交新修订审核" }));

    await waitFor(() =>
      expect(mockedApi.createChildRevision).toHaveBeenCalledWith(
        "child-1",
        expect.objectContaining({
          question: "如何找回密码？",
          response_content: "请先确认身份信息，再联系管理员重置密码。"
        }),
        ["knowledge-base-1"]
      )
    );
  });
});
