import { Descriptions, Image, Space, Typography } from "antd";

import { api } from "../api/client";
import type { ReviewChildRevision, ReviewParentRevision } from "../api/types";

export function ChildRevisionFullView({
  childRevision,
  parentRevision,
  parentName
}: {
  childRevision: ReviewChildRevision;
  parentRevision?: ReviewParentRevision | null;
  parentName?: string;
}): JSX.Element {
  const revision = childRevision;
  return (
    <Descriptions bordered size="small" column={1} title="提交内容全貌">
      {parentRevision ? (
        <>
          <Descriptions.Item label="问题大类">{parentRevision.name}</Descriptions.Item>
          <Descriptions.Item label="问题大类关键词">
            {parentRevision.canonical_keyword}
          </Descriptions.Item>
          {parentRevision.lexical_rules.length > 0 ? (
            <Descriptions.Item label="别名/词法规则">
              <Space direction="vertical" size={4}>
                {parentRevision.lexical_rules.map((rule, index) => (
                  <Typography.Text key={`${rule.rule_type}-${rule.rule_value}-${index}`}>
                    [{rule.rule_type}] {rule.rule_value}
                  </Typography.Text>
                ))}
              </Space>
            </Descriptions.Item>
          ) : null}
        </>
      ) : parentName ? (
        <Descriptions.Item label="问题大类">{parentName}</Descriptions.Item>
      ) : null}
      <Descriptions.Item label="问题">
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
          {revision.question}
        </Typography.Paragraph>
      </Descriptions.Item>
      <Descriptions.Item label="回复内容">
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
          {revision.response_content}
        </Typography.Paragraph>
      </Descriptions.Item>
      {revision.question_variants.length > 0 ? (
        <Descriptions.Item label="同义问句">
          <Space direction="vertical" size={4}>
            {revision.question_variants.map((variant) => (
              <Typography.Text key={variant}>{variant}</Typography.Text>
            ))}
          </Space>
        </Descriptions.Item>
      ) : null}
      {revision.follow_up_guidance ? (
        <Descriptions.Item label="后续指引">
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {revision.follow_up_guidance}
          </Typography.Paragraph>
        </Descriptions.Item>
      ) : null}
      {revision.question_type ? (
        <Descriptions.Item label="问题类型">{revision.question_type}</Descriptions.Item>
      ) : null}
      {revision.business_object ? (
        <Descriptions.Item label="具体功能与模块">{revision.business_object}</Descriptions.Item>
      ) : null}
      {revision.purpose ? (
        <Descriptions.Item label="应用场景">{revision.purpose}</Descriptions.Item>
      ) : null}
      {revision.customer_type ? (
        <Descriptions.Item label="客户类型">{revision.customer_type}</Descriptions.Item>
      ) : null}
      {revision.feature_explanation ? (
        <Descriptions.Item label="功能说明">
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {revision.feature_explanation}
          </Typography.Paragraph>
        </Descriptions.Item>
      ) : null}
      {revision.example ? (
        <Descriptions.Item label="示例">
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {revision.example}
          </Typography.Paragraph>
        </Descriptions.Item>
      ) : null}
      {revision.attachments.length > 0 ? (
        <Descriptions.Item label="佐证附件">
          <Space size={[8, 8]} wrap>
            {revision.attachments.map((attachment) => {
              const downloadUrl = api.knowledgeAttachmentDownloadUrl(attachment.id);
              return attachment.content_type.startsWith("image/") ? (
                <Image key={attachment.id} alt={attachment.name} src={downloadUrl} width={120} />
              ) : (
                <a key={attachment.id} href={downloadUrl} rel="noreferrer" target="_blank">
                  {attachment.name}
                  <Typography.Text type="secondary">（点击下载）</Typography.Text>
                </a>
              );
            })}
          </Space>
        </Descriptions.Item>
      ) : null}
      {revision.web_links.length > 0 ? (
        <Descriptions.Item label="相关网页链接">
          <Space direction="vertical" size={4}>
            {revision.web_links.map((webLink) => (
              <a key={webLink.url} href={webLink.url} rel="noreferrer" target="_blank">
                {webLink.title || webLink.url}
              </a>
            ))}
          </Space>
        </Descriptions.Item>
      ) : null}
      {revision.internal_notes ? (
        <Descriptions.Item label="内部备注">
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {revision.internal_notes}
          </Typography.Paragraph>
        </Descriptions.Item>
      ) : null}
    </Descriptions>
  );
}
