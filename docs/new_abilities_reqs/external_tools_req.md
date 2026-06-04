# external_tools — 外部原子工具集成模块需求文档

> **父文档**: [Agent 剪辑工具扩充 PRD](../Agent%20剪辑工具扩充需求与技术设计文档.md)
> **Phase**: 3 (外部原子工具层) + Phase 3b (远期 ASR/LLM 规划)
> **目标文件**: `tools/external_tools.py` + `tools/subprocess_executor.py`

---

## 1. 模块归属与前置依赖

| 项目 | 说明 |
|------|------|
| **所属 Phase** | Phase 3 — 外部原子工具层，需系统级依赖 |
| **对应 Python 文件** | `tools/external_tools.py` (业务工具) + `tools/subprocess_executor.py` (安全执行器) |
| **注册 Handler** | `external_tools` |
| **核心依赖** | `jy_wrapper.JyProject`, `FFmpeg ≥ 5.0`, `librosa ≥ 0.10`, `soundfile ≥ 0.12`, `noisereduce ≥ 3.0`, `OpenCV ≥ 4.8` |
| **运行时环境** | Docker (Debian-slim + FFmpeg + OpenCV)，建议内存 ≥ 2GB |
| **预计工时** | 5-10 天 |

### 注册的 Agent Tools (Phase 3a)

| Tool 名称 | 类别 | 依赖 | 对应功能 ID |
|-----------|------|------|-------------|
| `extract_freeze_frame` | write | FFmpeg | T-07 画面定格 |
| `detect_beats` | read | librosa | A-01 BGM 卡点 (节拍检测) |
| `apply_beat_sync_cut` | write | JyProject + librosa | A-01 BGM 卡点 (草稿生成) |
| `denoise_audio` | write | noisereduce / FFmpeg | A-02 人声增强+降噪 |
| `chroma_key_remove` | write | FFmpeg | V-03 绿幕抠像 |
| `track_object` | read | OpenCV | V-07/V-09 目标跟踪 |
| `apply_text_tracking` | write | JyProject + OpenCV | V-07 文字跟踪运动 |
| `analyze_motion` | read | OpenCV | TR-03 光流运镜分析 |
| `apply_speed_ramp` | write | JyProject | T-06 曲线变速 (已迁移至 Phase 2 draft_injector，见 [draft_injector_req.md](draft_injector_req.md)) |

### Phase 3b 远期规划 (不注册)

| 能力 ID | 名称 | 技术栈 | 预估工时 |
|---------|------|--------|---------|
| T-09 | 多机位对齐 | Whisper ASR + 音频波形交叉相关 | 5 天 |
| T-10 | B-Roll 智能叠加 | ASR 转写 → LLM 语义匹配 → 素材库检索 | 7 天 |

---

## 2. SubprocessExecutor — 子进程安全执行器

### 2.1 类设计

```python
import subprocess
import resource
import os
import hashlib
import json
import shutil
from dataclasses import dataclass
from typing import Optional


@dataclass
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str
    output_path: Optional[str] = None
    cache_hit: bool = False


@dataclass
class SubprocessConfig:
    timeout_seconds: int = 120
    memory_limit_mb: int = 2048
    cache_dir: str = "__jycache__"


class SubprocessExecutor:

    def __init__(self, config: SubprocessConfig = SubprocessConfig()):
        self.config = config
        os.makedirs(config.cache_dir, exist_ok=True)

    def _get_cache_path(self, input_path: str, params: dict) -> str:
        key = hashlib.md5(
            (input_path + json.dumps(params, sort_keys=True)).encode()
        ).hexdigest()
        return os.path.join(self.config.cache_dir, key)

    def run(
        self,
        cmd: list[str],
        output_path: Optional[str] = None,
        cache_params: Optional[dict] = None,
        timeout: Optional[int] = None,
    ) -> SubprocessResult:
        """
        Args:
            cmd: 命令行参数列表
            output_path: 预期输出文件路径
            cache_params: 缓存键参数 dict，需包含 "input" 字段
            timeout: 超时秒数
        Returns:
            SubprocessResult
        Raises:
            subprocess.TimeoutExpired
            RuntimeError: 返回码非零
        """
        # 缓存命中 → 复制到用户期望路径
        if output_path and cache_params:
            cached = self._get_cache_path(cache_params.get("input", ""), cache_params)
            if os.path.exists(cached):
                shutil.copy2(cached, output_path)
                return SubprocessResult(0, "", "", output_path, cache_hit=True)

        t = timeout or self.config.timeout_seconds

        def _set_limits():
            limit = self.config.memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
            preexec_fn=_set_limits if os.name != "nt" else None,
        )

        result = SubprocessResult(proc.returncode, proc.stdout, proc.stderr, output_path)

        if proc.returncode != 0:
            raise RuntimeError(
                f"Subprocess failed (exit {proc.returncode}): {' '.join(cmd)}\n"
                f"stderr: {proc.stderr[-500:]}"
            )

        # 写入缓存
        if output_path and os.path.exists(output_path) and cache_params:
            cached = self._get_cache_path(cache_params.get("input", ""), cache_params)
            shutil.copy2(output_path, cached)

        return result


# 全局单例
_executor = SubprocessExecutor()
```

