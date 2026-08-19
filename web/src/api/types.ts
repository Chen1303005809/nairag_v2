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
