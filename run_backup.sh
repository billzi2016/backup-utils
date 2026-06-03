#!/bin/bash
# 一键启动备份脚本
# 用法：直接在终端运行 bash /Volumes/LuZhang/backup_utils/run_backup.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 检查目标盘是否挂载
if [ ! -d "/Volumes/LuZhang16T" ]; then
    echo "[错误] 目标盘 /Volumes/LuZhang16T 未挂载，请先接上硬盘"
    exit 1
fi

echo "正在启动备份，日志保存在 $SCRIPT_DIR"
python3 "$SCRIPT_DIR/backup.py"