---

## 3. 各工具接口定义与核心逻辑

### 3.1 extract_freeze_frame — 定格帧

```python
def extract_freeze_frame(
    video_path: str,
    timestamp_s: float,
    output_dir: str = "__jycache__",
    duration_s: float = 3.0,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> dict:
```

**两步法流程**:
```
Step 1: ffmpeg -ss {ts} -i {video} -vframes 1 -q:v 2 frame.png
Step 2: ffmpeg -i frame.png
        -vf "loop=-1:1:0,trim=duration={duration},setpts=N/FRAME_RATE/TB"
        -c:v libx264 -preset ultrafast -pix_fmt yuv420p -an out.mp4
```

**异常**: `timestamp_s > 视频时长` → `{"ok": False, "reason": "timestamp_exceeds_duration"}`

### 3.2 detect_beats / apply_beat_sync_cut — BGM 卡点

```python
def detect_beats(
    audio_path: str,
    sr: int = 22050,
    tightness: float = 1.0,
) -> dict:
```

**出参**: `{"ok": True, "bpm": 128.0, "beat_times": [0.0, 0.468, ...], "total_beats": 128}`

```python
def apply_beat_sync_cut(
    project_name: str,
    video_path: str,
    bpm: float,
    beat_times: list[float],
    beats_per_clip: int = 2,
) -> dict:
```

**流程**: 遍历 beat_times → 每 beats_per_clip 个节拍一个 clip → 调用 `project.add_clip()` → 每个 clip 添加入场 keyframe。**使用 `KeyframeProperty` 枚举，非字符串**。

### 3.3 denoise_audio — 降噪+人声增强

```python
def denoise_audio(
    input_path: str,
    output_path: Optional[str] = None,
    method: str = "noisereduce",
    highpass_hz: int = 80,
    lowpass_hz: int = 8000,
) -> dict:
```

**双方案**:
- `"noisereduce"`: Python 频谱减法，精准但慢 (≤5 分钟推荐)。⚠️ 当前实现将立体声合并为单声道处理，输出为单声道。如需保留立体声，应对每个声道分别降噪后再合并。
- `"ffmpeg"`: `highpass + lowpass + afftdn + loudnorm` 滤镜链，快速但粗糙 (>5 分钟推荐)。输出保留立体声。

### 3.4 chroma_key_remove — 绿幕抠像

```python
def chroma_key_remove(
    input_path: str,
    output_path: Optional[str] = None,
    color_hex: str = "00FF00",
    similarity: float = 0.3,
    blend: float = 0.1,
) -> dict:
```

**输出**: WEBM (libvpx-vp9 + yuva420p)，保留 alpha 通道。剪映不支持带 alpha 的 MP4，故用 WEBM。
⚠️ **注意**: `media_normalizer.py` 会自动将 WEBM 转为 MP4（丢失 alpha）。抠像产出的 WEBM 需跳过 normalizer 处理，或由 Agent 侧在导入时标记 `skip_normalize=True`。

### 3.5 track_object / apply_text_tracking — 目标跟踪

```python
def track_object(
    video_path: str,
    roi: tuple[float, float, float, float],
    sample_fps: int = 5,
    tracker_type: str = "CSRT",
) -> dict:
```

**跟踪器选择**: CSRT (精度高，慢) / KCF (快，精度低)
**出参**: `{"ok": True, "trajectory": [{"frame": 0, "time_us": 0, "cx": 0.5, ...}, ...]}`

```python
def apply_text_tracking(
    project_name: str,
    segment_id: str,
    trajectory: list[dict],
) -> dict:
```

**路径获取**: 使用 `JyProject(project_name).root` + `project.name` 构建，**禁止直接用环境变量**。

### 3.6 analyze_motion — 光流运镜分析

```python
def analyze_motion(
    video_path: str,
    sample_fps: int = 2,
    head_seconds: float = 2.0,
    tail_seconds: float = 2.0,
) -> dict:
```

**算法**: OpenCV Farneback 光流 → 主导运动方向
**出参**: `{"ok": True, "head_motion": {"direction": "right", "magnitude": 3.5}, "tail_motion": {...}, "recommendation": "compatible"}`

