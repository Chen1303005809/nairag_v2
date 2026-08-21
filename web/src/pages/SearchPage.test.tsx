import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type {
  ConversationSearchResponse,
  KnowledgeBase,
  OcrRecognition,
  SearchResponse
} from "../api/types";
import { SearchPage } from "./SearchPage";

vi.mock("../api/client", () => ({
  api: {
    listKnowledgeBases: vi.fn(),
    recognizeSearchImage: vi.fn(),
    recognizeConversationImage: vi.fn(),
    search: vi.fn(),
    conversationSearch: vi.fn(),
    submitHelpfulFeedback: vi.fn()
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

const recognition: OcrRecognition = {
  text: "账号 登录失败",
  keywords: ["账号", "登录"],
  confidence: 0.97,
  model_version: "PP-OCRv6_medium",
  recognition_token: "ocr-ticket"
};

const noMatchResponse: SearchResponse = {
  search_event_id: "event-1",
  query_mode: "image",
  no_match: true,
  no_match_guidance: "未找到足够相关的知识，请转研发查询。",
  groups: []
};

const conversationNoQueryResponse: ConversationSearchResponse = {
  queries: [],
  total_candidates: 0,
  no_query_guidance: "未发现待查询问题",
  no_match: false,
  no_match_guidance: null,
  groups: []
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.recognizeSearchImage.mockResolvedValue(recognition);
  mockedApi.recognizeConversationImage.mockResolvedValue(recognition);
  mockedApi.search.mockResolvedValue(noMatchResponse);
  mockedApi.conversationSearch.mockResolvedValue(conversationNoQueryResponse);
});

afterEach(() => {
  cleanup();
});

describe("SearchPage OCR", () => {
  it("allows an image-only search after a trusted OCR recognition", async () => {
    const { container } = render(<SearchPage />);
    await waitFor(() => expect(mockedApi.listKnowledgeBases).toHaveBeenCalled());

    const uploadInput = container.querySelector("input[type=file]");
    expect(uploadInput).not.toBeNull();
    fireEvent.change(uploadInput!, {
      target: {
        files: [new File(["image"], "query.png", { type: "image/png" })]
      }
    });

    expect(await screen.findByText("账号 登录失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "向量检索" }));

    await waitFor(() =>
      expect(mockedApi.search).toHaveBeenCalledWith("vector", "", undefined, {}, "ocr-ticket")
    );
  });

  it("recognizes an image pasted from the clipboard", async () => {
    render(<SearchPage />);
    await waitFor(() => expect(mockedApi.listKnowledgeBases).toHaveBeenCalled());

    const image = new File(["image"], "clipboard.png", { type: "image/png" });
    fireEvent.paste(window, {
      clipboardData: {
        items: [
          {
            kind: "file",
            type: "image/png",
            getAsFile: () => image
          }
        ],
        files: [image]
      }
    });

    await waitFor(() => expect(mockedApi.recognizeSearchImage).toHaveBeenCalledWith(image));
    expect(await screen.findByText("账号 登录失败")).toBeInTheDocument();
  });

  it("parses a pasted conversation and requests assisted search", async () => {
    render(<SearchPage />);
    await waitFor(() => expect(mockedApi.listKnowledgeBases).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: /快速检索/ }));
    fireEvent.paste(screen.getByRole("textbox", { name: "快速检索聊天内容" }), {
      clipboardData: {
        getData: (type: string) =>
          type === "text/plain"
            ? "张客户 09:30\n登录一直失败怎么办？\n\n融航-李支持 09:31\n我需要先查询一下。"
            : "",
        items: [],
        files: []
      }
    });
    fireEvent.click(screen.getByRole("button", { name: /提取查询并检索/ }));

    await waitFor(() =>
      expect(mockedApi.conversationSearch).toHaveBeenCalledWith(
        [
          {
            speaker: "张客户",
            role: "customer",
            body: "登录一直失败怎么办？",
            sent_at: null
          },
          {
            speaker: "融航-李支持",
            role: "ours",
            body: "我需要先查询一下。",
            sent_at: null
          }
        ],
        undefined
      )
    );
    expect((await screen.findAllByText("未发现待查询问题")).length).toBeGreaterThan(0);
  });

  it("OCRs an image manually attached to a forwarded chat card before assisted search", async () => {
    render(<SearchPage />);
    await waitFor(() => expect(mockedApi.listKnowledgeBases).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: /快速检索/ }));
    const image = new File(["image"], "forwarded-card.png", { type: "image/png" });
    fireEvent.paste(screen.getByRole("textbox", { name: "快速检索聊天内容" }), {
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
    fireEvent.click(screen.getByRole("button", { name: /提取查询并检索/ }));

    await waitFor(() => expect(mockedApi.recognizeConversationImage).toHaveBeenCalledWith(image));
    await waitFor(() =>
      expect(mockedApi.conversationSearch).toHaveBeenCalledWith(
        [
          {
            speaker: "Edward",
            role: "customer",
            body: "账号 登录失败",
            sent_at: null
          },
          {
            speaker: "宋承臻(融航-咨询专员02)",
            role: "ours",
            body: "我们反馈核实下",
            sent_at: null
          }
        ],
        undefined
      )
    );
  });
});
