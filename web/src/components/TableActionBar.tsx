import type { ReactNode } from "react";

interface TableActionBarProps {
  children: ReactNode;
}

export function TableActionBar({ children }: TableActionBarProps): JSX.Element {
  return <div className="table-action-bar">{children}</div>;
}
