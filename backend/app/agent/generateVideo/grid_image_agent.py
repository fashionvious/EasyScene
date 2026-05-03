"""
六宫格图片生成Agent节点
为每个分镜头生成六宫格故事板图片
"""
import os
import logging
import requests
import dashscope
from dashscope import MultiModalConversation
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from app.agent.generatePic.llm_client import HelloAgentsLLM
from app.agent.generateVideo.seed_manager import derive_grid_seed

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


class GridImageAgent:
    """六宫格图片生成Agent节点"""
    
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

    def generate_grid_image(
        self,
        shot_no: int,
        shot_script_text: str,
        scene_group_no: int,
        script_id: str,
        global_seed: int = 0,
    ) -> Dict[str, Any]:
        """
        为指定分镜生成六宫格图片

        参数:
        - shot_no: 分镜号
        - shot_script_text: 分镜脚本内容
        - scene_group_no: 场景组号，用于匹配场景背景图
        - script_id: 剧本ID
        - global_seed: 全局统一seed，用于派生六宫格确定性seed

        返回:
        - {"shot_no": shot_no, "grid_image_path": "xxx"} 或 {"shot_no": shot_no, "error": "xxx"}
        """
        logger.info(f"开始为分镜{shot_no}生成六宫格图片，场景组: {scene_group_no}")

        image_output_dir = self._get_image_output_dir(script_id)
        logger.info(f"图片输出目录: {image_output_dir}")

        try:
            grid_prompt = self._generate_grid_prompt(shot_script_text)
            if not grid_prompt:
                logger.warning(f"分镜{shot_no}的六宫格提示词生成失败")
                return {"shot_no": shot_no, "grid_image_path": "", "error": "六宫格提示词生成失败"}

            logger.info(f"生成的六宫格提示词: {grid_prompt}")

            background_images, character_images = self._collect_reference_images(
                image_output_dir, scene_group_no, script_id
            )
            logger.info(f"场景背景图数量: {len(background_images)}, 角色四视图数量: {len(character_images)}")

            grid_seed = derive_grid_seed(global_seed, shot_no)
            image_path = self._generate_grid_image_with_api(
                "最重要的一点，请务必遵循：将下列6个panel根据描述分别生成一张图片，然后按顺序拼到一起构成一张分镜头故事板。严格限制为2列3行的布局（左边3个，右边3个），整体画幅为16:9，每个单格为8:3。严禁因为画幅是宽屏就自动生成第3列，必须且只能生成6个panel。"+grid_prompt, background_images, character_images,
                shot_no, image_output_dir, grid_seed
            )

            if image_path:
                logger.info(f"分镜{shot_no}的六宫格图片生成完成，图片路径: {image_path}")
                return {"shot_no": shot_no, "grid_image_path": image_path}
            else:
                logger.warning(f"分镜{shot_no}的六宫格图片生成失败")
                return {"shot_no": shot_no, "grid_image_path": "", "error": "六宫格图片生成失败"}

        except Exception as e:
            logger.error(f"六宫格图片生成失败: {str(e)}")
            return {"shot_no": shot_no, "grid_image_path": "", "error": str(e)}

    def _generate_grid_prompt(self, shot_script_text: str) -> str:
        """使用LLM生成六宫格图片提示词"""
        prompt = f"""你是一位拥有顶级工业标准的影视级分镜导演。你的任务是根据我提供的【分镜头脚本】和【角色四视图参考图】，批量生成高质量的、可直接用于AI视频/图像生成的中文提示词。

【核心规则】
1. 布局与画幅：强制输出 2列3行 的六宫格故事板布局（左边3个画格，右边3个画格）。整体画幅严格为 16:9，每个单格画幅严格为 8:3。6个画格必须完全等大且排列整齐。严禁因为整体是宽屏就自作主张生成第3列，必须且只能有2列！必须在画面中画出清晰的网格线，将画面物理分割为6个独立的画格。
2. 参考图死锁（最重要！）：用户会提供某角色的四视图参考图。你必须明白：每张四视图是【一个角色的不同角度】，而不是4个不同的人！在生成六宫格时，6个Panel中的角色必须且只能严格按照角色四视图中的形象生成（每张角色四视图右下角都有标注角色姓名）。严禁参考其他人物，严禁自由发挥改变角色外观！
3. 角色DNA提取：仔细观察提供的四视图，提炼出该角色最不易走样的核心防变形锚点（即角色DNA），如：特定发色与发型、瞳色、标志性面部特征、核心服饰款式与颜色。这些锚点必须在"全局锁定"中明确写出，用于压制AI的随机性。
4. 动作连贯推演：将每个脚本的动作合理拆解为 Panel 1 到 Panel 6 的连贯互动，必须逐个Panel详细描述，严禁将多个Panel合并描述。
5. 视觉风格：优先分析脚本适合的风格，信息不足时默认使用：影视级光影、2K分辨率、高精度渲染（C4D Octane Render / 高质量写实风格）。

【输入脚本】
{shot_script_text}

【输出要求】
1. 严格对应输入的脚本数量，生成同等数量的提示词。
2. 每条提示词约 150-200 字，必须是一段连贯的、可直接喂给AI的中文提示词。
3. 提示词末尾必须加上参数 --ar 16:9。
4. 严禁输出任何解释性废话，只按以下格式模板输出：
comic storyboard, a 16:9 canvas divided into 2 columns and 3 rows, exactly 6 equal-sized panels, each panel 8:3 aspect ratio, left side 3 panels, right side 3 panels, strictly aligned, white borders, no third column. 
CRITICAL RULE: strictly consistent character design across all 6 panels, same exact character from reference images in every panel, no character drift, strictly follow reference images.
Panel 1 (左上): [景别]+[核心动作]
Panel 2 (左中): [景别]+[核心动作]
Panel 3 (左下): [景别]+[核心动作]
Panel 4 (右上): [景别]+[核心动作]
Panel 5 (右中): [景别]+[核心动作]
Panel 6 (右下): [景别]+[核心动作]
全局锁定: [角色DNA: 极其具体的面部/发型/服饰锚点], [环境与背景], [光源与风格] --ar 16:9

【极简原则】
每个Panel的描述不超过30个字，只写【景别】和【人物(人物一定要使用角色名称不要使用代词，如他、她、他们等，ai无法识别)及其核心动作】（如：中景,茹蜷缩抱膝），严禁在Panel中重复描述角色特征！角色特征全部放在"全局锁定"的"角色DNA"中。"""
        messages = [{"role": "user", "content": prompt}]

        try:
            response = self.llm_client.think(messages=messages)
            return response.strip() if response else ""
        except Exception as e:
            logger.error(f"生成六宫格提示词失败: {str(e)}")
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

    def _generate_grid_image_with_api(
        self,
        grid_prompt: str,
        background_images: list,
        character_images: list,
        shot_no: int,
        image_output_dir: Path,
        seed: int = None,
    ) -> str:
        """使用ImageGeneration.call API生成六宫格图片并保存到本地"""
        try:
            from dashscope.aigc.image_generation import ImageGeneration
            from dashscope.api_entities.dashscope_response import Message

            dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

            content = [{"text": grid_prompt}]

            for img in background_images:
                content.append({"image": img})
            for img in character_images:
                content.append({"image": img})

            message = Message(role="user", content=content)

            logger.info(f"调用ImageGeneration API生成六宫格图片，参考图片数量: {len(background_images) + len(character_images)}")

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
                logger.info(f"分镜 {shot_no} 六宫格生成使用seed: {seed}")

            rsp = ImageGeneration.call(**call_kwargs)

            if rsp.status_code == 200:
                for i, choice in enumerate(rsp.output.choices):
                    for j, content_item in enumerate(choice["message"]["content"]):
                        if content_item.get("type") == "image":
                            image_url = content_item["image"]
                            return self._download_and_save_grid_image(
                                image_url, shot_no, image_output_dir
                            )

                logger.error(f"响应中未找到图片URL: {rsp}")
                return ""
            else:
                error_code = getattr(rsp, 'code', 'UNKNOWN')
                error_message = getattr(rsp, 'message', 'Unknown error')
                logger.error(f"六宫格图片生成失败 - status_code: {rsp.status_code}, code: {error_code}, message: {error_message}")
                return ""

        except Exception as e:
            logger.error(f"调用ImageGeneration API失败: {str(e)}")
            return ""

    def _download_and_save_grid_image(
        self, image_url: str, shot_no: int, image_output_dir: Path
    ) -> str:
        """下载六宫格图片并保存到本地"""
        try:
            import urllib.request

            old_image_pattern = f"{shot_no}_*_jgg.png"
            for old_image in image_output_dir.glob(old_image_pattern):
                try:
                    old_image.unlink()
                    logger.info(f"已删除旧的六宫格图片: {old_image}")
                except Exception as e:
                    logger.warning(f"删除旧图片失败: {old_image}, 错误: {e}")

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            file_name = f"{shot_no}_{timestamp}_jgg.png"
            file_path = image_output_dir / file_name

            urllib.request.urlretrieve(image_url, str(file_path))
            logger.info(f"六宫格图片已保存到: {file_path}")
            return str(file_path)
        except Exception as e:
            logger.error(f"下载或保存六宫格图片失败: {e}")
            return ""
