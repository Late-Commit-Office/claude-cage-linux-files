# MCP server for the Linux Files page: lists folders, describes files and
# copies files or zipped folders into a folder shared with the user's computer.
# Every tool is for the plugin's own page only (_meta.ui.visibility: ["app"]).
# Standard input and output carry newline-delimited JSON-RPC; Python's
# standard library only.
import grp
import json
import mimetypes
import os
import pwd
import re
import stat
import sys
import threading
import time
import uuid
import zipfile

VERSION = "0.2.1"
PAGE_ONLY = {"ui": {"visibility": ["app"]}}
MAX_SCAN = 50000          # entries read from one folder before giving up
DEFAULT_LIMIT = 300
MAX_LIMIT = 1000
PREVIEW_BYTES = 4096
PREVIEW_MAX_FILE = 2 * 1024 * 1024
COUNT_LIMIT = 10000
# Pseudo file systems: reading them can block or never end.
SPECIAL_ROOTS = ("/proc", "/sys", "/dev")
SHARED_TYPES = ("fuse.sshfs", "virtiofs", "9p")
EXPORT_SUBFOLDER = "Downloads"


def tool(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": list(required)},
        "_meta": PAGE_ONLY,
    }


PATH = {"type": "string", "description": "Absolute path."}
TOOLS = [
    tool("list_directory", "List one folder: names, kinds, sizes, permissions and modification times, folders first.",
         {"path": PATH,
          "offset": {"type": "integer", "minimum": 0, "description": "Index of the first entry to return."},
          "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "description": "Most entries to return."},
          "hidden": {"type": "boolean", "description": "Include names that start with a dot."}},
         ["path"]),
    tool("path_details", "Describe one file or folder, with a short text preview for small text files.",
         {"path": PATH}, ["path"]),
    tool("places", "Starting points: the home folder, the root folder and the folders shared with the user's computer.",
         {}),
    tool("remember_destination", "Remember the shared folder that downloads go to, or forget it so the page asks each time.",
         {"destination": {"type": ["string", "null"], "description": "A shared folder returned by places, or null to forget."}},
         ["destination"]),
    tool("export_start", "Copy a file, or a folder as a zip archive, into the Downloads folder of a shared folder.",
         {"path": PATH,
          "destination": {"type": "string", "description": "A shared folder returned by places."}},
         ["path", "destination"]),
    tool("export_status", "Progress of a copy started with export_start.",
         {"job": {"type": "string"}}, ["job"]),
    tool("export_cancel", "Stop a copy started with export_start.",
         {"job": {"type": "string"}}, ["job"]),
]


class Refused(Exception):
    """A failure that is reported to the page as a tool error."""


# ---------------------------------------------------------------- paths

def absolute(path):
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        raise Refused("Give an absolute path.")
    return os.path.normpath(path) if path != "/" else "/"


def under(path, roots):
    return any(path == root or path.startswith(root + "/") for root in roots)


def kind_of(mode):
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def natural_key(name):
    parts = re.split(r"(\d+)", name.casefold())
    return [(0, int(part), part) if part.isdigit() else (1, 0, part) for part in parts]


def user_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def group_name(gid):
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def os_message(error):
    reason = error.strerror or str(error)
    return f"{reason}: {error.filename}" if error.filename else reason


# ---------------------------------------------------------------- listing

def describe_entry(entry):
    item = {"name": entry.name, "hidden": entry.name.startswith(".")}
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError:
        item.update(kind="unknown")
        return item
    item.update(kind=kind_of(info.st_mode), mode=stat.filemode(info.st_mode),
                size=info.st_size, modified=int(info.st_mtime))
    if item["kind"] == "symlink":
        try:
            target = os.stat(entry.path)
            item["target_kind"] = kind_of(target.st_mode)
            item["size"] = target.st_size
        except OSError:
            item["target_kind"] = "broken"
    return item


def is_folder(item):
    return item["kind"] == "directory" or item.get("target_kind") == "directory"


