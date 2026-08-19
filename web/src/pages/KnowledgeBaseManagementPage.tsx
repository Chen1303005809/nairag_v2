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
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type { ManagedKnowledgeBase, ReviewerAssignment, User } from "../api/types";

interface CreateKnowledgeBaseValues {
  logicalKey: string;
  name: string;
  description?: string;
  isActive: boolean;
}

interface UpdateKnowledgeBaseValues {
  name: string;
  description?: string;
  isActive: boolean;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "操作失败，请稍后重试";
}

function normalizeDescription(value: string | undefined): string | null {
  const description = value?.trim();
  return description || null;
}

export function KnowledgeBaseManagementPage(): JSX.Element {
  const [knowledgeBases, setKnowledgeBases] = useState<ManagedKnowledgeBase[]>([]);
  const [reviewAdministrators, setReviewAdministrators] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editingKnowledgeBase, setEditingKnowledgeBase] = useState<ManagedKnowledgeBase>();
  const [reviewerKnowledgeBase, setReviewerKnowledgeBase] = useState<ManagedKnowledgeBase>();
  const [assignments, setAssignments] = useState<ReviewerAssignment[]>([]);
  const [assigningReviewerId, setAssigningReviewerId] = useState<string>();
  const [createForm] = Form.useForm<CreateKnowledgeBaseValues>();
  const [editForm] = Form.useForm<UpdateKnowledgeBaseValues>();

  const loadManagementData = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [managedKnowledgeBases, users] = await Promise.all([
        api.listManagedKnowledgeBases(),
        api.listUsers(false)
      ]);
      setKnowledgeBases(managedKnowledgeBases);
      setReviewAdministrators(
        users.filter((user) => user.role === "review_admin" && user.is_active)
      );
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadManagementData();
  }, [loadManagementData]);

  const loadAssignments = async (knowledgeBase: ManagedKnowledgeBase): Promise<void> => {
    setReviewerKnowledgeBase(knowledgeBase);
    setAssignments([]);
    try {
      setAssignments(await api.listKnowledgeBaseReviewers(knowledgeBase.id));
    } catch (reason) {
      message.error(errorMessage(reason));
    }
  };

  const createKnowledgeBase = async (values: CreateKnowledgeBaseValues): Promise<void> => {
    setSubmitting(true);
    try {
      await api.createKnowledgeBase(
        values.logicalKey,
        values.name,
        normalizeDescription(values.description),
        values.isActive
      );
      createForm.resetFields();
      setCreateOpen(false);
      await loadManagementData();
      message.success("知识库已创建；物理 Collection 将由后续索引模块进行实际初始化");
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const openEdit = (knowledgeBase: ManagedKnowledgeBase): void => {
    setEditingKnowledgeBase(knowledgeBase);
    editForm.setFieldsValue({
      name: knowledgeBase.name,
      description: knowledgeBase.description ?? "",
      isActive: knowledgeBase.is_active
    });
  };

  const updateKnowledgeBase = async (values: UpdateKnowledgeBaseValues): Promise<void> => {
    if (!editingKnowledgeBase) {
      return;
    }
    setSubmitting(true);
    try {
      await api.updateKnowledgeBase(editingKnowledgeBase.id, {
        name: values.name,
        description: normalizeDescription(values.description),
        is_active: values.isActive
      });
      setEditingKnowledgeBase(undefined);
      await loadManagementData();
      message.success("知识库已更新");
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const assignReviewer = async (): Promise<void> => {
    if (!reviewerKnowledgeBase || !assigningReviewerId) {
      return;
    }
    setSubmitting(true);
    try {
      await api.assignKnowledgeBaseReviewer(reviewerKnowledgeBase.id, assigningReviewerId);
      setAssigningReviewerId(undefined);
      setAssignments(await api.listKnowledgeBaseReviewers(reviewerKnowledgeBase.id));
      await loadManagementData();
      message.success("已分配审查管理员");
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const unassignReviewer = async (reviewerUserId: string): Promise<void> => {
    if (!reviewerKnowledgeBase) {
      return;
    }
    try {
      await api.unassignKnowledgeBaseReviewer(reviewerKnowledgeBase.id, reviewerUserId);
      setAssignments(await api.listKnowledgeBaseReviewers(reviewerKnowledgeBase.id));
      await loadManagementData();
      message.success("已解除审查授权");
    } catch (reason) {
      message.error(errorMessage(reason));
    }
  };

  const unassignedReviewers = useMemo(() => {
    const assignedIds = new Set(assignments.map((assignment) => assignment.reviewer.id));
    return reviewAdministrators.filter((reviewer) => !assignedIds.has(reviewer.id));
  }, [assignments, reviewAdministrators]);

  const columns: ColumnsType<ManagedKnowledgeBase> = [
    {
      title: "知识库",
      key: "knowledge_base",
      render: (_, knowledgeBase) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{knowledgeBase.name}</Typography.Text>
          <Typography.Text type="secondary" code>
            {knowledgeBase.logical_key}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "当前 Collection 代",
      key: "collection",
      width: 260,
      render: (_, knowledgeBase) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>第 {knowledgeBase.current_collection_generation} 代</Typography.Text>
          <Typography.Text copyable type="secondary" code>
            {knowledgeBase.current_physical_collection_name}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 100,
      render: (isActive: boolean) => <Tag color={isActive ? "green" : "red"}>{isActive ? "启用" : "停用"}</Tag>
    },
    {
      title: "审查管理员",
      dataIndex: "reviewer_count",
      width: 130,
      render: (reviewerCount: number) => `${reviewerCount} 人`
    },
    {
      title: "操作",
      key: "actions",
      width: 190,
      render: (_, knowledgeBase) => (
        <Space size="small">
          <Button type="link" onClick={() => void loadAssignments(knowledgeBase)}>
            审查授权
          </Button>
          <Button type="link" onClick={() => openEdit(knowledgeBase)}>
            编辑
          </Button>
        </Space>
      )
    }
  ];

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>知识库管理</Typography.Title>
          <Typography.Paragraph type="secondary">
            管理业务知识库及其审查管理员授权。逻辑标识创建后保持不变。
          </Typography.Paragraph>
        </div>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          创建知识库
        </Button>
      </div>

      <Table<ManagedKnowledgeBase>
        rowKey="id"
        columns={columns}
        dataSource={knowledgeBases}
        loading={loading}
        scroll={{ x: 850 }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
      />

      <Modal
        title="创建知识库"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        okText="创建"
        confirmLoading={submitting}
        destroyOnClose
      >
        <Form<CreateKnowledgeBaseValues>
          form={createForm}
          layout="vertical"
          initialValues={{ isActive: true }}
          onFinish={createKnowledgeBase}
        >
          <Form.Item
            name="logicalKey"
            label="逻辑标识"
            extra="仅小写字母、数字、下划线或连字符；创建后不可修改。"
            rules={[
              { required: true, message: "请输入逻辑标识" },
              { pattern: /^[a-z][a-z0-9_-]{2,63}$/, message: "使用 3–64 位小写字母、数字或 _-" }
            ]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} maxLength={2000} />
          </Form.Item>
          <Form.Item name="isActive" label="启用知识库" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`编辑知识库${editingKnowledgeBase ? `：${editingKnowledgeBase.name}` : ""}`}
        open={Boolean(editingKnowledgeBase)}
        onCancel={() => setEditingKnowledgeBase(undefined)}
        onOk={() => editForm.submit()}
        okText="保存"
        confirmLoading={submitting}
        destroyOnClose
      >
        <Form<UpdateKnowledgeBaseValues> form={editForm} layout="vertical" onFinish={updateKnowledgeBase}>
          <Form.Item label="逻辑标识">
            <Input value={editingKnowledgeBase?.logical_key} disabled />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} maxLength={2000} />
          </Form.Item>
          <Form.Item name="isActive" label="启用知识库" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`审查管理员授权${reviewerKnowledgeBase ? `：${reviewerKnowledgeBase.name}` : ""}`}
        open={Boolean(reviewerKnowledgeBase)}
        onCancel={() => setReviewerKnowledgeBase(undefined)}
        footer={<Button onClick={() => setReviewerKnowledgeBase(undefined)}>关闭</Button>}
        width={760}
      >
        <Space.Compact block>
          <Select
            value={assigningReviewerId}
            placeholder="选择要分配的启用审查管理员"
            options={unassignedReviewers.map((reviewer) => ({
              value: reviewer.id,
              label: `${reviewer.display_name}（${reviewer.username}）`
            }))}
            onChange={setAssigningReviewerId}
          />
          <Button type="primary" disabled={!assigningReviewerId} loading={submitting} onClick={() => void assignReviewer()}>
            分配
          </Button>
        </Space.Compact>

        <Table<ReviewerAssignment>
          className="reviewer-assignment-table"
          rowKey={(assignment) => assignment.reviewer.id}
          dataSource={assignments}
          pagination={false}
          locale={{ emptyText: "尚未分配审查管理员" }}
          columns={[
            {
              title: "审查管理员",
              key: "reviewer",
              render: (_, assignment) => `${assignment.reviewer.display_name}（${assignment.reviewer.username}）`
            },
            {
              title: "状态",
              key: "status",
              width: 100,
              render: (_, assignment) => (
                <Tag color={assignment.reviewer.is_active ? "green" : "red"}>
                  {assignment.reviewer.is_active ? "启用" : "停用"}
                </Tag>
              )
            },
            {
              title: "操作",
              key: "action",
              width: 110,
              render: (_, assignment) => (
                <Popconfirm
                  title="解除该审查授权？"
                  okText="解除"
                  cancelText="取消"
                  onConfirm={() => unassignReviewer(assignment.reviewer.id)}
                >
                  <Button type="link" danger>
                    解除授权
                  </Button>
                </Popconfirm>
              )
            }
          ]}
        />
      </Modal>
    </section>
  );
}
