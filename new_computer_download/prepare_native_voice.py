#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载/自检本地音色前端，或为固定 C++ 后端生成参考音条件。"""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.omni.voice_conditioning import download_models, prepare_voice, validate_models


def main() -> int:
    """安装或准备音色条件；结构化结果只含路径/哈希，不含媒体或对话。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache/voices")
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        parser.error("原生音色前端必须使用项目 Python 3.11 环境")
    try:
        if args.download_models:
            download_models(args.model_dir)
        if args.self_test:
            validate_models(args.model_dir)
        audio = args.audio or (ROOT / "voices/silverwalf_voice.wav" if args.self_test else None)
        if audio is None:
            validate_models(args.model_dir)
        result = prepare_voice(audio, args.model_dir, args.cache_dir) if audio else {"models_verified": True}
        if args.result_file:
            args.result_file.write_text(json.dumps(result) + "\n", encoding="utf-8")
        else:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        # 前端错误必须让后端拒绝 session，不打印样本、Base64 或不受控服务端文本。
        print(f"原生音色准备失败: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
