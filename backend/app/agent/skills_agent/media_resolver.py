"""
媒体素材解析器 - 根据文件名在预配置的搜索路径中自动查找视频/音频/图片文件
"""
import os
import glob
from pathlib import Path
from typing import Optional


# 默认搜索路径（相对于 skills_agent/ 目录）
DEFAULT_MEDIA_SEARCH_PATHS = [
    "video",                    # skills_agent/video/
    "audio",                    # skills_agent/audio/
    "assets",                   # skills_agent/assets/
    "../assets",                # 上级 assets/
    "../../assets",             # 更上级
]


class MediaResolver:
    """
    媒体素材解析器
    
    根据文件名（或部分文件名）在预配置的搜索路径中查找媒体文件，
    用户无需输入完整路径，只需输入文件名即可。
    """
    
    # 支持的媒体扩展名
    VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}
    AUDIO_EXTS = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"}
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}
    ALL_MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS
    
    def __init__(
        self,
        search_paths: list[str] = None,
        extra_paths: list[str] = None,
        base_dir: str = None
    ):
        """
        初始化解析器
        
        Args:
            search_paths: 搜索路径列表（相对于 base_dir）
            extra_paths: 额外的绝对搜索路径
            base_dir: 基准目录（默认为当前文件所在目录）
        """
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).parent
        
        # 构建搜索路径
        self.search_paths: list[Path] = []
        
        if search_paths is None:
            search_paths = DEFAULT_MEDIA_SEARCH_PATHS
        
        for p in search_paths:
            full_path = (self.base_dir / p).resolve()
            if full_path.exists() and full_path.is_dir():
                self.search_paths.append(full_path)
        
        # 添加额外路径
        if extra_paths:
            for p in extra_paths:
                full_path = Path(p).resolve()
                if full_path.exists() and full_path.is_dir():
                    self.search_paths.append(full_path)
    
    def resolve(self, query: str) -> dict:
        """
        解析媒体文件
        
        支持以下输入格式：
        1. 完整绝对路径: "D:/videos/test.mp4" -> 直接返回
        2. 相对路径: "video/test.mp4" -> 相对于 base_dir 解析
        3. 仅文件名: "test.mp4" -> 在搜索路径中查找
        4. 部分文件名: "test" -> 模糊匹配（前缀匹配）
        
        Args:
            query: 文件名、路径或部分文件名
            
        Returns:
            {
                "found": bool,
                "path": str | None,       # 解析后的绝对路径
                "query": str,             # 原始查询
                "candidates": list[str],  # 所有候选文件（模糊匹配时）
                "type": str | None,       # "video" | "audio" | "image" | "unknown"
            }
        """
        query = query.strip().replace("\\", "/")
        
        # 1. 如果是绝对路径且文件存在，直接返回
        if os.path.isabs(query):
            abs_path = Path(query)
            if abs_path.exists() and abs_path.is_file():
                return self._make_result(True, str(abs_path), query)
            # 绝对路径但文件不存在
            return self._make_result(False, None, query)
        
        # 2. 尝试相对于 base_dir 解析
        rel_path = (self.base_dir / query).resolve()
        if rel_path.exists() and rel_path.is_file():
            return self._make_result(True, str(rel_path), query)
        
        # 3. 在搜索路径中精确查找文件名
        filename = os.path.basename(query)
        for search_dir in self.search_paths:
            target = search_dir / filename
            if target.exists() and target.is_file():
                return self._make_result(True, str(target.resolve()), query)
        
        # 4. 模糊匹配：在搜索路径中搜索包含 query 的文件
        candidates = []
        query_lower = query.lower()
        
        for search_dir in self.search_paths:
            for ext in self.ALL_MEDIA_EXTS:
                pattern = str(search_dir / "**" / f"*{query_lower}*{ext.lstrip('.')}")
                # 用 glob 递归搜索
                for match in glob.glob(pattern, recursive=True):
                    candidates.append(match)
                # 也搜索精确前缀
                pattern2 = str(search_dir / "**" / f"{query_lower}*")
                for match in glob.glob(pattern2, recursive=True):
                    if match not in candidates:
                        candidates.append(match)
        
        # 去重并排序
        candidates = sorted(set(candidates))
        
        if len(candidates) == 1:
            # 只有一个候选，直接返回
            return self._make_result(True, candidates[0], query, candidates)
        elif len(candidates) > 1:
            # 多个候选，返回列表让调用者选择
            return self._make_result(False, None, query, candidates)
        else:
            return self._make_result(False, None, query, [])
    
    def list_available(self, media_type: str = None) -> list[dict]:
        """
        列出搜索路径中所有可用的媒体文件
        
        Args:
            media_type: "video" | "audio" | "image" | None(全部)
            
        Returns:
            [{"name": str, "path": str, "type": str, "size_mb": float}]
        """
        if media_type == "video":
            exts = self.VIDEO_EXTS
        elif media_type == "audio":
            exts = self.AUDIO_EXTS
        elif media_type == "image":
            exts = self.IMAGE_EXTS
        else:
            exts = self.ALL_MEDIA_EXTS
        
        results = []
        for search_dir in self.search_paths:
            for ext in exts:
                for f in search_dir.rglob(f"*{ext}"):
                    size_mb = f.stat().st_size / (1024 * 1024)
                    media_t = self._classify_ext(ext)
                    results.append({
                        "name": f.name,
                        "path": str(f.resolve()),
                        "type": media_t,
                        "size_mb": round(size_mb, 2)
                    })
        
        return sorted(results, key=lambda x: x["name"])
    
    def _classify_ext(self, ext: str) -> str:
        if ext in self.VIDEO_EXTS:
            return "video"
        elif ext in self.AUDIO_EXTS:
            return "audio"
        elif ext in self.IMAGE_EXTS:
            return "image"
        return "unknown"
    
    def _make_result(
        self,
        found: bool,
        path: Optional[str],
        query: str,
        candidates: list[str] = None
    ) -> dict:
        result = {
            "found": found,
            "path": path,
            "query": query,
            "candidates": candidates or [],
        }
        if path:
            ext = Path(path).suffix.lower()
            result["type"] = self._classify_ext(ext)
        else:
            result["type"] = None
        return result


