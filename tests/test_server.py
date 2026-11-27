"""Drives linux-files/server.py over stdin/stdout the way the workbench does."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "linux-files"
SERVER = PLUGIN / "server.py"


def load_module():
    sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder
    spec = importlib.util.spec_from_file_location("linux_files_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Server:
    def __init__(self, env=None):
        environment = dict(os.environ)
        environment.update(env or {})
        self.process = subprocess.Popen([sys.executable, "-I", str(SERVER)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, env=environment)
        self.next = 1

    def send(self, method, params=None):
        message = {"jsonrpc": "2.0", "id": self.next, "method": method}
        if params is not None:
            message["params"] = params
        self.next += 1
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        reply = json.loads(self.process.stdout.readline())
        assert reply["id"] == message["id"]
        return reply

    def call(self, name, **arguments):
        reply = self.send("tools/call", {"name": name, "arguments": arguments})
        return reply["result"]

    def ok(self, name, **arguments):
        result = self.call(name, **arguments)
        assert not result.get("isError"), result
        return result["structuredContent"]

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=5)
        self.process.stdout.close()


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tree = self.root / "tree"
        (tree / "sub dir").mkdir(parents=True)
        (tree / "file10.txt").write_text("ten\n")
        (tree / "file2.txt").write_text("two\n")
        (tree / "Alpha.bin").write_bytes(b"\0\1\2")
        (tree / ".hidden").write_text("secret\n")
        (tree / "sub dir" / "inner.txt").write_text("inner\n")
        os.symlink("sub dir", tree / "link-to-dir")
        os.symlink("missing", tree / "broken-link")
        os.mkfifo(tree / "pipe")
        self.tree = tree
        self.shared = self.root / "shared"
        self.shared.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.mountinfo = self.root / "mountinfo"
        self.mountinfo.write_text("")  # no real shares: only what a test declares
        self.share(self.shared)
        self.downloads = self.shared / "Downloads"
        self.server = self.start({})

    def start(self, env):
        server = Server({"HOME": str(self.home), "LINUX_FILES_MOUNTINFO": str(self.mountinfo), **env})
        server.send("initialize", {"protocolVersion": "2025-06-18"})
        return server

    def share(self, *folders):
        self.mountinfo.write_text("".join(f"{50 + i} 1 0:{50 + i} / {folder} rw - fuse.sshfs sshfs rw\n"
                                          for i, folder in enumerate(folders)))

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def test_tools_are_page_only(self):
        tools = self.server.send("tools/list")["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertEqual(names, {"list_directory", "path_details", "places", "remember_destination",
                                 "export_start", "export_status", "export_cancel"})
        for tool in tools:
            self.assertEqual(tool["_meta"]["ui"]["visibility"], ["app"], tool["name"])

    def test_unknown_method_and_notification(self):
        self.server.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        reply = self.server.send("nope")
        self.assertEqual(reply["error"]["code"], -32601)

    def test_listing_sorts_folders_first_naturally_and_hides_dotfiles(self):
        listing = self.server.ok("list_directory", path=str(self.tree))
        names = [entry["name"] for entry in listing["entries"]]
        self.assertEqual(names[:2], ["link-to-dir", "sub dir"])
        self.assertLess(names.index("file2.txt"), names.index("file10.txt"))
        self.assertNotIn(".hidden", names)
        self.assertEqual(listing["hidden_count"], 1)
        self.assertEqual(listing["parent"], str(self.root))
        kinds = {entry["name"]: entry for entry in listing["entries"]}
        self.assertEqual(kinds["link-to-dir"]["target_kind"], "directory")
        self.assertEqual(kinds["broken-link"]["target_kind"], "broken")
        self.assertEqual(kinds["pipe"]["kind"], "other")
        with_hidden = self.server.ok("list_directory", path=str(self.tree), hidden=True)
        self.assertIn(".hidden", [entry["name"] for entry in with_hidden["entries"]])

    def test_listing_pages(self):
        first = self.server.ok("list_directory", path=str(self.tree), limit=2)
        rest = self.server.ok("list_directory", path=str(self.tree), offset=2, limit=100)
        self.assertEqual(len(first["entries"]), 2)
        self.assertEqual(first["total"], len(first["entries"]) + len(rest["entries"]))

    def test_listing_errors(self):
        self.assertTrue(self.server.call("list_directory", path="relative")["isError"])
        self.assertTrue(self.server.call("list_directory", path=str(self.tree / "file2.txt"))["isError"])
        self.assertTrue(self.server.call("list_directory", path=str(self.root / "missing"))["isError"])
        self.assertTrue(self.server.call("list_directory", path=str(self.tree), offset="x")["isError"])
        self.assertTrue(self.server.call("no_such_tool")["isError"])

    def test_root_listing(self):
        listing = self.server.ok("list_directory", path="/")
        self.assertIsNone(listing["parent"])
        self.assertIn("proc", [entry["name"] for entry in listing["entries"]])

    def test_details(self):
        text = self.server.ok("path_details", path=str(self.tree / "file2.txt"))
        self.assertEqual(text["kind"], "file")
        self.assertEqual(text["preview"], {"text": "two\n", "complete": True})
        self.assertEqual(text["mime"], "text/plain")
        binary = self.server.ok("path_details", path=str(self.tree / "Alpha.bin"))
        self.assertNotIn("preview", binary)
        folder = self.server.ok("path_details", path=str(self.tree / "sub dir"))
        self.assertEqual((folder["kind"], folder["items"]), ("directory", 1))
        link = self.server.ok("path_details", path=str(self.tree / "link-to-dir"))
        self.assertEqual((link["kind"], link["target_kind"], link["link_target"]), ("symlink", "directory", "sub dir"))
        broken = self.server.ok("path_details", path=str(self.tree / "broken-link"))
        self.assertEqual(broken["target_kind"], "broken")
        pipe = self.server.ok("path_details", path=str(self.tree / "pipe"))
        self.assertEqual(pipe["kind"], "other")  # and the call did not block on the fifo
        proc = self.server.ok("path_details", path="/proc/self/status")
        self.assertNotIn("preview", proc)

    def test_places(self):
        places = self.server.ok("places")
        self.assertEqual(places["root"], "/")
        self.assertEqual(places["shared"], [{"path": str(self.shared), "writable": True, "has_downloads": False}])
        self.assertEqual(places["suggested"], str(self.shared))
        self.assertIsNone(places["remembered"])

    def test_remember_forget_and_restart(self):
        self.server.close()
        one, two = self.root / "one", self.root / "two"
        (one / "Downloads").mkdir(parents=True)
        two.mkdir()
        self.share(one, two)
        self.server = self.start({})
        places = self.server.ok("places")
        self.assertEqual([folder["path"] for folder in places["shared"]], [str(one), str(two)])
        self.assertEqual((places["remembered"], places["suggested"]), (None, str(one)))
        places = self.server.ok("remember_destination", destination=str(two))
        self.assertEqual(places["remembered"], str(two))
        self.assertTrue((self.home / ".config" / "linux-files" / "preferences.json").is_file())
        self.server.close()
        self.server = self.start({})
        self.assertEqual(self.server.ok("places")["remembered"], str(two))  # survives a restart
        job = self.server.ok("export_start", path=str(self.tree / "file2.txt"), destination=str(two))
        self.assertEqual(self.wait(job["id"])["result"], str(two / "Downloads" / "file2.txt"))
        self.assertTrue(self.server.call("remember_destination", destination=str(self.tree))["isError"])  # not shared
        self.assertTrue(self.server.call("remember_destination")["isError"])  # forgetting must be explicit
        self.assertEqual(self.server.ok("places")["remembered"], str(two))
        self.assertIsNone(self.server.ok("remember_destination", destination=None)["remembered"])
        self.assertIsNone(self.server.ok("places")["remembered"])

    def test_saved_folder_that_is_no_longer_shared_is_ignored(self):
        self.server.close()
        one = self.root / "one"
        one.mkdir()
        self.share(one)
        self.server = self.start({})
        self.server.ok("remember_destination", destination=str(one))
        self.share()  # the project was closed
        self.assertIsNone(self.server.ok("places")["remembered"])

    def wait(self, job):
        for _ in range(200):
            status = self.server.ok("export_status", job=job)
            if status["state"] != "running":
                return status
            time.sleep(0.02)
        self.fail("export did not finish")

    def test_export_file_and_unique_names(self):
        for expected in ("file2.txt", "file2 (2).txt"):
            job = self.server.ok("export_start", path=str(self.tree / "file2.txt"), destination=str(self.shared))
            status = self.wait(job["id"])
            self.assertEqual(status["state"], "done", status)
            self.assertEqual(status["result"], str(self.downloads / expected))
            self.assertEqual((self.downloads / expected).read_text(), "two\n")
        self.assertEqual(sorted(os.listdir(self.downloads)), ["file2 (2).txt", "file2.txt"])  # no partial files left

    def test_export_folder_as_zip(self):
        job = self.server.ok("export_start", path=str(self.tree), destination=str(self.shared))
        status = self.wait(job["id"])
        self.assertEqual(status["state"], "done", status)
        with zipfile.ZipFile(status["result"]) as archive:
            names = set(archive.namelist())
            self.assertIn("tree/sub dir/inner.txt", names)
            self.assertIn("tree/.hidden", names)
            self.assertNotIn("tree/pipe", names)
            self.assertNotIn("tree/link-to-dir", names)
            self.assertEqual(archive.read("tree/file10.txt"), b"ten\n")

    def test_export_folder_containing_its_destination(self):
        job = self.server.ok("export_start", path=str(self.root), destination=str(self.shared))
        status = self.wait(job["id"])
        self.assertEqual(status["state"], "done", status)
        with zipfile.ZipFile(status["result"]) as archive:
            self.assertFalse(any(name.endswith(".partial") for name in archive.namelist()))

    def test_export_refusals(self):
        refused = [
            dict(path=str(self.tree / "file2.txt"), destination=str(self.root / "tree")),  # not shared
            dict(path="/", destination=str(self.shared)),
            dict(path="/proc/self", destination=str(self.shared)),
            dict(path=str(self.tree / "pipe"), destination=str(self.shared)),
            dict(path=str(self.tree / "broken-link"), destination=str(self.shared)),
        ]
        for arguments in refused:
            self.assertTrue(self.server.call("export_start", **arguments)["isError"], arguments)
        self.assertTrue(self.server.call("export_status", job="nope")["isError"])

    def test_export_cancel(self):
        big = self.tree / "big.bin"
        with open(big, "wb") as file:
            file.truncate(512 * 1024 * 1024)  # sparse: reading it takes a while
        job = self.server.ok("export_start", path=str(big), destination=str(self.shared))
        self.server.ok("export_cancel", job=job["id"])
        status = self.wait(job["id"])
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual([path for path in self.shared.rglob("*") if path.is_file()], [])


class UnitTest(unittest.TestCase):
    module = load_module()

    def test_shared_mounts(self):
        mountinfo = "\n".join([
            "22 1 0:21 / / rw,relatime - ext4 /dev/vda1 rw",
            "40 22 0:35 / /Users/me/Cage\\040Work rw,nosuid,nodev,relatime - fuse.sshfs sshfs rw,user_id=0",
            "41 22 0:36 / /Users/me/project rw,relatime shared:5 - fuse.sshfs sshfs rw",
            "42 22 0:37 / /Users/me/readonly ro,relatime - fuse.sshfs sshfs ro",
            "43 22 0:38 / /mnt/virt rw - virtiofs share rw",
            "44 22 0:5 / /proc rw - proc proc rw",
            "45 22 0:39 / /proc/fake rw - fuse.sshfs sshfs rw",
            "garbage line",
        ])
        self.assertEqual(self.module.shared_mounts(mountinfo), ["/Users/me/Cage Work", "/Users/me/project", "/mnt/virt"])

    def test_suggested_prefers_a_downloads_folder(self):
        folders = [
            {"path": "/a", "writable": True, "has_downloads": False},
            {"path": "/b", "writable": True, "has_downloads": True},
            {"path": "/c", "writable": False, "has_downloads": True},
        ]
        self.assertEqual(self.module.suggested(folders), "/b")
        self.assertIsNone(self.module.suggested([folders[2]]))

    def test_free_name(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("a.tar.gz", ".bashrc", "plain"):
                Path(folder, name).write_text("")
            self.assertEqual(self.module.free_name(folder, "a.tar.gz"), "a (2).tar.gz")
            self.assertEqual(self.module.free_name(folder, ".bashrc"), ".bashrc (2)")
            self.assertEqual(self.module.free_name(folder, "plain"), "plain (2)")
            self.assertEqual(self.module.free_name(folder, "new.txt"), "new.txt")


if __name__ == "__main__":
    unittest.main()
