# AI Career Copilot

AI 驱动的求职辅助系统：简历解析、岗位匹配、自动投递、智能 Agent。

---

## 项目组成

| 模块 | 技术栈 | 目录 |
|------|--------|------|
| 后端 API | FastAPI + Python 3.12 + 异步 SQLAlchemy + PostgreSQL + Redis + RabbitMQ | `backend/` |
| 浏览器插件 | Chrome Extension MV3 + Vue 3 + TypeScript + Vite + Pinia + Tailwind CSS | `extension/` |

---

## 快速开始

### 方式一：一键启动（推荐，Docker 全栈）

确保已安装并启动 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，然后双击或在 PowerShell 中执行：

```powershell
.\start.ps1
```

脚本会自动完成：

1. 检查 Docker / Docker Compose 环境
2. 检查并生成 `backend/app/configs/.env`（如缺失则从 `.env.example` 复制）
3. 构建并启动 PostgreSQL、Redis、RabbitMQ、后端服务
4. 后端容器首次启动时自动执行 `alembic upgrade head` 创建数据库表
5. 等待后端就绪后输出访问地址

启动成功后访问：

- API 服务：http://localhost:8000
- 健康检查：http://localhost:8000/health（应返回 `{"status": "ok"}`）
- Swagger 文档：http://localhost:8000/docs
- RabbitMQ 管理后台：http://localhost:15672（默认 guest/guest）

### 方式二：手动 Docker 启动

```bash
# 项目根目录
docker compose up -d --build
```

常用命令：

```bash
docker compose logs -f backend    # 查看后端日志
docker compose down               # 停止服务
docker compose down -v            # 停止并删除数据卷（谨慎，会清空数据）
```

### 方式三：本地开发启动

适合需要频繁修改代码的二次开发场景。

#### 1. 安装依赖

项目使用 `uv` 管理 Python 依赖（如未安装：`pip install uv`）：

```bash
uv sync
```

#### 2. 准备环境变量

```bash
cp backend/app/configs/.env.example backend/app/configs/.env
```

编辑 `backend/app/configs/.env`，重点配置：

- `LLM_PROVIDER`：当前使用 `mimo`，可切换 `deepseek` / `openai`
- `*_API_KEY`：对应 LLM 提供商的 API Key
- `SENTENCE_TRANSFORMER_MODEL`：句向量模型路径或 HuggingFace 模型名

#### 3. 启动本地基础设施

需要本地已安装并启动 PostgreSQL 16、Redis 7、RabbitMQ 3。

#### 4. 执行数据库迁移

```bash
cd backend
../.venv/Scripts/python.exe -m alembic upgrade head
```

> 项目强制使用 `.venv` 中的 Python，禁止直接使用系统 Python。

#### 5. 启动后端

```bash
cd backend
../.venv/Scripts/uvicorn main:app --reload
```

### 启动浏览器插件

```bash
cd extension
npm install
npm run build      # 生产构建，必须执行才会生成 dist/manifest.json
```

构建完成后，检查 `extension/dist/manifest.json` 以及 `icon16.png` / `icon48.png` / `icon128.png` 是否存在。确认存在后，打开 Chrome：

1. 地址栏输入 `chrome://extensions/`
2. 打开右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `extension/dist/` 目录

#### 首次使用：注册并登录

数据库初始化后**没有预置用户**，首次使用需要在 Extension 内注册账号：

1. 点击浏览器右上角扩展图标，或按 Chrome 侧栏快捷键打开 SidePanel。
2. 在登录面板中切换到「注册」，填写邮箱、昵称、密码、确认密码和后端地址（默认 `http://localhost:8000`）。
   - 密码长度 8-64 位，需同时包含字母和数字。
3. 点击「注册并登录」，注册成功后会自动登录并进入主界面。
4. 登录成功后访问 Boss 直聘岗位列表页（`zhipin.com/web/geek/jobs`），SidePanel 会自动提取当前页面岗位。

> 如果习惯用命令行注册，也可以直接调用 `POST /api/auth/register` 接口，再用同一组邮箱密码登录。

开发模式可改用：

```bash
npm run dev        # Vite 开发服务器，实时预览 popup
```

---

## 运行测试

```bash
# 项目根目录
pytest

# 只跑单元测试
pytest -m unit

# 只跑集成测试（需要真实 PG/Redis/RabbitMQ）
pytest -m integration

# 跑单个测试
pytest backend/tests/test_file.py::test_name
```

## 代码检查

```bash
ruff check .
ruff format .
mypy .
```

---

## 常见问题

### 1. 双击 `start.ps1` 提示“无法加载脚本”

Windows 默认 PowerShell 执行策略较严格。解决方法：

- **推荐**：在终端中运行：
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\start.ps1
  ```
- 或临时修改当前会话策略：
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```

### 2. Docker 启动时报端口冲突

检查本地是否已占用以下端口：5432（PostgreSQL）、6379（Redis）、5672/15672（RabbitMQ）、8000（后端）。

```bash
# 查看端口占用（PowerShell）
Get-NetTCPConnection -LocalPort 8000
```

可修改 `docker-compose.yml` 中的端口映射，或停止本地占用服务。

### 3. 后端日志提示 LLM API 调用失败

检查 `backend/app/configs/.env` 中的 API Key 是否有效，以及 `LLM_PROVIDER` 是否与你填写的 Key 对应。例如：

