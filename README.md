# FreshLoop

智能家庭食品管理与膳食决策平台。

## D01 本地启动

1. 复制 `.env.example` 为 `.env`。
2. 执行 `docker compose up --build`。
3. 打开 http://localhost:5173 查看前端。
4. 打开 http://localhost:8000/docs 查看 API 文档。

当前版本提供 FastAPI 健康检查、Vue 3/Vite 前端、PostgreSQL/pgvector 和 Redis 基础服务。后续开发按《FreshLoop项目完整规划》中的 D02-D15 推进。

