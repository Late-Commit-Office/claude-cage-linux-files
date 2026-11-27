"""Puts linux-files into a checkout of the plugin marketplace (Lee-xeo/cage-plugins).

    python3 scripts/export_market.py MARKET_DIR --author LOGIN --fork OWNER/REPO [--branch NAME] [--run-url URL]

MARKET_DIR must be a checkout of the marketplace's main branch. The script
replaces plugins/linux-files with this repo's linux-files/ (without .DS_Store
or __pycache__) and adds the author to authors.json. It doesn't run the
marketplace's scripts/build.py, commit or push; the workflow
.github/workflows/market.yml does that.

It prints key=value lines (for $GITHUB_OUTPUT):
  version    the plugin's version
  status     "published" if the marketplace already lists this version
             (nothing was changed), "new" or "update" otherwise
  title      a commit and pull request title
  pr_url     a link that opens a pull request form with the title and body
It also writes the pull request body to MARKET_DIR/../pr-body.md.
"""
import argparse
import json
import re
import shutil
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "linux-files"
UPSTREAM = "Lee-xeo/cage-plugins"
IGNORE = shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc")


def plugin_version():
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    server = re.search(r'^VERSION = "([^"]+)"$', (PLUGIN / "server.py").read_text(encoding="utf-8"), re.M)
    if not server or server.group(1) != manifest["version"]:
        sys.exit(f"error: plugin.json version {manifest['version']} doesn't match VERSION in server.py")
    return manifest["name"], manifest["version"]


def listed_version(market, name):
    index = json.loads((market / "index.json").read_text(encoding="utf-8"))
    return next((entry["version"] for entry in index["plugins"] if entry["name"] == name and not entry.get("target")), None)


def add_author(market, name, author):
    path = market / "authors.json"
    authors = json.loads(path.read_text(encoding="utf-8"))
    authors[name] = author
    path.write_text(json.dumps(dict(sorted(authors.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pr_body(version, status, previous, run_url):
    tick = lambda done: "x" if done else " "
    tests = f"passed in CI: {run_url}" if run_url else "passed locally"
    return f"""## 变更类型 / Change Type

- [{tick(status == "new")}] 新增插件（New plugin）
- [{tick(status == "update")}] 更新已有插件（Update existing plugin，已提高 `plugin.json` 版本号）{f" {previous} → {version}" if previous else ""}
- [ ] 文档 / 脚本 / 仓库维护（Docs / Scripts / Maintenance）

## 插件概览 / Plugin Overview

| 项目 | 说明 |
| --- | --- |
| 插件名称与版本 | `linux-files` / `{version}` |
| 运行位置 (`kind`) | `guest` |
| 适用平台 | All (Python 3 standard library only) |
| 能力形态 | Page (`contributes.views`) with its own MCP server; all tools are page-only (`_meta.ui.visibility: ["app"]`) |
| 声明权限 (`permissions`) | `[]` |
| 所属分类 (`categories`) | `files`, `system` |
| 上游开源项目与许可 | None (original, MIT) |

### 功能说明与设计要点

A Finder-style column browser for the whole Linux file system: details and a text preview for any file, copy path or name, and download a file (or a folder as a `.zip`) into a folder shared with the user's computer.

1. **Why `guest`:** it browses the Linux environment's own file system and needs nothing on the user's computer.
2. **Runtime:** `python3 server.py`, standard library only. Nothing is downloaded at runtime.
3. **Data flow:** no network access. It reads files the environment's user can read. It writes only when the user confirms a download: into the chosen shared folder (written as a hidden `.partial` file, then renamed), and the remembered download folder in `~/.config/linux-files/preferences.json`. Models can't call its tools.

Source and tests: https://github.com/late-commit-office/claude-cage-linux-file

## 运行效果图 / Runtime Screenshots or Demo

<!-- TODO before marking ready: drag in screenshots of the Plugins page, the page in the workbench (light and dark), and a download. -->

## 测试与验证 / Testing & Verification

- **实测环境**：macOS (Apple Silicon) with a Debian 13 arm64 environment
- **自动检查**：
  - [x] `python3 scripts/build.py --check` 通过
  - [x] 插件自带单元测试 / 冒烟测试通过: Python tests (server and manifest rules) and headless-Chromium page checks, {tests}
- **端到端验证**：<!-- TODO before marking ready: what you did in Cage and what happened. -->installed in Cage from a draft.
- **尚未实测的范围**：Windows and Linux computers (shared-folder detection reads `/proc/self/mountinfo` for `fuse.sshfs`, `virtiofs` and `9p` mounts).

## 提交前自查清单 / Checklist

- [ ] 已在 PR 描述中附带插件在 Cage 中的**真实运行效果图**（截图 / GIF / 录屏），并已遮盖敏感信息
- [x] 效果图仅上传到 PR 描述中，未提交进 `plugins/`、`targets/` 或 `dist/` 目录
- [x] 已在根目录 `authors.json` 登记插件名与有效的 GitHub 用户名
- [x] 已在插件目录（含各 `targets/` 目录）提供覆盖全部 15 种 Cage 界面语言的 `i18n.json`，且 `plugin.json` 保持简洁英文
- [x] `permissions` 如实声明，`categories` 填写 1~3 个有效分类
- [x] 工具名称、说明、参数说明及 `i18n.json` 仅描述功能，不含产品名及 `VM` / `guest` / `虚拟机` / `客机` 等保留词
- [x] 运行时不下载未锁定代码（外部依赖已按要求锁定版本与哈希），并已关闭上游默认遥测/统计
- [x] 基于开源项目的插件已附上游 `LICENSE`、`source.json` 与说明数据流向及依赖的 `README.md` (not applicable)
- [{tick(status == "update")}] 修改已有插件时已提高 `plugin.json` 版本号（并按需清理不再保留的旧版 `dist/` 归档）
- [x] 已运行 `python3 scripts/build.py` 更新 `dist/` 与 `index.json` 并随本次 PR 一并提交
- [x] 同意以本仓库 [MIT 许可](https://github.com/Lee-xeo/cage-plugins/blob/main/LICENSE) 发布本次提交的插件代码
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("market", type=Path)
    parser.add_argument("--author", required=True, help="GitHub login shown as the plugin's author")
    parser.add_argument("--fork", required=True, help="OWNER/REPO of your fork of the marketplace")
    parser.add_argument("--branch", default="linux-files")
    parser.add_argument("--run-url", default="")
    args = parser.parse_args()
    market = args.market.resolve()
    if not (market / "scripts" / "build.py").is_file() or not (market / "index.json").is_file():
        sys.exit(f"error: {market} is not a checkout of {UPSTREAM}")

    name, version = plugin_version()
    previous = listed_version(market, name)
    if previous == version:
        print(f"version={version}\nstatus=published")
        return
    status = "update" if previous else "new"

    target = market / "plugins" / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(PLUGIN, target, ignore=IGNORE)
    add_author(market, name, args.author)

    title = (f"feat({name}): add Linux Files plugin (v{version})" if status == "new"
             else f"feat({name}): v{version}")
    body = pr_body(version, status, previous, args.run_url)
    (market.parent / "pr-body.md").write_text(body, encoding="utf-8")
    owner, repo = args.fork.split("/")
    query = urllib.parse.urlencode({"quick_pull": "1", "title": title, "body": body})
    url = f"https://github.com/{UPSTREAM}/compare/main...{owner}:{repo}:{args.branch}?{query}"
    print(f"version={version}\nstatus={status}\ntitle={title}\npr_url={url}")


if __name__ == "__main__":
    main()
