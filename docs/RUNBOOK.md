# RUNBOOK — Claude 账号受限 / 环境疑似污染后的标准处置

| 字段 | 值 |
| --- | --- |
| 编号 | RBC-001 |
| 版本 | 1.1.0（随仓库发布，以 git tag 为准） |
| 适用平台 | macOS（Claude Code / Claude Desktop / Chromium 系浏览器） |
| 最后更新 | 2026-11-11 |
| 涉及工具 | 本仓库 `run.sh`（清理）、Claude-Cage（隔离，可选）、Magpie（轮换，可选） |

**目标**：在账号受限或怀疑本机环境被风控标记后，按可验证、可回滚的步骤清理本机痕迹，并在恢复使用时选择合理的隔离方案。

**本 runbook 不能做的事**：解除封禁、绕过 KYC 或任何身份验证、撤销服务端设备登记、保证账号永不关联。账号状态只能通过官方支持渠道申诉。任何"清理后必然解封"的说法都不成立。

**适用场景**：

- ✅ 账号被封 / 要求 KYC，准备整理本机环境后走官方申诉或换用新账号
- ✅ 转售、移交或回收这台 Mac 前的定向清理
- ✅ 日常隐私整理（只做 P1–P4）
- ❌ 试图规避平台风控去批量注册账号（见 P5 风险声明）

---

## 流程总览

```
P0 判定与止损 → P1 保留备份 → P2 本机清理 → P3 浏览器与扩展
     → P4 验证 → P5 恢复使用（路线 A 隔离 / 路线 B 社区方案）→ P6 记录与复盘
```

