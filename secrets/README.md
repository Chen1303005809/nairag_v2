# 本地 Docker Secret

请在本目录创建以下三个文件，且不要提交它们：

- `postgres_password.txt`：PostgreSQL 本地开发密码；必须与 API/worker 使用的数据库 Secret 一致。

- `initial_admin_password.txt`：仅在 `user_account` 为空时使用的初始系统管理员密码；首次登录后必须修改。
- `jwt_signing_key.txt`：长度至少 32 字符的随机 JWT 签名密钥。

生产环境还需要为附件 Bucket 单独创建两份 MinIO 凭据：

- `attachment_minio_access_key.txt`：附件服务账号的 Access Key。
- `attachment_minio_secret_key.txt`：附件服务账号的 Secret Key。

生产 Compose 会在与 Milvus 共用的 MinIO 服务中创建私有 `nairag-attachments` Bucket，并只向该服务账号授予该 Bucket 的最小读取、写入、删除及分段上传权限。

可为该账号生成独立随机凭据：

```bash
openssl rand -hex 16 > secrets/attachment_minio_access_key.txt
openssl rand -hex 32 > secrets/attachment_minio_secret_key.txt
chmod 600 secrets/attachment_minio_access_key.txt secrets/attachment_minio_secret_key.txt
```

可使用以下命令生成 JWT 密钥：

```bash
openssl rand -base64 48 > secrets/jwt_signing_key.txt
```

也可以直接运行仓库根目录的 `scripts/init-dev-secrets.sh`，它会以 `0600` 权限幂等生成缺失的本地开发 Secret；已有文件不会被覆盖。

初始管理员用户名由 `INITIAL_ADMIN_USERNAME` 设置，默认值为 `admin`。服务不会把上述 Secret 输出到日志。
