import {
  Alert,
  Button,
  Card,
  Descriptions,
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
import { ChildRevisionFullView } from "../components/ChildRevisionFullView";
import { TableActionBar } from "../components/TableActionBar";
import { formatDateTime } from "../dateTime";
import { uniqueTableFilterOptions } from "../tableFilters";
import type {
  KnowledgeBase,
  ReviewDecisionKind,
  ReviewQueueItem,
  ReviewTargetStatus
} from "../api/types";

function targetStatus(status: ReviewTargetStatus): JSX.Element {
  const labels: Record<ReviewTargetStatus, [string, string]> = {
    pending_review: ["gold", "待审核"],
    approved: ["processing", "待索引"],
    rejected: ["error", "已驳回"],
    indexing: ["processing", "索引中"],
    published: ["success", "已发布"],
    index_failed: ["error", "索引失败"]
  };
  const [color, label] = labels[status];
  return <Tag color={color}>{label}</Tag>;
}

export function ReviewWorkbenchPage({ systemAdmin = false }: { systemAdmin?: boolean }): JSX.Element {
  const [queue, setQueue] = useState<ReviewQueueItem[]>([]);
  const [history, setHistory] = useState<ReviewQueueItem[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [decisionItem, setDecisionItem] = useState<ReviewQueueItem>();
  const [decision, setDecision] = useState<ReviewDecisionKind>("approved");
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [retryingKey, setRetryingKey] = useState<string>();

  const refresh = async (): Promise<void> => {
    setLoading(true);
    try {
      const [nextQueue, nextKnowledgeBases, nextHistory] = await Promise.all([
        api.listReviewQueue(knowledgeBaseId),
        systemAdmin ? api.listKnowledgeBases() : api.listAssignedReviewKnowledgeBases(),
        api.listMyReviewHistory()
      ]);
      setQueue(nextQueue);
      setKnowledgeBases(nextKnowledgeBases);
      setHistory(nextHistory);
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "无法加载审核队列");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, [knowledgeBaseId, systemAdmin]);

  const submitDecision = async (): Promise<void> => {
    if (!decisionItem) {
      return;
    }
    setSaving(true);
    try {
      await api.decideReviewTarget(
        decisionItem.review_submission_id,
        decisionItem.knowledge_base.id,
        decision,
        comment
      );
      message.success(decision === "approved" ? "已通过审核" : "已驳回上传");
      setDecisionItem(undefined);
      setComment("");
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "保存审核决定失败");
    } finally {
      setSaving(false);
    }
  };

  const retryIndexing = async (item: ReviewQueueItem): Promise<void> => {
    const key = `${item.review_submission_id}:${item.knowledge_base.id}`;
    setRetryingKey(key);
    try {
      await api.retryReviewTargetIndexing(item.review_submission_id, item.knowledge_base.id);
      message.success("已重新加入索引队列");
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "重试索引失败");
    } finally {
      setRetryingKey(undefined);
    }
  };

  const contentCell = (item: ReviewQueueItem): JSX.Element => (
    <Space direction="vertical" size={0}>
      <Typography.Text strong>{item.parent_revision?.name ?? item.child_revision.question}</Typography.Text>
      <Typography.Text type="secondary">
        {item.submission_kind === "parent_with_primary" ? "问题大类" : "问题小类"}
      </Typography.Text>
    </Space>
  );

  const expandedRow = (item: ReviewQueueItem): JSX.Element => (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <ChildRevisionFullView
        childRevision={item.child_revision}
        parentRevision={item.parent_revision}
      />
      <Descriptions bordered size="small" column={1} title="审核信息">
        <Descriptions.Item label="上传者">
          {item.submitter.display_name}（{item.submitter.username}）
        </Descriptions.Item>
        <Descriptions.Item label="上传时间">{formatDateTime(item.submitted_at)}</Descriptions.Item>
        <Descriptions.Item label="审核者">
          {item.reviewer ? `${item.reviewer.display_name}（${item.reviewer.username}）` : "—"}
        </Descriptions.Item>
        <Descriptions.Item label="实际审核时间">{formatDateTime(item.reviewed_at)}</Descriptions.Item>
        {item.review_comment ? (
          <Descriptions.Item label="审核备注">{item.review_comment}</Descriptions.Item>
        ) : null}
      </Descriptions>
    </Space>
  );

  const queueKnowledgeBaseFilters = useMemo(
    () =>
      uniqueTableFilterOptions(queue, (item) => [
        { text: item.knowledge_base.name, value: item.knowledge_base.id }
      ]),
    [queue]
  );

  const queueUploaderFilters = useMemo(
    () =>
      uniqueTableFilterOptions(queue, (item) => [
        {
          text: `${item.submitter.display_name}（${item.submitter.username}）`,
          value: item.submitter.id
        }
      ]),
    [queue]
  );

  const historyKnowledgeBaseFilters = useMemo(
    () =>
      uniqueTableFilterOptions(history, (item) => [
        { text: item.knowledge_base.name, value: item.knowledge_base.id }
      ]),
    [history]
  );

  const historyUploaderFilters = useMemo(
    () =>
      uniqueTableFilterOptions(history, (item) => [
        {
          text: `${item.submitter.display_name}（${item.submitter.username}）`,
          value: item.submitter.id
        }
      ]),
    [history]
  );

  const queueColumns: TableProps<ReviewQueueItem>["columns"] = [
    {
      title: "上传内容",
      key: "content",
      render: (_value: unknown, item: ReviewQueueItem) => contentCell(item)
    },
    {
      title: "目标知识库",
      dataIndex: ["knowledge_base", "name"],
      key: "knowledge_base",
      filters: queueKnowledgeBaseFilters,
      filterSearch: true,
      onFilter: (value, item) => item.knowledge_base.id === String(value)
    },
    {
      title: "上传者",
      key: "submitter",
      filters: queueUploaderFilters,
      filterSearch: true,
      onFilter: (value, item) => item.submitter.id === String(value),
      render: (_value: unknown, item: ReviewQueueItem) =>
        `${item.submitter.display_name}（${item.submitter.username}）`
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
      render: () => "—"
    },
    {
      title: "实际审核时间",
      key: "reviewed_at",
      render: () => "—"
    },
    {
      title: "状态",
      dataIndex: "target_status",
      key: "target_status",
      render: (value: ReviewTargetStatus) => targetStatus(value)
    },
    {
      title: "操作",
      key: "actions",
      width: 100,
      fixed: "right",
      ellipsis: true,
      render: (_value: unknown, item: ReviewQueueItem) => (
        <TableActionBar>
          <Button type="primary" onClick={() => setDecisionItem(item)}>
            审核
          </Button>
        </TableActionBar>
      )
    }
  ];

  const historyColumns: TableProps<ReviewQueueItem>["columns"] = [
    {
      title: "上传内容",
      key: "content",
      render: (_value: unknown, item: ReviewQueueItem) => contentCell(item)
    },
    {
      title: "目标知识库",
      dataIndex: ["knowledge_base", "name"],
      key: "knowledge_base",
      filters: historyKnowledgeBaseFilters,
      filterSearch: true,
      onFilter: (value, item) => item.knowledge_base.id === String(value)
    },
    {
      title: "上传者",
      key: "submitter",
      filters: historyUploaderFilters,
      filterSearch: true,
      onFilter: (value, item) => item.submitter.id === String(value),
      render: (_value: unknown, item: ReviewQueueItem) =>
        `${item.submitter.display_name}（${item.submitter.username}）`
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
      render: (_value: unknown, item: ReviewQueueItem) =>
        item.reviewer ? `${item.reviewer.display_name}（${item.reviewer.username}）` : "—"
    },
    {
      title: "实际审核时间",
      dataIndex: "reviewed_at",
      key: "reviewed_at",
      render: (value: string | null) => formatDateTime(value)
    },
    {
      title: "审核结果",
      dataIndex: "review_decision",
      key: "review_decision",
      render: (value: ReviewDecisionKind | null) =>
        value === "approved" ? <Tag color="green">通过</Tag> : <Tag color="red">驳回</Tag>
    },
    {
      title: "审核备注",
      dataIndex: "review_comment",
      key: "review_comment",
      render: (value: string | null) => value || "—"
    },
    {
      title: "当前状态/操作",
      key: "status_actions",
      width: 180,
      fixed: "right",
      ellipsis: true,
      render: (_value: unknown, item: ReviewQueueItem) => (
        <TableActionBar>
          {targetStatus(item.target_status)}
          {item.target_status === "index_failed" ? (
            <Button
              type="primary"
              ghost
              loading={retryingKey === `${item.review_submission_id}:${item.knowledge_base.id}`}
              onClick={() => void retryIndexing(item)}
            >
              重试索引
            </Button>
          ) : null}
        </TableActionBar>
      )
    }
  ];

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>审核工作台</Typography.Title>
          <Typography.Paragraph type="secondary">
            待审核仅展示你有权限处理的目标库；“我的审核历史”保留你已作出的审核决定，并在同一行显示当前索引状态与失败重试入口。问题大类与问题小类会在全部目标通过且索引完成后整体发布。
          </Typography.Paragraph>
        </div>
        <Space>
          <Select
            allowClear
            placeholder="全部授权知识库"
            value={knowledgeBaseId}
            onChange={setKnowledgeBaseId}
            options={knowledgeBases.map((knowledgeBase) => ({
              value: knowledgeBase.id,
              label: knowledgeBase.name
            }))}
            style={{ minWidth: 180 }}
          />
          <Button onClick={() => void refresh()} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>
      <Tabs
        items={[
          {
            key: "queue",
            label: "待审核",
            children:
              knowledgeBases.length === 0 && !systemAdmin ? (
                <Alert
                  type="info"
                  showIcon
                  message="尚未分配审核知识库"
                  description="请联系系统管理员为你的账号分配至少一个知识库。"
                />
              ) : (
                <Card>
                  <Table<ReviewQueueItem>
                    rowKey={(item) => `${item.review_submission_id}:${item.knowledge_base.id}`}
                    loading={loading}
                    columns={queueColumns}
                    dataSource={queue}
                    scroll={{ x: 1280 }}
                    expandable={{ expandedRowRender: expandedRow }}
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                    locale={{ emptyText: "当前没有待审核的上传内容" }}
                  />
                </Card>
              )
          },
          {
            key: "history",
            label: "我的审核历史",
            children: (
              <Card>
                <Table<ReviewQueueItem>
                  rowKey={(item) => `${item.review_submission_id}:${item.knowledge_base.id}:${item.id}`}
                  loading={loading}
                  columns={historyColumns}
                  dataSource={history}
                  scroll={{ x: 1450 }}
                  expandable={{ expandedRowRender: expandedRow }}
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  locale={{ emptyText: "尚无审核历史" }}
                />
              </Card>
            )
          }
        ]}
      />
      <Modal
        title="记录审核决定"
        open={Boolean(decisionItem)}
        onCancel={() => setDecisionItem(undefined)}
        onOk={() => void submitDecision()}
        okButtonProps={{ loading: saving }}
        okText="提交决定"
      >
        {decisionItem ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text>
              {decisionItem.parent_revision?.name ?? decisionItem.child_revision.question}
              {` · ${decisionItem.knowledge_base.name}`}
            </Typography.Text>
            <Select
              value={decision}
              onChange={setDecision}
              options={[
                { value: "approved", label: "通过" },
                { value: "rejected", label: "驳回" }
              ]}
              style={{ width: "100%" }}
            />
            <Input.TextArea
              rows={4}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="审核备注（可选）"
            />
          </Space>
        ) : null}
      </Modal>
    </section>
  );
}
