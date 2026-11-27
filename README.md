# Linux Files for Claude Cage

A [Claude Cage](https://github.com/Lee-xeo/Claude-Cage) plugin that adds a **Linux Files** page to the sidebar: a Finder-style column browser for the whole Debian environment.

- Browse any folder the environment's user can read, from `/` down. Folders come first, sorted naturally, and dotfiles are hidden until you toggle them.
- See details for any file: kind, size, times, permissions, owner, symlink target, and a text preview of the first 4 KB.
- **Copy path** or name of any file or folder: right-click it, use the toolbar button, or press ⌘C.
- **Download to computer**: a file, or a folder as a `.zip`, is copied into a folder shared with your computer. By default this is the `Downloads` subfolder of Cage's default workspace, the same place the Cage browser saves downloads. You confirm every download.

The plugin is `kind: "guest"`: it runs inside the environment, needs no permissions and shows no approval prompts. All of its tools are page-only (`_meta.ui.visibility: ["app"]`), so Claude never sees or calls them.

## Install

Requires Cage **0.2.1** or newer. There are two ways.

### From inside Cage, as a draft you approve (recommended for testing)

Cage's built-in plugin workshop (`cage-plugin`) lets Claude in the environment submit a plugin draft. You then review and install it from the sidebar, and nothing has to be copied on the Mac.

1. In a Cage terminal, in this repo, run `python3 scripts/make_draft.py`. It writes the draft to `~/.local/share/plugin-drafts/linux-files`, converting `plugin.json` into the workshop's `extension.json` (`kind: "guest"` becomes `target: "workspace"`).
2. Ask Claude in Cage to validate and submit the `linux-files` draft. That calls the workshop's `plugin_validate` and `plugin_submit_draft` tools.
3. In Cage, go to **Plugins → Pending drafts** (待审核草稿). Check the files, choose **in the environment** as where it runs, and click **Install & Reload**.

To test a change, repeat the three steps. Installing over an existing copy moves the old one to `~/.claude-cage/plugins-removed/`.

### From the Mac

On the **Mac**, not in a Cage terminal:

```sh
./scripts/install.sh
```

The script copies `linux-files/` into `~/.claude-cage/plugins/linux-files`. Then, in Cage, open the sidebar's **Plugins** page, turn the plugins switch on if it's off, and click **Reload**. **Linux Files** appears in the sidebar.

To update, pull and run the script again, then **Reload**.

## Use

| Action | How |
|---|---|
| Open a folder | Click it; its contents open in the next column |
| Move | ↑ ↓ within a column, → into a folder, ← back; type letters to jump to a name |
| Go to a path | Click the empty part of the path bar, the ✎ button or press ⌘L; type a path (`~` works) and press Enter |
| Start from a place | Sidebar: Home, Root, or a folder shared with your computer |
| Hidden files | 👁 button or ⌘⇧. |
| Copy path or name | Right-click, the **Copy path** button, or ⌘C |
| Download to computer | Right-click → **Download to computer**, or the toolbar button |
| Download somewhere else once | Right-click → **Download to…** (asks even if a folder is remembered) |
| Change or forget the download folder | **Download folder…** in the status bar |

**Where downloads go.**

- **Choosing a folder.** Each download asks you to pick from the folders shared with your computer. Picking one and clicking **Download** is the only confirmation. The suggested folder, which already has a `Downloads` folder, is preselected; that's normally Cage's default workspace.
- **Remembering it.** Tick **Remember this folder and don't ask again** and later downloads go straight there, after one confirm that names the folder.
- **Changing it.** **Download folder…** in the status bar switches to another folder or back to **Ask every time**. The remembered choice is kept in `~/.config/linux-files/preferences.json` in the Linux environment, and it's ignored while that folder isn't shared (for example after closing a project).
- **Fixing one folder for good.** Set **Download folder** in the plugin's settings (Plugins → Linux Files → Settings) to a shared folder's path as Linux sees it. It overrides the choice made in the page, and files go straight into it rather than a `Downloads` subfolder. A plugin can't write its own settings in Cage 0.2.1, so the page can't fill this in for you.
- **How files are saved.** Files go into the chosen folder's `Downloads` subfolder, and name clashes get a ` (2)` suffix. Folders become `<name>.zip`: links, pipes and devices are left out, and unreadable files are skipped and counted.

## How it works

```
linux-files/
├── plugin.json      guest plugin: one MCP server, one page, one optional setting
├── server.py        MCP server on stdin/stdout (Python 3 standard library only)
├── view/            the page: index.html, app.js, style.css
├── i18n.json        marketplace name and description in all 15 Cage languages
└── icon.svg
```

The page runs in Cage's sandboxed frame. It talks to the workbench over `postMessage` (JSON-RPC: `ui/initialize`, `tools/call`, `ui/copy`, `ui/confirm`, `ui/notify`, `ui/setState`) and gets all its data from its own server's tools:

| Tool | Does |
|---|---|
| `list_directory` | One folder, paged (300 entries per page, up to 50,000 read) |
| `path_details` | `stat`, owner, symlink target, item count, text preview |
| `places` | Home, `/`, the writable folders shared with the computer (found in `/proc/self/mountinfo`: `fuse.sshfs`, `virtiofs`, `9p`), and the remembered download folder |
| `remember_destination` | Save a shared folder as the download folder, or `null` to ask every time |
| `export_start` / `export_status` / `export_cancel` | Background copy into the shared folder, written to a hidden `.partial` file and renamed when complete |

The page uses the workbench's theme colours (light and dark) and its language (English, 简体中文, 繁體中文). It remembers the last location, the hidden-files toggle and the download folder.

**Limits.** The page sees what the environment's user can read: root-only paths show the error in their column. `/proc`, `/sys` and `/dev` can be browsed but not previewed or downloaded. A plugin page can't open a native Save dialog, which is why downloads go through a shared folder.

## Develop

```sh
python3 -m unittest discover -s tests            # server and manifest rules (26 tests)
node tests/e2e.mjs [--screenshots DIR]            # the page in headless Chromium (19 checks)
python3 dev/serve.py --shared /tmp/a --shared /tmp/b   # try the page at http://127.0.0.1:8765/
```

`--shared` pretends folders are shared with the computer, in place of the real mounts, so test downloads never touch real shared folders. `--export-folder` sets the plugin setting instead. `dev/serve.py` and `dev/harness.*` stand in for the Cage workbench. They frame the page with the same sandbox and CSP, answer the page protocol with Cage's limits (8 requests in flight, 1 MiB per message), and relay tool calls to a real `server.py`. Add `?scheme=dark` or `?locale=zh-CN` to the URL to switch theme or language.

`tests/test_manifest.py` re-checks the rules Cage enforces when it loads a plugin (`docs/plugins.md` and `internal/hostbridge` in Claude-Cage v0.2.1). That includes the reserved words, such as the product name and "VM", that tool names, descriptions and schemas must not contain.

The end-to-end test needs Node 24 and Chromium or Chrome. The other tests need only Python 3.
