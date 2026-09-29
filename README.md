# FreshLoop

智能家庭食品管理与膳食决策平台。当前版本已打通账户、家庭数据隔离、食品档案、批次库存、不可变库存流水、临期统计、消费分析和膳食计划草稿。

## 直接体验

本地开发默认使用 SQLite，不依赖 Docker：

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```

另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

打开 <http://localhost:5173>。开发环境首次启动会生成演示数据：

- 邮箱：`demo@freshloop.app`
- 密码：`freshloop123`

API 文档位于 <http://localhost:8000/docs>。

## 接入 AI 模型

后端支持 OpenAI 兼容接口，默认配置为阿里云百炼千问。将以下内容写入 `backend/.env`，然后重启后端：

```text
LLM_API_KEY=你的_API_Key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-turbo
```

使用其他 OpenAI 兼容模型时，替换 `LLM_BASE_URL` 和 `LLM_MODEL`。密钥不会发送到浏览器或写入仓库。

## Docker Compose

执行 `docker compose up --build` 会启动 Vue 前端、FastAPI、PostgreSQL/pgvector 和 Redis。Compose 环境通过环境变量让后端使用 PostgreSQL；直接运行后端时默认使用项目目录中的 SQLite 数据库。

## 已实现

- 注册、登录、JWT 鉴权和家庭空间数据隔离
- 食品档案及带购买日、到期日、价格的库存批次
- 入库、消费、丢弃流水；消费数量校验及事务更新
- 小票照片本地 OCR、文本草稿解析、重复导入拦截和确认入库
- 临期、过期、剩余库存价值统计
- 14 天消费量、丢弃量、浪费率及趋势分析
- 按库存和临期顺序生成 1–7 天膳食计划草稿并确认
- 响应式 Vue 界面、FastAPI OpenAPI 文档和 GitHub Actions 检查

## 验证

```powershell
cd backend
$env:DATABASE_URL='sqlite+aiosqlite:///./freshloop_test.db'
$env:APP_ENV='test'
.venv\Scripts\python -m pytest -q

cd ../frontend
npm run build
```

## 后续里程碑

小票 OCR、异步任务、知识库检索和外部大模型工具调用仍按项目完整规划继续推进。规划文档位于上级目录的 `outputs/FreshLoop项目完整规划.md`。
