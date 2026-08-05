# cluster-connect

**SSH HPC 集群桥接 skill for Claude Code。** 固化「本地 → (VPN) → 跳板 → 集群」的 SSH 连接 + Slurm/PBS 作业管理 + 文件传输，所有远程操作沙箱在 `root_dir` 内。stdlib only，零第三方依赖。

## 为什么做这个

现有方案各有缺口：

| 项目 | HPC 作业 | 跳板/VPN | 形态 |
|---|---|---|---|
| badseal/ssh-skill | ❌ | ✅ | skill，依赖 paramiko |
| slurm-mcp-server | ✅ Slurm | 隐含 | MCP 常驻 |
| catgo-LRG | ✅ Slurm | ❌ | 桌面工作台 |
| ssh-vps-skill | ❌ | ❌ | skill，偏 VPS |

**没有一个覆盖「跳板/VPN 多跳 + Slurm/PBS + 轻量 skill + 零依赖」。** cluster-connect 填这个空白。

## 特性

- **多跳拓扑**：ProxyJump 链 + VPN 在线检测（Tailscale/WireGuard）
- **双调度器**：Slurm + PBS/Torque（命令映射 + 状态翻译）
- **root_dir 沙箱**：所有远程文件操作 resolve 后必须落在根目录内，`..` 逃逸/绝对路径越界拒绝
- **首次公钥部署**：`--deploy-key`（ssh-copy-id，密码仅存内存不落盘）
- **多集群**：`--profile <name>` 切换，`profiles` 子命令列出全部
- **escape-trap 守卫**：拦截 `rm -rf /`、`mkfs`、fork bomb、SSH escape 序列、不配对单引号
- **ControlMaster 长连接**：`ssh -O check` 零网络探测
- **不创建根目录、不部署 init.sh**：用户告知 root_dir，AI 直接连；不存在则报错提示检查路径。作业脚本自己 `module load`。

## 安装

把 `cluster-connect/` 放到 Claude Code skills 目录：

```powershell
# 用户级（所有项目可用）
Copy-Item -Recurse cluster-connect $HOME/.claude/skills/

# 或项目级
Copy-Item -Recurse cluster-connect <your-project>/.claude/skills/
```

**要求**：Python 3.7+，`ssh`（必需，系统）；`rsync`（可选——Windows 无 rsync 时 `push`/`pull` 自动降级 `scp`，`scp` 由 Windows OpenSSH 客户端自带）。`--deploy-key` 在 Linux/macOS 可选 `sshpass`，Windows 打印手动步骤。

## 首次配置

```powershell
python scripts/setup.py --deploy-key        # 若集群要密码且未部署密钥
python scripts/setup.py --profile default   # 完整配置（拓扑 -> ssh config -> 调度器探测 -> profile）
```

`setup.py` 会逐跳询问拓扑，幂等写 `~/.ssh/config`（含 `ProxyJump` + `ControlMaster`，带 `# BEGIN/END` 标记，块外不动，写前 `.bak` 备份），自动探测 Slurm/PBS，询问 `root_dir`（**必须已存在**，不创建），生成 `~/.cluster-skill/<name>.json`。

## 用法

```powershell
python scripts/cluster.py doctor                              # 诊断全链路
python scripts/cluster.py submit job.sh --nodes 2 --gpus 4 --time 24:00:00
python scripts/cluster.py status                              # 查作业
python scripts/cluster.py status <jobid>                      # 单作业详情
python scripts/cluster.py delete <jobid>
python scripts/cluster.py push ./run1/ run1/                  # 上传（沙箱校验）
python scripts/cluster.py pull run1/OUTCAR ./out/             # 下载
python scripts/cluster.py log <jobid> -f                      # tail -f 日志
python scripts/cluster.py exec "sinfo -s"                     # 任意命令（守卫）
python scripts/cluster.py profiles                            # 列所有集群
python scripts/cluster.py --profile 167 status                # 切集群
```

AI 会话里直接说意图（"提交 job.sh"、"看 12345 日志"、"167 上查作业"），Claude 自动调对应子命令 + profile。

## 工作原理

- **profile**（`~/.cluster-skill/<name>.json`）记录 `ssh_alias` + 调度器 + `root_dir` + 硬件
- **SSH 拓扑**交给 `~/.ssh/config`（ProxyJump 多跳），`cluster.py` 只用 alias 连，复用 ssh 原生能力
- **沙箱**：`push`/`pull`/`submit`/`log` 的远程路径 resolve 后必须落在 `root_dir` 内
- **环境**：作业脚本自己 `module load`，`cluster.py` 不注入环境（无 init.sh）

## 测试

```powershell
python -m unittest discover -s tests -v
```

55 个单元测试，覆盖调度器命令构造、状态翻译、沙箱路径校验、escape-trap 守卫、profile schema、setup 幂等、公钥部署密码不落盘。

## 致谢

设计借鉴（思路，非代码）：[badseal/ssh-skill](https://github.com/badseal/ssh-skill)、[Charlie-Z-work/slurm-mcp-server](https://github.com/Charlie-Z-work/slurm-mcp-server)、[The66user/dft-autopilot-mcp](https://github.com/The66user/dft-autopilot-mcp)、[Hello-QM/catgo-LRG](https://github.com/Hello-QM/catgo-LRG)。

## License

MIT
