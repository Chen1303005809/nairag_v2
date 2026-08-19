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

