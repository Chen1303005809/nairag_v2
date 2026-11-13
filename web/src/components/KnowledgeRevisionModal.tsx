import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Form,
  Image,
  Input,
  Modal,
  Select,
  Space,
  Tag,
  Typography,
  Upload,
  message
} from "antd";
import { useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type {
  ChildContentInput,
  EditableContentEntry,
  EvidenceAttachment,
  KnowledgeContentTaxonomy,
  ParentContentInput,
  ReviewChildRevision,
  ReviewParentRevision,
  WebLinkInput
} from "../api/types";

const PARENT_CATEGORY_LABEL = "问题大类";
const CHILD_CATEGORY_LABEL = "问题小类";

export interface ChildContentFormValues {
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

export interface ParentContentFormValues {
  name: string;
  canonical_keyword: string;
  aliases?: string[];
}

export interface PublishedRevisionFormValues {
  parent?: ParentContentFormValues;
  primary_child?: ChildContentFormValues;
  child?: ChildContentFormValues;
  knowledge_base_ids?: string[];
}

function normalizeList(values: string[] | undefined): string[] {
  return (values ?? []).map((value) => value.trim()).filter(Boolean);
}

function nullable(value: string | undefined): string | null {
  const normalized = value?.trim();
  return normalized || null;
}

export function toChildContent(values: ChildContentFormValues): ChildContentInput {
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

export function toParentContent(values: ParentContentFormValues): ParentContentInput {
  return {
    name: values.name,
    canonical_keyword: values.canonical_keyword,
    lexical_rules: normalizeList(values.aliases).map((ruleValue) => ({
      rule_type: "alias" as const,
      rule_value: ruleValue
    }))
  };
}

export function toChildFormValues(revision: ReviewChildRevision): ChildContentFormValues {
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

export function toParentFormValues(revision: ReviewParentRevision): ParentContentFormValues {
  return {
    name: revision.name,
    canonical_keyword: revision.canonical_keyword,
    aliases: revision.lexical_rules
      .filter((rule) => rule.rule_type === "alias")
      .map((rule) => rule.rule_value)
  };
}

function taxonomySelectOptions(
  values: string[],
  preservedValue?: string | null
): Array<{ label: string; value: string }> {
  const options =
    preservedValue && !values.includes(preservedValue) ? [...values, preservedValue] : values;
  return options.map((value) => ({ label: value, value }));
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
            {fields.map(({ key, ...field }) => (
              <Space key={key} align="start" style={{ display: "flex", width: "100%" }}>
                <Form.Item
                  key={key}
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
        accept=".png,.jpg,.jpeg,.webp,.pdf,.doc,.docx,.xlsx,.pptx,.txt"
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
        支持 PNG、JPEG、WebP、PDF、DOC、DOCX、XLSX、PPTX、UTF-8 TXT；单个文件不超过 20 MB。图片附件可直接预览，其他类型点击文件名可下载查看。
      </Typography.Text>
    </Space>
  );
}

function ChildAttachmentField({ root }: { root: "primary_child" | "child" }): JSX.Element {
  return (
    <Form.Item name={[root, "attachments"]} label="附件">
      <AttachmentListInput />
    </Form.Item>
  );
}

export function ChildContentFields({
  root,
  taxonomy,
  preservedValues
}: {
  root: "primary_child" | "child";
  taxonomy: KnowledgeContentTaxonomy;
  preservedValues?: Pick<
    ChildContentInput,
    "question_type" | "business_object" | "purpose" | "customer_type"
  >;
}): JSX.Element {
  return (
    <>
      <Form.Item
        name={[root, "question"]}
        label={CHILD_CATEGORY_LABEL}
        rules={[{ required: true, message: `请输入${CHILD_CATEGORY_LABEL}` }]}
      >
        <Input.TextArea rows={2} placeholder="用户会提出的具体问题" />
      </Form.Item>
      <StructuredTextList
        name={[root, "question_variants"]}
        label="同义问句"
        addLabel="添加同义问句"
        placeholder="无需重复填写主问题"
      />
      <Form.Item
        name={[root, "response_content"]}
        label="回复内容"
        rules={[{ required: true, message: "请输入回复内容" }]}
      >
        <Input.TextArea rows={5} placeholder="审核通过后面向检索用户展示的回答" />
      </Form.Item>
      <div className="content-form-grid">
        <Form.Item
          name={[root, "question_type"]}
          label="问题类型"
          rules={[{ required: true, message: "请选择问题类型" }]}
        >
          <Select
            placeholder="--请选择--"
            options={taxonomySelectOptions(taxonomy.question_types, preservedValues?.question_type)}
          />
        </Form.Item>
        <Form.Item
          name={[root, "business_object"]}
          label="具体功能与模块"
          rules={[{ required: true, message: "请选择具体功能与模块" }]}
        >
          <Select
            placeholder="--请选择--"
            options={taxonomySelectOptions(
              taxonomy.business_objects,
              preservedValues?.business_object
            )}
          />
        </Form.Item>
        <Form.Item
          name={[root, "purpose"]}
          label="应用场景"
          rules={[{ required: true, message: "请选择应用场景" }]}
        >
          <Select
            placeholder="--请选择--"
            options={taxonomySelectOptions(taxonomy.purposes, preservedValues?.purpose)}
          />
        </Form.Item>
        <Form.Item
          name={[root, "customer_type"]}
          label="客户类型"
          rules={[{ required: true, message: "请选择客户类型" }]}
        >
          <Select
            placeholder="--请选择--"
            options={taxonomySelectOptions(taxonomy.customer_types, preservedValues?.customer_type)}
          />
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

export function ParentContentFields({ taxonomy }: { taxonomy: KnowledgeContentTaxonomy }): JSX.Element {
  return (
    <>
      <div className="content-form-grid">
        <Form.Item
          name={["parent", "name"]}
          label={PARENT_CATEGORY_LABEL}
          rules={[{ required: true, message: `请选择${PARENT_CATEGORY_LABEL}` }]}
        >
          <Select
            placeholder={`请选择${PARENT_CATEGORY_LABEL}`}
            options={taxonomySelectOptions(taxonomy.parent_types)}
          />
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

export function KnowledgeRevisionModal({
  entry,
  taxonomy,
  onClose,
  onSubmitted
}: {
  entry: EditableContentEntry;
  taxonomy: KnowledgeContentTaxonomy;
  onClose: () => void;
  onSubmitted?: () => Promise<void> | void;
}): JSX.Element {
  const [form] = Form.useForm<PublishedRevisionFormValues>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (entry.is_primary) {
      if (!entry.parent_revision) {
        message.error(`${PARENT_CATEGORY_LABEL}内容尚未加载，无法发起修订`);
        return;
      }
      form.setFieldsValue({
        parent: toParentFormValues(entry.parent_revision),
        primary_child: toChildFormValues(entry.child_revision)
      });
    } else {
      form.setFieldsValue({
        child: toChildFormValues(entry.child_revision),
        knowledge_base_ids: entry.knowledge_bases.map((knowledgeBase) => knowledgeBase.id)
      });
    }
  }, [entry, form]);

  const close = (): void => {
    if (!saving) {
      onClose();
      form.resetFields();
    }
  };

  const submit = async (values: PublishedRevisionFormValues): Promise<void> => {
    setSaving(true);
    try {
      if (entry.is_primary) {
        if (!values.parent || !values.primary_child) {
          throw new Error(`${PARENT_CATEGORY_LABEL}和${CHILD_CATEGORY_LABEL}内容不能为空`);
        }
        await api.createParentRevision(
          entry.parent_id,
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
        await api.createChildRevision(entry.child_id, toChildContent(values.child), knowledgeBaseIds);
      }
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "提交修订失败，请稍后重试");
      setSaving(false);
      return;
    }
    message.success("已生成新修订并提交审核，审核通过后会重新嵌入");
    onClose();
    form.resetFields();
    await onSubmitted?.();
  };

  return (
    <Modal
      open
      title="修改已发布知识并提交审核"
      onCancel={close}
      footer={null}
      destroyOnClose
      width={760}
    >
      <Alert
        type="warning"
        showIcon
        message="线上版本会继续服务，直到新修订审核并重新嵌入成功"
        description={
          entry.is_primary
            ? `与${PARENT_CATEGORY_LABEL}一同创建的${CHILD_CATEGORY_LABEL}必须和${PARENT_CATEGORY_LABEL}一起修订，并在全部已发布目标知识库完成审核后统一生效。`
            : `${CHILD_CATEGORY_LABEL}会仅在选中的目标知识库创建新候选并重新嵌入。`
        }
      />
      <Form<PublishedRevisionFormValues>
        form={form}
        layout="vertical"
        onFinish={(values) => void submit(values)}
        requiredMark
        style={{ marginTop: 16 }}
      >
        {entry.is_primary ? (
          <>
            <Typography.Title level={5}>{PARENT_CATEGORY_LABEL}</Typography.Title>
            <ParentContentFields taxonomy={taxonomy} />
            <Typography.Title level={5}>{CHILD_CATEGORY_LABEL}</Typography.Title>
            <ChildContentFields
              root="primary_child"
              taxonomy={taxonomy}
              preservedValues={entry.child_revision}
            />
            <Form.Item label="重新审核知识库">
              <Space size={[4, 4]} wrap>
                {entry.knowledge_bases.map((knowledgeBase) => (
                  <Tag key={knowledgeBase.id}>{knowledgeBase.name}</Tag>
                ))}
              </Space>
            </Form.Item>
          </>
        ) : (
          <>
            <Typography.Text type="secondary">
              {PARENT_CATEGORY_LABEL}：{entry.parent_name}
            </Typography.Text>
            <ChildContentFields
              root="child"
              taxonomy={taxonomy}
              preservedValues={entry.child_revision}
            />
            <Form.Item
              name="knowledge_base_ids"
              label="重新审核知识库"
              rules={[{ required: true, message: "请选择至少一个知识库" }]}
            >
              <Checkbox.Group
                className="knowledge-base-options"
                options={entry.knowledge_bases.map((knowledgeBase) => ({
                  label: knowledgeBase.name,
                  value: knowledgeBase.id
                }))}
              />
            </Form.Item>
          </>
        )}
        <Button type="primary" htmlType="submit" loading={saving}>
          提交新修订审核
        </Button>
      </Form>
    </Modal>
  );
}
