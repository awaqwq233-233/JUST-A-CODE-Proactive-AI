"""只读文件名扫描子进程；父进程负责取消和硬超时，不读取文件正文。"""

import json
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import time


@contextmanager
def directory_entries(root, relative):
    """POSIX 逐级以 NOFOLLOW 打开目录，避免检查后链接替换导致越界扫描。"""
    if os.name != "posix":
        directory = root / relative
        if directory.resolve(strict=True) != directory:
            raise OSError("扫描目录已变化")
        with os.scandir(directory) as entries:
            yield entries
        return
    home = Path.home().resolve()
    descriptor = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (*root.relative_to(home).parts, *relative.parts):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        with os.scandir(descriptor) as entries:
            yield entries
    finally:
        os.close(descriptor)


def scan_files(arguments):
    """核对真实目录并有界扫描；跳过隐藏项、符号链接和非普通文件。"""
    root = Path(arguments["path"])
    home = Path.home().resolve()
    resolved = root.resolve(strict=True)
    if (resolved != root or not resolved.is_relative_to(home) or resolved == home
            or any(part.startswith(".") for part in root.relative_to(home).parts)
            or not root.is_dir()):
        raise ValueError("搜索范围不是主目录内的普通子目录")
    query = arguments["query"].casefold()
    limit = arguments["max_results"]
    matches, pending, scanned, unreadable = [], [(Path(), 0)], 0, 0
    truncated, deadline = False, time.monotonic() + 4
    while pending:
        relative, depth = pending.pop()
        try:
            with directory_entries(root, relative) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > 60000 or time.monotonic() >= deadline:
                        truncated = True
                        break
                    if entry.name.startswith(".") or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < 20:
                            pending.append((relative / entry.name, depth + 1))
                        else:
                            truncated = True
                    elif entry.is_file(follow_symlinks=False) and query in entry.name.casefold():
                        candidate = root / relative / entry.name
                        if candidate.resolve(strict=True) != candidate:
                            continue
                        matches.append(str(candidate))
                        if len(matches) >= limit:
                            truncated = True
                            break
        except OSError:
            unreadable += 1
        if len(matches) >= limit or scanned > 60000 or time.monotonic() >= deadline:
            truncated = True
            break
    return dict(status="partial" if truncated or unreadable else "completed", matches=matches,
                scanned=min(scanned, 60000), unreadable=unreadable, truncated=truncated,
                path=str(root), query=arguments["query"])


def main():
    """只接受一条有界 JSON 请求，UTF-8 输出结构化结果或明确错误。"""
    try:
        data = json.loads(sys.stdin.buffer.read(16385).decode("utf-8"))
        if (set(data) != {"path", "query", "max_results"} or not isinstance(data["path"], str)
                or not isinstance(data["query"], str) or not 1 <= len(data["query"]) <= 80
                or type(data["max_results"]) is not int or not 1 <= data["max_results"] <= 20):
            raise ValueError("搜索参数无效")
        result = scan_files(data)
    except (OSError, ValueError, KeyError, TypeError):
        result = dict(status="failed", reason="目录不存在、不可访问或超出搜索范围")
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
