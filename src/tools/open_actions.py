"""打开应用 / 打开网页工具（跨平台，macOS 优先）。"""
import platform
import subprocess

# 平台标识，避免和 main.py 的全局常量耦合，这里独立判断一次
_PLATFORM = platform.system()  # 'Darwin' / 'Windows' / 'Linux'


def open_url(arguments):
    """在系统默认浏览器中打开一个网页网址。

    参数：url —— 必须以 http:// 或 https:// 开头（拦截本地协议，避免被滥用拉起其它程序）。
    """
    url = (arguments or {}).get("url", "")
    if not url:
        return "错误：缺少 url 参数"
    # 安全收敛：仅允许 http/https，拒绝 file:// / app:// 等本地协议
    if not (url.lower().startswith("http://") or url.lower().startswith("https://")):
        return f"错误：出于安全限制，仅支持 http/https 网址，已拒绝：{url}"

    try:
        if _PLATFORM == "Darwin":
            subprocess.run(["open", url], check=True)
        elif _PLATFORM == "Windows":
            # start 是 cmd 内建命令，必须通过 cmd /c 触发；标题参数留空串
            subprocess.run(["cmd", "/c", "start", "", url], check=True)
        else:
            subprocess.run(["xdg-open", url], check=True)
        return f"已为你打开网址：{url}"
    except Exception as e:
        return f"打开网址失败：{e}"


def open_app(arguments):
    """按名称打开一个已安装的桌面应用程序（如 备忘录、Safari、终端）。

    参数：app_name —— 应用名称，macOS 下即 "打开方式" 里显示的名字。
    """
    name = (arguments or {}).get("app_name") or (arguments or {}).get("name") or ""
    if not name:
        return "错误：缺少 app_name 参数"

    try:
        if _PLATFORM == "Darwin":
            # -a 后接应用名，macOS 会自动定位 /Applications 下的 .app
            subprocess.run(["open", "-a", name], check=True)
        elif _PLATFORM == "Windows":
            subprocess.run(["cmd", "/c", "start", "", name], check=True)
        else:
            # Linux 用桌面文件启动；失败则提示
            r = subprocess.run(["gtk-launch", name], capture_output=True, text=True)
            if r.returncode != 0:
                return f"已尝试用 gtk-launch 打开 {name}，但当前 Linux 环境未找到对应桌面项（可改用 xdg-open 指定 .desktop 路径）。"
        return f"已尝试打开应用：{name}"
    except Exception as e:
        return f"打开应用失败：{e}"