def list_directory(args):
    path = absolute(args.get("path"))
    hidden = bool(args.get("hidden", False))
    offset = max(0, int(args.get("offset", 0) or 0))
    limit = min(MAX_LIMIT, max(1, int(args.get("limit", DEFAULT_LIMIT) or DEFAULT_LIMIT)))
    items, hidden_count, truncated = [], 0, False
    try:
        with os.scandir(path) as scan:
            for count, entry in enumerate(scan):
                if count >= MAX_SCAN:
                    truncated = True
                    break
                item = describe_entry(entry)
                if item["hidden"]:
                    hidden_count += 1
                    if not hidden:
                        continue
                items.append(item)
    except NotADirectoryError:
        raise Refused(f"Not a folder: {path}")
    except OSError as error:
        raise Refused(os_message(error))
    items.sort(key=lambda item: (0 if is_folder(item) else 1, natural_key(item["name"])))
    return {
        "path": path,
        "parent": None if path == "/" else os.path.dirname(path),
        "total": len(items),
        "hidden_count": hidden_count,
        "offset": offset,
        "entries": items[offset:offset + limit],
        "truncated": truncated,
    }


# ---------------------------------------------------------------- details

def text_preview(path, info):
    if not stat.S_ISREG(info.st_mode) or info.st_size > PREVIEW_MAX_FILE or under(path, SPECIAL_ROOTS):
        return None
    try:
        with open(path, "rb") as file:
            data = file.read(PREVIEW_BYTES)
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        # A multi-byte character cut at the end of the sample is still text.
        if error.start < len(data) - 3:
            return None
        text = data[:error.start].decode("utf-8")
    return {"text": text, "complete": info.st_size <= PREVIEW_BYTES}


def path_details(args):
    path = absolute(args.get("path"))
    try:
        link = os.lstat(path)
    except OSError as error:
        raise Refused(os_message(error))
    details = {
        "path": path,
        "name": os.path.basename(path) or "/",
        "kind": kind_of(link.st_mode),
        "mode": stat.filemode(link.st_mode),
        "octal": oct(stat.S_IMODE(link.st_mode))[2:].zfill(4),
        "owner": user_name(link.st_uid),
        "group": group_name(link.st_gid),
        "size": link.st_size,
        "modified": int(link.st_mtime),
        "accessed": int(link.st_atime),
        "changed": int(link.st_ctime),
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
    }
    info = link
    if stat.S_ISLNK(link.st_mode):
        try:
            details["link_target"] = os.readlink(path)
        except OSError:
            pass
        try:
            info = os.stat(path)
            details["target_kind"] = kind_of(info.st_mode)
            details["size"] = info.st_size
            details["real_path"] = os.path.realpath(path)
        except OSError:
            details["target_kind"] = "broken"
            return details
    if stat.S_ISDIR(info.st_mode):
        count = 0
        try:
            with os.scandir(path) as scan:
                for _ in scan:
                    count += 1
                    if count >= COUNT_LIMIT:
                        break
            details["items"] = count
            details["items_capped"] = count >= COUNT_LIMIT
        except OSError:
            pass
    elif stat.S_ISREG(info.st_mode):
        details["mime"] = mimetypes.guess_type(path)[0]
        preview = text_preview(path, info)
        if preview:
            details["preview"] = preview
    return details


# ---------------------------------------------------------------- shared folders

def unescape_mount(field):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), field)


def shared_mounts(mountinfo):
    """Writable folders served from the user's computer, from /proc/self/mountinfo text."""
    found = []
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        fields, tail = left.split(), right.split()
        if len(fields) < 6 or not tail:
            continue
        fstype, point, options = tail[0], unescape_mount(fields[4]), fields[5].split(",")
        if not any(fstype == shared or fstype.startswith(shared + ".") for shared in SHARED_TYPES):
            continue
        if "ro" in options or under(point, SPECIAL_ROOTS) or point in found:
            continue
        found.append(point)
    return found


def read_mountinfo():
    # LINUX_FILES_MOUNTINFO is for tests only: Cage starts guest plugins with a
    # fixed environment (HOME, USER, PATH, … and declared settings), never this.
    source = os.environ.get("LINUX_FILES_MOUNTINFO") or "/proc/self/mountinfo"
    try:
        with open(source, encoding="utf-8", errors="replace") as file:
            return file.read()
    except OSError:
        return ""


def configured_folder():
    folder = os.environ.get("EXPORT_FOLDER", "").strip()
    return os.path.normpath(folder) if folder.startswith("/") else ""


