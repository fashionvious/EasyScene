"""
场景背景图生成Agent节点
为每个场景组生成背景参考图
"""
import os
import logging
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from openai import OpenAI

from app.agent.generatePic.llm_client import HelloAgentsLLM
from app.agent.generateVideo.seed_manager import derive_scene_seed

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


class SceneBackgroundAgent:
    """场景背景图生成Agent节点"""

    def __init__(self, llm_client: HelloAgentsLLM, script_id: str = None):
        self.llm_client = llm_client
        self.script_id = script_id
        api_key = os.getenv("ARK_API_KEY")
        if not api_key:
            raise ValueError("请在.env文件中设置ARK_API_KEY")
        self.client = OpenAI(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=api_key,
        )

    def _get_image_output_dir(self, script_id: str) -> Path:
        """根据 script_id 获取图片输出目录"""
        base_dir = Path(__file__).resolve().parents[3]
        if script_id:
            image_output_dir = base_dir / script_id / "generated_images"
        else:
            image_output_dir = base_dir / "generated_images"
        image_output_dir.mkdir(parents=True, exist_ok=True)
        return image_output_dir

    def generate_background_for_scene_group(
        self,
        scene_group_no: int,
        scene_name: str,
        shot_scripts: List[Dict[str, Any]],
        script_id: str,
        global_seed: int = 0,
    ) -> Dict[str, Any]:
        """
        为指定场景组生成背景图

        参数:
        - scene_group_no: 场景组号
        - scene_name: 场景名称
        - shot_scripts: 该场景组下的所有分镜脚本
        - script_id: 剧本ID
        - global_seed: 全局统一seed，用于派生场景确定性seed

        返回:
        - {"scene_group": scene_group_no, "scene_name": scene_name, "background_image_path": "xxx"}
        """
        logger.info(f"开始为场景组{scene_group_no}生成背景图，场景名称: {scene_name}")

        image_output_dir = self._get_image_output_dir(script_id)
        logger.info(f"图片输出目录: {image_output_dir}")

        try:
            combined_shot_script = self._combine_shot_scripts(shot_scripts)
            logger.info(f"合并后的分镜内容长度: {len(combined_shot_script)}")

            background_prompt = self._generate_background_prompt(combined_shot_script, scene_name)
            if not background_prompt:
                logger.warning(f"场景组{scene_group_no}的背景图提示词生成失败")
                return {
                    "scene_group": scene_group_no,
                    "scene_name": scene_name,
                    "background_image_path": ""
                }

            logger.info(f"生成的背景图提示词: {background_prompt}")

            scene_seed = derive_scene_seed(global_seed, scene_group_no)
            image_path = self._generate_background_image(
                background_prompt,
                scene_group_no,
                image_output_dir,
                scene_seed
            )

            if image_path:
                logger.info(f"场景组{scene_group_no}的背景图生成完成，图片路径: {image_path}")
            else:
                logger.warning(f"场景组{scene_group_no}的背景图生成失败")

            return {
                "scene_group": scene_group_no,
                "scene_name": scene_name,
                "background_image_path": image_path or ""
            }

        except Exception as e:
            logger.error(f"场景背景图生成失败: {str(e)}")
            return {
                "scene_group": scene_group_no,
                "scene_name": scene_name,
                "background_image_path": "",
                "error": str(e)
            }

    def _combine_shot_scripts(self, shot_scripts: List[Dict[str, Any]]) -> str:
        """合并场景组下所有分镜内容"""
        combined = []
        for shot in shot_scripts:
            total_script = shot.get("total_script", "")
            if total_script:
                combined.append(total_script)
        return "\n".join(combined)

    def _generate_background_prompt(self, shot_script: str, scene_name: str) -> str:
        """使用LLM生成背景图提示词"""
        prompt = f"""你是一个专业的电影场景设计师和AI绘图提示词工程师。

# 任务目标
根据分镜头脚本描述，生成一个高质量的场景背景图提示词。这个场景图将作为AI视频生成的背景参考。

# 场景信息
场景名称：{scene_name}
分镜描述：{shot_script}

# 核心要求
1. **场景氛围**：准确捕捉分镜描述中的时间、天气、光线、情绪氛围
2. **环境细节**：提取并强化环境中的关键元素（建筑、道路、植被、道具等）
3. **构图视角**：根据分镜描述确定合适的镜头角度和景深
4. **色彩基调**：根据场景氛围确定整体色调（暖调/冷调/高对比度等）
5. **风格统一**：保持与电影级画面风格一致，避免卡通化或过度艺术化

# 技术规范
- 画布比例：16:9（横屏）2688*1536
- 画面质量：高清、细节丰富、无噪点
- 风格：电影质感
- 光影：自然真实，符合物理规律

# 输出要求
直接输出一段不超过200字的中文提示词，格式要求：
1. 以场景氛围和风格开头
2. 详细描述环境元素和构图
3. 包含光影和色彩基调
4. 以技术参数结尾

# 示例
输入场景：昏暗的小巷，夜晚，雨天
输出提示词：电影级夜景氛围，昏暗潮湿的小巷，两侧是斑驳的砖墙，地面有积水反光，远处路灯昏黄，雨丝斜飘，冷色调为主，高对比度，广角镜头，景深适中，16:9画幅，4K高清画质，虚幻引擎5渲染，真实光影。

请根据上述要求，为当前场景生成提示词，可以直接复制使用"""

        messages = [{"role": "user", "content": prompt}]

        try:
            response = self.llm_client.think(messages=messages)
            return response.strip() if response else ""
        except Exception as e:
            logger.error(f"生成背景图提示词失败: {str(e)}")
            return ""

    def _generate_background_image(self, prompt: str, scene_group_no: int, image_output_dir: Path, seed: int = None) -> str:
        """使用豆包 Seedream API 生成背景图并保存到本地"""
        try:
            logger.info(f"调用豆包 Seedream API生成背景图，场景组: {scene_group_no}")

            extra_body: dict[str, Any] = {
                "watermark": False,
                "sequential_image_generation": "disabled",
            }

            if seed is not None:
                extra_body["seed"] = seed
                logger.info(f"场景组 {scene_group_no} 背景图生成使用seed: {seed}")

            response = self.client.images.generate(
                model="doubao-seedream-4-5-251128",
                prompt=prompt,
                size="2K",
                extra_body=extra_body,
            )

            if response.data and len(response.data) > 0:
                image_url = response.data[0].url
                if image_url:
                    logger.info(f"成功获取背景图 URL: {image_url}")
                    return self._download_and_save_background_image(image_url, scene_group_no, image_output_dir)

            logger.error(f"背景图生成失败：响应中无图片数据")
            return ""

        except Exception as e:
            logger.error(f"调用豆包 Seedream API失败: {str(e)}")
            return ""

    def _download_and_save_background_image(self, image_url: str, scene_group_no: int, image_output_dir: Path) -> str:
        """下载背景图并保存到本地"""
        try:
            old_image_pattern = f"{scene_group_no}_*_bgbg.png"
            for old_image in image_output_dir.glob(old_image_pattern):
                try:
                    old_image.unlink()
                    logger.info(f"已删除旧的背景图: {old_image}")
                except Exception as e:
                    logger.warning(f"删除旧图片失败: {old_image}, 错误: {e}")

            img_response = requests.get(image_url, timeout=30)
            img_response.raise_for_status()

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            image_filename = f"{scene_group_no}_{timestamp}_bgbg.png"
            image_path = image_output_dir / image_filename

            with open(image_path, "wb") as f:
                f.write(img_response.content)

            logger.info(f"场景组{scene_group_no}的背景图已保存到: {image_path}")
            return str(image_path)
        except Exception as e:
            logger.error(f"下载或保存背景图失败: {e}")
            return ""
