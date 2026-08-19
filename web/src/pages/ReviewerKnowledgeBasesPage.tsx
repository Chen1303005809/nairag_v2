import { Alert, Card, Empty, Spin, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { KnowledgeBase } from "../api/types";

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "无法加载授权知识库";
}

export function ReviewerKnowledgeBasesPage(): JSX.Element {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const loadKnowledgeBases = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(undefined);
    try {
      setKnowledgeBases(await api.listAssignedReviewKnowledgeBases());
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadKnowledgeBases();
  }, [loadKnowledgeBases]);

  if (loading) {
    return <Spin />;
  }
  if (error) {
    return <Alert type="error" showIcon message={error} />;
  }
  if (!knowledgeBases.length) {
    return <Empty description="你尚未获分配任何启用中的知识库" />;
  }

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>我的审查知识库</Typography.Title>
          <Typography.Paragraph type="secondary">
            这里只显示你当前获授权且处于启用状态的知识库。
          </Typography.Paragraph>
        </div>
      </div>
      <div className="knowledge-base-card-grid">
        {knowledgeBases.map((knowledgeBase) => (
          <Card key={knowledgeBase.id} title={knowledgeBase.name} size="small">
            <Typography.Paragraph code>{knowledgeBase.logical_key}</Typography.Paragraph>
            <Typography.Paragraph type="secondary">
              {knowledgeBase.description || "未填写说明"}
            </Typography.Paragraph>
          </Card>
        ))}
      </div>
    </section>
  );
}
