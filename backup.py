#!/usr/bin/env python3
"""
exFAT -> APFS 鲁棒备份脚本
  源盘：/Volumes/LuZhang
  目标：/Volumes/LuZhang16T
- 自动跳过指定顶层目录和所有点开头的文件/目录
- 遇到错误不中断，写入 errors_*.log
- 支持断点续传（目标已存在且大小相同则跳过）
"""

import os
import sys
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path

# ── 配置 ───────────────────────────────────────────────────────────────────
SRC       = Path("/Volumes/LuZhang")
DST       = Path("/Volumes/LuZhang16T")
UTILS_DIR = SRC / "backup_utils"

# 不复制的顶层目录名（相对于 SRC）
EXCLUDED_TOPLEVEL = frozenset([
    "[只读]原始备份",
    "generated_mri",
    "IU_Datasets",
    "backup_utils",               # 脚本自身所在目录
    "$RECYCLE.BIN",
    "System Volume Information",
])
# ───────────────────────────────────────────────────────────────────────────


def is_dot_path(name: str) -> bool:
    """跳过所有点开头的文件/目录（._xxxx、.DS_Store、.Spotlight-V100 等）"""
    return name.startswith(".")


def copy_file_robust(src: Path, dst: Path) -> tuple[bool, str]:
    """
    用三种方法依次尝试拷贝文件，返回 (成功, 错误信息)。
    exFAT 上的文件常见问题：权限位缺失、文件名特殊字符等。
    """
    # 方法一：shutil.copy2（保留时间戳，忽略权限）
    try:
        shutil.copy2(str(src), str(dst))
        return True, ""
    except PermissionError:
        pass   # 权限问题，尝试 chmod 后重试
    except OSError as e:
        # 文件名过长、非法字符等——chmod 也救不了，直接报错
        return False, f"shutil: {e}"
    except Exception as e:
        return False, f"shutil: {e}"

    # 方法二：先 chmod 放开读权限，再 shutil.copy2
    try:
        os.chmod(str(src), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        shutil.copy2(str(src), str(dst))
        return True, ""
    except Exception:
        pass

    # 方法三：调用系统 /bin/cp -f
    try:
        r = subprocess.run(
            ["/bin/cp", "-f", str(src), str(dst)],
            capture_output=True, timeout=120,
        )
        if r.returncode == 0:
            return True, ""
        stderr = r.stderr.decode(errors="replace").strip()
        return False, f"cp: {stderr}"
    except subprocess.TimeoutExpired:
        return False, "cp 超时（120 秒）"
    except Exception as e:
        return False, f"cp: {e}"


def main() -> int:
    if not SRC.is_dir():
        sys.exit(f"[中止] 源盘未挂载：{SRC}")
    if not DST.is_dir():
        sys.exit(f"[中止] 目标盘未挂载：{DST}")

    UTILS_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = UTILS_DIR / f"backup_{ts}.log"
    err_path = UTILS_DIR / f"errors_{ts}.log"

    n_copied  = 0
    n_skipped = 0
    n_errors  = 0

    with open(log_path, "w", encoding="utf-8") as log_f, \
         open(err_path, "w", encoding="utf-8") as err_f:

        def log(msg: str):
            print(msg, flush=True)
            log_f.write(msg + "\n")
            log_f.flush()

        def log_err(path: Path, reason: str):
            nonlocal n_errors
            n_errors += 1
            line = f"{path} | {reason}"
            err_f.write(line + "\n")
            err_f.flush()
            log_f.write(f"[错误] {line}\n")
            log_f.flush()
            print(f"  [错误] {line}", file=sys.stderr, flush=True)

        log("=" * 60)
        log(f"备份开始：{datetime.now()}")
        log(f"源盘    ：{SRC}")
        log(f"目标    ：{DST}")
        log(f"排除目录：{sorted(EXCLUDED_TOPLEVEL)}")
        log("=" * 60)

        def on_walk_error(exc: OSError):
            """os.walk 遍历目录失败时的回调"""
            log_err(Path(str(exc.filename) if exc.filename else "?"), str(exc))

        for dirpath_str, dirnames, filenames in os.walk(
            str(SRC), topdown=True, onerror=on_walk_error
        ):
            dirpath = Path(dirpath_str)

            try:
                rel = dirpath.relative_to(SRC)
            except ValueError:
                continue

            parts = rel.parts

            # 如果当前目录在排除列表里，跳过整棵子树
            if parts and parts[0] in EXCLUDED_TOPLEVEL:
                dirnames.clear()
                continue

            # 在根目录时，剔除不需要递归的顶层目录 + 所有点开头的目录
            if not parts:
                dirnames[:] = [
                    d for d in dirnames
                    if d not in EXCLUDED_TOPLEVEL and not is_dot_path(d)
                ]
            else:
                # 非根目录：只跳过点开头的子目录
                dirnames[:] = [d for d in dirnames if not is_dot_path(d)]

            # 确保目标目录存在
            dst_dir = DST / rel
            try:
                dst_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                log_err(dst_dir, f"创建目录失败：{e}")
                dirnames.clear()   # 目录建不了，跳过整棵子树
                continue

            # 逐文件拷贝
            for fname in filenames:
                # 跳过点开头的文件（._xxxx、.DS_Store 等）
                if is_dot_path(fname):
                    continue

                src_file = dirpath / fname
                dst_file = dst_dir / fname

                # 断点续传：目标已存在且大小一致则跳过
                try:
                    src_size = src_file.stat().st_size
                    if dst_file.exists() and dst_file.stat().st_size == src_size:
                        n_skipped += 1
                        continue
                except Exception:
                    pass   # stat 失败，继续尝试拷贝

                ok, reason = copy_file_robust(src_file, dst_file)
                if ok:
                    n_copied += 1
                    if n_copied % 1000 == 0:
                        log(f"  ... 已复制 {n_copied:,} 个文件，错误 {n_errors:,} 个")
                else:
                    log_err(src_file, reason)

        # 汇总
        log("=" * 60)
        log(f"备份完成：{datetime.now()}")
        log(f"已复制  ：{n_copied:,} 个文件")
        log(f"已跳过  ：{n_skipped:,} 个（断点续传）")
        log(f"错误数  ：{n_errors:,} 个")
        log(f"完整日志：{log_path}")
        log(f"错误日志：{err_path}")

    return 0 if n_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
