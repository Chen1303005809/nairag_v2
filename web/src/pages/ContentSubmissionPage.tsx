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
import { uniqueTableFilterOptions } from "../tableFilters";
import type {
  AvailableParent,
  ChildContentInput,
  EditableContentEntry,
  KnowledgeBase,
  ParentContentInput,
  ReviewChildRevision,
  ReviewParentRevision,
  ReviewSubmission,
  ReviewSubmissionStatus
} from "../api/types";
import { formatDateTime } from "../dateTime";
import {
  businessObjectOptions,
  customerTypeOptions,
  parentTypeOptions,
  purposeOptions,
  questionTypeOptions
} from "../constants/knowledgeOptions";

const PARENT_CATEGORY_LABEL = "问题大类";
const CHILD_CATEGORY_LABEL = "问题小类";

interface ChildContentFormValues {
  question: string;
  response_content: string;
  question_variants?: string[];
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
    aliases?: string[];
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

interface PublishedRevisionFormValues {
  parent?: ParentSubmissionFormValues["parent"];
  primary_child?: ChildContentFormValues;
  child?: ChildContentFormValues;
  knowledge_base_ids?: string[];
}

function normalizeList(values: string[] | undefined): string[] {
  return (values ?? [])
    .map((value) => value.trim())
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
    question_variants: normalizeList(values.question_variants),
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
    lexical_rules: normalizeList(values.aliases).map((ruleValue) => ({
      rule_type: "alias" as const,
      rule_value: ruleValue
    }))
  };
}

function toChildFormValues(revision: ReviewChildRevision): ChildContentFormValues {
  return {
    question: revision.question,
    response_content: revision.response_content,
    question_variants: [...revision.question_variants],
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
  };
}

function StructuredTextList({
  name,
  label,
  addLabel,
  placeholder
}: {
  name: (string | number)[];
  label: string;
  addLabel: string;
  placeholder: string;
}): JSX.Element {
  return (
    <Form.Item label={label}>
      <Form.List name={name}>
        {(fields, { add, remove }) => (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            {fields.map((field) => (
              <Space key={field.key} align="start" style={{ display: "flex", width: "100%" }}>
                <Form.Item
                  {...field}
                  rules={[{ required: true, whitespace: true, message: `请输入${label}` }]}
                  style={{ flex: 1, marginBottom: 0 }}
                >
                  <Input placeholder={placeholder} />
                </Form.Item>
                <Button type="link" danger onClick={() => remove(field.name)}>
                  删除
                </Button>
              </Space>
            ))}
            <Button type="dashed" onClick={() => add()} block>
              + {addLabel}
            </Button>
          </Space>
        )}
      </Form.List>
    </Form.Item>
  );
}

