import os
import signal
from openai import OpenAI
from dotenv import load_dotenv
from typing import List, Dict, Generator
from pathlib import Path

# 加载 .env 文件中的环境变量
#load_dotenv()
env_path = Path(__file__).resolve().parents[3] / ".env"  # 根据实际层级调整
load_dotenv(env_path)


# 超时处理类
class TimeoutError(Exception):
    pass


def timeout_handler(signum, frame):
    raise TimeoutError("操作超时")
class HelloAgentsLLM:
    """
    为本书 "Hello Agents" 定制的LLM客户端。
    它用于调用任何兼容OpenAI接口的服务，并默认使用流式响应。
    """
    def __init__(self, model: str = None, apiKey: str = None, baseUrl: str = None, timeout: int = None):
        """
        初始化客户端。优先使用传入参数，如果未提供，则从环境变量加载。
        """
        self.model = model or os.getenv("LLM_MODEL_ID")
        apiKey = apiKey or os.getenv("LLM_API_KEY")
        baseUrl = baseUrl or os.getenv("LLM_BASE_URL")
        timeout = timeout or int(os.getenv("LLM_TIMEOUT", 120))  # 默认超时时间改为120秒
        
        if not all([self.model, apiKey, baseUrl]):
            raise ValueError("模型ID、API密钥和服务地址必须被提供或在.env文件中定义。")

        self.client = OpenAI(api_key=apiKey, base_url=baseUrl, timeout=timeout)
        print(f"🔧 LLM客户端初始化完成: model={self.model}, timeout={timeout}s")

    def think(self, messages: List[Dict[str, str]], temperature: float = 0) -> str:
        """
        调用大语言模型进行思考，并返回其响应。
        """
        print(f"🧠 正在调用 {self.model} 模型...")
        try:
            # 使用非流式响应以避免潜在的流式响应问题
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                stream=False,  # 改为非流式
            )
            
            print("✅ 大语言模型响应成功")
            content = response.choices[0].message.content or ""
            print(f"✅ 响应内容长度: {len(content)} 字符")
            return content

        except Exception as e:
            print(f"❌ 调用LLM API时发生错误: {e}")
            import traceback
            traceback.print_exc()
            return None

    def think_stream(self, messages: List[Dict[str, str]], temperature: float = 0) -> Generator[str, None, None]:
        """
        调用大语言模型进行思考，并以生成器方式返回流式响应。
        每次yield一个字符串片段。
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )
            
            # 处理流式响应
            for chunk in response:
                content = chunk.choices[0].delta.content or ""
                if content:
                    yield content

        except Exception as e:
            print(f"❌ 调用LLM API时发生错误: {e}")
            raise