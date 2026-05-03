"""
分镜头脚本生成Agent节点
将剧本转化为适合AI视频生成的分镜头脚本
"""
import re
import logging
from datetime import datetime
from typing import List, Dict, Any

from app.agent.generatePic.llm_client import HelloAgentsLLM

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


class ShotScriptGenerationAgent:
    """分镜头脚本生成Agent节点"""
    
    def __init__(self, llm_client: HelloAgentsLLM, db_save_callback=None, redis_update_callback=None):
        self.llm_client = llm_client
        self.db_save_callback = db_save_callback
        self.redis_update_callback = redis_update_callback
    
    def __call__(self, state: Dict) -> Dict:
        """生成分镜头脚本"""
        logger.info(f"开始生成分镜头脚本，剧本: {state['script_name']}")
        
        try:
            prompt = f"""你是一个专业的分镜头脚本编写助手。你的任务是将剧本转化为适合AI视频生成的分镜头脚本。

# 核心目标
生成适合AI视频生成的分镜脚本，要求**完整保留原剧本的主要内容和关键细节**，剧情连贯且节奏适中，适当剧情到一个分镜下，但也要避免因过度合并导致内容丢失，并按场景进行分组。

# 重要规则
1. **场景分组原则**：
   - 必须按照场景对分镜进行分组，每个场景为一组。
   - 场景是指故事发生的地点或环境（如"公司门口"、"昏暗的小巷"、"温馨的卧室"等）。
   - 当场景发生明显变化时，必须开始新的场景组。
   - 输出格式中必须明确标注场景组号和场景名称。

2. **适度拆分与合并原则**：
   - **内容完整性优先**：不要为了凑满10秒视频时长而强行合并大量对话或动作。如果原剧本中包含多句重要对话、关键动作细节或情绪转折，应适当拆分为多个分镜，以确保原剧本的主要内容得到完整表述。
   - **合理规划内容量**：一个分镜应包含一个合理的"剧情节拍"（例如，一次完整的对话交互，或一个连贯的动作过程），其内容量应能在10秒视频内清晰、从容地展现，避免单镜信息过载。
   - **避免无意义碎片化**：不要将一句简短的对话或一个极细微且无独立意义的动作单独生成为一个分镜。

3. **独立性原则（关键）**：
   - 每一个分镜脚本都会被**独立**发送给不同的画师进行绘图。画师看不到其他分镜的内容。
   - **严禁引用**：严禁使用"同上"、"同前"、"装束不变"、"场景不变"、"同一地点"、"原地"等任何引用性词汇。
   - **完整复述**：如果下一镜的装束或场景与上一镜相同，必须**完整复述**具体的装束描述和场景名称（例如：如果上一镜是"公司门口"，下一镜也必须写"公司门口"，不能写"同一地点"）。

4. **内容要求**：
   - 包含对布景和人物妆造的描述（不含长相、性格等固定特征，着重描述当下的心理和生理状态）。
   - 如果有对话内容，需包含人物说话的状态（如"愤怒地喊道"）。
   - 描述事件尽量直观客观，避免华丽辞藻，包含提升画面质量的提示词。

5. **时长限制**：
   - 每个分镜生成的视频时长不可超过10秒。请根据此上限合理规划每个分镜包含的事件量，宁可多拆一个分镜，也不要让单镜内容过于拥挤。

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

【错误示例（过度合并导致内容丢失）】：
【场景组1】场景名称：街道
分镜1：...，画面描述：沈千凝无精打采地走在回家的路上，难过地自言自语："才第一天..."，然后遇到李明，李明安慰她："没关系，慢慢来"，沈千凝点头微笑。 <-- 错误！信息量过大，10秒视频无法从容展现，且丢失了情绪转折的细节。

【正确示例（适度拆分保留细节）】：
【场景组1】场景名称：街道
分镜1：...，画面描述：沈千凝无精打采地走在回家的路上，难过地自言自语："才第一天..."。
分镜2：...，画面描述：李明走到沈千凝身边，温柔地安慰她："没关系，慢慢来"。
分镜3：...，画面描述：沈千凝听后抬起头，点头微笑，心情似乎好转。 <-- 正确！合理拆分，保留了完整的对话和情绪转折细节。

【错误示例（无意义碎片化）】：
【场景组1】场景名称：街道
分镜1：...，画面描述：沈千凝走在路上。
分镜2：...，画面描述：沈千凝自言自语。 <-- 错误！这句自言自语极短，与走路动作连贯，无需单独成镜。

# 任务
请根据以下剧本生成分镜脚本，并按场景进行分组：
{state['script_content']}
"""
            messages = [{"role": "user", "content": prompt}]
            
            response = self.llm_client.think(messages=messages)
            
            if not response:
                state["error_message"] = "分镜头脚本生成失败：LLM返回空结果"
                return state
            
            shot_scripts = self._parse_shot_scripts(response)
            
            state["shot_scripts"] = shot_scripts
            state["shots_generated"] = True
            state["updated_at"] = datetime.utcnow().isoformat()
            
            if self.db_save_callback:
                try:
                    script_id = state.get("script_id")
                    save_success = self.db_save_callback(script_id, shot_scripts)
                    if save_success:
                        logger.info(f"分镜头脚本生成完成，共生成{len(shot_scripts)}个分镜，已保存到数据库")
                        
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
                "scene_group": 1,
                "scene_name": "场景名称",
                "shot_group": 1,
                "total_script": "分镜内容"
            },
            ...
        ]
        """
        shot_scripts = []
        
        scene_groups = re.split(r'【场景组(\d+)】场景名称[：:](.*?)(?=\n|$)', response)
        
        if len(scene_groups) <= 1:
            logger.info("未找到场景组标记，使用旧版解析逻辑")
            return self._parse_shot_scripts_legacy(response)
        
        logger.info(f"找到场景组标记，开始解析场景分组")
        
        scene_name_to_group = {}
        current_scene_group_no = 0
        
        i = 1
        global_shot_no = 1
        while i < len(scene_groups) - 2:
            try:
                original_scene_group_no = int(scene_groups[i])
                scene_name_raw = scene_groups[i + 1].strip()
                scene_content = scene_groups[i + 2]
                
                scene_name = re.sub(r'[（(].*?[）)]', '', scene_name_raw).strip()
                
                if not scene_name:
                    scene_name = scene_name_raw
                
                logger.info(f"解析场景组{original_scene_group_no}: {scene_name_raw} -> 清理后: {scene_name}")
                
                if scene_name in scene_name_to_group:
                    scene_group_no = scene_name_to_group[scene_name]
                    logger.info(f"场景'{scene_name}'已存在，合并到场景组{scene_group_no}")
                else:
                    current_scene_group_no += 1
                    scene_group_no = current_scene_group_no
                    scene_name_to_group[scene_name] = scene_group_no
                    logger.info(f"创建新场景组{scene_group_no}: {scene_name}")
                
                scene_shots = []
                shot_lines = scene_content.split("分镜")
                
                for line in shot_lines[1:]:
                    try:
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
                
                for idx, shot in enumerate(scene_shots):
                    shot["shot_group"] = (idx // 4) + 1
                
                shot_scripts.extend(scene_shots)
                
            except Exception as e:
                logger.warning(f"解析场景组失败: {str(e)}")
                i += 3
                continue
            
            i += 3
        
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
        
        lines = response.split("分镜")
        
        for idx, line in enumerate(lines[1:], 1):
            try:
                if "：" in line or ":" in line:
                    parts = line.split("：", 1) if "：" in line else line.split(":", 1)
                    if len(parts) >= 2:
                        content = parts[1].strip()
                        
                        shot_scripts.append({
                            "shot_no": idx,
                            "scene_group": 1,
                            "scene_name": "默认场景",
                            "shot_group": (idx - 1) // 4 + 1,
                            "total_script": content
                        })
            except Exception as e:
                logger.warning(f"解析分镜头脚本失败: {str(e)}")
                continue
        
        if not shot_scripts:
            shot_scripts.append({
                "shot_no": 1,
                "scene_group": 1,
                "scene_name": "默认场景",
                "shot_group": 1,
                "total_script": response
            })
        
        return shot_scripts
