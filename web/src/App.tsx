import { LogoutOutlined, UserOutlined } from "@ant-design/icons";
import {
  Alert,
  App as AntApp,
  Button,
  Dropdown,
  Layout,
  Spin,
  Tabs,
  Typography,
  message
} from "antd";
import { Suspense, lazy, useEffect, useState } from "react";

import { api, ApiError } from "./api/client";
import type { User } from "./api/types";
import { RoleTag } from "./components/role";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { LoginPage } from "./pages/LoginPage";

const AccountManagementPage = lazy(async () => {
  const page = await import("./pages/AccountManagementPage");
  return { default: page.AccountManagementPage };
});

const KnowledgeBaseManagementPage = lazy(async () => {
  const page = await import("./pages/KnowledgeBaseManagementPage");
  return { default: page.KnowledgeBaseManagementPage };
});

const ReviewerKnowledgeBasesPage = lazy(async () => {
  const page = await import("./pages/ReviewerKnowledgeBasesPage");
  return { default: page.ReviewerKnowledgeBasesPage };
});

function App(): JSX.Element {
  const [user, setUser] = useState<User>();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadSession = async (): Promise<void> => {
      try {
        setUser(await api.me());
      } catch (reason) {
        if (!(reason instanceof ApiError) || reason.status !== 401) {
          message.error(reason instanceof Error ? reason.message : "无法读取登录状态");
        }
      } finally {
        setLoading(false);
      }
    };
    void loadSession();
  }, []);

  const handleLogin = async ({ username, password }: { username: string; password: string }): Promise<void> => {
    const result = await api.login(username, password);
    setUser(result.user);
  };

  const handlePasswordChange = async (currentPassword: string, newPassword: string): Promise<void> => {
    const result = await api.changePassword(currentPassword, newPassword);
    setUser(result.user);
    message.success("密码已修改");
  };

  const logout = async (): Promise<void> => {
    try {
      await api.logout();
    } catch (reason) {
      if (!(reason instanceof ApiError) || reason.status !== 401) {
        message.error(reason instanceof Error ? reason.message : "退出登录失败");
        return;
      }
    }
    setUser(undefined);
  };

  if (loading) {
    return (
      <main className="loading-page">
        <Spin size="large" />
      </main>
    );
  }
  if (!user) {
    return <LoginPage onLogin={handleLogin} />;
  }
  if (user.must_change_password) {
    return <ChangePasswordPage onChangePassword={handlePasswordChange} />;
  }

  const menuItems = [
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      onClick: () => void logout()
    }
  ];

  return (
    <Layout className="application-layout">
      <Layout.Header className="application-header">
        <Typography.Title className="product-name" level={4}>
          Nairag 知识库
        </Typography.Title>
        <Dropdown menu={{ items: menuItems }}>
          <Button type="text" className="user-menu" icon={<UserOutlined />}>
            {user.display_name} <RoleTag role={user.role} />
          </Button>
        </Dropdown>
      </Layout.Header>
      <Layout.Content className="application-content">
        <AntApp>
          {user.role === "system_admin" ? (
            <Tabs
              defaultActiveKey="knowledge-bases"
              items={[
                {
                  key: "knowledge-bases",
                  label: "知识库管理",
                  children: (
                    <Suspense fallback={<Spin />}>
                      <KnowledgeBaseManagementPage />
                    </Suspense>
                  )
                },
                {
                  key: "accounts",
                  label: "账号管理",
                  children: (
                    <Suspense fallback={<Spin />}>
                      <AccountManagementPage />
                    </Suspense>
                  )
                }
              ]}
            />
          ) : user.role === "review_admin" ? (
            <Suspense fallback={<Spin />}>
              <ReviewerKnowledgeBasesPage />
            </Suspense>
          ) : (
            <Alert
              type="info"
              showIcon
              message="账号已准备就绪"
              description="知识投稿、审核与检索功能将在后续模块中开放。"
            />
          )}
        </AntApp>
      </Layout.Content>
    </Layout>
  );
}

export default App;