- 使用 DeepSeek：`LLM_PROVIDER=deepseek`，并填写 `DEEPSEEK_API_KEY`
- 使用 OpenAI：`LLM_PROVIDER=openai`，并填写 `OPENAI_API_KEY`
- 使用 Mimo：`LLM_PROVIDER=mimo`，并填写 `MIMO_API_KEY`

### 4. 语义匹配模块启动慢或报错

检查 `SENTENCE_TRANSFORMER_MODEL` 配置：

- 若使用 HuggingFace 模型名（如 `BAAI/bge-small-zh-v1.5`），首次会自动下载，可能较慢
- 若使用本地路径，确保路径存在且模型文件完整
- 临时关闭语义匹配可设置 `SEMANTIC_SCORER_ENABLED=false`

### 5. 数据库表不存在或 Schema 不一致

- **Docker / 一键启动模式**：后端容器启动时会自动执行 `alembic upgrade head`，无需手动迁移。
- **本地开发模式**：需要手动执行迁移：

  ```bash
  cd backend
  ../.venv/Scripts/python.exe -m alembic upgrade head
  ```

如果 Docker 模式下仍遇到表不存在的问题，可在容器运行后手动进入容器再执行一次：

```bash
docker compose exec backend alembic upgrade head
```

### 6. Chrome 加载扩展提示「清单文件缺失或不可读取」

说明 `extension/dist/` 下没有 `manifest.json`。必须先执行生产构建：

```bash
cd extension
npm run build
```

构建完成后应能在 `extension/dist/` 下看到 `manifest.json`。Vite 会把 `extension/public/` 下的文件原样复制到 `extension/dist/`。

### 7. 扩展图标能显示，但点击后弹窗空白

通常是 `dist/index.html` 生成异常。正常应该是包含 `<div id="app"></div>` 的 Vue 入口页；如果看到 `CRXJS DEV MODE` 或加载脚本失败的提示，说明构建插件冲突。

解决办法：重新执行 `npm run build`，确保 `extension/dist/index.html` 是 Vue 入口页，然后在 `chrome://extensions/` 里刷新扩展。

### 8. `.env` 中的密码/Key 是否安全？

`backend/app/configs/.env` 已加入 `.gitignore`，不会被提交。但本地开发使用的弱密码（如 `postgres/postgres`）**严禁用于生产环境**。

### 9. Extension 登录失败或提示「未登录」

- 数据库初始化后没有默认用户，必须先调用 `/api/auth/register` 注册账号（见「首次使用：注册并登录」）。
- access_token 存储在 Service Worker 内存中，刷新页面或 SW 被回收后需要重新登录。
- 检查 SidePanel / Service Worker 的 DevTools 控制台，确认后端地址和 `/api/auth/login` 请求正常。

### 10. 岗位列表为空或点击岗位不分析

- 确保当前页面是 Boss 直聘岗位列表页：`zhipin.com/web/geek/jobs`。
- 在 Boss 页面按 `F12` → Console，搜索 `[AI Career Copilot]` 查看 Content Script 注入和提取日志。
- 若 Boss 页面 DOM 结构改版，可能需要在 `extension/src/modules/boss/selector.ts` 中更新选择器。

### 11. 分析/匹配/话术一直 loading

- 检查 RabbitMQ 是否正常消费任务，后端日志是否有任务入队和出队记录。
- 检查 `backend/app/configs/.env` 中 LLM API Key 是否有效。
- 通过 `GET /api/tasks/{task_id}` 查看任务状态和错误信息。

### 12. 关闭 SidePanel 后岗位列表和状态丢失

- Step 6 已修复状态持久化，现在使用 `chrome.storage.local` 自动保存岗位、分析结果和投递记录。
- 若仍丢失，检查 SidePanel Console 是否有 `[store] 持久化状态失败` 报错，并确认 manifest 包含 `storage` 权限。

更完整的真实 Boss 页面手动联调清单见 [`docs/plans/mvp_extension_completion_plan.md`](./docs/plans/mvp_extension_completion_plan.md)。

---

## 目录结构速览

```
AI Career Copilot/
├── backend/
│   ├── app/
│   │   ├── api/routers/        # FastAPI 路由
│   │   ├── domain/             # 业务逻辑（service/models/validator）
│   │   ├── infra/              # 数据库、缓存、消息队列实现
│   │   ├── core/               # 配置、异常、常量、中间件
│   │   ├── runtime/            # Agent 执行引擎
│   │   ├── tools/              # Agent 可调用的工具
│   │   └── integrations/       # 第三方服务集成
│   ├── migrations/             # Alembic 数据库迁移
│   ├── tests/                  # 测试用例
│   └── main.py                 # 后端入口
├── extension/                  # Chrome 插件（Vue 3 + Vite）
├── docker-compose.yml          # Docker 全栈编排
├── start.ps1                   # Windows 一键启动脚本
├── pyproject.toml              # Python 项目配置
└── README.md                   # 本文档
```

---

## 开发规范

- 所有 IO 操作使用 `async/await`
- 依赖方向：`api → domain → repositories → database`
- 使用 Pydantic 校验输入，禁止 `except: pass`
- 提交格式：`feat(auth): add jwt refresh token support`

更多规则见 [`.trae/rules/project_rules.md`](./.trae/rules/project_rules.md)。
