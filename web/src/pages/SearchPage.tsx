import {
  Alert,
  Button,
  Card,
  Divider,
  Descriptions,
  Image,
  Input,
  Modal,
  Select,
  Space,
  Tag,
  Tabs,
  Typography,
  Upload,
  message
} from "antd";
import {
  DeleteOutlined,
  LikeOutlined,
  MessageOutlined,
  PictureOutlined,
  PlusOutlined,
  SearchOutlined
} from "@ant-design/icons";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type {
  ConversationSearchResult,
  ConversationSearchResponse,
  KnowledgeBase,
  OcrRecognition,
  SearchAnnotationResultFeedbackInput,
  SearchAnnotationResultLabel,
  SearchAnnotationReviewResponse,
  SearchResponse,
  SearchResult,
  SearchRetrievalMode,
  SupplementalSearchResult,
  ConversationSupplementalSearchResult
} from "../api/types";
import {
  businessObjectOptions,
  customerTypeOptions,
  parentTypeOptions,
  purposeOptions,
  questionTypeOptions
} from "../constants/knowledgeOptions";
import {
  assertConversationWithinLimits,
  conversationImagesFromClipboard,
  ConversationParseError,
  prepareWecomConversation
} from "../conversation";
import { ConversationEditor } from "../components/ConversationEditor";
import type { ConversationEditorHandle } from "../components/ConversationEditor";

interface RenderableResult extends SearchResult {
  matched_queries?: string[];
}

type RenderableSupplementalResult =
  | SupplementalSearchResult
  | ConversationSupplementalSearchResult;
type AnnotationRenderableResult = RenderableResult | RenderableSupplementalResult;

interface ResultAnnotationDraft {
  feedbackType?: SearchAnnotationResultLabel;
  otherNote: string;
}

const annotationFeedbackLabels: Record<SearchAnnotationResultLabel, string> = {
  high_score_irrelevant: "分数高但是无关",
  low_score_relevant: "分数低但是有关",
  normal: "结果正常",
  other: "其他"
};

const matchedFieldLabels: Record<string, string> = {
  question: "问题",
  question_variant: "同义问句",
  response_content: "回复内容",
  "parent.canonical_keyword": "父类关键词"
};

const selectionStageLabels: Record<SearchResult["selection_stage"], string> = {
  hybrid: "混合检索直返",
  rerank: "重排确认",
  llm: "LLM 相关判断",
  score_fallback: "基础检索兜底",
  keyword_fallback: "关键词保底",
  field_filter: "字段筛选",
  legacy: "历史结果"
};

function selectionStageColor(stage: SearchResult["selection_stage"]): string {
  if (stage === "keyword_fallback" || stage === "score_fallback") {
    return "gold";
  }
  if (stage === "field_filter") {
    return "cyan";
  }
  if (stage === "llm") {
    return "purple";
  }
  return "green";
}

function matchedFieldLabel(field: string | null): string {
  return field ? matchedFieldLabels[field] ?? field : "未记录";
}

function isKnowledgeResult(item: AnnotationRenderableResult): item is RenderableResult {
  return "knowledge_base_name" in item;
}

