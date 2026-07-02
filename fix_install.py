import sys
import subprocess
import platform
import os
import urllib.request

def install_package(package_name):
    print(f"[安装] 正在安装 {package_name} ...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        print(f"[成功] {package_name} 安装完成。")
    except subprocess.CalledProcessError:
        print(f"[失败] {package_name} 安装失败。")

def install_pyaudio_wheel():
    """
    手动下载并安装 PyAudio 的 whl 文件，避开编译错误。
    """
    print("\n[系统] 检测到 Windows 环境，正在尝试下载 PyAudio 预编译包...")
    
    # 检查 Python 版本
    py_ver = sys.version_info
    # 构造版本字符串，如 "cp311"
    ver_str = f"cp{py_ver.major}{py_ver.minor}"
    
    # 检查架构 (64位或32位)
    arch = platform.architecture()[0]
    if arch == '64bit':
        arch_str = "win_amd64"
    else:
        arch_str = "win32"
    
    # 凯撒大学 (UCI) 镜像站不仅全，而且通常兼容性最好，但 URL 不固定。
    # 为了稳定，我们使用 pip 的 --only-binary 选项或 --prefer-binary
    # 或者尝试从清华源安装，有时清华源会有预编译包。
    
    print("[尝试 1] 使用 pip win_amd64 预编译包安装...")
    try:
        # 尝试安装 pipwin，它是一个专门用来在 Windows 上安装二进制包的工具
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pipwin"])
        # 使用 pipwin 安装 pyaudio
        subprocess.check_call([sys.executable, "-m", "pipwin", "install", "pyaudio"])
        print("[成功] PyAudio 通过 pipwin 安装成功！")
        return
    except Exception as e:
        print(f"[失败] pipwin 方法失败: {e}")

    print("[尝试 2] 尝试直接强制使用二进制包...")
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", 
            "pyaudio", 
            "--only-binary=:all:",
            "--trusted-host", "pypi.org",
            "--trusted-host", "files.pythonhosted.org"
        ])
        print("[成功] PyAudio 安装成功！")
    except:
        print("[失败] 仍然无法安装 PyAudio。")
        print("请尝试手动下载对应版本的 .whl 文件：https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio")

def fix_install():
    print("==========================================")
    print("      J.A.C - 依赖修复工具                 ")
    print("==========================================")
    
    # 1. 升级 pip
    subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])

    # 2. 安装基础依赖
    print("\n>>> 安装基础视觉库...")
    install_package("opencv-python")
    install_package("ultralytics")
    
    # 3. 解决 PyAudio 问题
    print("\n>>> 解决 PyAudio (录音) 依赖...")
    try:
        import pyaudio
        print("[跳过] PyAudio 已安装。")
    except ImportError:
        install_pyaudio_wheel()

    # 4. 解决 llama-cpp-python 问题
    print("\n>>> 解决 llama-cpp-python (大脑) 依赖...")
    try:
        # Windows 下安装 llama-cpp-python 经常需要编译
        # 我们可以尝试安装预编译版本
        # 这里的链接是一个非官方的预编译仓库，通常很有效
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", 
            "llama-cpp-python", 
            "--extra-index-url", "https://abetlen.github.io/llama-cpp-python/whl/cpu"
        ])
    except Exception as e:
        print(f"[警告] llama-cpp-python 安装可能出错: {e}")
        print("如果这一步失败，请确保安装了 Visual Studio C++ Build Tools。")

    # 5. 解决 webrtcvad 问题
    print("\n>>> 解决 webrtcvad (语音检测) 依赖...")
    try:
        import webrtcvad
        print("[跳过] webrtcvad 已安装。")
    except ImportError:
        print("[安装] 正在安装 webrtcvad-wheels (Windows 兼容版)...")
        install_package("webrtcvad-wheels")

    print("\n==========================================")
    print("      修复尝试结束。请查看上方日志。       ")
    print("==========================================")

def install_ffmpeg_helper():
    """安装 imageio-ffmpeg 以获取 ffmpeg 可执行文件"""
    print("[系统] 正在安装 imageio-ffmpeg 以自动配置 ffmpeg 环境...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "imageio-ffmpeg"])
        print("[成功] imageio-ffmpeg 安装成功！")
    except Exception as e:
        print(f"[失败] imageio-ffmpeg 安装失败: {e}")

if __name__ == "__main__":
    print("=== J.A.C 环境修复工具 ===")
    fix_install() # 调用主修复流程
    # install_pyaudio_wheel() # 已经包含在 fix_install 中
    install_ffmpeg_helper()
    print("\n[提示] 如果 llama-cpp-python 依然报错，请手动安装 Visual Studio C++ Build Tools")
    print("=== 修复完成 ===")
