import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { KnowledgeBase, OcrRecognition, SearchResponse } from "../api/types";
import { SearchPage } from "./SearchPage";

vi.mock("../api/client", () => ({
  api: {
    listKnowledgeBases: vi.fn(),
    recognizeSearchImage: vi.fn(),
    search: vi.fn(),
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

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.recognizeSearchImage.mockResolvedValue(recognition);
  mockedApi.search.mockResolvedValue(noMatchResponse);
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
});
