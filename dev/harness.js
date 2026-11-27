// Stand-in for the Cage workbench side of the plugin page protocol
// (docs/plugins.md, "页面"): JSON-RPC 2.0 over postMessage, the same method
// names, limits and error codes. Tool calls go to the real MCP server.
"use strict";

const params = new URLSearchParams(location.search);
const frame = document.getElementById("view");
const logBox = document.getElementById("log");
const DARK = { paper: "#1d1f22", ink: "#e6e8eb", line: "#33373c", chrome: "#25282c", accent: "#6ea2ff" };

const harness = window.harness = {
  copied: [], notes: [], confirms: [], state: null, errors: [],
  inFlight: 0, maxInFlight: 0, confirmAnswer: params.get("confirm") !== "no",
};

function log(text) {
  logBox.textContent = `${new Date().toLocaleTimeString()} ${text}\n` + logBox.textContent.slice(0, 20000);
}

let initialized = false;

async function handle(method, p) {
  if (method !== "ui/initialize" && !initialized) throw { code: -32002, message: "not initialized" };
  switch (method) {
    case "ui/initialize":
      initialized = true;
      return {
        protocolVersion: 1, plugin: "linux-files", view: "browser", presentation: "page",
        theme: params.get("scheme") === "dark" ? { scheme: "dark", tokens: DARK } : { scheme: "light", tokens: {} },
        locale: params.get("locale") || "en", state: harness.state,
      };
    case "tools/list":
    case "tools/call": {
      const response = await fetch("/rpc", { method: "POST", body: JSON.stringify({ method, params: p }) });
      const reply = await response.json();
      if (reply.error) throw { code: -32005, message: reply.error.message };
      return reply.result;
    }
    case "ui/copy":
      if (typeof p.text !== "string" || p.text.length > 65536) throw { code: -32602, message: "bad text" };
      harness.copied.push(p.text);
      log("copy: " + p.text);
      return {};
    case "ui/notify":
      if (String(p.message).length > 500) throw { code: -32002, message: "too long" };
      harness.notes.push(p);
      log(`notify (${p.tone || "info"}): ${p.message}`);
      return {};
    case "ui/confirm":
      if (String(p.title).length > 200 || String(p.message).length > 2000 || String(p.confirmLabel || "").length > 40) {
        throw { code: -32002, message: "confirm text too long" };
      }
      harness.confirms.push(p);
      log(`confirm → ${harness.confirmAnswer}: ${p.message}`);
      return { confirmed: harness.confirmAnswer };
    case "ui/setState":
      if (JSON.stringify(p.state).length > 65536) throw { code: -32002, message: "state too large" };
      harness.state = p.state;
      return {};
    case "ui/openUrl":
      throw { code: -32002, message: "no openUrls declared" };
    default:
      throw { code: -32601, message: "no such method" };
  }
}

window.addEventListener("message", async event => {
  if (event.source !== frame.contentWindow) return;
  const message = event.data;
  if (!message || message.jsonrpc !== "2.0" || message.id === undefined) return;
  const reply = { jsonrpc: "2.0", id: message.id };
  if (JSON.stringify(message).length > 1 << 20) {
    reply.error = { code: -32002, message: "message too large" };
  } else if (harness.inFlight >= 8) {
    harness.errors.push("more than 8 requests in flight");
    reply.error = { code: -32002, message: "too many requests" };
  } else {
    harness.inFlight++;
    harness.maxInFlight = Math.max(harness.maxInFlight, harness.inFlight);
    try {
      reply.result = await handle(message.method, message.params || {});
    } catch (error) {
      reply.error = { code: error.code || -32603, message: error.message || String(error) };
      if (reply.error.code !== -32001) log(`error ${message.method}: ${reply.error.message}`);
    } finally {
      harness.inFlight--;
    }
  }
  frame.contentWindow.postMessage(reply, "*");
});

window.setScheme = scheme => {
  frame.contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/themeChanged",
    params: scheme === "dark" ? { scheme: "dark", tokens: DARK } : { scheme: "light", tokens: {} } }, "*");
};
