---
name: cluster-connect
description: Use when connecting to remote HPC clusters over SSH, submitting or monitoring Slurm/PBS jobs, transferring files to or from a cluster, or diagnosing connection failures across jumpbox or VPN topologies
---

# cluster-connect · SSH HPC Cluster Bridge

## Overview

固化「本地 -> (VPN) -> 跳板 -> 集群」的 SSH 连接 + 作业管理 + 文件传输。首次 `setup.py` 交互式生成 profile + ssh config，之后 `cluster.py` 子命令通过 alias 直达。支持 Slurm + PBS/Torque。所有远程文件操作沙箱在 `paths.root_dir` 内。stdlib only，零第三方依赖，可独立成 repo 开源。

**Core principle:** setup once (profile + ssh config + key), then alias-driven; sandbox every remote path to root_dir.

## When to Use

- 连接 / 诊断 HPC 集群 SSH 链路（直连、跳板、VPN+多跳）
- 提交、查询、取消、看日志 Slurm / PBS 作业
- 与集群互传文件（rsync，沙箱校验）
- 首次配置新集群连接 / 公钥部署

## When NOT to Use

- 本地计算（无 SSH）
- Web / TUI / 桌面集群管理（用 catgo 等重形态工具）
- LSF / SGE 集群（不支持，仅 Slurm + PBS/Torque）

## First-Time Setup

`<skill-dir>` = 本 skill 的安装目录（项目内 `.claude/skills/cluster-connect/`；全局安装时 `~/.claude/skills/cluster-connect/`）。脚本 stdlib-only，任意 Python 3.7+ 即可：

```powershell
# 若集群要密码且尚未部署密钥（首次必走）
python <skill-dir>/scripts/setup.py --deploy-key
# 完整配置：拓扑 -> ssh config -> 调度器探测 -> profile
python <skill-dir>/scripts/setup.py --profile default
```

`setup.py` 会：
1. 逐跳询问拓扑，幂等写 `~/.ssh/config`（含 `ProxyJump` + `ControlMaster auto` + `ControlPersist 12h`，带 `# BEGIN/END cluster-connect:<name>` 标记，块外内容不动，写前 `.bak` 备份）
2. 测连通（`ssh -O check` 零网络探测 + `ssh <alias> true`）；要密码则提示先 `--deploy-key`
3. 自动探测调度器（`sinfo` / `qstat`）
4. 询问 `root_dir`（**必须是你集群上已有的目录**，cluster-connect 不创建它；不存在则 doctor FAIL 并提示你检查路径）
5. 写 `~/.cluster-skill/profile.json`（确认环节，对齐 catgo「config 不可猜」）

`--deploy-key` 流程：`ssh-copy-id` 部署公钥 -> 验证免密 -> 密码仅存内存，不落盘（profile/config/日志均不含）。多跳拓扑对每跳部署；已免密则跳过。Windows 无 `ssh-copy-id` 时打印手动 `authorized_keys` 步骤。

## Daily Commands

```powershell
$s = "<skill-dir>/scripts/cluster.py"
python $s doctor                         # 诊断全链路
python $s submit job.sh --nodes 2 --gpus 4 --time 24:00:00 --yes
python $s status                         # 我的作业
python $s status --all                   # 全部队列
python $s status <jobid>                 # 单作业详情
python $s delete <jobid>
python $s push ./run1/ run1/             # 上传（沙箱校验 remote 路径）
python $s pull run1/OUTCAR ./out/        # 下载
python $s log <jobid> -f                 # tail -f 日志
python $s exec "sinfo -s"                # 任意命令（escape-trap 守卫）
```

全局 `--profile <name>` 选 profile（默认 `default`）。

## 多集群

每个集群一个 profile（`~/.cluster-skill/<name>.json`），`--profile <name>` 切换：

```powershell
& $py $s profiles                       # 列出所有已配集群
& $py $s --profile 167 doctor           # 诊断 167
& $py $s --profile 167 status           # 查 167 作业
```

新集群用 `setup.py --profile <name>` 配置。AI 会话里你提"167"/"169"，我用对应 profile。

## Doctor 诊断决策树

连不上时按序排查（`doctor` 自动跑前几步并报告断点）：

1. 本地 `ssh` 在 PATH？
2. ControlMaster socket 活？-> `ssh -O check <alias>`（零网络探测）
3. `ssh <alias> true` 通？不通继续
4. VPN up？-> Tailscale / WireGuard 是否连接（`tailscale status`）
5. 跳板 reachable？-> 跳板机开机？内网网线在？
6. 末跳 reachable？-> 集群登录节点在线？
7. key 认证？-> 要密码 = 未部署密钥 -> 跑 `setup.py --deploy-key`
8. 调度器命令？-> `sinfo` / `qstat` 存在？
9. root_dir 存在且可写？-> 不存在则提示你检查路径（cluster-connect 不创建根目录）

