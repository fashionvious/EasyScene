"""
Celery 重量步骤包装 — FFmpeg / TTS / Export / RoughCut。

复用 text2video 的 celery_app 实例（generateVideo/celery_config.py），
避免创建第三个 Celery 应用。
"""
from __future__ import annotations

import logging
import subprocess
import sys

from app.agent.generateVideo.celery_config import celery_app

logger = logging.getLogger(__name__)

# ============================================================================
# Celery Tasks
# ============================================================================


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def ffmpeg_normalize_task(
    self, video_path: str, output_path: str, **kwargs,
) -> dict:
    """FFmpeg 视频标准化（耗时 120-600s）"""
    try:
        from jianying_editor_skill.scripts.utils.media_normalizer import (
            normalize_webm_for_jianying,
        )
        result = normalize_webm_for_jianying(video_path, output_path)
        return {"success": True, "output_path": output_path, "result": str(result)}
    except Exception as e:
        logger.error("FFmpeg normalize failed: %s", e)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10)
def tts_synthesis_task(
    self, text: str, output_path: str, speaker: str = "zh_male_huoli", **kwargs,
) -> dict:
    """TTS 语音合成（耗时 30-120s）"""
    try:
        cmd = [
            sys.executable,
            "scripts/universal_tts.py",
            "--text", text,
            "--output", output_path,
            "--speaker", speaker,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return {"success": True, "output_path": output_path}
        raise RuntimeError(result.stderr.strip())
    except Exception as e:
        logger.error("TTS synthesis failed: %s", e)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def auto_export_task(
    self, draft_name: str, output_path: str,
    resolution: str = "1080p", fps: int = 30, **kwargs,
) -> dict:
    """自动导出（耗时 60-900s）"""
    try:
        cmd = [
            sys.executable,
            "scripts/auto_exporter.py",
            draft_name, output_path,
            "--res", resolution,
            "--fps", str(fps),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=900,
        )
        if result.returncode == 0:
            return {"success": True, "output_path": output_path}
        raise RuntimeError(result.stderr.strip())
    except Exception as e:
        logger.error("Auto export failed: %s", e)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def smart_rough_cut_task(
    self, video_path: str, **kwargs,
) -> dict:
    """智能粗剪（耗时 180-600s）"""
    try:
        cmd = [sys.executable, "scripts/smart_rough_cut.py", "--video", video_path]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0:
            return {"success": True, "output": result.stdout.strip()}
        raise RuntimeError(result.stderr.strip())
    except Exception as e:
        logger.error("Smart rough cut failed: %s", e)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


# ============================================================================
# 路由映射
# ============================================================================

# (tool_name, action) → celery_task 映射
# StepOrchestrator._dispatch_celery 使用此映射查找对应的 Celery Task
ASYNC_TOOL_TASK_MAP: dict[str, dict[str, object]] = {
    "execute_cli_script": {
        "smart_rough_cut":  smart_rough_cut_task,
        "export":           auto_export_task,
        "universal_tts":    tts_synthesis_task,
    },
}
