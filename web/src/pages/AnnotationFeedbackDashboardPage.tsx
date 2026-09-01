import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  Input,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { formatDateTime } from "../dateTime";
import type {
  AnnotationFeedbackDetail,
  AnnotationFeedbackFilters,
  AnnotationFeedbackListItem,
  AnnotationFeedbackSummary,
  ManagedKnowledgeBase,
  SearchAnnotationResultLabel
} from "../api/types";

const feedbackTypeLabels: Record<SearchAnnotationResultLabel, string> = {
  high_score_irrelevant: "分数高但是无关",
  low_score_relevant: "分数低但是有关",
  normal: "结果正常（跳过）",
  other: "其他"
};

const interactionTypeLabels = {
  vector: "向量检索",
  quick_search: "快速检索"
} as const;

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "无法加载标注反馈";
}

function dateRangeFilters(dateRange: [string, string]): AnnotationFeedbackFilters {
  const [from, to] = dateRange;
  return {
    // DatePicker represents dates in the administrator's local time. Send
    // timezone-aware instants so the server does not reinterpret the range in
    // its own deployment timezone.
    annotated_from: from ? dayjs(from).startOf("day").toISOString() : undefined,
    annotated_to: to ? dayjs(to).endOf("day").toISOString() : undefined
  };
}

function feedbackTypeColor(feedbackType: SearchAnnotationResultLabel): string {
  if (feedbackType === "high_score_irrelevant") {
    return "volcano";
  }
  if (feedbackType === "low_score_relevant") {
    return "gold";
  }
  return feedbackType === "normal" ? "green" : "blue";
}

function feedbackTypeTag(feedbackType: SearchAnnotationResultLabel): JSX.Element {
  return <Tag color={feedbackTypeColor(feedbackType)}>{feedbackTypeLabels[feedbackType]}</Tag>;
}

function resultLabelCount(item: AnnotationFeedbackListItem, type: SearchAnnotationResultLabel): number {
  switch (type) {
    case "high_score_irrelevant":
      return item.high_score_irrelevant_count;
    case "low_score_relevant":
      return item.low_score_relevant_count;
    case "normal":
      return item.normal_count;
    case "other":
      return item.other_count;
  }
}

function DetailResults({ detail }: { detail: AnnotationFeedbackDetail }): JSX.Element {
  const columns: ColumnsType<AnnotationFeedbackDetail["query_details"][number]["results"][number]> = [
    { title: "排名", dataIndex: "rank", width: 72 },
    {
      title: "结果",
      key: "knowledge",
      width: 350,
      render: (_, result) =>
        result.result_kind === "supplement" ? (
          <Space direction="vertical" size={2}>
            <Tag color="cyan">全局补充资料</Tag>
            <Typography.Text>{result.question}</Typography.Text>
            <Typography.Paragraph ellipsis={{ rows: 2, expandable: "collapsible" }}>
              {result.content}
            </Typography.Paragraph>
          </Space>
        ) : (
          <Space direction="vertical" size={0}>
            <Typography.Text type="secondary">{result.parent_name}</Typography.Text>
            <Typography.Text>{result.question}</Typography.Text>
          </Space>
        )
    },
    {
      title: "知识库",
      dataIndex: "knowledge_base_name",
      width: 160,
      render: (value: string | null, result) =>
        result.result_kind === "supplement" ? "全局资料" : value ?? "—"
    },
    {
      title: "综合分",
      dataIndex: "score",
      width: 110,
      render: (value: number) => `${(value * 100).toFixed(2)}%`
    },
    {
      title: "混合分",
      dataIndex: "hybrid_score",
      width: 110,
      render: (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(2)}%`)
    },
    {
      title: "重排分",
      dataIndex: "rerank_score",
      width: 110,
      render: (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(2)}%`)
    },
    { title: "命中阶段", dataIndex: "selection_stage", width: 130 },
    { title: "命中字段", dataIndex: "matched_field", width: 130, render: (value) => value ?? "—" },
    {
      title: "命中查询",
      dataIndex: "matched_queries",
      width: 240,
      render: (values: string[]) => values.join("；")
    },
    {
      title: "结果标签",
      dataIndex: "feedback_type",
      width: 160,
      render: (value: SearchAnnotationResultLabel) => feedbackTypeTag(value)
    },
    {
      title: "其他说明",
      dataIndex: "other_note",
      width: 260,
      ellipsis: true,
      render: (value: string | null) => value ?? "—"
    }
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      {detail.degraded ? (
        <Alert
          type="warning"
          showIcon
          message="该次检索使用了基础兜底流程"
          description={detail.degradation_reasons.join("、") || "未记录具体原因"}
        />
      ) : null}
      {detail.query_details.map((query) => (
        <Card
          key={query.search_event_id}
          size="small"
          title={`查询 ${query.query_order}：${query.query_text ?? query.ocr_text ?? "（未记录查询）"}`}
        >
          {query.query_text && query.ocr_text ? (
            <Typography.Paragraph type="secondary">
              OCR 查询：{query.ocr_text}
            </Typography.Paragraph>
          ) : null}
          {query.results.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={query.no_match ? "该查询无匹配结果" : "该查询未返回结果"}
            />
          ) : (
            <Table
              columns={columns}
              dataSource={query.results}
              pagination={false}
              rowKey="result_item_id"
              scroll={{ x: 1670 }}
              size="small"
            />
          )}
        </Card>
      ))}
    </Space>
  );
}

