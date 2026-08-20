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
  Tabs,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { formatDateTime } from "../dateTime";
import type { ManagedKnowledgeBase, ManagedKnowledgeEntry, ReviewerAssignment, User } from "../api/types";
import { uniqueTableFilterOptions } from "../tableFilters";

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

function activeReviewAdministrators(users: User[]): User[] {
  return users.filter((user) => user.role === "review_admin" && user.is_active);
}

export function KnowledgeBaseManagementPage(): JSX.Element {
  const [knowledgeBases, setKnowledgeBases] = useState<ManagedKnowledgeBase[]>([]);
  const [managedKnowledge, setManagedKnowledge] = useState<ManagedKnowledgeEntry[]>([]);
  const [reviewAdministrators, setReviewAdministrators] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editingKnowledgeBase, setEditingKnowledgeBase] = useState<ManagedKnowledgeBase>();
  const [reviewerKnowledgeBase, setReviewerKnowledgeBase] = useState<ManagedKnowledgeBase>();
  const [assignments, setAssignments] = useState<ReviewerAssignment[]>([]);
  const [assigningReviewerId, setAssigningReviewerId] = useState<string>();
  const [reviewerLoading, setReviewerLoading] = useState(false);
  const [createForm] = Form.useForm<CreateKnowledgeBaseValues>();
  const [editForm] = Form.useForm<UpdateKnowledgeBaseValues>();

  const loadManagementData = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [managedKnowledgeBases, users, managedKnowledgeEntries] = await Promise.all([
        api.listManagedKnowledgeBases(),
        api.listUsers(false),
        api.listManagedKnowledgeEntries()
      ]);
      setKnowledgeBases(managedKnowledgeBases);
      setReviewAdministrators(activeReviewAdministrators(users));
      setManagedKnowledge(managedKnowledgeEntries);
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
    setReviewAdministrators([]);
    setReviewerLoading(true);
    try {
      const [assignments, users] = await Promise.all([
        api.listKnowledgeBaseReviewers(knowledgeBase.id),
        api.listUsers(false)
      ]);
      setAssignments(assignments);
      setReviewAdministrators(activeReviewAdministrators(users));
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setReviewerLoading(false);
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

  const deleteManagedKnowledge = async (entry: ManagedKnowledgeEntry): Promise<void> => {
    setSubmitting(true);
    try {
      await api.archiveManagedKnowledge(entry.child_id, entry.knowledge_base.id);
      await loadManagementData();
      message.success("知识已删除，相关嵌入正在后台清理");
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const unassignedReviewers = useMemo(() => {
    const assignedIds = new Set(assignments.map((assignment) => assignment.reviewer.id));
    return reviewAdministrators.filter((reviewer) => !assignedIds.has(reviewer.id));
  }, [assignments, reviewAdministrators]);

  const managedKnowledgeBaseFilters = useMemo(
    () =>
      uniqueTableFilterOptions(managedKnowledge, (entry) => [
        { text: entry.knowledge_base.name, value: entry.knowledge_base.id }
      ]),
    [managedKnowledge]
  );

  const managedKnowledgeUploaderFilters = useMemo(
    () =>
      uniqueTableFilterOptions(managedKnowledge, (entry) => [
        {
          text: `${entry.uploaded_by.display_name}（${entry.uploaded_by.username}）`,
          value: entry.uploaded_by.id
        }
      ]),
    [managedKnowledge]
  );

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

  const managedKnowledgeColumns: ColumnsType<ManagedKnowledgeEntry> = [
    {
      title: "知识内容",
      key: "content",
      render: (_, entry) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{entry.child_revision.question}</Typography.Text>
          <Typography.Text type="secondary">
            {entry.parent_name} · {entry.is_primary ? "问题小类（与问题大类一同提交）" : "问题小类"} · v
            {entry.child_revision.revision_number}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "知识库",
      key: "knowledge_base",
      filters: managedKnowledgeBaseFilters,
      filterSearch: true,
      onFilter: (value, entry) => entry.knowledge_base.id === String(value),
      render: (_, entry) => entry.knowledge_base.name
    },
    {
      title: "上传者",
      key: "uploaded_by",
      filters: managedKnowledgeUploaderFilters,
      filterSearch: true,
      onFilter: (value, entry) => entry.uploaded_by.id === String(value),
      render: (_, entry) => `${entry.uploaded_by.display_name}（${entry.uploaded_by.username}）`
    },
    {
      title: "上传时间",
      dataIndex: "uploaded_at",
      key: "uploaded_at",
      render: (value: string) => formatDateTime(value)
    },
    {
      title: "实际嵌入时间",
      dataIndex: "embedded_at",
      key: "embedded_at",
      render: (value: string | null) => formatDateTime(value)
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (value: ManagedKnowledgeEntry["status"]) => {
        if (value === "published") {
          return <Tag color="green">已发布</Tag>;
        }
        if (value === "archived") {
          return <Tag color="default">已删除（已归档）</Tag>;
        }
        return <Tag color="processing">待处理</Tag>;
      }
    },
    {
      title: "操作",
      key: "actions",
      width: 110,
      render: (_, entry) => (
        <Popconfirm
          title="删除该知识及其嵌入？"
          description="删除后立即停止检索可见性，并在后台清理该知识库中的全部派生嵌入；修订和审计历史会保留。"
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true, loading: submitting }}
          onConfirm={() => void deleteManagedKnowledge(entry)}
          disabled={entry.status !== "published"}
        >
          <Button type="link" danger disabled={entry.status !== "published"}>
            删除
          </Button>
        </Popconfirm>
      )
    }
  ];

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>知识库管理</Typography.Title>
          <Typography.Paragraph type="secondary">
            管理业务知识库、全部已发布知识及审查管理员授权。逻辑标识创建后保持不变。
          </Typography.Paragraph>
        </div>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          创建知识库
        </Button>
      </div>

      <Tabs
        items={[
          {
            key: "knowledge-bases",
            label: "知识库",
            children: (
              <Table<ManagedKnowledgeBase>
                rowKey="id"
                columns={columns}
                dataSource={knowledgeBases}
                loading={loading}
                scroll={{ x: 850 }}
                pagination={{ pageSize: 10, showSizeChanger: false }}
              />
            )
          },
          {
            key: "knowledge",
            label: "全部知识",
            children: (
              <Table<ManagedKnowledgeEntry>
                rowKey={(entry) => `${entry.child_id}:${entry.knowledge_base.id}`}
                columns={managedKnowledgeColumns}
                dataSource={managedKnowledge}
                loading={loading}
                scroll={{ x: 1350 }}
                pagination={{ pageSize: 10, showSizeChanger: false }}
                locale={{ emptyText: "尚无已发布或已删除知识" }}
              />
            )
          }
        ]}
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
            loading={reviewerLoading}
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
