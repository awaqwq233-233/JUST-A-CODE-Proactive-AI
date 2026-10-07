#!/usr/bin/env python3
"""安装期下载锁定 Whisper 资源；HTTPS 镜像回退官方，运行期仅离线使用。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.omni.transcription_worker import DEFAULT_MODEL_DIR, WhisperProcess, verify_resources


def download_in_ranges(client, url, path, size, part_size=8 * 1024 * 1024):
    """大资源用四条有界 Range 流；逐段核对偏移/长度，最终仍验证整文件哈希。"""
    with client.stream("GET", url, headers={"Range": "bytes=0-0"}) as response:
        response.raise_for_status()
        if response.status_code != 206 or response.headers.get("content-range") != f"bytes 0-0/{size}":
            raise ValueError("下载源不支持精确 Range")
        resolved = str(response.url)
    with path.open("wb") as handle:
        handle.truncate(size)
    started, progress, last_report, lock = time.monotonic(), [0], [0], threading.Lock()
    failed = threading.Event()

    def part(start):
        """写入互不重叠的文件片段，不接受忽略 Range 的响应。"""
        import httpx
        for attempt in range(3):
            try:
                return write_part(start)
            except httpx.HTTPError:
                if attempt == 2 or failed.is_set():
                    raise

    def write_part(start):
        """仅重试传输失败；错误偏移、长度或超时上限直接拒绝。"""
        if failed.is_set():
            raise ValueError("分段下载已取消")
        end = min(start + part_size, size) - 1
        with client.stream("GET", resolved, headers={"Range": f"bytes={start}-{end}"}) as response:
            response.raise_for_status()
            if response.status_code != 206 or response.headers.get("content-range") != f"bytes {start}-{end}/{size}":
                raise ValueError("下载分段偏移不符")
            count = 0
            with path.open("r+b") as handle:
                handle.seek(start)
                for chunk in response.iter_bytes(1024 * 1024):
                    count += len(chunk)
                    if failed.is_set() or count > end - start + 1 or time.monotonic() - started > 600:
                        raise ValueError("下载分段超过长度或总时限")
                    handle.write(chunk)
            if count != end - start + 1:
                raise ValueError("下载分段不完整")
        with lock:
            progress[0] += count
            if progress[0] - last_report[0] >= 50 * 1024 * 1024:
                last_report[0] = progress[0]
                print(f"[下载] model.bin {progress[0] // (1024 * 1024)} / {size // (1024 * 1024)} MiB", flush=True)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(part, start) for start in range(0, size, part_size)]
        try:
            for future in futures:
                future.result()
        except BaseException:
            failed.set()
            for future in futures:
                future.cancel()
            raise


def download_resources(directory):
    """哈希正确则复用；仅将验证过的资源原子安装到仓库外。"""
    import httpx
    directory = Path(directory).expanduser().resolve()
    if directory.is_relative_to(ROOT):
        raise ValueError("Whisper 模型必须放在仓库外")
    directory.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / "transcription.lock.json").read_text(encoding="utf-8"))
    for name, expected in lock["files"].items():
        path = directory / name
        if path.is_file():
            with path.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() == expected:
                    continue
        temporary = directory / (name + ".part")
        completed = False
        for host in ("https://hf-mirror.com", "https://huggingface.co"):
            try:
                url = f"{host}/{lock['repository']}/resolve/{lock['revision']}/{name}?download=true&jac_cache={int(time.time())}"
                digest = hashlib.sha256()
                started, received = time.monotonic(), 0
                with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(20, connect=10), trust_env=False) as client:
                    size = lock.get("sizes", {}).get(name, 0)
                    if size > 50 * 1024 * 1024:
                        download_in_ranges(client, url, temporary, size)
                        with temporary.open("rb") as handle:
                            digest = hashlib.file_digest(handle, "sha256")
                    else:
                        with client.stream("GET", url) as response:
                            response.raise_for_status()
                            with temporary.open("wb") as handle:
                                for chunk in response.iter_bytes(1024 * 1024):
                                    received += len(chunk)
                                    elapsed = time.monotonic() - started
                                    if elapsed > 180 or (elapsed > 20 and received / elapsed < 128 * 1024):
                                        raise httpx.ReadTimeout("下载源过慢，切换备用源")
                                    digest.update(chunk)
                                    handle.write(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError("下载资源哈希不符")
                os.replace(temporary, path)
                completed = True
                print(f"[资源] {name} 校验通过", flush=True)
                break
            except (httpx.HTTPError, ValueError) as error:
                reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                print(f"[下载] {name} 当前源失败（{reason}），尝试下一源", flush=True)
            finally:
                temporary.unlink(missing_ok=True)
        if not completed:
            raise RuntimeError(f"Whisper 资源下载失败：{name}")


def main(argv=None):
    """准备资源并可启动 CPU 子进程自检，不打开任何设备。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path(os.environ.get("JAC_WHISPER_MODEL_DIR", str(DEFAULT_MODEL_DIR))))
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.download_models:
        download_resources(args.model_dir)
    verify_resources(args.model_dir)
    if args.self_test:
        worker = WhisperProcess(args.model_dir)
        try:
            worker.start()
        finally:
            worker.stop()
            worker.close()
    print("[通过] Whisper 资源与所请求的 CPU 自检已完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
