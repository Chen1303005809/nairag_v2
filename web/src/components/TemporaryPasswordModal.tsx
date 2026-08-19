import { Modal, Typography } from "antd";

export function showTemporaryPassword(password: string): void {
  Modal.success({
    title: "一次性临时密码",
    content: (
      <div>
        <p>请立即安全地交给该账号使用者；关闭后系统不会再次展示。</p>
        <Typography.Paragraph copyable strong>
          {password}
        </Typography.Paragraph>
      </div>
    ),
    okText: "我已安全保存"
  });
}

