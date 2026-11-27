"""Puts linux-files into a checkout of the plugin marketplace (Lee-xeo/cage-plugins).

    python3 scripts/export_market.py MARKET_DIR --author LOGIN --fork OWNER/REPO [--branch NAME] [--run-url URL] [--base REF]

MARKET_DIR is a checkout of the marketplace: its main branch, or the fork's
branch with main merged in. Whether this version is already published is
judged from REF's index.json when --base is given (e.g. origin/main), since a
fork branch's own index.json lists the version being exported. The script
replaces plugins/linux-files with this repo's linux-files/ (without .DS_Store
or __pycache__) and adds the author to authors.json. It doesn't run the
marketplace's scripts/build.py, commit or push; the workflow
.github/workflows/market.yml does that.

It prints key=value lines (for $GITHUB_OUTPUT):
  version    the plugin's version
  status     "published" if the marketplace already lists this version
             (nothing was changed), "new" or "update" otherwise
  title      a commit and pull request title
  pr_url     a link that opens a pull request form with the title, and the
             body when the link stays short enough
  body_in_url  "true" or "false"
It also writes the pull request body, first-pr.md filled in, to
MARKET_DIR/../pr-body.md.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "linux-files"
UPSTREAM = "Lee-xeo/cage-plugins"
IGNORE = shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc")
TEMPLATE = ROOT / "first-pr.md"
# A link longer than this may be cut off by the browser or GitHub; it then carries the title only.
MAX_URL = 7500


def plugin_version():
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    server = re.search(r'^VERSION = "([^"]+)"$', (PLUGIN / "server.py").read_text(encoding="utf-8"), re.M)
    if not server or server.group(1) != manifest["version"]:
        sys.exit(f"error: plugin.json version {manifest['version']} doesn't match VERSION in server.py")
    return manifest["name"], manifest["version"]


def listed_version(market, name, base=None):
    if base:
        text = subprocess.run(["git", "-C", str(market), "show", f"{base}:index.json"],
                              check=True, capture_output=True, text=True).stdout
    else:
        text = (market / "index.json").read_text(encoding="utf-8")
    index = json.loads(text)
    return next((entry["version"] for entry in index["plugins"] if entry["name"] == name and not entry.get("target")), None)


def add_author(market, name, author):
    path = market / "authors.json"
    authors = json.loads(path.read_text(encoding="utf-8"))
    authors[name] = author
    path.write_text(json.dumps(dict(sorted(authors.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pr_body(version, status, previous, run_url):
    """first-pr.md with its {{...}} placeholders filled in and its leading comment removed."""
    tick = lambda done: "x" if done else " "
    values = {
        "VERSION": version,
        "NEW": tick(status == "new"),
        "UPDATE": tick(status == "update"),
        "PREVIOUS": f" {previous} → {version}" if previous else "",
        "TESTS": f"Both passed in CI: {run_url}" if run_url else "Both passed locally.",
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.S)
    text = re.sub(r"\{\{([A-Z]+)\}\}", lambda match: values[match.group(1)], text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("market", type=Path)
    parser.add_argument("--author", required=True, help="GitHub login shown as the plugin's author")
    parser.add_argument("--fork", required=True, help="OWNER/REPO of your fork of the marketplace")
    parser.add_argument("--branch", default="linux-files")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--base", default="", help="git ref whose index.json says what upstream has published")
    args = parser.parse_args()
    market = args.market.resolve()
    if not (market / "scripts" / "build.py").is_file() or not (market / "index.json").is_file():
        sys.exit(f"error: {market} is not a checkout of {UPSTREAM}")

    name, version = plugin_version()
    previous = listed_version(market, name, args.base)
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
    base = f"https://github.com/{UPSTREAM}/compare/main...{owner}:{repo}:{args.branch}?"
    url = base + urllib.parse.urlencode({"quick_pull": "1", "title": title, "body": body})
    filled = len(url) <= MAX_URL
    if not filled:
        url = base + urllib.parse.urlencode({"quick_pull": "1", "title": title})
    print(f"version={version}\nstatus={status}\ntitle={title}\npr_url={url}\nbody_in_url={str(filled).lower()}")


if __name__ == "__main__":
    main()
