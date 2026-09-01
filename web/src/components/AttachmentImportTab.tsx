import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Form,
  Input,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message
} from "antd";
import type { TableProps } from "antd";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type {
  AttachmentImportBatch,
  AttachmentImportBatchDetail,
  AttachmentImportCandidate,
  AvailableParent,
  KnowledgeBase,
  KnowledgeContentTaxonomy,
  ParentContentInput
} from "../api/types";
import { formatDateTime } from "../dateTime";

interface AttachmentImportFormValues {
  target: "new" | "existing";
  parent?: {
    name: string;
    canonical_keyword: string;
    aliases?: string[];
  };
  existing_parent_id?: string;
  primary_child_id: string;
  children: AttachmentImportCandidate[];
  knowledge_base_ids?: string[];
}

function nullable(value: string | null | undefined): string | null {
  const normalized = value?.trim();
  return normalized || null;
}

function statusTag(status: AttachmentImportBatch["status"]): JSX.Element {
  const labels: Record<AttachmentImportBatch["status"], [string, string]> = {
    processing: ["processing", "处理中"],
    ready: ["success", "待确认"],
    ready_with_warnings: ["warning", "待确认，有警告"],
    failed: ["error", "失败"],
    submitted: ["blue", "已提交"]
  };
  const [color, label] = labels[status];
  return <Tag color={color}>{label}</Tag>;
}

function toParentContent(values: AttachmentImportFormValues["parent"]): ParentContentInput {
  if (!values) {
    throw new Error("请填写问题大类信息");
  }
  return {
    name: values.name,
    canonical_keyword: values.canonical_keyword,
    lexical_rules: (values.aliases ?? [])
      .map((value) => value.trim())
      .filter(Boolean)
      .map((rule_value) => ({ rule_type: "alias" as const, rule_value }))
  };
}

function toCandidate(candidate: AttachmentImportCandidate): AttachmentImportCandidate {
  return {
    ...candidate,
    id: candidate.id.trim(),
    question: candidate.question.trim(),
    response_content: candidate.response_content.trim(),
    question_variants: (candidate.question_variants ?? []).map((value) => value.trim()).filter(Boolean),
    follow_up_guidance: nullable(candidate.follow_up_guidance),
    question_type: nullable(candidate.question_type),
    business_object: nullable(candidate.business_object),
    purpose: nullable(candidate.purpose),
    customer_type: nullable(candidate.customer_type),
    feature_explanation: nullable(candidate.feature_explanation),
    example: nullable(candidate.example),
    internal_notes: nullable(candidate.internal_notes)
  };
}

function attachmentImportFormValues(detail: AttachmentImportBatchDetail): AttachmentImportFormValues | null {
  if (!detail.proposal) {
    return null;
  }
  return {
    target: "new",
    parent: {
      name: detail.proposal.parent.name,
      canonical_keyword: detail.proposal.parent.canonical_keyword,
      aliases: [...detail.proposal.parent.aliases]
    },
    primary_child_id: detail.proposal.recommended_primary_child_id,
    children: detail.proposal.children.map((candidate) => ({ ...candidate })),
    knowledge_base_ids: []
  };
}

