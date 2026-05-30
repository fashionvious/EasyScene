# 视频剪辑 Agent 迭代需求文档

> 基于目标架构参考文档（Hermes Agent）与当前项目代码的差异分析

---

## 一、当前功能现状评估

### 1.1 已实现的核心模块

| 模块 | 文件 | 功能描述 |
|------|------|----------|
| **主Agent** | `jianying_agent.py` | 基于LangChain的Agent，使用`create_agent`创建，注入技能中间件 |
| **CLI执行器** | `cli_executor.py` | 执行预定义脚本（asset_search, auto_exporter, draft_inspector等），支持超时控制 |
| **Python执行器** | `python_executor.py` | 执行LLM生成的JyProject编排代码，通过subprocess运行 |
| **媒体解析器** | `media_resolver.py` | 根据文件名在搜索路径中查找媒体文件 |
| **Skill解析器** | `skill_parser.py` | 解析jianying-editor-skill目录，生成Skill对象 |
| **外部工具调用** | 多个脚本 | FFmpeg（media_normalizer）、TTS（universal_tts）、AI视频分析（smart_rough_cut） |

### 1.2 现有架构模式

- **Agent框架**：LangChain `create_agent` + `AgentMiddleware`
- **检查点**：`InMemorySaver`（内存检查点）
- **工具注册**：通过`@tool`装饰器注册LangChain工具
- **外部调用**：`subprocess.run` 执行CLI命令和Python脚本

---

## 二、核心架构差距分析

### 2.1 致命缺陷 1：缺乏状态管理与重试机制

**现状**：
- CLI执行器（`cli_executor.py`）和Python执行器（`python_executor.py`）仅有基础的超时处理
- 外部工具调用（FFmpeg、TTS）失败后没有自动重试机制
- 任务执行失败后直接返回错误，没有降级或补偿策略

**对标Hermes架构**：
- Hermes提供`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT=3`重试机制
- 支持`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`连续失败上限
- 通过`AbortController`支持外部中断

**影响**：
- 网络抖动或临时性错误导致整个任务失败
- 用户体验差，需要手动重试

### 2.2 致命缺陷 2：上下文管理可能导致Token爆炸

**现状**：
- `JianYingSkillMiddleware.wrap_model_call` 将所有技能描述、媒体文件列表直接注入系统消息
- 媒体文件列表最多显示10个（`available_files[:10]`），但技能描述没有限制
- 每次调用都注入完整上下文，没有渐进式披露机制

**对标Hermes架构**：
- Hermes提供`autoCompact`机制，当token接近阈值时自动压缩
- `AUTOCOMPACT_BUFFER_TOKENS = 13,000` 预留缓冲
- 支持`compactConversation()`生成摘要替换历史消息

**影响**：
- 长对话场景下Token消耗指数增长
- 可能触发API的token上限错误

### 2.3 致命缺陷 3：外部工具调用缺乏健壮性

**现状**：
- `media_normalizer.py`中的FFmpeg调用没有超时控制
- `subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)`没有设置`timeout`参数
- FFmpeg输出没有截断，可能产生海量日志

**对标Hermes架构**：
- Hermes提供`DEFAULT_TIMEOUT_MS = 120_000`（2分钟）默认超时
- `MAX_TIMEOUT_MS = 600_000`（10分钟）最大超时
- 工具结果超过阈值时自动写入磁盘文件，返回引用路径

**影响**：
- FFmpeg卡死导致Agent永久挂起
- 大视频处理时内存溢出

---

## 三、迭代修改需求列表

### 需求 1：为外部工具调用增加超时控制与输出截断机制

**优先级**：P0（最高）

**涉及文件**：
- `jianying-editor-skill/scripts/utils/media_normalizer.py`
- `skills_agent/cli_executor.py`
- `skills_agent/python_executor.py`