function ChildContentFields({ root }: { root: "primary_child" | "child" }): JSX.Element {
  return (
    <>
      <Form.Item
        name={[root, "question"]}
        label={CHILD_CATEGORY_LABEL}
        rules={[{ required: true, message: `请输入${CHILD_CATEGORY_LABEL}` }]}
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
      <StructuredTextList
        name={[root, "question_variants"]}
        label="同义问句"
        addLabel="添加同义问句"
        placeholder="无需重复填写主问题"
      />
      <div className="content-form-grid">
        <Form.Item
          name={[root, "question_type"]}
          label="问题类型"
          rules={[{ required: true, message: "请选择问题类型" }]}
        >
          <Select placeholder="--请选择--" options={questionTypeOptions} />
        </Form.Item>
        <Form.Item
          name={[root, "business_object"]}
          label="具体功能与模块"
          rules={[{ required: true, message: "请选择具体功能与模块" }]}
        >
          <Select placeholder="--请选择--" options={businessObjectOptions} />
        </Form.Item>
        <Form.Item
          name={[root, "purpose"]}
          label="应用场景"
          rules={[{ required: true, message: "请选择应用场景" }]}
        >
          <Select placeholder="--请选择--" options={purposeOptions} />
        </Form.Item>
        <Form.Item
          name={[root, "customer_type"]}
          label="客户类型"
          rules={[{ required: true, message: "请选择客户类型" }]}
        >
          <Select placeholder="--请选择--" options={customerTypeOptions} />
        </Form.Item>
      </div>
      <Collapse
        ghost
        items={[
          {
            key: "supplementary-fields",
            label: "可补充说明",
            children: (
              <>
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

function ParentContentFields(): JSX.Element {
  return (
    <>
      <div className="content-form-grid">
        <Form.Item
          name={["parent", "name"]}
          label={PARENT_CATEGORY_LABEL}
          rules={[{ required: true, message: `请选择${PARENT_CATEGORY_LABEL}` }]}
        >
          <Select placeholder={`请选择${PARENT_CATEGORY_LABEL}`} options={parentTypeOptions} />
        </Form.Item>
        <Form.Item
          name={["parent", "canonical_keyword"]}
          label="问题大类关键词"
          rules={[{ required: true, message: "请输入问题大类关键词" }]}
        >
          <Input placeholder="例如：登录" />
        </Form.Item>
      </div>
      <StructuredTextList
        name={["parent", "aliases"]}
        label="别名"
        addLabel="添加别名"
        placeholder="例如：登陆"
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

function submissionKindLabel(submissionKind: ReviewSubmission["submission_kind"]): string {
  return submissionKind === "parent_with_primary"
    ? `${PARENT_CATEGORY_LABEL} + ${CHILD_CATEGORY_LABEL}`
    : CHILD_CATEGORY_LABEL;
}

function contentEntryKindLabel(isPrimary: boolean): string {
  return isPrimary ? `${CHILD_CATEGORY_LABEL}（与${PARENT_CATEGORY_LABEL}一同提交）` : CHILD_CATEGORY_LABEL;
}

export function ContentSubmissionPage(): JSX.Element {
  const [parentForm] = Form.useForm<ParentSubmissionFormValues>();
  const [childForm] = Form.useForm<ChildSubmissionFormValues>();
  const [resubmissionForm] = Form.useForm<ResubmissionFormValues>();
  const [publishedRevisionForm] = Form.useForm<PublishedRevisionFormValues>();
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [availableParents, setAvailableParents] = useState<AvailableParent[]>([]);
  const [submissions, setSubmissions] = useState<ReviewSubmission[]>([]);
  const [editableEntries, setEditableEntries] = useState<EditableContentEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [submittingParent, setSubmittingParent] = useState(false);
  const [submittingChild, setSubmittingChild] = useState(false);
  const [editingSubmission, setEditingSubmission] = useState<ReviewSubmission | null>(null);
  const [resubmitting, setResubmitting] = useState(false);
  const [editingPublishedEntry, setEditingPublishedEntry] = useState<EditableContentEntry | null>(null);
  const [savingPublishedRevision, setSavingPublishedRevision] = useState(false);
  const selectedParentId = Form.useWatch("parent_id", childForm);

  const selectedParent = useMemo(
    () => availableParents.find((parent) => parent.id === selectedParentId),
    [availableParents, selectedParentId]
  );

  const refresh = async (): Promise<void> => {
    setLoading(true);
    try {
      const [nextKnowledgeBases, nextParents, nextSubmissions, nextEditableEntries] = await Promise.all([
        api.listKnowledgeBases(),
        api.listAvailableParents(),
        api.listMyContentSubmissions(),
        api.listEditableContentEntries()
      ]);
      setKnowledgeBases(nextKnowledgeBases);
      setAvailableParents(nextParents);
      setSubmissions(nextSubmissions);
      setEditableEntries(nextEditableEntries);
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "无法加载上传信息");
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
      message.success(`${PARENT_CATEGORY_LABEL}与${CHILD_CATEGORY_LABEL}已作为一个候选提交审核`);
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "提交失败，请稍后重试");
    } finally {
      setSubmittingParent(false);
    }
  };

  const openResubmission = (submission: ReviewSubmission): void => {
    if (!submission.child_revision) {
      message.error("上传内容尚未加载，无法编辑");
      return;
    }
    if (
      submission.submission_kind === "parent_with_primary" &&
      !submission.parent_revision
    ) {
      message.error(`${PARENT_CATEGORY_LABEL}内容尚未加载，无法编辑`);
      return;
    }

    const rejectedTargetIds = submission.targets
      .filter((target) => target.status === "rejected")
      .map((target) => target.id);
    if (rejectedTargetIds.length === 0) {
      message.info("当前上传没有可重新提交的被驳回目标");
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
          throw new Error(`${PARENT_CATEGORY_LABEL}和${CHILD_CATEGORY_LABEL}内容不能为空`);
        }
        await api.resubmitRejectedParent(
          editingSubmission.id,
          toParentContent(values.parent),
          toChildContent(values.primary_child)
        );
      } else {
        if (!values.child) {
          throw new Error(`${CHILD_CATEGORY_LABEL}内容不能为空`);
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
      message.success(`${CHILD_CATEGORY_LABEL}已提交审核`);
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "提交失败，请稍后重试");
    } finally {
      setSubmittingChild(false);
    }
  };

  const openPublishedRevision = (entry: EditableContentEntry): void => {
    if (entry.is_primary) {
      if (!entry.parent_revision) {
        message.error(`${PARENT_CATEGORY_LABEL}内容尚未加载，无法发起修订`);
        return;
      }
      publishedRevisionForm.setFieldsValue({
        parent: toParentFormValues(entry.parent_revision),
        primary_child: toChildFormValues(entry.child_revision)
      });
    } else {
      publishedRevisionForm.setFieldsValue({
        child: toChildFormValues(entry.child_revision),
        knowledge_base_ids: entry.knowledge_bases.map((knowledgeBase) => knowledgeBase.id)
      });
    }
    setEditingPublishedEntry(entry);
  };

  const closePublishedRevision = (): void => {
    if (savingPublishedRevision) {
      return;
    }
    setEditingPublishedEntry(null);
    publishedRevisionForm.resetFields();
  };

  const submitPublishedRevision = async (values: PublishedRevisionFormValues): Promise<void> => {
    if (!editingPublishedEntry) {
      return;
    }
    setSavingPublishedRevision(true);
    try {
      if (editingPublishedEntry.is_primary) {
        if (!values.parent || !values.primary_child) {
          throw new Error(`${PARENT_CATEGORY_LABEL}和${CHILD_CATEGORY_LABEL}内容不能为空`);
        }
        await api.createParentRevision(
          editingPublishedEntry.parent_id,
          toParentContent(values.parent),
          toChildContent(values.primary_child)
        );
      } else {
        if (!values.child) {
          throw new Error(`${CHILD_CATEGORY_LABEL}内容不能为空`);
        }
        const knowledgeBaseIds = values.knowledge_base_ids ?? [];
        if (knowledgeBaseIds.length === 0) {
          throw new Error("请选择至少一个目标知识库");
        }
        await api.createChildRevision(
          editingPublishedEntry.child_id,
          toChildContent(values.child),
          knowledgeBaseIds
        );
      }
      message.success("已生成新修订并提交审核，审核通过后会重新嵌入");
      setEditingPublishedEntry(null);
      publishedRevisionForm.resetFields();
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "提交修订失败，请稍后重试");
    } finally {
      setSavingPublishedRevision(false);
    }
  };

  const submissionKnowledgeBaseFilters = useMemo(
    () =>
      uniqueTableFilterOptions(submissions, (submission) =>
        submission.targets.map((target) => ({ text: target.name, value: target.id }))
      ),
    [submissions]
  );

  const submissionUploaderFilters = useMemo(
    () =>
      uniqueTableFilterOptions(submissions, (submission) => [
        {
          text: `${submission.submitter.display_name}（${submission.submitter.username}）`,
          value: submission.submitter.id
        }
      ]),
    [submissions]
  );

  const editableKnowledgeBaseFilters = useMemo(
    () =>
      uniqueTableFilterOptions(editableEntries, (entry) =>
        entry.knowledge_bases.map((knowledgeBase) => ({ text: knowledgeBase.name, value: knowledgeBase.id }))
      ),
    [editableEntries]
  );

  const submissionColumns: TableProps<ReviewSubmission>["columns"] = [
    {
      title: "上传内容",
      dataIndex: "title",
      key: "title",
      render: (title: string, submission: ReviewSubmission) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{title}</Typography.Text>
          <Typography.Text type="secondary">
            {submissionKindLabel(submission.submission_kind)}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "目标知识库",
      key: "targets",
      filters: submissionKnowledgeBaseFilters,
      filterSearch: true,
      onFilter: (value, submission) => submission.targets.some((target) => target.id === String(value)),
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
      title: "上传者",
      key: "submitter",
      filters: submissionUploaderFilters,
      filterSearch: true,
      onFilter: (value, submission) => submission.submitter.id === String(value),
      render: (_value: unknown, submission: ReviewSubmission) =>
        `${submission.submitter.display_name}（${submission.submitter.username}）`
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (value: ReviewSubmissionStatus) => submissionStatus(value)
    },
    {
      title: "上传时间",
      dataIndex: "submitted_at",
      key: "submitted_at",
      render: (value: string) => formatDateTime(value)
    },
    {
      title: "审核者",
      key: "reviewer",
      render: (_value: unknown, submission: ReviewSubmission) => (
        <Space direction="vertical" size={0}>
          {submission.targets.map((target) => (
            <Typography.Text key={target.id} type={target.reviewer ? undefined : "secondary"}>
              {target.name}：
              {target.reviewer
                ? `${target.reviewer.display_name}（${target.reviewer.username}）`
                : "待审核"}
            </Typography.Text>
          ))}
        </Space>
      )
    },
    {
      title: "实际审核时间",
      key: "reviewed_at",
      render: (_value: unknown, submission: ReviewSubmission) => (
        <Space direction="vertical" size={0}>
          {submission.targets.map((target) => (
            <Typography.Text key={target.id} type={target.reviewed_at ? undefined : "secondary"}>
              {target.name}：{formatDateTime(target.reviewed_at)}
            </Typography.Text>
          ))}
        </Space>
      )
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

  const editableEntryColumns: TableProps<EditableContentEntry>["columns"] = [
    {
      title: PARENT_CATEGORY_LABEL,
      key: "parent",
      render: (_value: unknown, entry: EditableContentEntry) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{entry.parent_name}</Typography.Text>
          <Typography.Text type="secondary">
            {contentEntryKindLabel(entry.is_primary)}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: `当前${CHILD_CATEGORY_LABEL}`,
      dataIndex: ["child_revision", "question"],
      key: "question"
    },
    {
      title: "已发布知识库",
      key: "knowledge_bases",
      filters: editableKnowledgeBaseFilters,
      filterSearch: true,
      onFilter: (value, entry) => entry.knowledge_bases.some((knowledgeBase) => knowledgeBase.id === String(value)),
      render: (_value: unknown, entry: EditableContentEntry) => (
        <Space size={[4, 4]} wrap>
          {entry.knowledge_bases.map((knowledgeBase) => (
            <Tag key={knowledgeBase.id}>{knowledgeBase.name}</Tag>
          ))}
        </Space>
      )
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: (_value: unknown, entry: EditableContentEntry) => (
        <Button type="link" onClick={() => openPublishedRevision(entry)}>
          修改并提交审核
        </Button>
      )
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
          <Typography.Title level={3}>知识上传</Typography.Title>
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
            label: `新建${PARENT_CATEGORY_LABEL}`,
            children: (
              <Card>
                {knowledgeBases.length === 0 ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="没有可用知识库"
                    description="请等待系统管理员创建并启用至少一个知识库后再上传。"
                  />
                ) : (
                  <Form<ParentSubmissionFormValues>
                    form={parentForm}
                    layout="vertical"
                    onFinish={(values) => void submitParent(values)}
                    requiredMark
                  >
                    <Typography.Title level={5}>{PARENT_CATEGORY_LABEL}</Typography.Title>
                    <ParentContentFields />
                    <Typography.Title level={5}>{CHILD_CATEGORY_LABEL}</Typography.Title>
                    <ChildContentFields root="primary_child" />
                    <Form.Item
                      name="knowledge_base_ids"
                      label="目标知识库"
                      rules={[{ required: true, message: "请选择至少一个知识库" }]}
                    >
                      <Checkbox.Group className="knowledge-base-options" options={parentKnowledgeBaseOptions} />
                    </Form.Item>
                    <Button type="primary" htmlType="submit" loading={submittingParent}>
                      提交候选
                    </Button>
                  </Form>
                )}
              </Card>
            )
          },
          {
            key: "child",
            label: `新建${CHILD_CATEGORY_LABEL}`,
            children: (
              <Card>
                {availableParents.length === 0 ? (
                  <Alert
                    type="info"
                    showIcon
                    message={`暂时没有可选择的${PARENT_CATEGORY_LABEL}`}
                    description={`${CHILD_CATEGORY_LABEL}只能投放到已完成可用审核的${PARENT_CATEGORY_LABEL}；请等待${PARENT_CATEGORY_LABEL}与${CHILD_CATEGORY_LABEL}发布。`}
                  />
                ) : (
                  <Form<ChildSubmissionFormValues>
                    form={childForm}
                    layout="vertical"
                    onFinish={(values) => void submitChild(values)}
                    requiredMark
                  >
                    <Form.Item
                      name="parent_id"
                      label={PARENT_CATEGORY_LABEL}
                      rules={[{ required: true, message: `请选择${PARENT_CATEGORY_LABEL}` }]}
                    >
                      <Select
                        placeholder={`选择已发布的${PARENT_CATEGORY_LABEL}`}
                        options={availableParents.map((parent) => ({
                          value: parent.id,
                          label: `${parent.canonical_keyword}(${parent.name})`
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
                        className="knowledge-base-options"
                        disabled={!selectedParent}
                        options={childKnowledgeBaseOptions}
                      />
                    </Form.Item>
                    <Button type="primary" htmlType="submit" loading={submittingChild}>
                      提交候选
                    </Button>
                  </Form>
                )}
              </Card>
            )
          },
          {
            key: "revise",
            label: "修改已发布内容",
            children: (
              <Card>
                <Alert
                  type="info"
                  showIcon
                  message="修改会创建新的候选修订"
                  description={`单独创建的${CHILD_CATEGORY_LABEL}按下方列出的知识库重新审核；与${PARENT_CATEGORY_LABEL}一同创建的${CHILD_CATEGORY_LABEL}会连同${PARENT_CATEGORY_LABEL}在全部已发布知识库重新审核和嵌入。`}
                  style={{ marginBottom: 16 }}
                />
                <Table<EditableContentEntry>
                  rowKey={(entry) => `${entry.child_id}:${entry.child_revision.id}`}
                  loading={loading}
                  columns={editableEntryColumns}
                  dataSource={editableEntries}
                  scroll={{ x: 900 }}
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  locale={{ emptyText: "当前没有可修改的已发布知识" }}
                />
              </Card>
            )
          },
          {
            key: "mine",
            label: "我的上传",
            children: (
              <Table<ReviewSubmission>
                rowKey="id"
                loading={loading}
                columns={submissionColumns}
                dataSource={submissions}
                scroll={{ x: 1300 }}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                locale={{ emptyText: "尚未提交知识内容" }}
              />
            )
          }
        ]}
      />
      <Modal
        open={editingSubmission !== null}
        title="编辑驳回内容并重新上传"
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
                  <Typography.Title level={5}>{PARENT_CATEGORY_LABEL}</Typography.Title>
                  <ParentContentFields />
                  <Typography.Title level={5}>{CHILD_CATEGORY_LABEL}</Typography.Title>
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
      <Modal
        open={editingPublishedEntry !== null}
        title="修改已发布知识并提交审核"
        onCancel={closePublishedRevision}
        footer={null}
        destroyOnClose
        width={760}
      >
        {editingPublishedEntry && (
          <>
            <Alert
              type="warning"
              showIcon
              message="线上版本会继续服务，直到新修订审核并重新嵌入成功"
              description={
                editingPublishedEntry.is_primary
                  ? `与${PARENT_CATEGORY_LABEL}一同创建的${CHILD_CATEGORY_LABEL}必须和${PARENT_CATEGORY_LABEL}一起修订，并在全部已发布目标知识库完成审核后统一生效。`
                  : `${CHILD_CATEGORY_LABEL}会仅在选中的目标知识库创建新候选并重新嵌入。`
              }
            />
            <Form<PublishedRevisionFormValues>
              form={publishedRevisionForm}
              layout="vertical"
              onFinish={(values) => void submitPublishedRevision(values)}
              requiredMark
              style={{ marginTop: 16 }}
            >
              {editingPublishedEntry.is_primary ? (
                <>
                  <Typography.Title level={5}>{PARENT_CATEGORY_LABEL}</Typography.Title>
                  <ParentContentFields />
                  <Typography.Title level={5}>{CHILD_CATEGORY_LABEL}</Typography.Title>
                  <ChildContentFields root="primary_child" />
                  <Form.Item label="重新审核知识库">
                    <Space size={[4, 4]} wrap>
                      {editingPublishedEntry.knowledge_bases.map((knowledgeBase) => (
                        <Tag key={knowledgeBase.id}>{knowledgeBase.name}</Tag>
                      ))}
                    </Space>
                  </Form.Item>
                </>
              ) : (
                <>
                  <Typography.Text type="secondary">
                    {PARENT_CATEGORY_LABEL}：{editingPublishedEntry.parent_name}
                  </Typography.Text>
                  <ChildContentFields root="child" />
                  <Form.Item
                    name="knowledge_base_ids"
                    label="重新审核知识库"
                    rules={[{ required: true, message: "请选择至少一个知识库" }]}
                  >
                    <Checkbox.Group
                      className="knowledge-base-options"
                      options={editingPublishedEntry.knowledge_bases.map((knowledgeBase) => ({
                        label: knowledgeBase.name,
                        value: knowledgeBase.id
                      }))}
                    />
                  </Form.Item>
                </>
              )}
              <Button type="primary" htmlType="submit" loading={savingPublishedRevision}>
                提交新修订审核
              </Button>
            </Form>
          </>
        )}
      </Modal>
    </section>
  );
}
