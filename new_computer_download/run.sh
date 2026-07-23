#!/usr/bin/env bash
# J.A.C. 新电脑一键依赖补全 —— macOS / Linux 启动器
# 用法：在终端进入本目录后执行  bash run.sh   或  ./run.sh
# 可附带参数原样传给 setup_new_computer.py，例如：./run.sh --skip-models
set -e

cd "$(dirname "$0")"

# 优先用 python3，找不到再退回 python
if command -v python3 >/dev/null 2>&1; then
  PY="${PYTHON:-python3}"
else
  PY="${PYTHON:-python}"
fi

echo ">>> 使用 Python: $($PY --version 2>&1)"
echo ">>> 项目根目录: $(dirname "$PWD")"
echo

"$PY" "$(dirname "$0")/setup_new_computer.py" "$@"
