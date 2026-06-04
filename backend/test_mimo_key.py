"""测试 Xiaomi Mimo API key 是否有效"""
import os
os.environ.pop("DASHSCOPE_API_KEY", None)  # 避免干扰

from openai import OpenAI

KEY = "tp-cln8bpp64z0z5pa6kawzhh6lyi7tby9cctc4s6vdw626k4tu"
BASE = "https://token-plan-cn.xiaomimimo.com/v1"

client = OpenAI(api_key=KEY.strip(), base_url=BASE)

try:
    resp = client.chat.completions.create(
        model="mimo-v2.5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=10,
    )
    print(f"OK: {resp.choices[0].message.content}")
except Exception as e:
    print(f"FAIL: {e}")