function CandidateEditor({
  taxonomy,
  field,
  index,
  total,
  onRemove,
  onMove
}: {
  taxonomy: KnowledgeContentTaxonomy;
  field: { key: number; name: number };
  index: number;
  total: number;
  onRemove: () => void;
  onMove: (offset: number) => void;
}): JSX.Element {
  const selectOptions = (values: string[]) => values.map((value) => ({ label: value, value }));
  return (
    <Card
      size="small"
      title={`问题小类 ${index + 1}`}
      extra={
        <Space>
          <Button disabled={index === 0} size="small" onClick={() => onMove(-1)}>
            上移
          </Button>
          <Button disabled={index === total - 1} size="small" onClick={() => onMove(1)}>
            下移
          </Button>
          <Button danger size="small" onClick={onRemove}>
            删除
          </Button>
        </Space>
      }
    >
      <Form.Item name={[field.name, "id"]} hidden>
        <Input />
      </Form.Item>
      <Form.Item
        name={[field.name, "question"]}
        label="问题小类"
        rules={[{ required: true, whitespace: true, message: "请输入问题小类" }]}
      >
        <Input.TextArea rows={2} />
      </Form.Item>
      <Form.Item
        name={[field.name, "response_content"]}
        label="回复内容"
        rules={[{ required: true, whitespace: true, message: "请输入回复内容" }]}
      >
        <Input.TextArea rows={4} />
      </Form.Item>
      <Form.Item name={[field.name, "question_variants"]} label="同义问句">
        <Select mode="tags" tokenSeparators={["\n", "，", ","]} placeholder="输入后按 Enter 添加" />
      </Form.Item>
      <div className="content-form-grid">
        <Form.Item
          name={[field.name, "question_type"]}
          label="问题类型"
          rules={[{ required: true, message: "请选择问题类型" }]}
        >
          <Select options={selectOptions(taxonomy.question_types)} />
        </Form.Item>
        <Form.Item
          name={[field.name, "business_object"]}
          label="具体功能与模块"
          rules={[{ required: true, message: "请选择具体功能与模块" }]}
        >
          <Select options={selectOptions(taxonomy.business_objects)} />
        </Form.Item>
        <Form.Item
          name={[field.name, "purpose"]}
          label="应用场景"
          rules={[{ required: true, message: "请选择应用场景" }]}
        >
          <Select options={selectOptions(taxonomy.purposes)} />
        </Form.Item>
        <Form.Item
          name={[field.name, "customer_type"]}
          label="客户类型"
          rules={[{ required: true, message: "请选择客户类型" }]}
        >
          <Select options={selectOptions(taxonomy.customer_types)} />
        </Form.Item>
      </div>
      <Collapse
        ghost
        items={[
          {
            key: "additional",
            label: "可补充说明",
            children: (
              <>
                <Form.Item name={[field.name, "feature_explanation"]} label="功能说明">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Form.Item name={[field.name, "example"]} label="示例">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Form.Item name={[field.name, "follow_up_guidance"]} label="后续指引">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Form.Item name={[field.name, "internal_notes"]} label="内部备注">
                  <Input.TextArea rows={2} />
                </Form.Item>
              </>
            )
          }
        ]}
      />
    </Card>
  );
}

