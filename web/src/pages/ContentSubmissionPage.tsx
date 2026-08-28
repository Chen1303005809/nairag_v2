import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Form,
  Image,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message
} from "antd";
import type { TableProps } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import {
  assertBothPartiesPresent,
  assertConversationWithinLimits,
  ConversationParseError,
  prepareWecomConversation
} from "../conversation";
import { ConversationEditor } from "../components/ConversationEditor";
import type { ConversationEditorHandle } from "../components/ConversationEditor";
import { KnowledgeDetailModal } from "../components/KnowledgeDetailModal";
import { uniqueTableFilterOptions } from "../tableFilters";
import type {
  AvailableParent,
  ChildContentInput,
  EditableContentEntry,
  EvidenceAttachment,
  IngestionBatch,
  IngestionBatchDetail,
  KnowledgeBase,
  KnowledgeDraft,
  KnowledgeDraftInput,
  ParentContentInput,
  ReviewChildRevision,
  ReviewParentRevision,
  ReviewSubmission,
  ReviewSubmissionStatus,
  WebLinkInput
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
  attachments?: EvidenceAttachment[];
  web_links?: WebLinkInput[];
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
  parent_id?: string;
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
    internal_notes: nullable(values.internal_notes),
    attachments: (values.attachments ?? []).map((attachment) => attachment.id),
    web_links: (values.web_links ?? []).map((webLink) => ({
      title: webLink.title.trim(),
      url: webLink.url.trim()
    }))
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
    internal_notes: revision.internal_notes ?? "",
    attachments: [...revision.attachments],
    web_links: revision.web_links.map((webLink) => ({ ...webLink }))
  };
}

function draftToChildFormValues(draft: KnowledgeDraft): ChildSubmissionFormValues {
  return {
    parent_id: draft.parent_id ?? undefined,
    child: {
      question: draft.question ?? "",
      response_content: draft.response_content ?? "",
      question_variants: [...draft.question_variants],
      follow_up_guidance: draft.follow_up_guidance ?? "",
      question_type: draft.question_type ?? "",
      business_object: draft.business_object ?? "",
      purpose: draft.purpose ?? "",
      customer_type: draft.customer_type ?? "",
      feature_explanation: draft.feature_explanation ?? "",
      example: draft.example ?? "",
      internal_notes: draft.internal_notes ?? "",
      attachments: [...draft.attachments],
      web_links: draft.web_links.map((webLink) => ({ ...webLink }))
    },
    knowledge_base_ids: [...draft.knowledge_base_ids]
  };
}

function childFormValuesToDraftInput(values: {
  parent_id?: string;
  child?: Partial<ChildContentFormValues>;
  knowledge_base_ids?: string[];
}): KnowledgeDraftInput {
  const child = values.child ?? {};
  return {
    parent_id: values.parent_id ?? null,
    question: child.question?.trim() || null,
    response_content: child.response_content?.trim() || null,
    question_variants: normalizeList(child.question_variants),
    follow_up_guidance: nullable(child.follow_up_guidance),
    question_type: nullable(child.question_type),
    business_object: nullable(child.business_object),
    purpose: nullable(child.purpose),
    customer_type: nullable(child.customer_type),
    feature_explanation: nullable(child.feature_explanation),
    example: nullable(child.example),
    internal_notes: nullable(child.internal_notes),
    attachments: (child.attachments ?? []).map((attachment) => attachment.id),
    web_links: (child.web_links ?? [])
      .map((webLink) => ({ title: webLink.title.trim(), url: webLink.url.trim() }))
      .filter((webLink) => webLink.title && webLink.url),
    knowledge_base_ids: values.knowledge_base_ids ?? []
  };
}

function draftHasBusinessContent(input: KnowledgeDraftInput): boolean {
  return Boolean(
    input.question ||
      input.response_content ||
      (input.question_variants && input.question_variants.length > 0) ||
      input.follow_up_guidance ||
      input.question_type ||
      input.business_object ||
      input.purpose ||
      input.customer_type ||
      input.feature_explanation ||
      input.example ||
      input.internal_notes ||
      (input.attachments && input.attachments.length > 0) ||
      (input.web_links && input.web_links.length > 0)
  );
}