function ResultItemView({
  item,
  feedbackGiven,
  onFeedback,
  showHelpfulFeedback = true
}: {
  item: RenderableResult;
  feedbackGiven: boolean;
  onFeedback: (item: RenderableResult) => void;
  showHelpfulFeedback?: boolean;
}): JSX.Element {
  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      <Space wrap>
        <Tag color="blue">{item.knowledge_base_name}</Tag>
        <Tag color={selectionStageColor(item.selection_stage)}>
          命中阶段：{selectionStageLabels[item.selection_stage]}
        </Tag>
        <Tag color="geekblue">
          综合相关度：{(item.score * 100).toFixed(2)}%
        </Tag>
        {item.hybrid_score !== null ? (
          <Tag color="blue">混合分：{(item.hybrid_score * 100).toFixed(2)}%</Tag>
        ) : null}
        {item.rerank_score !== null ? (
          <Tag color="volcano">重排分：{(item.rerank_score * 100).toFixed(2)}%</Tag>
        ) : null}
        <Tag color="purple">命中字段：{matchedFieldLabel(item.matched_field)}</Tag>
        {item.matched_queries?.map((query) => (
          <Tag key={query} color="magenta">
            命中查询：{query}
          </Tag>
        ))}
      </Space>
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="问题小类">
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {item.question}
          </Typography.Paragraph>
        </Descriptions.Item>
        <Descriptions.Item label="回复内容">
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {item.response_content}
          </Typography.Paragraph>
        </Descriptions.Item>
        {item.question_variants.length > 0 ? (
          <Descriptions.Item label="同义问句">
            <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
              {item.question_variants.join("\n")}
            </Typography.Paragraph>
          </Descriptions.Item>
        ) : null}
        {item.follow_up_guidance ? (
          <Descriptions.Item label="后续指引">
            <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
              {item.follow_up_guidance}
            </Typography.Paragraph>
          </Descriptions.Item>
        ) : null}
        {item.question_type ? (
          <Descriptions.Item label="问题类型">{item.question_type}</Descriptions.Item>
        ) : null}
        {item.business_object ? (
          <Descriptions.Item label="具体功能与模块">{item.business_object}</Descriptions.Item>
        ) : null}
        {item.purpose ? <Descriptions.Item label="应用场景">{item.purpose}</Descriptions.Item> : null}
        {item.customer_type ? (
          <Descriptions.Item label="客户类型">{item.customer_type}</Descriptions.Item>
        ) : null}
        {item.feature_explanation ? (
          <Descriptions.Item label="功能说明">
            <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
              {item.feature_explanation}
            </Typography.Paragraph>
          </Descriptions.Item>
        ) : null}
        {item.example ? (
          <Descriptions.Item label="示例">
            <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
              {item.example}
            </Typography.Paragraph>
          </Descriptions.Item>
        ) : null}
        {item.attachments.length > 0 ? (
          <Descriptions.Item label="附件">
            <Space size={[8, 8]} wrap>
              {item.attachments.map((attachment) => {
                const downloadUrl = api.knowledgeAttachmentDownloadUrl(attachment.id);
                return attachment.content_type.startsWith("image/") ? (
                  <Image
                    key={attachment.id}
                    alt={attachment.name}
                    src={downloadUrl}
                    width={120}
                  />
                ) : (
                  <a key={attachment.id} href={downloadUrl} rel="noreferrer" target="_blank">
                    {attachment.name}
                  </a>
                );
              })}
            </Space>
          </Descriptions.Item>
        ) : null}
        {item.web_links.length > 0 ? (
          <Descriptions.Item label="相关网页链接">
            <Space direction="vertical" size={4}>
              {item.web_links.map((webLink) => (
                <a key={webLink.url} href={webLink.url} rel="noreferrer" target="_blank">
                  {webLink.title}
                </a>
              ))}
            </Space>
          </Descriptions.Item>
        ) : null}
      </Descriptions>
      {showHelpfulFeedback ? (
        <Button
          type={feedbackGiven ? "primary" : "default"}
          size="small"
          icon={<LikeOutlined />}
          disabled={feedbackGiven}
          onClick={() => onFeedback(item)}
        >
          {feedbackGiven ? "已反馈" : `有用（${item.helpful_count}）`}
        </Button>
      ) : null}
    </Space>
  );
}

function ResultsGroupsView({
  groups,
  feedbackIds,
  onFeedback
}: {
  groups: Array<{
    parent_id: string;
    parent_name: string;
    canonical_keyword: string;
    children: RenderableResult[];
  }>;
  feedbackIds: Set<string>;
  onFeedback: (item: RenderableResult) => void;
}): JSX.Element {
  return (
    <>
      {groups.map((group) => (
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
              <ResultItemView
                item={item}
                feedbackGiven={feedbackIds.has(item.result_item_id)}
                onFeedback={onFeedback}
              />
            </div>
          ))}
        </Card>
      ))}
    </>
  );
}

