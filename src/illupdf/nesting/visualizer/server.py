from __future__ import annotations
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from typing import Any
from .state import VisualizerState

HTML_PAGE = """<!doctype html>
<html lang="en">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0f1117;
  --panel: #1a1d27;
  --border: #2a2d3a;
  --accent: #7c6dfa;
  --green: #4ade80;
  --red: #f87171;
  --text: #e8e8f0;
  --muted: #6b7280;
  --canvas-bg: #12141e;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; }

.layout { display: grid; grid-template-columns: 320px 1fr; min-height: 100vh; width: 100%; }
.sidebar { 
  padding: 0; 
  background: var(--panel); 
  border-right: 1px solid var(--border); 
  box-sizing: border-box; 
  display: flex; flex-direction: column; overflow-y: auto;
}
.canvas-wrap { display: flex; align-items: center; justify-content: center; padding: 24px; background: var(--canvas-bg); }

.sb-header { padding: 18px 20px 12px; border-bottom: 1px solid var(--border); }
.sb-header h1 { font-size: 15px; font-weight: 700; letter-spacing: .02em; color: var(--text); margin: 0; }
.sb-header .sub { font-size: 11px; color: var(--muted); margin-top: 2px; }

.sb-section { padding: 14px 20px; border-bottom: 1px solid var(--border); }
.sb-section .label { font-size: 10px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; }

p, pre, button { font-size: 12px; }
pre { white-space: pre-wrap; word-break: break-word; background: var(--bg); padding: 8px; border-radius: 6px; border: 1px solid var(--border); color: var(--text); font-size: 11px; max-height: 250px; overflow-y: auto; }

.row { margin-bottom: 8px; font-size: 12px; display: flex; justify-content: space-between; align-items: center; }
.row strong { color: var(--muted); font-weight: 500; }
.row span { font-weight: 600; text-align: right; }

.pill { 
  display: inline-block; padding: 3px 10px; border-radius: 99px; font-size: 10px; font-weight: 600; 
  background: rgba(124,109,250,.15); color: var(--accent); border: 1px solid rgba(124,109,250,.3); margin-right: 4px; margin-bottom: 8px;
}
#status { background: rgba(74,222,128,.15); color: var(--green); border-color: rgba(74,222,128,.3); }
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sb-header">
      <h1>illupdf Nesting Visualizer</h1>
      <div class="sub">Nesting Layout Generator</div>
    </div>
    
    <div class="sb-section">
      <div class="label">Status</div>
      <div class="meta">
        <span class="pill" id="stage">Idle</span>
        <span class="pill" id="status">Waiting</span>
      </div>
      <div class="row"><strong>Command</strong></div>
      <pre id="command">-</pre>
    </div>

    <div class="sb-section" style="flex:1;">
      <div class="label">Logs & Messages</div>
      <div class="row"><strong>Console Output</strong></div>
      <pre id="message"></pre>
      <div class="row"><strong>Error Logs</strong></div>
      <pre id="error" style="color: var(--red); border-color: rgba(248,113,113,0.3); background: rgba(248,113,113,0.05);">none</pre>
    </div>
  </aside>
  <main class="canvas-wrap">
    <div style="color: var(--muted); text-align: center; border: 1px dashed var(--border); padding: 50px; border-radius: 12px;">
       <h2 style="color: var(--text); margin-bottom: 10px;">Nesting Rendering Stream</h2>
       <p>The Nesting engine currently runs synchronously in the background.<br>Live canvas rendering will be supported in a future update.<br><strong>Please monitor the Console Output log on the left.</strong></p>
    </div>
  </main>
</div>
<script>
const commandEl = document.getElementById("command");
const messageEl = document.getElementById("message");
const errorEl = document.getElementById("error");
const stageEl = document.getElementById("stage");
const statusEl = document.getElementById("status");

async function pollState() {
  try {
    const response = await fetch("/state");
    const state = await response.json();
    commandEl.textContent = state.command || "-";
    
    // Attempt to extract stdout lines
    let logText = "";
    if (state.frames && state.frames.length > 0) {
      const lastFrame = state.frames[state.frames.length - 1];
      if (lastFrame.message) {
        logText += lastFrame.message + "\\n";
      }
    }
    if (state.outputs_text) {
      logText += state.outputs_text;
    }
    
    messageEl.textContent = logText || "No logs yet...";
    errorEl.textContent = state.error || "none";
    
    if (logText.includes("[Nesting]") || logText.includes("Running")) {
      stageEl.textContent = "Processing";
      statusEl.textContent = "Active";
    }
  } catch (error) {
    errorEl.textContent = String(error);
  }
}

setInterval(pollState, 500);
pollState();
</script>
</body>
</html>
"""

def make_handler(state: VisualizerState) -> type[BaseHTTPRequestHandler]:
    class VisualizerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"", "/"}:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML_PAGE.encode("utf-8"))
                return
            if parsed.path == "/state":
                payload = json.dumps(state.snapshot()).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    return VisualizerHandler
