from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from app.agent.utils.cookie_loader import generate_qr_code_data, poll_qr_code_status
from app.agent.hotspot.finder import find_hotspots
from pydantic import BaseModel
from typing import List, Dict
router = APIRouter(tags=["bilibili"])
class HotspotRequest(BaseModel):
    keywords: List[str]
    weights: Dict[str, float]

class VeoGenerateRequest(BaseModel):
    prompt_path: str

class ManualLinkRequest(BaseModel):
    video_url: str
    series: str = "Manual Input Series"

class IterateRequest(BaseModel):
    base_prompt_path: str
    video_url: str
@router.get("/bilibili/get-qr-code")
async def get_qr_code():
    try:
        data = generate_qr_code_data()
        return JSONResponse(content=data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成二维码失败: {e}")

@router.get("/bilibili/poll-qr-code")
async def poll_qr_status(qrcode_key: str):
    try:
        data = poll_qr_code_status(qrcode_key)
        return JSONResponse(content=data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"轮询状态失败: {e}")
    
@router.post("/bilibili/hotspot-search")
async def search_hotspots(request: HotspotRequest):
    try:
        candidates = find_hotspots(keywords=request.keywords, top_k=20, weights=request.weights)
        return JSONResponse(content=[vars(h) for h in candidates])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索热点时发生错误: {e}")
    