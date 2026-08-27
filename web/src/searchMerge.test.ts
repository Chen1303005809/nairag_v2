import { describe, expect, it } from "vitest";

import type { SearchResponse, SearchResult } from "./api/types";
import { mergeSearchResponses } from "./searchMerge";

function result(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    result_item_id: "result-1",
    rank: 1,
    score: 0.6,
    hybrid_score: 0.6,
    rerank_score: null,
    selection_stage: "hybrid",
    helpful_count_at_search: 3,
    child_id: "child-1",
    knowledge_base_id: "knowledge-base-1",
    knowledge_base_name: "支持知识库",
    child_revision_id: "revision-1",
    question: "如何登录？",
    response_content: "请检查账号状态。",
    question_variants: [],
    follow_up_guidance: null,
    question_type: null,
    business_object: null,
    purpose: null,
    customer_type: null,
    feature_explanation: null,
    example: null,
    attachments: [],
    web_links: [],
    helpful_count: 3,
    match_reason: "hybrid_dense_bm25",
    matched_field: "question",
    ...overrides
  };
}

function response(
  eventId: string,
  parentId: string,
  child: SearchResult
): SearchResponse {
  return {
    search_event_id: eventId,
    query_mode: "text",
    no_match: false,
    no_match_guidance: null,
    degraded: false,
    degradation_reasons: [],
    groups: [
      {
        parent_id: parentId,
        parent_name: parentId,
        canonical_keyword: parentId,
        children: [child]
      }
    ]
  };
}

describe("mergeSearchResponses", () => {
  it("uses comprehensive score, helpful snapshot, then original rank for duplicate items", () => {
    const merged = mergeSearchResponses(
      ["第一个问法", "第二个问法"],
      [
        response("event-1", "parent-1", result({ rank: 4 })),
        response("event-2", "parent-1", result({ rank: 2, result_item_id: "result-2" }))
      ]
    );

    const child = merged.groups[0].children[0];
    expect(child.rank).toBe(2);
    expect(child.search_event_id).toBe("event-2");
    expect(child.matched_queries).toEqual(["第一个问法", "第二个问法"]);
  });

  it("applies the same tie-breaker when ordering parent groups", () => {
    const merged = mergeSearchResponses(
      ["问法一", "问法二"],
      [
        response("event-1", "parent-later", result({ rank: 3 })),
        response(
          "event-2",
          "parent-first",
          result({
            rank: 1,
            result_item_id: "result-2",
            child_id: "child-2",
            child_revision_id: "revision-2"
          })
        )
      ]
    );

    expect(merged.groups.map((group) => group.parent_id)).toEqual([
      "parent-first",
      "parent-later"
    ]);
  });
});