## 作业状态语义（别误判）

| Slurm | PBS | 含义 |
|---|---|---|
| RUNNING (R) | R | 运行中 |
| COMPLETING (CG) | E | **正退出，非失败** |
| PENDING (PD) | Q | 排队（也可能是 hold） |
| COMPLETED (CD) | C | 已完成 |
| FAILED (F) | F | 失败 |
| TIMEOUT (TO) | - | 超时被 kill |
| CANCELLED (CA) | W | 取消 |

**陷阱**：`COMPLETING`/`CG` 不是崩溃（还在收尾）；PBS `E` 是 Exiting 不是 Error；PBS `C` 是 Completed 不是 Cancelled。完整映射见 `references/schedulers.md`。

## 作业 watch（AI 会话内）

`status --watch` 在终端阻塞轮询（30s）。**AI 会话内禁止用 `--watch`**（会卡住会话），改用 Monitor 工具后台轮询 `cluster.py status`：每 60s 一次，TTL 跟随作业时限，SSH 不可用时指数退避（30s -> 10min），作业进入 COMPLETED/FAILED/CANCELLED 即停。

## Sandbox（root_dir 隔离）

所有远程文件操作（`push`/`pull`/`submit`/`log`）的目标路径 resolve 后必须落在 `paths.root_dir` 内，否则 `cluster.py` 拒绝执行（防 `..` 逃逸、绝对路径越界）。`exec` 默认 `cd root_dir` 再执行。删 root_dir 子目录是合法操作；但 `rm -rf /`、`rm -rf ~`、`rm -rf $HOME`、`mkfs`、fork bomb、SSH escape 序列（`~.` 等）、不配对单引号均被 `is_dangerous` 守卫拦截。这是应用层软约束（用户直接 `ssh` 仍可绕过），但对 AI 会话内操作是有效护栏。

## Red Flags

- **不得**在集群写 `root_dir` 外文件（呼应 cluster_readonly 红线）
- **不得**绕过 sandbox 直接 `ssh <alias> "rm ..."` 操作 `root_dir` 外路径
- **不得**在 AI 会话内用 `status --watch`（用 Monitor 工具）
- **不得**硬编码密码（密钥优先；`--deploy-key` 部署后免密）
- **不得**把真实 IP / 用户 / 路径写进开源 example（用占位符）
- 提交作业前先 local 校验输入文件（呼应 input_build）

## Common Mistakes

- root_dir 必须是集群上**已存在**的目录 -> cluster-connect 不创建它；doctor FAIL 时检查路径是否正确
- 跳板关机 / Tailscale 断 -> `doctor` 报「第 1 跳不可达：VPN 未连接」
- `submit` 的 `script` 路径必须相对 `root_dir` 或在 `root_dir` 下绝对路径
- Windows 无 rsync 时 `push`/`pull` 自动降级 `scp`：push 会先 `ssh mkdir -p` 远程目标目录（scp 不自动建目录），pull 会自动创建本地目标父目录；降级路径同样先展示命令再确认
- PBS GPU 语法站点相关（`-l gpus=N` vs `select=...:gpus=N`）-> `setup.py` 探测时记录，`submit` 按记录渲染
- `exec` 的命令里 `$VAR` 会被远程 shell 展开（远程语义）；若含不配对单引号会被守卫拦截

## Reference Files

| 需要 | 读 |
|---|---|
| Slurm / PBS 命令映射、状态码、资源声明 | `references/schedulers.md` |
| profile 字段模板 | `profiles/profile.template.json` |
| 拓扑示例（直连 / 跳板 / VPN+多跳） | `profiles/examples/` |
| 纯函数库（命令构造 / 状态翻译 / 沙箱校验） | `scripts/cluster_lib.py` |

## Dependencies

`ssh`（必需，系统）；`rsync`（可选——Windows 无 rsync 时 `push`/`pull` 自动降级 `scp`，`scp` 由 Windows OpenSSH 客户端自带）；Python 3.7+（stdlib only，无第三方包）。`--deploy-key` 在 Linux/macOS 用 `sshpass`（可选，未装则回退交互），Windows 打印手动 `authorized_keys` 步骤。

## Acknowledgements

设计借鉴（思路，非代码）：[badseal/ssh-skill](https://github.com/badseal/ssh-skill)（ssh config 注释元数据、ControlMaster 长连接）、[Charlie-Z-work/slurm-mcp-server](https://github.com/Charlie-Z-work/slurm-mcp-server)（`ssh -O check` 探测、日志 fallback 链、资源检查、escape-trap 守卫）、[The66user/dft-autopilot-mcp](https://github.com/The66user/dft-autopilot-mcp)（plan-before-compute、SSH 委派系统 client）、[Hello-QM/catgo-LRG](https://github.com/Hello-QM/catgo-LRG)（config 不可猜确认清单、root_dir 沙箱）。