function draftSourceTag(source: KnowledgeDraft["source"]): JSX.Element {
  return source === "intelligent_generated" ? (
    <Tag color="purple">智能生成</Tag>
  ) : (
    <Tag color="cyan">手动保存</Tag>
  );
}

function ingestionStatusTag(status: IngestionBatch["status"]): JSX.Element {
  const label: Record<IngestionBatch["status"], [string, string]> = {
    processing: ["processing", "处理中"],
    completed: ["success", "已完成"],
    completed_with_warnings: ["warning", "已完成，有警告"],
    failed: ["error", "失败"]
  };
  const [color, text] = label[status];
  return <Tag color={color}>{text}</Tag>;
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

function StructuredWebLinkList({ root }: { root: "primary_child" | "child" }): JSX.Element {
  return (
    <Form.Item label="相关网页链接">
      <Form.List name={[root, "web_links"]}>
        {(fields, { add, remove }) => (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            {fields.map((field) => (
              <Space key={field.key} align="start" style={{ display: "flex", width: "100%" }}>
                <Form.Item
                  name={[field.name, "title"]}
                  rules={[{ required: true, whitespace: true, message: "请输入链接标题" }]}
                  style={{ flex: 1, marginBottom: 0 }}
                >
                  <Input placeholder="链接标题" />
                </Form.Item>
                <Form.Item
                  name={[field.name, "url"]}
                  rules={[
                    { required: true, whitespace: true, message: "请输入链接地址" },
                    { type: "url", message: "请输入有效的链接地址" }
                  ]}
                  style={{ flex: 2, marginBottom: 0 }}
                >
                  <Input placeholder="https://example.com" />
                </Form.Item>
                <Button type="link" danger onClick={() => remove(field.name)}>
                  删除
                </Button>
              </Space>
            ))}
            <Button type="dashed" onClick={() => add()} block>
              + 添加网页链接
            </Button>
          </Space>
        )}
      </Form.List>
    </Form.Item>
  );
}

function formatAttachmentSize(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024) {
    return `${Math.max(1, Math.ceil(sizeBytes / 1024))} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ChildAttachmentField({ root }: { root: "primary_child" | "child" }): JSX.Element {
  return (
    <Form.Item name={[root, "attachments"]} label="附件">
      <AttachmentListInput />
    </Form.Item>
  );
}

function AttachmentListInput({
  value,
  onChange
}: {
  value?: EvidenceAttachment[];
  onChange?: (attachments: EvidenceAttachment[]) => void;
}): JSX.Element {
  const attachments = value ?? [];
  const [uploading, setUploading] = useState(false);

  const upload = async (file: File): Promise<void> => {
    if (attachments.length >= 10) {
      message.warning("每个知识修订最多添加 10 个附件");
      return;
    }
    setUploading(true);
    try {
      const attachment = await api.uploadKnowledgeAttachment(file);
      onChange?.([...attachments, attachment]);
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "附件上传失败");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (attachment: EvidenceAttachment): Promise<void> => {
    try {
      await api.deleteKnowledgeAttachment(attachment.id);
    } catch (reason) {
      if (!(reason instanceof ApiError) || ![404, 409].includes(reason.status)) {
        message.error(reason instanceof Error ? reason.message : "附件删除失败");
        return;
      }
    }
    onChange?.(attachments.filter((item) => item.id !== attachment.id));
  };

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {attachments.map((attachment) => (
        <Space key={attachment.id} wrap>
          {attachment.content_type.startsWith("image/") ? (
            <Image
              alt={attachment.name}
              src={api.knowledgeAttachmentDownloadUrl(attachment.id)}
              width={64}
              height={64}
              style={{ objectFit: "cover" }}
            />
          ) : null}
          <Space direction="vertical" size={0}>
            {attachment.content_type.startsWith("image/") ? (
              <Typography.Text>{attachment.name}</Typography.Text>
            ) : (
              <a
                href={api.knowledgeAttachmentDownloadUrl(attachment.id)}
                rel="noreferrer"
                target="_blank"
              >
                {attachment.name}
              </a>
            )}
            <Typography.Text type="secondary">
              {formatAttachmentSize(attachment.size_bytes)}
            </Typography.Text>
          </Space>
          <Button type="link" danger size="small" onClick={() => void remove(attachment)}>
            移除
          </Button>
        </Space>
      ))}
      <Upload
        accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.xlsx,.pptx,.txt"
        beforeUpload={(file) => {
          void upload(file);
          return Upload.LIST_IGNORE;
        }}
        disabled={uploading || attachments.length >= 10}
        showUploadList={false}
      >
        <Button loading={uploading} disabled={attachments.length >= 10}>
          上传附件
        </Button>
      </Upload>
      <Typography.Text type="secondary">
        支持 PNG、JPEG、WebP、PDF、DOCX、XLSX、PPTX、UTF-8 TXT；单个文件不超过 20 MB。图片附件可直接预览，其他类型点击文件名可下载查看。
      </Typography.Text>
    </Space>
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
      <ChildAttachmentField root={root} />
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
                <StructuredWebLinkList root={root} />
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
  const [viewingPublishedEntry, setViewingPublishedEntry] = useState<EditableContentEntry | null>(null);
  const [savingPublishedRevision, setSavingPublishedRevision] = useState(false);
  const [activeSubmissionTab, setActiveSubmissionTab] = useState("parent");
  const [drafts, setDrafts] = useState<KnowledgeDraft[]>([]);
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);
  const ingestionEditorRef = useRef<ConversationEditorHandle>(null);
  const [ingestionLoading, setIngestionLoading] = useState(false);
  const [ingestionBatches, setIngestionBatches] = useState<IngestionBatch[]>([]);
  const [ingestionBatch, setIngestionBatch] = useState<IngestionBatchDetail | null>(null);
  const selectedParentId = Form.useWatch("parent_id", childForm);

  const selectedParent = useMemo(
    () => availableParents.find((parent) => parent.id === selectedParentId),
    [availableParents, selectedParentId]
  );

  const refresh = async (): Promise<void> => {
    setLoading(true);
    try {
      const [
        nextKnowledgeBases,
        nextParents,
        nextSubmissions,
        nextEditableEntries,
        nextDrafts,
        nextIngestionBatches
      ] = await Promise.all([
        api.listKnowledgeBases(),
        api.listAvailableParents(),
        api.listMyContentSubmissions(),
        api.listEditableContentEntries(),
        api.listKnowledgeDrafts(),
        api.listIngestionBatches()
      ]);
      setKnowledgeBases(nextKnowledgeBases);
      setAvailableParents(nextParents);
      setSubmissions(nextSubmissions);
      setEditableEntries(nextEditableEntries);
      setDrafts(nextDrafts);
      setIngestionBatches(nextIngestionBatches);
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
    const allowedKnowledgeBaseIds = new Set(
      selectedParent?.available_knowledge_bases.map((knowledgeBase) => knowledgeBase.id) ?? []
    );
    const selectedKnowledgeBaseIds = childForm.getFieldValue("knowledge_base_ids") ?? [];
    const validKnowledgeBaseIds = selectedKnowledgeBaseIds.filter((knowledgeBaseId: string) =>
      allowedKnowledgeBaseIds.has(knowledgeBaseId)
    );
    if (validKnowledgeBaseIds.length !== selectedKnowledgeBaseIds.length) {
      childForm.setFieldValue("knowledge_base_ids", validKnowledgeBaseIds);
    }
  }, [childForm, selectedParent]);

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
      if (editingDraftId) {
        await api.updateKnowledgeDraft(
          editingDraftId,
          childFormValuesToDraftInput(values)
        );
        await api.submitKnowledgeDraft(editingDraftId);
        setEditingDraftId(null);
      } else {
        await api.createChildSubmission(
          values.parent_id!,
          toChildContent(values.child),
          values.knowledge_base_ids
        );
      }
      childForm.resetFields();
      message.success(
        editingDraftId ? "草稿已提交审核，草稿已删除" : `${CHILD_CATEGORY_LABEL}已提交审核`
      );
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "提交失败，请稍后重试");
    } finally {
      setSubmittingChild(false);
    }
  };

  const saveChildDraft = async (): Promise<void> => {
    const values = childForm.getFieldsValue();
    const draftInput = childFormValuesToDraftInput(values);
    if (!draftHasBusinessContent(draftInput)) {
      message.warning("草稿至少需要一个非空业务字段");
      return;
    }
    setSavingDraft(true);
    try {
      if (editingDraftId) {
        await api.updateKnowledgeDraft(editingDraftId, draftInput);
        message.success("草稿已更新");
      } else {
        const draft = await api.createKnowledgeDraft(draftInput);
        setEditingDraftId(draft.id);
        message.success("草稿已暂存");
      }
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "草稿暂存失败");
    } finally {
      setSavingDraft(false);
    }
  };

  const openDraft = (draft: KnowledgeDraft): void => {
    childForm.setFieldsValue(draftToChildFormValues(draft));
    setEditingDraftId(draft.id);
    setActiveSubmissionTab("child");
  };

  const removeDraft = async (draft: KnowledgeDraft): Promise<void> => {
    try {
      await api.deleteKnowledgeDraft(draft.id);
      if (editingDraftId === draft.id) {
        setEditingDraftId(null);
        childForm.resetFields();
      }
      message.success("草稿已删除");
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "草稿删除失败");
    }
  };

  const submitDraftDirectly = async (draft: KnowledgeDraft): Promise<void> => {
    if (!draft.parent_id || draft.knowledge_base_ids.length === 0) {
      message.warning("请先编辑草稿并选择父类与目标知识库");
      openDraft(draft);
      return;
    }
    try {
      await api.submitKnowledgeDraft(draft.id);
      message.success("草稿已提交审核");
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "草稿提交失败");
    }
  };

  const sleep = (milliseconds: number): Promise<void> =>
    new Promise((resolve) => setTimeout(resolve, milliseconds));

  const generateDrafts = async (): Promise<void> => {
    let batchId: string;
    setIngestionLoading(true);
    try {
      const editorValue = ingestionEditorRef.current?.getValue() ?? { text: "", images: [] };
      const preparedConversation = await prepareWecomConversation(
        editorValue.text,
        editorValue.images,
        api.recognizeConversationImage
      );
      assertBothPartiesPresent(preparedConversation.messages);
      assertConversationWithinLimits(preparedConversation.messages);
      if (preparedConversation.imageCount > 0) {
        ingestionEditorRef.current?.replaceWithText(preparedConversation.text);
        message.success(`已识别 ${preparedConversation.imageCount} 张聊天图片`);
      }
      setIngestionBatch(null);
      const batch = await api.createIngestionBatch(preparedConversation.messages);
      batchId = batch.id;
      setIngestionBatch({ ...batch, drafts: [] });
      setIngestionBatches((current) => [batch, ...current.filter((item) => item.id !== batch.id)]);
    } catch (reason) {
      setIngestionLoading(false);
      if (reason instanceof ConversationParseError) {
        message.warning(reason.message);
      } else {
        message.error(reason instanceof Error ? reason.message : "智能生成发起失败");
      }
      return;
    }

    try {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        await sleep(1500);
        const detail = await api.getIngestionBatch(batchId);
        setIngestionBatch(detail);
        if (detail.status !== "processing") {
          if (detail.status === "failed") {
            message.error(detail.last_error ?? "智能生成失败");
          } else if (detail.generated_count === 0) {
            message.info("本段会话没有生成草稿");
          } else {
            message.success(`已生成 ${detail.generated_count} 条草稿`);
          }
          await refresh();
          return;
        }
      }
      message.info("仍在处理中，稍后可在“我的草稿”查看结果");
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "查询智能生成状态失败");
    } finally {
      setIngestionLoading(false);
    }
  };

  const loadIngestionBatch = async (batchId: string): Promise<void> => {
    try {
      setIngestionBatch(await api.getIngestionBatch(batchId));
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "无法加载智能生成批次");
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
      width: 220,
      render: (_value: unknown, entry: EditableContentEntry) => (
        <Space size="small">
          <Button type="link" onClick={() => setViewingPublishedEntry(entry)}>
            查看细则
          </Button>
          <Button type="link" onClick={() => openPublishedRevision(entry)}>
            修改并提交审核
          </Button>
        </Space>
      )
    }
  ];

  const draftColumns: TableProps<KnowledgeDraft>["columns"] = [
    {
      title: "来源",
      dataIndex: "source",
      key: "source",
      width: 110,
      render: (source: KnowledgeDraft["source"]) => draftSourceTag(source)
    },
    {
      title: CHILD_CATEGORY_LABEL,
      dataIndex: "question",
      key: "question",
      render: (question: string | null, draft: KnowledgeDraft) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{question || "未填写问题"}</Typography.Text>
          {draft.response_content ? (
            <Typography.Text type="secondary" ellipsis={{ tooltip: draft.response_content }}>
              {draft.response_content}
            </Typography.Text>
          ) : null}
        </Space>
      )
    },
    {
      title: PARENT_CATEGORY_LABEL,
      dataIndex: "parent_id",
      key: "parent_id",
      render: (parentId: string | null) => {
        const parent = availableParents.find((item) => item.id === parentId);
        return parent ? `${parent.canonical_keyword}（${parent.name}）` : "未选择";
      }
    },
    {
      title: "目标知识库",
      dataIndex: "knowledge_base_ids",
      key: "knowledge_base_ids",
      render: (knowledgeBaseIds: string[]) => (
        <Space size={[4, 4]} wrap>
          {knowledgeBaseIds.length === 0 ? (
            <Typography.Text type="secondary">未选择</Typography.Text>
          ) : (
            knowledgeBaseIds.map((knowledgeBaseId) => {
              const knowledgeBase = knowledgeBases.find((item) => item.id === knowledgeBaseId);
              return <Tag key={knowledgeBaseId}>{knowledgeBase?.name ?? "已不可用的知识库"}</Tag>;
            })
          )}
        </Space>
      )
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 180,
      render: (value: string) => formatDateTime(value)
    },
    {
      title: "操作",
      key: "actions",
      width: 220,
      render: (_value: unknown, draft: KnowledgeDraft) => (
        <Space>
          <Button type="link" onClick={() => openDraft(draft)}>
            编辑
          </Button>
          <Button type="link" onClick={() => void submitDraftDirectly(draft)}>
            提交审核
          </Button>
          <Popconfirm
            title="删除此草稿？"
            description="删除后无法恢复。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => void removeDraft(draft)}
          >
            <Button type="link" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      )
    }
  ];

  const ingestionBatchColumns: TableProps<IngestionBatch>["columns"] = [
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: IngestionBatch["status"]) => ingestionStatusTag(status)
    },
    {
      title: "生成 / 未生成",
      key: "counts",
      render: (_value: unknown, batch: IngestionBatch) =>
        `${batch.generated_count} / ${batch.rejected_count}`
    },
    {
      title: "发起时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (value: string) => formatDateTime(value)
    },
    {
      title: "操作",
      key: "actions",
      render: (_value: unknown, batch: IngestionBatch) => (
        <Button type="link" onClick={() => void loadIngestionBatch(batch.id)}>
          查看详情
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
        activeKey={activeSubmissionTab}
        onChange={setActiveSubmissionTab}
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
            key: "fast-upload",
            label: "快速上传",
            children: (
              <Card>
                <Space direction="vertical" size={16} style={{ width: "100%" }}>
                  <Alert
                    type="info"
                    showIcon
                    message="从会话生成普通子条目草稿"
                    description="可直接粘贴企业微信转发卡片。系统仅提炼可复用知识，生成的内容会保存在仅自己可见的草稿中；卡片中的图片会先 OCR 并替换“[图片]”，不会自动选择问题大类或知识库。"
                  />
                  <ConversationEditor
                    ref={ingestionEditorRef}
                    ariaLabel="快速上传聊天内容"
                    placeholder={
                      "客户A 09:30\n登录一直失败怎么办？\n\n融航-支持专员 09:31\n我先查询一下。"
                    }
                  />
                  <Space wrap>
                    <Button
                      type="primary"
                      loading={ingestionLoading}
                      onClick={() => void generateDrafts()}
                    >
                      智能生成草稿
                    </Button>
                    <Typography.Text type="secondary">
                      说话人名称中包含“融航”视为我方；必须能识别客户与我方双方发言。
                    </Typography.Text>
                  </Space>
                  {ingestionBatch ? (
                    <Card
                      size="small"
                      title={
                        <Space>
                          <span>当前批次</span>
                          {ingestionStatusTag(ingestionBatch.status)}
                        </Space>
                      }
                    >
                      <Space direction="vertical" size={8} style={{ width: "100%" }}>
                        <Typography.Text>
                          已生成 {ingestionBatch.generated_count} 条草稿；未生成 {ingestionBatch.rejected_count} 条。
                        </Typography.Text>
                        {ingestionBatch.last_error ? (
                          <Alert type="error" showIcon message={ingestionBatch.last_error} />
                        ) : null}
                        {ingestionBatch.rejection_reasons.length > 0 ? (
                          <Space direction="vertical" size={4} style={{ width: "100%" }}>
                            <Typography.Text type="secondary">未生成原因</Typography.Text>
                            {ingestionBatch.rejection_reasons.map((reason, index) => (
                              <Typography.Text key={`${reason.topic}-${index}`} type="secondary">
                                {reason.topic}：{reason.reason}
                              </Typography.Text>
                            ))}
                          </Space>
                        ) : null}
                        {ingestionBatch.drafts.length > 0 ? (
                          <Space direction="vertical" size={4} style={{ width: "100%" }}>
                            <Typography.Text type="secondary">本批生成的草稿</Typography.Text>
                            {ingestionBatch.drafts.map((draft) => (
                              <Space key={draft.id} wrap>
                                {draftSourceTag(draft.source)}
                                <Typography.Text>{draft.question ?? "未命名草稿"}</Typography.Text>
                                <Button type="link" onClick={() => openDraft(draft)}>
                                  编辑草稿
                                </Button>
                              </Space>
                            ))}
                          </Space>
                        ) : null}
                      </Space>
                    </Card>
                  ) : null}
                  <div>
                    <Typography.Title level={5}>最近智能生成批次</Typography.Title>
                    <Table<IngestionBatch>
                      rowKey="id"
                      size="small"
                      columns={ingestionBatchColumns}
                      dataSource={ingestionBatches}
                      pagination={{ pageSize: 5, hideOnSinglePage: true }}
                      locale={{ emptyText: "尚未发起智能生成" }}
                    />
                  </div>
                </Space>
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
                    description={`仍可先暂存草稿；正式提交前需等待${PARENT_CATEGORY_LABEL}与${CHILD_CATEGORY_LABEL}发布。`}
                    style={{ marginBottom: 16 }}
                  />
                ) : null}
                <Form<ChildSubmissionFormValues>
                  form={childForm}
                  layout="vertical"
                  onFinish={(values) => void submitChild(values)}
                  requiredMark
                >
                  <Alert
                    type="info"
                    showIcon
                    message="暂存草稿允许字段不完整"
                    description={`可先填写${CHILD_CATEGORY_LABEL}内容，稍后再选择${PARENT_CATEGORY_LABEL}和目标知识库；提交审核时才执行完整校验。`}
                    style={{ marginBottom: 16 }}
                  />
                  <Form.Item
                    name="parent_id"
                    label={PARENT_CATEGORY_LABEL}
                    rules={[{ required: true, message: `请选择${PARENT_CATEGORY_LABEL}` }]}
                  >
                    <Select
                      allowClear
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
                  <Space wrap>
                    <Button type="primary" htmlType="submit" loading={submittingChild}>
                      {editingDraftId ? "提交草稿审核" : "提交候选"}
                    </Button>
                    <Button loading={savingDraft} onClick={() => void saveChildDraft()}>
                      {editingDraftId ? "更新草稿" : "暂存草稿"}
                    </Button>
                    {editingDraftId ? (
                      <Button
                        onClick={() => {
                          childForm.resetFields();
                          setEditingDraftId(null);
                        }}
                      >
                        取消编辑草稿
                      </Button>
                    ) : null}
                  </Space>
                </Form>
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
            key: "drafts",
            label: `我的草稿${drafts.length > 0 ? ` (${drafts.length})` : ""}`,
            children: (
              <Card>
                <Alert
                  type="info"
                  showIcon
                  message="草稿仅自己可见"
                  description="智能生成和手动暂存的草稿均可继续编辑。提交审核成功后，该草稿会立即删除。"
                  style={{ marginBottom: 16 }}
                />
                <Table<KnowledgeDraft>
                  rowKey="id"
                  loading={loading}
                  columns={draftColumns}
                  dataSource={drafts}
                  scroll={{ x: 1050 }}
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  locale={{ emptyText: "尚未暂存草稿" }}
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
      <KnowledgeDetailModal
        open={viewingPublishedEntry !== null}
        onClose={() => setViewingPublishedEntry(null)}
        childRevision={viewingPublishedEntry?.child_revision ?? null}
        parentRevision={viewingPublishedEntry?.parent_revision}
        parentName={viewingPublishedEntry?.parent_name}
      />
    </section>
  );
}
