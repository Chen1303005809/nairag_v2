# 本地 Docker Secret

请在本目录创建以下两个文件，且不要提交它们：

- `initial_admin_password.txt`：仅在 `user_account` 为空时使用的初始系统管理员密码；首次登录后必须修改。
- `jwt_signing_key.txt`：长度至少 32 字符的随机 JWT 签名密钥。

可使用以下命令生成 JWT 密钥：

```bash
openssl rand -base64 48 > secrets/jwt_signing_key.txt
```

初始管理员用户名由 `INITIAL_ADMIN_USERNAME` 设置，默认值为 `admin`。服务不会把上述 Secret 输出到日志。

