# Schedulers Reference: Slurm ↔ PBS/Torque

`cluster.py` 内置同源映射表；本文件是人工查阅版，调度器命令构造与状态翻译的 ground truth。

## 命令映射

| 操作 | Slurm | PBS/Torque |
|---|---|---|
| 提交作业 | `sbatch <script>` | `qsub <script>` |
| 查看作业 | `squeue [-u <user>]` | `qstat [-u <user>]` |
| 作业详情 | `scontrol show job <id>` | `qstat -f <id>` |
| 取消作业 | `scancel <id>` | `qdel <id>` |
| 队列/分区 | `sinfo` | `qstat -q` / `pbsnodes -a` |
| 历史作业 | `sacct -j <id>` | `qstat -x <id>` (Torque) / `tracejob <id>` |
| 节点信息 | `scontrol show node <name>` | `pbsnodes <name>` |

## 状态码翻译

| 含义 | Slurm | PBS/Torque |
|---|---|---|
| 排队 | PENDING (PD) | Q (Queued) |
| 运行中 | RUNNING (R) | R (Running) |
| 正退出（非失败） | COMPLETING (CG) | E (Exiting) |
| 已完成 | COMPLETED | C (Completed) |
| 失败 | FAILED | F (Failed) |
| 超时 | TIMEOUT | — (walltime 后被 kill，状态随站点) |
| 取消 | CANCELLED | W (Waiting/deleted) |
| 挂起 | HELD (scontrol hold) | H (Held) |

## 陷阱

- **Slurm `COMPLETING`/`CG` 不是失败**：作业正在收尾（写输出、清理），仍在 RUNNING 范畴，别误判为崩溃。
- **PBS `E` (Exiting) 不是 Error**：正退出，对应 Slurm COMPLETING。
- **PBS `C` 是 Completed 不是 Cancelled**：Cancelled 在 PBS 是 `W` 或站点自定义。
- **GPU 资源无统一 PBS 语法**：Slurm 显式 `--gres=gpu:N`；PBS 用 `-l gpus=N`（Torque 6+）或 `select=...:gpus=N`（站点相关）。`setup.py` 探测时记录站点语法到 profile 注释，cluster.py submit 按记录渲染。
- **作业脚本不改写**：Slurm 用 `#SBATCH` 指令、PBS 用 `#PBS` 指令；`cluster.py submit` 不改写脚本内容，只包装提交命令 + 资源声明。

## 资源声明对照

| 资源 | Slurm | PBS/Torque |
|---|---|---|
| 节点数 | `--nodes=N` (`-N`) | `-l nodes=N` |
| 每节点核数 | `--tasks-per-node=N` / `--cpus-per-task=N` | `-l ppn=N` |
| GPU | `--gres=gpu:G` | `-l gpus=G` (Torque 6+) |
| 时限 | `--time=HH:MM:SS` (`-t`) | `-l walltime=HH:MM:SS` |
| 分区/队列 | `--partition=P` (`-p`) | `-q P` |
| 账户 | `--account=A` (`-A`) | `-A A` (站点相关) |
| 内存 | `--mem=MG` / `--mem-per-cpu=MG` | `-l mem=MG` / `-l pmem=MG` |