function SupplementalResultItemView({
  item
}: {
  item: RenderableSupplementalResult;
}): JSX.Element {
  const matchedQueries = "matched_queries" in item ? item.matched_queries : [];
  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      <Space wrap>
        <Tag color="cyan">全局补充资料</Tag>
        <Tag color="geekblue">相关度：{(item.score * 100).toFixed(2)}%</Tag>
        <Tag color={item.selection_stage === "supplemental_rerank" ? "volcano" : "gold"}>
          {item.selection_stage === "supplemental_rerank" ? "平台重排" : "来源融合"}
        </Tag>
        {item.rerank_score !== null ? (
          <Tag color="volcano">重排分：{(item.rerank_score * 100).toFixed(2)}%</Tag>
        ) : null}
        {matchedQueries.map((query) => (
          <Tag key={query} color="magenta">命中查询：{query}</Tag>
        ))}
      </Space>
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="资料名称">
          <Typography.Text strong>{item.title}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="相关片段">
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {item.content}
          </Typography.Paragraph>
        </Descriptions.Item>
      </Descriptions>
    </Space>
  );
}

function SupplementalResultsView({
  results
}: {
  results: RenderableSupplementalResult[];
}): JSX.Element | null {
  if (results.length === 0) {
    return null;
  }
  return (
    <Card title="相关资料" style={{ marginBottom: 12 }}>
      {results.map((item, index) => (
        <div key={item.result_item_id}>
          {index > 0 ? <Divider /> : null}
          <SupplementalResultItemView item={item} />
        </div>
      ))}
    </Card>
  );
}

function visibleResultsForAnnotation(
  groups: Array<{
    children: RenderableResult[];
  }>,
  supplementalResults: RenderableSupplementalResult[]
): AnnotationRenderableResult[] {
  const resultIds = new Set<string>();
  const knowledgeResults = groups.flatMap((group) =>
    group.children.filter((item) => {
      if (resultIds.has(item.result_item_id)) {
        return false;
      }
      resultIds.add(item.result_item_id);
      return true;
    })
  );
  return [
    ...knowledgeResults,
    ...supplementalResults.filter((item) => {
      if (resultIds.has(item.result_item_id)) {
        return false;
      }
      resultIds.add(item.result_item_id);
      return true;
    })
  ];
}

