import { Tag } from "antd";

import type { UserRole } from "../api/types";

const roleLabels: Record<UserRole, string> = {
  normal_user: "普通用户",
  review_admin: "审查管理员",
  system_admin: "系统管理员"
};

const roleColors: Record<UserRole, string> = {
  normal_user: "default",
  review_admin: "blue",
  system_admin: "purple"
};

export function roleLabel(role: UserRole): string {
  return roleLabels[role];
}

export function RoleTag({ role }: { role: UserRole }): JSX.Element {
  return <Tag color={roleColors[role]}>{roleLabel(role)}</Tag>;
}
