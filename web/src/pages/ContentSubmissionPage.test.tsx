import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type {
  AvailableParent,
  EditableContentEntry,
  IngestionBatch,
  KnowledgeBase,
  KnowledgeDraft,
  OcrRecognition,
  ReviewSubmission
} from "../api/types";
import { ContentSubmissionPage } from "./ContentSubmissionPage";

vi.mock("../api/client", () => ({
  api: {
    listKnowledgeBases: vi.fn(),
    listAvailableParents: vi.fn(),
    listMyContentSubmissions: vi.fn(),
    listEditableContentEntries: vi.fn(),
    listKnowledgeDrafts: vi.fn(),
    listIngestionBatches: vi.fn(),
    createKnowledgeDraft: vi.fn(),
    updateKnowledgeDraft: vi.fn(),
    deleteKnowledgeDraft: vi.fn(),
    submitKnowledgeDraft: vi.fn(),
    createIngestionBatch: vi.fn(),
    getIngestionBatch: vi.fn(),
    recognizeSearchImage: vi.fn(),
    recognizeConversationImage: vi.fn(),
    createChildRevision: vi.fn(),
    resubmitRejectedChild: vi.fn(),
    uploadKnowledgeAttachment: vi.fn(),
    knowledgeAttachmentDownloadUrl: vi.fn(
      (attachmentId: string) => `/api/v1/knowledge-content/attachments/${attachmentId}/download`
    )
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
    internal_notes: null,
    attachments: [],
    web_links: []
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

const availableParent: AvailableParent = {
  id: "parent-1",
  name: "问题反馈",
  canonical_keyword: "账号登录",
  primary_child_id: "child-1",
  available_knowledge_bases: [
    {
      id: knowledgeBase.id,
      logical_key: knowledgeBase.logical_key,
      name: knowledgeBase.name
    }
  ]
};

const draft: KnowledgeDraft = {
  id: "draft-1",
  source: "manual_saved",
  parent_id: null,
  ingestion_batch_id: null,
  question: "待补充的问题",
  response_content: null,
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
  web_links: [],
  knowledge_base_ids: [],
  source_hash: null,
  extracted_at: null,
  model_version: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const ingestionBatch: IngestionBatch = {
  id: "batch-1",
  status: "completed",
  message_count: 2,
  source_hash: "a".repeat(64),
  generated_count: 1,
  rejected_count: 0,
  rejection_reasons: [],
  model_version: "fake-llm",
  last_error: null,
  created_at: "2026-01-01T00:00:00Z",
  completed_at: "2026-01-01T00:00:10Z"
};

const ocrRecognition: OcrRecognition = {
  text: "资金账户可用余额低于最小预留",
  keywords: ["资金账户", "最小预留"],
  confidence: 0.97,
  model_version: "PP-OCRv6_medium",
  recognition_token: "ocr-ticket"
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.listAvailableParents.mockResolvedValue([]);
  mockedApi.listMyContentSubmissions.mockResolvedValue([rejectedSubmission]);
  mockedApi.listEditableContentEntries.mockResolvedValue([]);
  mockedApi.listKnowledgeDrafts.mockResolvedValue([]);
  mockedApi.listIngestionBatches.mockResolvedValue([]);
  mockedApi.createKnowledgeDraft.mockResolvedValue(draft);
  mockedApi.updateKnowledgeDraft.mockResolvedValue(draft);
  mockedApi.deleteKnowledgeDraft.mockResolvedValue(undefined);
  mockedApi.submitKnowledgeDraft.mockResolvedValue(rejectedSubmission);
  mockedApi.createIngestionBatch.mockResolvedValue(ingestionBatch);
  mockedApi.getIngestionBatch.mockResolvedValue({ ...ingestionBatch, drafts: [draft] });
  mockedApi.recognizeSearchImage.mockResolvedValue(ocrRecognition);
  mockedApi.recognizeConversationImage.mockResolvedValue(ocrRecognition);
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
    expect(screen.getByRole("tab", { name: "新建问题大类" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "问题类型" })).toBeInTheDocument();
    const supplementaryFields = screen.getByRole("button", { name: /可补充说明/ });
    expect(supplementaryFields).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /业务字段/ })).not.toBeInTheDocument();
    expect(document.querySelector(".content-form-grid")).toBeInTheDocument();
    expect(document.querySelector(".knowledge-base-options")).toBeInTheDocument();

    fireEvent.click(supplementaryFields);
    expect(await screen.findByRole("textbox", { name: "功能说明" })).toBeInTheDocument();
  });

  it("shows available parent options with the keyword first", async () => {
    mockedApi.listAvailableParents.mockResolvedValue([availableParent]);
    render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "新建问题小类" }));
    fireEvent.mouseDown(await screen.findByRole("combobox", { name: "问题大类" }));

    expect(await screen.findByRole("option", { name: "账号登录(问题反馈)" })).toBeInTheDocument();
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

  it("allows a partial ordinary-child draft before a parent is available", async () => {
    render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "新建问题小类" }));
    const question = await screen.findByRole("textbox", { name: "问题小类" });
    fireEvent.change(question, { target: { value: "待选择父类的草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "暂存草稿" }));

    await waitFor(() =>
      expect(mockedApi.createKnowledgeDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          parent_id: null,
          question: "待选择父类的草稿",
          knowledge_base_ids: []
        })
      )
    );
  });

  it("shows private drafts and recent intelligent-generation batches", async () => {
    mockedApi.listKnowledgeDrafts.mockResolvedValue([draft]);
    mockedApi.listIngestionBatches.mockResolvedValue([ingestionBatch]);
    render(<ContentSubmissionPage />);

    fireEvent.click(await screen.findByRole("tab", { name: "我的草稿 (1)" }));
    expect(await screen.findByText("待补充的问题")).toBeInTheDocument();
    expect(screen.getByText("手动保存")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "快速上传" }));
    expect(await screen.findByText("最近智能生成批次")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    await waitFor(() => expect(mockedApi.getIngestionBatch).toHaveBeenCalledWith("batch-1"));
  });

  it("does not show an empty action column for uploads without resubmission actions", async () => {
    mockedApi.listMyContentSubmissions.mockResolvedValue([
      {
        ...rejectedSubmission,
        status: "published",
        targets: [{ ...rejectedSubmission.targets[0], status: "approved" }]
      }
    ]);
    render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "我的上传" }));
    await screen.findByText("账号登录");

    expect(screen.queryByRole("columnheader", { name: "操作" })).not.toBeInTheDocument();
  });

  it("shows uploaded attachments immediately with a download link or image preview", async () => {
    mockedApi.uploadKnowledgeAttachment
      .mockResolvedValueOnce({
        id: "attachment-pdf",
        name: "manual.pdf",
        content_type: "application/pdf",
        size_bytes: 2048
      })
      .mockResolvedValueOnce({
        id: "attachment-png",
        name: "screenshot.png",
        content_type: "image/png",
        size_bytes: 1024
      });
    const { container } = render(<ContentSubmissionPage />);

    await screen.findByRole("button", { name: "上传附件" });

    fireEvent.change(container.querySelector("input[type=file]")!, {
      target: { files: [new File(["pdf"], "manual.pdf", { type: "application/pdf" })] }
    });
    await waitFor(() => expect(mockedApi.uploadKnowledgeAttachment).toHaveBeenCalledTimes(1));
    const pdfLink = await screen.findByRole("link", { name: "manual.pdf" });
    expect(pdfLink).toHaveAttribute(
      "href",
      "/api/v1/knowledge-content/attachments/attachment-pdf/download"
    );

    fireEvent.change(container.querySelector("input[type=file]")!, {
      target: { files: [new File(["png"], "screenshot.png", { type: "image/png" })] }
    });
    await waitFor(() => expect(mockedApi.uploadKnowledgeAttachment).toHaveBeenCalledTimes(2));
    const preview = await screen.findByRole("img", { name: "screenshot.png" });
    expect(preview).toHaveAttribute(
      "src",
      "/api/v1/knowledge-content/attachments/attachment-png/download"
    );
  });

  it("submits uploaded attachment ids together with the draft content", async () => {
    mockedApi.uploadKnowledgeAttachment.mockResolvedValueOnce({
      id: "attachment-1",
      name: "evidence.png",
      content_type: "image/png",
      size_bytes: 1024
    });
    const { container } = render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "新建问题小类" }));
    fireEvent.change(container.querySelector("input[type=file]")!, {
      target: { files: [new File(["png"], "evidence.png", { type: "image/png" })] }
    });
    await waitFor(() => expect(mockedApi.uploadKnowledgeAttachment).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("img", { name: "evidence.png" })).toBeInTheDocument();

    fireEvent.change(await screen.findByRole("textbox", { name: "问题小类" }), {
      target: { value: "如何找回密码？" }
    });
    fireEvent.click(screen.getByRole("button", { name: "暂存草稿" }));

    await waitFor(() =>
      expect(mockedApi.createKnowledgeDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          question: "如何找回密码？",
          attachments: ["attachment-1"]
        })
      )
    );
  });

  it("OCRs an image manually attached to a forwarded chat card before creating a fast-upload batch", async () => {
    render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "快速上传" }));
    const image = new File(["image"], "forwarded-card.png", { type: "image/png" });
    fireEvent.paste(screen.getByRole("textbox", { name: "快速上传聊天内容" }), {
      clipboardData: {
        getData: (type: string) =>
          type === "text/plain"
            ? "Edward 8-17 11:28\n[图片]\n\n宋承臻(融航-咨询专员02) 8-17 11:30\n我们反馈核实下"
            : "",
        items: [],
        files: []
      }
    });
    fireEvent.click(
      screen.getByRole("button", { name: "图片占位符（点击后可粘贴或选择图片）" })
    );
    fireEvent.change(screen.getByLabelText("选择聊天图片"), { target: { files: [image] } });
    fireEvent.click(screen.getByRole("button", { name: "智能生成草稿" }));

    await waitFor(() => expect(mockedApi.recognizeConversationImage).toHaveBeenCalledWith(image));
    await waitFor(() =>
      expect(mockedApi.createIngestionBatch).toHaveBeenCalledWith([
        {
          speaker: "Edward",
          role: "customer",
          body: "资金账户可用余额低于最小预留",
          sent_at: null
        },
        {
          speaker: "宋承臻(融航-咨询专员02)",
          role: "ours",
          body: "我们反馈核实下",
          sent_at: null
        }
      ])
    );
  });
});
