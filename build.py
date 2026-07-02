import PyInstaller.__main__
import os
import shutil

def build_exe():
    print("==========================================")
    print("      J.A.C - 构建工具 (正在打包...)       ")
    print("==========================================")
    
    # 清理旧的构建文件夹
    if os.path.exists('build'):
        shutil.rmtree('build')
    if os.path.exists('dist'):
        shutil.rmtree('dist')

    # PyInstaller 参数
    params = [
        'main.py',                 # 主程序入口
        '--name=JAC_Prototype',    # 生成的 exe 名字
        '--onedir',                # 生成文件夹模式 (比单文件启动更快，更易排错)
        '--console',               # 显示控制台窗口 (方便看日志)
        '--clean',                 # 清理缓存
        # 需要收集 ultralytics 的数据文件
        '--collect-all=ultralytics',
        # 确保 src 包被正确导入
        '--paths=.',
    ]

    print(f"[系统] 执行命令: pyinstaller {' '.join(params)}")
    
    try:
        PyInstaller.__main__.run(params)
        print("\n==========================================")
        print("      构建成功！                           ")
        print("      可执行文件位于: dist/JAC_Prototype/JAC_Prototype.exe")
        print("==========================================")
    except Exception as e:
        print(f"\n[错误] 构建失败: {e}")

if __name__ == "__main__":
    build_exe()