def shared_folders():
    folders = []
    setting = configured_folder()
    if setting:
        folders.append({"path": setting, "source": "setting"})
    for point in shared_mounts(read_mountinfo()):
        # A single shared file is bound onto its own path: folders only.
        if point != setting and os.path.isdir(point):
            folders.append({"path": point, "source": "mount"})
    for folder in folders:
        folder["writable"] = os.access(folder["path"], os.W_OK)
        folder["has_downloads"] = os.path.isdir(os.path.join(folder["path"], EXPORT_SUBFOLDER))
    return folders


def suggested(folders):
    writable = [folder for folder in folders if folder["writable"]]
    for rule in (lambda folder: folder["source"] == "setting",
                 lambda folder: folder["has_downloads"],
                 lambda folder: True):
        for folder in writable:
            if rule(folder):
                return folder["path"]
    return None


def preferences_path():
    return os.path.join(os.path.expanduser("~"), ".config", "linux-files", "preferences.json")


def load_preferences():
    try:
        with open(preferences_path(), encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_preferences(data):
    path = preferences_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")
    os.replace(temporary, path)


def remembered(folders):
    """The folder downloads go to without asking: the plugin setting wins over a saved choice."""
    usable = {folder["path"]: folder for folder in folders if folder["writable"]}
    setting = configured_folder()
    if setting:
        return (setting, "setting") if setting in usable else (None, None)
    saved = load_preferences().get("download_folder")
    if isinstance(saved, str) and saved in usable:
        return saved, "saved"
    return None, None


def places(_args):
    folders = shared_folders()
    home = os.path.expanduser("~")
    folder, source = remembered(folders)
    return {"home": home, "root": "/", "shared": folders, "suggested": suggested(folders),
            "remembered": folder, "remembered_source": source, "export_subfolder": EXPORT_SUBFOLDER}


def remember_destination(args):
    if "destination" not in args:
        raise Refused("Give a shared folder, or null to forget the saved one.")
    destination = args["destination"]
    preferences = load_preferences()
    if destination is None:
        preferences.pop("download_folder", None)
    else:
        export_target(destination)  # refuses folders that are not shared and writable
        preferences["download_folder"] = destination
    try:
        save_preferences(preferences)
    except OSError as error:
        raise Refused(os_message(error))
    return places({})


# ---------------------------------------------------------------- export

JOBS = {}
JOBS_LOCK = threading.Lock()


class Cancelled(Exception):
    pass


def export_target(destination):
    folders = shared_folders()
    match = next((folder for folder in folders if folder["path"] == destination), None)
    if match is None:
        raise Refused("That folder is not shared with your computer.")
    if not match["writable"]:
        raise Refused(f"Cannot write to {destination}.")
    if match["source"] == "setting":
        return destination
    return os.path.join(destination, EXPORT_SUBFOLDER)


def free_name(folder, name):
    stem, dot, extension = name.partition(".") if not name.startswith(".") else (name, "", "")
    candidate, number = name, 2
    while os.path.lexists(os.path.join(folder, candidate)):
        candidate = f"{stem} ({number}){dot}{extension}"
        number += 1
    return candidate


def copy_stream(job, source, target):
    while True:
        if job["cancel"].is_set():
            raise Cancelled()
        chunk = source.read(1 << 20)
        if not chunk:
            return
        target.write(chunk)
        job["bytes_done"] += len(chunk)


def write_file(job, source_path, partial):
    with open(source_path, "rb") as source, open(partial, "xb") as target:
        copy_stream(job, source, target)
    job["files_done"] = 1


def skip(job, reason):
    job["skipped_count"] += 1
    if len(job["skipped"]) < 5:
        job["skipped"].append(reason)


def write_zip(job, folder, partial):
    base = os.path.dirname(folder.rstrip("/")) or "/"
    with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for root, dirs, files in os.walk(folder, onerror=lambda error: skip(job, os_message(error))):
            if under(root, SPECIAL_ROOTS):
                dirs[:] = []
                continue
            relative_root = os.path.relpath(root, base)
            if not files and not dirs:
                archive.writestr(relative_root + "/", b"")
            for name in sorted(files):
                path = os.path.join(root, name)
                if path == partial:
                    continue  # the archive being written, when the folder holds its own destination
                try:
                    info = os.lstat(path)
                except OSError as error:
                    skip(job, os_message(error))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue  # links, pipes, sockets and devices are left out
                entry = zipfile.ZipInfo.from_file(path, os.path.join(relative_root, name), strict_timestamps=False)
                entry.compress_type = zipfile.ZIP_DEFLATED
                try:
                    with open(path, "rb") as source, archive.open(entry, "w", force_zip64=True) as target:
                        copy_stream(job, source, target)
                    job["files_done"] += 1
                except PermissionError as error:
                    skip(job, os_message(error))


def run_export(job):
    partial = job["partial"]
    try:
        if job["is_folder"]:
            write_zip(job, job["source"], partial)
        else:
            write_file(job, job["source"], partial)
        final = os.path.join(job["folder"], free_name(job["folder"], job["name"]))
        os.rename(partial, final)
        job.update(state="done", result=final)
    except Cancelled:
        job["state"] = "cancelled"
    except Exception as error:  # reported to the page, never raised
        job.update(state="failed", error=os_message(error) if isinstance(error, OSError) else str(error))
    finally:
        if job["state"] != "done":
            try:
                os.unlink(partial)
            except OSError:
                pass
        job["finished"] = time.time()


def export_start(args):
    source = absolute(args.get("path"))
    folder = export_target(args.get("destination"))
    if source == "/" or under(source, SPECIAL_ROOTS):
        raise Refused("This location cannot be copied.")
    try:
        info = os.stat(source)
    except OSError as error:
        raise Refused(os_message(error))
    is_folder = stat.S_ISDIR(info.st_mode)
    if not is_folder and not stat.S_ISREG(info.st_mode):
        raise Refused("Only regular files and folders can be copied.")
    if not os.access(source, os.R_OK):
        raise Refused(f"Permission denied: {source}")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as error:
        raise Refused(os_message(error))
    name = os.path.basename(source) + (".zip" if is_folder else "")
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id, "state": "running", "source": source, "name": name, "folder": folder,
        "is_folder": is_folder, "bytes_total": None if is_folder else info.st_size,
        "bytes_done": 0, "files_done": 0, "skipped": [], "skipped_count": 0, "started": time.time(),
        "partial": os.path.join(folder, f".{name}.{job_id[:8]}.partial"),
        "cancel": threading.Event(),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=run_export, args=(job,), daemon=True).start()
    return job_view(job)


