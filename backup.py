#!/usr/bin/env python3
"""
exFAT -> APFS 鲁棒备份脚本
  源盘：/Volumes/LuZhang
  目标：/Volumes/LuZhang16T

特性：
  - 排除规则从 exclude.txt 读取
  - 日志写入 logs/ 子目录
  - 终端实时进度条 + 错误即时打印
  - 三级容错拷贝（shutil → chmod+shutil → /bin/cp）
  - 断点续传（目标已存在且大小一致则跳过）
  - 所有点开头文件/目录自动忽略（exFAT 元数据）
"""

import os
import sys
import shutil
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path

# ── 路径配置 ───────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
SRC          = Path("/Volumes/LuZhang")
DST          = Path("/Volumes/LuZhang16T")
EXCLUDE_FILE = SCRIPT_DIR / "exclude.txt"
LOG_DIR      = SCRIPT_DIR / "logs"
# ──────────────────────────────────────────────────────────────────────────


# ── 配置加载 ───────────────────────────────────────────────────────────────

def load_excludes(path: Path) -> frozenset:
    """从文件读取顶层排除目录名，忽略注释行和空行"""
    if not path.exists():
        print(f"[警告] 找不到排除配置：{path}，使用空列表")
        return frozenset()
    items = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                items.add(s)
    return frozenset(items)


# ── 进度条 ─────────────────────────────────────────────────────────────────

class ProgressBar:
    """无第三方依赖的终端进度条（只显示待复制文件，不计跳过）"""
    BAR_WIDTH = 32

    def __init__(self, total: int):
        self.total   = max(total, 1)
        self.current = 0
        self.errors  = 0
        self._start  = time.monotonic()

    def advance(self, filename: str, *, error: bool = False):
        self.current += 1
        if error:
            self.errors += 1
        self._draw(filename)

    def _draw(self, filename: str):
        pct     = self.current / self.total
        filled  = int(self.BAR_WIDTH * pct)
        bar     = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        elapsed = time.monotonic() - self._start
        eta     = (elapsed / pct - elapsed) if pct > 0.001 else 0
        name    = filename[-40:] if len(filename) > 40 else filename
        sys.stdout.write(
            f"\r [{bar}] {pct*100:5.1f}%"
            f"  {self.current:,}/{self.total:,}"
            f"  错误:{self.errors:,}"
            f"  ETA:{eta:4.0f}s"
            f"  {name:<40}"
        )
        sys.stdout.flush()

    def interrupt_print(self, msg: str):
        """在进度条上方插入错误行（不破坏进度条）"""
        sys.stdout.write(f"\r{' ' * 130}\r")
        print(msg, flush=True)

    def finish(self):
        elapsed = time.monotonic() - self._start
        bar = "█" * self.BAR_WIDTH
        sys.stdout.write(
            f"\r [{bar}] 100.0%"
            f"  {self.current:,}/{self.total:,}"
            f"  错误:{self.errors:,}"
            f"  用时:{elapsed:.1f}s"
            f"{'':40}\n"
        )
        sys.stdout.flush()


# ── 文件拷贝 ───────────────────────────────────────────────────────────────

