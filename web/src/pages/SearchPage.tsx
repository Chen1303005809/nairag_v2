import {
  Alert,
  Button,
  Card,
  Divider,
  Descriptions,
  Input,
  Select,
  Space,
  Tag,
  Tabs,
  Typography,
  Upload,
  message
} from "antd";
import { DeleteOutlined, LikeOutlined, PictureOutlined, SearchOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type {
  KnowledgeBase,
  OcrRecognition,
  SearchResponse,
  SearchResult,
  SearchRetrievalMode
} from "../api/types";
import {
  businessObjectOptions,
  customerTypeOptions,
  parentTypeOptions,
  purposeOptions,
  questionTypeOptions
} from "../constants/knowledgeOptions";

function imageFromClipboard(clipboardData: DataTransfer | null): File | undefined {
  const items = Array.from(clipboardData?.items ?? []);
  for (const item of items) {
    if (item.kind === "file" && (item.type.startsWith("image/") || !item.type)) {
      const image = item.getAsFile();
      if (image) {
        return image;
      }
    }
  }
  const files = Array.from(clipboardData?.files ?? []);
  return files.find((file) => file.type.startsWith("image/")) ?? files.find((file) => !file.type);
}

export function SearchPage(): JSX.Element {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>();
  const [retrievalMode, setRetrievalMode] = useState<SearchRetrievalMode>("vector");
  const [query, setQuery] = useState("");
  const [ocrRecognition, setOcrRecognition] = useState<OcrRecognition>();
  const [parentType, setParentType] = useState<string>();
  const [questionType, setQuestionType] = useState<string>();
  const [businessObject, setBusinessObject] = useState<string>();
  const [purpose, setPurpose] = useState<string>();
  const [customerType, setCustomerType] = useState<string>();
  const [result, setResult] = useState<SearchResponse>();
  const [loading, setLoading] = useState(false);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [feedbackIds, setFeedbackIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    void api
      .listKnowledgeBases()
      .then(setKnowledgeBases)
      .catch((reason) => {
        message.error(reason instanceof Error ? reason.message : "无法加载知识库");
      });
  }, []);

  const runSearch = async (): Promise<void> => {
    const normalizedQuery = query.trim();
    if (retrievalMode === "vector" && !normalizedQuery && !ocrRecognition) {
      message.warning("请输入问题或上传包含文字的图片");
      return;
    }
    setLoading(true);
    try {
      setResult(
        await api.search(
          retrievalMode,
          retrievalMode === "vector" ? normalizedQuery : undefined,
          knowledgeBaseId,
          retrievalMode === "field_filter"
            ? {
                parent_type: parentType,
                question_type: questionType,
                business_object: businessObject,
                purpose,
                customer_type: customerType
              }
            : {},
          retrievalMode === "vector" ? ocrRecognition?.recognition_token : undefined
        )
      );
      setFeedbackIds(new Set());
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "检索失败");
    } finally {
      setLoading(false);
    }
  };

  const recognizeImage = useCallback(async (file: File): Promise<void> => {
    const supportedTypes = ["image/png", "image/jpeg", "image/webp"];
    if (file.type && !supportedTypes.includes(file.type)) {
      message.warning("仅支持 PNG、JPEG 或 WebP 图片");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      message.warning("图片不能超过 10 MB");
      return;
    }
    setOcrLoading(true);
    setOcrRecognition(undefined);
    setResult(undefined);
    setFeedbackIds(new Set());
    try {
      const recognition = await api.recognizeSearchImage(file);
      setOcrRecognition(recognition);
      message.success("图片文字识别完成");
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "图片文字识别失败");
    } finally {
      setOcrLoading(false);
    }
  }, []);

  useEffect(() => {
    if (retrievalMode !== "vector") {
      return;
    }
    const recognizePastedImage = (event: ClipboardEvent): void => {
      const image = imageFromClipboard(event.clipboardData);
      if (!image) {
        return;
      }
      event.preventDefault();
      if (ocrLoading || loading) {
        message.info("当前操作尚未完成，请稍后再粘贴图片");
        return;
      }
      void recognizeImage(image);
    };
    window.addEventListener("paste", recognizePastedImage);
    return () => window.removeEventListener("paste", recognizePastedImage);
  }, [loading, ocrLoading, recognizeImage, retrievalMode]);

  const changeRetrievalMode = (value: string): void => {
    setRetrievalMode(value as SearchRetrievalMode);
    if (value !== "vector") {
      setOcrRecognition(undefined);
    }
    setResult(undefined);
    setFeedbackIds(new Set());
  };

  const markHelpful = async (item: SearchResult): Promise<void> => {
    if (!result || feedbackIds.has(item.result_item_id)) {
      return;
    }
    try {
      const feedback = await api.submitHelpfulFeedback(
        result.search_event_id,
        item.result_item_id
      );
      setFeedbackIds((current) => new Set(current).add(item.result_item_id));
      setResult((current) => {
        if (!current) {
          return current;
        }
        return {
          ...current,
          groups: current.groups.map((group) => ({
            ...group,
            children: group.children.map((child) =>
              child.result_item_id === item.result_item_id
                ? { ...child, helpful_count: feedback.helpful_count }
                : child
            )
          }))
        };
      });
      message.success("已记录有用反馈");
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "反馈提交失败");
    }
  };

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>知识检索</Typography.Title>
          <Typography.Paragraph type="secondary">
            可输入问题、上传包含文字的图片，或将两者结合检索；字段筛选会返回所有符合条件的已发布条目。
          </Typography.Paragraph>
        </div>
      </div>
      <Card>
        <Tabs
          activeKey={retrievalMode}
          onChange={changeRetrievalMode}
          items={[
            {
              key: "vector",
              label: "向量检索",
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Space.Compact style={{ width: "100%" }}>
                    <Input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      onPressEnter={() => void runSearch()}
                      placeholder="例如：如何找回密码？"
                      prefix={<SearchOutlined />}
                    />
                    <Select
                      allowClear
                      value={knowledgeBaseId}
                      onChange={setKnowledgeBaseId}
                      placeholder="全部知识库"
                      options={knowledgeBases.map((knowledgeBase) => ({
                        value: knowledgeBase.id,
                        label: knowledgeBase.name
                      }))}
                      style={{ minWidth: 180 }}
                    />
                    <Button type="primary" loading={loading} onClick={() => void runSearch()}>
                      向量检索
                    </Button>
                  </Space.Compact>
                  <Space wrap>
                    <Upload
                      accept="image/png,image/jpeg,image/webp"
                      beforeUpload={(file) => {
                        void recognizeImage(file);
                        return Upload.LIST_IGNORE;
                      }}
                      disabled={ocrLoading || loading}
                      showUploadList={false}
                    >
                      <Button icon={<PictureOutlined />} loading={ocrLoading}>
                        上传图片识别
                      </Button>
                    </Upload>
                    <Typography.Text type="secondary">
                      支持 PNG、JPEG、WebP，最大 10 MB；也可按 ⌘V / Ctrl+V 粘贴截图。
                    </Typography.Text>
                  </Space>
                  {ocrRecognition ? (
                    <Alert
                      type="info"
                      showIcon
                      message={`已识别文字（置信度 ${(ocrRecognition.confidence * 100).toFixed(1)}%）`}
                      description={
                        <Space direction="vertical" size={4} style={{ width: "100%" }}>
                          <Typography.Text>{ocrRecognition.text}</Typography.Text>
                          <Space wrap>
                            <Tag>{ocrRecognition.model_version}</Tag>
                            {ocrRecognition.keywords.map((keyword) => (
                              <Tag key={keyword}>{keyword}</Tag>
                            ))}
                            <Button
                              type="link"
                              danger
                              icon={<DeleteOutlined />}
                              onClick={() => setOcrRecognition(undefined)}
                            >
                              移除图片
                            </Button>
                          </Space>
                        </Space>
                      }
                    />
                  ) : null}
                </Space>
              )
            },
            {
              key: "field_filter",
              label: "字段筛选",
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Typography.Text type="secondary">
                    不使用关键词或向量相关度；未选择任何条件时，展示全部已发布条目。
                  </Typography.Text>
                  <Space wrap style={{ width: "100%" }}>
                    <Select
                      allowClear
                      value={knowledgeBaseId}
                      onChange={setKnowledgeBaseId}
                      placeholder="全部知识库"
                      options={knowledgeBases.map((knowledgeBase) => ({
                        value: knowledgeBase.id,
                        label: knowledgeBase.name
                      }))}
                      style={{ minWidth: 180 }}
                    />
                    <Select
                      allowClear
                      value={parentType}
                      onChange={setParentType}
                      placeholder="类型"
                      options={parentTypeOptions}
                      style={{ minWidth: 160 }}
                    />
                    <Select
                      allowClear
                      value={questionType}
                      onChange={setQuestionType}
                      placeholder="问题类型"
                      options={questionTypeOptions}
                      style={{ minWidth: 220 }}
                    />
                    <Select
                      allowClear
                      value={businessObject}
                      onChange={setBusinessObject}
                      placeholder="具体功能与模块"
                      options={businessObjectOptions}
                      style={{ minWidth: 220 }}
                    />
                    <Select
                      allowClear
                      value={purpose}
                      onChange={setPurpose}
                      placeholder="应用场景"
                      options={purposeOptions}
                      style={{ minWidth: 180 }}
                    />
                    <Select
                      allowClear
                      value={customerType}
                      onChange={setCustomerType}
                      placeholder="客户类型"
                      options={customerTypeOptions}
                      style={{ minWidth: 160 }}
                    />
                  </Space>
                  <Button type="primary" loading={loading} onClick={() => void runSearch()}>
                    筛选所有匹配条目
                  </Button>
                </Space>
              )
            }
          ]}
        />
      </Card>
      {result ? (
        <div style={{ marginTop: 16 }}>
          {result.no_match ? (
            <Alert type="info" showIcon message="无匹配" description={result.no_match_guidance} />
          ) : (
            result.groups.map((group) => (
              <Card
                key={group.parent_id}
                title={
                  <Space>
                    <Typography.Text strong>{group.parent_name}</Typography.Text>
                    <Tag>{group.canonical_keyword}</Tag>
                  </Space>
                }
                style={{ marginBottom: 12 }}
              >
                {group.children.map((item, index) => (
                  <div key={item.result_item_id}>
                    {index > 0 ? <Divider /> : null}
                    <Space direction="vertical" size={4} style={{ width: "100%" }}>
                      <Space wrap>
                        <Tag color="blue">{item.knowledge_base_name}</Tag>
                        <Tag
                          color={
                            item.match_reason === "parent_keyword_fallback" ||
                            item.match_reason === "ocr_keyword_fallback"
                              ? "gold"
                              : item.match_reason === "field_filter"
                                ? "cyan"
                                : "green"
                          }
                        >
                          {item.match_reason === "parent_keyword_fallback"
                            ? "关键词保底"
                            : item.match_reason === "ocr_keyword_fallback"
                              ? "OCR 关键词保底"
                            : item.match_reason === "field_filter"
                              ? "字段筛选"
                              : "相关命中"}
                        </Tag>
                      </Space>
                      <Descriptions bordered size="small" column={1}>
                        <Descriptions.Item label="问题小类">
                          <Typography.Paragraph
                            style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                          >
                            {item.question}
                          </Typography.Paragraph>
                        </Descriptions.Item>
                        <Descriptions.Item label="回复内容">
                          <Typography.Paragraph
                            style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                          >
                            {item.response_content}
                          </Typography.Paragraph>
                        </Descriptions.Item>
                        {item.question_variants.length > 0 ? (
                          <Descriptions.Item label="同义问句">
                            <Typography.Paragraph
                              style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                            >
                              {item.question_variants.join("\n")}
                            </Typography.Paragraph>
                          </Descriptions.Item>
                        ) : null}
                        {item.follow_up_guidance ? (
                          <Descriptions.Item label="后续指引">
                            <Typography.Paragraph
                              style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                            >
                              {item.follow_up_guidance}
                            </Typography.Paragraph>
                          </Descriptions.Item>
                        ) : null}
                        {item.question_type ? (
                          <Descriptions.Item label="问题类型">{item.question_type}</Descriptions.Item>
                        ) : null}
                        {item.business_object ? (
                          <Descriptions.Item label="具体功能与模块">
                            {item.business_object}
                          </Descriptions.Item>
                        ) : null}
                        {item.purpose ? (
                          <Descriptions.Item label="应用场景">{item.purpose}</Descriptions.Item>
                        ) : null}
                        {item.customer_type ? (
                          <Descriptions.Item label="客户类型">{item.customer_type}</Descriptions.Item>
                        ) : null}
                        {item.feature_explanation ? (
                          <Descriptions.Item label="功能说明">
                            <Typography.Paragraph
                              style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                            >
                              {item.feature_explanation}
                            </Typography.Paragraph>
                          </Descriptions.Item>
                        ) : null}
                        {item.example ? (
                          <Descriptions.Item label="示例">
                            <Typography.Paragraph
                              style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                            >
                              {item.example}
                            </Typography.Paragraph>
                          </Descriptions.Item>
                        ) : null}
                        {item.internal_notes ? (
                          <Descriptions.Item label="内部备注">
                            <Typography.Paragraph
                              style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                            >
                              {item.internal_notes}
                            </Typography.Paragraph>
                          </Descriptions.Item>
                        ) : null}
                      </Descriptions>
                      <Button
                        type={feedbackIds.has(item.result_item_id) ? "primary" : "default"}
                        size="small"
                        icon={<LikeOutlined />}
                        disabled={feedbackIds.has(item.result_item_id)}
                        onClick={() => void markHelpful(item)}
                      >
                        {feedbackIds.has(item.result_item_id) ? "已反馈" : `有用（${item.helpful_count}）`}
                      </Button>
                    </Space>
                  </div>
                ))}
              </Card>
            ))
          )}
        </div>
      ) : null}
    </section>
  );
}
