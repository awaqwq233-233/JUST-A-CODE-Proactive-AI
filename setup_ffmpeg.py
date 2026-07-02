import imageio_ffmpeg
import os
import shutil
import sys

def setup_ffmpeg():
    print("=== FFmpeg 诊断与修复 ===")
    try:
        exe_path = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"[信息] imageio-ffmpeg 找到的路径: {exe_path}")
        
        ffmpeg_dir = os.path.dirname(exe_path)
        ffmpeg_filename = os.path.basename(exe_path)
        
        print(f"[信息] 目录: {ffmpeg_dir}")
        print(f"[信息] 文件名: {ffmpeg_filename}")
        
        # 目标：在项目根目录创建一个 ffmpeg.exe
        # 这样 main.py 只要把当前目录加入 PATH，或者直接调用都能找到
        project_root = os.getcwd()
        target_path = os.path.join(project_root, "ffmpeg.exe")
        
        if os.path.exists(target_path):
            print(f"[信息] 项目根目录下已存在 ffmpeg.exe，跳过复制。")
        else:
            print(f"[操作] 正在将 {ffmpeg_filename} 复制为 ffmpeg.exe 到项目根目录...")
            shutil.copy2(exe_path, target_path)
            print("[成功] 复制完成！")
            
        # 验证
        if os.path.exists(target_path):
             print(f"[验证] ffmpeg.exe 位于: {target_path}")
             # 尝试运行一下看版本
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