def copy_file_robust(src: Path, dst: Path) -> tuple[bool, str]:
    """
    三级容错拷贝，返回 (成功, 错误信息)。
    exFAT 常见问题：权限位缺失、文件名含特殊字符等。
    """
    # 第一级：shutil.copy2（保留时间戳）
    try:
        shutil.copy2(str(src), str(dst))
        return True, ""
    except PermissionError:
        pass   # 权限问题，尝试 chmod 后重试
    except OSError as e:
        return False, f"shutil: {e}"
    except Exception as e:
        return False, f"shutil: {e}"

    # 第二级：先 chmod 放开读权限，再 copy2
    try:
        os.chmod(str(src), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        shutil.copy2(str(src), str(dst))
        return True, ""
    except Exception:
        pass

    # 第三级：调用系统 /bin/cp -f
    try:
        r = subprocess.run(
            ["/bin/cp", "-f", str(src), str(dst)],
            capture_output=True, timeout=120,
        )
        if r.returncode == 0:
            return True, ""
        return False, "cp: " + r.stderr.decode(errors="replace").strip()
    except subprocess.TimeoutExpired:
        return False, "cp 超时（120s）"
    except Exception as e:
        return False, f"cp: {e}"


# ── 目录遍历 ───────────────────────────────────────────────────────────────

def walk_source(src: Path, excluded: frozenset, on_error=None):
    """
    遍历源盘，yield (dirpath, rel, filenames)。
    自动剪枝排除目录和所有点开头目录。
    """
    for dirpath_str, dirnames, filenames in os.walk(
        str(src), topdown=True, onerror=on_error
    ):
        dirpath = Path(dirpath_str)
        try:
            rel = dirpath.relative_to(src)
        except ValueError:
            continue

        parts = rel.parts

        # 当前目录属于排除的顶层目录，跳过整棵子树
        if parts and parts[0] in excluded:
            dirnames.clear()
            continue

        # 过滤子目录：顶层用排除列表，所有层跳过点开头目录
        if not parts:
            dirnames[:] = [
                d for d in dirnames
                if d not in excluded and not d.startswith(".")
            ]
        else:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        yield dirpath, rel, filenames


def scan_total(src: Path, dst: Path, excluded: frozenset) -> int:
    """预扫描统计真正需要复制的文件数（目标已存在且大小一致的不计入）"""
    count = 0
    for dirpath, rel, filenames in walk_source(src, excluded):
        dst_dir = dst / rel
        for f in filenames:
            if f.startswith("."):
                continue
            # 目标已存在且大小一致 → 断点续传会跳过，不计入进度总数
            try:
                if (dst_dir / f).stat().st_size == (dirpath / f).stat().st_size:
                    continue
            except Exception:
                pass
            count += 1
    return count


# ── 主流程 ─────────────────────────────────────────────────────────────────

def run_backup() -> int:
    # 环境检查
    if not SRC.is_dir():
        sys.exit(f"[中止] 源盘未挂载：{SRC}")
    if not DST.is_dir():
        sys.exit(f"[中止] 目标盘未挂载：{DST}")

    excluded = load_excludes(EXCLUDE_FILE)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = LOG_DIR / f"backup_{ts}.log"
    err_path = LOG_DIR / f"errors_{ts}.log"

    # 预扫描（排除已完成文件，只统计真正需要复制的数量）
    print(f"\n[扫描] 正在统计待复制文件数量……")
    total = scan_total(SRC, DST, excluded)
    print(f"[扫描] 待复制 {total:,} 个文件\n")

    n_copied  = 0
    n_skipped = 0
    n_errors  = 0
    bar       = ProgressBar(total)

    with open(log_path, "w", encoding="utf-8") as log_f, \
         open(err_path, "w", encoding="utf-8") as err_f:

        def log(msg: str):
            log_f.write(msg + "\n")
            log_f.flush()

        def log_err(path: Path, reason: str):
            nonlocal n_errors
            n_errors += 1
            line = f"{path} | {reason}"
            err_f.write(line + "\n")
            err_f.flush()
            log(f"[错误] {line}")
            bar.interrupt_print(f"  [错误] {line}")

        log("=" * 60)
        log(f"备份开始：{datetime.now()}")
        log(f"源盘    ：{SRC}")
        log(f"目标    ：{DST}")
        log(f"排除    ：{sorted(excluded)}")
        log(f"总文件数：{total:,}")
        log("=" * 60)

        def on_walk_error(exc: OSError):
            log_err(Path(str(exc.filename) if exc.filename else "?"), str(exc))

        for dirpath, rel, filenames in walk_source(SRC, excluded, on_error=on_walk_error):
            dst_dir = DST / rel
            try:
                dst_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                log_err(dst_dir, f"创建目录失败：{e}")
                continue

            for fname in filenames:
                # 跳过点开头文件（._xxxx、.DS_Store 等）
                if fname.startswith("."):
                    continue

                src_file = dirpath / fname
                dst_file = dst_dir / fname

                # 断点续传：目标已存在且大小一致则静默跳过（不计入进度条）
                try:
                    if dst_file.exists() and dst_file.stat().st_size == src_file.stat().st_size:
                        n_skipped += 1
                        log(f"[跳过] {src_file}")
                        continue
                except Exception:
                    pass

                ok, reason = copy_file_robust(src_file, dst_file)
                if ok:
                    n_copied += 1
                    log(f"[OK] {src_file}")
                    bar.advance(str(src_file))
                else:
                    bar.advance(str(src_file), error=True)
                    log_err(src_file, reason)

        bar.finish()

        summary = (
            f"\n{'='*60}\n"
            f"备份完成：{datetime.now()}\n"
            f"已复制  ：{n_copied:,} 个\n"
            f"已跳过  ：{n_skipped:,} 个（断点续传）\n"
            f"错误    ：{n_errors:,} 个\n"
            f"完整日志：{log_path}\n"
            f"错误日志：{err_path}\n"
            f"{'='*60}"
        )
        print(summary)
        log(summary)

    return 0 if n_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(run_backup())
