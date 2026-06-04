# 剪辑 Agent 小规模测试报告 (Langfuse 全维度集成)

> 生成时间: 2026-06-04 17:46 ~ 17:57 (UTC)
> 测试范围: P0/P1/P2 各 2 个用例
> Langfuse 端点: `https://jp.cloud.langfuse.com` (v3.178.0)

---

## 汇总

| 级别 | 总数 | 成功 | 失败 | 总 Token |
|------|------|------|------|----------|
| P0 | 2 | 2 | 0 | 45,283 |
| P1 | 2 | 2 | 0 | 336,413 |
| P2 | 2 | 2 | 0 | 449,427 |
| **合计** | **6** | **6** | **0** | **831,123** |

## 性能概览

| 指标 | 值 |
|------|----|
| 全局总 Token | 831,123 |
| 平均 Token / 用例 | 138,520 |
| 平均耗时 / 用例 | 100.6s |
| 平均工具调用 / 用例 | 8.0 steps |
| Langfuse Trace 拉取成功率 | 6/6 (100%) |
| Trace 拉取平均重试次数 | 1.0 (全部 attempt 1 命中) |

---

## 详细结果

### [OK] P0-01: 用户通过文件名引用视频素材，Agent 自动解析为完整路径

| Token | 后端延迟 | Cost | 执行步骤 | 工具报错率 |
|-------|---------|------|---------|-----------|
| 22,411 | 21.17s | N/A | 1 | 0.00% |

- **总耗时**: 23.27s
- **工具调用**: `resolve_media`
- **Langfuse**: success (attempt 1)

---

### [OK] P0-02: 用户想查看所有可用素材

| Token | 后端延迟 | Cost | 执行步骤 | 工具报错率 |
|-------|---------|------|---------|-----------|
| 22,872 | 30.61s | N/A | 2 | 0.00% |

- **总耗时**: 32.67s
- **工具调用**: `list_media` x2
- **Langfuse**: success (attempt 1)

---

### [OK] P1-01: 悬疑短剧混剪：导入多段素材、自动拼接、在片段间添加叠化转场、铺 BGM

| Token | 后端延迟 | Cost | 执行步骤 | 工具报错率 |
|-------|---------|------|---------|-----------|
| 297,425 | 191.26s | N/A | 15 | 6.67% |

- **总耗时**: 193.38s
- **工具调用**: `resolve_media` x6, `load_skill` x6, `execute_cli_script`, `validate_jyproject_code`, `submit_storyboard`
- **Langfuse**: success (attempt 1)

---

### [OK] P1-02: 悬疑短剧开场：声音先出来制造悬念，画面随后跟上，同步生成字幕

| Token | 后端延迟 | Cost | 执行步骤 | 工具报错率 |
|-------|---------|------|---------|-----------|
| 38,988 | 55.33s | N/A | 3 | 0.00% |

- **总耗时**: 57.42s
- **工具调用**: `resolve_media`, `load_skill`, `execute_jyproject_code`
- **Langfuse**: success (attempt 1)

---

### [OK] P2-01: 用户给出模糊时间描述，考验 Agent 的参数归一化能力

| Token | 后端延迟 | Cost | 执行步骤 | 工具报错率 |
|-------|---------|------|---------|-----------|
| 320,921 | 186.54s | N/A | 20 | 5.00% |

- **总耗时**: 188.61s
- **工具调用**: `resolve_media`, `execute_cli_script` x7, `load_skill` x4, `execute_jyproject_code` x6, `submit_storyboard`
- **Langfuse**: success (attempt 1)

---

### [OK] P2-02: 用户给出超出素材范围的时间

| Token | 后端延迟 | Cost | 执行步骤 | 工具报错率 |
|-------|---------|------|---------|-----------|
| 128,506 | 106.14s | N/A | 7 | 14.29% |

- **总耗时**: 108.24s
- **工具调用**: `resolve_media`, `load_skill` x3, `validate_jyproject_code`, `execute_jyproject_code`, `list_media`
- **Langfuse**: success (attempt 1)

---

## 技术说明

- **Langfuse Trace 关联方式**: 通过 `session_id` = `conversation_id` (API route → `_inject_langfuse_callback` → `propagate_attributes` → OTEL context)
- **Token 数据来源**: `streaming=True` + `model_kwargs={"stream_usage": True}` → MiMo-v2.5-pro 流式最后一个 chunk 的 usage 字段 → LangChain CallbackHandler → Langfuse observation
- **拉取方式**: `requests` → Langfuse REST API (`GET /api/public/traces?session_id=xxx` → `GET /api/public/traces/{trace_id}`)
- **Cost 为 N/A**: MiMo-v2.5-pro 的 pricing 未在 Langfuse 中配置
