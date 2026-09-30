# cluster-connect

**SSH HPC 集群桥接 skill for Claude Code。** 固化「本地 → (VPN) → 跳板 → 集群」的 SSH 连接 + Slurm/PBS 作业管理 + 文件传输，所有远程文件操作沙箱在 `root_dir` 内。**stdlib only，零第三方依赖**，Python 3.7+ 即可运行。

## 功能矩阵

| 子命令 | 功能 | 关键细节 |
|---|---|---|
| `setup.py --profile <name>` | 首次配置向导 | 逐跳询问拓扑 → 幂等写 `~/.ssh/config`（`# BEGIN/END` 标记、写前 `.bak` 备份、块外不动）→ 自动探测调度器（`sinfo`/`qstat`）→ 询问 `root_dir`（**必须已存在**，不创建）→ 生成 `~/.cluster-skill/<name>.json` |
| `setup.py --deploy-key` | 公钥部署 | ssh-copy-id 逐跳部署 + 验证免密；密码仅存内存不落盘；Windows 无 ssh-copy-id 时打印手动步骤 |
| `submit job.sh` | 提交作业 | `--nodes/--gpus/--time`，`--yes` 跳过确认 |
| `status` / `status <id>` | 查作业 | `--all` 全队列 / 单作业详情；**`--watch` 勿在 AI 会话内使用**（会卡住会话） |
| `delete <id>` | 取消作业 | |
| `log <id> -f` | 日志 | `tail -f` |
| `push` / `pull` | 文件传输 | rsync（Windows 无 rsync 自动降级 scp）；push 先 `ssh mkdir -p`，pull 自动建本地父目录 |
| `exec "<cmd>"` | 任意远程命令 | escape-trap 守卫：拦 `rm -rf /`、`mkfs`、fork bomb、SSH escape 序列、不配对单引号 |
| `doctor` | 9 步诊断 | ssh 在 PATH → ControlMaster socket → 末跳连通 → VPN（Tailscale/WireGuard）→ 跳板 → key 认证 → 调度器命令 → root_dir 存在可写 |
| `profiles` / `--profile <name>` | 多集群 | 列出全部 / 切换目标集群 |

**双调度器**：Slurm + PBS/Torque，内置命令映射与状态翻译（如 Slurm `CG` = PBS `E` = 正在退出非失败；PBS `C` = Completed 不是 Cancelled）。命令映射表见 `references/schedulers.md`。**不支持 LSF/SGE**。

## 为什么做这个

现有方案各有缺口：

| 项目 | HPC 作业 | 跳板/VPN | 形态 |
|---|---|---|---|
| badseal/ssh-skill | ❌ | ✅ | skill，依赖 paramiko |
| slurm-mcp-server | ✅ Slurm | 隐含 | MCP 常驻 |
| catgo-LRG | ✅ Slurm | ❌ | 桌面工作台 |
| ssh-vps-skill | ❌ | ❌ | skill，偏 VPS |

**没有一个同时覆盖「跳板/VPN 多跳 + Slurm/PBS + 轻量 skill + 零依赖」。** cluster-connect 填这个空白。

## 安装

```powershell
# 用户级（所有项目可用）
Copy-Item -Recurse cluster-connect $HOME/.claude/skills/
# 或项目级
Copy-Item -Recurse cluster-connect <your-project>/.claude/skills/
```

**要求**：Python 3.7+，`ssh`（必需，系统自带）；`rsync`（可选，降级 scp）；`sshpass`（可选，Linux/macOS 的 `--deploy-key`）。Linux / macOS / Windows（Git Bash、PowerShell）通用。

## 快速开始

```powershell
# 1. 首次配置（若集群要密码且未部署密钥，先 --deploy-key）
python scripts/setup.py --deploy-key
python scripts/setup.py --profile default

# 2. 日常使用
python scripts/cluster.py doctor                              # 诊断全链路
python scripts/cluster.py submit job.sh --nodes 2 --gpus 4 --time 24:00:00 --yes
python scripts/cluster.py status                              # 查作业
python scripts/cluster.py pull run1/OUTCAR ./out/             # 下载结果
python scripts/cluster.py --profile 167 status                # 切换集群
```

AI 会话里直接说意图（"提交 job.sh"、"看 12345 日志"、"167 上查作业"），Claude 自动调对应子命令 + profile。

## 工作原理

- **profile**（`~/.cluster-skill/<name>.json`）记录 `ssh_alias` + 调度器 + `root_dir` + 硬件；模板见 `profiles/profile.template.json`，`profiles/examples/` 有 direct-slurm / jumpbox-pbs / vpn-multihop-slurm 三个示例
- **SSH 拓扑**交给 `~/.ssh/config`（ProxyJump 多跳 + ControlMaster 长连接复用），`cluster.py` 只用 alias 连，复用 ssh 原生能力，`ssh -O check` 零网络探测
- **沙箱**：`push`/`pull`/`submit`/`log` 的远程路径 resolve 后必须落在 `root_dir` 内，`..` 逃逸与绝对路径越界一律拒绝
- **环境纪律**：作业脚本自己 `module load`，`cluster.py` 不注入环境、不部署 init.sh、不创建 root_dir

## 测试

```powershell
python -m unittest discover -s tests -v
```

55 个单元测试，覆盖调度器命令构造、状态翻译、沙箱路径校验、escape-trap 守卫、profile schema、setup 幂等、公钥部署密码不落盘。

## 生态位

| 环节 | 工具 |
|---|---|
| 建模（slab / 异质结 / 电解质盒） | [emcbuild](https://github.com/feihanyu953-web/emcbuild) |
| 输入文件生成与校验 | [input_build](https://github.com/feihanyu953-web/input_build) |
| **集群提交 / 传输 / 诊断** | **本项目** |
| 集群侧 VASP 后处理 | [vaspkit-remote](https://github.com/feihanyu953-web/vaspkit-remote)（依赖本项目的 exec/pull） |
| MD 轨迹本地分析 | [lammps-analyze](https://github.com/feihanyu953-web/lammps-analyze)（用本项目 pull 轨迹） |

## 致谢

设计借鉴（思路，非代码）：[badseal/ssh-skill](https://github.com/badseal/ssh-skill)、[Charlie-Z-work/slurm-mcp-server](https://github.com/Charlie-Z-work/slurm-mcp-server)、[The66user/dft-autopilot-mcp](https://github.com/The66user/dft-autopilot-mcp)、[Hello-QM/catgo-LRG](https://github.com/Hello-QM/catgo-LRG)。

## License

MIT
