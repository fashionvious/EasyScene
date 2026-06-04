"""
VideoAgent API 接口
提供视频剪辑 AI 对话功能，调用 jianying_agent 处理用户输入并返回 AI 生成信息。
使用 LangGraph astream_events 实时推送结构化事件，支持展示思考过程和工具调用步骤。
同时提供会话管理接口，支持聊天消息持久化到数据库。
"""
import os
import uuid
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import CurrentUser, SessionDep
from app.core.db import engine
from app import crud
from app.models import (
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    ConversationPublic,
    ChatMessage,
    ChatMessageCreate,
    ChatMessagePublic,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["videoagent"])


def _is_recursion_limit_error(exc: Exception) -> bool:
    """判断异常是否为 LangGraph 递归限制。"""
    msg = str(exc)
    return (
        "Recursion limit" in msg
        or "recursion_limit" in msg
        or "GRAPH_RECURSION_LIMIT" in msg
    )


async def _launch_edit_task_async(
    storyboard: dict, user_id: str, script_id: str,
) -> str:
    """启动编辑任务（异步 wrapper，供 SSE 生成器使用）。"""
    from app.agent.skills_agent.orchestrator_factory import launch_edit_task
    return await launch_edit_task(storyboard, user_id, script_id)


def _get_recursion_limit(user_message: str) -> int:
    """
    根据用户消息决定 recursion_limit。

    - "继续" / "continue" → 扩大为 80（用户确认需要更多步骤）
    - 默认 → 40
    """
    msg_lower = user_message.strip().lower()
    if msg_lower in ("继续", "continue", "继续执行", "go on", "yes", "是"):
        logger.info("[VideoAgent] 用户确认继续，recursion_limit 扩大至 80")
        return 80
    return 40

# ==================== Agent 单例管理 ====================

_agent_instance = None
_middleware_instance = None


def _get_skill_root() -> str:
    """获取 jianying-editor-skill 根目录路径"""
    env_root = os.getenv("JY_SKILL_ROOT", "").strip()
    if env_root and os.path.exists(os.path.join(env_root, "SKILL.md")):
        return env_root

    current_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill"),
        os.path.join(current_dir, "..", "..", "..", "..", "jianying-editor-skill"),
    ]
    for p in candidates:
        abs_p = os.path.abspath(p)
        if os.path.exists(os.path.join(abs_p, "SKILL.md")):
            return abs_p

    return os.path.abspath(
        os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill")
    )


def _inject_langfuse_callback(config: dict, conversation_id: str) -> None:
    """向 LangGraph config 注入 Langfuse CallbackHandler + trace 属性。

    将 conversation_id 作为 Langfuse session_id：
    - start_langfuse_trace_context() 在 OTEL context 上设置 session_id/tags/trace_name
    - create_langchain_callback() 创建 CallbackHandler（在新 API 下只需 public_key）

    OTEL context MUST 在整个 Agent 调用期间保持活跃，因此存入 config["_lf_ctx"]
    供调用方在 Agent 完成后退出。
    """
    try:
        from app.agent.skills_agent.observability import (
            start_langfuse_trace_context,
            create_langchain_callback,
        )
    except Exception as e:
        logger.warning("[Langfuse] import 失败: %s", e)
        return

    try:
        lf_ctx = start_langfuse_trace_context(
            trace_name="jianying_agent",
            session_id=conversation_id,
            tags=["easy-scene", "video-editing"],
        )
        if lf_ctx is None:
            logger.warning("[Langfuse] trace context 创建失败 (客户端未配置?)")
            return

        # 进入 OTEL context（设置 session_id/tags/trace_name 为 baggage）
        lf_ctx.__enter__()
        config["_lf_ctx"] = lf_ctx

        handler = create_langchain_callback()
        if handler:
            config["callbacks"] = [handler]
            logger.info("[Langfuse] callback 注入成功, session_id=%s", conversation_id[:8])
        else:
            lf_ctx.__exit__(None, None, None)
            config.pop("_lf_ctx", None)
            logger.warning("[Langfuse] handler 创建失败")
    except Exception as e:
        logger.warning("[Langfuse] 注入失败: %s", e)


