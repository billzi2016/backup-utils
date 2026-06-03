# backup-utils

exFAT → APFS 鲁棒备份工具。专为 macOS 上从 exFAT 外盘向 APFS 外盘迁移大量文件设计，遇到错误不中断，全程记录日志。

## 功能

- **三级容错拷贝**：`shutil.copy2` → `chmod` 后重试 → 系统 `/bin/cp -f`，全部失败才记录错误
- **断点续传**：目标文件已存在且大小一致则静默跳过，中途中断重跑不从头来
- **终端进度条**：原地刷新（同 tqdm 风格），只有错误才插入新行打印完整路径
- **排除规则外置**：编辑 `exclude.txt` 即可，无需动代码
- **日志分离**：完整日志和错误日志分别写入 `logs/` 目录，按时间戳命名
- **自动忽略点文件**：`.DS_Store`、`._xxxx` 等 exFAT 元数据文件全部跳过

## 目录结构

```
backup_utils/
├── backup.py       # 主脚本
├── exclude.txt     # 排除目录列表
├── run_backup.sh   # 一键启动脚本
├── README.md
└── logs/           # 运行时自动创建
    ├── backup_YYYY-MM-DD_HH-MM-SS.log   # 完整日志
    └── errors_YYYY-MM-DD_HH-MM-SS.log   # 仅错误
```

## 使用方法

```bash
bash /Volumes/LuZhang/backup_utils/run_backup.sh
```

或直接：

```bash
python3 /Volumes/LuZhang/backup_utils/backup.py
```

## 配置排除列表

编辑 `exclude.txt`，每行写一个**源盘根目录下**的目录名，`#` 开头为注释：

```
# 不需要备份的目录
[只读]原始备份
generated_mri
IU_Datasets
```

## 进度条说明

```
 [████████░░░░░░░░░░░░░░░░░░░░░░░░] 25.0%  250/1000  错误:2  ETA:  90s  /Volumes/LuZhang/vis/brain.nii
```

- 进度条分母 = 扫描时排除已完成文件后的**真实待复制数量**
- 正常拷贝：原地覆写进度条，不产生新行
- 发生错误：在进度条上方插入一行错误信息（含完整路径），进度条继续

## 环境要求

- macOS + Python 3.9+
- 无第三方依赖
