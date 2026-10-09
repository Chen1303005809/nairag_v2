import { EditOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, Modal, Space, Typography, message } from "antd";
import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { KnowledgeContentTaxonomy, KnowledgeTaxonomyField } from "../api/types";

interface TaxonomyOptionFormValues {
  value: string;
}

interface TaxonomyFieldDefinition {
  key: KnowledgeTaxonomyField;
  label: string;
}

const taxonomyFields: TaxonomyFieldDefinition[] = [
  { key: "question_types", label: "问题类型" },
  { key: "business_objects", label: "具体功能与模块" },
  { key: "purposes", label: "应用场景" },
  { key: "customer_types", label: "客户类型" }
];

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "操作失败，请稍后重试";
}

export function TaxonomyOptionsManagementPanel(): JSX.Element {
  const [taxonomy, setTaxonomy] = useState<KnowledgeContentTaxonomy>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dialog, setDialog] = useState<{
    field: TaxonomyFieldDefinition;
    oldValue?: string;
  }>();
  const [form] = Form.useForm<TaxonomyOptionFormValues>();

  const loadTaxonomy = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      setTaxonomy(await api.getKnowledgeContentTaxonomy());
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTaxonomy();
  }, [loadTaxonomy]);

  const openDialog = (field: TaxonomyFieldDefinition, oldValue?: string): void => {
    setDialog({ field, oldValue });
    form.setFieldsValue({ value: oldValue ?? "" });
  };

  const saveOption = async (values: TaxonomyOptionFormValues): Promise<void> => {
    if (!dialog) return;
    const value = values.value.trim();
    setSaving(true);
    try {
      const updated = dialog.oldValue
        ? await api.renameKnowledgeTaxonomyOption(dialog.field.key, dialog.oldValue, value)
        : await api.createKnowledgeTaxonomyOption(dialog.field.key, value);
      setTaxonomy(updated);
      setDialog(undefined);
      form.resetFields();
      message.success(dialog.oldValue ? "选项已更新" : "选项已添加");
    } catch (reason) {
      message.error(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Space align="start" style={{ width: "100%", justifyContent: "space-between" }} wrap>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          管理知识上传和搜索使用的分类选项。改名只影响后续选择，已有知识保留原值。
        </Typography.Paragraph>
        <Button icon={<ReloadOutlined />} onClick={() => void loadTaxonomy()} loading={loading}>
          刷新
        </Button>
      </Space>
      <div className="taxonomy-options-grid">
        {taxonomyFields.map((field) => (
          <Card
            key={field.key}
            title={field.label}
            extra={
              <Button
                type="primary"
                size="small"
                icon={<PlusOutlined />}
                onClick={() => openDialog(field)}
              >
                添加选项
              </Button>
            }
            loading={loading}
          >
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {(taxonomy?.[field.key] ?? []).map((value, index) => (
                <Space
                  key={value}
                  style={{ width: "100%", justifyContent: "space-between" }}
                  className="taxonomy-option-row"
                >
                  <Typography.Text>
                    {index + 1}. {value}
                  </Typography.Text>
                  <Button
                    type="link"
                    icon={<EditOutlined />}
                    onClick={() => openDialog(field, value)}
                  >
                    编辑
                  </Button>
                </Space>
              ))}
            </Space>
          </Card>
        ))}
      </div>
      <Modal
        title={`${dialog?.oldValue ? "编辑" : "添加"}${dialog?.field.label ?? "选项"}`}
        open={Boolean(dialog)}
        onCancel={() => {
          setDialog(undefined);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        okText="保存"
        confirmLoading={saving}
        destroyOnClose
      >
        <Form<TaxonomyOptionFormValues>
          form={form}
          layout="vertical"
          onFinish={(values) => void saveOption(values)}
        >
          <Form.Item
            name="value"
            label="选项内容"
            rules={[
              { required: true, whitespace: true, message: "请输入选项内容" },
              { max: 255, message: "选项内容不能超过 255 个字符" }
            ]}
          >
            <Input maxLength={255} autoFocus />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
