import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";

interface LoginValues {
  username: string;
  password: string;
}

interface LoginPageProps {
  onLogin: (values: LoginValues) => Promise<void>;
}

export function LoginPage({ onLogin }: LoginPageProps): JSX.Element {
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);

  const submit = async (values: LoginValues): Promise<void> => {
    setError(undefined);
    setSubmitting(true);
    try {
      await onLogin(values);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-page">
      <Card className="auth-card" bordered={false}>
        <Typography.Title level={2}>Nairag 知识库</Typography.Title>
        <Typography.Paragraph type="secondary">
          使用由系统管理员创建的账号登录。
        </Typography.Paragraph>
        {error ? <Alert className="form-alert" type="error" showIcon message={error} /> : null}
        <Form<LoginValues> layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input autoComplete="username" prefix={<UserOutlined />} />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password autoComplete="current-password" prefix={<LockOutlined />} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>
      </Card>
    </main>
  );
}

