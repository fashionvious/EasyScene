"""
首帧图与尾帧图生成Agent节点
为每个分镜头生成首帧图（即六宫格中的Panel 1）和尾帧图（即六宫格中的最后一帧）
"""
import os
import logging
import dashscope
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from app.agent.generatePic.llm_client import HelloAgentsLLM
from app.agent.generateVideo.seed_manager import derive_first_frame_seed

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


class FirstAndLastFrameAgent:
    """首帧图与尾帧图生成Agent节点 - 生成六宫格中的Panel 1（首帧）和最后一帧（尾帧）"""

    def __init__(self, llm_client: HelloAgentsLLM, script_id: str = None):
        self.llm_client = llm_client
        self.script_id = script_id
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError("请在.env文件中设置DASHSCOPE_API_KEY")

    def _get_image_output_dir(self, script_id: str) -> Path:
        """根据 script_id 获取图片输出目录"""
        base_dir = Path(__file__).resolve().parents[3]
        if script_id:
            image_output_dir = base_dir / script_id / "generated_images"
        else:
            image_output_dir = base_dir / "generated_images"
        image_output_dir.mkdir(parents=True, exist_ok=True)
        return image_output_dir

    def generate_first_frame_image(
        self,
        shot_no: int,
        shot_script_text: str,
        scene_group_no: int,
        script_id: str,
        script_name: str,
        global_seed: int = 0,
    ) -> Dict[str, Any]:
        """
        为指定分镜生成首帧图（即六宫格中的Panel 1）

        参数:
        - shot_no: 分镜号
        - shot_script_text: 分镜脚本内容
        - scene_group_no: 场景组号，用于匹配场景背景图
        - script_id: 剧本ID
        - script_name: 剧本名称
        - global_seed: 全局统一seed，用于派生首帧图确定性seed

        返回:
        - {"shot_no": shot_no, "first_frame_image_path": "xxx"} 或 {"shot_no": shot_no, "error": "xxx"}
        """
        logger.info(f"开始为分镜{shot_no}生成首帧图，场景组: {scene_group_no}")

        image_output_dir = self._get_image_output_dir(script_id)
        logger.info(f"图片输出目录: {image_output_dir}")

        try:
            first_frame_prompt = self._generate_first_frame_prompt(shot_script_text)
            if not first_frame_prompt:
                logger.warning(f"分镜{shot_no}的首帧图提示词生成失败")
                return {"shot_no": shot_no, "first_frame_image_path": "", "error": "首帧图提示词生成失败"}

            logger.info(f"生成的首帧图提示词: {first_frame_prompt}")

            background_images, character_images = self._collect_reference_images(
                image_output_dir, scene_group_no, script_id
            )
            logger.info(f"场景背景图数量: {len(background_images)}, 角色四视图数量: {len(character_images)}")

            first_frame_seed = derive_first_frame_seed(global_seed, shot_no)
            image_path = self._generate_first_frame_image_with_api(
                first_frame_prompt, background_images, character_images,
                shot_no, scene_group_no, script_name, image_output_dir, first_frame_seed
            )

            if image_path:
                logger.info(f"分镜{shot_no}的首帧图生成完成，图片路径: {image_path}")
                return {"shot_no": shot_no, "first_frame_image_path": image_path}
            else:
                logger.warning(f"分镜{shot_no}的首帧图生成失败")
                return {"shot_no": shot_no, "first_frame_image_path": "", "error": "首帧图生成失败"}

        except Exception as e:
            logger.error(f"首帧图生成失败: {str(e)}")
            return {"shot_no": shot_no, "first_frame_image_path": "", "error": str(e)}

    def generate_last_frame_image(
        self,
        shot_no: int,
        shot_script_text: str,
        scene_group_no: int,
        script_id: str,
        script_name: str,
        first_frame_image_path: str,
        global_seed: int = 0,
    ) -> Dict[str, Any]:
        """
        为指定分镜生成尾帧图（即视频的最后一帧）

        参数:
        - shot_no: 分镜号
        - shot_script_text: 分镜脚本内容
        - scene_group_no: 场景组号，用于匹配场景背景图
        - script_id: 剧本ID
        - script_name: 剧本名称
        - first_frame_image_path: 当前分镜的首帧图路径，用于保证场景一致性
        - global_seed: 全局统一seed，用于派生尾帧图确定性seed

        返回:
        - {"shot_no": shot_no, "last_frame_image_path": "xxx"} 或 {"shot_no": shot_no, "error": "xxx"}
        """
        logger.info(f"开始为分镜{shot_no}生成尾帧图，场景组: {scene_group_no}")

        image_output_dir = self._get_image_output_dir(script_id)
        logger.info(f"图片输出目录: {image_output_dir}")

        try:
            last_frame_prompt = self._generate_last_frame_prompt(shot_script_text)
            if not last_frame_prompt:
                logger.warning(f"分镜{shot_no}的尾帧图提示词生成失败")
                return {"shot_no": shot_no, "last_frame_image_path": "", "error": "尾帧图提示词生成失败"}

            logger.info(f"生成的尾帧图提示词: {last_frame_prompt}")

            background_images, character_images = self._collect_reference_images(
                image_output_dir, scene_group_no, script_id
            )
            logger.info(f"场景背景图数量: {len(background_images)}, 角色四视图数量: {len(character_images)}")

            first_frame_ref = self._path_to_base64_url(Path(first_frame_image_path)) if first_frame_image_path else ""
            if not first_frame_ref:
                logger.warning(f"分镜{shot_no}的首帧图读取失败，无法作为尾帧图参考")

            first_frame_seed = derive_first_frame_seed(global_seed, shot_no)
            last_frame_seed = first_frame_seed + 1 if first_frame_seed < 2147483647 else first_frame_seed

            image_path = self._generate_last_frame_image_with_api(
                last_frame_prompt, background_images, character_images,
                first_frame_ref, shot_no, scene_group_no, script_name,
                image_output_dir, last_frame_seed
            )

            if image_path:
                logger.info(f"分镜{shot_no}的尾帧图生成完成，图片路径: {image_path}")
                return {"shot_no": shot_no, "last_frame_image_path": image_path}
            else:
                logger.warning(f"分镜{shot_no}的尾帧图生成失败")
                return {"shot_no": shot_no, "last_frame_image_path": "", "error": "尾帧图生成失败"}

        except Exception as e:
            logger.error(f"尾帧图生成失败: {str(e)}")
            return {"shot_no": shot_no, "last_frame_image_path": "", "error": str(e)}

    def _generate_first_frame_prompt(self, shot_script_text: str) -> str:
        """使用LLM生成首帧图提示词（只生成Panel 1，即六宫格中的第一个画面）"""
        prompt = f"""你是一位拥有顶级工业标准的影视级分镜导演。你的任务是根据我提供的【分镜头脚本】和【角色四视图参考图】，生成高质量的首帧图提示词。首帧图就是分镜头故事板中的第一个画面（Panel 1），代表该分镜的起始画面。

【核心规则】
1. 画面规格：单张图片，画幅严格为 16:9，8:3的单格画幅。只生成一个画面，不要生成多宫格故事板。
2. 参考图死锁（最重要！）：用户会提供某角色的四视图参考图。你必须明白：每张四视图是【一个角色的不同角度】，而不是4个不同的人！首帧图中的角色必须且只能严格按照角色四视图中的形象生成。严禁参考其他人物，严禁自由发挥改变角色外观！
3. 角色DNA提取：仔细观察提供的四视图，提炼出该角色最不易走样的核心防变形锚点（即角色DNA），如：特定发色与发型、瞳色、标志性面部特征、核心服饰款式与颜色。这些锚点必须在提示词中明确写出，用于压制AI的随机性。
4. 首帧动作推演：根据分镜头脚本，推演出该分镜起始时刻的画面。首帧应该是动作的起点、场景的初始状态，为后续动作提供视觉锚点。
5. 视觉风格：优先分析脚本适合的风格，信息不足时默认使用：影视级光影、2K分辨率、高精度渲染（C4D Octane Render / 高质量写实风格）。

【输入脚本】
{shot_script_text}

【输出要求】
1. 生成一段约 100-150 字的、连贯的、可直接喂给AI的中文提示词。
2. 提示词末尾必须加上参数 --ar 16:9。
3. 严禁输出任何解释性废话，只按以下格式模板输出：
cinematic first frame, single panel, 16:9 aspect ratio, 
CRITICAL RULE: strictly consistent character design from reference images, no character drift, strictly follow reference images.
[景别]+[核心动作与起始状态]
角色DNA: [极其具体的面部/发型/服饰锚点], [环境与背景], [光源与风格] --ar 16:9

【极简原则】
画面描述不超过50个字，只写【景别】和【人物及其核心动作/起始状态】，角色特征全部放在"角色DNA"中。"""

        messages = [{"role": "user", "content": prompt}]

        try:
            response = self.llm_client.think(messages=messages)
            return response.strip() if response else ""
        except Exception as e:
            logger.error(f"生成首帧图提示词失败: {str(e)}")
            return ""

    def _generate_last_frame_prompt(self, shot_script_text: str) -> str:
        """使用LLM生成尾帧图提示词（视频的最后一帧画面）"""
        prompt = f"""你是一位拥有顶级工业标准的影视级分镜导演。你的任务是根据我提供的【分镜头脚本】、【角色四视图参考图】和【首帧图参考图】，生成高质量的尾帧图提示词。尾帧图就是分镜头视频播放结束时的最后一帧画面，代表该分镜的结束状态。

【核心规则】
1. 画面规格：单张图片，画幅严格为 16:9，8:3的单格画幅。只生成一个画面，不要生成多宫格故事板。
2. 参考图死锁（最重要！）：用户会提供某角色的四视图参考图。你必须明白：每张四视图是【一个角色的不同角度】，而不是4个不同的人！尾帧图中的角色必须且只能严格按照角色四视图中的形象生成。严禁参考其他人物，严禁自由发挥改变角色外观！
3. 场景一致性（极其重要！）：用户会提供当前分镜的首帧图作为参考。尾帧图必须与首帧图保持严格的场景一致性——相同的场景背景、相同的光照环境、相同的角色外观。尾帧图只是在首帧图基础上展示动作结束后的状态，场景和角色绝不能漂移！
4. 角色DNA提取：仔细观察提供的四视图，提炼出该角色最不易走样的核心防变形锚点（即角色DNA），如：特定发色与发型、瞳色、标志性面部特征、核心服饰款式与颜色。这些锚点必须在提示词中明确写出，用于压制AI的随机性。
5. 尾帧动作推演：根据分镜头脚本，推演出该分镜结束时刻的画面。尾帧应该是动作的终点、场景的最终状态，与首帧图形成动作的起止呼应。根据脚本中的动作描述，推演角色在动作完成后的姿态、位置和表情变化。
6. 视觉风格：必须与首帧图保持完全一致的视觉风格。优先分析脚本适合的风格，信息不足时默认使用：影视级光影、2K分辨率、高精度渲染（C4D Octane Render / 高质量写实风格）。

【输入脚本】
{shot_script_text}

【输出要求】
1. 生成一段约 100-150 字的、连贯的、可直接喂给AI的中文提示词。
2. 提示词末尾必须加上参数 --ar 16:9。
3. 严禁输出任何解释性废话，只按以下格式模板输出：
cinematic last frame, single panel, 16:9 aspect ratio, 
CRITICAL RULE: strictly consistent scene and character with the provided first frame reference image, no scene drift, no character drift, strictly follow reference images.
[景别]+[核心动作与结束状态]
角色DNA: [极其具体的面部/发型/服饰锚点], [与首帧一致的环境与背景], [与首帧一致的光源与风格] --ar 16:9

【极简原则】
画面描述不超过50个字，只写【景别】和【人物及其核心动作/结束状态】，角色特征全部放在"角色DNA"中。"""

        messages = [{"role": "user", "content": prompt}]

        try:
            response = self.llm_client.think(messages=messages)
            return response.strip() if response else ""
        except Exception as e:
            logger.error(f"生成尾帧图提示词失败: {str(e)}")
            return ""

    def _collect_reference_images(
        self,
        image_output_dir: Path,
        scene_group_no: int,
        script_id: str,
    ) -> tuple:
        """
        收集参考图片（场景背景图 + 角色四视图）
        使用base64编码将图片内容直接嵌入，避免file URL和HTTP URL的兼容性问题

        返回:
        - (background_images, character_images) 两个列表，元素为base64 data URL字符串
        """
        background_images = []
        for file in image_output_dir.glob(f"{scene_group_no}_*bgbg*"):
            if file.is_file():
                data_url = self._path_to_base64_url(file)
                if data_url:
                    background_images.append(data_url)
                    logger.info(f"找到场景背景图: {file.name} (base64编码)")

        character_images = []
        for file in image_output_dir.glob("*three_view*"):
            if file.is_file():
                data_url = self._path_to_base64_url(file)
                if data_url:
                    character_images.append(data_url)
                    logger.info(f"找到角色四视图: {file.name} (base64编码)")

        return background_images, character_images

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

    def _generate_first_frame_image_with_api(
        self,
        first_frame_prompt: str,
        background_images: list,
        character_images: list,
        shot_no: int,
        scene_group_no: int,
        script_name: str,
        image_output_dir: Path,
        seed: int = None,
    ) -> str:
        """使用ImageGeneration.call API生成首帧图并保存到本地"""
        try:
            from dashscope.aigc.image_generation import ImageGeneration
            from dashscope.api_entities.dashscope_response import Message

            dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

            content = [{"text": first_frame_prompt}]

            for img in background_images:
                content.append({"image": img})
            for img in character_images:
                content.append({"image": img})

            message = Message(role="user", content=content)

            logger.info(f"调用ImageGeneration API生成首帧图，参考图片数量: {len(background_images) + len(character_images)}")

            call_kwargs = dict(
                model="wan2.7-image-pro",
                api_key=self.api_key,
                messages=[message],
                enable_sequential=True,
                n=1,
                size='2688*1536',
            )

            if seed is not None:
                call_kwargs["seed"] = seed
                logger.info(f"分镜 {shot_no} 首帧图生成使用seed: {seed}")

            rsp = ImageGeneration.call(**call_kwargs)

            if rsp.status_code == 200:
                for i, choice in enumerate(rsp.output.choices):
                    for j, content_item in enumerate(choice["message"]["content"]):
                        if content_item.get("type") == "image":
                            image_url = content_item["image"]
                            return self._download_and_save_image(
                                image_url, shot_no, scene_group_no, script_name, image_output_dir, "ff"
                            )

                logger.error(f"响应中未找到图片URL: {rsp}")
                return ""
            else:
                error_code = getattr(rsp, 'code', 'UNKNOWN')
                error_message = getattr(rsp, 'message', 'Unknown error')
                logger.error(f"首帧图生成失败 - status_code: {rsp.status_code}, code: {error_code}, message: {error_message}")
                return ""

        except Exception as e:
            logger.error(f"调用ImageGeneration API失败: {str(e)}")
            return ""

    def _generate_last_frame_image_with_api(
        self,
        last_frame_prompt: str,
        background_images: list,
        character_images: list,
        first_frame_ref: str,
        shot_no: int,
        scene_group_no: int,
        script_name: str,
        image_output_dir: Path,
        seed: int = None,
    ) -> str:
        """使用ImageGeneration.call API生成尾帧图并保存到本地，额外上传首帧图作为参考"""
        try:
            from dashscope.aigc.image_generation import ImageGeneration
            from dashscope.api_entities.dashscope_response import Message

            dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

            content = [{"text": last_frame_prompt}]

            for img in background_images:
                content.append({"image": img})
            for img in character_images:
                content.append({"image": img})
            if first_frame_ref:
                content.append({"image": first_frame_ref})
                logger.info(f"已添加首帧图作为尾帧图生成的参考")

            message = Message(role="user", content=content)

            logger.info(f"调用ImageGeneration API生成尾帧图，参考图片数量: {len(background_images) + len(character_images) + (1 if first_frame_ref else 0)}")

            call_kwargs = dict(
                model="wan2.7-image-pro",
                api_key=self.api_key,
                messages=[message],
                enable_sequential=True,
                n=1,
                size='2688*1536',
            )

            if seed is not None:
                call_kwargs["seed"] = seed
                logger.info(f"分镜 {shot_no} 尾帧图生成使用seed: {seed}")

            rsp = ImageGeneration.call(**call_kwargs)

            if rsp.status_code == 200:
                for i, choice in enumerate(rsp.output.choices):
                    for j, content_item in enumerate(choice["message"]["content"]):
                        if content_item.get("type") == "image":
                            image_url = content_item["image"]
                            return self._download_and_save_image(
                                image_url, shot_no, scene_group_no, script_name, image_output_dir, "lf"
                            )

                logger.error(f"响应中未找到图片URL: {rsp}")
                return ""
            else:
                error_code = getattr(rsp, 'code', 'UNKNOWN')
                error_message = getattr(rsp, 'message', 'Unknown error')
                logger.error(f"尾帧图生成失败 - status_code: {rsp.status_code}, code: {error_code}, message: {error_message}")
                return ""

        except Exception as e:
            logger.error(f"调用ImageGeneration API失败: {str(e)}")
            return ""

    def _download_and_save_image(
        self, image_url: str, shot_no: int, scene_group_no: int, script_name: str, image_output_dir: Path, frame_type: str
    ) -> str:
        """
        下载图片并保存到本地
        文件命名格式：剧本名_场景组号_分镜号_时间戳_ff / 剧本名_场景组号_分镜号_时间戳_lf
        frame_type: "ff" 表示首帧图, "lf" 表示尾帧图
        """
        try:
            import urllib.request

            old_image_pattern = f"{script_name}_{scene_group_no}_{shot_no}_*_{frame_type}.png"
            for old_image in image_output_dir.glob(old_image_pattern):
                try:
                    old_image.unlink()
                    logger.info(f"已删除旧的{frame_type}图: {old_image}")
                except Exception as e:
                    logger.warning(f"删除旧图片失败: {old_image}, 错误: {e}")

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            file_name = f"{script_name}_{scene_group_no}_{shot_no}_{timestamp}_{frame_type}.png"
            file_path = image_output_dir / file_name

            urllib.request.urlretrieve(image_url, str(file_path))
            logger.info(f"{frame_type}图已保存到: {file_path}")
            return str(file_path)
        except Exception as e:
            logger.error(f"下载或保存{frame_type}图失败: {e}")
            return ""
