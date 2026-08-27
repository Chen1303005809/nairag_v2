import type {
  ConversationSearchResponse,
  ConversationSearchResult,
  SearchResponse
} from "./api/types";

interface MergedItem {
  item: ConversationSearchResult;
  key: string;
}

/**
 * Merge several single-query search responses into one unified,
 * deduplicated conversation-search result.
 */
export function mergeSearchResponses(
  queries: string[],
  responses: SearchResponse[]
): ConversationSearchResponse {
  const merged = new Map<string, MergedItem>();
  const parentGroups = new Map<string, ConversationSearchResponse["groups"][number]>();

  responses.forEach((response, responseIndex) => {
    const query = queries[responseIndex] ?? "";
    response.groups.forEach((group) => {
      if (!parentGroups.has(group.parent_id)) {
        parentGroups.set(group.parent_id, {
          parent_id: group.parent_id,
          parent_name: group.parent_name,
          canonical_keyword: group.canonical_keyword,
          children: []
        });
      }
      group.children.forEach((child) => {
        const key = `${child.child_revision_id}:${child.knowledge_base_id}`;
        const existing = merged.get(key);
        const withQuery: ConversationSearchResult = {
          ...child,
          search_event_id: response.search_event_id,
          matched_queries: [query]
        };
        if (!existing) {
          merged.set(key, { item: withQuery, key });
          parentGroups.get(group.parent_id)?.children.push(withQuery);
          return;
        }
        if (query && !existing.item.matched_queries.includes(query)) {
          existing.item.matched_queries.push(query);
        }
        if (
          child.score > existing.item.score ||
          (child.score === existing.item.score &&
            (child.helpful_count_at_search > existing.item.helpful_count_at_search ||
              (child.helpful_count_at_search === existing.item.helpful_count_at_search &&
                child.rank < existing.item.rank)))
        ) {
          Object.assign(existing.item, {
            ...child,
            search_event_id: response.search_event_id,
            matched_queries: existing.item.matched_queries
          });
        }
      });
    });
  });

  const groups = Array.from(parentGroups.values());
  groups.forEach((group) => {
    group.children.sort(
      (left, right) =>
        right.score - left.score ||
        right.helpful_count_at_search - left.helpful_count_at_search ||
        left.rank - right.rank
    );
  });
  groups.sort(
    (left, right) => {
      const bestLeft = left.children[0];
      const bestRight = right.children[0];
      return (
        bestRight.score - bestLeft.score ||
        bestRight.helpful_count_at_search - bestLeft.helpful_count_at_search ||
        bestLeft.rank - bestRight.rank
      );
    }
  );

  const anyNoMatch = responses.length > 0 && responses.every((response) => response.no_match);
  const degradationReasons = Array.from(
    new Set(responses.flatMap((response) => response.degradation_reasons))
  );
  return {
    queries,
    total_candidates: queries.length,
    no_query_guidance: queries.length === 0 ? "未发现待查询问题" : null,
    no_match: anyNoMatch,
    no_match_guidance: anyNoMatch ? responses[0].no_match_guidance : null,
    degraded: degradationReasons.length > 0,
    degradation_reasons: degradationReasons,
    groups
  };
}
