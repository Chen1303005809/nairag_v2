import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type {
  AttachmentImportBatch,
  AttachmentImportBatchDetail,
  KnowledgeBase,
  KnowledgeContentTaxonomy
} from "../api/types";
import { AttachmentImportTab } from "./AttachmentImportTab";

vi.mock("../api/client", () => ({
  api: {
    listAttachmentImportBatches: vi.fn(),
    getAttachmentImportBatch: vi.fn(),
    createAttachmentImportBatch: vi.fn(),
    retryAttachmentImportBatch: vi.fn(),
    deleteAttachmentImportBatch: vi.fn(),
    confirmAttachmentImportBatch: vi.fn(),
    knowledgeAttachmentDownloadUrl: vi.fn(
      (attachmentId: string) => `/api/v1/knowledge-content/attachments/${attachmentId}/download`
    )
  }
}));

const mockedApi = vi.mocked(api);

const taxonomy: KnowledgeContentTaxonomy = {
  parent_types: ["问题反馈"],
  question_types: ["功能故障类"],
  business_objects: ["平台使用说明"],
  purposes: ["企业微信咨询"],
  customer_types: ["个人客户"]
};

const knowledgeBase: KnowledgeBase = {
  id: "knowledge-base-1",
  logical_key: "support",
  name: "支持知识库",
  description: null,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

function batchFor(fileName: string): AttachmentImportBatch {
  return {
    id: `batch-${fileName}`,
    status: "ready",
    attachment: {
      id: `attachment-${fileName}`,
      name: fileName,
      content_type: "application/octet-stream",
      size_bytes: 10
    },
    warnings: [],
    image_count: 0,
    extracted_char_count: 10,
    model_version: "test-model",
    attempt_count: 1,
    last_error: null,
    expires_at: "2026-01-08T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:00:01Z",
    submitted_at: null,
    final_submission_id: null,
    final_parent_id: null
  };
}

function detailFor(fileName: string): AttachmentImportBatchDetail {
  const batch = batchFor(fileName);
  return {
    ...batch,
    proposal: {
      parent: {
        name: "问题反馈",
        canonical_keyword: fileName,
        aliases: []
      },
      children: [
        {
          id: `candidate-${fileName}`,
          question: `${fileName}怎么处理？`,
          response_content: "请按附件中的步骤操作。",
          question_variants: [],
          follow_up_guidance: null,
          question_type: "功能故障类",
          business_object: "平台使用说明",
          purpose: "企业微信咨询",
          customer_type: "个人客户",
          feature_explanation: null,
          example: null,
          internal_notes: null
        }
      ],
      recommended_primary_child_id: `candidate-${fileName}`,
      warnings: [],
      image_count: 0,
      similar_parents: []
    }
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listAttachmentImportBatches.mockResolvedValue([]);
  mockedApi.getAttachmentImportBatch.mockImplementation(async (batchId) =>
    detailFor(batchId.replace(/^batch-/, ""))
  );
});

afterEach(() => {
  cleanup();
});

describe("AttachmentImportTab", () => {
  it("creates independent batches in parallel for a multi-file selection", async () => {
    const pendingResolvers = new Map<string, (batch: AttachmentImportBatch) => void>();
    mockedApi.createAttachmentImportBatch.mockImplementation(
      (file) =>
        new Promise<AttachmentImportBatch>((resolve) => {
          pendingResolvers.set(file.name, () => resolve(batchFor(file.name)));
        })
    );

    render(
      <AttachmentImportTab
        taxonomy={taxonomy}
        knowledgeBases={[knowledgeBase]}
        availableParents={[]}
        onConfirmed={vi.fn(async () => undefined)}
      />
    );

    const fileInput = document.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();
    expect((fileInput as HTMLInputElement).multiple).toBe(true);

    const firstFile = new File(["first"], "first.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    });
    const secondFile = new File(["second"], "second.doc", {
      type: "application/msword"
    });
    fireEvent.change(fileInput!, { target: { files: [firstFile, secondFile] } });

    await waitFor(() => expect(mockedApi.createAttachmentImportBatch).toHaveBeenCalledTimes(2));
    expect(mockedApi.createAttachmentImportBatch).toHaveBeenNthCalledWith(1, firstFile);
    expect(mockedApi.createAttachmentImportBatch).toHaveBeenNthCalledWith(2, secondFile);
    expect(pendingResolvers).toHaveProperty("size", 2);

    pendingResolvers.get(firstFile.name)?.(batchFor(firstFile.name));
    pendingResolvers.get(secondFile.name)?.(batchFor(secondFile.name));

    await waitFor(() => expect(screen.getAllByText(firstFile.name).length).toBeGreaterThan(0));
    expect(screen.getAllByText(secondFile.name).length).toBeGreaterThan(0);
    expect(mockedApi.getAttachmentImportBatch).toHaveBeenCalledWith(`batch-${firstFile.name}`);
    expect(mockedApi.getAttachmentImportBatch).toHaveBeenCalledWith(`batch-${secondFile.name}`);
  });
});
