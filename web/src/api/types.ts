export type UserRole = "normal_user" | "review_admin" | "system_admin";

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: UserRole;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface LoginResponse {
  user: User;
}

export interface TemporaryPasswordResponse {
  user: User;
  temporary_password: string;
}

export interface KnowledgeBase {
  id: string;
  logical_key: string;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ManagedKnowledgeBase extends KnowledgeBase {
  current_collection_generation: number;
  current_physical_collection_name: string;
  reviewer_count: number;
}

export interface ReviewerAccount {
  id: string;
  username: string;
  display_name: string;
  is_active: boolean;
}

export interface ReviewerAssignment {
  knowledge_base_id: string;
  reviewer: ReviewerAccount;
  assigned_by_user_id: string;
  assigned_at: string;
}

export type ParentLexicalRuleType = "alias" | "regex";
export type ReviewSubmissionKind = "parent_with_primary" | "child";
export type ReviewSubmissionStatus =
  | "pending_review"
  | "indexing"
  | "published"
  | "rejected"
  | "index_failed";
export type ReviewTargetStatus =
  | "pending_review"
  | "approved"
  | "rejected"
  | "indexing"
  | "published"
  | "index_failed";
export type ReviewDecisionKind = "approved" | "rejected";
export type ChildPublicationStatus = "pending" | "published" | "archived";

export interface ParentLexicalRuleInput {
  rule_type: ParentLexicalRuleType;
  rule_value: string;
}

export interface ParentContentInput {
  name: string;
  canonical_keyword: string;
  lexical_rules: ParentLexicalRuleInput[];
}

export interface EvidenceAttachment {
  id: string;
  name: string;
  content_type: string;
  size_bytes: number;
}

export interface WebLinkInput {
  title: string;
  url: string;
}

export interface ChildContentInput {
  question: string;
  response_content: string;
  question_variants: string[];
  follow_up_guidance?: string | null;
  question_type?: string | null;
  business_object?: string | null;
  purpose?: string | null;
  customer_type?: string | null;
  feature_explanation?: string | null;
  example?: string | null;
  internal_notes?: string | null;
  attachments: string[];
  web_links: WebLinkInput[];
}

export interface AvailableKnowledgeBase {
  id: string;
  logical_key: string;
  name: string;
}

export interface AvailableParent {
  id: string;
  name: string;
  canonical_keyword: string;
  primary_child_id: string;
  available_knowledge_bases: AvailableKnowledgeBase[];
}

export interface ReviewActor {
  id: string;
  username: string;
  display_name: string;
}

export interface ReviewSubmissionTarget extends AvailableKnowledgeBase {
  status: ReviewTargetStatus;
  review_comment: string | null;
  reviewer: ReviewActor | null;
  reviewed_at: string | null;
  review_decision: ReviewDecisionKind | null;
}

export interface ReviewSubmission {
  id: string;
  submission_kind: ReviewSubmissionKind;
  status: ReviewSubmissionStatus;
  parent_id: string;
  parent_revision_id: string | null;
  child_id: string;
  child_revision_id: string;
  title: string;
  submitter: ReviewActor;
  targets: ReviewSubmissionTarget[];
  submitted_at: string;
  parent_revision: ReviewParentRevision | null;
  child_revision: ReviewChildRevision | null;
}

export interface ReviewParentRevision {
  id: string;
  revision_number: number;
  name: string;
  canonical_keyword: string;
  lexical_rules: ParentLexicalRuleInput[];
}

export interface ReviewChildRevision extends Omit<ChildContentInput, "attachments"> {
  id: string;
  revision_number: number;
  attachments: EvidenceAttachment[];
}

export interface ReviewQueueItem {
  id: string;
  review_submission_id: string;
  submission_kind: ReviewSubmissionKind;
  submission_status: ReviewSubmissionStatus;
  target_status: ReviewTargetStatus;
  parent_id: string;
  parent_revision_id: string | null;
  child_id: string;
  child_revision_id: string;
  knowledge_base_id: string;
  knowledge_base: AvailableKnowledgeBase;
  submitter: ReviewActor;
  reviewer: ReviewActor | null;
  review_decision: ReviewDecisionKind | null;
  review_comment: string | null;
  parent_revision: ReviewParentRevision | null;
  child_revision: ReviewChildRevision;
  submitted_at: string;
  reviewed_at: string | null;
}

export interface ManagedKnowledgeEntry {
  child_id: string;
  parent_id: string;
  parent_name: string;
  is_primary: boolean;
  knowledge_base: AvailableKnowledgeBase;
  status: ChildPublicationStatus;
  child_revision: ReviewChildRevision;
  uploaded_by: ReviewActor;
  uploaded_at: string;
  embedded_at: string | null;
  archived_at: string | null;
}

export interface EditableContentEntry {
  child_id: string;
  parent_id: string;
  parent_name: string;
  is_primary: boolean;
  knowledge_bases: AvailableKnowledgeBase[];
  parent_revision: ReviewParentRevision | null;
  child_revision: ReviewChildRevision;
}

export interface ReviewDecision {
  id: string;
  review_submission_id: string;
  knowledge_base_id: string;
  decision: ReviewDecisionKind;
  comment: string | null;
  decided_by_user_id: string;
  decided_at: string;
}

export interface SearchResult {
  result_item_id: string;
  rank: number;
  score: number;
  hybrid_score: number | null;
  rerank_score: number | null;
  selection_stage:
    | "hybrid"
    | "rerank"
    | "llm"
    | "score_fallback"
    | "keyword_fallback"
    | "field_filter"
    | "legacy";
  helpful_count_at_search: number;
  child_id: string;
  knowledge_base_id: string;
  knowledge_base_name: string;
  child_revision_id: string;
  question: string;
  response_content: string;
  question_variants: string[];
  follow_up_guidance: string | null;
  question_type: string | null;
  business_object: string | null;
  purpose: string | null;
  customer_type: string | null;
  feature_explanation: string | null;
  example: string | null;
  attachments: EvidenceAttachment[];
  web_links: WebLinkInput[];
  helpful_count: number;
  match_reason: string;
  matched_field: string | null;
}

export interface SearchFilters {
  parent_type?: string;
  question_type?: string;
  business_object?: string;
  purpose?: string;
  customer_type?: string;
}

export type SearchRetrievalMode = "vector" | "field_filter";

export interface OcrRecognition {
  text: string;
  keywords: string[];
  confidence: number;
  model_version: string;
  recognition_token: string;
}

export interface SearchGroup {
  parent_id: string;
  parent_name: string;
  canonical_keyword: string;
  children: SearchResult[];
}

export interface SearchResponse {
  search_event_id: string;
  search_interaction_id: string | null;
  query_mode: "text" | "image" | "mixed";
  no_match: boolean;
  no_match_guidance: string | null;
  degraded: boolean;
  degradation_reasons: string[];
  groups: SearchGroup[];
}

export interface NormalizedMessageInput {
  speaker: string;
  role: "customer" | "ours";
  body: string;
  sent_at?: string | null;
}

export interface ConversationSearchResult extends SearchResult {
  search_event_id: string;
  matched_queries: string[];
}

export interface ConversationSearchGroup {
  parent_id: string;
  parent_name: string;
  canonical_keyword: string;
  children: ConversationSearchResult[];
}

export interface ConversationSearchResponse {
  search_interaction_id: string | null;
  queries: string[];
  total_candidates: number;
  no_query_guidance: string | null;
  no_match: boolean;
  no_match_guidance: string | null;
  degraded: boolean;
  degradation_reasons: string[];
  groups: ConversationSearchGroup[];
}

export type KnowledgeDraftSource = "manual_saved" | "intelligent_generated";

export interface KnowledgeDraft {
  id: string;
  source: KnowledgeDraftSource;
  parent_id: string | null;
  ingestion_batch_id: string | null;
  question: string | null;
  response_content: string | null;
  question_variants: string[];
  follow_up_guidance: string | null;
  question_type: string | null;
  business_object: string | null;
  purpose: string | null;
  customer_type: string | null;
  feature_explanation: string | null;
  example: string | null;
  internal_notes: string | null;
  attachments: EvidenceAttachment[];
  web_links: WebLinkInput[];
  knowledge_base_ids: string[];
  source_hash: string | null;
  extracted_at: string | null;
  model_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDraftInput {
  parent_id?: string | null;
  question?: string | null;
  response_content?: string | null;
  question_variants?: string[];
  follow_up_guidance?: string | null;
  question_type?: string | null;
  business_object?: string | null;
  purpose?: string | null;
  customer_type?: string | null;
  feature_explanation?: string | null;
  example?: string | null;
  internal_notes?: string | null;
  attachments?: string[];
  web_links?: WebLinkInput[];
  knowledge_base_ids?: string[];
}

export type IngestionBatchStatus =
  | "processing"
  | "completed"
  | "completed_with_warnings"
  | "failed";

export interface IngestionBatch {
  id: string;
  status: IngestionBatchStatus;
  message_count: number;
  source_hash: string;
  generated_count: number;
  rejected_count: number;
  rejection_reasons: Array<{ topic: string; reason: string }>;
  model_version: string | null;
  last_error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface IngestionBatchDetail extends IngestionBatch {
  drafts: KnowledgeDraft[];
}

export interface HelpfulFeedbackResponse {
  accepted: boolean;
  already_recorded: boolean;
  helpful_count: number;
}

export type SearchInteractionType = "vector" | "quick_search";

export type SearchAnnotationResultLabel =
  | "high_score_irrelevant"
  | "low_score_relevant"
  | "normal"
  | "other";

export interface SearchAnnotationResultFeedbackInput {
  search_result_item_id: string;
  feedback_type: SearchAnnotationResultLabel;
  other_note?: string;
}

export interface SearchAnnotationResultFeedback {
  search_result_item_id: string;
  feedback_type: SearchAnnotationResultLabel;
  other_note: string | null;
}

export interface SearchAnnotationReviewResponse {
  accepted: boolean;
  already_recorded: boolean;
  reviewed_result_count: number;
  submitted_at: string;
  result_feedbacks: SearchAnnotationResultFeedback[];
}

export interface AnnotationFeedbackFilters {
  annotated_from?: string;
  annotated_to?: string;
  knowledge_base_id?: string;
  query_keyword?: string;
}

export interface AnnotationFeedbackListFilters extends AnnotationFeedbackFilters {
  feedback_type?: SearchAnnotationResultLabel;
  page?: number;
  page_size?: number;
}

export interface AnnotationFeedbackUser {
  id: string;
  username: string;
  display_name: string;
}

export interface AnnotationFeedbackSummary {
  completed_review_count: number;
  annotated_result_count: number;
  high_score_irrelevant_count: number;
  low_score_relevant_count: number;
  normal_count: number;
  other_count: number;
}

export interface AnnotationFeedbackListItem {
  id: string;
  submitted_by: AnnotationFeedbackUser;
  interaction_type: SearchInteractionType;
  queries: string[];
  target_knowledge_base_id: string | null;
  target_knowledge_base_name: string | null;
  high_score_irrelevant_count: number;
  low_score_relevant_count: number;
  normal_count: number;
  other_count: number;
  searched_at: string;
  submitted_at: string;
  result_count: number;
}

export interface AnnotationFeedbackPage {
  items: AnnotationFeedbackListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AnnotationFeedbackResultDetail {
  result_item_id: string;
  rank: number;
  score: number;
  hybrid_score: number | null;
  rerank_score: number | null;
  selection_stage: string;
  matched_field: string | null;
  parent_name: string;
  question: string;
  knowledge_base_id: string;
  knowledge_base_name: string;
  matched_queries: string[];
  feedback_type: SearchAnnotationResultLabel;
  other_note: string | null;
}

export interface AnnotationFeedbackQueryDetail {
  search_event_id: string;
  query_order: number;
  query_text: string | null;
  ocr_text: string | null;
  no_match: boolean;
  results: AnnotationFeedbackResultDetail[];
}

export interface AnnotationFeedbackDetail extends AnnotationFeedbackListItem {
  no_match: boolean;
  degraded: boolean;
  degradation_reasons: string[];
  query_details: AnnotationFeedbackQueryDetail[];
}
