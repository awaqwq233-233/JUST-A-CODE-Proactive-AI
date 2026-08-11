"""受限 shell 工具：只允许白名单内的只读/安全命令，拦截一切危险操作。"""
import shlex
import subprocess

# 允许执行的命令白名单（基命令必须在此列表内；参数任意但走 shell=False 防注入）
_ALLOWED = {
    "ls", "pwd", "date", "echo", "whoami", "uname", "hostname", "which",
    "cat", "head", "tail", "df", "du", "ps", "wc", "grep",
}

# 始终禁止的基命令（双重保险，即便不在白名单也显式拦截）
_FORBIDDEN = {
    "rm", "sudo", "su", "mv", "cp", "dd", "mkfs", "chmod", "chown", "kill",
    "shutdown", "reboot", "halt", "poweroff", "curl", "wget", "nc", "ssh",
    "scp", "ftp", "python", "python3", "perl", "ruby", "node", "git", "vim",
    "nano", "tee",
}


def run_command(arguments):
    """执行一条受限 shell 命令（仅白名单内的只读命令）。

    参数：command —— 要执行的命令字符串（如 "ls -la ~/Desktop"）。
    """
    args = arguments or {}
    command = (args.get("command") or "").strip()
    if not command:
        return "错误：缺少 command 参数"

    try:
        tokens = shlex.split(command)
    except Exception as e:
        return f"命令解析失败：{e}"

    if not tokens:
        return "错误：空命令"

    base = tokens[0]
    # 第一重：显式黑名单
    if base in _FORBIDDEN:
        return f"已拒绝执行危险命令：{base}（受限 shell 仅允许只读/安全命令）"
    # 第二重：白名单必须命中
    if base not in _ALLOWED:
        return (f"已拒绝执行未授权命令：{base}"
                f"（受限 shell 仅允许：{', '.join(sorted(_ALLOWED))}）")

    try:
        # 关键：shell=False，参数原样传递，杜绝 && / | / ; / > 等注入
        result = subprocess.run(
            tokens, capture_output=True, text=True, timeout=10, shell=False,
        )
        out = (result.stdout or "") + (result.stderr or "")
        out = out.strip()
        if not out:
            out = f"（命令 {base} 执行成功，无输出）"
        # 截断超长输出，避免撑爆上下文
        if len(out) > 2000:
            out = out[:2000] + "\n…（输出过长已截断）"
        return out
    except subprocess.TimeoutExpired:
        return f"命令执行超时（>10s）：{command}"
    except Exception as e:
        return f"命令执行失败：{e}"
