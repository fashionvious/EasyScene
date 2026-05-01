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
) -> None:
    """保存一条聊天消息到数据库"""
    with Session(engine) as session:
        msg_in = ChatMessageCreate(
            conversation_id=uuid.UUID(conversation_id),
            role=role,
            content=content,
        )
        crud.create_chat_message(session=session, msg_in=msg_in)


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
    config = {"configurable": {"thread_id": conversation_id}}

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
    config = {"configurable": {"thread_id": conversation_id}}

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
                        yield _make_sse("tool_result", {
                            "tool_name": tool_name,
                            "result": output_str,
                        })

                    # --- Agent 步骤结束（可用于追踪多轮工具调用） ---
                    elif kind == "on_chain_end" and name == "AgentExecutor":
                        pass  # 不需要单独发事件

                # 保存 AI 回复到数据库
                if assistant_content:
                    _save_message(conversation_id, "assistant", assistant_content)
                _update_conversation_timestamp(conversation_id)

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

                    yield _make_sse("text", {"content": ai_message})
                    yield _make_sse("done", {"conversation_id": conversation_id})

        except Exception as e:
            logger.error(f"[VideoAgent] 流式生成失败: {e}", exc_info=True)
            # 即使出错，也尝试保存已累积的内容
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
