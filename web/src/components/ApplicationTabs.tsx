import { Tabs } from "antd";
import type { TabsProps } from "antd";

type ApplicationTabsProps = Pick<TabsProps, "defaultActiveKey" | "items">;

export function ApplicationTabs({ defaultActiveKey, items }: ApplicationTabsProps): JSX.Element {
  return <Tabs defaultActiveKey={defaultActiveKey} items={items} destroyInactiveTabPane />;
}
