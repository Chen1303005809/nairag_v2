import {
  Alert,
  Button,
  Card,
  Divider,
  Input,
  Select,
  Space,
  Tag,
  Typography,
  message
} from "antd";
import { SearchOutlined, LikeOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { KnowledgeBase, SearchResponse, SearchResult } from "../api/types";

export function SearchPage(): JSX.Element {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>();
  const [query, setQuery] = useState("");
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
    if (!normalizedQuery) {
      message.warning("请输入要查询的问题");
      return;
    }
    setLoading(true);
    try {
      setResult(await api.search(normalizedQuery, knowledgeBaseId));
      setFeedbackIds(new Set());
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "检索失败");
    } finally {
      setLoading(false);
    }
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
            当前提供文本检索；结果按父类分组，并以 PostgreSQL 发布关系为最终可见性依据。
          </Typography.Paragraph>
        </div>
      </div>
      <Card>
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
            检索
          </Button>
        </Space.Compact>
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
                        <Typography.Text strong>{item.question}</Typography.Text>
                        <Tag color="blue">{item.knowledge_base_name}</Tag>
                        <Tag color={item.match_reason === "parent_keyword_fallback" ? "gold" : "green"}>
                          {item.match_reason === "parent_keyword_fallback" ? "关键词保底" : "相关命中"}
                        </Tag>
                      </Space>
                      <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
                        {item.response_content}
                      </Typography.Paragraph>
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