function SearchAnnotationReviewPanel({
  interactionId,
  results,
  onSubmit
}: {
  interactionId: string | null;
  results: AnnotationRenderableResult[];
  onSubmit: (
    feedbacks: SearchAnnotationResultFeedbackInput[]
  ) => Promise<SearchAnnotationReviewResponse>;
}): JSX.Element | null {
  const [open, setOpen] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, ResultAnnotationDraft>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  if (!interactionId) {
    return null;
  }

  const currentResult = results[currentIndex];
  const currentDraft = currentResult
    ? drafts[currentResult.result_item_id] ?? { otherNote: "" }
    : undefined;
  const isComplete = results.every((item) => Boolean(drafts[item.result_item_id]?.feedbackType));
  const isOther = currentDraft?.feedbackType === "other";
  const otherNoteMissing = isOther && !currentDraft?.otherNote.trim();

  const updateDraft = (
    resultItemId: string,
    feedbackType: SearchAnnotationResultLabel,
    otherNote?: string
  ): void => {
    setDrafts((current) => ({
      ...current,
      [resultItemId]: {
        feedbackType,
        otherNote: feedbackType === "other" ? otherNote ?? current[resultItemId]?.otherNote ?? "" : ""
      }
    }));
  };

  const recordAndContinue = (feedbackType: Exclude<SearchAnnotationResultLabel, "other">): void => {
    if (!currentResult) {
      return;
    }
    updateDraft(currentResult.result_item_id, feedbackType);
    setCurrentIndex((index) => index + 1);
  };

  const saveOtherAndContinue = (): void => {
    if (!currentResult || !currentDraft || !currentDraft.otherNote.trim()) {
      return;
    }
    updateDraft(currentResult.result_item_id, "other", currentDraft.otherNote);
    setCurrentIndex((index) => index + 1);
  };

  const submit = async (): Promise<void> => {
    if (!isComplete || submitting) {
      return;
    }
    const feedbacks = results.map((item) => {
      const draft = drafts[item.result_item_id];
      if (!draft?.feedbackType) {
        throw new Error("请逐条完成本次检索结果的标注");
      }
      return {
        search_result_item_id: item.result_item_id,
        feedback_type: draft.feedbackType,
        ...(draft.feedbackType === "other" ? { other_note: draft.otherNote.trim() } : {})
      };
    });
    setSubmitting(true);
    try {
      const review = await onSubmit(feedbacks);
      setSubmitted(true);
      setOpen(false);
      message.success(review.already_recorded ? "本次检索已标注" : "已记录本次检索标注");
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "标注提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  const labelCounts = results.reduce<Record<SearchAnnotationResultLabel, number>>(
    (counts, item) => {
      const feedbackType = drafts[item.result_item_id]?.feedbackType;
      if (feedbackType) {
        counts[feedbackType] += 1;
      }
      return counts;
    },
    { high_score_irrelevant: 0, low_score_relevant: 0, normal: 0, other: 0 }
  );

  return (
    <Card size="small" style={{ marginTop: 16 }} title="本次检索标注">
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          以本次检索为一个 Review 提交。点击后请逐条标注；“跳过”会记录为“结果正常”（高分有关或低分无关）。
        </Typography.Text>
        {submitted ? (
          <Alert
            type="success"
            showIcon
            message="已记录本次检索标注，提交后不可修改。"
          />
        ) : (
          <Button type="primary" onClick={() => setOpen(true)}>
            {results.length === 0 ? "完成无结果标注" : "开始逐条标注"}
          </Button>
        )}
      </Space>
      <Modal
        footer={null}
        onCancel={() => setOpen(false)}
        open={open}
        title="检索结果标注"
        width={820}
      >
        {results.length === 0 ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message="本次检索没有展示结果"
              description="可完成该次无结果 Review；这不会标记为某条结果的问题。"
            />
            <Space>
              <Button onClick={() => setOpen(false)}>取消</Button>
              <Button type="primary" loading={submitting} onClick={() => void submit()}>
                完成并提交
              </Button>
            </Space>
          </Space>
        ) : currentResult ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              第 {currentIndex + 1} / {results.length} 条。选择标签后会进入下一条；可以返回修改已处理的结果。
            </Typography.Text>
            {isKnowledgeResult(currentResult) ? (
              <ResultItemView
                item={currentResult}
                feedbackGiven={false}
                onFeedback={() => undefined}
                showHelpfulFeedback={false}
              />
            ) : (
              <SupplementalResultItemView item={currentResult} />
            )}
            <Card size="small" title="为这条结果选择标签">
              <Space wrap>
                <Button
                  danger
                  type={currentDraft?.feedbackType === "high_score_irrelevant" ? "primary" : "default"}
                  onClick={() => recordAndContinue("high_score_irrelevant")}
                >
                  高分无关
                </Button>
                <Button
                  type={currentDraft?.feedbackType === "low_score_relevant" ? "primary" : "default"}
                  onClick={() => recordAndContinue("low_score_relevant")}
                >
                  低分有关
                </Button>
                <Button
                  type={currentDraft?.feedbackType === "normal" ? "primary" : "default"}
                  onClick={() => recordAndContinue("normal")}
                >
                  跳过（结果正常）
                </Button>
                <Button
                  type={currentDraft?.feedbackType === "other" ? "primary" : "default"}
                  onClick={() => updateDraft(currentResult.result_item_id, "other")}
                >
                  其他
                </Button>
              </Space>
              {isOther ? (
                <Space direction="vertical" size={8} style={{ marginTop: 12, width: "100%" }}>
                  <Input.TextArea
                    aria-label="其他结果标注说明"
                    autoSize={{ minRows: 3, maxRows: 8 }}
                    maxLength={4000}
                    placeholder="请说明这条检索结果的问题（必填，最多 4000 字）"
                    value={currentDraft?.otherNote ?? ""}
                    onChange={(event) =>
                      updateDraft(currentResult.result_item_id, "other", event.target.value)
                    }
                  />
                  <Button
                    type="primary"
                    disabled={otherNoteMissing}
                    onClick={saveOtherAndContinue}
                  >
                    保存其他并继续
                  </Button>
                </Space>
              ) : null}
            </Card>
            <Space>
              <Button disabled={currentIndex === 0} onClick={() => setCurrentIndex((index) => index - 1)}>
                上一条
              </Button>
              <Button onClick={() => setOpen(false)}>稍后继续</Button>
            </Space>
          </Space>
        ) : (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Alert
              type="success"
              showIcon
              message="已逐条完成标注"
              description="请确认后提交整个检索 Review；提交后不可修改。"
            />
            <Space wrap>
              {(Object.keys(annotationFeedbackLabels) as SearchAnnotationResultLabel[]).map((type) => (
                <Tag key={type}>{annotationFeedbackLabels[type]}：{labelCounts[type]}</Tag>
              ))}
            </Space>
            <Space>
              <Button onClick={() => setCurrentIndex(Math.max(results.length - 1, 0))}>返回修改</Button>
              <Button type="primary" loading={submitting} onClick={() => void submit()}>
                完成并提交
              </Button>
            </Space>
          </Space>
        )}
      </Modal>
    </Card>
  );
}

