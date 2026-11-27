"""Dev harness: serves the plugin page inside a stand-in for the Cage workbench.

    python3 dev/serve.py [--port 8765] [--export-folder DIR] [--home DIR]

Open http://127.0.0.1:8765/ (add ?scheme=dark or ?locale=zh-CN). The page runs
in a sandboxed iframe like in Cage; its tools/call requests are relayed to a
real linux-files/server.py process over stdin/stdout.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "linux-files"
DEV = ROOT / "dev"


class McpProcess:
    def __init__(self, env):
        self.process = subprocess.Popen([sys.executable, "-I", "-B", str(PLUGIN / "server.py")], cwd=PLUGIN,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        self.lock = threading.Lock()
        self.next = 1
        self.rpc("initialize", {"protocolVersion": "2025-06-18"})

    def rpc(self, method, params):
        with self.lock:
            message = {"jsonrpc": "2.0", "id": self.next, "method": method, "params": params}
            self.next += 1
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
            return json.loads(self.process.stdout.readline())


class Handler(SimpleHTTPRequestHandler):
    mcp = None

    def log_message(self, *args):
        pass

    def translate_path(self, path):
        path = path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return str(DEV / "harness.html")
        if path.startswith("/dev/"):
            return str(DEV / path[len("/dev/"):])
        if path.startswith("/view/"):
            return str(PLUGIN / "view" / path[len("/view/"):])
        return str(DEV / "missing")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        if self.path.startswith("/view/"):
            # Close to what the workbench applies to plugin pages.
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'")
        super().end_headers()

    def do_POST(self):
        if self.path != "/rpc":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reply = self.mcp.rpc(body["method"], body.get("params") or {})
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--export-folder", default="")
    parser.add_argument("--home", default="")
    parser.add_argument("--shared", action="append", default=[],
                        help="pretend this folder is shared with the Mac (repeatable); replaces the real mounts")
    args = parser.parse_args()
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME", "SHELL") if key in os.environ}
    env["HOME"] = args.home or os.path.expanduser("~")
    if args.export_folder:
        env["EXPORT_FOLDER"] = args.export_folder
    if args.shared:
        handle, name = tempfile.mkstemp(prefix="linux-files-mountinfo-")
        os.close(handle)
        fake = Path(name)
        fake.write_text("".join(f"{90 + i} 1 0:{90 + i} / {path.replace(' ', chr(92) + '040')} rw,relatime - fuse.sshfs sshfs rw\n"
                                for i, path in enumerate(args.shared)))
        env["LINUX_FILES_MOUNTINFO"] = str(fake)
    Handler.mcp = McpProcess(env)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
