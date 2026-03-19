import os
import time
import random
import requests
from datetime import datetime
from typing import Optional, Dict, Any
from llm_client import HelloAgentsLLM
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# --- 第一步：提示词生成器 ---
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

class PromptGenerator:
    """提示词生成器：使用qwen-max模型生成文生图提示词"""
    
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client
    
    def generate(self, user_input: str) -> str:
        """根据用户输入生成文生图提示词"""
        print("\n--- 第一步：生成文生图提示词 ---")
        prompt = PROMPT_GENERATOR_TEMPLATE.format(user_input=user_input)
        messages = [{"role": "user", "content": prompt}]
        
        work2pic_prompt = self.llm_client.think(messages=messages)
        
        if work2pic_prompt:
            print(f"\n✅ 提示词生成成功:\n{work2pic_prompt}")
            return work2pic_prompt
        else:
            print("❌ 提示词生成失败")
            return None


# --- 第二步：图片生成器 ---
class ImageGenerator:
    """图片生成器：调用阿里云API生成图片"""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError("请提供阿里云DashScope API密钥或在.env文件中设置DASHSCOPE_API_KEY")
        
        self.base_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    
    def generate(self, prompt: str) -> Optional[str]:
        """调用阿里云API生成图片，返回图片URL"""
        print("\n--- 第二步：调用API生成图片 ---")
        
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
                "size": "1024*1024",  # 图片尺寸
                "n": 1  # 生成图片数量
            }
        }
        
        try:
            # 提交任务
            print("📤 正在提交图片生成任务...")
            response = requests.post(self.base_url, headers=headers, json=payload)
            response.raise_for_status()
            
            result = response.json()
            task_id = result.get("output", {}).get("task_id")
            
            if not task_id:
                print(f"❌ 任务提交失败: {result}")
                return None
            
            print(f"✅ 任务已提交，任务ID: {task_id}")
            
            # 轮询查询任务状态
            image_url = self._poll_task_status(task_id, headers)
            return image_url
            
        except requests.exceptions.RequestException as e:
            print(f"❌ API调用失败: {e}")
            return None
    
    def _poll_task_status(self, task_id: str, headers: dict, max_wait: int = 300) -> Optional[str]:
        """轮询查询任务状态，直到任务完成或超时"""
        #query_url = f"{self.base_url}/{task_id}"
        query_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

        start_time = time.time()
        
        # 查询任务状态时，需要移除X-DashScope-Async头
        query_headers = {
            "Authorization": headers["Authorization"]
        }
        
        print("⏳ 正在等待图片生成...")
        
        while time.time() - start_time < max_wait:
            try:
                response = requests.get(query_url, headers=query_headers)
                response.raise_for_status()
                
                result = response.json()
                status = result.get("output", {}).get("task_status")
                
                if status == "SUCCEEDED":
                    # 获取图片URL
                    results = result.get("output", {}).get("results", [])
                    if results and len(results) > 0:
                        image_url = results[0].get("url")
                        print(f"✅ 图片生成成功!")
                        print(f"📷 图片URL: {image_url}")
                        return image_url
                    else:
                        print("❌ 未找到生成的图片URL")
                        return None
                
                elif status == "FAILED":
                    error_msg = result.get("output", {}).get("message", "未知错误")
                    print(f"❌ 图片生成失败: {error_msg}")
                    return None
                
                elif status in ["PENDING", "RUNNING"]:
                    print(f"⏳ 任务状态: {status}，继续等待...")
                    time.sleep(3)  # 等待3秒后再次查询
                
                else:
                    print(f"⚠️ 未知任务状态: {status}")
                    time.sleep(3)
                    
            except requests.exceptions.RequestException as e:
                print(f"❌ 查询任务状态失败: {e}")
                time.sleep(3)
        
        print("❌ 任务超时")
        return None


