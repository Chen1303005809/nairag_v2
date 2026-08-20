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
  query_mode: "text" | "image" | "mixed";
  no_match: boolean;
  no_match_guidance: string | null;
  groups: SearchGroup[];
}

export interface HelpfulFeedbackResponse {
  accepted: boolean;
  already_recorded: boolean;
  helpful_count: number;
}
