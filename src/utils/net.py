"""网络 / SSL 辅助。

在被企业代理或防火墙做 TLS 拦截的内网环境，HuggingFace / OpenAI 等权重下载常因
自签证书报 CERTIFICATE_VERIFY_FAILED（日志里常见 "self-signed certificate in certificate chain"）。

设置环境变量 JAC_HF_INSECURE=1 可关闭 SSL 证书校验，使 whisper / qwen-tts 等能正常下载权重。

安全说明：仅在被代理/防火墙做 TLS 拦截的【可信内网】使用；会跳过证书校验、存在中间人攻击风险。
必须显式开启（环境变量），绝不作为默认行为。
"""
import os
import ssl


def setup_insecure_ssl():
    """若 JAC_HF_INSECURE=1，则关闭 SSL 证书校验，使 whisper / qwen-tts / huggingface_hub 能正常下载。

    影响范围（仅在本进程内生效）：
      - HF_HUB_DISABLE_SSL_VERIFY=1  -> huggingface_hub / qwen-tts 不校验证书
      - PYTHONHTTPSVERIFY=0          -> whisper / torch.hub / urllib 走不校验的默认 HTTPS context
      - urllib3 警告屏蔽
    """
    if os.environ.get("JAC_HF_INSECURE") != "1":
        return
    print("[SSL] JAC_HF_INSECURE=1：关闭 SSL 证书校验（仅限可信内网/代理环境，存在中间人风险）。")
    os.environ["HF_HUB_DISABLE_SSL_VERIFY"] = "1"
    os.environ["PYTHONHTTPSVERIFY"] = "0"
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
    except Exception:
        pass
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