type SearchTabKey = SearchRetrievalMode | "conversation";

export function SearchPage(): JSX.Element {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>();
  const [activeTab, setActiveTab] = useState<SearchTabKey>("vector");
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

  const conversationEditorRef = useRef<ConversationEditorHandle>(null);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [retrievingQueries, setRetrievingQueries] = useState(false);
  const [conversationResult, setConversationResult] = useState<ConversationSearchResponse>();
  const [editableQueries, setEditableQueries] = useState<string[]>([]);

  useEffect(() => {
    void api
      .listKnowledgeBases()
      .then(setKnowledgeBases)
      .catch((reason) => {
        message.error(reason instanceof Error ? reason.message : "无法加载知识库");
      });
  }, []);

  const submitFeedback = async (
    searchEventId: string,
    item: RenderableResult
  ): Promise<void> => {
    if (feedbackIds.has(item.result_item_id)) {
      return;
    }
    try {
      const feedback = await api.submitHelpfulFeedback(searchEventId, item.result_item_id);
      setFeedbackIds((current) => new Set(current).add(item.result_item_id));
      setResult((current) =>
        current
          ? {
              ...current,
              groups: current.groups.map((group) => ({
                ...group,
                children: group.children.map((child) =>
                  child.result_item_id === item.result_item_id
                    ? { ...child, helpful_count: feedback.helpful_count }
                    : child
                )
              }))
            }
          : current
      );
      setConversationResult((current) =>
        current
          ? {
              ...current,
              groups: current.groups.map((group) => ({
                ...group,
                children: group.children.map((child) =>
                  child.result_item_id === item.result_item_id
                    ? { ...child, helpful_count: feedback.helpful_count }
                    : child
                )
              }))
            }
          : current
      );
      message.success("已记录有用反馈");
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "反馈提交失败");
    }
  };

  const markHelpful = async (item: RenderableResult): Promise<void> => {
    if (!result) {
      return;
    }
    await submitFeedback(result.search_event_id, item);
  };

  const markConversationHelpful = async (item: RenderableResult): Promise<void> => {
    const conversationItem = item as ConversationSearchResult;
    if (!conversationItem.search_event_id) {
      return;
    }
    await submitFeedback(conversationItem.search_event_id, item);
  };

  const runSearch = async (): Promise<void> => {
    const normalizedQuery = query.trim();
    if (retrievalMode === "vector" && !normalizedQuery && !ocrRecognition) {
      message.warning("请输入问题或上传包含文字的图片");
      return;
    }
    setLoading(true);
    setFeedbackIds(new Set());
    try {
      const response = await api.search(
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
      );
      setResult(response);
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "检索失败");
    } finally {
      setLoading(false);
    }
  };

  const runConversationSearch = async (): Promise<void> => {
    setConversationLoading(true);
    setConversationResult(undefined);
    setEditableQueries([]);
    setFeedbackIds(new Set());
    try {
      const editorValue = conversationEditorRef.current?.getValue() ?? { text: "", images: [] };
      const preparedConversation = await prepareWecomConversation(
        editorValue.text,
        editorValue.images,
        api.recognizeConversationImage
      );
      assertConversationWithinLimits(preparedConversation.messages);
      if (preparedConversation.imageCount > 0) {
        conversationEditorRef.current?.replaceWithText(preparedConversation.text);
        message.success(`已识别 ${preparedConversation.imageCount} 张聊天图片`);
      }
      const response = await api.conversationSearch(preparedConversation.messages, knowledgeBaseId);
      setConversationResult(response);
      setEditableQueries([...response.queries]);
      if (response.queries.length === 0) {
        message.info("未发现待查询问题");
      }
    } catch (reason) {
      if (reason instanceof ConversationParseError) {
        message.warning(reason.message);
      } else {
        message.error(reason instanceof Error ? reason.message : "快速检索失败");
      }
    } finally {
      setConversationLoading(false);
    }
  };

  const retrieveWithEditedQueries = async (): Promise<void> => {
    const queries = editableQueries.map((value) => value.trim()).filter(Boolean);
    if (queries.length === 0) {
      message.warning("请至少保留一条查询");
      return;
    }
    setRetrievingQueries(true);
    setFeedbackIds(new Set());
    try {
      setConversationResult(await api.queryBatchSearch(queries, knowledgeBaseId));
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "重新检索失败");
    } finally {
      setRetrievingQueries(false);
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
    if (activeTab !== "vector") {
      return;
    }
    const recognizePastedImage = (event: ClipboardEvent): void => {
      const image = conversationImagesFromClipboard(event.clipboardData)[0];
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
  }, [activeTab, loading, ocrLoading, recognizeImage]);

  const changeTab = (value: string): void => {
    setActiveTab(value as SearchTabKey);
    if (value === "vector" || value === "field_filter") {
      setRetrievalMode(value);
    }
    if (value !== "vector") {
      setOcrRecognition(undefined);
    }
    setResult(undefined);
    setFeedbackIds(new Set());
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
          activeKey={activeTab}
          onChange={changeTab}
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
            },
            {
              key: "conversation",
              label: (
                <span>
                  <MessageOutlined /> 快速检索
                </span>
              ),
              children: (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  <Typography.Text type="secondary">
                    可直接粘贴企业微信转发卡片。系统会提取最多 5 条待查询问题并自动检索；
                    说话人名称中包含“融航”视为我方，其余视为客户。卡片中的图片会先 OCR 并替换“[图片]”。
                  </Typography.Text>
                  <ConversationEditor
                    ref={conversationEditorRef}
                    ariaLabel="快速检索聊天内容"
                    placeholder={
                      "客户A 09:30\n登录一直失败怎么办？\n\n融航-支持专员 09:31\n我先查询一下。"
                    }
                  />
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<SearchOutlined />}
                      loading={conversationLoading}
                      onClick={() => void runConversationSearch()}
                    >
                      提取查询并检索
                    </Button>
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
                  </Space>
                  {conversationResult ? (
                    <Card size="small" title="提取出的查询（可编辑后重新检索）">
                      <Space direction="vertical" size={8} style={{ width: "100%" }}>
                        {conversationResult.total_candidates > conversationResult.queries.length ? (
                          <Alert
                            type="warning"
                            showIcon
                            message={`已执行前 ${conversationResult.queries.length} 条查询`}
                            description={`另有 ${conversationResult.total_candidates - conversationResult.queries.length} 条候选未执行。`}
                          />
                        ) : null}
                        {editableQueries.length === 0 ? (
                          <Typography.Text type="secondary">未发现待查询问题</Typography.Text>
                        ) : (
                          editableQueries.map((searchQuery, index) => (
                            <Space.Compact key={index} style={{ width: "100%" }}>
                              <Input
                                value={searchQuery}
                                onChange={(event) => {
                                  const next = [...editableQueries];
                                  next[index] = event.target.value;
                                  setEditableQueries(next);
                                }}
                                placeholder="查询语句"
                              />
                              <Button
                                icon={<DeleteOutlined />}
                                onClick={() =>
                                  setEditableQueries((current) =>
                                    current.filter((_item, itemIndex) => itemIndex !== index)
                                  )
                                }
                              />
                            </Space.Compact>
                          ))
                        )}
                        <Space wrap>
                          <Button
                            icon={<PlusOutlined />}
                            disabled={editableQueries.length >= 5}
                            onClick={() => setEditableQueries((current) => [...current, ""])}
                          >
                            添加查询
                          </Button>
                          <Button
                            type="primary"
                            loading={retrievingQueries}
                            disabled={editableQueries.length === 0}
                            onClick={() => void retrieveWithEditedQueries()}
                          >
                            按当前查询重新检索
                          </Button>
                        </Space>
                      </Space>
                    </Card>
                  ) : null}
                </Space>
              )
            }
          ]}
        />
      </Card>
      {activeTab !== "conversation" && result ? (
        <div style={{ marginTop: 16 }}>
          {result.degraded ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="已使用基础检索兜底"
              description="部分候选已按基础检索流程返回，结果可能未经过完整模型验证。"
            />
          ) : null}
          {result.no_match ? (
            <Alert type="info" showIcon message="无匹配" description={result.no_match_guidance} />
          ) : (
            <>
              <ResultsGroupsView
                groups={result.groups}
                feedbackIds={feedbackIds}
                onFeedback={(item) => void markHelpful(item)}
              />
              <SupplementalResultsView results={result.supplemental_results} />
            </>
          )}
          <SearchAnnotationReviewPanel
            key={result.search_interaction_id ?? "missing-search-interaction"}
            interactionId={result.search_interaction_id}
            results={visibleResultsForAnnotation(result.groups, result.supplemental_results)}
            onSubmit={(resultFeedbacks) =>
              api.submitSearchAnnotationReview(
                result.search_interaction_id ?? "",
                resultFeedbacks
              )
            }
          />
        </div>
      ) : null}
      {activeTab === "conversation" && conversationResult ? (
        <div style={{ marginTop: 16 }}>
          {conversationResult.degraded ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="已使用基础检索兜底"
              description="部分查询已按基础检索流程返回，结果可能未经过完整模型验证。"
            />
          ) : null}
          {conversationResult.no_query_guidance ? (
            <Alert
              type="info"
              showIcon
              message={conversationResult.no_query_guidance}
              description="系统不会强行检索；可以调整粘贴内容后重试。"
            />
          ) : conversationResult.no_match ? (
            <Alert
              type="info"
              showIcon
              message="无匹配"
              description={conversationResult.no_match_guidance}
            />
          ) : (
            <>
              <ResultsGroupsView
                groups={conversationResult.groups}
                feedbackIds={feedbackIds}
                onFeedback={(item) => void markConversationHelpful(item)}
              />
              <SupplementalResultsView results={conversationResult.supplemental_results} />
            </>
          )}
          {!conversationResult.no_query_guidance ? (
            <SearchAnnotationReviewPanel
              key={conversationResult.search_interaction_id ?? "missing-conversation-interaction"}
              interactionId={conversationResult.search_interaction_id}
              results={visibleResultsForAnnotation(
                conversationResult.groups,
                conversationResult.supplemental_results
              )}
              onSubmit={(resultFeedbacks) =>
                api.submitSearchAnnotationReview(
                  conversationResult.search_interaction_id ?? "",
                  resultFeedbacks
                )
              }
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
