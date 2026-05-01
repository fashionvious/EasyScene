from http import HTTPStatus
from urllib.parse import urlparse, unquote
from pathlib import PurePosixPath
import requests
from dashscope import ImageSynthesis
import os
import dashscope
import time

# 以下为北京地域url，若使用新加坡地域的模型，需将url替换为：https://dashscope-intl.aliyuncs.com/api/v1
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

prompt = "一副典雅庄重的对联悬挂于厅堂之中，房间是个安静古典的中式布置，桌子上放着一些青花瓷，对联上左书“义本生知人机同道善思新”，右书“通云赋智乾坤启数高志远”， 横批“智启千问”，字体飘逸，在中间挂着一幅中国风的画作，内容是岳阳楼。"

# 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
# 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
api_key = os.getenv("DASHSCOPE_API_KEY")

def async_call():
    print('----创建任务----')
    task_info = create_async_task()
    print('----轮询任务状态----')
    poll_task_status(task_info)


# 创建异步任务
def create_async_task():
    rsp = ImageSynthesis.async_call(api_key=api_key,
                                    model="qwen-image-plus", # 当前仅qwen-image-plus、qwen-image模型支持异步接口
                                    prompt=prompt,
                                    negative_prompt=" ",
                                    n=1,
                                    size='1664*928',
                                    prompt_extend=True,
                                    watermark=False)
    print(rsp)
    if rsp.status_code == HTTPStatus.OK:
        print(rsp.output)
    else:
        print(f'创建任务失败, status_code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}')
    return rsp


# 轮询异步任务状态，每5秒查询一次，最多轮询1分钟
def poll_task_status(task):
    start_time = time.time()
    timeout = 60  # 1分钟超时
    
    while True:
        # 检查是否超时
        if time.time() - start_time > timeout:
            print('轮询超时（1分钟），任务未完成')
            return
            
        # 获取任务状态
        status_rsp = ImageSynthesis.fetch(task)
        print(f'任务状态查询结果: {status_rsp}')
        
        if status_rsp.status_code != HTTPStatus.OK:
            print(f'获取任务状态失败, status_code: {status_rsp.status_code}, code: {status_rsp.code}, message: {status_rsp.message}')
            return
        task_status = status_rsp.output.task_status
        print(f'当前任务状态: {task_status}')
        
        if task_status == 'SUCCEEDED':
            print('任务已完成，正在下载图像...')
            for result in status_rsp.output.results:
                file_name = PurePosixPath(unquote(urlparse(result.url).path)).parts[-1]
                with open(f'./{file_name}', 'wb+') as f:
                    f.write(requests.get(result.url).content)
                print(f'图像已保存为: {file_name}')
            break
        elif task_status == 'FAILED':
            print(f'任务执行失败, status: {task_status}, code: {status_rsp.code}, message: {status_rsp.message}')
            break
        elif task_status == 'PENDING' or task_status == 'RUNNING':
            print('任务正在进行中，5秒后继续查询...')
            time.sleep(5)
        elif task_status == 'CANCELED':
            print('任务已被取消。')
            break
        else:
            print(f'未知任务状态: {task_status}，5秒后继续查询...')
            time.sleep(5)

# 取消异步任务，只有处于PENDING状态的任务才可以取消
def cancel_task(task):
    rsp = ImageSynthesis.cancel(task)
    print(rsp)
    if rsp.status_code == HTTPStatus.OK:
        print(rsp.output.task_status)
    else:
        print(f'取消任务失败, status_code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}')


if __name__ == '__main__':
    async_call()