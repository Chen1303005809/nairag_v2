import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { User, UserRole } from "../api/types";
import { RoleTag, roleLabel } from "../components/role";
import { showTemporaryPassword } from "../components/TemporaryPasswordModal";

interface CreateUserValues {
  username: string;
  displayName: string;
  role: UserRole;
}

interface UpdateUserValues {
  displayName: string;
  role: UserRole;
  isActive: boolean;
}

const roleOptions = (Object.keys({
  normal_user: true,
  review_admin: true,
  system_admin: true
}) as UserRole[]).map((role) => ({ value: role, label: roleLabel(role) }));

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "操作失败，请稍后重试";
}

export function AccountManagementPage(): JSX.Element {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User>();
  const [submitting, setSubmitting] = useState(false);
  const [createForm] = Form.useForm<CreateUserValues>();
  const [editForm] = Form.useForm<UpdateUserValues>();

  const loadUsers = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      setUsers(await api.listUsers());
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers]);

  const createUser = async (values: CreateUserValues): Promise<void> => {
    setSubmitting(true);
    try {
      const result = await api.createUser(values.username, values.displayName, values.role);
      setCreateOpen(false);
      createForm.resetFields();
      await loadUsers();
      showTemporaryPassword(result.temporary_password);
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const openEdit = (user: User): void => {
    setEditingUser(user);
    editForm.setFieldsValue({
      displayName: user.display_name,
      role: user.role,
      isActive: user.is_active
    });
  };

  const updateUser = async (values: UpdateUserValues): Promise<void> => {
    if (!editingUser) {
      return;
    }
    setSubmitting(true);
    try {
      await api.updateUser(editingUser.id, {
        display_name: values.displayName,
        role: values.role,
        is_active: values.isActive
      });
      setEditingUser(undefined);
      await loadUsers();
      message.success("账号已更新");
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const resetPassword = async (user: User): Promise<void> => {
    try {
      const result = await api.resetUserPassword(user.id);
      await loadUsers();
      showTemporaryPassword(result.temporary_password);
    } catch (reason) {
      message.error(errorMessage(reason));
    }
  };

  const columns: ColumnsType<User> = [
    { title: "用户名", dataIndex: "username", width: 170 },
    { title: "显示名称", dataIndex: "display_name", width: 180 },
    { title: "角色", dataIndex: "role", width: 140, render: (role: UserRole) => <RoleTag role={role} /> },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 110,
      render: (isActive: boolean) => <Tag color={isActive ? "green" : "red"}>{isActive ? "启用" : "停用"}</Tag>
    },
    {
      title: "首次改密",
      dataIndex: "must_change_password",
      width: 120,
      render: (required: boolean) => (required ? <Tag color="orange">待修改</Tag> : "已完成")
    },
    {
      title: "操作",
      key: "actions",
      width: 190,
      render: (_, user) => (
        <Space size="small">
          <Button type="link" onClick={() => openEdit(user)}>
            编辑
          </Button>
          {user.role === "system_admin" ? (
            <Typography.Text type="secondary">请本人自定义修改</Typography.Text>
          ) : (
            <Popconfirm
              title={`重置 ${user.display_name} 的密码？`}
              description="旧登录令牌会立即失效。"
              okText="重置"
              cancelText="取消"
              onConfirm={() => resetPassword(user)}
            >
              <Button type="link">重置密码</Button>
            </Popconfirm>
          )}
        </Space>
      )
    }
  ];

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>账号管理</Typography.Title>
          <Typography.Paragraph type="secondary">
            创建、启停、分配角色或重置账号；系统管理员密码需由本人输入当前密码后自定义修改。临时密码只会显示一次。
          </Typography.Paragraph>
        </div>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          创建账号
        </Button>
      </div>

      <Table<User>
        rowKey="id"
        columns={columns}
        dataSource={users}
        loading={loading}
        scroll={{ x: 850 }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
      />

      <Modal
        title="创建账号"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        okText="创建并生成临时密码"
        confirmLoading={submitting}
        destroyOnClose
      >
        <Form<CreateUserValues>
          form={createForm}
          layout="vertical"
          initialValues={{ role: "normal_user" }}
          onFinish={createUser}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[
              { required: true, message: "请输入用户名" },
              { pattern: /^[a-z0-9][a-z0-9._-]{2,63}$/, message: "使用 3–64 位小写字母、数字或 ._-" }
            ]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="displayName" label="显示名称" rules={[{ required: true, message: "请输入显示名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={roleOptions} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`编辑账号${editingUser ? `：${editingUser.username}` : ""}`}
        open={Boolean(editingUser)}
        onCancel={() => setEditingUser(undefined)}
        onOk={() => editForm.submit()}
        okText="保存"
        confirmLoading={submitting}
        destroyOnClose
      >
        <Form<UpdateUserValues> form={editForm} layout="vertical" onFinish={updateUser}>
          <Form.Item name="displayName" label="显示名称" rules={[{ required: true, message: "请输入显示名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={roleOptions} />
          </Form.Item>
          <Form.Item name="isActive" label="启用账号" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  );
}
