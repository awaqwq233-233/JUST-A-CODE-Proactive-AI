import imageio_ffmpeg
import os
import shutil
import sys
import platform

def setup_ffmpeg():
    print("=== FFmpeg 诊断与修复 ===")
    try:
        exe_path = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"[信息] imageio-ffmpeg 找到的路径: {exe_path}")

        ffmpeg_dir = os.path.dirname(exe_path)
        ffmpeg_filename = os.path.basename(exe_path)

        print(f"[信息] 目录: {ffmpeg_dir}")
        print(f"[信息] 文件名: {ffmpeg_filename}")

        # 目标文件名按平台区分：Windows 用 ffmpeg.exe，macOS/Linux 用 ffmpeg
        is_windows = platform.system() == "Windows"
        target_name = "ffmpeg.exe" if is_windows else "ffmpeg"
        project_root = os.getcwd()
        target_path = os.path.join(project_root, target_name)

        if os.path.exists(target_path):
            print(f"[信息] 项目根目录下已存在 {target_name}，跳过复制。")
        else:
            print(f"[操作] 正在将 {ffmpeg_filename} 复制为 {target_name} 到项目根目录...")
            shutil.copy2(exe_path, target_path)
            # 非 Windows 平台需要显式给执行权限（Unix 不认扩展名，且默认无 x 位）
            if not is_windows:
                try:
                    os.chmod(target_path, 0o755)
                except OSError:
                    pass
            print("[成功] 复制完成！")

        # 验证
        if os.path.exists(target_path):
            print(f"[验证] ffmpeg 位于: {target_path}")
            import subprocess
            try:
                output = subprocess.check_output([target_path, "-version"], stderr=subprocess.STDOUT)
                print(f"[测试] ffmpeg 运行正常:\n{output.decode('utf-8').splitlines()[0]}")
            except Exception as e:
                print(f"[错误] ffmpeg 运行测试失败: {e}")

    except Exception as e:
        print(f"[错误] 诊断过程中出错: {e}")

if __name__ == "__main__":
    setup_ffmpeg()