def job_view(job):
    view = {key: job.get(key) for key in ("id", "state", "source", "name", "folder", "is_folder",
                                           "bytes_total", "bytes_done", "files_done", "result", "error")}
    view["skipped"] = job["skipped_count"]
    view["skipped_examples"] = list(job["skipped"])
    return view


def find_job(args):
    with JOBS_LOCK:
        job = JOBS.get(args.get("job"))
    if job is None:
        raise Refused("No such copy.")
    return job


def export_status(args):
    return job_view(find_job(args))


def export_cancel(args):
    job = find_job(args)
    job["cancel"].set()
    return job_view(job)


# ---------------------------------------------------------------- protocol

HANDLERS = {
    "list_directory": list_directory,
    "path_details": path_details,
    "places": places,
    "remember_destination": remember_destination,
    "export_start": export_start,
    "export_status": export_status,
    "export_cancel": export_cancel,
}


def call(name, args):
    handler = HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}
    try:
        result = handler(args if isinstance(args, dict) else {})
    except Refused as refusal:
        return {"content": [{"type": "text", "text": str(refusal)}], "isError": True}
    except (TypeError, ValueError) as error:
        return {"content": [{"type": "text", "text": f"Invalid arguments: {error}"}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(result)}], "structuredContent": result}


def answer(message):
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "linux-files", "version": VERSION}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return call(params.get("name"), params.get("arguments") or {})
    if method == "ping":
        return {}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if not isinstance(request, dict) or "id" not in request:
            continue  # notifications need no reply
        result = answer(request)
        reply = {"jsonrpc": "2.0", "id": request["id"]}
        if result is None:
            reply["error"] = {"code": -32601, "message": "method not found"}
        else:
            reply["result"] = result
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
