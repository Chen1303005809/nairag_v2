import {
  Alert,
  Button,
  Card,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message
} from "antd";
import { DeleteOutlined, ReloadOutlined, UploadOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import { formatDateTime } from "../dateTime";
import type { SupplementalMaterial } from "../api/types";

const pageSize = 20;
const statusOptions = ["pending", "processing", "processed", "failed"];

function fileExtension(filename: string): string {
  const suffix = filename.split(".").pop();
  return suffix && suffix !== filename ? suffix.toLowerCase() : "";
}

function statusColor(value: string | null): string {
  if (value === "failed") return "red";
  if (value === "processed" || value === "completed") return "green";
  if (value === "processing") return "blue";
  return "gold";
}

export function GlobalMaterialsManagementPage(): JSX.Element {
  const [materials, setMaterials] = useState<SupplementalMaterial[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [extensions, setExtensions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [types, result] = await Promise.all([
        api.listSupplementalSupportedFileTypes(),
        api.listSupplementalMaterials(page, pageSize, statuses)
      ]);
      setExtensions(types.extensions);
      setMaterials(result.materials);
      setTotal(result.total);
      setUnavailable(false);
    } catch (reason) {
      setUnavailable(reason instanceof ApiError && reason.status === 503);
      message.error(reason instanceof Error ? reason.message : "无法读取全局资料");
    } finally {
      setLoading(false);
    }
  }, [page, statuses]);

  useEffect(() => {
    void load();
  }, [load]);

  const upload = async (file: File): Promise<void> => {
    const extension = fileExtension(file.name);
    if (extensions.length > 0 && !extensions.includes(extension)) {
      message.warning(`当前服务不支持 .${extension || "（无扩展名）"} 文件`);
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      message.warning("文件不能超过 20 MB");
      return;
    }
    setUploading(true);
    try {
      const result = await api.uploadSupplementalMaterial(file);
      message.success(result.track_id ? "文件已提交处理" : "文件已上传");
      setPage(1);
      await load();
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (documentId: string): Promise<void> => {
    try {
      await api.deleteSupplementalMaterial(documentId);
      message.success("资料已删除");
      if (materials.length === 1 && page > 1) {
        setPage((current) => current - 1);
      } else {
        await load();
      }
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : "删除失败");
    }
  };

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={3}>全局资料</Typography.Title>
          <Typography.Paragraph type="secondary">
            资料独立存储于 LightRAG，仅作为所有知识库检索后的补充来源。这里不会提供清空、缓存、扫描、重处理或文本编辑操作。
          </Typography.Paragraph>
        </div>
      </div>
      {unavailable ? (
        <Alert
          showIcon
          type="warning"
          style={{ marginBottom: 16 }}
          message="全局补充资料服务暂不可用"
          description="平台主知识库功能不受影响；待独立服务恢复并通过健康检查后可自动继续管理。"
        />
      ) : null}
      <Card>
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Space wrap>
            <Upload
              accept={extensions.length > 0 ? extensions.map((item) => `.${item}`).join(",") : undefined}
              beforeUpload={(file) => {
                void upload(file);
                return Upload.LIST_IGNORE;
              }}
              disabled={uploading || unavailable}
              showUploadList={false}
            >
              <Button icon={<UploadOutlined />} loading={uploading} disabled={unavailable}>
                上传资料
              </Button>
            </Upload>
            <Select
              allowClear
              mode="multiple"
              value={statuses}
              options={statusOptions.map((value) => ({ value, label: value }))}
              placeholder="按状态筛选"
              style={{ minWidth: 240 }}
              onChange={(values) => {
                setPage(1);
                setStatuses(values);
              }}
              disabled={unavailable}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              刷新
            </Button>
            {extensions.length > 0 ? (
              <Typography.Text type="secondary">
                支持：{extensions.map((item) => `.${item}`).join("、")}；最大 20 MB
              </Typography.Text>
            ) : null}
          </Space>
          <Table<SupplementalMaterial>
            dataSource={materials}
            loading={loading}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: false,
              onChange: setPage
            }}
            rowKey="document_id"
            size="small"
            columns={[
              {
                title: "资料",
                dataIndex: "title",
                render: (value: string, record) => (
                  <Space direction="vertical" size={0}>
                    <Typography.Text>{value}</Typography.Text>
                    {record.track_id ? <Typography.Text type="secondary">任务：{record.track_id}</Typography.Text> : null}
                  </Space>
                )
              },
              {
                title: "状态",
                dataIndex: "status",
                width: 130,
                render: (value: string | null) => <Tag color={statusColor(value)}>{value ?? "未知"}</Tag>
              },
              {
                title: "进度",
                dataIndex: "progress",
                width: 150,
                render: (value: number | null) =>
                  value === null ? "-" : <Progress percent={Math.max(0, Math.min(100, value * 100))} size="small" />
              },
              {
                title: "分块",
                dataIndex: "chunks_count",
                width: 90,
                render: (value: number | null) => value ?? "-"
              },
              {
                title: "更新时间",
                dataIndex: "updated_at",
                width: 180,
                onCell: () => ({ style: { whiteSpace: "nowrap" } }),
                render: (value: string | null) => formatDateTime(value)
              },
              {
                title: "处理信息",
                dataIndex: "error_message",
                render: (value: string | null) => value ?? "-"
              },
              {
                title: "操作",
                width: 90,
                render: (_value, record) => (
                  <Popconfirm
                    title="删除这份全局资料？"
                    description="删除后将无法从补充资料中检索到它。"
                    okText="删除"
                    cancelText="取消"
                    onConfirm={() => remove(record.document_id)}
                  >
                    <Button danger size="small" icon={<DeleteOutlined />} disabled={unavailable}>
                      删除
                    </Button>
                  </Popconfirm>
                )
              }
            ]}
          />
        </Space>
      </Card>
    </section>
  );
}
