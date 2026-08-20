import { describe, expect, it } from "vitest";

import { uniqueTableFilterOptions } from "./tableFilters";

describe("uniqueTableFilterOptions", () => {
  it("deduplicates options by stable value and orders their labels", () => {
    const records = [
      { knowledgeBases: [{ id: "kb-2", name: "运营知识库" }] },
      { knowledgeBases: [{ id: "kb-1", name: "产品知识库" }, { id: "kb-2", name: "运营知识库" }] }
    ];

    expect(
      uniqueTableFilterOptions(records, (record) =>
        record.knowledgeBases.map((knowledgeBase) => ({ text: knowledgeBase.name, value: knowledgeBase.id }))
      )
    ).toEqual([
      { text: "产品知识库", value: "kb-1" },
      { text: "运营知识库", value: "kb-2" }
    ]);
  });
});
