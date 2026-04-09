"""
文生视频LangGraph工作流
使用LangGraph编排角色提取和分镜头脚本生成的业务流程
"""
import json
import re
import os
import sys
import asyncio
import logging
from typing import TypedDict, Annotated, List, Dict, Any, Optional


# 自定义 reducer 函数：用于合并 characters_to_review 的并发更新
def merge_character_queue(left: List[Dict[str, str]], right: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    合并角色审核队列的并发更新
    
    规则：
    - 如果 right 是空列表或 None，返回 left（保留原值）
    - 如果 right 是非空列表，返回 right（使用新值）
    - 这样可以确保 route_review 节点的更新生效，而 generate_shots 节点的不更新不影响
    """
    if not right:  # right 是空列表或 None
        return left
    return right


# 自定义 reducer 函数：用于合并 current_character 的并发更新
def merge_current_character(left: Optional[Dict[str, str]], right: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """
    合并当前审核角色的并发更新
    
    规则：
    - 如果 right 是 None，返回 left（保留原值）
    - 如果 right 不是 None，返回 right（使用新值）
    - 这样可以确保 route_review 和其他节点的更新生效，而不更新的节点不影响
    """
    if right is None:
        return left
    return right
from datetime import datetime
from pathlib import Path

# LangGraph相关导入
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Interrupt
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from langchain_core.messages import HumanMessage, AIMessage

# 本地导入
sys.path.append(str(Path(__file__).resolve().parents[3]))
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(env_path)

from app.agent.generatePic.llm_client import HelloAgentsLLM

# 日志配置
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


# ============================================================================
# 状态定义
# ============================================================================
class VideoGenerationState(TypedDict):
    """文生视频工作流状态"""
    # 输入
    script_id: str
    script_name: str
    script_content: str
    user_id: str
    
    # 角色信息
    characters: List[Dict[str, str]]
    characters_generated: bool
    characters_confirmed: bool
    
    # HITL相关 - 角色审核队列
    # 使用 Annotated 和自定义 reducer 来处理并发更新
    # 当多个节点并发更新时，使用 merge_character_queue 函数合并
    characters_to_review: Annotated[List[Dict[str, str]], merge_character_queue]  # 待审核的角色队列
    # 使用 Annotated 和自定义 reducer 来处理并发更新
    # 当多个节点并发更新时，使用 merge_current_character 函数合并
    current_character: Annotated[Optional[Dict[str, str]], merge_current_character]  # 当前正在审核的角色
    
    # 角色四视图
    character_three_views: List[Dict[str, str]]  # [{"role_name": "xxx", "three_view_prompt": "xxx", "three_view_image_path": "xxx"}]
    three_views_generated: bool
    
    # 分镜头脚本
    shot_scripts: List[Dict[str, Any]]
    shots_generated: bool
    shots_confirmed: bool
    
    # 错误信息
    error_message: Optional[str]
    
    # 元数据
    current_stage: str
    created_at: str
    updated_at: str


# ============================================================================
# Agent节点定义
# ============================================================================

class CharacterExtractionAgent:
    """角色提取Agent节点"""
    
    def __init__(self, llm_client: HelloAgentsLLM, db_save_callback=None, redis_update_callback=None):
        self.llm_client = llm_client
        self.db_save_callback = db_save_callback  # 数据库保存回调函数
        self.redis_update_callback = redis_update_callback  # Redis状态更新回调函数
    
    def __call__(self, state: VideoGenerationState) -> VideoGenerationState:
        """执行角色提取"""
        logger.info(f"开始提取角色信息，剧本: {state['script_name']}")
        
        try:
            # 修改后的 Prompt：明确要求提取所有角色，并使用省略号暗示列表可变长
            prompt = f"""根据用户上传的剧本文档：
{state['script_content']}

请提取出**所有**主要人物的姓名、角色基础描述（如长相、性格等这些基本固定不变的，而不是角色的状态）。
如果剧本文档中信息不足，请根据剧情合理推测设计，但不要脱离内容。

请**严格**按照以下 JSON 列表格式返回结果，不要包含 Markdown 标记（如 ```json），只返回纯 JSON 字符串。
请确保提取出剧本中出现的每一个主要角色，不要遗漏。

格式示例：
[
    {{"role_name": "角色姓名1", "role_desc": "角色基础描述1"}},
    {{"role_name": "角色姓名2", "role_desc": "角色基础描述2"}},
    ...
]
"""
            
            messages = [{"role": "user", "content": prompt}]
            
            # 调用LLM
            logger.info("开始调用LLM进行角色提取...")
            response = self.llm_client.think(messages=messages)
            logger.info(f"LLM调用完成，响应类型: {type(response)}, 响应长度: {len(response) if response else 0}")
            
            if not response:
                logger.error("LLM返回空结果")
                state["error_message"] = "角色提取失败：LLM返回空结果"
                return state
            
            # 解析角色信息
            characters = self._parse_characters(response)
            
            state["characters"] = characters
            state["characters_generated"] = True
            # HITL: 将所有角色放入待审核队列
            state["characters_to_review"] = characters.copy()
            state["updated_at"] = datetime.utcnow().isoformat()
            
            # 立即保存角色信息到数据库
            if self.db_save_callback:
                try:
                    script_id = state.get("script_id")
                    save_success = self.db_save_callback(script_id, characters)
                    if save_success:
                        logger.info(f"角色提取完成，共提取{len(characters)}个角色，已保存到数据库并放入审核队列")
                        
                        # 更新Redis状态，标记角色生成完成
                        if self.redis_update_callback:
                            try:
                                update_success = self.redis_update_callback(
                                    script_id=script_id,
                                    stage="char_desc",
                                    status="completed"
                                )
                                if update_success:
                                    logger.info(f"已更新Redis状态：角色生成完成")
                            except Exception as e:
                                logger.error(f"更新Redis状态失败: {str(e)}")
                    else:
                        logger.warning(f"角色信息保存到数据库失败，但继续执行后续流程")
                except Exception as e:
                    logger.error(f"保存角色信息到数据库时发生错误: {str(e)}")
            else:
                logger.info(f"角色提取完成，共提取{len(characters)}个角色，已放入审核队列")
            
        except Exception as e:
            logger.error(f"角色提取失败: {str(e)}")
            state["error_message"] = f"角色提取失败: {str(e)}"
        
        return state
    
    def _parse_characters(self, response: str) -> List[Dict[str, str]]:
        """解析LLM返回的角色信息"""
        logger.info(f"=== 准备解析角色信息，LLM 原始返回内容 ===\n{response}\n=== 内容结束 ===")
        
        characters = []
        
        # ---------------------------------------------------------
        # 新增：字符串清洗预处理
        # ---------------------------------------------------------
        try:
            # 1. 去除可能存在的 BOM 头
            if response.startswith('\ufeff'):
                response = response[1:]
            
            # 2. 去除首尾空白
            response = response.strip()
            
            # 3. 去除 Markdown 代码块标记 (以防万一 LLM 忽略了指令)
            if response.startswith("```json"):
                response = response[7:]
            elif response.startswith("```"):
                response = response[3:]
            
            if response.endswith("```"):
                response = response[:-3]
                
            response = response.strip()
            logger.info(f"清洗后的内容前50字符: {response[:50]}")
        except Exception as e:
            logger.error(f"字符串清洗失败: {e}")

        # ---------------------------------------------------------
        # 1. 优先尝试 JSON 解析
        # ---------------------------------------------------------
        try:
            # 策略 A: 直接解析 (如果清洗后就是纯 JSON)
            data = json.loads(response)
            logger.info("直接 JSON 解析成功")
            
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        name = item.get("role_name", "").strip()
                        desc = item.get("role_desc", "").strip()
                        if name:
                            characters.append({
                                "role_name": name,
                                "role_desc": desc or "暂无描述"
                            })
                if characters:
                    return characters
                    
        except json.JSONDecodeError as e:
            # 策略 B: 如果直接解析失败，尝试用正则提取数组部分
            logger.warning(f"直接 JSON 解析失败: {e}，尝试正则提取...")
            try:
                json_match = re.search(r'$$.*$$', response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    # 调试：打印正则提取到的字符串
                    logger.info(f"正则提取到的 JSON 长度: {len(json_str)}, 首字符: {repr(json_str[0])}")
                    
                    data = json.loads(json_str)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                name = item.get("role_name", "").strip()
                                desc = item.get("role_desc", "").strip()
                                if name:
                                    characters.append({
                                        "role_name": name,
                                        "role_desc": desc or "暂无描述"
                                    })
                        if characters:
                            logger.info("使用正则提取后解析成功")
                            return characters
            except Exception as inner_e:
                logger.error(f"正则提取后解析也失败: {inner_e}")

        # ---------------------------------------------------------
        # 2. 兜底：原有的字符串解析逻辑
        # ---------------------------------------------------------
        logger.info("尝试使用字符串分割解析...")
        lines = response.split("角色")
        
        # 如果解析失败，返回一个默认角色
        if not characters:
            logger.warning("所有解析方式均失败，返回默认角色")
            characters.append({
                "role_name": "主角",
                "role_desc": "主要角色"
            })
        
        return characters

import requests
import time
import base64
import dashscope
from dashscope import MultiModalConversation

class CharacterThreeViewAgent:
    """角色四视图生成Agent节点"""

    def __init__(self, llm_client: HelloAgentsLLM, script_id: str = None):
        self.llm_client = llm_client
        # 保存 script_id，但会在 __call__ 中从 state 动态获取
        self.script_id = script_id
        # 获取API Key
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
    
    def __call__(self, state: VideoGenerationState) -> VideoGenerationState:
        """执行角色四视图生成"""
        logger.info(f"开始生成角色四视图，剧本: {state['script_name']}")

        # 从 state 中获取 script_id，如果没有则使用初始化时的 script_id
        script_id = state.get("script_id") or self.script_id
        if not script_id:
            logger.error("无法获取 script_id，使用默认路径")

        # 获取图片输出目录
        image_output_dir = self._get_image_output_dir(script_id)
        logger.info(f"图片输出目录: {image_output_dir}")

        try:
            character_three_views = state.get("character_three_views", [])

            # HITL模式：如果存在current_character，只处理当前角色
            current_character = state.get("current_character")
            if current_character:
                # 只处理当前审核的角色
                characters_to_process = [current_character]
                logger.info(f"HITL模式：只处理当前角色 {current_character.get('role_name', '未知')}")

                # 检查是否需要强制重新生成
                force_regenerate = current_character.get("force_regenerate", False)
                if force_regenerate:
                    logger.info(f"强制重新生成模式：将重新生成角色 {current_character.get('role_name', '未知')} 的四视图")
            else:
                # 非HITL模式：处理所有角色
                characters_to_process = state["characters"]
                logger.info(f"批量模式：处理所有角色，共{len(characters_to_process)}个")
                force_regenerate = False

            for character in characters_to_process:
                role_name = character.get("role_name", "")
                role_desc = character.get("role_desc", "")

                if not role_name:
                    continue

                # 检查是否已经生成过四视图
                existing_view = next(
                    (v for v in character_three_views if v.get("role_name") == role_name),
                    None
                )

                # 如果已存在且不是强制重新生成，则跳过
                if existing_view and existing_view.get("three_view_image_path") and not force_regenerate:
                    logger.info(f"角色 {role_name} 的四视图已存在，跳过生成")
                    continue

                # 如果是强制重新生成，记录日志
                if force_regenerate and existing_view:
                    logger.info(f"强制重新生成角色 {role_name} 的四视图（旧图片将被覆盖）")

                # Step 1: 生成四视图提示词
                three_view_prompt = self._generate_three_view_prompt(role_name, role_desc)

                if not three_view_prompt:
                    logger.warning(f"角色 {role_name} 的四视图提示词生成失败")
                    continue

                # Step 2: 使用提示词生成四视图图片 (修改了调用方式)
                image_path = self._generate_three_view_image(three_view_prompt, role_name, image_output_dir)

                # 更新或添加四视图信息
                if existing_view:
                    existing_view["three_view_prompt"] = three_view_prompt
                    existing_view["three_view_image_path"] = image_path or ""
                else:
                    character_three_views.append({
                        "role_name": role_name,
                        "three_view_prompt": three_view_prompt,
                        "three_view_image_path": image_path or ""
                    })

                # 根据是否成功生成图片记录不同的日志
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
        prompt = f"""你是一个熟悉各类 AI 绘画模型底层的顶级提示词工程师。你的任务是根据用户的简单描述，生成用于AI视频生成的"高质量角色四视图"中文提示词。

Rule 1: 排版与防出画规范 (CRITICAL)
1. 画布比例默认使用 16:9 。
2. 视角顺序：四格面板并排显示，严格从左到右依次为：[全身侧面]、[全身正面]、[全身背面]、[面部肖像特写] 。背景统一为纯灰色，图片右下角标注角色名。
3. 防裁切极其重要：必须在提示词中强力加入 "广角镜头"、"从头到脚全身完全可见"、"头顶和脚底留有大量空白边缘"。

Rule 2: 角色一致性
必须在提示词中强调："四格面板中为完全一致的角色"，并固化脸型、发色、瞳色、服装材质 。

Rule 3: 风格判定与特征发散
你需要根据用户描述扩充视觉细节（例如"战士"要扩充出战甲材质、光影等）。
* 如果用户要求 3D/写实风格：起手加入 "3D渲染，照片级真实，虚幻引擎5"，并在末尾加上负面词 "禁止生成 动漫、2D、素描、画面裁剪残缺" 。
* 如果用户要求 2D/动漫风格：起手加入 "2D平面插画，动漫风格，赛璐璐渲染"，并在末尾加上负面词 "禁止生成 3D、照片写实、真实质感、画面裁剪残缺" 。

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
    
    def _generate_three_view_image(self, prompt: str, role_name: str, image_output_dir: Path) -> str:
        """使用 qwen-image-2.0-pro 模型生成四视图图片并保存到本地"""
        try:
            # 设置 DashScope API 基础 URL（北京地域）
            dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
            
            # 构建消息格式
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"text": prompt}
                    ]
                }
            ]
            
            # 调用 qwen-image-2.0-pro 模型生成图片
            response = MultiModalConversation.call(
                api_key=self.api_key,
                model="qwen-image-2.0",
                messages=messages,
                result_format='message',
                stream=False,
                watermark=False,
                prompt_extend=True,
                negative_prompt="低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲。",
                size='2688*1536'
            )
            
            # 检查响应状态
            if response.status_code == 200:
                # 提取图片 URL
                # 响应格式：{"output": {"choices": [{"message": {"content": [{"image": "url"}]}}]}}
                try:
                    choices = response.output.get('choices', [])
                    if choices:
                        content = choices[0].get('message', {}).get('content', [])
                        if content and isinstance(content, list):
                            # 查找图片类型的 content
                            for item in content:
                                if isinstance(item, dict) and 'image' in item:
                                    image_url = item['image']
                                    logger.info(f"成功获取图片 URL: {image_url}")
                                    # 下载并保存图片
                                    return self._download_and_save_image(image_url, role_name, image_output_dir)
                    
                    logger.error(f"响应中未找到图片 URL: {response}")
                    return ""
                    
                except Exception as e:
                    logger.error(f"解析响应失败: {str(e)}, 响应: {response}")
                    return ""
            else:
                # 处理错误
                error_code = getattr(response, 'code', 'UNKNOWN')
                error_message = getattr(response, 'message', 'Unknown error')
                
                # 特殊处理配额用完的情况
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
            # 删除该角色的旧四视图图片（避免文件堆积）
            # 查找所有以 "{role_name}_three_view_" 开头的文件
            old_image_pattern = f"{role_name}_three_view_*.png"
            for old_image in image_output_dir.glob(old_image_pattern):
                try:
                    old_image.unlink()
                    logger.info(f"已删除旧的四视图图片: {old_image}")
                except Exception as e:
                    logger.warning(f"删除旧图片失败: {old_image}, 错误: {e}")
            
            # 下载新图片
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


class ShotScriptGenerationAgent:
    """分镜头脚本生成Agent节点"""
    
    def __init__(self, llm_client: HelloAgentsLLM, db_save_callback=None, redis_update_callback=None):
        self.llm_client = llm_client
        self.db_save_callback = db_save_callback  # 数据库保存回调函数
        self.redis_update_callback = redis_update_callback  # Redis状态更新回调函数
    
    def __call__(self, state: VideoGenerationState) -> VideoGenerationState:
        """生成分镜头脚本"""
        logger.info(f"开始生成分镜头脚本，剧本: {state['script_name']}")
        
        try:
            # 构建提示词
            prompt = f"""你是一个专业的分镜头脚本编写助手。你的任务是将剧本转化为紧凑、高效的分镜头脚本。

# 核心目标
生成适合AI视频生成的分镜脚本，要求剧情紧凑，避免碎片化，并按场景进行分组。

# 重要规则
1. **场景分组原则**：
   - 必须按照场景对分镜进行分组，每个场景为一组。
   - 场景是指故事发生的地点或环境（如"公司门口"、"昏暗的小巷"、"温馨的卧室"等）。
   - 当场景发生明显变化时，必须开始新的场景组。
   - 输出格式中必须明确标注场景组号和场景名称。

2. **合并原则**：
   - 请尽量合并连续的对话和动作。一个分镜应包含一个完整的"剧情节拍"（例如，一段完整的对话交互，或一个连贯的动作过程）。
   - **严禁过度拆分**。不要将每一句对话或每一个细微动作都单独生成为一个分镜。
   - 只要场景、时间、人物状态没有发生显著变化，尽量将多句对话或动作合并在同一个分镜中，充分利用15秒的视频时长。

3. **独立性原则（关键）**：
   - 每一个分镜脚本都会被**独立**发送给不同的画师进行绘图。画师看不到其他分镜的内容。
   - **严禁引用**：严禁使用"同上"、"同前"、"装束不变"、"场景不变"、"同一地点"、"原地"等任何引用性词汇。
   - **完整复述**：如果下一镜的装束或场景与上一镜相同，必须**完整复述**具体的装束描述和场景名称（例如：如果上一镜是"公司门口"，下一镜也必须写"公司门口"，不能写"同一地点"）。

4. **内容要求**：
   - 包含对布景和人物妆造的描述（不含长相、性格等固定特征，着重描述当下的心理和生理状态）。
   - 如果有对话内容，需包含人物说话的状态（如"愤怒地喊道"）。
   - 描述事件尽量直观客观，避免华丽辞藻，包含提升画面质量的提示词。

5. **时长限制**：
   - 每个分镜生成的视频时长不可超过15秒。请根据此上限合理规划每个分镜包含的事件量。

# 输出格式（新增场景分组）
【场景组1】场景名称：xxx
分镜1：时间（粗略描述），室内/室外，场景，角色妆造分镜头，画面描述xxx
分镜2：时间（粗略描述），室内/室外，场景，角色妆造分镜头，画面描述xxx

【场景组2】场景名称：xxx
分镜3：时间（粗略描述），室内/室外，场景，角色妆造分镜头，画面描述xxx
分镜4：时间（粗略描述），室内/室外，场景，角色妆造分镜头，画面描述xxx

# 示例
【错误示例（场景引用）】：
【场景组1】场景名称：昏暗的小巷
分镜1：...，场景：昏暗的小巷，角色妆造：黑色风衣，...
分镜2：...，场景：同一地点，角色妆造：同上，...  <-- 错误！画师不知道"同一地点"是哪里，也不知道"同上"是什么。

【正确示例（完整复述）】：
【场景组1】场景名称：昏暗的小巷
分镜1：...，场景：昏暗的小巷，角色妆造：黑色风衣，...
分镜2：...，场景：昏暗的小巷，角色妆造：黑色风衣，...  <-- 正确！完整复述了场景和装束。

【错误示例（过度拆分）】：
【场景组1】场景名称：街道
分镜1：...，画面描述：沈千凝走在路上。
分镜2：...，画面描述：沈千凝自言自语。  <-- 错误！这两个动作连贯且场景未变，应合并。

【正确示例（紧凑合并）】：
【场景组1】场景名称：街道
分镜1：...，画面描述：沈千凝无精打采地走在回家的路上，难过地自言自语："才第一天..."。 <-- 正确！合并了动作和对话。

# 任务
请根据以下剧本生成分镜脚本，并按场景进行分组：
{state['script_content']}
"""
            messages = [{"role": "user", "content": prompt}]
            
            # 调用LLM
            response = self.llm_client.think(messages=messages)
            
            if not response:
                state["error_message"] = "分镜头脚本生成失败：LLM返回空结果"
                return state
            
            # 解析分镜头脚本
            shot_scripts = self._parse_shot_scripts(response)
            
            state["shot_scripts"] = shot_scripts
            state["shots_generated"] = True
            state["updated_at"] = datetime.utcnow().isoformat()
            
            # 立即保存分镜头脚本到数据库
            if self.db_save_callback:
                try:
                    script_id = state.get("script_id")
                    save_success = self.db_save_callback(script_id, shot_scripts)
                    if save_success:
                        logger.info(f"分镜头脚本生成完成，共生成{len(shot_scripts)}个分镜，已保存到数据库")
                        
                        # 更新Redis状态，标记分镜生成完成
                        if self.redis_update_callback:
                            try:
                                update_success = self.redis_update_callback(
                                    script_id=script_id,
                                    stage="shotlist_script",
                                    status="completed"
                                )
                                if update_success:
                                    logger.info(f"已更新Redis状态：分镜生成完成")
                            except Exception as e:
                                logger.error(f"更新Redis状态失败: {str(e)}")
                    else:
                        logger.warning(f"分镜头脚本保存到数据库失败，但继续执行后续流程")
                except Exception as e:
                    logger.error(f"保存分镜头脚本到数据库时发生错误: {str(e)}")
            else:
                logger.info(f"分镜头脚本生成完成，共生成{len(shot_scripts)}个分镜")
            
        except Exception as e:
            logger.error(f"分镜头脚本生成失败: {str(e)}")
            state["error_message"] = f"分镜头脚本生成失败: {str(e)}"
        
        return state
    
    def _parse_shot_scripts(self, response: str) -> List[Dict[str, Any]]:
        """
        解析LLM返回的分镜头脚本
        支持场景分组和分镜头分组功能
        
        返回格式：
        [
            {
                "shot_no": 1,
                "scene_group": 1,  # 场景组号
                "scene_name": "场景名称",
                "shot_group": 1,   # 分镜头组号（每个场景下每4个分镜为1组）
                "total_script": "分镜内容"
            },
            ...
        ]
        """
        shot_scripts = []
        
        # 按场景组分割
        scene_groups = re.split(r'【场景组(\d+)】场景名称[：:](.*?)(?=\n|$)', response)
        
        # 如果没有找到场景组标记，使用旧的解析逻辑
        if len(scene_groups) <= 1:
            logger.info("未找到场景组标记，使用旧版解析逻辑")
            return self._parse_shot_scripts_legacy(response)
        
        logger.info(f"找到场景组标记，开始解析场景分组")
        
        # 用于存储场景名称到场景组号的映射（实现相同场景名合并）
        scene_name_to_group = {}
        current_scene_group_no = 0
        
        # 解析场景组
        # scene_groups格式：[前置文本, 组号1, 场景名1, 内容1, 组号2, 场景名2, 内容2, ...]
        i = 1
        global_shot_no = 1
        while i < len(scene_groups) - 2:
            try:
                original_scene_group_no = int(scene_groups[i])
                scene_name_raw = scene_groups[i + 1].strip()
                scene_content = scene_groups[i + 2]
                
                # 清理场景名称：去除括号及其内容
                # 例如："街道转角处（靠近医院方向）" -> "街道转角处"
                scene_name = re.sub(r'[（(].*?[）)]', '', scene_name_raw).strip()
                
                # 如果场景名称为空，使用原始名称
                if not scene_name:
                    scene_name = scene_name_raw
                
                logger.info(f"解析场景组{original_scene_group_no}: {scene_name_raw} -> 清理后: {scene_name}")
                
                # 检查是否已存在相同场景名
                if scene_name in scene_name_to_group:
                    # 使用已有的场景组号
                    scene_group_no = scene_name_to_group[scene_name]
                    logger.info(f"场景'{scene_name}'已存在，合并到场景组{scene_group_no}")
                else:
                    # 创建新的场景组号
                    current_scene_group_no += 1
                    scene_group_no = current_scene_group_no
                    scene_name_to_group[scene_name] = scene_group_no
                    logger.info(f"创建新场景组{scene_group_no}: {scene_name}")
                
                # 解析该场景组下的分镜
                scene_shots = []
                shot_lines = scene_content.split("分镜")
                
                for line in shot_lines[1:]:  # 跳过第一个空元素
                    try:
                        # 提取分镜内容
                        if "：" in line or ":" in line:
                            parts = line.split("：", 1) if "：" in line else line.split(":", 1)
                            if len(parts) >= 2:
                                content = parts[1].strip()
                                
                                scene_shots.append({
                                    "shot_no": global_shot_no,
                                    "scene_group": scene_group_no,
                                    "scene_name": scene_name,
                                    "total_script": content
                                })
                                global_shot_no += 1
                    except Exception as e:
                        logger.warning(f"解析分镜失败: {str(e)}")
                        continue
                
                # 为该场景组下的分镜添加分镜头组号（每4个为1组）
                for idx, shot in enumerate(scene_shots):
                    shot["shot_group"] = (idx // 4) + 1
                
                shot_scripts.extend(scene_shots)
                
            except Exception as e:
                logger.warning(f"解析场景组失败: {str(e)}")
                i += 3
                continue
            
            i += 3
        
        # 如果解析失败，返回一个默认分镜
        if not shot_scripts:
            logger.warning("场景分组解析失败，使用默认分镜")
            shot_scripts.append({
                "shot_no": 1,
                "scene_group": 1,
                "scene_name": "默认场景",
                "shot_group": 1,
                "total_script": response
            })
        
        logger.info(f"分镜头脚本解析完成，共{len(shot_scripts)}个分镜")
        return shot_scripts
    
    def _parse_shot_scripts_legacy(self, response: str) -> List[Dict[str, Any]]:
        """旧版解析逻辑（向后兼容）"""
        shot_scripts = []
        
        # 简单的解析逻辑：按"分镜"分割
        lines = response.split("分镜")
        
        for idx, line in enumerate(lines[1:], 1):  # 跳过第一个空元素
            try:
                # 提取分镜编号
                if "：" in line or ":" in line:
                    parts = line.split("：", 1) if "：" in line else line.split(":", 1)
                    if len(parts) >= 2:
                        content = parts[1].strip()
                        
                        shot_scripts.append({
                            "shot_no": idx,
                            "scene_group": 1,  # 默认场景组
                            "scene_name": "默认场景",
                            "shot_group": (idx - 1) // 4 + 1,  # 每4个为1组
                            "total_script": content
                        })
            except Exception as e:
                logger.warning(f"解析分镜头脚本失败: {str(e)}")
                continue
        
        # 如果解析失败，返回一个默认分镜
        if not shot_scripts:
            shot_scripts.append({
                "shot_no": 1,
                "scene_group": 1,
                "scene_name": "默认场景",
                "shot_group": 1,
                "total_script": response
            })
        
        return shot_scripts


# ============================================================================
# HITL节点定义
# ============================================================================

def route_character_review_node(state: VideoGenerationState) -> dict:
    """
    节点：处理角色审核队列逻辑
    - 如果队列不为空：取出第一个角色，设置为 current_character
    - 如果队列为空：将 current_character 设置为 None
    返回状态更新字典。
    """
    characters_to_review = state.get("characters_to_review", [])
    
    if characters_to_review:
        # 取出第一个角色
        current_character = characters_to_review[0]
        # 剩余队列
        remaining_characters = characters_to_review[1:]
        
        logger.info(f"准备审核角色: {current_character.get('role_name', '未知')}")
        
        return {
            "current_character": current_character,
            "characters_to_review": remaining_characters
        }
    else:
        logger.info("所有角色已审核完毕，退出HITL循环")
        # 清空当前角色，确保路由走向 end_review
        return {"current_character": None}


def check_review_queue_router(state: VideoGenerationState) -> str:
    """
    路由函数：根据状态决定下一步
    - 如果 current_character 存在：进入 interrupt_for_review
    - 否则：结束循环 (END)
    """
    if state.get("current_character"):
        return "interrupt_for_review"
    else:
        return "end_review"


def character_review_interrupt(state: VideoGenerationState) -> dict:
    """
    HITL中断节点：使用 LangGraph 官方 interrupt API 暂停图执行，等待用户审核当前角色
    
    使用 interrupt() 函数会：
    1. 抛出 GraphInterrupt 异常，暂停图的执行
    2. 将当前状态保存到 checkpointer
    3. 等待用户通过 Command(resume=...) 恢复执行
    
    interrupt() 的参数会作为中断信息返回给调用者
    """
    current_character = state.get("current_character")
    if current_character:
        logger.info(f"HITL中断：等待用户审核角色 {current_character.get('role_name', '未知')}")
        
        # 使用 LangGraph 官方 interrupt API
        # interrupt 会暂停执行并返回给调用者
        # 用户审核后，通过 Command(resume=审核后的角色信息) 恢复
        user_reviewed_character = interrupt({
            "type": "character_review",
            "character": current_character,
            "message": f"请审核角色: {current_character.get('role_name', '未知')}"
        })
        
        # 用户恢复执行后，user_reviewed_character 包含审核后的角色信息
        logger.info(f"用户审核完成，角色: {user_reviewed_character.get('role_name', '未知')}")
        
        # 返回更新后的角色信息
        return {"current_character": user_reviewed_character}
    
    return {}


def process_reviewed_character(state: VideoGenerationState) -> dict:
    """
    处理用户审核后的角色：将当前角色更新到 characters 列表中
    """
    current_character = state.get("current_character")
    if current_character:
        logger.info(f"用户已审核角色: {current_character.get('role_name', '未知')}")
        characters = state.get("characters", [])
        # 创建新列表以避免原地修改
        new_characters = []
        updated = False
        for char in characters:
            if char.get("role_name") == current_character.get("role_name"):
                new_characters.append(current_character)
                updated = True
            else:
                new_characters.append(char)
        
        if updated:
            return {"characters": new_characters}
            
    return {}


# ============================================================================
# 工作流构建
# ============================================================================

def build_video_generation_graph(
    llm_client: HelloAgentsLLM,
    checkpointer=None,
    save_characters_callback=None,
    save_shots_callback=None,
    redis_update_callback=None,
    script_id: str = None
):
    """
    构建文生视频工作流图

    参数:
    - llm_client: LLM客户端
    - checkpointer: AsyncPostgresSaver实例，用于存储图的执行状态（短期记忆）
    - save_characters_callback: 角色信息保存到数据库的回调函数
    - save_shots_callback: 分镜脚本保存到数据库的回调函数
    - redis_update_callback: Redis状态更新回调函数
    - script_id: 剧本ID，用于创建按剧本区分的图片保存目录

    工作流设计:
    1. character_agent和shot_agent并行执行（通过条件边实现）
    2. character_agent完成后进入HITL循环，逐个审核角色
    3. 每个角色审核后生成四视图
    4. 所有角色审核完毕后，汇合到END
    """

    # 创建Agent节点
    character_agent = CharacterExtractionAgent(llm_client, save_characters_callback, redis_update_callback)
    three_view_agent = CharacterThreeViewAgent(llm_client, script_id)
    shot_agent = ShotScriptGenerationAgent(llm_client, save_shots_callback, redis_update_callback)
    
    # 创建状态图
    workflow = StateGraph(VideoGenerationState)
    
    # 添加节点
    workflow.add_node("extract_characters", character_agent)
    workflow.add_node("route_review", route_character_review_node) # 修改为新的节点函数
    workflow.add_node("interrupt_for_review", character_review_interrupt)
    workflow.add_node("process_reviewed", process_reviewed_character)
    workflow.add_node("generate_three_view", three_view_agent)
    workflow.add_node("generate_shots", shot_agent)
    
    # 设置入口点
    workflow.set_entry_point("extract_characters")
    
    # 并行执行设计：
    # 从extract_characters同时触发route_review和generate_shots
    def parallel_router(state: VideoGenerationState) -> List[str]:
        """并行路由：同时触发HITL循环和分镜头脚本生成"""
        return ["route_review", "generate_shots"]
    
    workflow.add_conditional_edges(
        "extract_characters",
        parallel_router,
        ["route_review", "generate_shots"]
    )
    
    # HITL循环
    # 修改：使用新的路由函数
    workflow.add_conditional_edges(
        "route_review",
        check_review_queue_router,
        {
            "interrupt_for_review": "interrupt_for_review",
            "end_review": END
        }
    )
    
    # 用户审核后恢复执行
    workflow.add_edge("interrupt_for_review", "process_reviewed")
    workflow.add_edge("process_reviewed", "generate_three_view")
    
    # 生成四视图后回到路由检查
    workflow.add_edge("generate_three_view", "route_review")
    
    # 分镜头脚本生成后直接结束
    workflow.add_edge("generate_shots", END)
    
    # 编译图
    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)
    else:
        return workflow.compile()


async def run_video_generation_workflow(
    script_id: str,
    script_name: str,
    script_content: str,
    user_id: str,
    db_uri: Optional[str] = None,
    save_characters_callback=None,
    save_shots_callback=None,
    redis_update_callback=None,
) -> Dict[str, Any]:
    """
    运行文生视频工作流
    
    参数:
    - script_id: 剧本ID
    - script_name: 剧本名称
    - script_content: 剧本内容
    - user_id: 用户ID
    - db_uri: 数据库连接字符串（用于checkpointer）
    - save_characters_callback: 角色信息保存到数据库的回调函数
    - save_shots_callback: 分镜脚本保存到数据库的回调函数
    - redis_update_callback: Redis状态更新回调函数
    
    返回:
    - 工作流执行结果
    
    注意：
    - 当工作流遇到 interrupt() 时，会抛出 GraphInterrupt 异常
    - 此时工作流暂停，状态保存到 checkpointer
    - 需要通过 resume_workflow() 函数恢复执行
    """
    # 初始化LLM客户端
    llm_client = HelloAgentsLLM()
    
    # 初始化checkpointer（如果提供了db_uri）
    checkpointer = None
    pool = None
    
    if db_uri:
        try:
            # 创建数据库连接池
            pool = AsyncConnectionPool(
                conninfo=db_uri,
                min_size=5,
                max_size=10,
                kwargs={"autocommit": True, "prepare_threshold": 0}
            )
            await pool.open()
            
            # 创建checkpointer
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            
            logger.info("Checkpointer初始化成功")
        except Exception as e:
            logger.error(f"Checkpointer初始化失败: {str(e)}")
            # 如果checkpointer初始化失败，继续执行但不使用checkpointer
    
    try:
        # 构建工作流图
        app = build_video_generation_graph(
            llm_client,
            checkpointer,
            save_characters_callback,
            save_shots_callback,
            redis_update_callback,
            script_id  # 传递 script_id 用于创建按剧本区分的目录
        )
        
        # 初始化状态
        initial_state: VideoGenerationState = {
            "script_id": script_id,
            "script_name": script_name,
            "script_content": script_content,
            "user_id": user_id,
            "characters": [],
            "characters_generated": False,
            "characters_confirmed": False,
            "characters_to_review": [],
            "current_character": None,
            "character_three_views": [],
            "three_views_generated": False,
            "shot_scripts": [],
            "shots_generated": False,
            "shots_confirmed": False,
            "error_message": None,
            "current_stage": "initialized",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        # 运行工作流
        # 注意：如果使用checkpointer，需要提供thread_id
        config = {"configurable": {"thread_id": script_id}}
        
        # 使用 ainvoke 运行工作流
        # 当遇到 interrupt() 时，会抛出 GraphInterrupt 异常
        result = await app.ainvoke(initial_state, config=config)
        
        # 检查结果是否包含 Interrupt 对象或其他不可序列化的对象
        # 如果结果中包含 Interrupt，说明工作流被中断了
        def has_interrupt(obj):
            """递归检查对象是否包含 Interrupt"""
            if isinstance(obj, Interrupt):
                return True
            if isinstance(obj, dict):
                return any(has_interrupt(v) for v in obj.values())
            if isinstance(obj, (list, tuple)):
                return any(has_interrupt(item) for item in obj)
            return False
        
        if has_interrupt(result):
            logger.info(f"工作流遇到HITL中断，等待用户审核，剧本: {script_name}")
            return {
                "interrupted": True,
                "script_id": script_id,
                "message": "工作流已暂停，等待用户审核角色信息",
                "characters": result.get("characters", []) if isinstance(result, dict) else [],
            }
        
        logger.info(f"工作流执行完成，剧本: {script_name}")
        return result
        
    except Interrupt as e:
        # 捕获 LangGraph 的 Interrupt 异常（HITL中断）
        logger.info(f"工作流遇到HITL中断，等待用户审核，剧本: {script_name}")
        # 返回中断信息，而不是错误
        # 前端应该根据这个信息提示用户进行审核
        return {
            "interrupted": True,
            "script_id": script_id,
            "message": "工作流已暂停，等待用户审核角色信息",
            "characters": [],  # 这里应该从状态中获取，但简化处理
        }
    
    except Exception as e:
        logger.error(f"工作流执行失败: {str(e)}")
        return {
            "error_message": str(e),
            "script_id": script_id,
        }
    
    finally:
        # 清理资源
        if pool:
            await pool.close()
            logger.info("数据库连接池已关闭")


async def resume_workflow_and_generate_three_view(
    script_id: str,
    character: dict,
    user_id: str,
    db_uri: Optional[str] = None,
    force_regenerate: bool = False,
) -> Dict[str, Any]:
    """
    恢复工作流并生成角色四视图
    
    参数:
    - script_id: 剧本ID
    - character: 用户审核后的角色信息
    - user_id: 用户ID
    - db_uri: 数据库连接字符串
    - force_regenerate: 是否强制重新生成四视图（即使已存在）
    
    返回:
    - 工作流恢复执行结果
    """
    # 初始化LLM客户端
    llm_client = HelloAgentsLLM()
    
    # 初始化checkpointer
    checkpointer = None
    pool = None
    
    if db_uri:
        try:
            # 创建数据库连接池
            pool = AsyncConnectionPool(
                conninfo=db_uri,
                min_size=5,
                max_size=10,
                kwargs={"autocommit": True, "prepare_threshold": 0}
            )
            await pool.open()
            
            # 创建checkpointer
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            
            logger.info("Checkpointer初始化成功")
        except Exception as e:
            logger.error(f"Checkpointer初始化失败: {str(e)}")
    
    try:
        # 构建工作流图
        app = build_video_generation_graph(
            llm_client,
            checkpointer,
            script_id=script_id  # 传递 script_id 用于创建按剧本区分的目录
        )
        
        # 配置
        config = {"configurable": {"thread_id": script_id}}
        
        # 如果需要强制重新生成，在character中添加标记
        if force_regenerate:
            character_with_flag = {**character, "force_regenerate": True}
        else:
            character_with_flag = character
        
        # 使用 Command 恢复工作流
        # Command(resume=character) 会将 character 作为 interrupt() 的返回值
        from langgraph.types import Command
        
        result = await app.ainvoke(
            Command(resume=character_with_flag),
            config=config
        )
        
        # 检查结果是否包含 Interrupt 对象或其他不可序列化的对象
        def has_interrupt(obj):
            """递归检查对象是否包含 Interrupt"""
            if isinstance(obj, Interrupt):
                return True
            if isinstance(obj, dict):
                return any(has_interrupt(v) for v in obj.values())
            if isinstance(obj, (list, tuple)):
                return any(has_interrupt(item) for item in obj)
            return False
        
        if has_interrupt(result):
            logger.info(f"工作流恢复执行遇到HITL中断，等待用户审核，剧本: {script_name}")
            return {
                "interrupted": True,
                "script_id": script_id,
                "message": "工作流已暂停，等待用户审核角色信息",
            }
        
        logger.info(f"工作流恢复执行完成，剧本: {script_name}")
        
        # 提取四视图图片路径
        character_three_views = result.get("character_three_views", [])
        three_view_image_path = None
        for view in character_three_views:
            if view.get("role_name") == character.get("role_name"):
                three_view_image_path = view.get("three_view_image_path")
                break
        
        return {
            "success": True,
            "script_id": script_id,
            "character": character,
            "three_view_image_path": three_view_image_path,
        }
        
    except Interrupt as e:
        # 捕获 LangGraph 的 Interrupt 异常（HITL中断）
        logger.info(f"工作流恢复执行遇到HITL中断，等待用户审核，剧本: {script_name}")
        return {
            "interrupted": True,
            "script_id": script_id,
            "message": "工作流已暂停，等待用户审核角色信息",
        }
    
    except Exception as e:
        logger.error(f"工作流恢复执行失败: {str(e)}")
        return {
            "error_message": str(e),
            "script_id": script_id,
        }
    
    finally:
        # 清理资源
        if pool:
            await pool.close()
            logger.info("数据库连接池已关闭")