export function AttachmentImportTab({
  taxonomy,
  knowledgeBases,
  availableParents,
  onConfirmed
}: {
  taxonomy: KnowledgeContentTaxonomy;
  knowledgeBases: KnowledgeBase[];
  availableParents: AvailableParent[];
  onConfirmed: () => Promise<void>;
}): JSX.Element {
  const [form] = Form.useForm<AttachmentImportFormValues>();
  const [batches, setBatches] = useState<AttachmentImportBatch[]>([]);
  const [selected, setSelected] = useState<AttachmentImportBatchDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const selectedTarget = Form.useWatch("target", form);
  const selectedExistingParentId = Form.useWatch("existing_parent_id", form);
  const formChildren = Form.useWatch("children", form) ?? [];
  const selectedExistingParent = useMemo(
    () => availableParents.find((parent) => parent.id === selectedExistingParentId),
    [availableParents, selectedExistingParentId]
  );

  const isAvailable = (): boolean => typeof api.listAttachmentImportBatches === "function";

  const refresh = async (): Promise<void> => {
    if (!isAvailable()) {
      return;
    }
    setLoading(true);
    try {
      setBatches(await api.listAttachmentImportBatches());
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "无法加载附件解析批次");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const showDetail = async (batchId: string): Promise<AttachmentImportBatchDetail | null> => {
    if (!isAvailable()) {
      return null;
    }
    try {
      const detail = await api.getAttachmentImportBatch(batchId);
      setSelected(detail);
      const values = attachmentImportFormValues(detail);
      if (values) {
        form.setFieldsValue(values);
      }
      return detail;
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "无法读取附件解析方案");
      return null;
    }
  };

  const poll = async (batchId: string): Promise<void> => {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const detail = await showDetail(batchId);
      if (!detail || detail.status !== "processing") {
        await refresh();
        return;
      }
    }
    message.info("附件仍在处理中，可稍后从最近批次继续查看。");
  };

  const upload = async (file: File): Promise<void> => {
    if (typeof api.createAttachmentImportBatch !== "function") {
      message.error("当前 API 尚未提供附件解析功能");
      return;
    }
    setUploading(true);
    try {
      const batch = await api.createAttachmentImportBatch(file);
      setBatches((current) => [batch, ...current.filter((item) => item.id !== batch.id)]);
      await showDetail(batch.id);
      await poll(batch.id);
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "附件解析发起失败");
    } finally {
      setUploading(false);
    }
  };

  const retry = async (batchId: string): Promise<void> => {
    try {
      const batch = await api.retryAttachmentImportBatch(batchId);
      setBatches((current) => [batch, ...current.filter((item) => item.id !== batch.id)]);
      await showDetail(batch.id);
      void poll(batch.id);
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "重试附件解析失败");
    }
  };

  const remove = async (batchId: string): Promise<void> => {
    try {
      await api.deleteAttachmentImportBatch(batchId);
      setBatches((current) => current.filter((item) => item.id !== batchId));
      if (selected?.id === batchId) {
        setSelected(null);
        form.resetFields();
      }
      message.success("附件解析批次已取消");
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "取消附件解析失败");
    }
  };

  const confirm = async (values: AttachmentImportFormValues): Promise<void> => {
    if (!selected) {
      return;
    }
    setConfirming(true);
    try {
      const target = values.target;
      const response = await api.confirmAttachmentImportBatch(selected.id, {
        target,
        parent: target === "new" ? toParentContent(values.parent) : null,
        existing_parent_id: target === "existing" ? values.existing_parent_id ?? null : null,
        primary_child_id: values.primary_child_id,
        children: values.children.map(toCandidate),
        knowledge_base_ids: target === "new" ? values.knowledge_base_ids ?? [] : []
      });
      message.success(
        `已提交主小类审核${response.created_draft_ids.length ? `，并生成 ${response.created_draft_ids.length} 条普通小类草稿` : ""}`
      );
      await Promise.all([onConfirmed(), refresh(), showDetail(selected.id)]);
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "确认附件解析方案失败");
    } finally {
      setConfirming(false);
    }
  };

  const columns: TableProps<AttachmentImportBatch>["columns"] = [
    {
      title: "原附件",
      key: "attachment",
      width: 260,
      render: (_value, batch) => batch.attachment.name
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 130,
      render: (value: AttachmentImportBatch["status"]) => statusTag(value)
    },
    {
      title: "发起时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 180,
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (value: string) => formatDateTime(value)
    },
    {
      title: "操作",
      key: "actions",
      width: 220,
      render: (_value, batch) => (
        <Space size={4} wrap>
          <Button type="link" onClick={() => void showDetail(batch.id)}>
            查看
          </Button>
          {batch.status === "failed" ? (
            <Button type="link" onClick={() => void retry(batch.id)}>
              重试
            </Button>
          ) : null}
          {batch.status !== "submitted" ? (
            <Popconfirm
              title="取消附件解析批次？"
              description="将删除未绑定的原附件和解析方案。"
              okText="取消批次"
              cancelText="保留"
              onConfirm={() => void remove(batch.id)}
            >
              <Button type="link" danger>
                取消
              </Button>
            </Popconfirm>
          ) : null}
        </Space>
      )
    }
  ];

  const parentOptions = taxonomy.parent_types.map((value) => ({ label: value, value }));
  const knowledgeBaseOptions = knowledgeBases.map((knowledgeBase) => ({
    label: knowledgeBase.name,
    value: knowledgeBase.id
  }));

  return (
    <Card>
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="从一个 DOC 或 DOCX 附件生成可编辑的知识方案"
          description="系统只处理正文，不执行附件内的指令，也不会 OCR 内嵌图片。确认前请检查生成内容和固定分类；原文件会随主小类按知识发布范围对登录用户可见。"
        />
        <Upload
          accept=".doc,.docx"
          beforeUpload={(file) => {
            void upload(file);
            return Upload.LIST_IGNORE;
          }}
          showUploadList={false}
          disabled={uploading}
        >
          <Button type="primary" loading={uploading}>
            上传并解析附件
          </Button>
        </Upload>
        <Typography.Text type="secondary">一次只能上传一个 DOC 或 DOCX，单文件不超过 20 MB。</Typography.Text>

        {selected ? (
          <Card
            size="small"
            title={
              <Space>
                <span>{selected.attachment.name}</span>
                {statusTag(selected.status)}
                <Typography.Link
                  href={api.knowledgeAttachmentDownloadUrl(selected.attachment.id)}
                  rel="noreferrer"
                  target="_blank"
                >
                  查看原附件
                </Typography.Link>
              </Space>
            }
          >
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              {selected.last_error ? <Alert type="error" showIcon message={selected.last_error} /> : null}
              {selected.warnings.map((warning, index) => (
                <Alert key={`${warning}-${index}`} type="warning" showIcon message={warning} />
              ))}
              {selected.status === "processing" ? (
                <Typography.Text type="secondary">解析正在后台执行，页面关闭后也可从最近批次继续查看。</Typography.Text>
              ) : null}
              {selected.status === "submitted" ? (
                <Alert
                  type="success"
                  showIcon
                  message="该附件解析方案已提交"
                  description={`投稿 ID：${selected.final_submission_id ?? "-"}`}
                />
              ) : null}
              {selected.proposal ? (
                <Form<AttachmentImportFormValues>
                  form={form}
                  layout="vertical"
                  onFinish={(values) => void confirm(values)}
                  requiredMark
                >
                  <Form.Item name="target" label="处理方式" rules={[{ required: true }]}>
                    <Radio.Group>
                      <Radio value="new">新建问题大类</Radio>
                      <Radio value="existing">归并已有问题大类</Radio>
                    </Radio.Group>
                  </Form.Item>
                  {selectedTarget === "new" ? (
                    <>
                      <div className="content-form-grid">
                        <Form.Item
                          name={["parent", "name"]}
                          label="问题大类"
                          rules={[{ required: true, message: "请选择问题大类" }]}
                        >
                          <Select options={parentOptions} />
                        </Form.Item>
                        <Form.Item
                          name={["parent", "canonical_keyword"]}
                          label="问题大类关键词"
                          rules={[{ required: true, whitespace: true, message: "请输入问题大类关键词" }]}
                        >
                          <Input />
                        </Form.Item>
                      </div>
                      <Form.Item name={["parent", "aliases"]} label="别名">
                        <Select mode="tags" tokenSeparators={["\n", "，", ","]} />
                      </Form.Item>
                    </>
                  ) : (
                    <>
                      <Form.Item
                        name="existing_parent_id"
                        label="归并到已发布问题大类"
                        rules={[{ required: true, message: "请选择已发布问题大类" }]}
                      >
                        <Select
                          showSearch
                          optionFilterProp="label"
                          options={availableParents.map((parent) => ({
                            value: parent.id,
                            label: `${parent.canonical_keyword}（${parent.name}）`
                          }))}
                        />
                      </Form.Item>
                      {selected.proposal.similar_parents.length > 0 ? (
                        <Alert
                          type="info"
                          showIcon
                          message="相似已有大类"
                          description={
                            <Space wrap>
                              {selected.proposal.similar_parents.map((parent) => (
                                <Button
                                  key={parent.id}
                                  size="small"
                                  onClick={() => form.setFieldValue("existing_parent_id", parent.id)}
                                >
                                  {parent.canonical_keyword}（匹配 {parent.score}%）
                                </Button>
                              ))}
                            </Space>
                          }
                        />
                      ) : null}
                      {selectedExistingParent ? (
                        <Typography.Text type="secondary">
                          将在 {selectedExistingParent.available_knowledge_bases.map((item) => item.name).join("、")} 中重新审核主小类；所有解析小类会保存为普通小类草稿。
                        </Typography.Text>
                      ) : null}
                    </>
                  )}
                  <Form.Item
                    name="primary_child_id"
                    label="主问题小类"
                    rules={[{ required: true, message: "请选择主问题小类" }]}
                  >
                    <Select
                      options={formChildren.map((candidate: AttachmentImportCandidate) => ({
                        label: candidate.question || "未命名问题小类",
                        value: candidate.id
                      }))}
                    />
                  </Form.Item>
                  <Form.List name="children">
                    {(fields, { move, remove }) => (
                      <Space direction="vertical" size={12} style={{ width: "100%" }}>
                        {fields.map((field, index) => (
                          <CandidateEditor
                            key={field.key}
                            taxonomy={taxonomy}
                            field={field}
                            index={index}
                            total={fields.length}
                            onRemove={() => remove(field.name)}
                            onMove={(offset) => move(field.name, field.name + offset)}
                          />
                        ))}
                      </Space>
                    )}
                  </Form.List>
                  {selectedTarget === "new" ? (
                    <Form.Item
                      name="knowledge_base_ids"
                      label="目标知识库"
                      rules={[{ required: true, message: "请选择至少一个知识库" }]}
                    >
                      <Checkbox.Group className="knowledge-base-options" options={knowledgeBaseOptions} />
                    </Form.Item>
                  ) : null}
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={confirming}
                    disabled={selected.status === "submitted"}
                  >
                    确认并提交审核
                  </Button>
                </Form>
              ) : null}
            </Space>
          </Card>
        ) : null}

        <div>
          <Typography.Title level={5}>最近附件解析批次</Typography.Title>
          <Table<AttachmentImportBatch>
            rowKey="id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={batches}
            scroll={{ x: 700 }}
            pagination={{ pageSize: 5, hideOnSinglePage: true }}
            locale={{ emptyText: "尚未上传附件解析" }}
          />
        </div>
      </Space>
    </Card>
  );
}
