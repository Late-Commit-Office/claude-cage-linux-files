## 变更类型 / Change Type

- [x] 新增插件（New plugin）
- [ ] 更新已有插件（Update existing plugin，已提高 `plugin.json` 版本号）
- [ ] 文档 / 脚本 / 仓库维护（Docs / Scripts / Maintenance）

## 插件概览 / Plugin Overview

| 项目 | 说明 |
| --- | --- |
| 插件名称与版本 | `linux-files` / `0.2.8` |
| 运行位置 (`kind`) | `guest`（环境内） |
| 适用平台 | All: one folder in `plugins/`, Python 3 standard library only. Tested with macOS. |
| 能力形态 | 扩展页面 (`contributes.views`)，由自带的 MCP 服务器提供数据；**不向 Claude 提供工具**（见下文） |
| 声明权限 (`permissions`) | `[]` |
| 所属分类 (`categories`) | `files`, `system` |
| 上游开源项目与许可 | 无 (original work, MIT) |

### 功能说明与设计要点

**Linux 文件**在侧栏添加一个页面，以分栏方式浏览环境内的整个 Linux 文件系统：

- 从 `/` 起浏览当前用户可读的任意文件夹，可显示隐藏文件；
- 查看文件详情（类型、大小、时间、权限、所有者、链接目标），并预览文本文件前 4 KB；
- 复制路径或名称；
- 把文件（或打包成 `.zip` 的文件夹）下载到与电脑共享的文件夹。每次下载都需确认，也可记住下载位置。

1. **为何选 `guest`**：浏览的是环境自身的文件系统，不依赖电脑上的任何软件，在各系统上表现一致。
2. **运行方式**：`python3 server.py`，仅用 Python 标准库；运行时不下载任何代码，无遥测。
3. **数据流向**：不联网。只读取当前用户可读的文件（`/proc`、`/sys`、`/dev` 可浏览，但不能预览或下载）；仅在用户确认下载后，写入所选共享文件夹的 `Downloads/` 子文件夹（`Downloads` 若是软链接则拒绝；先写入隐藏的 `.partial` 文件，完成后以不覆盖的方式取一个空闲文件名）；记住的下载位置保存在 `~/.config/linux-files/preferences.json`。

#### No tools for Claude / 不向 Claude 提供工具

MCP 服务器只为插件页面提供数据。全部 7 个工具都声明了 `_meta.ui.visibility: ["app"]`，Cage 不会把它们提供给 Claude，也会拒绝模型调用（见 `internal/hostbridge/external.go`）；插件工坊的校验也把它们全部标为 `page_only: true`。

插件详情页仍会显示「Claude 工具」标签：`ui/src/host-plugins.jsx` 的 `purposes()` 只要插件声明了 `contributes.mcpServers` 就会加上它，不检查工具可见性。后端已为每个工具提供 `PageOnly`，可据此只给有模型可见工具的插件加标签；这属于 Cage 本身的改动，本 PR 不依赖它。

源码、测试与开发工具：https://github.com/late-commit-office/claude-cage-linux-files

## 运行效果图 / Runtime Screenshots or Demo

**插件页面 / Plugins page:** Linux 文件 installed, enabled and running. Its 7 tools are listed, it needs no permissions, and it runs in the environment. (The Claude 工具 tag is explained above.)
<!-- screenshots/linux-files-installed.png -->
<img width="2560" height="1756" alt="linux-files-installed" src="https://github.com/user-attachments/assets/0a1c18ce-0a48-4516-b260-617756dc2915" />


**浅色主题 / Light theme:** the page in the workbench, with the folders shared with the computer in the sidebar and a deep folder open.
<!-- screenshots/linux-files-light.png -->
<img width="2560" height="1756" alt="linux-files-light" src="https://github.com/user-attachments/assets/911b0803-0a5c-403d-968d-fb123ef34a9c" />


**深色主题 / Dark theme:** the same page in the dark theme. The columns scroll sideways freely; here they're scrolled past the selected file's details.
<!-- screenshots/linux-files-dark-scrolled.png -->
<img width="2560" height="1756" alt="linux-files-dark-scrolled" src="https://github.com/user-attachments/assets/b822db2b-af42-479c-adf8-2207bd3c32c3" />

## 测试与验证 / Testing & Verification

- **实测环境**：macOS (Apple Silicon), Cage v0.2.1, Debian 13 arm64 environment
- **自动检查**：
  - [x] `python3 scripts/build.py --check` 通过
  - [x] 插件自带单元测试 / 冒烟测试通过: Python tests for the server and Cage's manifest rules (`python3 -m unittest discover -s tests`), and page tests in headless Chromium inside a stand-in for the workbench with the same sandbox, CSP and message limits (`node tests/e2e.mjs`). Both passed in CI: TODO-CI-RUN-URL
- **端到端验证**：Installed in Cage from a plugin draft and used it there.
- **尚未实测的范围**：
  - Windows and Linux computers. Shared folders are found in `/proc/self/mountinfo` as `fuse.sshfs`, `virtiofs` or `9p` mounts, which is only verified with a Mac.
  - Intel Macs.

## 提交前自查清单 / Checklist

- [x] 已在 PR 描述中附带插件在 Cage 中的**真实运行效果图**（截图 / GIF / 录屏），并已遮盖敏感信息
- [x] 效果图仅上传到 PR 描述中，未提交进 `plugins/`、`targets/` 或 `dist/` 目录
- [x] 已在根目录 `authors.json` 登记插件名与有效的 GitHub 用户名
- [x] 已在插件目录（含各 `targets/` 目录）提供覆盖全部 15 种 Cage 界面语言的 `i18n.json`，且 `plugin.json` 保持简洁英文
- [x] `permissions` 如实声明，`categories` 填写 1~3 个有效分类
- [x] 工具名称、说明、参数说明及 `i18n.json` 仅描述功能，不含产品名及 `VM` / `guest` / `虚拟机` / `客机` 等保留词
- [x] 运行时不下载未锁定代码（外部依赖已按要求锁定版本与哈希），并已关闭上游默认遥测/统计 (no dependencies, no telemetry)
- [ ] 基于开源项目的插件已附上游 `LICENSE`、`source.json` 与说明数据流向及依赖的 `README.md` (not applicable: original work)
- [ ] 修改已有插件时已提高 `plugin.json` 版本号（并按需清理不再保留的旧版 `dist/` 归档）
- [x] 已运行 `python3 scripts/build.py` 更新 `dist/` 与 `index.json` 并随本次 PR 一并提交
- [x] 同意以本仓库 [MIT 许可](https://github.com/Lee-xeo/cage-plugins/blob/main/LICENSE) 发布本次提交的插件代码