# 创建 LangChain 工具
def create_media_resolver_tool(
    search_paths: list[str] = None,
    extra_paths: list[str] = None,
    base_dir: str = None
):
    """创建媒体解析工具"""
    resolver = MediaResolver(search_paths, extra_paths, base_dir)
    
    from langchain.tools import tool
    
    @tool
    def resolve_media(query: str) -> str:
        """
        根据文件名查找视频/音频/图片文件的完整路径。
        
        支持以下输入：
        - 完整路径: "D:/videos/test.mp4" -> 直接验证并返回
        - 仅文件名: "test01.mp4" -> 在预配置路径中自动搜索
        - 部分文件名: "test" -> 模糊匹配所有包含该关键词的媒体文件
        
        Args:
            query: 文件名、路径或部分文件名
        """
        result = resolver.resolve(query)
        
        if result["found"]:
            return f"找到文件: {result['path']} (类型: {result['type']})"
        
        if result["candidates"]:
            lines = [f"找到 {len(result['candidates'])} 个候选文件:"]
            for c in result["candidates"]:
                lines.append(f"  - {c}")
            lines.append("请指定更精确的文件名。")
            return "\n".join(lines)
        
        return f"未找到匹配 '{query}' 的文件。请检查文件名或使用 list_media 查看可用文件。"
    
    @tool
    def list_media(media_type: str = None) -> str:
        """
        列出所有可用的视频/音频/图片文件。
        
        Args:
            media_type: 媒体类型过滤 ("video", "audio", "image")，不填则列出全部
        """
        files = resolver.list_available(media_type)
        
        if not files:
            return "没有找到任何媒体文件。"
        
        lines = [f"可用媒体文件 ({len(files)} 个):"]
        for f in files:
            lines.append(f"  - {f['name']} ({f['type']}, {f['size_mb']}MB)")
            lines.append(f"    路径: {f['path']}")
        
        return "\n".join(lines)
    
    return resolve_media, list_media, resolver