### 3.7 apply_speed_ramp — 曲线变速

> ⚠️ **模块归属变更**: `apply_speed_ramp` 的实际实现是 draft.json 注入（Phase 2），
> 因为 pyJianYingDraft 的 `KeyframeProperty` 枚举中没有 speed 属性，
> 需要直接注入 KFTypeSpeed 节点到 draft_content.json。
> 具体实现参见 [draft_injector_req.md](draft_injector_req.md)。

---

## 4. 异常边界矩阵

| 异常场景 | 适用工具 | 策略 |
|---------|---------|------|
| `timestamp_s` 超过视频时长 | `extract_freeze_frame` | ffprobe 预探测 → `timestamp_exceeds_duration` |
| FFmpeg 子进程返回非零 | 所有 FFmpeg 工具 | `RuntimeError` 含 stderr 最后 500 字符 |
| 子进程超时 | 所有外部工具 | `subprocess.TimeoutExpired` → `{"ok": False, "reason": "timeout"}` |
| 内存超限 | 所有外部工具 | `resource.RLIMIT_AS` 触发 OOM Killer → `MemoryError` |
| 音频文件格式不支持 | `detect_beats`, `denoise_audio` | `soundfile.LibsndfileError` → `"reason": "unsupported_audio_format"` |
| 绿幕素材无 alpha | `chroma_key_remove` | 输出 WEBM 确保透明度，Agent 侧导入时注意 WEBM→MP4 归一化 |
| 跟踪器初始化失败 | `track_object` | 首帧读取失败 → `"reason": "first_frame_read_failed"` |
| speed 值非法 | `apply_speed_ramp` | speed ≤ 0 或 > 10 → `"reason": "invalid_speed"` |
| segment_id 找不到 | `apply_speed_ramp`, `apply_text_tracking` | 遍历未匹配 → `"reason": "segment_not_found"` |
| 缓存目录不可写 | `SubprocessExecutor` | `OSError` → 降级为不缓存模式 |

---

## 5. 可观测性设计 (Observability)

### 5.1 LangFuse Trace 埋点

Phase 3 的工具 Trace 模式与 Phase 1/2 一致，增加子进程级别的 Span：

```python
from langfuse import Langfuse

langfuse = Langfuse(
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)


def extract_freeze_frame(
    video_path: str,
    timestamp_s: float,
    output_dir: str = "__jycache__",
    duration_s: float = 3.0,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> dict:
    trace = langfuse.trace(
        name="extract_freeze_frame",
        metadata={
            "tool": "extract_freeze_frame",
            "phase": "3",
            "handler": "external_tools",
            "capability_id": "T-07",
            "external_dep": "FFmpeg",
        },
        input={
            "video_path": video_path,
            "timestamp_s": timestamp_s,
            "duration_s": duration_s,
            "width": width,
            "height": height,
        },
    )

    try:
        # 预探测 Span
        probe_span = trace.span(
            name="extract_freeze_frame.probe",
            input={"video_path": video_path},
        )

        if not Path(video_path).exists():
            probe_span.update(level="ERROR", status_message="File not found")
            probe_span.end()
            return {"ok": False, "reason": "file_not_found",
                    "detail": f"Video not found: {video_path}"}

        # ffprobe 探针
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
                      "-show_format", video_path]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        info = json.loads(probe.stdout)
        video_duration = float(info["format"]["duration"])

        if timestamp_s > video_duration:
            probe_span.update(level="ERROR", status_message="timestamp exceeds duration")
            probe_span.end()
            return {"ok": False, "reason": "timestamp_exceeds_duration",
                    "detail": f"{timestamp_s}s > {video_duration}s"}

        probe_span.update(output={"video_duration": video_duration})
        probe_span.end()

        # Step 1: 截帧 Span
        frame_span = trace.span(
            name="extract_freeze_frame.extract_frame",
            input={"timestamp_s": timestamp_s},
        )

        frame_path = os.path.join(output_dir, f"_freeze_frame_{uuid.uuid4().hex}.png")
        cmd1 = ["ffmpeg", "-y", "-ss", str(timestamp_s), "-i", video_path,
                "-vframes", "1", "-q:v", "2", frame_path]

        try:
            _executor.run(cmd1, output_path=frame_path)
            frame_span.update(output={"frame_path": frame_path, "ok": True})
        except RuntimeError as e:
            frame_span.update(level="ERROR", status_message=str(e))
            frame_span.end()
            raise
        frame_span.end()

        # Step 2: 生成定格视频 Span
        render_span = trace.span(
            name="extract_freeze_frame.render_video",
            input={"duration_s": duration_s},
        )

        output_name = f"freeze_{Path(video_path).stem}_{int(timestamp_s*1000)}ms_{int(duration_s)}s.mp4"
        output_path = os.path.join(output_dir, output_name)

        vf_parts = [f"loop=-1:1:0,trim=duration={duration_s},setpts=N/FRAME_RATE/TB"]
        if width and height:
            vf_parts.insert(0, f"scale={width}:{height}")

        cmd2 = ["ffmpeg", "-y", "-i", frame_path,
                "-vf", ",".join(vf_parts),
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-an", output_path]

        result = _executor.run(
            cmd2,
            output_path=output_path,
            cache_params={"input": video_path, "ts": timestamp_s,
                          "dur": duration_s, "w": width, "h": height},
        )

        # 清理临时帧
        try:
            os.remove(frame_path)
        except OSError:
            pass

        render_span.update(output={"output_path": output_path, "cache_hit": result.cache_hit})
        render_span.end()

        final = {
            "ok": True,
            "output_path": output_path,
            "timestamp_s": timestamp_s,
            "duration_s": duration_s,
            "cache_hit": result.cache_hit,
        }
        trace.update(output=final)
        return final

    except Exception as e:
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return {"ok": False, "reason": type(e).__name__, "detail": str(e)}


# detect_beats, denoise_audio, chroma_key_remove, track_object,
# analyze_motion, apply_speed_ramp 均遵循相同 Trace 模式:
#   1. langfuse.trace(name=func_name, metadata={phase: "3", external_dep: "..."})
#   2. trace.span(name=f"{func_name}.subprocess") — 记录子进程 stdout/stderr 长度
#   3. 缓存命中时 metadata 增加 cache_hit: True
```

