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
  Tag,
  Typography,
  message
} from "antd";
import type { TableProps } from "antd";
import { useEffect, useState } from "react";

import { api } from "../api/client";
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
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [decisionItem, setDecisionItem] = useState<ReviewQueueItem>();
  const [decision, setDecision] = useState<ReviewDecisionKind>("approved");
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);

  const refresh = async (): Promise<void> => {
    setLoading(true);
    try {
      const [nextQueue, nextKnowledgeBases] = await Promise.all([
        api.listReviewQueue(knowledgeBaseId),
        systemAdmin ? api.listKnowledgeBases() : api.listAssignedReviewKnowledgeBases()
      ]);
      setQueue(nextQueue);
      setKnowledgeBases(nextKnowledgeBases);
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
      message.success(decision === "approved" ? "已通过审核" : "已驳回投稿");
      setDecisionItem(undefined);
      setComment("");
      await refresh();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "保存审核决定失败");
    } finally {
      setSaving(false);
    }
  };

  const columns: TableProps<ReviewQueueItem>["columns"] = [
    {
      title: "投稿内容",
      key: "content",
      render: (_value: unknown, item: ReviewQueueItem) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>
            {item.parent_revision?.name ?? item.child_revision.question}
          </Typography.Text>
          <Typography.Text type="secondary">
            {item.submission_kind === "parent_with_primary" ? "父类 + 主子条目" : "普通子条目"}
          </Typography.Text>
        </Space>
      )
    },
    {
      title: "目标知识库",
      dataIndex: ["knowledge_base", "name"],
      key: "knowledge_base"
    },
    {
      title: "投稿人",
      dataIndex: ["submitter", "display_name"],
      key: "submitter"
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
      render: (_value: unknown, item: ReviewQueueItem) => (
        <Button type="primary" onClick={() => setDecisionItem(item)}>
          审核
        </Button>
      )
    }
  ];

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>审核工作台</Typography.Title>
          <Typography.Paragraph type="secondary">
            这里只展示你有权限审核的目标库。父类聚合会在全部目标通过且索引完成后整体发布。
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
      {knowledgeBases.length === 0 && !systemAdmin ? (
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
            columns={columns}
            dataSource={queue}
            expandable={{
              expandedRowRender: (item) => (
                <Descriptions bordered size="small" column={1}>
                  {item.parent_revision ? (
                    <Descriptions.Item label="父类关键词">
                      {item.parent_revision.canonical_keyword}
                    </Descriptions.Item>
                  ) : null}
                  <Descriptions.Item label="问题">
                    {item.child_revision.question}
                  </Descriptions.Item>
                  <Descriptions.Item label="回复内容">
                    {item.child_revision.response_content}
                  </Descriptions.Item>
                  {item.child_revision.question_variants.length > 0 ? (
                    <Descriptions.Item label="同义问句">
                      {item.child_revision.question_variants.join("；")}
                    </Descriptions.Item>
                  ) : null}
                  {item.child_revision.internal_notes ? (
                    <Descriptions.Item label="内部备注">
                      {item.child_revision.internal_notes}
                    </Descriptions.Item>
                  ) : null}
                </Descriptions>
              )
            }}
            pagination={{ pageSize: 10, hideOnSinglePage: true }}
            locale={{ emptyText: "当前没有待审核投稿" }}
          />
        </Card>
      )}
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
