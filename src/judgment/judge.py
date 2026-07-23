# J.A.C. 环境判断引擎
# 连接 LM Studio 中运行的 MiniCPM-o，持续监测摄像头画面+音频转录，
# 决定是否需要 J.A.C. 大模型介入

import base64
import cv2
import json
import logging
import queue
import time
from collections import deque
from dataclasses import dataclass

import requests

logger = logging.getLogger("judge")


@dataclass
class InterventionRequest:
    """判断引擎发出的介入请求"""
    reason: str
    transcript: str
    timestamp: float


class JudgmentEngine:
    """
    环境判断引擎。
    每 interval 秒从 SharedContext 获取最新摄像头帧 + 最近音频转录，
    发送给 LM Studio 中运行的 MiniCPM-o 模型，
    模型返回 INTERVENE: <reason> 或 SILENT，
    INTERVENE 时推入 intervention_queue。
    """

    JUDGE_SYSTEM_PROMPT = (
        "你是智能眼镜助手J.A.C.的环境判断模块。\n\n"
        "你的任务：持续观察摄像头画面和用户的语音情况，判断是否需要J.A.C.主动介入帮助。\n\n"
        "【需要介入的场景】\n"
        "- 用户明确叫了J.A.C.或类似唤醒词\n"
        "- 画面中的人表现出困惑、焦急、痛苦、需要帮助的表情或动作\n"
        "- 有人在提问或寻求帮助\n"
        "- 环境中有异常或紧急情况\n"
        "- 发生对话需要J.A.C.参与\n"
        "- 用户看起来在等待或困惑\n\n"
        "【不需要介入的场景】\n"
        "- 人们正常交谈，没有寻求帮助\n"
        "- 一切平静正常，没有人需要帮助\n"
        "- 画面中没有人\n"
        "- 对话内容不需要J.A.C.的参与\n\n"
        "请分析下面提供的【当前画面描述】和【最近音频转录】，然后输出以下格式之一：\n"
        "INTERVENE: 为什么需要介入的简要原因\n"
        "SILENT\n\n"
        "直接输出判断结果，不要输出其他内容。"
    )

    def __init__(self, api_url="http://127.0.0.1:12345/v1/chat/completions", check_url="http://127.0.0.1:12345/v1/models", model_name="minicpm-v-4_5", interval=4.0, timeout=15.0, transcription_window=15.0):
        self.api_url = api_url
        self.check_url = check_url
        self.model_name = model_name
        self.interval = interval
        self.timeout = timeout
        self.transcription_window = transcription_window
        self.running = True
        self._available = False
        self._recheck_at = 0  # 模型不可用时，周期性重新检测的节流时间戳
        # 视觉能力：默认尝试发图；若模型报"不支持图像输入"则自动降级为纯文本判断
        self._vision_supported = True
        self.context = None
        self.intervention_queue = queue.Queue()

    @staticmethod
    def _normalize(name):
        """规范化模型 ID：小写、去 -gguf 后缀、下划线转连字符，便于跨命名风格匹配。"""
        return name.lower().replace("-gguf", "").replace(".gguf", "").replace("_", "-").strip()

    def _match_model(self, loaded_ids):
        """在已下载/加载的模型 ID 中，大小写不敏感地模糊匹配目标模型，命中返回真实 ID。"""
        target = self._normalize(self.model_name)
        for mid in loaded_ids:
            if not mid:
                continue
            norm = self._normalize(mid)
            if norm == target or target in norm or norm in target:
                return mid
        return None

    def check_available(self):
        try:
            resp = requests.get(self.check_url, timeout=3)
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                loaded_ids = [m.get("id", "") for m in models]
                matched = self._match_model(loaded_ids)
                if matched:
                    if matched != self.model_name:
                        logger.info("判断模型 ID 匹配: 配置 '%s' -> 实际 '%s'", self.model_name, matched)
                    # 回填真实 ID，保证后续 judge() 的 payload["model"] 用对名字
                    self.model_name = matched
                    logger.info("检测到判断模型: %s (API: %s)", self.model_name, self.api_url)
                    self._available = True
                    return True
                elif loaded_ids:
                    logger.warning("MiniCPM-o (%s) 未在 LM Studio 加载的模型中 (%s)", self.model_name, loaded_ids)
        except requests.ConnectionError:
            pass
        except Exception as exc:
            logger.warning("判断模型可用性检测异常: %s", exc)

        logger.warning("MiniCPM-o 判断模型服务不可用，判断引擎将进入被动模式")
        self._available = False
        return False

    @property
    def available(self):
        return self._available

    def judge(self, frame, transcript_text):
        if not self._available:
            return False, ""

        user_content = []
        text_parts = ["【当前环境信息】"]
        if transcript_text:
            text_parts.append(f"最近音频转录：{transcript_text}")
        else:
            text_parts.append("最近音频：无语音输入")
        text_parts.append("\n请判断是否需要J.A.C.介入。")
        user_content.append({"type": "text", "text": "\n".join(text_parts)})

        # 仅当模型支持视觉时才附带图像；不支持则走纯文本（基于音频转录）判断
        if frame is not None and self._vision_supported:
            try:
                ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ret:
                    img_b64 = base64.b64encode(buffer).decode("utf-8")
                    user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
            except Exception as exc:
                logger.warning("判断引擎图像编码失败: %s", exc)

        messages = [
            {"role": "system", "content": self.JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        payload = {"model": self.model_name, "messages": messages, "temperature": 0.2, "max_tokens": 64, "stream": False}

        try:
            resp = requests.post(self.api_url, json=payload, timeout=self.timeout, headers={"Content-Type": "application/json"})
            if resp.status_code != 200:
                err_text = resp.text[:300]
                # 模型被 LM Studio 卸载（通常因显存不足被挤出）：标记为不可用，run 循环会周期性重试
                if resp.status_code == 400 and "unloaded" in err_text.lower():
                    logger.warning("判断模型已被 LM Studio 卸载（可能因显存不足被挤出）。请在 LM Studio 重新加载 '%s'；判断引擎将周期性自动重试。", self.model_name)
                    self._available = False
                    return False, ""
                # 模型不支持图像输入：自动降级为纯文本判断，并立即用纯文本重试一次
                if resp.status_code == 400 and self._vision_supported and "image" in err_text.lower():
                    logger.warning("判断模型不支持图像输入，自动降级为纯文本判断（后续不再发送画面）")
                    self._vision_supported = False
                    return self.judge(None, transcript_text)
                logger.warning("判断模型 API 返回 %s: %s", resp.status_code, err_text)
                return False, ""
            content = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("判断模型请求异常: %s", exc)
            return False, ""

        if content.startswith("INTERVENE"):
            reason = content[len("INTERVENE:"):].strip()
            logger.info("判断模型决定介入: %s", reason)
            return True, reason
        else:
            logger.debug("判断模型决定保持静默: %s", content[:60])
            return False, ""

    def run(self):
        if self.context is None:
            logger.error("判断引擎未注入 SharedContext，无法启动")
            return

        self.check_available()
        logger.info("判断引擎主循环启动 (interval=%ss, available=%s)", self.interval, self._available)

        while self.running:
            # 模型不可用（被卸载 / 未加载）：每隔约 20s 重新检测，恢复后自动继续，避免日志刷屏
            if not self._available:
                now = time.time()
                if now >= self._recheck_at:
                    self.check_available()
                    self._recheck_at = now + 20
                time.sleep(self.interval)
                continue

            loop_start = time.time()
            try:
                frame = self.context.get_frame()
                transcript = self.context.get_recent_transcriptions(window=self.transcription_window)
                should_intervene, reason = self.judge(frame, transcript)
                if should_intervene:
                    req = InterventionRequest(reason=reason, transcript=transcript, timestamp=time.time())
                    self.intervention_queue.put(req)
                    logger.info("判断结果: INTERVENE - %s", reason)
                else:
                    logger.debug("判断结果: 保持静默")
            except Exception as exc:
                logger.error("判断循环异常: %s", exc, exc_info=True)

            elapsed = time.time() - loop_start
            time.sleep(max(0.1, self.interval - elapsed))

        logger.info("判断引擎主循环已停止")

    def stop(self):
        self.running = False

    def set_context(self, context):
        self.context = context

    def get_intervention(self, timeout=0.1):
        try:
            return self.intervention_queue.get_nowait()
        except queue.Empty:
            return None

    @staticmethod
    def default_config():
        return {
            "api_url": "http://127.0.0.1:12345/v1/chat/completions",
            "check_url": "http://127.0.0.1:12345/v1/models",
            "model_name": "minicpm-v-4_5",
            "interval": 4.0,
            "transcription_window": 15.0,
        }
