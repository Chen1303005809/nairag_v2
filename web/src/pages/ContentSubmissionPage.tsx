import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message
} from "antd";
import type { TableProps } from "antd";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type {
  AvailableParent,
  ChildContentInput,
  KnowledgeBase,
  ParentContentInput,
  ReviewChildRevision,
  ReviewParentRevision,
  ReviewSubmission,
  ReviewSubmissionStatus
} from "../api/types";

interface ChildContentFormValues {
  question: string;
  response_content: string;
  question_variants?: string;
  follow_up_guidance?: string;
  question_type?: string;
  business_object?: string;
  purpose?: string;
  customer_type?: string;
  feature_explanation?: string;
  example?: string;
  internal_notes?: string;
}

interface ParentSubmissionFormValues {
  parent: {
    name: string;
    canonical_keyword: string;
    aliases?: string;
  };
  primary_child: ChildContentFormValues;
  knowledge_base_ids: string[];
}

interface ChildSubmissionFormValues {
  parent_id: string;
  child: ChildContentFormValues;
  knowledge_base_ids: string[];
}

interface ResubmissionFormValues {
  parent?: ParentSubmissionFormValues["parent"];
  primary_child?: ChildContentFormValues;
  child?: ChildContentFormValues;
}

function lines(value: string | undefined): string[] {
  return (value ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

const parentTypeOptions = [
  { label: "问题反馈", value: "问题反馈" },
  { label: "需求提交", value: "需求提交" },
  { label: "配置项咨询", value: "配置项咨询" }
];

function nullable(value: string | undefined): string | null {
  const normalized = value?.trim();
  return normalized || null;
}

function toChildContent(values: ChildContentFormValues): ChildContentInput {
  return {
    question: values.question,
    response_content: values.response_content,
    question_variants: lines(values.question_variants),
    follow_up_guidance: nullable(values.follow_up_guidance),
    question_type: nullable(values.question_type),
    business_object: nullable(values.business_object),
    purpose: nullable(values.purpose),
    customer_type: nullable(values.customer_type),
    feature_explanation: nullable(values.feature_explanation),
    example: nullable(values.example),
    internal_notes: nullable(values.internal_notes)
  };
}

function toParentContent(values: ParentSubmissionFormValues["parent"]): ParentContentInput {
  return {
    name: values.name,
    canonical_keyword: values.canonical_keyword,
    lexical_rules: lines(values.aliases).map((ruleValue) => ({
      rule_type: "alias" as const,
      rule_value: ruleValue
    }))
  };
}

function toChildFormValues(revision: ReviewChildRevision): ChildContentFormValues {
  return {
    question: revision.question,
    response_content: revision.response_content,
    question_variants: revision.question_variants.join("\n"),
    follow_up_guidance: revision.follow_up_guidance ?? "",
    question_type: revision.question_type ?? "",
    business_object: revision.business_object ?? "",
    purpose: revision.purpose ?? "",
    customer_type: revision.customer_type ?? "",
    feature_explanation: revision.feature_explanation ?? "",
    example: revision.example ?? "",
    internal_notes: revision.internal_notes ?? ""
  };
}

function toParentFormValues(revision: ReviewParentRevision): ParentSubmissionFormValues["parent"] {
  return {
    name: revision.name,
    canonical_keyword: revision.canonical_keyword,
    aliases: revision.lexical_rules
      .filter((rule) => rule.rule_type === "alias")
      .map((rule) => rule.rule_value)
      .join("\n")
  };
}

function ChildContentFields({ root }: { root: "primary_child" | "child" }): JSX.Element {
  return (
    <>
      <Form.Item
        name={[root, "question"]}
        label="具体问题所属小类"
        rules={[{ required: true, message: "请输入具体问题所属小类" }]}
      >
        <Input.TextArea rows={2} placeholder="用户会提出的具体问题" />
      </Form.Item>
      <Form.Item
        name={[root, "response_content"]}
        label="回复内容"
        rules={[{ required: true, message: "请输入回复内容" }]}
      >
        <Input.TextArea rows={5} placeholder="审核通过后面向检索用户展示的回答" />
      </Form.Item>
      <Form.Item name={[root, "question_variants"]} label="同义问句">
        <Input.TextArea rows={3} placeholder="每行一个；无需重复填写主问题" />
      </Form.Item>
      <Collapse
        ghost
        items={[
          {
            key: "more-fields",
            label: "补充业务字段（可选）",
            children: (
              <>
                <Form.Item name={[root, "question_type"]} label="问题类型">
                  <Input />
                </Form.Item>
                <Form.Item name={[root, "business_object"]} label="业务对象">
                  <Input />
                </Form.Item>
                <Form.Item name={[root, "purpose"]} label="使用目的">
                  <Input />
                </Form.Item>
                <Form.Item name={[root, "customer_type"]} label="客户类型">
                  <Input />
                </Form.Item>
                <Form.Item name={[root, "feature_explanation"]} label="功能说明">
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name={[root, "example"]} label="示例">
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name={[root, "follow_up_guidance"]} label="后续指引">
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name={[root, "internal_notes"]} label="内部备注">
                  <Input.TextArea rows={3} />
                </Form.Item>
              </>
            )
          }
        ]}
      />
    </>
  );
}

function submissionStatus(status: ReviewSubmissionStatus): JSX.Element {
  const statusMap: Record<ReviewSubmissionStatus, [string, string]> = {
    pending_review: ["gold", "待审核"],
    indexing: ["processing", "索引中"],
    published: ["success", "已发布"],
    rejected: ["error", "已驳回"],
    index_failed: ["error", "索引失败"]
  };
  const [color, label] = statusMap[status];
  return <Tag color={color}>{label}</Tag>;
}

export function ContentSubmissionPage(): JSX.Element {
  const [parentForm] = Form.useForm<ParentSubmissionFormValues>();
  const [childForm] = Form.useForm<ChildSubmissionFormValues>();
  const [resubmissionForm] = Form.useForm<ResubmissionFormValues>();
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [availableParents, setAvailableParents] = useState<AvailableParent[]>([]);
  const [submissions, setSubmissions] = useState<ReviewSubmission[]>([]);
  const [loading, setLoading] = useState(true);
  const [submittingParent, setSubmittingParent] = useState(false);
  const [submittingChild, setSubmittingChild] = useState(false);
  const [editingSubmission, setEditingSubmission] = useState<ReviewSubmission | null>(null);
  const [resubmitting, setResubmitting] = useState(false);
  const selectedParentId = Form.useWatch("parent_id", childForm);

  const selectedParent = useMemo(
    () => availableParents.find((parent) => parent.id === selectedParentId),
    [availableParents, selectedParentId]
  );

  const refresh = async (): Promise<void> => {
    setLoading(true);
    try {
      const [nextKnowledgeBases, nextParents, nextSubmissions] = await Promise.all([
        api.listKnowledgeBases(),
        api.listAvailableParents(),
        api.listMyContentSubmissions()
      ]);
      setKnowledgeBases(nextKnowledgeBases);
      setAvailableParents(nextParents);
      setSubmissions(nextSubmissions);
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "无法加载投稿信息");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    childForm.setFieldValue("knowledge_base_ids", []);
  }, [childForm, selectedParentId]);

  const submitParent = async (values: ParentSubmissionFormValues): Promise<void> => {
    setSubmittingParent(true);
    try {
      const parent = toParentContent(values.parent);
      await api.createParentSubmission(
        parent,
        toChildContent(values.primary_child),
        values.knowledge_base_ids
      );
      parentForm.resetFields();
      message.success("父类与主子条目已作为一个候选提交审核");
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "提交失败，请稍后重试");
    } finally {
      setSubmittingParent(false);
    }
  };

  const openResubmission = (submission: ReviewSubmission): void => {
    if (!submission.child_revision) {
      message.error("投稿内容尚未加载，无法编辑");
      return;
    }
    if (
      submission.submission_kind === "parent_with_primary" &&
      !submission.parent_revision
    ) {
      message.error("父类内容尚未加载，无法编辑");
      return;
    }

    const rejectedTargetIds = submission.targets
      .filter((target) => target.status === "rejected")
      .map((target) => target.id);
    if (rejectedTargetIds.length === 0) {
      message.info("当前投稿没有可重新提交的被驳回目标");
      return;
    }

    if (submission.submission_kind === "parent_with_primary") {
      resubmissionForm.setFieldsValue({
        parent: toParentFormValues(submission.parent_revision!),
        primary_child: toChildFormValues(submission.child_revision)
      });
    } else {
      resubmissionForm.setFieldsValue({ child: toChildFormValues(submission.child_revision) });
    }
    setEditingSubmission(submission);
  };

  const closeResubmission = (): void => {
    if (resubmitting) {
      return;
    }
    setEditingSubmission(null);
    resubmissionForm.resetFields();
  };

  const resubmitSubmission = async (values: ResubmissionFormValues): Promise<void> => {
    if (!editingSubmission) {
      return;
    }
    setResubmitting(true);
    try {
      const rejectedTargetIds = editingSubmission.targets
        .filter((target) => target.status === "rejected")
        .map((target) => target.id);
      if (editingSubmission.submission_kind === "parent_with_primary") {
        if (!values.parent || !values.primary_child) {
          throw new Error("父类和主子条目内容不能为空");
        }
        await api.resubmitRejectedParent(
          editingSubmission.id,
          toParentContent(values.parent),
          toChildContent(values.primary_child)
        );
      } else {
        if (!values.child) {
          throw new Error("子条目内容不能为空");
        }
        await api.resubmitRejectedChild(
          editingSubmission.id,
          toChildContent(values.child),
          rejectedTargetIds
        );
      }
      message.success("已生成新的候选并重新提交审核");
      setEditingSubmission(null);
      resubmissionForm.resetFields();
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "重新提交失败，请稍后重试");
    } finally {
      setResubmitting(false);
    }
  };

  const submitChild = async (values: ChildSubmissionFormValues): Promise<void> => {
    setSubmittingChild(true);
    try {
      await api.createChildSubmission(
        values.parent_id,
        toChildContent(values.child),
        values.knowledge_base_ids
      );
      childForm.resetFields();
      message.success("普通子条目已提交审核");
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "提交失败，请稍后重试");
    } finally {
      setSubmittingChild(false);
    }
  };

  const submissionColumns: TableProps<ReviewSubmission>["columns"] = [
    {
      title: "投稿内容",
      dataIndex: "title",
      key: "title",
      render: (title: string, submission: ReviewSubmission) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{title}</Typography.Text>
          <Typography.Text type="secondary">
            {submission.submission_kind === "parent_with_primary" ? "父类 + 主子条目" : "普通子条目"}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "目标知识库",
      key: "targets",
      render: (_value: unknown, submission: ReviewSubmission) => (
        <Space size={[4, 4]} wrap>
          {submission.targets.map((target) => (
            <Tag
              key={target.id}
              color={target.status === "rejected" ? "error" : undefined}
              title={target.review_comment ?? undefined}
            >
              {target.name}
            </Tag>
          ))}
        </Space>
      )
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (value: ReviewSubmissionStatus) => submissionStatus(value)
    },
    {
      title: "提交时间",
      dataIndex: "submitted_at",
      key: "submitted_at",
      render: (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false })
    },
    {
      title: "操作",
      key: "actions",
      render: (_value: unknown, submission: ReviewSubmission) => {
        const editable =
          submission.child_revision !== null &&
          (submission.submission_kind === "child" || submission.parent_revision !== null) &&
          submission.targets.some((target) => target.status === "rejected");
        return editable ? (
          <Button type="link" onClick={() => openResubmission(submission)}>
            编辑重提
          </Button>
        ) : null;
      }
    }
  ];

  const parentKnowledgeBaseOptions = knowledgeBases.map((knowledgeBase) => ({
    label: knowledgeBase.name,
    value: knowledgeBase.id
  }));
  const childKnowledgeBaseOptions = (selectedParent?.available_knowledge_bases ?? []).map(
    (knowledgeBase) => ({ label: knowledgeBase.name, value: knowledgeBase.id })
  );

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>知识投稿</Typography.Title>
          <Typography.Paragraph type="secondary">
            提交后生成不可变候选修订。候选在审核和后续索引成功前不会对检索用户生效。
          </Typography.Paragraph>
        </div>
        <Button onClick={() => void refresh()} loading={loading}>
          刷新
        </Button>
      </div>
      <Tabs
        items={[
          {
            key: "parent",
            label: "新建父类与主子条目",
            children: (
              <Card>
                {knowledgeBases.length === 0 ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="没有可用知识库"
                    description="请等待系统管理员创建并启用至少一个知识库后再投稿。"
                  />
                ) : (
                  <Form<ParentSubmissionFormValues>
                    form={parentForm}
                    layout="vertical"
                    onFinish={(values) => void submitParent(values)}
                    requiredMark
                  >
                    <Typography.Title level={5}>父类字段</Typography.Title>
                    <Form.Item
                      name={["parent", "name"]}
                      label="类型"
                      rules={[{ required: true, message: "请选择类型" }]}
                    >
                      <Select placeholder="请选择类型" options={parentTypeOptions} />
                    </Form.Item>
                    <Form.Item
                      name={["parent", "canonical_keyword"]}
                      label="问题主关键词"
                      rules={[{ required: true, message: "请输入问题主关键词" }]}
                    >
                      <Input placeholder="例如：登录" />
                    </Form.Item>
                    <Form.Item name={["parent", "aliases"]} label="别名">
                      <Input.TextArea rows={2} placeholder="每行一个，例如：登陆" />
                    </Form.Item>
                    <Typography.Title level={5}>主子条目字段</Typography.Title>
                    <ChildContentFields root="primary_child" />
                    <Form.Item
                      name="knowledge_base_ids"
                      label="目标知识库"
                      rules={[{ required: true, message: "请选择至少一个知识库" }]}
                    >
                      <Checkbox.Group options={parentKnowledgeBaseOptions} />
                    </Form.Item>
                    <Button type="primary" htmlType="submit" loading={submittingParent}>
                      提交父类候选
                    </Button>
                  </Form>
                )}
              </Card>
            )
          },
          {
            key: "child",
            label: "新建普通子条目",
            children: (
              <Card>
                {availableParents.length === 0 ? (
                  <Alert
                    type="info"
                    showIcon
                    message="暂时没有可选择的父类"
                    description="普通子条目只能投放到已完成可用审核的父类；请等待父类与主子条目发布。"
                  />
                ) : (
                  <Form<ChildSubmissionFormValues>
                    form={childForm}
                    layout="vertical"
                    onFinish={(values) => void submitChild(values)}
                    requiredMark
                  >
                    <Form.Item name="parent_id" label="父类" rules={[{ required: true, message: "请选择父类" }]}>
                      <Select
                        placeholder="选择已经可用的父类"
                        options={availableParents.map((parent) => ({
                          value: parent.id,
                          label: `${parent.name}（${parent.canonical_keyword}）`
                        }))}
                      />
                    </Form.Item>
                    <ChildContentFields root="child" />
                    <Form.Item
                      name="knowledge_base_ids"
                      label="目标知识库"
                      rules={[{ required: true, message: "请选择至少一个知识库" }]}
                    >
                      <Checkbox.Group
                        disabled={!selectedParent}
                        options={childKnowledgeBaseOptions}
                      />
                    </Form.Item>
                    <Button type="primary" htmlType="submit" loading={submittingChild}>
                      提交普通子条目
                    </Button>
                  </Form>
                )}
              </Card>
            )
          },
          {
            key: "mine",
            label: "我的投稿",
            children: (
              <Table<ReviewSubmission>
                rowKey="id"
                loading={loading}
                columns={submissionColumns}
                dataSource={submissions}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                locale={{ emptyText: "尚未提交知识内容" }}
              />
            )
          }
        ]}
      />
      <Modal
        open={editingSubmission !== null}
        title="编辑驳回投稿并重新提交"
        onCancel={closeResubmission}
        footer={null}
        destroyOnClose
        width={760}
      >
        {editingSubmission && (
          <>
            <Alert
              type="warning"
              showIcon
              message="原审核记录会保留，新内容将生成新的候选修订"
              description={
                <Space direction="vertical" size={4}>
                  {editingSubmission.targets
                    .filter((target) => target.status === "rejected")
                    .map((target) => (
                      <Typography.Text key={target.id}>
                        {target.name}：{target.review_comment || "审核未填写具体意见"}
                      </Typography.Text>
                    ))}
                </Space>
              }
            />
            <Form<ResubmissionFormValues>
              form={resubmissionForm}
              layout="vertical"
              onFinish={(values) => void resubmitSubmission(values)}
              requiredMark
              style={{ marginTop: 16 }}
            >
              {editingSubmission.submission_kind === "parent_with_primary" && (
                <>
                  <Typography.Title level={5}>父类字段</Typography.Title>
                  <Form.Item
                    name={["parent", "name"]}
                    label="类型"
                    rules={[{ required: true, message: "请选择类型" }]}
                  >
                    <Select placeholder="请选择类型" options={parentTypeOptions} />
                  </Form.Item>
                  <Form.Item
                    name={["parent", "canonical_keyword"]}
                    label="问题主关键词"
                    rules={[{ required: true, message: "请输入问题主关键词" }]}
                  >
                    <Input />
                  </Form.Item>
                  <Form.Item name={["parent", "aliases"]} label="别名">
                    <Input.TextArea rows={2} />
                  </Form.Item>
                  <Typography.Title level={5}>主子条目字段</Typography.Title>
                  <ChildContentFields root="primary_child" />
                </>
              )}
              {editingSubmission.submission_kind === "child" && (
                <>
                  <Typography.Text type="secondary">
                    当前问题：{editingSubmission.title}
                  </Typography.Text>
                  <ChildContentFields root="child" />
                </>
              )}
              <Form.Item label="重新提交目标">
                <Space size={[4, 4]} wrap>
                  {editingSubmission.targets
                    .filter((target) =>
                      editingSubmission.submission_kind === "parent_with_primary"
                        ? true
                        : target.status === "rejected"
                    )
                    .map((target) => (
                      <Tag key={target.id}>{target.name}</Tag>
                    ))}
                </Space>
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={resubmitting}>
                重新提交审核
              </Button>
            </Form>
          </>
        )}
      </Modal>
    </section>
  );
}