def _get_or_create_agent():
    """获取或创建 JianYing Agent 单例"""
    global _agent_instance, _middleware_instance

    if _agent_instance is not None:
        return _agent_instance, _middleware_instance

    from app.agent.skills_agent import create_jianying_agent

    skill_root = _get_skill_root()
    logger.info(f"[VideoAgent] Skill Root: {skill_root}")

    media_search_paths = []
    video_dir = os.path.join(skill_root, "video")
    if os.path.isdir(video_dir):
        media_search_paths.append(video_dir)

    agent, middleware = create_jianying_agent(
        skill_root=skill_root,
        media_search_paths=media_search_paths,
    )

    _agent_instance = agent
    _middleware_instance = middleware
    # 注册全局单例（供 orchestrator_factory 获取 ToolRegistry）
    from app.agent.skills_agent import set_middleware_instance
    set_middleware_instance(middleware)
    return agent, middleware


# ==================== 请求/响应模型 ====================


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=2000, description="用户消息")
    script_id: str = Field(..., description="剧本 ID")
    conversation_id: Optional[str] = Field(None, description="会话 ID，为空则新建")


class ChatResponse(BaseModel):
    """聊天响应"""
    conversation_id: str = Field(..., description="会话 ID")
    message: str = Field(..., description="AI 回复内容")


# ==================== SSE 事件类型 ====================
# 借鉴 Claude Code 的 agent 输出模式，定义结构化事件类型：
# - thinking: AI 思考过程（流式文本）
# - text: AI 回复文本（流式文本）
# - tool_call: 工具调用开始（工具名 + 参数）
# - tool_result: 工具调用结果
# - done: 完成