**验收标准**：
1. 所有`subprocess.run`调用必须设置`timeout`参数（默认120秒，最大600秒）
2. 超时后返回结构化错误信息：`{"success": false, "error": "timeout", "timeout_seconds": 120}`
3. stdout/stderr超过10KB时自动截断，保留首尾各2KB，中间用`...[truncated]...`标记
4. 提供`on_timeout`回调机制，支持超时后自动转后台或终止进程

**示例改造**（media_normalizer.py）：
```python
# 改造前
proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

# 改造后
proc = subprocess.run(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    timeout=120,  # 新增：超时控制
)
```

---

### 需求 2：为任务执行增加重试机制与降级策略

**优先级**：P0

**涉及文件**：
- `skills_agent/cli_executor.py`
- `skills_agent/python_executor.py`
- `jianying-editor-skill/scripts/universal_tts.py`（已有重试，需统一规范）

**验收标准**：
1. 定义`RetryConfig`数据类：
   ```python
   @dataclass
   class RetryConfig:
       max_retries: int = 3
       base_delay: float = 1.0  # 秒
       max_delay: float = 30.0
       retryable_errors: tuple = (TimeoutError, ConnectionError, subprocess.TimeoutExpired)
   ```
2. 实现`@with_retry`装饰器，支持指数退避重试
3. CLI执行器和Python执行器使用统一的重试配置
4. 连续失败次数超过阈值后触发降级（如返回缓存结果或默认值）

---

### 需求 3：实现Token感知的上下文注入机制

**优先级**：P1

**涉及文件**：
- `skills_agent/jianying_agent.py`（`JianYingSkillMiddleware`类）

**验收标准**：
1. 引入`tiktoken`库进行token计数
2. 实现`calculate_context_budget`函数，根据模型上下文窗口计算可用预算
3. 技能描述和媒体文件列表注入前进行token估算：
   - 如果总token超过预算的80%，触发渐进式截断
   - 优先保留核心技能，示例类技能可省略
4. 媒体文件列表从固定10个改为基于token预算动态调整

**实现要点**：
```python
def calculate_context_budget(model_context_window: int, reserved_for_response: int = 4096) -> int:
    """计算可用于注入的token预算"""
    return model_context_window - reserved_for_response

def truncate_skills_by_budget(skills: list[Skill], budget: int) -> str:
    """基于token预算截断技能描述"""
    # 按优先级排序：main > rule > script > example
    # 逐个添加直到达到预算上限
```

---

### 需求 4：为Python执行器增加输出截断与结果摘要机制

**优先级**：P1

**涉及文件**：
- `skills_agent/python_executor.py`

**验收标准**：
1. 定义`MAX_OUTPUT_BYTES = 10240`（10KB）输出上限
2. 执行结果超过上限时：
   - 完整输出写入临时文件
   - 返回摘要格式：`"执行成功，输出已截断。完整输出: /path/to/output.log (15KB)"`
3. 实现`summarize_output`函数，提取关键信息（如错误行、警告行）

---

### 需求 5：集成Langfuse可观测性埋点

**优先级**：P2

**涉及文件**：
- `skills_agent/jianying_agent.py`

**验收标准**：
1. 使用Langfuse Python SDK v2+原生API（**禁止使用已废弃的`langfuse.decorators`**）
2. 为每次Agent调用创建trace，记录：
   - 输入消息
   - 工具调用链
   - Token消耗
   - 执行耗时
3. 为外部工具调用创建span，记录：
   - 工具名称
   - 输入参数
   - 执行结果（截断后）
   - 是否超时/失败

**Langfuse集成代码骨架**：
```python
from langfuse import Langfuse

langfuse = Langfuse()

def create_agent_trace(user_message: str) -> str:
    """创建Agent执行trace"""
    trace = langfuse.trace(
        name="jianying_agent",
        input=user_message,
        metadata={"model": "qwen3.6-plus"}
    )
    return trace.id

def create_tool_span(trace_id: str, tool_name: str, input_data: dict) -> str:
    """创建工具调用span"""
    span = langfuse.span(
        trace_id=trace_id,
        name=tool_name,
        input=input_data
    )
    return span.id
```

