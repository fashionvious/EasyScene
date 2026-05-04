"""
视频生成Agent节点
基于六宫格分镜头图片或首帧图生成视频
"""
import os
import logging
import dashscope
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from app.agent.generatePic.llm_client import HelloAgentsLLM
from app.agent.generateVideo.seed_manager import derive_video_seed

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


class VideoGenerationAgent:
    """视频生成Agent节点 - 基于六宫格分镜头图片生成视频"""

    def __init__(self, llm_client: HelloAgentsLLM, script_id: str = None):
        self.llm_client = llm_client
        self.script_id = script_id
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError("请在.env文件中设置DASHSCOPE_API_KEY")
        self.ark_api_key = os.getenv("ARK_API_KEY")

    def _get_video_output_dir(self, script_id: str) -> Path:
        """根据 script_id 获取视频输出目录（与四视图路径一致）"""
        base_dir = Path(__file__).resolve().parents[3]
        if script_id:
            video_output_dir = base_dir / script_id / "generated_images"
        else:
            video_output_dir = base_dir / "generated_images"
        video_output_dir.mkdir(parents=True, exist_ok=True)
        return video_output_dir

    def _path_to_base64_url(self, file_path: Path) -> str:
        """将本地文件转换为base64 data URL，直接嵌入图片内容"""
        import base64
        import mimetypes

        try:
            with open(file_path, "rb") as f:
                file_content = f.read()

            mime_type, _ = mimetypes.guess_type(str(file_path))
            if not mime_type:
                mime_type = "image/png"

            b64_str = base64.b64encode(file_content).decode("utf-8")
            return f"data:{mime_type};base64,{b64_str}"
        except Exception as e:
            logger.error(f"读取或编码图片失败: {file_path}, 错误: {e}")
            return ""

    def generate_video(
        self,
        shot_no: int,
        shotlist_text: str,
        script_id: str,
        script_name: str,
        global_seed: int = 0,
    ) -> Dict[str, Any]:
        """
        为指定分镜生成视频

        参数:
        - shot_no: 分镜号
        - shotlist_text: 分镜头脚本内容
        - script_id: 剧本ID
        - script_name: 剧本名称
        - global_seed: 全局统一seed

        返回:
        - {"shot_no": shot_no, "video_path": "xxx"} 或 {"shot_no": shot_no, "error": "xxx"}
        """
        logger.info(f"开始为分镜{shot_no}生成视频，剧本: {script_name}")

        video_output_dir = self._get_video_output_dir(script_id)
        logger.info(f"视频输出目录: {video_output_dir}")

        try:
            grid_image_path = self._find_grid_image(video_output_dir, shot_no)
            if not grid_image_path:
                logger.warning(f"分镜{shot_no}的六宫格图片不存在，无法生成视频")
                return {"shot_no": shot_no, "video_path": "", "error": "六宫格图片不存在，请先生成六宫格图片"}

            grid_image_base64 = self._path_to_base64_url(grid_image_path)
            if not grid_image_base64:
                logger.warning(f"分镜{shot_no}的六宫格图片base64编码失败")
                return {"shot_no": shot_no, "video_path": "", "error": "六宫格图片base64编码失败"}

            prompt = f"请根据我给你的六宫格分镜头图片，以及分镜头脚本{shotlist_text}生成视频，保持角色、场景、整体风格氛围和图片中一致，运镜流畅恰当。重要：当场景中有多个角色对话时，角色之间必须自然地对视，目光看向对话对象而非镜头，呈现真实的对话视线关系"

            video_path = self._generate_video_with_api(
                prompt=prompt,
                grid_image_base64=grid_image_base64,
                shot_no=shot_no,
                script_name=script_name,
                video_output_dir=video_output_dir,
            )

            if video_path:
                logger.info(f"分镜{shot_no}的视频生成完成，视频路径: {video_path}")
                return {"shot_no": shot_no, "video_path": video_path}
            else:
                logger.warning(f"分镜{shot_no}的视频生成失败")
                return {"shot_no": shot_no, "video_path": "", "error": "视频生成失败"}

        except Exception as e:
            logger.error(f"视频生成失败: {str(e)}")
            return {"shot_no": shot_no, "video_path": "", "error": str(e)}

    def _find_grid_image(self, video_output_dir: Path, shot_no: int) -> str:
        """查找指定分镜的六宫格图片"""
        grid_images = list(video_output_dir.glob(f"{shot_no}_*_jgg.png"))
        if grid_images:
            grid_images.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            logger.info(f"找到分镜{shot_no}的六宫格图片: {grid_images[0]}")
            return str(grid_images[0])
        return ""

    def _generate_video_with_api(
        self,
        prompt: str,
        grid_image_base64: str,
        shot_no: int,
        script_name: str,
        video_output_dir: Path,
    ) -> str:
        """使用DashScope VideoSynthesis API (wan2.7-r2v模型) 生成视频并保存到本地"""
        try:
            from dashscope import VideoSynthesis
            from http import HTTPStatus

            dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

            media = [
                {
                    "type": "reference_image",
                    "url": grid_image_base64,
                }
            ]

            logger.info(f"调用VideoSynthesis API生成视频，分镜: {shot_no}, 模型: wan2.7-r2v")

            rsp = VideoSynthesis.async_call(
                api_key=self.api_key,
                model='wan2.7-r2v',
                prompt=prompt,
                media=media,
                resolution='720P',
                duration=10,
                prompt_extend=False,
                watermark=True,
            )

            if rsp.status_code == HTTPStatus.OK:
                logger.info(f"视频生成任务已提交，task_id: {rsp.output.task_id}")
            else:
                error_code = getattr(rsp, 'code', 'UNKNOWN')
                error_message = getattr(rsp, 'message', 'Unknown error')
                logger.error(f"视频生成任务提交失败 - status_code: {rsp.status_code}, code: {error_code}, message: {error_message}")
                return ""

            logger.info(f"等待视频生成任务完成，task_id: {rsp.output.task_id}")
            rsp = VideoSynthesis.wait(task=rsp, api_key=self.api_key)

            if rsp.status_code == HTTPStatus.OK:
                video_url = rsp.output.video_url
                logger.info(f"视频生成完成，视频URL: {video_url}")
                return self._download_and_save_video(
                    video_url, shot_no, script_name, video_output_dir
                )
            else:
                error_code = getattr(rsp, 'code', 'UNKNOWN')
                error_message = getattr(rsp, 'message', 'Unknown error')
                logger.error(f"视频生成失败 - status_code: {rsp.status_code}, code: {error_code}, message: {error_message}")
                return ""

        except Exception as e:
            logger.error(f"调用VideoSynthesis API失败: {str(e)}")
            return ""

    def _download_and_save_video(
        self,
        video_url: str,
        shot_no: int,
        script_name: str,
        video_output_dir: Path,
    ) -> str:
        """下载视频并保存到本地"""
        try:
            import urllib.request

            old_video_pattern = f"{script_name}_{shot_no}_*.mp4"
            for old_video in video_output_dir.glob(old_video_pattern):
                try:
                    old_video.unlink()
                    logger.info(f"已删除旧的视频: {old_video}")
                except Exception as e:
                    logger.warning(f"删除旧视频失败: {old_video}, 错误: {e}")

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            file_name = f"{script_name}_{shot_no}_{timestamp}.mp4"
            file_path = video_output_dir / file_name

            urllib.request.urlretrieve(video_url, str(file_path))
            logger.info(f"视频已保存到: {file_path}")
            return str(file_path)
        except Exception as e:
            logger.error(f"下载或保存视频失败: {e}")
            return ""

    def _find_first_frame_image(self, video_output_dir: Path, shot_no: int) -> str:
        """查找指定分镜的首帧图"""
        first_frame_images = list(video_output_dir.glob(f"*_{shot_no}_*_ff.png"))
        if first_frame_images:
            first_frame_images.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            logger.info(f"找到分镜{shot_no}的首帧图: {first_frame_images[0]}")
            return str(first_frame_images[0])
        return ""

    def generate_video_from_first_frame(
        self,
        shot_no: int,
        shotlist_text: str,
        script_id: str,
        script_name: str,
        global_seed: int = 0,
    ) -> Dict[str, Any]:
        """
        基于首帧图生成视频（使用doubao-seedance-1-0-pro-250528模型）

        参数:
        - shot_no: 分镜号
        - shotlist_text: 分镜头脚本内容
        - script_id: 剧本ID
        - script_name: 剧本名称
        - global_seed: 全局统一seed

        返回:
        - {"shot_no": shot_no, "video_path": "xxx"} 或 {"shot_no": shot_no, "error": "xxx"}
        """
        logger.info(f"开始为分镜{shot_no}基于首帧图生成视频（seedance），剧本: {script_name}")

        if not self.ark_api_key:
            logger.error("未设置ARK_API_KEY，无法使用seedance模型")
            return {"shot_no": shot_no, "video_path": "", "error": "未设置ARK_API_KEY，请在.env文件中配置"}

        video_output_dir = self._get_video_output_dir(script_id)
        logger.info(f"视频输出目录: {video_output_dir}")

        try:
            first_frame_image_path = self._find_first_frame_image(video_output_dir, shot_no)
            if not first_frame_image_path:
                logger.warning(f"分镜{shot_no}的首帧图不存在，无法生成视频")
                return {"shot_no": shot_no, "video_path": "", "error": "首帧图不存在，请先生成首帧图"}

            first_frame_base64 = self._path_to_base64_url(Path(first_frame_image_path))
            if not first_frame_base64:
                logger.warning(f"分镜{shot_no}的首帧图base64编码失败")
                return {"shot_no": shot_no, "video_path": "", "error": "首帧图base64编码失败"}

            prompt = f"请根据首帧图片和分镜头脚本{shotlist_text}生成视频，保持角色、场景、整体风格氛围和图片中一致，运镜流畅恰当。重要：当场景中有多个角色对话时，角色之间必须自然地对视，目光看向对话对象而非镜头，呈现真实的对话视线关系 --resolution 480p --duration 10 --camerafixed false --watermark false"

            video_path = self._generate_video_with_seedance_api(
                prompt=prompt,
                first_frame_base64=first_frame_base64,
                shot_no=shot_no,
                script_name=script_name,
                video_output_dir=video_output_dir,
            )

            if video_path:
                logger.info(f"分镜{shot_no}的seedance视频生成完成，视频路径: {video_path}")
                return {"shot_no": shot_no, "video_path": video_path}
            else:
                logger.warning(f"分镜{shot_no}的seedance视频生成失败")
                return {"shot_no": shot_no, "video_path": "", "error": "seedance视频生成失败"}

        except Exception as e:
            logger.error(f"seedance视频生成失败: {str(e)}")
            return {"shot_no": shot_no, "video_path": "", "error": str(e)}

    def _generate_video_with_seedance_api(
        self,
        prompt: str,
        first_frame_base64: str,
        shot_no: int,
        script_name: str,
        video_output_dir: Path,
    ) -> str:
        """使用火山方舟 Ark SDK (doubao-seedance-1-0-pro-250528模型) 基于首帧图生成视频"""
        import time

        try:
            from volcenginesdkarkruntime import Ark

            client = Ark(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key=self.ark_api_key,
            )

            logger.info(f"调用Seedance API生成视频，分镜: {shot_no}, 模型: doubao-seedance-1-0-pro-250528")

            create_result = client.content_generation.tasks.create(
                model="doubao-seedance-1-0-pro-250528",
                content=[
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": first_frame_base64,
                        },
                    },
                ],
            )

            task_id = create_result.id
            logger.info(f"Seedance视频生成任务已提交，task_id: {task_id}")

            max_wait_time = 1200
            start_time = time.time()
            poll_interval = 30

            while True:
                elapsed = time.time() - start_time
                if elapsed > max_wait_time:
                    logger.error(f"Seedance视频生成超时，task_id: {task_id}，已等待{elapsed:.0f}秒")
                    return ""

                get_result = client.content_generation.tasks.get(task_id=task_id)
                status = get_result.status

                if status == "succeeded":
                    logger.info(f"Seedance视频生成成功，task_id: {task_id}")
                    video_url = None
                    if hasattr(get_result, 'content') and get_result.content:
                        content = get_result.content
                        if hasattr(content, 'video_url') and content.video_url:
                            video_url = content.video_url

                    if video_url:
                        logger.info(f"Seedance视频URL: {video_url}")
                        return self._download_and_save_video(
                            video_url, shot_no, script_name, video_output_dir
                        )
                    else:
                        logger.error(f"Seedance视频生成成功但未获取到视频URL，result: {get_result}")
                        return ""

                elif status == "failed":
                    error_info = getattr(get_result, 'error', 'Unknown error')
                    logger.error(f"Seedance视频生成失败，task_id: {task_id}，error: {error_info}")
                    return ""

                else:
                    logger.info(f"Seedance视频生成中，task_id: {task_id}，status: {status}，已等待{elapsed:.0f}秒")
                    time.sleep(poll_interval)

        except ImportError:
            logger.error("未安装volcenginesdkarkruntime，请运行: pip install 'volcengine-python-sdk[ark]'")
            return ""
        except Exception as e:
            logger.error(f"调用Seedance API失败: {str(e)}")
            return ""

    def _find_last_frame_image(self, video_output_dir: Path, shot_no: int) -> str:
        """查找指定分镜的尾帧图"""
        last_frame_images = list(video_output_dir.glob(f"*_{shot_no}_*_lf.png"))
        if last_frame_images:
            last_frame_images.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            logger.info(f"找到分镜{shot_no}的尾帧图: {last_frame_images[0]}")
            return str(last_frame_images[0])
        return ""

    def generate_video_from_first_and_last_frame(
        self,
        shot_no: int,
        shotlist_text: str,
        script_id: str,
        script_name: str,
        global_seed: int = 0,
    ) -> Dict[str, Any]:
        """
        基于首帧图和尾帧图生成视频（使用doubao-seedance-1-0-pro-250528模型）

        参数:
        - shot_no: 分镜号
        - shotlist_text: 分镜头脚本内容
        - script_id: 剧本ID
        - script_name: 剧本名称
        - global_seed: 全局统一seed

        返回:
        - {"shot_no": shot_no, "video_path": "xxx"} 或 {"shot_no": shot_no, "error": "xxx"}
        """
        logger.info(f"开始为分镜{shot_no}基于首尾帧生成视频（seedance），剧本: {script_name}")

        if not self.ark_api_key:
            logger.error("未设置ARK_API_KEY，无法使用seedance模型")
            return {"shot_no": shot_no, "video_path": "", "error": "未设置ARK_API_KEY，请在.env文件中配置"}

        video_output_dir = self._get_video_output_dir(script_id)
        logger.info(f"视频输出目录: {video_output_dir}")

        try:
            first_frame_image_path = self._find_first_frame_image(video_output_dir, shot_no)
            if not first_frame_image_path:
                logger.warning(f"分镜{shot_no}的首帧图不存在，无法生成视频")
                return {"shot_no": shot_no, "video_path": "", "error": "首帧图不存在，请先生成首帧图"}

            first_frame_base64 = self._path_to_base64_url(Path(first_frame_image_path))
            if not first_frame_base64:
                logger.warning(f"分镜{shot_no}的首帧图base64编码失败")
                return {"shot_no": shot_no, "video_path": "", "error": "首帧图base64编码失败"}

            last_frame_image_path = self._find_last_frame_image(video_output_dir, shot_no)
            if not last_frame_image_path:
                logger.warning(f"分镜{shot_no}的尾帧图不存在，无法生成视频")
                return {"shot_no": shot_no, "video_path": "", "error": "尾帧图不存在，请先生成尾帧图"}

            last_frame_base64 = self._path_to_base64_url(Path(last_frame_image_path))
            if not last_frame_base64:
                logger.warning(f"分镜{shot_no}的尾帧图base64编码失败")
                return {"shot_no": shot_no, "video_path": "", "error": "尾帧图base64编码失败"}

            prompt = f"请根据首帧图和尾帧图，以及分镜头脚本{shotlist_text}生成视频，视频需从首帧画面平滑过渡到尾帧画面，保持角色、场景、整体风格氛围与首尾帧一致，运镜流畅恰当。重要：当场景中有多个角色对话时，角色之间必须自然地对视，目光看向对话对象而非镜头，呈现真实的对话视线关系 --resolution 480p --duration 10 --camerafixed false --watermark false"

            video_path = self._generate_video_with_seedance_first_last_frame_api(
                prompt=prompt,
                first_frame_base64=first_frame_base64,
                last_frame_base64=last_frame_base64,
                shot_no=shot_no,
                script_name=script_name,
                video_output_dir=video_output_dir,
            )

            if video_path:
                logger.info(f"分镜{shot_no}的seedance首尾帧视频生成完成，视频路径: {video_path}")
                return {"shot_no": shot_no, "video_path": video_path}
            else:
                logger.warning(f"分镜{shot_no}的seedance首尾帧视频生成失败")
                return {"shot_no": shot_no, "video_path": "", "error": "seedance首尾帧视频生成失败"}

        except Exception as e:
            logger.error(f"seedance首尾帧视频生成失败: {str(e)}")
            return {"shot_no": shot_no, "video_path": "", "error": str(e)}

    def _generate_video_with_seedance_first_last_frame_api(
        self,
        prompt: str,
        first_frame_base64: str,
        last_frame_base64: str,
        shot_no: int,
        script_name: str,
        video_output_dir: Path,
    ) -> str:
        """使用火山方舟 Ark SDK (doubao-seedance-1-0-pro-250528模型) 基于首尾帧生成视频"""
        import time

        try:
            from volcenginesdkarkruntime import Ark

            client = Ark(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key=self.ark_api_key,
            )

            logger.info(f"调用Seedance首尾帧API生成视频，分镜: {shot_no}, 模型: doubao-seedance-1-0-pro-250528")

            create_result = client.content_generation.tasks.create(
                model="doubao-seedance-1-0-pro-250528",
                content=[
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "role": "first_frame",
                        "image_url": {
                            "url": first_frame_base64,
                        },
                    },
                    {
                        "type": "image_url",
                        "role": "last_frame",
                        "image_url": {
                            "url": last_frame_base64,
                        },
                    },
                ],
            )

            task_id = create_result.id
            logger.info(f"Seedance首尾帧视频生成任务已提交，task_id: {task_id}")

            max_wait_time = 1200
            start_time = time.time()
            poll_interval = 30

            while True:
                elapsed = time.time() - start_time
                if elapsed > max_wait_time:
                    logger.error(f"Seedance首尾帧视频生成超时，task_id: {task_id}，已等待{elapsed:.0f}秒")
                    return ""

                get_result = client.content_generation.tasks.get(task_id=task_id)
                status = get_result.status

                if status == "succeeded":
                    logger.info(f"Seedance首尾帧视频生成成功，task_id: {task_id}")
                    video_url = None
                    if hasattr(get_result, 'content') and get_result.content:
                        content = get_result.content
                        if hasattr(content, 'video_url') and content.video_url:
                            video_url = content.video_url

                    if video_url:
                        logger.info(f"Seedance首尾帧视频URL: {video_url}")
                        return self._download_and_save_video(
                            video_url, shot_no, script_name, video_output_dir
                        )
                    else:
                        logger.error(f"Seedance首尾帧视频生成成功但未获取到视频URL，result: {get_result}")
                        return ""

                elif status == "failed":
                    error_info = getattr(get_result, 'error', 'Unknown error')
                    logger.error(f"Seedance首尾帧视频生成失败，task_id: {task_id}，error: {error_info}")
                    return ""

                else:
                    logger.info(f"Seedance首尾帧视频生成中，task_id: {task_id}，status: {status}，已等待{elapsed:.0f}秒")
                    time.sleep(poll_interval)

        except ImportError:
            logger.error("未安装volcenginesdkarkruntime，请运行: pip install 'volcengine-python-sdk[ark]'")
            return ""
        except Exception as e:
            logger.error(f"调用Seedance首尾帧API失败: {str(e)}")
            return ""
