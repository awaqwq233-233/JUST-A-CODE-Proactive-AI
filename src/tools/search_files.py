"""本地文件搜索工具（只读，绝不修改任何文件）。"""
import os

# 默认只扫描用户的几个常用目录，避免误扫整个磁盘导致极慢
_DEFAULT_DIRS = ["Desktop", "Documents", "Downloads"]


def search_files(arguments):
    """在用户目录中按文件名关键字搜索（只读）。

    参数：
      query       必填，文件名包含的子串（不区分大小写）
      path        可选，指定单个起始目录（必须是用户目录下的路径）
      max_results 可选，最多返回条数，默认 20，最大 100
    """
    args = arguments or {}
    query = (args.get("query") or "").strip()
    if not query:
        return "错误：缺少 query 参数（要搜索的文件名关键字）"

    max_results = int(args.get("max_results") or 20)
    max_results = max(1, min(max_results, 100))

    home = os.path.expanduser("~")
    explicit = (args.get("path") or "").strip()
    if explicit:
        # 安全收敛：只允许搜索用户目录下的路径，避免扫到 /System、/etc 等
        base = os.path.abspath(os.path.expanduser(explicit))
        if not (base == home or base.startswith(home + os.sep)):
            return f"错误：出于安全限制，只能搜索用户目录（{home}）下的路径。"
        scan_dirs = [base]
    else:
        # 未指定路径时，只扫桌面/文档/下载三个常用目录
        scan_dirs = [os.path.join(home, d) for d in _DEFAULT_DIRS if os.path.isdir(os.path.join(home, d))]

    matches = []
    scanned = 0
    scan_limit = 60000  # 硬上限：避免扫描超大目录时卡死
    q = query.lower()
    try:
        for d in scan_dirs:
            for root, dirs, files in os.walk(d):
                # 跳过隐藏目录（如 .git、.cache）以提速并减少噪音
                dirs[:] = [name for name in dirs if not name.startswith(".")]
                for fname in files:
                    scanned += 1
                    if scanned > scan_limit:
                        break
                    if q in fname.lower():
                        matches.append(os.path.join(root, fname))
                        if len(matches) >= max_results:
                            break
                if len(matches) >= max_results or scanned > scan_limit:
                    break
            if len(matches) >= max_results or scanned > scan_limit:
                break
    except Exception as e:
        return f"搜索过程出错：{e}"

    if not matches:
        return f"未找到文件名包含「{query}」的文件（已扫描约 {scanned} 个文件）。"
    # 返回前若干条，避免结果过长撑爆上下文
    shown = matches[:max_results]
    head = "\n".join(shown)
    more = "" if len(matches) <= max_results else f"\n…（仅显示前 {max_results} 条，共匹配 {len(matches)} 个）"
    return f"找到 {len(matches)} 个匹配文件：\n{head}{more}"
