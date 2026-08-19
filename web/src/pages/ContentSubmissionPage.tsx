import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Form,
  Input,
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
    regexes?: string;
  };
  primary_child: ChildContentFormValues;
  knowledge_base_ids: string[];
}

interface ChildSubmissionFormValues {
  parent_id: string;
  child: ChildContentFormValues;
  knowledge_base_ids: string[];
}

function lines(value: string | undefined): string[] {
  return (value ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

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

function ChildContentFields({ root }: { root: "primary_child" | "child" }): JSX.Element {
  return (
    <>
      <Form.Item name={[root, "question"]} label="问题" rules={[{ required: true, message: "请输入问题" }]}>
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
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [availableParents, setAvailableParents] = useState<AvailableParent[]>([]);
  const [submissions, setSubmissions] = useState<ReviewSubmission[]>([]);
  const [loading, setLoading] = useState(true);
  const [submittingParent, setSubmittingParent] = useState(false);
  const [submittingChild, setSubmittingChild] = useState(false);
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
      const parent: ParentContentInput = {
        name: values.parent.name,
        canonical_keyword: values.parent.canonical_keyword,
        lexical_rules: [
          ...lines(values.parent.aliases).map((ruleValue) => ({
            rule_type: "alias" as const,
            rule_value: ruleValue
          })),
          ...lines(values.parent.regexes).map((ruleValue) => ({
            rule_type: "regex" as const,
            rule_value: ruleValue
          }))
        ]
      };
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
            <Tag key={target.id}>{target.name}</Tag>
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
                    requiredMark={false}
                  >
                    <Typography.Title level={5}>父类字段</Typography.Title>
                    <Form.Item name={["parent", "name"]} label="父类名称" rules={[{ required: true }]}>
                      <Input placeholder="例如：账号登录" />
                    </Form.Item>
                    <Form.Item
                      name={["parent", "canonical_keyword"]}
                      label="规范关键词"
                      rules={[{ required: true }]}
                    >
                      <Input placeholder="例如：登录" />
                    </Form.Item>
                    <Form.Item name={["parent", "aliases"]} label="别名">
                      <Input.TextArea rows={2} placeholder="每行一个，例如：登陆" />
                    </Form.Item>
                    <Form.Item name={["parent", "regexes"]} label="受控正则">
                      <Input.TextArea rows={2} placeholder="每行一个；仅在确有必要时填写" />
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
                    requiredMark={false}
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
    </section>
  );
}