export function AnnotationFeedbackDashboardPage(): JSX.Element {
  const [knowledgeBases, setKnowledgeBases] = useState<ManagedKnowledgeBase[]>([]);
  const [summary, setSummary] = useState<AnnotationFeedbackSummary>();
  const [items, setItems] = useState<AnnotationFeedbackListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [dateRange, setDateRange] = useState<[string, string]>(["", ""]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>();
  const [feedbackType, setFeedbackType] = useState<SearchAnnotationResultLabel>();
  const [keywordDraft, setKeywordDraft] = useState("");
  const [queryKeyword, setQueryKeyword] = useState("");
  const [details, setDetails] = useState<Record<string, AnnotationFeedbackDetail>>({});
  const [loadingDetailIds, setLoadingDetailIds] = useState<Set<string>>(new Set());

  const filters = useMemo<AnnotationFeedbackFilters>(
    () => ({
      ...dateRangeFilters(dateRange),
      knowledge_base_id: knowledgeBaseId,
      query_keyword: queryKeyword.trim() || undefined
    }),
    [dateRange, knowledgeBaseId, queryKeyword]
  );

  const loadFeedback = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [summaryResponse, pageResponse] = await Promise.all([
        api.getAnnotationFeedbackSummary(filters),
        api.listAnnotationFeedback({
          ...filters,
          feedback_type: feedbackType,
          page,
          page_size: 20
        })
      ]);
      setSummary(summaryResponse);
      setItems(pageResponse.items);
      setTotal(pageResponse.total);
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, [feedbackType, filters, page]);

  useEffect(() => {
    void api
      .listManagedKnowledgeBases()
      .then(setKnowledgeBases)
      .catch((reason) => message.error(errorMessage(reason)));
  }, []);

  useEffect(() => {
    void loadFeedback();
  }, [loadFeedback]);

  const loadDetail = async (feedbackId: string): Promise<void> => {
    if (details[feedbackId] || loadingDetailIds.has(feedbackId)) {
      return;
    }
    setLoadingDetailIds((current) => new Set(current).add(feedbackId));
    try {
      const detail = await api.getAnnotationFeedbackDetail(feedbackId);
      setDetails((current) => ({ ...current, [feedbackId]: detail }));
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setLoadingDetailIds((current) => {
        const next = new Set(current);
        next.delete(feedbackId);
        return next;
      });
    }
  };

  const resetFilters = (): void => {
    setDateRange(["", ""]);
    setKnowledgeBaseId(undefined);
    setFeedbackType(undefined);
    setKeywordDraft("");
    setQueryKeyword("");
    setPage(1);
  };

  const columns: ColumnsType<AnnotationFeedbackListItem> = [
    {
      title: "用户",
      key: "submitted_by",
      width: 190,
      render: (_, item) => `${item.submitted_by.display_name}（${item.submitted_by.username}）`
    },
    {
      title: "检索类型",
      dataIndex: "interaction_type",
      width: 120,
      render: (value: AnnotationFeedbackListItem["interaction_type"]) => interactionTypeLabels[value]
    },
    {
      title: "查询语句",
      dataIndex: "queries",
      width: 300,
      render: (queries: string[]) => (
        <Space direction="vertical" size={0}>
          {queries.map((query, index) => (
            <Typography.Text key={`${index}:${query}`}>{query}</Typography.Text>
          ))}
        </Space>
      )
    },
    {
      title: "目标知识库",
      dataIndex: "target_knowledge_base_name",
      width: 170,
      render: (value: string | null) => value ?? "全部知识库"
    },
    {
      title: "结果标签汇总",
      key: "label_counts",
      width: 330,
      render: (_, item) => (
        <Space size={[4, 4]} wrap>
          {(Object.keys(feedbackTypeLabels) as SearchAnnotationResultLabel[])
            .filter((type) => resultLabelCount(item, type) > 0)
            .map((type) => (
              <Tag key={type} color={feedbackTypeColor(type)}>
                {feedbackTypeLabels[type]}：{resultLabelCount(item, type)}
              </Tag>
            ))}
        </Space>
      )
    },
    {
      title: "检索时间",
      dataIndex: "searched_at",
      width: 170,
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (value: string) => formatDateTime(value)
    },
    {
      title: "标注时间",
      dataIndex: "submitted_at",
      width: 170,
      onCell: () => ({ style: { whiteSpace: "nowrap" } }),
      render: (value: string) => formatDateTime(value)
    },
    { title: "展示结果数", dataIndex: "result_count", width: 110 }
  ];

  const feedbackContent = (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={8} xl={4}>
          <Card size="small">
            <Statistic title="已完成 Review" value={summary?.completed_review_count ?? 0} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={8} xl={4}>
          <Card size="small">
            <Statistic title="已标注结果" value={summary?.annotated_result_count ?? 0} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={8} xl={4}>
          <Card size="small">
            <Statistic
              title="分数高但是无关"
              value={summary?.high_score_irrelevant_count ?? 0}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={8} xl={4}>
          <Card size="small">
            <Statistic title="分数低但是有关" value={summary?.low_score_relevant_count ?? 0} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={8} xl={4}>
          <Card size="small">
            <Statistic title="结果正常（跳过）" value={summary?.normal_count ?? 0} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={8} xl={4}>
          <Card size="small">
            <Statistic title="其他" value={summary?.other_count ?? 0} />
          </Card>
        </Col>
      </Row>
      <Card size="small">
        <Space wrap>
          <DatePicker.RangePicker
            value={
              dateRange[0] && dateRange[1]
                ? [dayjs(dateRange[0]), dayjs(dateRange[1])]
                : null
            }
            onChange={(_values, dateStrings) => {
              setDateRange(dateStrings as [string, string]);
              setPage(1);
            }}
          />
          <Select
            allowClear
            placeholder="全部知识库"
            style={{ minWidth: 180 }}
            value={knowledgeBaseId}
            onChange={(value) => {
              setKnowledgeBaseId(value);
              setPage(1);
            }}
            options={knowledgeBases.map((knowledgeBase) => ({
              value: knowledgeBase.id,
              label: knowledgeBase.name
            }))}
          />
          <Select
            allowClear
            placeholder="全部结果标签"
            style={{ minWidth: 180 }}
            value={feedbackType}
            onChange={(value) => {
              setFeedbackType(value);
              setPage(1);
            }}
            options={(Object.keys(feedbackTypeLabels) as SearchAnnotationResultLabel[]).map(
              (type) => ({ value: type, label: feedbackTypeLabels[type] })
            )}
          />
          <Input.Search
            allowClear
            placeholder="查询关键词"
            style={{ width: 240 }}
            value={keywordDraft}
            onChange={(event) => setKeywordDraft(event.target.value)}
            onSearch={() => {
              setQueryKeyword(keywordDraft);
              setPage(1);
            }}
          />
          <Button onClick={resetFilters}>重置筛选</Button>
        </Space>
      </Card>
      <Table<AnnotationFeedbackListItem>
        columns={columns}
        dataSource={items}
        loading={loading}
        rowKey="id"
        scroll={{ x: 1750 }}
        expandable={{
          onExpand: (expanded, item) => {
            if (expanded) {
              void loadDetail(item.id);
            }
          },
          expandedRowRender: (item) => {
            const detail = details[item.id];
            if (detail) {
              return <DetailResults detail={detail} />;
            }
            return loadingDetailIds.has(item.id) ? <Spin /> : "展开以加载当时返回的结果";
          }
        }}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          showSizeChanger: false,
          onChange: (nextPage) => setPage(nextPage)
        }}
      />
    </Space>
  );

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>数据面板</Typography.Title>
          <Typography.Paragraph type="secondary">
            查看不可修改的检索 Review 及其逐条结果标签；汇总会随时间、知识库和查询关键词筛选更新，标签筛选只显示包含该标签的 Review。
          </Typography.Paragraph>
        </div>
      </div>
      <Tabs items={[{ key: "annotation-feedback", label: "标注反馈", children: feedbackContent }]} />
    </section>
  );
}
