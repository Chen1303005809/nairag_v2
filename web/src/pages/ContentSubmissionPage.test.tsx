import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type {
  AvailableParent,
  EditableContentEntry,
  IngestionBatch,
  KnowledgeBase,
  KnowledgeDraft,
  KnowledgeContentTaxonomy,
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
    getKnowledgeContentTaxonomy: vi.fn(),
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
  mockedApi.getKnowledgeContentTaxonomy.mockResolvedValue({
    parent_types: ["问题反馈"],
    question_types: ["功能故障类"],
    business_objects: ["基础知识与算法"],
    purposes: ["内部培训"],
    customer_types: ["个人客户"],
    search_filters: {
      question_types: ["功能故障类"],
      business_objects: ["基础知识与算法"],
      purposes: ["内部培训"],
      customer_types: ["个人客户"]
    }
  } satisfies KnowledgeContentTaxonomy);
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

  it("preserves renamed taxonomy values while editing existing content", async () => {
    const historicalValues = {
      question_type: "已改名的问题类型",
      business_object: "已改名的功能模块",
      purpose: "已改名的应用场景",
      customer_type: "已改名的客户类型"
    };
    const historicalSubmission = {
      ...rejectedSubmission,
      child_revision: {
        ...rejectedSubmission.child_revision!,
        ...historicalValues
      }
    };
    mockedApi.listMyContentSubmissions.mockResolvedValue([historicalSubmission]);
    mockedApi.getKnowledgeContentTaxonomy.mockResolvedValue({
      parent_types: ["问题反馈"],
      question_types: ["新问题类型"],
      business_objects: ["新功能模块"],
      purposes: ["新应用场景"],
      customer_types: ["新客户类型"],
      search_filters: {
        question_types: ["新问题类型", historicalValues.question_type],
        business_objects: ["新功能模块", historicalValues.business_object],
        purposes: ["新应用场景", historicalValues.purpose],
        customer_types: ["新客户类型", historicalValues.customer_type]
      }
    });

    render(<ContentSubmissionPage />);
    fireEvent.click(screen.getByRole("tab", { name: "我的上传" }));
    fireEvent.click(await screen.findByRole("button", { name: "编辑重提" }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(within(dialog).getByRole("combobox", { name: "问题类型" }));
    expect(await screen.findByRole("option", { name: historicalValues.question_type })).toBeInTheDocument();
    fireEvent.keyDown(within(dialog).getByRole("combobox", { name: "问题类型" }), {
      key: "Escape"
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "重新提交审核" }));

    await waitFor(() =>
      expect(mockedApi.resubmitRejectedChild).toHaveBeenCalledWith(
        "submission-1",
        expect.objectContaining(historicalValues),
        ["knowledge-base-1"]
      )
    );
  });

  it("opens the full details for an uploaded child revision without another request", async () => {
    render(<ContentSubmissionPage />);
    fireEvent.click(screen.getByRole("tab", { name: "我的上传" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看细则" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("提交内容全貌")).toBeInTheDocument();
    expect(within(dialog).getByText("如何找回密码？")).toBeInTheDocument();
    expect(within(dialog).getByText("请联系管理员。")).toBeInTheDocument();
    expect(mockedApi.listMyContentSubmissions).toHaveBeenCalledTimes(1);
  });

  it("shows parent and child details for an uploaded parent aggregate", async () => {
    mockedApi.listMyContentSubmissions.mockResolvedValue([
      {
        ...rejectedSubmission,
        submission_kind: "parent_with_primary",
        parent_revision_id: "parent-revision-1",
        parent_revision: {
          id: "parent-revision-1",
          revision_number: 1,
          name: "问题反馈",
          canonical_keyword: "账号登录",
          lexical_rules: [{ rule_type: "alias", rule_value: "登录问题" }]
        }
      }
    ]);
    render(<ContentSubmissionPage />);
    fireEvent.click(screen.getByRole("tab", { name: "我的上传" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看细则" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("问题大类关键词")).toBeInTheDocument();
    expect(within(dialog).getByText("账号登录")).toBeInTheDocument();
    expect(within(dialog).getByText("[alias] 登录问题")).toBeInTheDocument();
    expect(within(dialog).getByText("如何找回密码？")).toBeInTheDocument();
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

  it("keeps the detail action for uploads without resubmission actions", async () => {
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

    expect(screen.getByRole("columnheader", { name: "操作" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看细则" })).toBeInTheDocument();
  });

  it("keeps upload timestamps readable in a single line", async () => {
    render(<ContentSubmissionPage />);

    fireEvent.click(screen.getByRole("tab", { name: "我的上传" }));
    const timestampCell = await screen.findByRole("cell", { name: "2026/01/01 08:00:00" });
    const statusColumn = document.querySelectorAll(".ant-table colgroup col")[3];

    expect(timestampCell).toHaveStyle({ whiteSpace: "nowrap" });
    expect(statusColumn?.getAttribute("style")).toContain("width: 100px");
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
