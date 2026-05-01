import os
from http import HTTPStatus
from dashscope import VideoSynthesis
import dashscope

# 以下为北京地域URL，各地域的URL不同
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

# 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
# 各地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
api_key = os.getenv("DASHSCOPE_API_KEY")

def sample_async_call_r2v():
    # 异步调用，返回一个task_id
    rsp = VideoSynthesis.async_call(
        api_key=api_key,
        model='wan2.7-r2v',
        prompt='视频1抱着图3，在图4的椅子上弹奏一支舒缓的乡村民谣，并说道：“今天的阳光真好。”图1手中拿着图2，路过视频1，把手中的图2放到视频1旁边的桌子上，并说道：“真好听，能不能再唱一遍”。 ',
        media=[
            {
                "type": "reference_image",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260408/sjuytr/wan-r2v-object-girl.jpg",
                "reference_voice": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260408/gbqewz/wan-r2v-girl-voice.mp3"
            },
            {
                "type": "reference_video",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/qigswt/wan-r2v-role2.mp4",
                "reference_voice": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260408/isllrq/wan-r2v-boy-voice.mp3"
            },
            {
                "type": "reference_image",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/rtjeqf/wan-r2v-object3.png"
            },
            {
                "type": "reference_image",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/qpzxps/wan-r2v-object4.png"
            },
            {
                "type": "reference_image",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/wfjikw/wan-r2v-backgroud5.png"
            }
        ],
        resolution='720P',
        ratio='16:9',
        duration=10,
        prompt_extend=False,
        watermark=True)
    print(rsp)
    if rsp.status_code == HTTPStatus.OK:
        print("task_id: %s" % rsp.output.task_id)
    else:
        print('Failed, status_code: %s, code: %s, message: %s' %
              (rsp.status_code, rsp.code, rsp.message))

    # 获取异步任务信息
    status = VideoSynthesis.fetch(task=rsp, api_key=api_key)
    if status.status_code == HTTPStatus.OK:
        print(status.output.task_status)
    else:
        print('Failed, status_code: %s, code: %s, message: %s' %
              (status.status_code, status.code, status.message))

    # 等待异步任务结束
    rsp = VideoSynthesis.wait(task=rsp, api_key=api_key)
    print(rsp)
    if rsp.status_code == HTTPStatus.OK:
        print(rsp.output.video_url)
    else:
        print('Failed, status_code: %s, code: %s, message: %s' %
              (rsp.status_code, rsp.code, rsp.message))

curl --location 'https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis' \
    -H 'X-DashScope-Async: enable' \
    -H "Authorization: Bearer $DASHSCOPE_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{
    "model": "wan2.7-r2v",
    "input": {
        "prompt": "参考图片，3D卡通冒险电影风，角色Q版但材质细腻，动作流畅，色彩鲜明，保持角色与森林场景一致，不要加入文字。氛围： 冒险、轻快、神秘、童趣。角色： 小男孩探险家：圆帽、背包、短斗篷。小伙伴：会飞的小机器人，圆形身体，蓝色发光眼。场景： 奇幻森林，巨大树根、蘑菇、藤蔓、藏宝洞口、阳光光束。分镜脚本： 1. 全景：奇幻森林里高大树木与光束交错，环境神秘明亮。 2. 中景：小男孩拨开藤蔓向前探路。 3. 中景：小机器人飞在他身边，用蓝光扫描前方。 4. 特写：一张旧藏宝图在男孩手里展开。 5. 近景：他露出兴奋表情，眼睛亮起来。 6. 动作镜头：两人跳过树根和小溪，继续深入森林。 7. 中景：藤蔓后方露出一个被苔藓覆盖的宝箱。 8. 特写：宝箱边缘闪出金色光芒。 9. 收束镜头：男孩和小机器人站在宝箱前惊喜对望，冒险感拉满。",
        "media": [
            {
                "type": "reference_image",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260403/wgjaxy/banana_storyboard_00000020.png"
            }
        ]
    },
    "parameters": {
        "resolution": "720P",
        "duration": 10,
        "prompt_extend": false,
        "watermark": true
    }
}'


if __name__ == '__main__':
    sample_async_call_r2v()