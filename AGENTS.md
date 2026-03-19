# Agent 工作指南 (AGENTS.md)

欢迎在 Full-Stack FastAPI (React + Python) 模板仓库中进行开发。本指南为 AI Agent 和开发者提供了核心开发命令、代码规范和架构约定的速查手册。

---

## 1. 项目架构概述
- **Backend**: Python, FastAPI, SQLModel, Alembic, Pytest (位于 `backend/` 目录下)
- **Frontend**: TypeScript, React, Vite, TanStack Query, Playwright (位于 `frontend/` 目录下)
- **基础设施**: Docker Compose (提供本地开发环境及数据库服务)

---

## 2. 核心构建、检查与测试命令

### 🤖 运行单一测试 (最关键操作)
- **后端 (Python/Pytest)**:
  如果 Docker 开发环境已经启动：
  ```bash
  # 运行指定文件中的某个测试函数
  docker compose exec backend bash scripts/tests-start.sh tests/path/to/test_file.py::test_function_name
  ```
- **前端 (Playwright/Vitest)**:
  ```bash
  cd frontend
  bunx playwright test path/to/test.spec.ts
  ```

### 🐍 后端常用命令 (于 `backend/` 目录下执行)
- **依赖管理**: `uv` 或 `pip` (具体读取 `pyproject.toml`)
- **代码检查与格式化 (Ruff & Mypy)**: 
  ```bash
  ruff check . --fix   # 自动修复 Lint 问题
  ruff format .        # 格式化代码
  mypy .               # 运行静态类型检查 (Strict模式)
  ```
- **全量测试**:
  ```bash
  bash ./scripts/test.sh
  # 或者在容器内: docker compose exec backend bash scripts/tests-start.sh
  ```
- **数据库迁移 (Alembic)**:
  ```bash
  docker compose exec backend alembic revision --autogenerate -m "修改说明"
  docker compose exec backend alembic upgrade head
  ```

### ⚛️ 前端常用命令 (于 `frontend/` 目录下执行)
- **依赖安装**: `npm install` (或者 `bun install` 因为有 `bun.lock` 文件存在)
- **代码检查与格式化 (Biome)**:
  ```bash
  npm run lint   # 执行 biome check --write
  ```
- **全量测试**:
  ```bash
  npm run test   # 执行 bunx playwright test
  ```
- **生成 API Client 代码**:
  ```bash
  npm run generate-client  # 根据后端的 OpenAPI schema 生成前端类型
  ```

---

## 3. 代码风格与开发规范

### 🐍 后端代码规范 (Python / FastAPI)
1. **类型提示 (Typing)**:
   - 仓库启用了严格类型检查 (`mypy strict=true`)，必须提供完整的类型注解。
   - 避免使用 `Any`，对于可能为空的字段使用 `类型 | None` (Python 3.10+ 原生语法)。
2. **格式化与 Lint (Ruff)**:
   - 遵循 Ruff 配置：自动排序 imports (`I` 规则)，禁止函数中存在未使用参数 (`ARG001` 规则)。
   - **严禁使用 `print` 语句** (`T201` 规则)，调试或记录信息请使用标准的 `logging` 库。
3. **数据库与数据模型 (SQLModel)**:
   - 统一使用 SQLModel 替代原生 SQLAlchemy 声明。
   - 严格区分基于 API 传输的 Schema 模型和直接映射数据库的 Table 模型 (`table=True`)。
4. **路由与业务逻辑**:
   - 保持路由 (Routers) 函数轻量化，复杂逻辑抽离到业务层 (CRUD 或 Services)。
   - 充分利用 FastAPI 的 `Depends` 进行依赖注入（例如：获取数据库 `Session` 或当前经过验证的用户）。
5. **异常与错误处理**:
   - 遇到已知逻辑错误时，统一抛出 FastAPI 的 `HTTPException`，返回标准 HTTP 状态码（如 400, 404）和易懂的详情信息，而不是直接返回 JSON 数据结构。

### ⚛️ 前端代码规范 (TypeScript / React)
1. **组件化与状态**:
   - 采用函数式组件 (Functional Components) 和 React Hooks 进行开发。
   - 异步数据获取和状态管理需统一使用 TanStack Query (`useQuery`, `useMutation`)，严禁使用原生 `useEffect` 去执行纯粹的 Fetch 请求。
2. **类型与接口 (TypeScript)**:
   - 所有 Props 与组件 state 必须定义清晰的 Interface 或是 Type。
   - API 数据请求及响应类型由 `openapi-ts` 自动生成，请直接导入使用，以保证前后端类型强制同步。
3. **命名规范**:
   - **变量/普通函数**: `camelCase` (例如：`fetchUserData`)
   - **React组件/接口/自定义类型**: `PascalCase` (例如：`UserProfile`, `UserData`)
   - **常量**: 大写下划线 `UPPER_SNAKE_CASE` (例如：`MAX_RETRY_COUNT`)
4. **代码格式化工具 (Biome)**:
   - 遵守本项目的 Biome 配置进行代码风格统一 (`biome.json`)。不要引入或开启任何有关 Prettier / ESLint 的规则。
   - 提交代码或结束文件编辑前，请务必运行 `npm run lint` 修复格式。
5. **UI 异常与错误处理**:
   - 渲染层的错误使用 React Error Boundaries 进行隔离捕获。
   - API 请求失败时，利用 TanStack Query 的回调 (例如：`onError`) 或 Axios/Fetch 响应拦截器统一弹出全局 Toast 错误提示，禁止吞噬报错日志。

---

## 4. Agent 协作准则 (Agent Guidelines)

- **避免凭空假设**: 绝不假设某个包已被安装，除非你在 `pyproject.toml` 或 `package.json` 中明确查证。引入新依赖前请使用 `read` / `bash` / `grep` 工具进行环境确认。
- **绝对路径要求**: 当你需要执行文件读写操作时，务必基于本项目的根目录提供**完整且正确的绝对路径**。
- **渐进式验证循环**: 在修改或生成大量代码后，必须主动运行测试 (Pytest/Playwright) 以及 Lint 工具 (Ruff/Biome)。若出现错误，请自行捕获报错输出并修复，形成闭环。
- **安全第一**: 绝对不要在日志或提交中输出环境变量(`.env`)、密码或其他敏感数据。
- **尊重项目惯例**: 新建文件时，仔细研究同级目录下的其他文件，以相同的代码结构和缩进风格进行仿写。不要使用与仓库其他部分格格不入的架构模式。