### 5.2 Phase 3 特有指标

| 指标 | Span 名称 | 记录内容 |
|------|----------|---------|
| 子进程耗时 | `{tool}.subprocess` | 命令、exit_code、stdout/stderr 长度 |
| 缓存命中率 | 全局 | `cache_hit: True/False` |
| 子进程内存峰值 | `{tool}.subprocess` | `memory_peak_mb`（如可获取） |
| 大文件处理时间 | `{tool}.subprocess` | `input_file_size_mb` |

---

## 6. Docker 环境要求

```dockerfile
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    librosa>=0.10 \
    soundfile>=0.12 \
    noisereduce>=3.0 \
    opencv-python>=4.8 \
    opencv-contrib-python>=4.8 \
    numpy>=1.24
```

**验证命令**:
```bash
ffmpeg -version | head -1          # ≥ 5.0
python -c "import cv2; print(cv2.__version__)"  # ≥ 4.8
python -c "import librosa; print(librosa.__version__)"  # ≥ 0.10
python -c "import noisereduce"      # 无 ImportError
```

---

## 7. 模块验收标准 (DoD)

### SubprocessExecutor

- [ ] 子进程超时后正确抛出 `TimeoutExpired`，Agent 侧不阻塞
- [ ] 内存限制 `setrlimit` 生效，子进程 OOM 时被操作系统 kill
- [ ] 缓存命中时 `shutil.copy2` 将缓存文件复制到用户期望路径
- [ ] 缓存键稳定：相同输入 + 相同参数 → 相同缓存路径
- [ ] Windows (`os.name == "nt"`) 下 `preexec_fn` 不加载

### 业务工具

- [ ] `extract_freeze_frame`: 两步法正确；帧文件临时清理；timestamp 越界拒绝
- [ ] `detect_beats`: BPM 在 60-200 范围内的音频返回合理节拍数
- [ ] `apply_beat_sync_cut`: 生成的草稿中 clip 数量 ≈ beat_count / beats_per_clip
- [ ] `denoise_audio`: noisereduce 和 ffmpeg 两种方案均可正常输出；noisereduce 方案需文档标注"输出为单声道"（已知限制）
- [ ] `chroma_key_remove`: 输出 WEBM 的 alpha 通道有效；已确认抠像输出跳过 media_normalizer 的 WEBM→MP4 转换
- [ ] `track_object`: ROI 区域固定的视频返回合理轨迹（cx/cy 变化 < 0.1）
- [ ] `apply_text_tracking`: 关键帧注入后 `project.save()` 被调用
- [ ] `analyze_motion`: 静态视频返回 `"static"`；平移动视频返回正确方向
- [ ] `apply_speed_ramp`: speed_curve 正确注入为 KFTypeSpeed；越界 speed 拒绝
- [ ] Docker 镜像内置所有依赖，CI 流水线可验证
- [ ] 所有 Tool 的 LangFuse Trace 包含子进程级别 Span
- [ ] 阶段结束时缓存命中率 ≥ 60%