def _make_sse(event_type: str, data: dict) -> str:
    """构造 SSE 事件字符串"""
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_text_content(content) -> str:
    """从消息 content 中提取纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content) if content else ""


def _try_extract_storyboard(text: str) -> dict | None:
    """从 AI 回复中尝试提取分镜 JSON 方案。
    返回 dict 如果找到，否则 None。
    """
    import re
    # 匹配 ```json ... ``` 代码块中的 JSON
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?"steps"[\s\S]*?\})\s*```', text)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and "steps" in data:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
    # 匹配裸 JSON（无条件代码块的）
    try:
        # 找第一个 { 开始、最后 } 结束的大 JSON
        start = text.find('{"project_name"')
        if start == -1:
            start = text.find('{"steps"')
        if start >= 0:
            depth = 0
            end = -1
            for i in range(start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > start:
                data = json.loads(text[start:end])
                if isinstance(data, dict) and "steps" in data:
                    return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


# ==================== 会话管理辅助函数 ====================


def _ensure_conversation(
    conversation_id: str,
    user_id: uuid.UUID,
    script_id: str,
) -> None:
    """确保会话存在于数据库中，不存在则创建"""
    with Session(engine) as session:
        conv = session.get(Conversation, uuid.UUID(conversation_id))
        if conv is None:
            conv_in = ConversationCreate(
                user_id=user_id,
                script_id=uuid.UUID(script_id),
            )
            conv = Conversation.model_validate(conv_in, update={"id": uuid.UUID(conversation_id)})
            session.add(conv)
            session.commit()


def _save_message(
    conversation_id: str,
    role: str,
    content: str,
    tool_steps_json: str | None = None,
) -> None:
    """保存一条聊天消息到数据库"""
    with Session(engine) as session:
        msg_in = ChatMessageCreate(
            conversation_id=uuid.UUID(conversation_id),
            role=role,
            content=content,
        )
        crud.create_chat_message(session=session, msg_in=msg_in, tool_steps_json=tool_steps_json)


def _update_conversation_title(
    conversation_id: str,
    title: str,
) -> None:
    """更新会话标题"""
    with Session(engine) as session:
        conv = session.get(Conversation, uuid.UUID(conversation_id))
        if conv and not conv.title_set:
            conv_update = ConversationUpdate(title=title, title_set=True)
            crud.update_conversation(session=session, db_conv=conv, conv_in=conv_update)


def _update_conversation_timestamp(
    conversation_id: str,
) -> None:
    """更新会话的 update_time"""
    with Session(engine) as session:
        conv = session.get(Conversation, uuid.UUID(conversation_id))
        if conv:
            conv.update_time = datetime.utcnow()
            session.add(conv)
            session.commit()


# ==================== 会话管理 API 端点 ====================


@router.get("/videoagent/conversations", response_model=list[ConversationPublic])
def list_conversations(
    script_id: str,
    current_user: CurrentUser,
    session: SessionDep,
):
    """获取当前用户在指定剧本下的所有会话"""
    conversations = crud.get_conversations_by_user_and_script(
        session=session,
        user_id=current_user.id,
        script_id=uuid.UUID(script_id),
    )
    return conversations


@router.get("/videoagent/conversations/{conversation_id}/messages", response_model=list[ChatMessagePublic])
def get_conversation_messages(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """获取指定会话的所有消息"""
    conv = crud.get_conversation(session=session, conversation_id=conversation_id)
    if not conv or conv.is_deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问此会话")
    messages = crud.get_chat_messages_by_conversation(
        session=session,
        conversation_id=conversation_id,
    )
    return messages


@router.patch("/videoagent/conversations/{conversation_id}", response_model=ConversationPublic)
def update_conversation(
    conversation_id: uuid.UUID,
    conv_update: ConversationUpdate,
    current_user: CurrentUser,
    session: SessionDep,
):
    """更新会话（标题等）"""
    conv = crud.get_conversation(session=session, conversation_id=conversation_id)
    if not conv or conv.is_deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问此会话")
    updated = crud.update_conversation(session=session, db_conv=conv, conv_in=conv_update)
    return updated


# ==================== 聊天 API 端点 ====================


@router.post("/videoagent/chat", response_model=ChatResponse)
async def api_chat(
    request: ChatRequest,
    current_user: CurrentUser,
):
    """
    视频剪辑 AI 对话接口（非流式）
    """
    try:
        agent, middleware = _get_or_create_agent()
    except Exception as e:
        logger.error(f"[VideoAgent] 创建 Agent 失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI 服务初始化失败: {str(e)}")

    conversation_id = request.conversation_id or str(uuid.uuid4())
    recursion_limit = _get_recursion_limit(request.message)
    config = {"configurable": {"thread_id": conversation_id}, "recursion_limit": recursion_limit}

    # 注入 Langfuse Callback：将 conversation_id 作为 session_id，后续
    # test_runner 通过 session_id 搜索来拉取 Token / Latency / Cost。
    _inject_langfuse_callback(config, conversation_id)

    # 确保会话存在于数据库
    _ensure_conversation(conversation_id, current_user.id, request.script_id)

    # 保存用户消息到数据库
    _save_message(conversation_id, "user", request.message)

    # 自动设置会话标题
    title = request.message[:20] + "..." if len(request.message) > 20 else request.message
    _update_conversation_title(conversation_id, title)

    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": request.message}]},
            config,
        )

        ai_message = ""
        if result and "messages" in result:
            messages = result["messages"]
            for msg in reversed(messages):
                msg_type = getattr(msg, "type", None)
                if msg_type == "ai":
                    ai_message = _extract_text_content(getattr(msg, "content", ""))
                    break

        if not ai_message:
            ai_message = "抱歉，AI 未能生成有效回复，请重试。"

        # 保存 AI 回复到数据库
        _save_message(conversation_id, "assistant", ai_message)
        _update_conversation_timestamp(conversation_id)

        return ChatResponse(conversation_id=conversation_id, message=ai_message)

    except Exception as e:
        logger.error(f"[VideoAgent] Agent 调用失败: {e}")
        if _is_recursion_limit_error(e):
            return ChatResponse(
                conversation_id=conversation_id,
                message=(
                    "任务步骤较多，当前执行已达到单轮上限。已完成部分内容，"
                    "请回复'继续'以自动扩大上限并恢复执行。"
                ),
            )
        raise HTTPException(status_code=500, detail=f"AI 处理失败: {str(e)}")


@router.post("/videoagent/chat/stream")
async def api_chat_stream(
    request: ChatRequest,
    current_user: CurrentUser,
):
    """
    视频剪辑 AI 对话流式接口

    以 SSE 格式推送结构化事件，借鉴 Claude Code 的 agent 输出模式：
    - type=thinking  : AI 思考过程（流式增量文本）
    - type=text      : AI 回复文本（流式增量文本）
    - type=tool_call : 工具调用开始（tool_name, tool_args）
    - type=tool_result : 工具调用结果（tool_name, result）
    - type=done      : 完成（conversation_id）
    """
    try:
        agent, middleware = _get_or_create_agent()
    except Exception as e:
        logger.error(f"[VideoAgent] 创建 Agent 失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI 服务初始化失败: {str(e)}")

    conversation_id = request.conversation_id or str(uuid.uuid4())
    recursion_limit = _get_recursion_limit(request.message)
    config = {"configurable": {"thread_id": conversation_id}, "recursion_limit": recursion_limit}

    # 注入 Langfuse Callback：将 conversation_id 作为 session_id，后续
    # test_runner 通过 session_id 搜索来拉取 Token / Latency / Cost。
    _inject_langfuse_callback(config, conversation_id)

    # 确保会话存在于数据库
    _ensure_conversation(conversation_id, current_user.id, request.script_id)

    # 保存用户消息到数据库
    _save_message(conversation_id, "user", request.message)

    # 自动设置会话标题
    title = request.message[:20] + "..." if len(request.message) > 20 else request.message
    _update_conversation_title(conversation_id, title)

    async def event_generator():
        """SSE 事件生成器 - 使用 astream_events 推送结构化事件"""
        # 累积 AI 回复内容，用于最终保存到数据库
        assistant_content = ""
        # 收集工具调用事件（按时间线顺序），用于持久化
        tool_events: list[dict] = []

        try:
            # 使用 astream_events 获取细粒度事件流
            has_astream_events = hasattr(agent, "astream_events")

            if has_astream_events:
                async for event in agent.astream_events(
                    {"messages": [{"role": "user", "content": request.message}]},
                    config,
                    version="v2",
                ):
                    kind = event.get("event", "")
                    data = event.get("data", {})
                    name = event.get("name", "")

                    # --- AI 模型开始生成 ---
                    if kind == "on_chain_start" and name == "ChatOpenAI":
                        yield _make_sse("thinking", {"content": ""})

                    # --- AI 流式输出 token ---
                    elif kind == "on_chat_model_stream":
                        chunk = data.get("chunk")
                        if chunk is None:
                            continue

                        # 提取文本内容
                        text = ""
                        if hasattr(chunk, "content"):
                            text = _extract_text_content(chunk.content)
                        elif isinstance(chunk, dict):
                            text = _extract_text_content(chunk.get("content", ""))

                        if not text:
                            continue

                        assistant_content += text
                        yield _make_sse("text", {"content": text})

                    # --- 工具调用开始 ---
                    elif kind == "on_tool_start":
                        tool_name = name
                        tool_input = data.get("input", {})
                        # 截断过长的参数显示
                        args_str = json.dumps(tool_input, ensure_ascii=False)
                        if len(args_str) > 500:
                            args_str = args_str[:500] + "..."
                        tool_events.append({
                            "type": "tool",
                            "tool_name": tool_name,
                            "tool_args": args_str,
                            "status": "running",
                        })
                        yield _make_sse("tool_call", {
                            "tool_name": tool_name,
                            "tool_args": args_str,
                        })

                    # --- 工具调用结束 ---
                    elif kind == "on_tool_end":
                        tool_name = name
                        output = data.get("output", "")
                        output_str = str(output)
                        if len(output_str) > 1000:
                            output_str = output_str[:1000] + "..."
                        # 合并到最后一个匹配的 running 条目，而非新建
                        merged = False
                        for entry in reversed(tool_events):
                            if entry.get("type") == "tool" and entry.get("tool_name") == tool_name and entry.get("status") == "running":
                                entry["result"] = output_str
                                entry["status"] = "completed"
                                merged = True
                                break
                        if not merged:
                            tool_events.append({
                                "type": "tool",
                                "tool_name": tool_name,
                                "result": output_str,
                                "status": "completed",
                            })
                        yield _make_sse("tool_result", {
                            "tool_name": tool_name,
                            "result": output_str,
                        })

                        # Path B: submit_storyboard 工具调用 → 启动编辑任务
                        if tool_name == "submit_storyboard" and '"status": "valid"' in output_str:
                            try:
                                # 从多个可能位置提取 storyboard JSON
                                tool_input = data.get("input", {})
                                json_str = (
                                    tool_input.get("storyboard_json", "")
                                    or str(tool_input)  # fallback: 整个 input 可能就是 JSON
                                )
                                # 如果工具输出的 output 本身就包含 storyboard
                                if not json_str or "steps" not in json_str:
                                    parsed_output = json.loads(output_str) if isinstance(output_str, str) else output_str
                                    json_str = json_str or str(tool_input)

                                storyboard = json.loads(json_str) if isinstance(json_str, str) else json_str
                                if isinstance(storyboard, dict) and "steps" in storyboard:
                                    task_id = await _launch_edit_task_async(
                                        storyboard, str(current_user.id), request.script_id,
                                    )
                                    yield _make_sse("task_created", {
                                        "task_id": task_id,
                                        "conversation_id": conversation_id,
                                        "total_steps": len(storyboard.get("steps", [])),
                                    })
                                    logger.info("[VideoAgent] submit_storyboard 触发, task_id=%s", task_id)
                                else:
                                    logger.warning("[VideoAgent] submit_storyboard JSON 无 steps: %s", json_str[:200])
                            except Exception as e:
                                logger.exception("[VideoAgent] submit_storyboard 启动失败")

                    # --- Agent 步骤结束（可用于追踪多轮工具调用） ---
                    elif kind == "on_chain_end" and name == "AgentExecutor":
                        pass  # 不需要单独发事件

                # 保存 AI 回复到数据库（含工具调用步骤）
                tool_steps_json = json.dumps(tool_events, ensure_ascii=False) if tool_events else None
                if assistant_content or tool_steps_json:
                    _save_message(conversation_id, "assistant", assistant_content, tool_steps_json)
                _update_conversation_timestamp(conversation_id)

                # Path B 集成：检测分镜 JSON 并启动编辑任务
                storyboard = _try_extract_storyboard(assistant_content)
                if storyboard:
                    try:
                        task_id = await _launch_edit_task_async(
                            storyboard, str(current_user.id), request.script_id,
                        )
                        yield _make_sse("task_created", {
                            "task_id": task_id,
                            "conversation_id": conversation_id,
                            "total_steps": len(storyboard.get("steps", [])),
                        })
                        logger.info("[VideoAgent] 分镜方案检测成功, task_id=%s", task_id)
                    except Exception as e:
                        logger.exception("[VideoAgent] 分镜方案启动失败")
                        yield _make_sse("error", {
                            "content": f"编辑任务启动失败: {e}",
                        })

                # 发送完成事件
                yield _make_sse("done", {"conversation_id": conversation_id})

            else:
                # 回退：使用 agent.stream()
                has_stream = hasattr(agent, "stream")

                if has_stream:
                    for chunk in agent.stream(
                        {"messages": [{"role": "user", "content": request.message}]},
                        config,
                    ):
                        text = ""
                        if isinstance(chunk, dict):
                            messages = chunk.get("messages", [])
                            if messages:
                                last_msg = messages[-1] if isinstance(messages, list) else messages
                                if hasattr(last_msg, "content"):
                                    text = _extract_text_content(last_msg.content)
                                elif isinstance(last_msg, dict):
                                    text = _extract_text_content(last_msg.get("content", ""))

                        elif hasattr(chunk, "content"):
                            text = _extract_text_content(chunk.content)

                        if text:
                            assistant_content += text
                            yield _make_sse("text", {"content": text})

                    # 保存 AI 回复到数据库
                    if assistant_content:
                        _save_message(conversation_id, "assistant", assistant_content)
                    _update_conversation_timestamp(conversation_id)

                    # Path B: 检测分镜 JSON
                    storyboard = _try_extract_storyboard(assistant_content)
                    if storyboard:
                        try:
                            task_id = await _launch_edit_task_async(
                                storyboard, str(current_user.id), request.script_id,
                            )
                            yield _make_sse("task_created", {
                                "task_id": task_id,
                                "conversation_id": conversation_id,
                                "total_steps": len(storyboard.get("steps", [])),
                            })
                        except Exception as e:
                            logger.exception("[VideoAgent] 分镜方案启动失败(stream)")

                    yield _make_sse("done", {"conversation_id": conversation_id})

                else:
                    # 最终回退：invoke 一次性返回
                    result = agent.invoke(
                        {"messages": [{"role": "user", "content": request.message}]},
                        config,
                    )
                    ai_message = ""
                    if result and "messages" in result:
                        for msg in reversed(result["messages"]):
                            if getattr(msg, "type", None) == "ai":
                                ai_message = _extract_text_content(getattr(msg, "content", ""))
                                break
                    if not ai_message:
                        ai_message = "抱歉，AI 未能生成有效回复，请重试。"

                    # 保存 AI 回复到数据库
                    _save_message(conversation_id, "assistant", ai_message)
                    _update_conversation_timestamp(conversation_id)

                    # Path B: 检测分镜 JSON
                    storyboard = _try_extract_storyboard(ai_message)
                    if storyboard:
                        try:
                            task_id = await _launch_edit_task_async(
                                storyboard, str(current_user.id), request.script_id,
                            )
                            yield _make_sse("task_created", {
                                "task_id": task_id,
                                "conversation_id": conversation_id,
                                "total_steps": len(storyboard.get("steps", [])),
                            })
                        except Exception as e:
                            logger.exception("[VideoAgent] 分镜方案启动失败(invoke)")

                    yield _make_sse("text", {"content": ai_message})
                    yield _make_sse("done", {"conversation_id": conversation_id})

        except Exception as e:
            logger.error(f"[VideoAgent] 流式生成失败: {e}", exc_info=True)

            # 递归限制 → HITL：告知用户任务未完成，询问是否继续
            if _is_recursion_limit_error(e):
                if assistant_content:
                    _save_message(conversation_id, "assistant", assistant_content)
                yield _make_sse("hitl_continue", {
                    "message": (
                        "任务步骤较多，当前执行已达到单轮上限。"
                        "已完成部分内容，请回复'继续'以自动扩大上限并恢复执行。"
                    ),
                    "conversation_id": conversation_id,
                    "suggestion": "回复'继续'以继续，或回复'取消'以中止",
                })
                yield _make_sse("done", {"conversation_id": conversation_id})
                return

            if assistant_content:
                _save_message(conversation_id, "assistant", assistant_content)
            yield _make_sse("error", {"content": f"AI 处理失败: {str(e)}"})
            yield _make_sse("done", {"conversation_id": conversation_id})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