---

### 需求 6：为FFmpeg调用增加进程组管理与优雅终止

**优先级**：P2

**涉及文件**：
- `jianying-editor-skill/scripts/utils/media_normalizer.py`

**验收标准**：
1. 使用`subprocess.Popen`替代`subprocess.run`，支持进程组管理
2. 超时时发送SIGTERM，等待5秒后发送SIGKILL
3. 清理临时文件（输出不完整的中间文件）

**实现要点**：
```python
import signal
import os

def run_ffmpeg_with_timeout(cmd: list, timeout: int = 120) -> dict:
    """运行FFmpeg，支持超时和优雅终止"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid  # 创建新进程组
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {"success": proc.returncode == 0, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired:
        # 先尝试SIGTERM
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # 强制终止
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
        return {"success": False, "error": "timeout", "timeout_seconds": timeout}
```

---

## 四、LangGraph 落地伪代码示例

> **最复杂需求**：需求2（重试机制）的LangGraph实现示例

### 4.1 设计思路

将重试逻辑封装为LangGraph的节点条件边，实现：
1. 工具调用失败时自动重试
2. 指数退避延迟
3. 超过最大重试次数后进入降级分支

### 4.2 代码骨架

```python
"""
LangGraph 重试机制实现示例
基于 StateGraph + 条件边实现工具调用的自动重试
"""
from typing import TypedDict, Annotated, Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
import asyncio
import time


# ========== 1. 定义状态 ==========
class AgentState(TypedDict):
    """Agent 状态定义"""
    messages: Annotated[list, add_messages]
    current_tool: str
    tool_input: dict
    tool_output: str | None
    retry_count: int
    max_retries: int
    last_error: str | None


# ========== 2. 重试配置 ==========
RETRY_CONFIG = {
    "max_retries": 3,
    "base_delay": 1.0,  # 秒
    "max_delay": 30.0,
}


def calculate_backoff(retry_count: int, base_delay: float = 1.0, max_delay: float = 30.0) -> float:
    """计算指数退避延迟时间"""
    delay = base_delay * (2 ** retry_count)
    return min(delay, max_delay)


# ========== 3. 定义节点 ==========
async def call_tool(state: AgentState) -> AgentState:
    """
    调用工具节点
    
    这里模拟工具调用，实际应该调用真实的工具函数
    """
    tool_name = state["current_tool"]
    tool_input = state["tool_input"]
    
    try:
        # 模拟工具调用（实际应替换为真实调用）
        if tool_name == "execute_cli_script":
            # result = executor.execute(tool_input["script_name"], tool_input.get("args", {}))
            result = {"success": True, "output": "模拟输出"}
        elif tool_name == "execute_jyproject_code":
            # result = executor.execute(tool_input["code"])
            result = {"success": True, "output": "项目创建成功"}
        else:
            result = {"success": False, "error": f"未知工具: {tool_name}"}
        
        if result["success"]:
            return {
                "tool_output": result.get("output", ""),
                "last_error": None,
            }
        else:
            return {
                "tool_output": None,
                "last_error": result.get("error", "未知错误"),
            }
    except Exception as e:
        return {
            "tool_output": None,
            "last_error": str(e),
        }


async def wait_and_retry(state: AgentState) -> AgentState:
    """
    等待并重试节点
    
    计算退避延迟，增加重试计数
    """
    retry_count = state["retry_count"]
    delay = calculate_backoff(retry_count, RETRY_CONFIG["base_delay"], RETRY_CONFIG["max_delay"])
    
    print(f"[重试] 第 {retry_count + 1} 次重试，等待 {delay:.1f} 秒...")
    await asyncio.sleep(delay)
    
    return {
        "retry_count": retry_count + 1,
    }


async def fallback_handler(state: AgentState) -> AgentState:
    """
    降级处理节点
    
    重试耗尽后的降级策略
    """
    error = state["last_error"]
    tool_name = state["current_tool"]
    
    # 降级策略：返回缓存结果或默认值
    fallback_output = f"工具 {tool_name} 执行失败（已重试 {state['retry_count']} 次）: {error}。已启用降级策略。"
    
    return {
        "tool_output": fallback_output,
    }


# ========== 4. 定义条件边 ==========
def should_retry(state: AgentState) -> Literal["wait_and_retry", "fallback", "success"]:
    """
    判断是否应该重试
    
    条件分支逻辑：
    - 如果工具调用成功 -> success
    - 如果还有重试次数 -> wait_and_retry
    - 否则 -> fallback
    """
    if state["tool_output"] is not None:
        return "success"
    
    if state["retry_count"] < state["max_retries"]:
        return "wait_and_retry"
    
    return "fallback"


# ========== 5. 构建图 ==========
def build_retry_graph() -> StateGraph:
    """
    构建带重试机制的 LangGraph
    
    图结构：
    call_tool -> 条件判断 -> success (END)
                          -> wait_and_retry -> call_tool (循环)
                          -> fallback (END)
    """
    graph = StateGraph(AgentState)
    
    # 添加节点
    graph.add_node("call_tool", call_tool)
    graph.add_node("wait_and_retry", wait_and_retry)
    graph.add_node("fallback", fallback_handler)
    
    # 设置入口
    graph.set_entry_point("call_tool")
    
    # 添加条件边
    graph.add_conditional_edges(
        "call_tool",
        should_retry,
        {
            "success": END,
            "wait_and_retry": "wait_and_retry",
            "fallback": "fallback",
        }
    )
    
    # 重试后回到工具调用
    graph.add_edge("wait_and_retry", "call_tool")
    
    # 降级后结束
    graph.add_edge("fallback", END)
    
    return graph


# ========== 6. 使用示例 ==========
async def run_with_retry(tool_name: str, tool_input: dict, max_retries: int = 3) -> str:
    """
    带重试机制的工具调用入口
    
    Args:
        tool_name: 工具名称
        tool_input: 工具输入参数
        max_retries: 最大重试次数
        
    Returns:
        工具执行结果
    """
    # 构建图
    graph = build_retry_graph()
    app = graph.compile()
    
    # 初始状态
    initial_state: AgentState = {
        "messages": [],
        "current_tool": tool_name,
        "tool_input": tool_input,
        "tool_output": None,
        "retry_count": 0,
        "max_retries": max_retries,
        "last_error": None,
    }
    
    # 执行
    final_state = await app.ainvoke(initial_state)
    return final_state["tool_output"]


# ========== 7. 测试 ==========
if __name__ == "__main__":
    async def main():
        result = await run_with_retry(
            tool_name="execute_cli_script",
            tool_input={"script_name": "asset_search", "args": {"query": "复古"}},
            max_retries=3
        )
        print(f"最终结果: {result}")
    
    asyncio.run(main())
```

### 4.3 集成说明

1. **安装依赖**：
   ```bash
   pip install langgraph langchain-core
   ```

2. **与现有代码集成**：
   - 将`call_tool`节点替换为真实的工具调用逻辑
   - 将`run_with_retry`作为`JianYingSkillMiddleware`的工具执行入口
   - 在`jianying_agent.py`中导入并使用

3. **扩展建议**：
   - 添加`RetryConfig`参数化配置
   - 支持不同工具的差异化重试策略
   - 集成Langfuse trace记录重试过程

---

## 附录：改造优先级建议

| 优先级 | 需求 | 预计工时 | 风险 |
|--------|------|----------|------|
| P0 | 需求1：超时控制与输出截断 | 2天 | 低 |
| P0 | 需求2：重试机制 | 3天 | 中 |
| P1 | 需求3：Token感知上下文 | 2天 | 中 |
| P1 | 需求4：输出截断摘要 | 1天 | 低 |
| P2 | 需求5：Langfuse埋点 | 2天 | 低 |
| P2 | 需求6：进程组管理 | 1天 | 低 |

**总计**：约11个工作日
