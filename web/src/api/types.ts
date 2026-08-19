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

export interface ParentLexicalRuleInput {
  rule_type: ParentLexicalRuleType;
  rule_value: string;
}

export interface ParentContentInput {
  name: string;
  canonical_keyword: string;
  lexical_rules: ParentLexicalRuleInput[];
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

export interface ReviewSubmissionTarget extends AvailableKnowledgeBase {
  status: ReviewTargetStatus;
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
  targets: ReviewSubmissionTarget[];
  submitted_at: string;
}

export interface ReviewParentRevision {
  id: string;
  revision_number: number;
  name: string;
  canonical_keyword: string;
  lexical_rules: ParentLexicalRuleInput[];
}

export interface ReviewChildRevision extends ChildContentInput {
  id: string;
  revision_number: number;
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
  submitter: {
    id: string;
    username: string;
    display_name: string;
  };
  parent_revision: ReviewParentRevision | null;
  child_revision: ReviewChildRevision;
  submitted_at: string;
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
