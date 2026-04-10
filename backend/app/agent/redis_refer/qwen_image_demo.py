import os
import base64
import mimetypes
import urllib.request
import dashscope
from dashscope.aigc.image_generation import ImageGeneration
from dashscope.api_entities.dashscope_response import Message

# 以下为北京地域base_url，各地域的base_url不同
dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

# 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
# 各地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
api_key = os.getenv("DASHSCOPE_API_KEY")

"""
图像输入方式说明（图生组图场景）：
使用本地文件 - 适合本地开发测试
"""
# 使用本地文件（支持绝对路径和相对路径）
# image_1 = "file:///path/to/your/image1.png"
# image_2 = "file:///path/to/your/image2.png"

message = Message(
    role="user",
    content=[
        {
            "text": "电影感组图，记录同一只流浪橘猫，特征必须前后一致。第一张：春天，橘猫穿梭在盛开的樱花树下；第二张：夏天，橘猫在老街的树荫下乘凉避暑；第三张：秋天，橘猫踩在满地的金色落叶上；第四张：冬天，橘猫在雪地上走留下足迹。"
        }
        # 图生组图场景：取消以下注释并注释掉上方纯文本
        # {"text": "参考图片风格生成四季组图"},
        # {"image": image_1}
        # {"image": image_2}
    ],
)

print("----sync call, please wait a moment----")
rsp = ImageGeneration.call(
    model="wan2.7-image-pro",
    api_key=api_key,
    messages=[message],
    enable_sequential=True,
    n=4,
    size="2K",  # wan2.7-image-pro仅文生图场景支持4K分辨率，图像编辑和组图生成支持最高2K分辨率
)

# 提取结果图片URL并保存到本地
if rsp.status_code == 200:
    for i, choice in enumerate(rsp.output.choices):
        for j, content in enumerate(choice["message"]["content"]):
            if content.get("type") == "image":
                image_url = content["image"]
                file_name = f"output_{i}_{j}.png"
                # 结果URL有效期为24小时，请及时下载
                urllib.request.urlretrieve(image_url, file_name)
                print(f"Image saved to {file_name}")
else:
    print(f"Failed: status_code={rsp.status_code}, message={rsp.message}")