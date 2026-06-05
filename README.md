# EasyScene

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

AI 驱动的全栈智能视频生成平台 —— 从剧本到成片，一句话即可完成。

---

## 项目简介

**EasyScene** 是一个端到端的 AI 视频自动化创作平台。它接收用户的文字剧本 / 故事脚本，通过多阶段 AI Agent 协作流水线，自动完成角色提取、场景设计、分镜头生成、图片生成和视频合成等全链路工作，最终输出可直接使用的视频内容。

与传统视频剪辑工具不同，EasyScene 将 LLM（大语言模型）、文生图模型、文生视频模型与自动化编辑引擎（剪映）深度编排在一起，让创作者无需学习复杂的剪辑软件，也无需手动协调多个 AI 工具，即可快速将创意转化为成片。

**解决的核心痛点：**

- **碎片化工作流**：无需在剧本撰写、角色设计、分镜策划、图片生成、视频生成之间来回切换多个工具
- **AI 工具编排复杂**：内置 LangGraph 多 Agent 协作引擎，自动串联调用链路
- **非技术人员门槛高**：提供 Web 前端界面，文本输入即可驱动全流程
- **批量化生产效率低**：基于 Celery 异步任务队列，支持并行批量生成

---

## 核心特性

### AI 视频生成流水线
- **剧本解析**：自动从原始文本中提取角色信息、场景描述和叙事结构
- **角色生成**：基于 LLM 提取角色属性，利用文生图模型生成角色四视图（正面 / 侧面 / 背面 / 3D）
- **场景图生成**：为每个场景组自动创建背景场景图
- **分镜头脚本生成**：AI 将剧本细化为包含拍摄指令的分镜头脚本
- **九宫格 / 首尾帧生成**：为每个分镜生成九宫格参考图、首帧图和尾帧图
- **视频生成**：基于 Seedance / 通义万相模型将分镜头图片转化为动态视频

### 智能编辑引擎（剪映集成）
- 基于 Skill 架构的剪映自动化编辑 Agent
- 声明式 Skill 目录结构（SKILL.md + Rules + Scripts + Examples），渐进式披露给 LLM
- 支持 WebSocket 实时推送编辑任务进度
- 步骤级编排：创建 / 暂停 / 恢复 / 取消 / 重试 / 跳过单步

### 对话式 AI 助手
- 每个项目内嵌对话面板，与 AI 实时讨论创意和修改方向
- 对话历史持久化存储，支持跨会话上下文

### 项目与协作管理
- 用户认证与权限（JWT + bcrypt）
- 剧本的多版本管理与软删除
- 操作日志记录（创建 / 修改 / AI 生成 / 删除 / 恢复 / 权限变更）
- 用户偏好设置（分辨率、帧率、默认配音等）

### 热点内容采集
- Bilibili 热点视频数据采集与分析

---

## 架构与技术栈

### 整体架构

```
┌─────────────────────────┐       ┌──────────────────────────────┐
│     Frontend (React)     │       │         Backend (FastAPI)      │
│  React 19 + TypeScript  │ HTTP  │  REST API + WebSocket         │
│  TanStack Router/Query  │◄─────►│  AI Agent Pipeline            │
│  Tailwind CSS 4 + Radix │       │  LangGraph Orchestration      │
│  Playwright E2E         │       │  Celery Async Tasks           │
└─────────────────────────┘       └──────────┬───────────────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    │                        │                        │
             ┌──────▼──────┐          ┌──────▼──────┐          ┌─────▼─────┐
             │  PostgreSQL  │          │    Redis     │          │  Traefik  │
             │  (数据持久化) │          │ (缓存/消息)  │          │ (反向代理) │
             └─────────────┘          └─────────────┘          └───────────┘
```

### 后端技术栈

| 类别               | 技术                                                              |
| ------------------ | ----------------------------------------------------------------- |
| **Web 框架**       | FastAPI 0.115 + Uvicorn + Starlette                               |
| **ORM / 数据库**   | SQLModel + SQLAlchemy 2.0 + PostgreSQL 17 + Alembic (Migration)   |
| **AI 编排**        | LangChain 1.2 + LangGraph 1.1                                     |
| **LLM / 模型服务** | OpenAI SDK (兼容多厂商) + DashScope (阿里通义) + Volcengine Ark (火山引擎) |
| **异步任务**       | Celery 5.6 + Redis 7                                              |
| **认证**           | JWT (PyJWT) + Passlib + bcrypt                                    |
| **监控与可观测**   | Sentry SDK + Langfuse (LLM Tracing)                               |
| **自动化编辑**     | Playwright + uiautomation + pynput (剪映桌面自动化)               |
| **TTS**            | edge-tts                                                          |
| **图像处理**       | OpenCV + NumPy + imageio                                          |
| **包管理**         | uv (Python) / Hatchling (Build)                                   |
| **代码质量**       | Ruff (Lint) + Mypy (Type Check) + Pytest (Test) + Coverage       |

### 前端技术栈

| 类别             | 技术                                       |
| ---------------- | ------------------------------------------ |
| **框架**         | React 19 + TypeScript 5.9                  |
| **构建工具**     | Vite 7 + SWC                               |
| **路由 / 状态**  | TanStack Router + TanStack Query           |
| **UI 组件**      | Radix UI + Tailwind CSS 4 + shadcn/ui 风格 |
| **表单**         | React Hook Form + Zod 4                    |
| **图标**         | Lucide React + React Icons                 |
| **通知**         | Sonner                                     |
| **API 客户端**   | Axios + @hey-api/openapi-ts (自动生成)     |
| **E2E 测试**     | Playwright 1.57                            |
| **代码质量**     | Biome (Lint & Format)                      |
| **主题**         | next-themes (Dark / Light)                 |

