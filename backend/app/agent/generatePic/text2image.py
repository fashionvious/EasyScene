"""
文生图功能函数模块
提供提示词生成、图片生成、图片存储等功能
"""
import os
import time
import random
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List, Generator
from pathlib import Path

# 加载环境变量
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(env_path)

# 导入LLM客户端
from app.agent.generatePic.llm_client import HelloAgentsLLM


# --- 提示词生成模板 ---
PROMPT_GENERATOR_TEMPLATE = """
你是一位专业的文生图提示词专家。请根据用户输入生成一段高质量的文生图提示词。

用户输入如下：{user_input}

请以如下格式生成一段文生图的提示词(其中的4个xx和**图片主体及其行为**和**具体背景**需要你根据用户输入内容分析得到，你可以适当发挥想象补充恰当的细节形容词)：
"请生成一张整体xx建模风格，xx质感，正面视角，镜头聚焦主体，**图片主体及其行为**，**具体背景**，xx构图，xx质感的2k高质量图片"

参考例子：
例子1 - 用户输入："黑夜下的一辆炫酷跑车"
生成提示词："请生成一张整体高精度建模风格，UE5质感，正面视角，镜头聚焦主体，一台与复古风结合的银灰色敞篷跑车，赛博朋克风格的城市夜景，霓虹灯广告，电影感构图，胶片颗粒质感，的2k高质量图片。"

例子2 - 用户输入："一只拟人仓鼠顶着叶子行走在昏暗的森林中"
生成提示词："请生成一张整体写实建模风格,背景虚化质感,正面视角, 镜头聚焦主体，一个穿着探险服的拟人仓鼠,一只手提着一盏煤油灯看向镜头,森林,一片比仓鼠大很多的有些干枯的叶子,像帐篷一样,撑在拟人仓鼠的头顶,煤油的灯发出的暖光,照亮周围的环境,阴天,电影感构图，胶片颗粒质感,的2k高质量图片。"

请直接输出提示词，不要包含任何额外的解释。
"""

PROMPT_MODIFIER_TEMPLATE = """
你是一位专业的文生图提示词专家。用户对当前的提示词有修改意见，请根据修改意见调整提示词。

当前提示词：{current_prompt}

用户修改意见：{user_input}

请根据用户的修改意见，调整并输出新的提示词。保持原有的格式和风格，只修改用户提到的部分。
请直接输出新的提示词，不要包含任何额外的解释。
"""


class PromptGenerator:
    """提示词生成器：使用qwen-max模型生成文生图提示词"""
    
    def __init__(self, llm_client: HelloAgentsLLM = None):
        if llm_client is None:
            self.llm_client = HelloAgentsLLM()
        else:
            self.llm_client = llm_client
    
    def generate_stream(self, user_input: str) -> Generator[str, None, None]:
        """根据用户输入生成文生图提示词（流式输出）"""
        prompt = PROMPT_GENERATOR_TEMPLATE.format(user_input=user_input)
        messages = [{"role": "user", "content": prompt}]
        
        yield from self.llm_client.think_stream(messages=messages)
    
    def modify_stream(self, user_input: str, current_prompt: str) -> Generator[str, None, None]:
        """根据用户修改意见修改提示词（流式输出）"""
        prompt = PROMPT_MODIFIER_TEMPLATE.format(
            user_input=user_input,
            current_prompt=current_prompt
        )
        messages = [{"role": "user", "content": prompt}]
        
        yield from self.llm_client.think_stream(messages=messages)


