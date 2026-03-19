"""
文生图API接口
提供提示词生成、修改和图片生成的API端点
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import json

from app.agent.generatePic.text2image import (
    generate_prompt_stream,
    modify_prompt_stream,
    generate_and_save_images,
)

router = APIRouter(tags=["text2image"])


# --- 请求模型 ---

class GeneratePromptRequest(BaseModel):
    """生成提示词请求"""
    user_input: str


class ModifyPromptRequest(BaseModel):
    """修改提示词请求"""
    user_input: str
    current_prompt: str


class GenerateImageRequest(BaseModel):
    """生成图片请求"""
    prompt: str
    size: str = "1024*1024"
    n: int = 1


# --- 响应模型 ---

class ImageResult(BaseModel):
    """图片结果"""
    image_url: str
    prompt: str
    local_path: Optional[str] = None
    db_saved: bool = False


# --- API端点 ---

@router.post("/text2image/generate-prompt")
async def api_generate_prompt(request: GeneratePromptRequest):
    """
    生成文生图提示词（流式输出）
    
    参数:
    - user_input: 用户输入的图片描述
    
    返回:
    - 流式响应，每次返回一个字符串片段
    """
    if not request.user_input or not request.user_input.strip():
        raise HTTPException(status_code=400, detail="请输入图片描述")
    
    async def generate():
        try:
            for chunk in generate_prompt_stream(request.user_input):
                yield chunk
        except Exception as e:
            yield f"\n[错误] 生成提示词失败: {str(e)}"
    
    return StreamingResponse(
        generate(),
        media_type="text/plain; charset=utf-8"
    )


@router.post("/text2image/modify-prompt")
async def api_modify_prompt(request: ModifyPromptRequest):
    """
    修改文生图提示词（流式输出）
    
    参数:
    - user_input: 用户的修改意见
    - current_prompt: 当前的提示词
    
    返回:
    - 流式响应，每次返回一个字符串片段
    """
    if not request.user_input or not request.user_input.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")
    
    if not request.current_prompt or not request.current_prompt.strip():
        raise HTTPException(status_code=400, detail="当前提示词不能为空")
    
    async def generate():
        try:
            for chunk in modify_prompt_stream(request.user_input, request.current_prompt):
                yield chunk
        except Exception as e:
            yield f"\n[错误] 修改提示词失败: {str(e)}"
    
    return StreamingResponse(
        generate(),
        media_type="text/plain; charset=utf-8"
    )


@router.post("/text2image/generate-image")
async def api_generate_image(request: GenerateImageRequest) -> List[ImageResult]:
    """
    根据提示词生成图片
    
    参数:
    - prompt: 文生图提示词
    - size: 图片尺寸，支持 "1024*1024", "720*1280", "768*1152", "1280*720"
    - n: 生成图片数量，1-4
    
    返回:
    - 图片结果列表
    """
    if not request.prompt or not request.prompt.strip():
        raise HTTPException(status_code=400, detail="提示词不能为空")
    
    # 验证图片尺寸
    valid_sizes = ["1024*1024", "720*1280", "768*1152", "1280*720"]
    if request.size not in valid_sizes:
        raise HTTPException(
            status_code=400,
            detail=f"图片尺寸无效，支持的尺寸: {', '.join(valid_sizes)}"
        )
    
    # 验证生成数量
    if request.n < 1 or request.n > 4:
        raise HTTPException(status_code=400, detail="生成数量必须在1-4之间")
    
    try:
        results = generate_and_save_images(
            prompt=request.prompt,
            size=request.size,
            n=request.n,
            save_local=True,
            save_db=True
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成图片失败: {str(e)}")
