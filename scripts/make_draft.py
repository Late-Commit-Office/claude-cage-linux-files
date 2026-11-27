"""Builds a plugin-workshop draft of linux-files inside the Cage environment.

    python3 scripts/make_draft.py

Run this in a Cage terminal (or by Claude in Cage). It writes the draft to
~/.local/share/plugin-drafts/linux-files: the plugin's files, with plugin.json
turned into the workshop's neutral extension.json (kind "guest" becomes target
"workspace"; engines is dropped). Then ask Claude in Cage to submit the draft
(plugin_submit_draft), and approve it in Plugins → Pending drafts → Install.
"""
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "linux-files"
DRAFTS = Path(os.environ.get("PLUGIN_DRAFTS_DIR") or Path.home() / ".local" / "share" / "plugin-drafts")
NEUTRAL_FIELDS = ("name", "displayName", "version", "description", "icon", "permissions", "categories", "contributes")


def main():
    manifest = json.loads((PLUGIN / "plugin.json").read_text())
    if manifest["kind"] != "guest":
        raise SystemExit("only guest plugins map to target 'workspace'")
    extension = {key: manifest[key] for key in NEUTRAL_FIELDS if key in manifest}
    extension["target"] = "workspace"
    draft = DRAFTS / manifest["name"]
    if draft.exists():
        shutil.rmtree(draft)
    shutil.copytree(PLUGIN, draft, ignore=shutil.ignore_patterns("plugin.json", "__pycache__", "*.pyc", ".DS_Store"))
    (draft / "extension.json").write_text(json.dumps(extension, indent=2, ensure_ascii=False) + "\n")
    print(draft)


if __name__ == "__main__":
    main()
