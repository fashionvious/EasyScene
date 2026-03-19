# agent/hotspot/finder.py (最终算法版)
# -*- coding: utf-8 -*-
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any

from app.agent.collectors.bilibili import search_by_keyword, BILI_COOKIE


@dataclass
class Hotspot:
    title: str
    url: str
    bvid: str
    duration: int = 0
    pubdate: int = 0
    tags: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


def _score(stats: Dict[str, Any], pubdate: int, duration: int, weights: Dict[str, float]) -> float:
    """
    最终版热度分计算函数
    Score = (P / (T + 2)^G) / (D + 60)^S
    P = Points (基础热度分), T = Time (小时), G = Gravity (时间衰减)
    D = Duration (秒), S = Short-video priority (时长权重)
    """
    # 1. 计算基础热度分 P (移除收藏和分享)
    p = (
            float(stats.get("likes", 0)) * weights.get("likes", 1.0) +
            float(stats.get("comments", 0)) * weights.get("comments", 0.8) +
            float(stats.get("danmaku", 0)) * weights.get("danmaku", 0.5) +
            float(stats.get("views", 0)) * weights.get("views", 0.1)
    )

    # 2. 计算时间衰减
    t = (time.time() - pubdate) / 3600.0
    g = weights.get("gravity", 1.8)
    time_adjusted_score = p / pow(t + 2, g)

    # 3. 应用时长惩罚 (S)
    s = weights.get("duration_weight", 0.25)
    # 增加一个常量避免时长过短时惩罚过大
    duration_penalty_factor = pow(duration + 60, s)

    # 4. 计算最终得分
    score = time_adjusted_score / duration_penalty_factor
    return score * 1000  # 将分数放大，便于阅读


def find_hotspots(keywords: List[str], top_k: int, weights: Dict[str, float]) -> List[Hotspot]:
    if not BILI_COOKIE:
        print("[⚠️ 警告] .env 文件中缺少 BILI_COOKIE。")

    print(f"🔍 正在为关键词 {keywords} 搜索真实热点视频...")
    pool: List[Hotspot] = []

    for kw in keywords:
        search_result = search_by_keyword(kw)
        try:
            video_list = search_result.get("data", {}).get("result", [])
            for v_data in video_list:
                if v_data.get("type") == "video":
                    stats = {
                        "views": v_data.get("play", 0),
                        "likes": v_data.get("like", 0),
                        "comments": v_data.get("review", 0),
                        "danmaku": v_data.get("danmaku", 0),
                    }
                    clean_title = re.sub(r'<em class="keyword">|</em>', '', v_data.get("title", ""))

                    # B站API返回的duration是 "分:秒" 格式的字符串，需要转换为秒
                    duration_str = v_data.get("duration", "0:0")
                    try:
                        minutes, seconds = map(int, duration_str.split(':'))
                        duration_in_seconds = minutes * 60 + seconds
                    except:
                        duration_in_seconds = 0

                    hotspot = Hotspot(
                        title=clean_title,
                        url=v_data.get("arcurl", ""),
                        bvid=v_data.get("bvid", ""),
                        duration=duration_in_seconds,
                        pubdate=v_data.get("pubdate", int(time.time())),
                        stats=stats,
                        tags=v_data.get("tag", "").split(",")
                    )
                    pool.append(hotspot)
        except Exception as e:
            print(f"❌ 解析关键词 '{kw}' 的搜索结果时出错: {e}")
            continue

    dedup: Dict[str, Hotspot] = {h.bvid: h for h in pool if h.bvid}
    unique_pool = list(dedup.values())

    for h in unique_pool:
        h.score = _score(h.stats, h.pubdate, h.duration, weights)

    ranked = sorted(unique_pool, key=lambda x: x.score, reverse=True)

    print(f"✅ 找到 {len(unique_pool)} 个不重复的视频，返回前 {top_k} 个。")
    return ranked[:top_k]