class ImageGenerator:
    """图片生成器：调用阿里云API生成图片"""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError("请提供阿里云DashScope API密钥或在.env文件中设置DASHSCOPE_API_KEY")
        
        self.base_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    
    def generate(self, prompt: str, size: str = "1024*1024", n: int = 1) -> List[str]:
        """
        调用阿里云API生成图片，返回图片URL列表
        
        参数:
        - prompt: 文生图提示词
        - size: 图片尺寸，支持 "1024*1024", "720*1280", "768*1152", "1280*720"
        - n: 生成图片数量，1-4
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable"  # 启用异步模式
        }
        
        payload = {
            "model": "wanx-v1",  # 使用通义万相模型
            "input": {
                "prompt": prompt
            },
            "parameters": {
                "style": "<auto>",  # 自动风格
                "size": size,
                "n": n
            }
        }
        
        try:
            # 提交任务
            response = requests.post(self.base_url, headers=headers, json=payload)
            response.raise_for_status()
            
            result = response.json()
            task_id = result.get("output", {}).get("task_id")
            
            if not task_id:
                raise Exception(f"任务提交失败: {result}")
            
            # 轮询查询任务状态
            image_urls = self._poll_task_status(task_id, headers)
            return image_urls
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"API调用失败: {e}")
    
    def _poll_task_status(self, task_id: str, headers: dict, max_wait: int = 300) -> List[str]:
        """轮询查询任务状态，直到任务完成或超时"""
        query_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        start_time = time.time()
        
        # 查询任务状态时，需要移除X-DashScope-Async头
        query_headers = {
            "Authorization": headers["Authorization"]
        }
        
        while time.time() - start_time < max_wait:
            try:
                response = requests.get(query_url, headers=query_headers)
                response.raise_for_status()
                
                result = response.json()
                status = result.get("output", {}).get("task_status")
                
                if status == "SUCCEEDED":
                    # 获取图片URL列表
                    results = result.get("output", {}).get("results", [])
                    image_urls = [r.get("url") for r in results if r.get("url")]
                    if image_urls:
                        return image_urls
                    else:
                        raise Exception("未找到生成的图片URL")
                
                elif status == "FAILED":
                    error_msg = result.get("output", {}).get("message", "未知错误")
                    raise Exception(f"图片生成失败: {error_msg}")
                
                elif status in ["PENDING", "RUNNING"]:
                    time.sleep(3)  # 等待3秒后再次查询
                
                else:
                    time.sleep(3)
                    
            except requests.exceptions.RequestException as e:
                time.sleep(3)
        
        raise Exception("任务超时")


class ImageStorage:
    """图片存储模块：支持本地存储和数据库存储"""
    
    def __init__(self, local_dir: str = "generated_images"):
        self.local_dir = local_dir
        os.makedirs(local_dir, exist_ok=True)
    
    def save_to_local(self, image_url: str) -> Optional[str]:
        """将图片保存到本地，返回本地文件路径"""
        try:
            # 生成唯一文件名：时间戳 + 随机数
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            random_num = random.randint(1000, 9999)
            filename = f"{timestamp}_{random_num}.png"
            filepath = os.path.join(self.local_dir, filename)
            
            # 下载图片
            response = requests.get(image_url, timeout=30)
            response.raise_for_status()
            
            # 保存图片
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            return filepath
            
        except Exception as e:
            print(f"本地存储失败: {e}")
            return None
    
    def save_to_database(self, image_url: str, prompt: str, db_config: Dict[str, Any] = None) -> bool:
        """将图片URL保存到PostgreSQL数据库"""
        if db_config is None:
            db_config = {
                "host": os.getenv("POSTGRES_SERVER", "localhost"),
                "port": int(os.getenv("POSTGRES_PORT", 5432)),
                "database": os.getenv("POSTGRES_DB", "app"),
                "user": os.getenv("POSTGRES_USER", "postgres"),
                "password": os.getenv("POSTGRES_PASSWORD", "changethis")
            }
        
        try:
            import psycopg2
            
            # 连接数据库
            conn = psycopg2.connect(
                host=db_config.get("host", "localhost"),
                port=db_config.get("port", 5432),
                database=db_config.get("database", "app"),
                user=db_config.get("user", "postgres"),
                password=db_config.get("password", "changethis")
            )
            
            cursor = conn.cursor()
            
            # 插入数据
            insert_query = """
                INSERT INTO generated_images (image_url, prompt, created_at)
                VALUES (%s, %s, %s)
            """
            created_at = datetime.now()
            cursor.execute(insert_query, (image_url, prompt, created_at))
            
            conn.commit()
            
            cursor.close()
            conn.close()
            return True
            
        except ImportError:
            print("未安装psycopg2库，请运行: pip install psycopg2-binary")
            return False
        except Exception as e:
            print(f"数据库存储失败: {e}")
            return False


# --- 便捷函数 ---

def generate_prompt_stream(user_input: str) -> Generator[str, None, None]:
    """
    根据用户输入生成文生图提示词（流式输出）
    
    参数:
    - user_input: 用户输入的图片描述
    
    返回:
    - 生成器，每次yield一个字符串片段
    """
    generator = PromptGenerator()
    yield from generator.generate_stream(user_input)


def modify_prompt_stream(user_input: str, current_prompt: str) -> Generator[str, None, None]:
    """
    根据用户修改意见修改提示词（流式输出）
    
    参数:
    - user_input: 用户的修改意见
    - current_prompt: 当前的提示词
    
    返回:
    - 生成器，每次yield一个字符串片段
    """
    generator = PromptGenerator()
    yield from generator.modify_stream(user_input, current_prompt)


def generate_images(prompt: str, size: str = "1024*1024", n: int = 1) -> List[str]:
    """
    根据提示词生成图片
    
    参数:
    - prompt: 文生图提示词
    - size: 图片尺寸，支持 "1024*1024", "720*1280", "768*1152", "1280*720"
    - n: 生成图片数量，1-4
    
    返回:
    - 图片URL列表
    """
    generator = ImageGenerator()
    return generator.generate(prompt, size, n)


def save_image_to_local(image_url: str) -> Optional[str]:
    """
    将图片保存到本地
    
    参数:
    - image_url: 图片URL
    
    返回:
    - 本地文件路径，失败返回None
    """
    storage = ImageStorage()
    return storage.save_to_local(image_url)


def save_image_to_database(image_url: str, prompt: str, db_config: Dict[str, Any] = None) -> bool:
    """
    将图片URL保存到数据库
    
    参数:
    - image_url: 图片URL
    - prompt: 生成图片使用的提示词
    - db_config: 数据库配置（可选）
    
    返回:
    - 是否保存成功
    """
    storage = ImageStorage()
    return storage.save_to_database(image_url, prompt, db_config)


def generate_and_save_images(
    prompt: str,
    size: str = "1024*1024",
    n: int = 1,
    save_local: bool = True,
    save_db: bool = True,
    db_config: Dict[str, Any] = None
) -> List[Dict[str, Any]]:
    """
    生成图片并保存
    
    参数:
    - prompt: 文生图提示词
    - size: 图片尺寸
    - n: 生成图片数量
    - save_local: 是否保存到本地
    - save_db: 是否保存到数据库
    - db_config: 数据库配置
    
    返回:
    - 包含图片信息的字典列表
    """
    # 生成图片
    image_urls = generate_images(prompt, size, n)
    
    results = []
    for image_url in image_urls:
        result = {
            "image_url": image_url,
            "prompt": prompt,
            "local_path": None,
            "db_saved": False
        }
        
        # 保存到本地
        if save_local:
            local_path = save_image_to_local(image_url)
            result["local_path"] = local_path
        
        # 保存到数据库
        if save_db:
            db_saved = save_image_to_database(image_url, prompt, db_config)
            result["db_saved"] = db_saved
        
        results.append(result)
    
    return results
