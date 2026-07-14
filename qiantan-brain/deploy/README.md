# 生产部署规范

## 1. 前置条件

1. 复制 `.env.example` 为 `.env`，设置强 `POSTGRES_PASSWORD`、`JWT_SECRET`、微信凭据和 `CORS_ORIGINS`。
2. 将 `deploy/nginx/conf.d/qiantan.conf` 中的示例域名替换为真实域名。
3. 把 TLS 文件放到：
   - `deploy/certs/fullchain.pem`
   - `deploy/certs/privkey.pem`
4. 小程序 `extConfig.apiBase` 必须配置为 `https://api.<你的域名>/api/v1`。

## 2. 启动

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml config
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

生产 Compose 会先运行一次 Alembic `upgrade head`，成功后再启动后端。数据库、Redis、后端均不发布宿主机端口，只有 Nginx 暴露 80/443。后端另外连接仅用于外部 API 出站的 `egress` 网络。

本地开发如需访问端口：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

## 3. 备份与恢复

```powershell
./deploy/backup-postgres.ps1
./deploy/restore-postgres.ps1 -BackupFile ./deploy/backups/qiantan-YYYYMMDD-HHMMSS.dump -ConfirmRestore
```

恢复会执行 `--clean --if-exists`，必须先停写、验证备份并安排维护窗口。默认备份保留 14 天。建议再将 `deploy/backups` 异地加密复制到对象存储。

## 4. 发布检查

- `docker compose ... config` 无错误。
- `migrate` 容器退出码为 0。
- `backend`、`db`、`redis`、`nginx` health 均为 healthy。
- 管理后台登录响应含 `HttpOnly; Secure; SameSite=Strict` Cookie。
- `https://api.<域名>/api/v1/health` 返回成功。
- 未授权访问 `/api/v1/health/detailed` 返回 401。
- 执行一次备份，并在隔离环境完成恢复演练。
