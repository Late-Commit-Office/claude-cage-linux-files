"""Checks the plugin against the rules the Cage app enforces when loading it.

Mirrors docs/plugins.md and internal/hostbridge (bridge.go CheckTextWording,
manifest.go) of Lee-xeo/Claude-Cage v0.2.1, so mistakes show up before the
plugin is copied into ~/.claude-cage/plugins.
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "linux-files"
NAME = re.compile(r"^[a-z0-9-]{1,40}$")
RESERVED = ["cage", "guest", "virtual machine", "vm", "虚拟机", "客机"]
VIEW_TYPES = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".txt", ".svg", ".png", ".jpg", ".jpeg",
              ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".wasm"}
CATEGORIES = {"desktop", "developer", "system", "network", "files", "productivity", "example"}
RESERVED_ENV = {"HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "LC_ALL", "TZ"}


def reserved_word(text):
    """CheckTextWording: Latin words match whole, others as substrings."""
    lower = text.lower()
    fields = [field for field in re.split(r"[^a-z0-9\u0080-\U0010ffff]+", lower) if field]
    for word in RESERVED:
        if " " in word or ord(word[0]) >= 0x80:
            if word in lower:
                return word
        elif word in fields:
            return word
    return None


def only(test, mapping, allowed, where):
    extra = set(mapping) - set(allowed)
    test.assertFalse(extra, f"{where}: fields not allowed: {sorted(extra)}")


class ManifestTest(unittest.TestCase):
    manifest = json.loads((PLUGIN / "plugin.json").read_text())

    def test_top_level(self):
        m = self.manifest
        only(self, m, {"name", "displayName", "version", "description", "icon", "engines", "kind",
                       "platforms", "permissions", "categories", "contributes"}, "plugin.json")
        self.assertRegex(m["name"], NAME)
        self.assertEqual(m["name"], PLUGIN.name)
        self.assertRegex(m["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(m["description"].strip())
        self.assertLessEqual(len(m.get("displayName", "")), 80)
        self.assertRegex(m["engines"]["cage"], r"^(>=|\^)\d+\.\d+\.\d+$")
        only(self, m["engines"], {"cage"}, "engines")
        self.assertEqual(m["kind"], "guest")
        self.assertEqual(m["permissions"], [])
        self.assertLessEqual(len(m["categories"]), 3)
        self.assertTrue(set(m["categories"]) <= CATEGORIES)
        icon = PLUGIN / m["icon"]
        self.assertTrue(icon.is_file() and icon.stat().st_size <= 256 * 1024)

    def test_contributes(self):
        c = self.manifest["contributes"]
        only(self, c, {"mcpServers", "views", "settings", "desktopApps"}, "contributes")
        self.assertEqual(len(c["mcpServers"]), 1, "one MCP server per plugin")
        server = c["mcpServers"][0]
        only(self, server, {"id", "command"}, "mcpServers[0]")
        self.assertRegex(server["id"], NAME)
        self.assertTrue((PLUGIN / server["command"][1]).is_file())
        for view in c["views"]:
            only(self, view, {"id", "title", "entry", "icon", "server", "openUrls", "presentation", "width", "height"}, "view")
            self.assertRegex(view["id"], NAME)
            self.assertLessEqual(len(view["title"]), 80)
            self.assertEqual(view.get("server"), server["id"])
            self.assertIn(view.get("presentation", "page"), {"page", "floating"})
            self.assertTrue((PLUGIN / view["entry"]).is_file())
        ids, envs = set(), set()
        for setting in c.get("settings", []):
            only(self, setting, {"id", "title", "description", "type", "env", "required", "default", "choices"}, "setting")
            self.assertRegex(setting["id"], NAME)
            self.assertNotIn(setting["id"], ids)
            ids.add(setting["id"])
            self.assertLessEqual(len(setting["title"]), 80)
            self.assertLessEqual(len(setting.get("description", "")), 500)
            self.assertIn(setting["type"], {"string", "secret", "boolean", "choice"})
            self.assertRegex(setting["env"], r"^[A-Z][A-Z0-9_]{0,63}$")
            self.assertNotIn(setting["env"], RESERVED_ENV | envs)
            self.assertFalse(setting["env"].startswith(("LD_", "DYLD_")))
            envs.add(setting["env"])

    def test_files_are_plain_and_view_files_are_served(self):
        for path in PLUGIN.rglob("*"):
            self.assertFalse(path.is_symlink(), f"links are refused: {path}")
            self.assertTrue(path.is_file() or path.is_dir(), path)
            if path.is_file() and "view" in path.relative_to(PLUGIN).parts:
                self.assertIn(path.suffix, VIEW_TYPES, path)
                self.assertLessEqual(path.stat().st_size, 20 * 1024 * 1024)
            if path.is_file():
                self.assertLessEqual(len(str(path.relative_to(PLUGIN))), 200)
        self.assertFalse(list(PLUGIN.rglob("__pycache__")), "remove __pycache__ before installing")

    def test_page_has_no_inline_script(self):
        html = (PLUGIN / "view" / "index.html").read_text()
        for match in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S):
            self.assertIn("src=", match.group(1))
            self.assertEqual(match.group(2).strip(), "")
        self.assertNotRegex(html, r"\son[a-z]+\s*=", "inline event handlers are blocked")
        self.assertNotRegex(html, r"<form\b", "sandboxed pages cannot submit forms")

    def test_page_avoids_blocked_apis(self):
        script = (PLUGIN / "view" / "app.js").read_text()
        for blocked in ("localStorage", "sessionStorage", "window.open(", "alert(", "eval(", "new Function", "new Worker", "fetch("):
            self.assertNotIn(blocked, script)

    def test_tool_wording(self):
        process = subprocess.run([sys.executable, "-I", str(PLUGIN / "server.py")], text=True, capture_output=True, timeout=10,
                                 input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        tools = json.loads(process.stdout)["result"]["tools"]
        for tool in tools:
            text = " ".join([tool["name"], tool.get("title", ""), tool.get("description", ""),
                             json.dumps(tool["inputSchema"], ensure_ascii=False)])
            self.assertIsNone(reserved_word(text), tool["name"])
            self.assertRegex(tool["name"], r"^[a-zA-Z0-9_-]{1,64}$")

    def test_wording_checker_matches_cage(self):
        self.assertEqual(reserved_word("open the VM"), "vm")
        self.assertIsNone(reserved_word("vmlinuz files"))
        self.assertEqual(reserved_word("a virtual machine"), "virtual machine")
        self.assertEqual(reserved_word("cage-plugin"), "cage")


if __name__ == "__main__":
    unittest.main()
