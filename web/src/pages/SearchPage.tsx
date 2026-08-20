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
  message
} from "antd";
import { SearchOutlined, LikeOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type {
  KnowledgeBase,
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

export function SearchPage(): JSX.Element {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>();
  const [retrievalMode, setRetrievalMode] = useState<SearchRetrievalMode>("vector");
  const [query, setQuery] = useState("");
  const [parentType, setParentType] = useState<string>();
  const [questionType, setQuestionType] = useState<string>();
  const [businessObject, setBusinessObject] = useState<string>();
  const [purpose, setPurpose] = useState<string>();
  const [customerType, setCustomerType] = useState<string>();
  const [result, setResult] = useState<SearchResponse>();
  const [loading, setLoading] = useState(false);
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
    if (retrievalMode === "vector" && !normalizedQuery) {
      message.warning("请输入要查询的问题");
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
            : {}
        )
      );
      setFeedbackIds(new Set());
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "检索失败");
    } finally {
      setLoading(false);
    }
  };

  const changeRetrievalMode = (value: string): void => {
    setRetrievalMode(value as SearchRetrievalMode);
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
            向量检索与字段筛选相互独立；字段筛选会返回所有符合条件的已发布条目。
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
                            item.match_reason === "parent_keyword_fallback"
                              ? "gold"
                              : item.match_reason === "field_filter"
                                ? "cyan"
                                : "green"
                          }
                        >
                          {item.match_reason === "parent_keyword_fallback"
                            ? "关键词保底"
                            : item.match_reason === "field_filter"
                              ? "字段筛选"
                              : "相关命中"}
                        </Tag>
                      </Space>
                      <Descriptions bordered size="small" column={1}>
                        <Descriptions.Item label="具体问题所属小类">
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
