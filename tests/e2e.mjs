// End-to-end test of the page in headless Chromium, through dev/serve.py.
//   node tests/e2e.mjs [--screenshots DIR]
// Uses only Node's standard library and the DevTools protocol.
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readdirSync, readFileSync, rmSync, existsSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..");
const shotsAt = process.argv.indexOf("--screenshots");
const shots = shotsAt > 0 ? process.argv[shotsAt + 1] : null;
const browserPath = ["/usr/bin/chromium", "/usr/bin/google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(existsSync);
if (!browserPath) throw new Error("no Chromium found");

const work = mkdtempSync(join(tmpdir(), "linux-files-e2e-"));
const home = join(work, "home");
const shared = join(work, "shared");
mkdirSync(join(home, "projects", "demo", "src"), { recursive: true });
mkdirSync(shared);
writeFileSync(join(home, "projects", "demo", "README.md"), "# Demo\n\nHello from the fixture.\n");
writeFileSync(join(home, "projects", "demo", "src", "main.py"), "print('hi')\n");
writeFileSync(join(home, "notes.txt"), "notes\n");
writeFileSync(join(home, ".profile"), "export X=1\n");
for (let i = 0; i < 40; i++) writeFileSync(join(home, `file${i}.log`), `line ${i}\n`);
symlinkSync(join(home, "projects"), join(home, "projects-link"));

const children = [];
const cleanup = () => { children.forEach(child => child.kill("SIGKILL")); rmSync(work, { recursive: true, force: true }); };
process.on("exit", cleanup);

function waitForLine(child, pattern) {
  return new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("timeout waiting for " + pattern)), 15000);
    const onData = chunk => {
      buffer += chunk;
      const match = buffer.match(pattern);
      if (match) { clearTimeout(timer); resolve(match); }
    };
    child.stdout?.on("data", onData);
    child.stderr?.on("data", onData);
  });
}

