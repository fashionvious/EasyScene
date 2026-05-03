"""
角色四视图生成Agent节点
为每个角色生成四视图（侧面、正面、背面、面部特写）的提示词和图片
"""
import os
import logging
import requests
import dashscope
from dashscope import MultiModalConversation
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from app.agent.generatePic.llm_client import HelloAgentsLLM
from app.agent.generateVideo.seed_manager import derive_character_seed

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


class CharacterThreeViewAgent:
    """角色四视图生成Agent节点"""

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
    
    def __call__(self, state: Dict) -> Dict:
        """执行角色四视图生成"""
        logger.info(f"开始生成角色四视图，剧本: {state['script_name']}")

        script_id = state.get("script_id") or self.script_id
        if not script_id:
            logger.error("无法获取 script_id，使用默认路径")

        image_output_dir = self._get_image_output_dir(script_id)
        logger.info(f"图片输出目录: {image_output_dir}")

        try:
            character_three_views = state.get("character_three_views", [])

            current_character = state.get("current_character")
            if current_character:
                characters_to_process = [current_character]
                logger.info(f"HITL模式：只处理当前角色 {current_character.get('role_name', '未知')}")

                force_regenerate = current_character.get("force_regenerate", False)
                if force_regenerate:
                    logger.info(f"强制重新生成模式：将重新生成角色 {current_character.get('role_name', '未知')} 的四视图")
            else:
                characters_to_process = state["characters"]
                logger.info(f"批量模式：处理所有角色，共{len(characters_to_process)}个")
                force_regenerate = False

            for character in characters_to_process:
                role_name = character.get("role_name", "")
                role_desc = character.get("role_desc", "")

                if not role_name:
                    continue

                existing_view = next(
                    (v for v in character_three_views if v.get("role_name") == role_name),
                    None
                )

                if existing_view and existing_view.get("three_view_image_path") and not force_regenerate:
                    logger.info(f"角色 {role_name} 的四视图已存在，跳过生成")
                    continue

                if force_regenerate and existing_view:
                    logger.info(f"强制重新生成角色 {role_name} 的四视图（旧图片将被覆盖）")

                three_view_prompt = self._generate_three_view_prompt(role_name, role_desc)

                if not three_view_prompt:
                    logger.warning(f"角色 {role_name} 的四视图提示词生成失败")
                    continue

                global_seed = state.get("global_seed", 0)
                character_seed = derive_character_seed(global_seed, role_name)
                image_path = self._generate_three_view_image(three_view_prompt, role_name, image_output_dir, character_seed)

                if existing_view:
                    existing_view["three_view_prompt"] = three_view_prompt
                    existing_view["three_view_image_path"] = image_path or ""
                else:
                    character_three_views.append({
                        "role_name": role_name,
                        "three_view_prompt": three_view_prompt,
                        "three_view_image_path": image_path or ""
                    })

                if image_path:
                    logger.info(f"角色 {role_name} 的四视图生成完成，图片路径: {image_path}")
                else:
                    logger.warning(f"角色 {role_name} 的四视图图片生成失败，可能是 API 配额限制或其他错误")

            state["character_three_views"] = character_three_views
            state["three_views_generated"] = True
            state["updated_at"] = datetime.utcnow().isoformat()

            logger.info(f"角色四视图生成完成，共生成{len(character_three_views)}个角色的四视图")

        except Exception as e:
            logger.error(f"角色四视图生成失败: {str(e)}")
            state["error_message"] = f"角色四视图生成失败: {str(e)}"

        return state
    
    def _generate_three_view_prompt(self, role_name: str, role_desc: str) -> str:
        """使用qwen3.5-plus生成四视图提示词"""
        prompt = f"""你是一个熟悉各类 AI 图像生成模型底层的顶级提示词工程师。你的任务是根据用户的简单描述，生成用于AI视频生成的"高质量写实风格角色四视图"中文提示词。如用户无明确要求，生成的人物四视图必须是现实世界的写实风格，照片级逼真，不要生成绘画或动漫风格。

Rule 1: 排版与防出画规范 (CRITICAL)
1. 画布比例默认使用 16:9 。
2. 视角顺序：四格面板并排显示，严格从左到右依次为：[全身侧面]、[全身正面]、[全身背面]、[面部肖像特写] 。背景统一为纯灰色，图片右下角标注角色名。
3. 防裁切极其重要：必须在提示词中强力加入 "广角镜头"、"从头到脚全身完全可见"、"头顶和脚底留有大量空白边缘"。

Rule 2: 角色一致性
必须在提示词中强调："四格面板中为完全一致的角色"，并固化脸型、发色、瞳色、服装材质 。

Rule 3: 风格判定与特征发散
你需要根据角色描述扩充视觉细节
保持与电影级画面风格一致，避免卡通化或过度艺术化
* 如果分析出用户想要 3D/写实风格：起手加入 "3D渲染，照片级真实，虚幻引擎5"，并在末尾加上负面词 "禁止生成 动漫、2D、素描、画面裁剪残缺" 。
* 如果分析出用户想要 2D/动漫风格：起手加入 "2D平面插画，动漫风格，赛璐璐渲染"，并在末尾加上负面词 "禁止生成 3D、照片写实、真实质感、画面裁剪残缺" 。

用户描述：{role_name}、{role_desc}

Output Format
结合以上所有规范和用户描述，直接输出一段不超过150 字的、连贯的中文提示词，可以直接复制使用 。"""
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            response = self.llm_client.think(messages=messages)
            return response.strip() if response else ""
        except Exception as e:
            logger.error(f"生成四视图提示词失败: {str(e)}")
            return ""
    
    def _generate_three_view_image(self, prompt: str, role_name: str, image_output_dir: Path, seed: int = None) -> str:
        """使用 qwen-image-2.0-pro 模型生成四视图图片并保存到本地"""
        try:
            dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"text": prompt}
                    ]
                }
            ]
            
            call_kwargs = dict(
                api_key=self.api_key,
                model="qwen-image-2.0-pro",
                messages=messages,
                result_format='message',
                stream=False,
                watermark=False,
                prompt_extend=True,
                negative_prompt="低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲。",
                size='2688*1536'
            )
            
            if seed is not None:
                call_kwargs["seed"] = seed
                logger.info(f"角色 '{role_name}' 四视图生成使用seed: {seed}")
            
            response = MultiModalConversation.call(**call_kwargs)
            
            if response.status_code == 200:
                try:
                    choices = response.output.get('choices', [])
                    if choices:
                        content = choices[0].get('message', {}).get('content', [])
                        if content and isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict) and 'image' in item:
                                    image_url = item['image']
                                    logger.info(f"成功获取图片 URL: {image_url}")
                                    return self._download_and_save_image(image_url, role_name, image_output_dir)
                    
                    logger.error(f"响应中未找到图片 URL: {response}")
                    return ""
                    
                except Exception as e:
                    logger.error(f"解析响应失败: {str(e)}, 响应: {response}")
                    return ""
            else:
                error_code = getattr(response, 'code', 'UNKNOWN')
                error_message = getattr(response, 'message', 'Unknown error')
                
                if error_code == "AllocationQuota.FreeTierOnly":
                    logger.error(f"DashScope API 免费额度已用完，请升级到付费版本或等待额度重置")
                    logger.error(f"详细错误: {error_message}")
                else:
                    logger.error(f"图片生成失败 - HTTP状态码: {response.status_code}, 错误码: {error_code}, 错误信息: {error_message}")
                    logger.error("请参考文档：https://help.aliyun.com/zh/model-studio/developer-reference/error-code")
                
                return ""

        except Exception as e:
            logger.error(f"生成四视图图片失败: {str(e)}")
            return ""

    def _download_and_save_image(self, image_url: str, role_name: str, image_output_dir: Path) -> str:
        """下载图片并保存到本地"""
        try:
            old_image_pattern = f"{role_name}_three_view_*.png"
            for old_image in image_output_dir.glob(old_image_pattern):
                try:
                    old_image.unlink()
                    logger.info(f"已删除旧的四视图图片: {old_image}")
                except Exception as e:
                    logger.warning(f"删除旧图片失败: {old_image}, 错误: {e}")
            
            img_response = requests.get(image_url, timeout=30)
            img_response.raise_for_status()
            
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            image_filename = f"{role_name}_three_view_{timestamp}.png"
            image_path = image_output_dir / image_filename
            
            with open(image_path, "wb") as f:
                f.write(img_response.content)
            
            logger.info(f"角色 {role_name} 的四视图图片已保存到: {image_path}")
            return str(image_path)
        except Exception as e:
            logger.error(f"下载或保存图片失败: {e}")
            return ""
