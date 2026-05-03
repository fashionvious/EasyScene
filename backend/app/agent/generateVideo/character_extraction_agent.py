"""
角色提取Agent节点
从剧本中提取所有主要角色的姓名和描述信息
"""
import json
import re
import logging
from typing import List, Dict, Optional
from datetime import datetime

from app.agent.generatePic.llm_client import HelloAgentsLLM

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


class CharacterExtractionAgent:
    """角色提取Agent节点"""
    
    def __init__(self, llm_client: HelloAgentsLLM, db_save_callback=None, redis_update_callback=None):
        self.llm_client = llm_client
        self.db_save_callback = db_save_callback
        self.redis_update_callback = redis_update_callback
    
    def __call__(self, state: Dict) -> Dict:
        """执行角色提取"""
        logger.info(f"开始提取角色信息，剧本: {state['script_name']}")
        
        try:
            prompt = f"""根据用户上传的剧本文档：
{state['script_content']}

请提取出**所有**主要人物的姓名、角色基础外观描述（如长相、发型等这些基本固定不变的，不包括角色的服装和状态）。
剧情中仅出现一次的人物不需要提取。
如果剧本文档中对主要人物的基础描述信息不足，请根据剧情合理推测设计，但不要脱离内容。

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
            
            logger.info("开始调用LLM进行角色提取...")
            response = self.llm_client.think(messages=messages)
            logger.info(f"LLM调用完成，响应类型: {type(response)}, 响应长度: {len(response) if response else 0}")
            
            if not response:
                logger.error("LLM返回空结果")
                state["error_message"] = "角色提取失败：LLM返回空结果"
                return state
            
            characters = self._parse_characters(response)
            
            state["characters"] = characters
            state["characters_generated"] = True
            state["characters_to_review"] = characters.copy()
            state["updated_at"] = datetime.utcnow().isoformat()
            
            if self.db_save_callback:
                try:
                    script_id = state.get("script_id")
                    save_success = self.db_save_callback(script_id, characters)
                    if save_success:
                        logger.info(f"角色提取完成，共提取{len(characters)}个角色，已保存到数据库并放入审核队列")
                        
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
        
        try:
            if response.startswith('\ufeff'):
                response = response[1:]
            
            response = response.strip()
            
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

        try:
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
            logger.warning(f"直接 JSON 解析失败: {e}，尝试正则提取...")
            try:
                json_match = re.search(r'$$.*$$', response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
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

        logger.info("尝试使用字符串分割解析...")
        lines = response.split("角色")
        
        if not characters:
            logger.warning("所有解析方式均失败，返回默认角色")
            characters.append({
                "role_name": "主角",
                "role_desc": "主要角色"
            })
        
        return characters