### 核心数据流（文生视频工作流）

```
用户输入剧本
    │
    ▼
角色提取 Agent (LLM) ────────► 角色列表 + 角色描述
    │
    ▼
角色四视图 Agent (文生图) ───► 角色四视图图片
    │
    ▼
场景图 Agent (文生图) ───────► 各场景背景图
    │
    ▼
分镜头脚本 Agent (LLM) ─────► 分镜头列表 + 拍摄指令
    │
    ▼
九宫格 / 首帧 / 尾帧生成 ───► 分镜参考图
    │
    ▼
视频生成 Agent (文生视频) ──► 成片视频
    │
    ▼
剪映编辑 Agent (可选) ─────► 精剪合成输出
```

整个流程通过 Redis 状态管理器追踪每个项目的推进阶段，支持断点续跑和失败重试。

---

## 本地开发指南

> **说明**：此部分内容将由开发者手动补充说明本地环境配置、运行命令及部署流程。以下为关键参考点：
>
> - 后端：Python 3.10+，使用 `uv sync` 安装依赖，`fastapi run --reload` 启动开发服务器
> - 前端：Bun / Node.js，使用 `bun install && bun run dev` 启动 Vite 开发服务器
> - 基础设施：`docker compose up -d` 启动 PostgreSQL、Redis、Adminer 等服务
> - 环境变量：参考 `.env` 文件配置，包含数据库连接、AI API Key、Sentry DSN 等
>
> **详细步骤请由开发者手动补充。**

---

## 可观测性与调试

### Sentry 错误监控

项目集成了 Sentry SDK，在非本地环境下自动启用。配置环境变量 `SENTRY_DSN` 即可将运行时错误和性能追踪数据上报到 Sentry。

### Langfuse LLM 可观测性

集成 [Langfuse](https://langfuse.com/) 用于追踪 LLM 调用链路的 Trace 记录，包括 Token 消耗、延迟、Prompt 内容等。使用方式：

```bash
cd backend
python test_langfuse_connectivity.py
```

该诊断脚本会验证 Langfuse 鉴权、Trace 拉取和指标提取的全链路。需要配置以下环境变量：

- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_BASE_URL`（默认 `https://cloud.langfuse.com`）

### 结构化日志

HTTP 请求日志通过 `uvicorn.error` logger 输出，包含脱敏后的 Headers 和 Body 摘要（截断长文本、隐藏 API Key 等敏感字段），详细实现见 [backend/app/agent/core/integrations/http_logging.py](backend/app/agent/core/integrations/http_logging.py)。

### 任务追踪与调试

- **Celery 任务**：可通过 Celery Flower 或直接查看 Redis 队列监控异步视频生成任务状态
- **Redis 状态管理器**：每个视频项目的推进阶段通过 Redis Key 追踪，key 格式以 script_id 为维度，支持实时查询各阶段完成状态
- **WebSocket 编辑进度**：编辑任务通过 WebSocket 实时推送步骤级进度更新（`/edit` 路由）
- **数据库操作日志**：`operation_log` 表记录所有关键操作（创建 / 修改 / AI 生成 / 删除 / 恢复 / 权限变更 / 版本切换），可按 target_type + target_id 检索任意实体的完整操作历史

---

## 贡献指南

欢迎社区贡献！请遵循以下流程：

### 提交 Issue

1. 在 [Issues 页面](https://github.com/your-org/EasyScene/issues) 提交问题
2. 使用清晰的标题描述问题或功能请求
3. 对于 Bug 报告，请附上：复现步骤、期望行为、实际行为、运行环境（Python 版本 / OS / 依赖版本）
4. 对于功能请求，请描述使用场景和预期效果

### 提交 Pull Request

1. Fork 本仓库并创建功能分支：`git checkout -b feat/your-feature`
2. 确保代码通过现有测试：
   - 后端：`bash ./scripts/test.sh`
   - 前端：`bun test`
3. 为新功能编写测试用例
4. 确保 Lint 通过：
   - 后端：`ruff check`
   - 前端：`bun lint`
5. 提交 Commit 并推送分支
6. 创建 Pull Request，描述变更内容和动机

### 代码风格

- **后端**：遵循 Ruff 规则集（pycodestyle + pyflakes + isort + bugbear + pyupgrade），使用 Mypy strict 模式
- **前端**：使用 Biome 进行代码格式化和 Lint
- Commit 消息请使用清晰的中文或英文描述

---

## 开源协议

本项目基于 [MIT License](./LICENSE) 开源。

---

## 致谢

本项目后端脚手架基于 [FastAPI Full Stack Template](https://github.com/fastapi/full-stack-fastapi-template) 构建。

以下 AI 模型与服务提供商使本项目成为可能：

- [阿里通义千问 / 通义万相](https://tongyi.aliyun.com/) —— 文生图、文生视频模型
- [火山引擎 Ark](https://www.volcengine.com/) —— Seedance 视频生成模型
- [剪映](https://www.capcut.cn/) —— 桌面端自动化编辑引擎