# --- 存储模块 ---
class ImageStorage:
    """图片存储模块：支持本地存储和数据库存储"""
    
    def __init__(self, local_dir: str = "generated_images"):
        self.local_dir = local_dir
        os.makedirs(local_dir, exist_ok=True)
    
    def save_to_local(self, image_url: str) -> Optional[str]:
        """将图片保存到本地，返回本地文件路径"""
        print("\n--- 本地存储 ---")
        
        try:
            # 生成唯一文件名：时间戳 + 随机数
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            random_num = random.randint(1000, 9999)
            filename = f"{timestamp}_{random_num}.png"
            filepath = os.path.join(self.local_dir, filename)
            
            # 下载图片
            print(f"📥 正在下载图片到本地...")
            response = requests.get(image_url, timeout=30)
            response.raise_for_status()
            
            # 保存图片
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            print(f"✅ 图片已保存到本地: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"❌ 本地存储失败: {e}")
            return None
    
    def save_to_database(self, image_url: str, prompt: str, db_config: Dict[str, Any]) -> bool:
        """将图片URL保存到PostgreSQL数据库"""
        print("\n--- 数据库存储 ---")
        
        try:
            import psycopg2
            from psycopg2 import sql
            
            # 连接数据库
            print("🔌 正在连接数据库...")
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
            print(f"✅ 图片URL已保存到数据库")
            
            cursor.close()
            conn.close()
            return True
            
        except ImportError:
            print("❌ 未安装psycopg2库，请运行: pip install psycopg2-binary")
            return False
        except Exception as e:
            print(f"❌ 数据库存储失败: {e}")
            return False


# --- 数据库表创建代码 ---
def create_database_table(db_config: Dict[str, Any]):
    """创建数据库表的SQL代码"""
    print("\n=== 数据库表创建代码 ===")
    
    sql_code = """
-- 创建generated_images表
CREATE TABLE IF NOT EXISTS generated_images (
    id SERIAL PRIMARY KEY,
    image_url VARCHAR(500) NOT NULL,
    prompt TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引以加速查询
CREATE INDEX IF NOT EXISTS idx_created_at ON generated_images(created_at);
CREATE INDEX IF NOT EXISTS idx_prompt ON generated_images USING gin(to_tsvector('english', prompt));

-- 添加注释
COMMENT ON TABLE generated_images IS '存储生成的图片信息';
COMMENT ON COLUMN generated_images.id IS '主键ID';
COMMENT ON COLUMN generated_images.image_url IS '图片URL地址';
COMMENT ON COLUMN generated_images.prompt IS '生成图片使用的提示词';
COMMENT ON COLUMN generated_images.created_at IS '创建时间';
COMMENT ON COLUMN generated_images.updated_at IS '更新时间';
"""
    
    print(sql_code)
    
    # 如果提供了数据库配置，尝试直接创建表
    if db_config:
        try:
            import psycopg2
            
            print("\n正在尝试连接数据库并创建表...")
            conn = psycopg2.connect(
                host=db_config.get("host", "localhost"),
                port=db_config.get("port", 5432),
                database=db_config.get("database", "app"),
                user=db_config.get("user", "postgres"),
                password=db_config.get("password", "changethis")
            )
            
            cursor = conn.cursor()
            cursor.execute(sql_code)
            conn.commit()
            
            print("✅ 数据库表创建成功!")
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            print(f"❌ 自动创建表失败: {e}")
            print("请手动执行上述SQL代码创建表")
    
    return sql_code


# --- 文生图Agent（基于Plan_and_Solve模式）---
class Text2ImageAgent:
    """
    文生图智能体
    基于Plan_and_Solve模式实现
    """
    
    def __init__(self, llm_client: HelloAgentsLLM, image_api_key: str = None):
        self.llm_client = llm_client
        self.prompt_generator = PromptGenerator(llm_client)
        self.image_generator = ImageGenerator(image_api_key)
        self.storage = ImageStorage()
    
    def run(self, user_input: str, save_local: bool = True, save_db: bool = False, db_config: Dict[str, Any] = None):
        """
        执行文生图任务
        
        参数:
        - user_input: 用户输入的描述
        - save_local: 是否保存到本地
        - save_db: 是否保存到数据库
        - db_config: 数据库配置
        """
        print(f"\n{'='*60}")
        print(f"🎨 文生图Agent启动")
        print(f"{'='*60}")
        print(f"📝 用户输入: {user_input}")
        
        # 步骤1：生成提示词
        work2pic_prompt = self.prompt_generator.generate(user_input)
        if not work2pic_prompt:
            print("\n❌ 任务失败：无法生成提示词")
            return None
        
        # 步骤2：生成图片
        image_url = self.image_generator.generate(work2pic_prompt)
        if not image_url:
            print("\n❌ 任务失败：无法生成图片")
            return None
        
        # 步骤3：存储图片
        result = {
            "prompt": work2pic_prompt,
            "image_url": image_url,
            "local_path": None,
            "db_saved": False
        }
        
        if save_local:
            local_path = self.storage.save_to_local(image_url)
            result["local_path"] = local_path
        
        if save_db and db_config:
            db_saved = self.storage.save_to_database(image_url, work2pic_prompt, db_config)
            result["db_saved"] = db_saved
        
        print(f"\n{'='*60}")
        print(f"✅ 任务完成!")
        print(f"{'='*60}")
        print(f"📷 图片URL: {image_url}")
        if result["local_path"]:
            print(f"💾 本地路径: {result['local_path']}")
        if result["db_saved"]:
            print(f"🗄️ 已保存到数据库")
        
        return result


# --- 主函数 ---
if __name__ == '__main__':
    # 示例：数据库配置
    db_config = {
        "host": "localhost",
        "port": 5432,
        "database": "app",
        "user": "postgres",
        "password": "changethis"  # 请替换为实际密码
    }
    
    # 打印数据库表创建代码
    # print("\n" + "="*60)
    # print("数据库表创建SQL代码：")
    # print("="*60)
    #create_database_table(None)  # 打印SQL代码，不自动创建
    
    # 初始化Agent
    try:
        llm_client = HelloAgentsLLM()  # 使用qwen-max模型
        agent = Text2ImageAgent(llm_client)
        
        # 运行示例
        user_input = "两岸同胞一家亲"
        result = agent.run(
            user_input=user_input,
            save_local=True,
            save_db=True,  # 设置为True并配置db_config以启用数据库存储
            db_config=db_config
        )
        
    except ValueError as e:
        print(f"\n❌ 初始化失败: {e}")
        print("\n请确保在.env文件中配置以下环境变量：")
        print("LLM_MODEL_ID=qwen-max")
        print("LLM_API_KEY=your_qwen_api_key")
        print("LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1")
        print("DASHSCOPE_API_KEY=your_dashscope_api_key")