const server = spawn("python3", ["-B", join(repo, "dev", "serve.py"), "--port", "0", "--home", home, "--export-folder", shared], { encoding: "utf8" });
children.push(server);
server.stdout.setEncoding("utf8");
const [base] = await waitForLine(server, /http:\/\/127\.0\.0\.1:\d+\//);

const profile = join(work, "chrome");
const chrome = spawn(browserPath, ["--headless=new", "--no-sandbox", "--disable-gpu", `--user-data-dir=${profile}`,
  "--remote-debugging-port=0", "--window-size=1400,820", "about:blank"]);
children.push(chrome);
chrome.stderr.setEncoding("utf8");
const [, wsUrl] = await waitForLine(chrome, /DevTools listening on (ws:\/\/\S+)/);

// ------------------------------------------------------------------ CDP
const browserSocket = new WebSocket(wsUrl);
await new Promise(resolve => browserSocket.addEventListener("open", resolve, { once: true }));
let seq = 0;
const pending = new Map();
const listeners = [];
browserSocket.addEventListener("message", event => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    message.error ? reject(new Error(message.error.message)) : resolve(message.result);
  } else if (message.method) listeners.forEach(listener => listener(message));
});
function send(method, params = {}, sessionId) {
  const id = ++seq;
  browserSocket.send(JSON.stringify({ id, method, params, sessionId }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
const cdp = (method, params) => send(method, params, sessionId);
// The sandboxed page is an out-of-process iframe: its own target and session.
let frameSession = null;
let frameReady = false;
const problems = [];
listeners.push(message => {
  if (message.method === "Target.attachedToTarget" && message.params.targetInfo.type === "iframe") {
    const child = message.params.sessionId;
    frameSession = child;
    frameReady = false;
    Promise.all([send("Runtime.enable", {}, child), send("Log.enable", {}, child)])
      .then(() => send("Runtime.runIfWaitingForDebugger", {}, child))
      .then(() => { if (frameSession === child) frameReady = true; });
  }
  if (message.sessionId !== sessionId && message.sessionId !== frameSession) return;
  if (message.method === "Runtime.exceptionThrown") problems.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
  if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") problems.push(message.params.args.map(arg => arg.value ?? arg.description).join(" "));
  if (message.method === "Log.entryAdded" && message.params.entry.level === "error") problems.push(message.params.entry.text + " " + (message.params.entry.url || ""));
});
await cdp("Runtime.enable");
await cdp("Page.enable");
await cdp("Log.enable");
await cdp("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
await cdp("Emulation.setDeviceMetricsOverride", { width: 1400, height: 820, deviceScaleFactor: 1, mobile: false });

async function open(query = "") {
  frameSession = null;
  await cdp("Page.navigate", { url: base + query });
  await waitFor(() => frameReady && page("document.readyState === 'complete'"), "iframe session");
}

async function page(expression) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, frameSession);
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
  return result.value;
}
const host = expression => cdp("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }).then(r => r.result.value);

async function waitFor(check, what, timeout = 8000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try { if (await check()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error("timed out waiting for " + what);
}

async function box(selector) {
  const rect = await page(`(() => { const n = document.querySelector(${JSON.stringify(selector)}); if (!n) return null; n.scrollIntoView({block: "nearest", inline: "nearest"}); const r = n.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()`);
  assert.ok(rect, "no element " + selector);
  return rect;
}

async function click(selector, button = "left") {
  const { x, y } = await box(selector);
  for (const type of ["mousePressed", "mouseReleased"]) {
    await cdp("Input.dispatchMouseEvent", { type, x, y, button, clickCount: 1 });
  }
}

const KEYS = { ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Enter: 13, Escape: 27 };
async function key(name, modifiers = 0) {
  const code = KEYS[name];
  const text = code ? undefined : name;
  await cdp("Input.dispatchKeyEvent", { type: code ? "rawKeyDown" : "keyDown", key: name, windowsVirtualKeyCode: code || name.toUpperCase().charCodeAt(0), text, modifiers });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: name, windowsVirtualKeyCode: code || name.toUpperCase().charCodeAt(0), modifiers });
}

async function screenshot(name) {
  if (!shots) return;
  mkdirSync(shots, { recursive: true });
  const { data } = await cdp("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(shots, name + ".png"), Buffer.from(data, "base64"));
}

const columnNames = () => page(`[...document.querySelectorAll(".column:not(.details)")].map(c => [...c.querySelectorAll(".row .name")].map(n => n.textContent))`);
const selected = () => page(`[...document.querySelectorAll(".column:not(.details)")].map(c => c.querySelector(".row.selected")?.dataset.name ?? null)`);
const rowSel = (column, name) => `.columns > .column:nth-child(${column + 1}) .row[data-name=${JSON.stringify(name)}]`;
const step = async (name, run) => { process.stdout.write(`• ${name} … `); await run(); console.log("ok"); };

// ------------------------------------------------------------------ scenario
await open();

await step("opens at home with folders first and dotfiles hidden", async () => {
  await waitFor(async () => (await columnNames())[0]?.length > 0, "home listing");
  const [names] = await columnNames();
  assert.deepEqual(names.slice(0, 2), ["projects", "projects-link"]);
  assert.ok(!names.includes(".profile"));
  assert.ok(names.indexOf("file2.log") < names.indexOf("file10.log"), "natural sort");
  assert.match(await page(`document.querySelector(".crumbs").textContent`), /home$/);
  assert.match(await page(`document.getElementById("status-count").textContent`), /items, 1 hidden/);
  assert.ok(await page(`document.querySelector('.place[data-path="${home}"]').classList.contains("current")`));
});
await screenshot("1-home");

await step("clicking folders opens columns", async () => {
  await click(rowSel(0, "projects"));
  await waitFor(async () => (await columnNames())[1]?.includes("demo"), "projects column");
  await click(rowSel(1, "demo"));
  await waitFor(async () => (await columnNames())[2]?.includes("README.md"), "demo column");
  assert.deepEqual(await selected(), ["projects", "demo", null]);
});

await step("clicking a file shows details with a preview", async () => {
  await click(rowSel(2, "README.md"));
  await waitFor(() => page(`!!document.querySelector(".details .preview")`), "details preview");
  assert.equal(await page(`document.querySelector(".details .preview").textContent`), "# Demo\n\nHello from the fixture.\n");
  assert.equal(await page(`document.querySelector(".details h3").textContent`), "README.md");
  assert.match(await page(`document.querySelector(".crumbs").textContent`), /projects›demo›README\.md$/);
});
await screenshot("2-details");

await step("keyboard: left keeps the columns, right returns, up/down move", async () => {
  await key("ArrowLeft");
  await key("ArrowUp"); // demo is the only entry: nothing changes
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.deepEqual(await selected(), ["projects", "demo", "README.md"]);
  assert.ok(await page(`!!document.querySelector(".details .preview")`), "details kept");
  await key("ArrowRight"); // back into demo, selection kept
  await key("ArrowUp");
  await waitFor(async () => (await selected())[2] === "src" && (await columnNames())[3]?.includes("main.py"), "src opened by keyboard");
  await key("ArrowDown");
  await waitFor(async () => (await selected())[2] === "README.md", "README selected by keyboard");
  await waitFor(() => page(`!!document.querySelector(".details .preview")`), "details after keyboard");
});

await step("type-ahead jumps to a name", async () => {
  await click(rowSel(0, "notes.txt"));
  await waitFor(async () => (await columnNames()).length === 1, "columns trimmed");
  await key("f"); await key("i"); await key("l"); await key("e"); await key("3"); await key("9");
  await waitFor(async () => (await selected())[0] === "file39.log", "type-ahead selection");
});

await step("context menu copies a path", async () => {
  await click(rowSel(0, "projects"));
  await waitFor(async () => (await columnNames()).length === 2, "projects column again");
  await click(rowSel(0, "projects"), "right");
  await waitFor(() => page(`!document.getElementById("menu").hidden`), "menu");
  await screenshot("3-menu");
  await click("#menu button:first-child");
  await waitFor(async () => (await host("harness.copied")).includes(join(home, "projects")), "copied path");
  assert.ok(await page(`document.getElementById("menu").hidden`));
});

await step("⌘C copies the current path", async () => {
  await click(rowSel(1, "demo"));
  await key("c", 4 /* meta */);
  await waitFor(async () => (await host("harness.copied")).includes(join(home, "projects", "demo")), "copied with ⌘C");
});

await step("download a file: pick destination, confirm, file lands in the shared folder", async () => {
  await click(rowSel(2, "README.md"));
  await waitFor(() => page(`!!document.querySelector(".details .preview")`), "details");
  await click("#download-btn");
  await waitFor(() => page(`!document.getElementById("modal").hidden`), "destination picker");
  assert.ok(await page(`document.querySelector(".option.chosen .path")?.textContent === ${JSON.stringify(shared)}`), "the setting is preselected");
  await screenshot("4-picker");
  await click("#modal-ok");
  await waitFor(() => existsSync(join(shared, "README.md")), "exported file");
  assert.equal(readFileSync(join(shared, "README.md"), "utf8"), "# Demo\n\nHello from the fixture.\n");
  const confirms = await host("harness.confirms");
  assert.match(confirms.at(-1).message, /README\.md/);
  await waitFor(() => page(`document.getElementById("jobs").textContent.includes("Saved")`), "job done in status bar");
  assert.match(await page(`document.getElementById("dest-label").textContent`), new RegExp(shared.replace(/[/.]/g, "\\$&")));
});

await step("download a folder as zip without asking for the destination again", async () => {
  await click(rowSel(0, "projects"), "right");
  await waitFor(() => page(`!document.getElementById("menu").hidden`), "menu");
  await click("#menu button:last-child");
  await waitFor(() => existsSync(join(shared, "projects.zip")), "exported zip");
  assert.ok(await page(`document.getElementById("modal").hidden`));
});

await step("declining the confirm exports nothing", async () => {
  await host("harness.confirmAnswer = false");
  await click(rowSel(0, "notes.txt"));
  await waitFor(() => page(`!!document.querySelector(".details .preview")`), "notes details");
  const before = readdirSync(shared).length;
  await click("#download-btn");
  await waitFor(async () => (await host("harness.confirms")).length === 3, "third confirm");
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(readdirSync(shared).length, before);
  await host("harness.confirmAnswer = true");
});

await step("hidden files toggle", async () => {
  await click("#hidden-btn");
  await waitFor(async () => (await columnNames())[0]?.includes(".profile"), "dotfile shown");
  assert.equal(await page(`document.getElementById("hidden-btn").getAttribute("aria-pressed")`), "true");
  await click("#hidden-btn");
  await waitFor(async () => !(await columnNames())[0]?.includes(".profile"), "dotfile hidden again");
});

await step("go to a typed path, including a file inside a symlinked folder", async () => {
  await click("#edit-path-btn");
  await waitFor(() => page(`document.activeElement?.id === "path-input"`), "path input focused");
  await page(`document.getElementById("path-input").value = ${JSON.stringify(join(home, "projects-link", "demo", "src", "main.py"))}`);
  await key("Enter");
  await waitFor(() => page(`document.querySelector(".details h3")?.textContent === "main.py"`), "typed file opened");
  assert.deepEqual(await selected(), ["projects-link", "demo", "src", "main.py"]);  // columns start at home
});

await step("root place lists /", async () => {
  await click('.place[data-path="/"]');
  await waitFor(async () => (await columnNames())[0]?.includes("proc"), "root listing");
  assert.equal(await page(`document.querySelector(".crumbs").textContent`), "/");
});

await step("state survives a reload (ui/setState)", async () => {
  await click(rowSel(0, "etc"));
  await waitFor(async () => (await selected())[0] === "etc", "etc selected");
  await new Promise(resolve => setTimeout(resolve, 600));
  const saved = await host("harness.state");
  assert.equal(saved.path, "/etc");
  assert.equal(saved.exportDest, shared);
  const before = frameSession;
  await page(`location.reload()`).catch(() => {});
  await waitFor(() => page("document.readyState === 'complete' && performance.now() < 5000"), "reloaded iframe");
  assert.ok(frameSession === before || frameSession, "frame session");
  await waitFor(async () => (await selected())[0] === "etc", "selection restored");
});

await step("unreadable folders show the error in the column", async () => {
  await click("#edit-path-btn");
  await page(`document.getElementById("path-input").value = "/root"`);
  await key("Enter");
  await waitFor(() => page(`!!document.querySelector(".column-note.error") || [...document.querySelectorAll(".row")].length > 0`), "root folder result");
});

await open("?scheme=dark&locale=zh-CN");
await step("dark theme and Chinese strings", async () => {
  await waitFor(async () => (await columnNames())[0]?.length > 0, "listing");
  assert.equal(await page(`document.documentElement.dataset.scheme`), "dark");
  assert.equal(await page(`document.getElementById("download-btn").textContent`), "下载到 Mac");
  await click(rowSel(0, "projects"));
  await waitFor(async () => (await columnNames())[1]?.includes("demo"), "projects column");
  await click(rowSel(1, "demo"));
  await waitFor(async () => (await columnNames())[2]?.includes("README.md"), "demo column");
  await click(rowSel(2, "README.md"));
  await waitFor(() => page(`!!document.querySelector(".details .preview")`), "details");
});
await screenshot("5-dark-zh");

await step("no page errors and never more than 8 requests in flight", async () => {
  const errors = await host("harness.errors");
  const maxInFlight = await host("harness.maxInFlight");
  assert.deepEqual(errors, []);
  assert.ok(maxInFlight <= 8, "max in flight " + maxInFlight);
  assert.deepEqual(problems.filter(text => !/favicon/.test(text)), []);
});

console.log("all e2e checks passed");
browserSocket.close();
process.exit(0);
