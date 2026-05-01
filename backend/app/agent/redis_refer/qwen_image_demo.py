import os
import base64
import mimetypes
import urllib.request
import dashscope
from dashscope.aigc.image_generation import ImageGeneration
from dashscope.api_entities.dashscope_response import Message
from http import HTTPStatus

# 以下为北京地域base_url，各地域的base_url不同
dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

# 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
# 各地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
api_key = os.getenv("DASHSCOPE_API_KEY")


# --- Base64编码函数 ---
# base64编码格式为 data:{MIME_type};base64,{base64_data}
def encode_file(file_path):
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError("不支持或无法识别的图像格式")
    with open(file_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded_string}"


"""
图像输入方式说明：
以下提供了三种图片输入方式，三选一即可
1. 使用公网URL - 适合已有公开可访问的图片
2. 使用本地文件 - 适合本地开发测试
3. 使用Base64编码 - 适合私有图片或需要加密传输的场景
"""
# 【方式一】使用公网图片 URL
image_1 = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251229/pjeqdf/car.webp"
image_2 = "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20251229/xsunlm/paint.webp"

# 【方式二】使用本地文件（支持绝对路径和相对路径）
# image_1 = "file:///path/to/your/car.png"
# image_2 = "file:///path/to/your/paint.png"

# 【方式三】使用Base64编码的图片
# image_1 = encode_file("/path/to/your/car.png")
# image_2 = encode_file("/path/to/your/paint.png")


# 创建异步任务
def create_async_task():
    print("Creating async task...")
    message = Message(
        role="user",
        content=[
            {"text": "把图2的涂鸦喷绘在图1的汽车上"},
            {"image": image_1},
            {"image": image_2},
        ],
    )
    response = ImageGeneration.async_call(
        model="wan2.7-image-pro",
        api_key=api_key,
        messages=[message],
        watermark=False,
        n=1,
        size="2K",  # wan2.7-image-pro仅文生图场景支持4K分辨率，图像编辑和组图生成支持最高2K分辨率
    )

    if response.status_code == 200:
        print("Task created successfully:", response)
        return response
    else:
        raise Exception(f"Failed to create task: {response.code} - {response.message}")


# 等待任务完成
def wait_for_completion(task_response):
    print("Waiting for task completion...")
    status = ImageGeneration.wait(task=task_response, api_key=api_key)

    if status.output.task_status == "SUCCEEDED":
        print("Task succeeded!")
        # 提取结果图片URL并保存到本地
        for i, choice in enumerate(status.output.choices):
            for j, content in enumerate(choice["message"]["content"]):
                if content.get("type") == "image":
                    image_url = content["image"]
                    file_name = f"output_{i}_{j}.png"
                    # 结果URL有效期为24小时，请及时下载
                    urllib.request.urlretrieve(image_url, file_name)
                    print(f"Image saved to {file_name}")
    else:
        raise Exception(f"Task failed with status: {status.output.task_status}")


# 获取异步任务信息
def fetch_task_status(task):
    print("Fetching task status...")
    status = ImageGeneration.fetch(task=task, api_key=api_key)

    if status.status_code == HTTPStatus.OK:
        print("Task status:", status.output.task_status)
        print("Response details:", status)
    else:
        print(f"Failed to fetch status: {status.code} - {status.message}")


# 取消异步任务
def cancel_task(task):
    print("Canceling task...")
    response = ImageGeneration.cancel(task=task, api_key=api_key)

    if response.status_code == HTTPStatus.OK:
        print("Task canceled successfully:", response.output.task_status)
    else:
        print(f"Failed to cancel task: {response.code} - {response.message}")


# 主执行流程
if __name__ == "__main__":
    task = create_async_task()
    wait_for_completion(task)