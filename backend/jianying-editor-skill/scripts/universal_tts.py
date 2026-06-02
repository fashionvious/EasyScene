import asyncio
import json
import os
import re
import ssl
from typing import Optional, Tuple

import websockets
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from utils.config import CONFIG


def get_jy_local_config() -> Tuple[str, str]:
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        return "1053764930506284", "2314914062247833"

    jy_user_data = os.path.join(local_app_data, r"JianyingPro\User Data")
    cfg = {"device_id": "1053764930506284", "iid": "2314914062247833"}

    ttnet_path = os.path.join(jy_user_data, r"TTNet\tt_net_config.config")
    if os.path.exists(ttnet_path):
        try:
            with open(ttnet_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            m = re.search(r"device_id&#\*(\d+)", content)
            if m:
                cfg["device_id"] = m.group(1)
        except Exception:
            pass

    log_dir = os.path.join(jy_user_data, "Log")
    if os.path.exists(log_dir):
        logs = sorted(
            [os.path.join(log_dir, x) for x in os.listdir(log_dir) if x.endswith(".log")],
            key=os.path.getmtime,
            reverse=True,
        )
        for p in logs[:5]:
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    chunk = f.read(1_000_000)
                m = re.search(r"iid=(\d+)", chunk)
                if m:
                    cfg["iid"] = m.group(1)
                    break
            except Exception:
                continue

    return cfg["device_id"], cfg["iid"]


APP_KEY = "IZjhUeAYwP"
APP_ID = "3704"


def _build_ssl_context() -> ssl.SSLContext:
    if CONFIG.tts_insecure_ssl:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        print("[!] WARNING: TLS verification disabled by JY_TTS_INSECURE_SSL=1", flush=True)
        return ctx
    return ssl.create_default_context()


async def _run_sami_tts(text: str, speaker: str, output_file: str, dev_id: str, iid: str):
    """核心 SAMI TTS 调用——让可重试异常向上传播，由装饰器处理"""
    ws_url = f"wss://sami.bytedance.com/internal/api/v2/ws?device_id={dev_id}&iid={iid}"
    headers = {
        "User-Agent": f"JianyingPro/5.9.0.11632 (Windows 10.0.19045; app_id:3704; device_id:{dev_id})"
    }
    ssl_context = _build_ssl_context()

    # 仅捕获不可重试的异常；ConnectionError/TimeoutError/OSError 向上传播供 tenacity 重试
    try:
        async with websockets.connect(
            ws_url, additional_headers=headers, ssl=ssl_context, open_timeout=20
        ) as ws:
            task_id = f"ai_gen_{os.urandom(4).hex()}"
            start_msg = {
                "app_id": APP_ID,
                "appkey": APP_KEY,
                "event": "StartTask",
                "namespace": "TTS",
                "task_id": task_id,
                "message_id": task_id + "_0",
                "payload": json.dumps(
                    {
                        "text": text,
                        "speaker": speaker,
                        "audio_config": {
                            "format": "ogg_opus",
                            "sample_rate": 24000,
                            "bit_rate": 64000,
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
            await ws.send(json.dumps(start_msg, ensure_ascii=False, separators=(",", ":")))
            await ws.send(
                json.dumps({"appkey": APP_KEY, "event": "FinishTask", "namespace": "TTS"})
            )

            audio_data = bytearray()
            while True:
                try:
                    resp_raw = await asyncio.wait_for(ws.recv(), timeout=15)
                except asyncio.TimeoutError:
                    return False, "SAMI Timeout"

                if isinstance(resp_raw, str):
                    resp = json.loads(resp_raw)
                    event = resp.get("event")
                    if event == "TaskFailed":
                        return (
                            False,
                            f"SAMI Error: {resp.get('status_text')} "
                            f"(Code: {resp.get('status_code')})",
                        )
                    if event == "TaskFinished":
                        break
                else:
                    audio_data.extend(resp_raw)

            if audio_data:
                with open(output_file, "wb") as f:
                    f.write(audio_data)
                return True, output_file
            return False, "No audio"
    except (json.JSONDecodeError, KeyError) as e:
        # 协议错误不可重试
        return False, f"SAMI protocol error: {e}"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
)
async def _run_sami_tts_with_retry(text: str, speaker: str, output_file: str, dev_id: str, iid: str):
    """带指数退避重试的 SAMI TTS 调用"""
    return await _run_sami_tts(text, speaker, output_file, dev_id, iid)


async def _run_edge_tts(text: str, output_file: str, voice: str = "zh-CN-YunxiNeural"):
    try:
        import edge_tts

        actual_path = output_file
        if output_file.endswith(".ogg") and not output_file.endswith(".mp3"):
            actual_path = output_file + ".mp3"

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(actual_path)
        return True, actual_path
    except Exception as e:
        return False, f"Edge-TTS Error: {str(e)}"


async def generate_voice_with_meta(
    text: str,
    output_path: str,
    speaker: str = "zh_male_huoli",
    *,
    backend: Optional[str] = None,
    allow_fallback: bool = True,
    sami_retries: int = 2,
) -> Tuple[Optional[str], Optional[str]]:
    """
    backend: None | "sami" | "edge"
    allow_fallback: when True, SAMI failure may fallback to edge.
    returns: (audio_path, backend_used)

    注意：sami_retries 参数已废弃——重试次数现在由 _run_sami_tts_with_retry
    上的 @retry(stop=stop_after_attempt(3)) 控制，不再使用手动循环。
    """
    dev_id, iid = get_jy_local_config()
    print(f"[*] Intelligent TTS Trace: speaker={speaker}, dev={dev_id}, iid={iid}", flush=True)

    force_sami = backend == "sami"
    force_edge = backend == "edge"

    if not force_edge:
        try:
            ok, res = await _run_sami_tts_with_retry(
                text, speaker, output_path, dev_id, iid,
            )
            if ok:
                print(f"[+] SAMI Success: {res}", flush=True)
                return res, "sami"
            print(f"[!] SAMI Failed (non-retryable): {res}", flush=True)
        except (ConnectionError, TimeoutError, OSError) as e:
            print(
                f"[!] SAMI Failed after 3 retries: {e}", flush=True,
            )

        if force_sami or not allow_fallback:
            return None, None

    voice = "zh-CN-YunxiNeural" if "male" in speaker else "zh-CN-XiaoxiaoNeural"
    ok_edge, res_edge = await _run_edge_tts(text, output_path, voice)
    if ok_edge:
        print(f"[+] Edge-TTS Success: {res_edge}", flush=True)
        return res_edge, "edge"
    return None, None


async def generate_voice(
    text: str,
    output_path: str,
    speaker: str = "zh_male_huoli",
    *,
    backend: Optional[str] = None,
    allow_fallback: bool = True,
    sami_retries: int = 2,
):
    path, _backend_used = await generate_voice_with_meta(
        text,
        output_path,
        speaker,
        backend=backend,
        allow_fallback=allow_fallback,
        sami_retries=sami_retries,
    )
    return path


if __name__ == "__main__":
    asyncio.run(generate_voice("测试智能配音系统集成成功。", "test.ogg"))