每阶段都有"预期结果"；**任何一步的实际结果与预期不符，立即停止**，进入[失败处理](#失败处理索引)。

---

## P0 判定与止损

| # | 操作 | 预期结果 |
| --- | --- | --- |
| 0.1 | 确认受限事实：封禁邮件 / KYC 弹窗 / 登录报错，截图留档 | 拿到明确的现象与时间点 |
| 0.2 | **止损**：停止在该环境上注册或登录任何新 Claude 账号 | 避免新账号在污染环境上被连带标记（社区多篇报告的共识，未经验证） |
| 0.3 | 正常退出 Claude Desktop 与所有 Claude Code 终端（Cmd+Q，不是强杀） | `ps aux | grep -i claude` 无相关进程 |
| 0.4 | 如需申诉，先走官方支持，**不要**在申诉期间反复尝试登录 | — |

> 判定要点：本机清理只解决"环境残留"这一类原因。如果受限与付款方式或账号行为有关，清理本机不会有任何效果。

## P1 保留备份（清你想保的）

清理工具会自动备份它**动过**的东西，但不动的东西需要你自己决定去留。社区实践（见附录 A）是：清环境前先把 **MCP 配置和对话历史**备份出来，之后可恢复。

```sh
BACKUP=~/ClaudeKeep-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP"
# 对话历史、项目索引、MCP/技能/插件配置（工具不会删它们，这里仅为防手滑）
tar czf "$BACKUP/claude-data.tgz" -C ~ \
  .claude/projects .claude/history.jsonl .claude/file-history \
  .claude/skills .claude/plugins .claude/hooks .claude/agents .claude/commands \
  .claude.json 2>/dev/null
ls -lh "$BACKUP"
```

**预期结果**：`claude-data.tgz` 存在且大小非零。该备份可能含令牌与私人对话，只保留在本机，不要上传云端或提交 git。

## P2 本机清理（核心步骤）

```sh
curl -fsSL https://raw.githubusercontent.com/hyeebeen/claude-mac-clean/main/run.sh | bash
```

按场景选择范围（脚本菜单里输入对应编号）：

| 场景 | 选择范围 | 说明 |
| --- | --- | --- |
| 只是想换账号 / 隐私整理 | `cache` + `account` | 不动设备记录 |
| 疑似设备级关联（社区推测 `ant-did` 参与，**未经验证**） | 上述 + `device` | 删的只是本地文件，不撤销服务端登记 |
| 准备移交 / 回收这台机器 | 全部 + 手动检查 P1 未覆盖项 | 本工具不是磁盘擦除工具 |

**操作要点**：

1. 先看只读审计输出的清单，确认每一项都是你预期要清的；
2. 输入 `CONFIRM` 后脚本自动完成：完整备份 → SHA-256 校验 → 移入废纸篓 → 白名单编辑 JSON；
3. macOS 登录令牌在**系统钥匙串**里，工具不碰。要真正退出登录，先在 Claude Code 里执行 `/logout` 再清理。

**预期结果**：报告以 `complete` 结束，屏幕显示备份目录与废纸篓目录路径。**把这两个路径记下来**（P6 要用）。

**失败处理**：任何校验失败脚本都会自行停止并报告 `stopped`，原数据不动；按提示排查磁盘空间/进程占用后可安全重跑。

## P3 浏览器与扩展

浏览器**不在脚本清理范围内**（设计如此，避免误伤其他站点分区），手动执行：

| # | 操作 | 预期结果 |
| --- | --- | --- |
| 3.1 | 完全退出浏览器（Cmd+Q） | — |
| 3.2 | 设置 → 隐私与安全 → 网站设置 → 查看所有网站数据，搜索 `claude`、`anthropic`，**逐站点**删除（claude.ai / claude.com / anthropic.com 及其子域） | 站点条目消失 |
| 3.3 | **不要**按关键词整库删除：Persona、Cloudflare、hCaptcha 等第三方分区同时服务 ChatGPT、Kraken 等无关站点 | 其他站点登录态不受影响 |
| 3.4 | 卸载 Claude 浏览器扩展后重装（社区实践，见附录 A）；若不再需要，直接卸载 | 扩展列表状态符合预期 |
| 3.5 | 注意（若走 P5 路线 B）：浏览器扩展的登录账号必须与 Claude Code 当前账号一致，否则扩展连不上 Claude Code | — |

**预期结果**：工具的浏览器盘点（审计输出末尾）显示 Claude 域名 Cookie 为 0；IndexedDB 无 `claude` 条目。Local/Session Storage、Service Worker、Safari 不在自动盘点范围，如需彻底请逐项人工检查。

## P4 验证

```sh
curl -fsSL https://raw.githubusercontent.com/hyeebeen/claude-mac-clean/main/run.sh | bash -s -- --audit-only
```

| # | 检查项 | 通过标准 |
| --- | --- | --- |
| 4.1 | 三个 scope 的审计计数 | 全部为 0（或仅剩你明确选择保留的项） |
| 4.2 | 浏览器盘点 | Claude 域名 Cookie/IndexedDB 为 0 |
| 4.3 | `ls ~/ClaudeCleanBackups/claude-mac-clean-*/` | 存在 `report.json`（状态 complete）与 `hashes.json` |
| 4.4 | （可选）重启一次 Mac | 干净启动后 Claude 相关目录不自动重建 |

## P5 恢复使用

### 路线 A（推荐）：隔离环境运行

在虚拟机里运行新账号，与本机环境彻底隔离——每个身份一个 VM、独立出口网络、独立指纹（时区/语言）。参考 [Claude-Cage](https://github.com/Lee-xeo/Claude-Cage)（MIT，基于 agent-vm/Lima 的桌面应用）：

- VM 数据存放在宿主 `~/.lima`（可自定义）、运行时配置在 `~/.agent-vm`；
- **本工具只清理宿主数据，不进入任何 VM 磁盘**；如需清理某台 VM，进入该 VM 内部执行同等操作；
- 同一份 Cage 配置内的项目共享客机系统、账户与文件系统，**项目之间没有隔离**；换身份应使用独立 VM；
- Cage 的"网络与隔离检查"展示配置与采样证据，**不能保证**账号不受平台限制。

选择理由：换号后最常见的问题是新旧环境重叠（同指纹、同出口、同存储）。隔离是从结构上消除重叠，而不是事后清理。

### 路线 B（社区实践，风险自负）

> ⚠️ 以下为社区公开报告的做法（附录 A），**本仓库不推荐、不提供支持**；多账号与订阅方式可能违反 Anthropic 服务条款，存在财产损失（礼品卡余额不可退）与账号全灭的风险；"不弹 KYC"为作者个人经验，未经独立验证。

要点摘录（细节见原帖）：手机端（App Store / Google Play）订阅 Pro 风控较低 → 新 Gmail 注册新美区 Apple ID → 礼品卡充值 → 手机端订阅 → 桌面端多账号用 [Magpie](https://github.com/yetone/magpie) 轮换；执行前先完成 P1–P4 的环境清理。

### 不推荐：在已污染的宿主机上直接换号登录

如果 P0 判定为环境关联，未清理直接换号等于把新账号放进同样的环境。

## P6 记录与复盘

| # | 操作 |
| --- | --- |
| 6.1 | 记录本次批次的时间戳、选择的 scope、`report.json` 路径（个人记录，勿含账号信息提交到任何仓库） |
| 6.2 | 保留 `~/ClaudeCleanBackups/` 与废纸篓副本 **7–14 天**，确认新环境稳定后再删除（备份含旧令牌，勿长期保留） |
| 6.3 | 若后续再次受限：对比两次时间线，若清理后短时间内复现，说明原因大概率不在本机环境，停止重复清理，转官方申诉 |

## 回滚

适用：清理后某个功能异常，需要恢复某项数据。

```sh
B=~/ClaudeCleanBackups/claude-mac-clean-<时间戳>   # report.json 里记录的批次
cat "$B/report.json"                               # 看动过哪些路径
shasum -a 256 -c <(jq -r '.["<相对路径>"]["sha256"] +"  <相对路径>"' "$B/hashes.json" --raw-output 2>/dev/null) 2>/dev/null || true
# 恢复单个文件/目录（先退出 Claude 应用）：
# cp -R "$B/<相对路径>" ~/<相对路径>
```

规则：**逐路径恢复，绝不整目录覆盖** `~/.claude`——那会覆盖清理之后新产生的对话历史。恢复 JSON 前先 diff 后续新增的配置键。

## 升级路径

| 信号 | 动作 |
| --- | --- |
| 清理 + 隔离后新账号仍快速受限 | 原因大概率不在本机（IP 段、付款方式、使用模式）；停止换号，走官方支持 |
| KYC 要求持续出现 | 只能官方渠道处理；本地工具无法影响 |
| 怀疑本机有恶意软件（而非常规残留） | 本工具不覆盖该场景，改用专业的恶意软件扫描 |

---

## 失败处理索引

| 症状 | 处理 |
| --- | --- |
| `Claude 相关进程仍在运行` | 按 0.3 正常退出后重跑；npm 安装的 Claude Code 注意检查隐藏终端 |
| `备份校验失败` / `备份期间源数据变化` | 有进程在写数据；回到 0.3，确认无进程后重跑 |
| `磁盘可用空间不足` | 清出至少（目标体积 + 100MB）后重跑 |
| `拒绝符号链接路径` | `~/.claude` 等被手动链接到别处；先移除链接、把真实数据放回原位，再执行 |
| 一键命令拿到旧版本 | `raw.githubusercontent.com` CDN 有数分钟缓存，等几分钟后重试 |

## 附录 A：社区实践来源

- **环境清理 + 多账号订阅实践**：[@Pluvio9yte 的 X 长文](https://x.com/Pluvio9yte/status/2116385910879379608)（2026-10-31）。作者同时是 [claude-ban-clean](https://github.com/Pluviobyte/claude-ban-clean) 的维护者；本仓库的清理方法参考自该项目（独立实现）。长文中"清理环境"一节与本 runbook 的 P1–P4 对应；"多账号订阅 + Magpie 轮换"对应 P5 路线 B。
- **隔离运行**：[Lee-xeo/Claude-Cage](https://github.com/Lee-xeo/Claude-Cage)（MIT）。对应 P5 路线 A。

本 runbook 对社区经验的采信原则：只纳入与"清理、备份、隔离"相关且可独立验证的部分；所有"某做法能降低风控"的说法均标注为社区经验、未经验证。

## 附录 B：Claude-Cage 共存要点

| 事项 | 说明 |
| --- | --- |
| 宿主清理 ≠ VM 清理 | 本工具只处理宿主 home；VM 内的 `~/.claude` 在虚拟磁盘里，需进入各 VM 清理 |
| 检测提醒 | 审计输出会检测 `~/.lima` / `~/.agent-vm` 并提示上述区别 |
| 共享目录 | Cage 打开的本机项目文件夹对 VM 可见；清理宿主前确认没有 VM 正在写这些目录 |
| 网络 | Cage 接管 VM 出口网络；宿主代理设置不在本工具清理范围 |

## 附录 C：命令速查

```sh
# 一键（审计 + 交互式清理）
curl -fsSL https://raw.githubusercontent.com/hyeebeen/claude-mac-clean/main/run.sh | bash
# 只读审计
curl -fsSL https://raw.githubusercontent.com/hyeebeen/claude-mac-clean/main/run.sh | bash -s -- --audit-only
# 克隆使用
git clone https://github.com/hyeebeen/claude-mac-clean.git && ./claude-mac-clean/run.sh
# 单元测试（临时目录，不碰真实数据）
python3 -m unittest discover -s tests -v
```
