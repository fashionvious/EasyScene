import os
import signal
from openai import OpenAI
from dotenv import load_dotenv
from typing import List, Dict, Generator, Optional
from pathlib import Path

env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(env_path)

class LLMTimeoutError(Exception):
    pass

def timeout_handler(signum, frame):
    raise LLMTimeoutError("操作超时")

class HelloAgentsLLM:
    def __init__(self, model: str = None, apiKey: str = None, baseUrl: str = None, timeout: int = None):
        self.model = model or os.getenv("LLM_MODEL_ID")
        apiKey = apiKey or os.getenv("LLM_API_KEY")
        baseUrl = baseUrl or os.getenv("LLM_BASE_URL")
        timeout = timeout or int(os.getenv("LLM_TIMEOUT", 120))

        if not all([self.model, apiKey, baseUrl]):
            raise ValueError("模型ID、API密钥和服务地址必须被提供或在.env文件中定义。")

        self.client = OpenAI(api_key=apiKey, base_url=baseUrl, timeout=timeout)
        print(f"🔧 LLM客户端初始化完成: model={self.model}, timeout={timeout}s")

    def think(
        self,
        messages: List[Dict[str, str]],
        model: str = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        top_p: float = 0.85,
        response_format: Optional[dict] = None,
        stream: bool = False
    ) -> str:
        use_model = model or self.model
        print(f"🧠 正在调用 {use_model} 模型... (max_tokens={max_tokens}, top_p={top_p})")
        try:
            kwargs = {
                "model": use_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "top_p": top_p,
                "stream": stream,
            }

            if response_format:
                kwargs["response_format"] = response_format

            response = self.client.chat.completions.create(**kwargs)

            if stream:
                full_content = []
                for chunk in response:
                    content = chunk.choices[0].delta.content or ""
                    if content:
                        full_content.append(content)
                content = "".join(full_content)
            else:
                content = response.choices[0].message.content or ""

            print(f"✅ 大语言模型响应成功，长度: {len(content)} 字符")
            return content

        except Exception as e:
            print(f"❌ 调用LLM API时发生错误: {e}")
            import traceback
            traceback.print_exc()
            return None

    def think_stream(
        self,
        messages: List[Dict[str, str]],
        model: str = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        top_p: float = 0.85,
    ) -> Generator[str, None, None]:
        try:
            response = self.client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                stream=True,
            )

            for chunk in response:
                content = chunk.choices[0].delta.content or ""
                if content:
                    yield content

        except Exception as e:
            print(f"❌ 调用LLM API时发生错误: {e}")
            raise
