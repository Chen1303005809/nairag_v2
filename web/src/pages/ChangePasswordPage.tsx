import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";

interface ChangePasswordValues {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

interface ChangePasswordPageProps {
  onChangePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

export function ChangePasswordPage({ onChangePassword }: ChangePasswordPageProps): JSX.Element {
  const [form] = Form.useForm<ChangePasswordValues>();
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);

  const submit = async (values: ChangePasswordValues): Promise<void> => {
    setError(undefined);
    setSubmitting(true);
    try {
      await onChangePassword(values.currentPassword, values.newPassword);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "修改密码失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-page">
      <Card className="auth-card" variant="borderless">
        <Typography.Title level={2}>请修改临时密码</Typography.Title>
        <Typography.Paragraph type="secondary">
          为保护账号安全，临时密码不能继续用于系统操作。
        </Typography.Paragraph>
        {error ? <Alert className="form-alert" type="error" showIcon message={error} /> : null}
        <Form<ChangePasswordValues> form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item
            name="currentPassword"
            label="当前密码"
            rules={[{ required: true, message: "请输入当前密码" }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="newPassword"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 12, message: "密码至少 12 个字符" }
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirmPassword"
            label="确认新密码"
            dependencies={["newPassword"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  return !value || getFieldValue("newPassword") === value
                    ? Promise.resolve()
                    : Promise.reject(new Error("两次输入的密码不一致"));
                }
              })
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            保存新密码
          </Button>
        </Form>
      </Card>
    </main>
  );
}
