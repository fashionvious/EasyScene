"""
全局统一Seed管理器
通过确定性seed方案实现人物/场景一致性，避免生成的视频产生人物/场景漂移。

设计思路：
1. 基于script_id生成全局主seed（同一剧本始终得到相同的主seed）
2. 为不同生成阶段（角色四视图、场景背景图、九宫格图）派生确定性子seed
3. 同一角色/场景在不同调用中使用相同seed，保证视觉一致性
4. 支持用户显式指定seed覆盖自动生成

参考：
- backend/app/agent/core/contracts/video_generation.py 中 VideoGenerationInput.seed
- backend/app/agent/core/contracts/image_generation.py 中 ImageGenerationInput.seed
- backend/app/agent/core/integrations/volcengine/video_payload.py 中 seed 透传
- backend/app/agent/core/integrations/openai/video_payload.py 中 seed 透传
"""
import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# DashScope API seed 取值范围：[0, 2^31-1]（32位有符号整数）
SEED_MAX = 2147483647  # 2^31 - 1


def _stable_hash(text: str) -> int:
    """对文本做确定性哈希，返回非负整数。"""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest, 16)


def generate_global_seed(script_id: str, user_seed: Optional[int] = None) -> int:
    """
    生成全局主seed。

    - 如果用户显式提供 user_seed，直接使用（截断到合法范围）
    - 否则基于 script_id 做确定性哈希，保证同一剧本始终得到相同seed

    返回: [0, SEED_MAX] 范围内的整数
    """
    if user_seed is not None:
        seed = user_seed if 0 <= user_seed <= SEED_MAX else user_seed % (SEED_MAX + 1)
        logger.info(f"使用用户指定seed: {seed}")
        return seed

    raw = _stable_hash(f"global:{script_id}")
    seed = raw % (SEED_MAX + 1)
    logger.info(f"基于script_id生成全局seed: {seed}")
    return seed


def derive_character_seed(global_seed: int, role_name: str) -> int:
    """
    为指定角色派生确定性seed。

    同一 global_seed + 同一 role_name 始终产生相同seed，
    保证同一角色在不同阶段的四视图生成具有视觉一致性。

    参数:
    - global_seed: 全局主seed
    - role_name: 角色名称

    返回: [0, SEED_MAX] 范围内的整数
    """
    raw = _stable_hash(f"character:{global_seed}:{role_name}")
    seed = raw % (SEED_MAX + 1)
    logger.info(f"角色 '{role_name}' 派生seed: {seed}")
    return seed


def derive_scene_seed(global_seed: int, scene_group_no: int) -> int:
    """
    为指定场景组派生确定性seed。

    同一 global_seed + 同一 scene_group_no 始终产生相同seed，
    保证同一场景的背景图在不同调用中保持视觉一致性。

    参数:
    - global_seed: 全局主seed
    - scene_group_no: 场景组号

    返回: [0, SEED_MAX] 范围内的整数
    """
    raw = _stable_hash(f"scene:{global_seed}:{scene_group_no}")
    seed = raw % (SEED_MAX + 1)
    logger.info(f"场景组 {scene_group_no} 派生seed: {seed}")
    return seed


def derive_grid_seed(global_seed: int, shot_no: int) -> int:
    """
    为指定分镜的九宫格图派生确定性seed。

    同一 global_seed + 同一 shot_no 始终产生相同seed，
    保证同一分镜的九宫格图在重新生成时保持视觉一致性。

    参数:
    - global_seed: 全局主seed
    - shot_no: 分镜号

    返回: [0, SEED_MAX] 范围内的整数
    """
    raw = _stable_hash(f"grid:{global_seed}:{shot_no}")
    seed = raw % (SEED_MAX + 1)
    logger.info(f"分镜 {shot_no} 九宫格派生seed: {seed}")
    return seed


def derive_first_frame_seed(global_seed: int, shot_no: int) -> int:
    """
    为指定分镜的首帧图派生确定性seed。

    同一 global_seed + 同一 shot_no 始终产生相同seed，
    保证同一分镜的首帧图在重新生成时保持视觉一致性。

    参数:
    - global_seed: 全局主seed
    - shot_no: 分镜号

    返回: [0, SEED_MAX] 范围内的整数
    """
    raw = _stable_hash(f"first_frame:{global_seed}:{shot_no}")
    seed = raw % (SEED_MAX + 1)
    logger.info(f"分镜 {shot_no} 首帧图派生seed: {seed}")
    return seed


def derive_video_seed(global_seed: int, shot_no: int) -> int:
    """
    为指定分镜的视频生成派生确定性seed。

    同一 global_seed + 同一 shot_no 始终产生相同seed，
    保证同一分镜的视频在重新生成时保持视觉一致性。

    参数:
    - global_seed: 全局主seed
    - shot_no: 分镜号

    返回: [0, SEED_MAX] 范围内的整数
    """
    raw = _stable_hash(f"video:{global_seed}:{shot_no}")
    seed = raw % (SEED_MAX + 1)
    logger.info(f"分镜 {shot_no} 视频派生seed: {seed}")
    return seed
