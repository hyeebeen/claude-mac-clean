# claude-mac-clean

macOS 上一条命令审计、备份并定向清理 Claude（Claude Code / Claude Desktop）的本地缓存、旧账号字段与设备记录，并只读盘点浏览器里的 Claude 站点数据。

默认只读。真删除前要求在终端输入 `CONFIRM`，且一定先做逐文件 SHA-256 校验的完整备份。

> 📘 **账号受限或疑似环境污染？** 按标准处置手册操作：**[docs/RUNBOOK.md](docs/RUNBOOK.md)** —— 判定止损 → 保留备份 → 本机清理 → 浏览器清理 → 验证 → 恢复使用（含 [Claude-Cage](https://github.com/Lee-xeo/Claude-Cage) 隔离方案与社区实践对照）→ 回滚。

## 在新 Mac 上一键执行

```sh
curl -fsSL https://raw.githubusercontent.com/hyeebeen/claude-mac-clean/main/run.sh | bash
```

- 脚本下载到临时目录运行，**不安装任何东西**，不留守护进程或定时任务。
- 只读审计直接输出；要执行清理，在菜单里选择范围并输入 `CONFIRM`。
- 只读模式：`curl -fsSL .../run.sh | bash -s -- --audit-only`
- 全新 Mac 没有 Python 时会提示运行 `xcode-select --install`（Apple 官方命令行工具，内含 Python 3.9+），装完重新执行即可。

也可以克隆后运行：

```sh
git clone https://github.com/hyeebeen/claude-mac-clean.git
./claude-mac-clean/run.sh            # 或: python3 claude-mac-clean/scripts/claude_clean.py
```

## 清理范围

| 范围 | 内容 | 影响 |
| --- | --- | --- |
| `cache` | Claude Code 缓存/遥测目录、CLI 节点缓存、Desktop 图形缓存、日志、崩溃报告（按进程名精确匹配 claude/claude helper）、窗口与探测状态 | 不影响登录 |
| `account` | `.claude.json` 及其滚动备份、Desktop `config.json` 中的白名单账号字段（`oauthAccount` 等 5 个顶层键）；CLI `.credentials.json` 与桌面配置 journal 整体移除 | 影响登录状态 |
| `device` | `ant-did`、`ant-device-registry.json` 两个文件 | 需单独选择 |

**不会触碰**：对话历史、projects、sessions、history.jsonl、file-history、skills、hooks、plugins、MCP、settings.json、系统钥匙串、时区/代理/Shell，以及上述白名单之外的任何文件。

## 安全模型

- **先备份后动手**：所有目标完整复制到 `~/ClaudeCleanBackups/claude-mac-clean-<时间戳>/`（目录 0700），逐文件 SHA-256 校验通过、且再次确认源数据未变化后才开始清理。
- **可回溯**：移除项进入 `~/.Trash/claude-mac-clean-<时间戳>/`（不清空废纸篓）；备份内含 `report.json`（只记路径和键名，不记值）与 `hashes.json`（校验表，恢复时核对）。确认无误后自行删除备份并清空废纸篓——在此之前旧数据仍在磁盘上。
- **进程保护**：检测到 Claude 相关进程即停止，包括 npm 安装（`claude.exe`）、node 包装启动、Electron 桌面端；不接受管道代答，必须在真实终端输入 `CONFIRM`。
- **路径防护**：拒绝主目录之外的任何路径与符号链接组件；JSON 以 UTF-8 原子替换，写后复读校验。
- **中途失败即停**：报告状态为 `stopped`，已完成部分如实记录，不自动回滚、不谎报成功。

## 浏览器（只读盘点）

工具会对已安装的 Chrome / Edge / Brave / Arc / Chromium 逐 Profile 盘点 claude.ai、claude.com、anthropic.com 的 IndexedDB、Cookies 数量与 Claude 扩展（浏览器运行时不读数据库）。它**从不修改浏览器**；实际清理请在退出浏览器后，于「设置 → 隐私 → 站点数据」按站点逐个删除，不要按关键词整库删除 Persona、Cloudflare、hCaptcha 等第三方分区（它们同时服务其他站点）。Local/Session Storage、Service Worker 与 Safari 不在盘点范围内。

## 明确做不到的事

- 不能解除账号封禁，不能绕过任何身份验证；账号问题走官方支持。
- macOS 登录令牌在系统钥匙串里，本工具不读取也不修改钥匙串；要真正退出登录，先在 Claude Code 执行 `/logout`。
- 删除 `ant-did` 不更换底层设备密钥，也不撤销服务端设备登记；本地清理不等于"全盘无痕"。
- Windows / Linux 不支持。

## 开发

```sh
python3 -m unittest discover -s tests -v   # 全部测试在临时目录中进行
```

兼容 Python 3.9+（macOS 自带版本），仅用标准库。

## 来源与致谢

清理范围与方法参考了 [Pluviobyte/claude-ban-clean](https://github.com/Pluviobyte/claude-ban-clean)（该仓库未附许可证，本项目为独立实现；其作者的[多账号环境实践长文](https://x.com/Pluvio9yte/status/2116385910879379608)亦收录于 RUNBOOK 附录）。处置手册中的隔离方案参考 [Lee-xeo/Claude-Cage](https://github.com/Lee-xeo/Claude-Cage)（MIT）。本实现另外修复/增强了：

- 进程检测覆盖 npm 安装与 node 包装启动（原实现会漏检 `node`/`claude.exe` 进程）；
- 崩溃报告按进程名精确匹配且大小写不敏感（原实现的 `Claude_*` 通配会漏掉 CLI 的小写 `claude-*` 报告、误配第三方应用）；
- 备份与废纸篓的全部目录层级都是 0700（原实现中间目录回落到 0755）；
- JSON 读写显式 UTF-8（兼容 BOM），报告同目录提供 SHA-256 校验表；
- 新增浏览器站点数据只读盘点。

## 许可证

[MIT](LICENSE)
