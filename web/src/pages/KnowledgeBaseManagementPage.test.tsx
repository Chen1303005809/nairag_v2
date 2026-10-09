import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { ManagedKnowledgeBase, ReviewerAssignment, User } from "../api/types";
import { KnowledgeBaseManagementPage } from "./KnowledgeBaseManagementPage";

vi.mock("../api/client", () => ({
  api: {
    listManagedKnowledgeBases: vi.fn(),
    listManagedKnowledgeEntries: vi.fn(),
    getKnowledgeContentTaxonomy: vi.fn(),
    createKnowledgeTaxonomyOption: vi.fn(),
    renameKnowledgeTaxonomyOption: vi.fn(),
    listUsers: vi.fn(),
    listKnowledgeBaseReviewers: vi.fn(),
    createKnowledgeBase: vi.fn(),
    updateKnowledgeBase: vi.fn(),
    assignKnowledgeBaseReviewer: vi.fn(),
    unassignKnowledgeBaseReviewer: vi.fn(),
    archiveManagedKnowledge: vi.fn()
  }
}));

const mockedApi = vi.mocked(api);

afterEach(() => {
  cleanup();
});

const knowledgeBase: ManagedKnowledgeBase = {
  id: "knowledge-base-1",
  logical_key: "support",
  name: "支持知识库",
  description: null,
  is_active: true,
  current_collection_generation: 1,
  current_physical_collection_name: "nairag_support_g1",
  reviewer_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const normalUser: User = {
  id: "user-1",
  username: "new-reviewer",
  display_name: "新审查管理员",
  role: "normal_user",
  is_active: true,
  must_change_password: false,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

const reviewerUser: User = { ...normalUser, role: "review_admin" };

const taxonomy = {
  parent_types: ["问题反馈", "需求提交", "配置项咨询"],
  question_types: ["功能故障类"],
  business_objects: ["对应平台使用说明书"],
  purposes: ["企业微信咨询"],
  customer_types: ["个人客户"],
  search_filters: {
    question_types: ["功能故障类"],
    business_objects: ["对应平台使用说明书"],
    purposes: ["企业微信咨询"],
    customer_types: ["个人客户"]
  }
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.listManagedKnowledgeBases.mockResolvedValue([knowledgeBase]);
  mockedApi.listManagedKnowledgeEntries.mockResolvedValue([]);
  mockedApi.getKnowledgeContentTaxonomy.mockResolvedValue(taxonomy);
  mockedApi.listUsers.mockResolvedValue([normalUser]);
  mockedApi.listKnowledgeBaseReviewers.mockResolvedValue([] as ReviewerAssignment[]);
});

describe("KnowledgeBaseManagementPage reviewer authorization", () => {
  it("refreshes reviewer candidates after a user is promoted", async () => {
    let currentUsers: User[] = [normalUser];
    mockedApi.listUsers.mockImplementation(async () => currentUsers);

    render(<KnowledgeBaseManagementPage />);
    await waitFor(() => expect(mockedApi.listUsers).toHaveBeenCalledWith(false));

    currentUsers = [reviewerUser];
    fireEvent.click(await screen.findByRole("button", { name: "审查授权" }));

    await waitFor(() => expect(mockedApi.listKnowledgeBaseReviewers).toHaveBeenCalledWith(knowledgeBase.id));
    await waitFor(() => expect(mockedApi.listUsers).toHaveBeenCalledTimes(2));

    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(await screen.findByText("新审查管理员（new-reviewer）")).toBeInTheDocument();
  });

  it("adds a dynamic taxonomy option from the field options tab", async () => {
    const updatedTaxonomy = {
      ...taxonomy,
      question_types: ["功能故障类", "新问题类型"],
      search_filters: { ...taxonomy.search_filters, question_types: ["功能故障类", "新问题类型"] }
    };
    mockedApi.createKnowledgeTaxonomyOption.mockResolvedValue(updatedTaxonomy);

    render(<KnowledgeBaseManagementPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "字段选项" }));
    expect(await screen.findByText("1. 功能故障类")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /添加选项/ })[0]);

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: "选项内容" }), {
      target: { value: "新问题类型" }
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /保\s*存/ }));

    await waitFor(() =>
      expect(mockedApi.createKnowledgeTaxonomyOption).toHaveBeenCalledWith(
        "question_types",
        "新问题类型"
      )
    );
    expect(await screen.findByText("2. 新问题类型")).toBeInTheDocument();
  });

  it("renames an existing taxonomy option in place", async () => {
    const updatedTaxonomy = {
      ...taxonomy,
      question_types: ["功能异常类"],
      search_filters: { ...taxonomy.search_filters, question_types: ["功能异常类", "功能故障类"] }
    };
    mockedApi.renameKnowledgeTaxonomyOption.mockResolvedValue(updatedTaxonomy);

    render(<KnowledgeBaseManagementPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "字段选项" }));
    expect(await screen.findByText("1. 功能故障类")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /编辑/ })[0]);

    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByRole("textbox", { name: "选项内容" });
    fireEvent.change(input, { target: { value: "功能异常类" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /保\s*存/ }));

    await waitFor(() =>
      expect(mockedApi.renameKnowledgeTaxonomyOption).toHaveBeenCalledWith(
        "question_types",
        "功能故障类",
        "功能异常类"
      )
    );
    expect(await screen.findByText("1. 功能异常类")).toBeInTheDocument();
  });
});
