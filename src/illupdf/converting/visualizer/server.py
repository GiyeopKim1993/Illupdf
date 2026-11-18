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
  --accent2: #fa6d8a;
  --green: #4ade80;
  --amber: #fbbf24;
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
canvas { width: min(100%, 960px); height: min(100vh - 48px, 720px); background: #12141e; border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 0 20px rgba(0,0,0,0.5); }

.sb-header { padding: 18px 20px 12px; border-bottom: 1px solid var(--border); }
.sb-header h1 { font-size: 15px; font-weight: 700; letter-spacing: .02em; color: var(--text); margin: 0; }
.sb-header .sub { font-size: 11px; color: var(--muted); margin-top: 2px; }

.sb-section { padding: 14px 20px; border-bottom: 1px solid var(--border); }
.sb-section .label { font-size: 10px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; }

p, pre, button { font-size: 12px; }
pre { white-space: pre-wrap; word-break: break-word; background: var(--bg); padding: 8px; border-radius: 6px; border: 1px solid var(--border); color: var(--text); font-size: 11px; }

.row { margin-bottom: 8px; font-size: 12px; display: flex; justify-content: space-between; align-items: center; }
.row strong { color: var(--muted); font-weight: 500; }
.row span { font-weight: 600; text-align: right; }

.controls { display: flex; gap: 6px; margin: 12px 0; flex-wrap: wrap; }
button { 
  background: var(--border); border: none; color: var(--text); padding: 6px 10px; 
  border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 500; transition: all 0.2s;
}
button:hover { background: #3a3d4a; }
button.active { background: var(--accent); color: #fff; }
button:disabled { opacity: 0.4; cursor: default; }

.pill { 
  display: inline-block; padding: 3px 10px; border-radius: 99px; font-size: 10px; font-weight: 600; 
  background: rgba(124,109,250,.15); color: var(--accent); border: 1px solid rgba(124,109,250,.3); margin-right: 4px; margin-bottom: 8px;
}
#status { background: rgba(74,222,128,.15); color: var(--green); border-color: rgba(74,222,128,.3); }

.hint { color: var(--muted); font-size: 11px; margin-top: 6px; line-height: 1.4; }
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sb-header">
      <h1>illupdf Visualizer</h1>
      <div class="sub">Keyring Output Generator</div>
    </div>
    
    <div class="sb-section">
      <div class="label">Status</div>
      <div class="meta">
        <span class="pill" id="stage">stage</span>
        <span class="pill" id="status">status</span>
        <span class="pill" id="iteration">iter</span>
      </div>
      <div class="row"><strong>Command</strong></div>
      <pre id="command"></pre>
    </div>

    <div class="sb-section">
      <div class="label">Controls</div>
      <div class="controls">
        <button id="all-frames">All Frames</button>
        <button id="balance-steps">Balance Steps</button>
      </div>
      <div class="controls">
        <button id="first">First</button>
        <button id="prev">Prev</button>
        <button id="live">Live</button>
        <button id="next">Next</button>
        <button id="last">Last</button>
      </div>
      <div class="hint">Arrow keys move step-by-step. Home/End jump to the first or last visible step.</div>
    </div>

    <div class="sb-section">
      <div class="label">Frame Details</div>
      <div class="row"><strong>View</strong><span id="view">all frames</span></div>
      <div class="row"><strong>Step</strong><span id="step">-</span></div>
      <div class="row"><strong>Frames</strong><span id="frames">0</span></div>
      <div class="row"><strong>Preset</strong><span id="preset">-</span></div>
      <div class="row"><strong>Theta (rad)</strong><span id="theta">-</span></div>
    </div>

    <div class="sb-section">
      <div class="label">Geometry Stats</div>
      <div class="row"><strong>Boss Center</strong><span id="boss-center">-</span></div>
      <div class="row"><strong>Outer Centroid</strong><span id="outer-centroid" style="color:#e07b00">-</span></div>
      <div class="row"><strong>Final Centroid</strong><span id="centroid">-</span></div>
      <div class="row"><strong>Centroid Shift</strong><span id="centroid-shift">-</span></div>
      <div class="row"><strong>Delta X</strong><span id="delta-x">-</span></div>
      <div class="row"><strong>BBox Size</strong><span id="bbox-size">-</span></div>
      <div class="row"><strong>BBox Area</strong><span id="bbox-area">-</span></div>
      <div class="row"><strong>Outline Area</strong><span id="outline-area">-</span></div>
    </div>

    <div class="sb-section" style="flex:1;">
      <div class="label">Logs & Messages</div>
      <div class="row"><strong>Message</strong></div>
      <pre id="message"></pre>
      <div class="row"><strong>Outputs</strong></div>
      <pre id="outputs">pending...</pre>
      <div class="row"><strong>Error</strong></div>
      <pre id="error" style="color: var(--red); border-color: rgba(248,113,113,0.3); background: rgba(248,113,113,0.05);">none</pre>
    </div>
  </aside>
  <main class="canvas-wrap">
    <canvas id="viewport" width="1280" height="900"></canvas>
  </main>
</div>
<script>
const canvas = document.getElementById("viewport");
const ctx = canvas.getContext("2d");
const commandEl = document.getElementById("command");
const stageEl = document.getElementById("stage");
const statusEl = document.getElementById("status");
const iterationEl = document.getElementById("iteration");
const viewEl = document.getElementById("view");
const stepEl = document.getElementById("step");
const thetaEl = document.getElementById("theta");
const presetEl = document.getElementById("preset");
const framesEl = document.getElementById("frames");
const bossCenterEl = document.getElementById("boss-center");
const outerCentroidEl = document.getElementById("outer-centroid");
const centroidEl = document.getElementById("centroid");
const centroidShiftEl = document.getElementById("centroid-shift");
const deltaXEl = document.getElementById("delta-x");
const bboxSizeEl = document.getElementById("bbox-size");
const bboxAreaEl = document.getElementById("bbox-area");
const outlineAreaEl = document.getElementById("outline-area");
const outputsEl = document.getElementById("outputs");
const messageEl = document.getElementById("message");
const errorEl = document.getElementById("error");
const allFramesButton = document.getElementById("all-frames");
const balanceStepsButton = document.getElementById("balance-steps");
const firstButton = document.getElementById("first");
const prevButton = document.getElementById("prev");
const liveButton = document.getElementById("live");
const nextButton = document.getElementById("next");
const lastButton = document.getElementById("last");

let frames = [];
let currentIndex = 0;
let liveMode = true;
let viewMode = null;

function formatPoint(point) {
  if (!point) return "-";
  return `${point[0].toFixed(3)}, ${point[1].toFixed(3)}`;
}

function formatDeltaX(frame) {
  if (!frame || !frame.boss_center_pt || !frame.centroid_pt) return "-";
  return (frame.centroid_pt[0] - frame.boss_center_pt[0]).toFixed(3);
}

function isBalanceFrame(frame) {
  return Boolean(frame && frame.is_balance_step);
}

function hasBalanceFrames() {
  return frames.some((frame) => isBalanceFrame(frame));
}

function getVisibleIndices() {
  const visible = [];
  frames.forEach((frame, index) => {
    if (viewMode === "balance") {
      if (isBalanceFrame(frame)) {
        visible.push(index);
      }
      return;
    }
    visible.push(index);
  });
  return visible;
}

function nearestVisibleIndex(visible, index) {
  if (!visible.length) return 0;
  let nearest = visible[0];
  let distance = Math.abs(index - nearest);
  visible.forEach((candidate) => {
    const candidateDistance = Math.abs(index - candidate);
    if (candidateDistance < distance || (candidateDistance === distance && candidate < nearest)) {
      nearest = candidate;
      distance = candidateDistance;
    }
  });
  return nearest;
}

function clampCurrentIndexToVisible() {
  const visible = getVisibleIndices();
  if (!visible.length) {
    currentIndex = 0;
    return visible;
  }
  if (liveMode) {
    currentIndex = visible[visible.length - 1];
    return visible;
  }
  if (!visible.includes(currentIndex)) {
    currentIndex = nearestVisibleIndex(visible, currentIndex);
  }
  return visible;
}

function updateButtons(visible, position) {
  const hasFrames = visible.length > 0;
  const hasBalance = hasBalanceFrames();
  allFramesButton.disabled = !frames.length;
  balanceStepsButton.disabled = !hasBalance;
  allFramesButton.classList.toggle("active", viewMode !== "balance");
  balanceStepsButton.classList.toggle("active", viewMode === "balance");
  liveButton.classList.toggle("active", liveMode);
  firstButton.disabled = !hasFrames || position <= 0;
  prevButton.disabled = !hasFrames || position <= 0;
  nextButton.disabled = !hasFrames || position >= visible.length - 1;
  lastButton.disabled = !hasFrames || position >= visible.length - 1;
}

function drawPath(path, color, width, dash) {
  if (!path || path.length < 2) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dash || []);
  ctx.beginPath();
  ctx.moveTo(...project(path[0]));
  for (let i = 1; i < path.length; i += 1) {
    ctx.lineTo(...project(path[i]));
  }
  ctx.closePath();
  ctx.stroke();
  ctx.restore();
}

let currentProject = (point) => point;
function project(point) {
  return currentProject(point);
}

function configureProjection(bbox) {
  const padding = 40;
  const [minX, minY, maxX, maxY] = bbox;
  const width = Math.max(1, maxX - minX);
  const height = Math.max(1, maxY - minY);
  const scale = Math.min((canvas.width - padding * 2) / width, (canvas.height - padding * 2) / height);
  currentProject = (point) => {
    const x = padding + (point[0] - minX) * scale;
    const y = canvas.height - padding - (point[1] - minY) * scale;
    return [x, y];
  };
}

function drawPoint(point, color, radius) {
  if (!point) return;
  const [x, y] = project(point);
  ctx.save();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawLine(line, color, width, dash) {
  if (!line) return;
  const [start, end] = line;
  const [x1, y1] = project(start);
  const [x2, y2] = project(end);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dash || []);
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.restore();
}

const imageCache = new Map();

function drawRaster(frame) {
  if (!frame.raster_b64 || !frame.raster_rect_pt) return;
  const rect = frame.raster_rect_pt;
  const [sx1, sy_max] = project([rect[0], rect[1]]);
  const [sx2, sy_min] = project([rect[0] + rect[2], rect[1] + rect[3]]);
  const sw = sx2 - sx1;
  const sh = sy_max - sy_min;

  const draw = (img) => {
    ctx.save();
    // Use semi-transparent if it's not the final stage to see underlying geometry
    if (frame.stage !== "final") ctx.globalAlpha = 0.8;
    ctx.drawImage(img, sx1, sy_min, sw, sh);
    ctx.restore();
  };

  if (imageCache.has(frame.raster_b64)) {
    draw(imageCache.get(frame.raster_b64));
  } else {
    const img = new Image();
    img.onload = () => {
      imageCache.set(frame.raster_b64, img);
      renderFrame();
    };
    img.src = "data:image/png;base64," + frame.raster_b64;
  }
}

function drawBackground() {
  const size = 20;
  ctx.save();
  ctx.fillStyle = "#12141e";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "rgba(255,255,255,0.02)";
  for (let y = 0; y < canvas.height; y += size) {
    for (let x = (y / size) % 2 === 0 ? 0 : size; x < canvas.width; x += size * 2) {
      ctx.fillRect(x, y, size, size);
    }
  }
  ctx.restore();
}

function renderFrame() {
  drawBackground();
  const visible = clampCurrentIndexToVisible();
  if (!visible.length) {
    stageEl.textContent = "stage";
    statusEl.textContent = "pending";
    iterationEl.textContent = "iter -";
    viewEl.textContent = viewMode === "balance" ? "balance steps" : "all frames";
    stepEl.textContent = "-";
    thetaEl.textContent = "-";
    presetEl.textContent = "-";
    framesEl.textContent = "0";
    bossCenterEl.textContent = "-";
    outerCentroidEl.textContent = "-";
    centroidEl.textContent = "-";
    centroidShiftEl.textContent = `Δx=${dxMm}mm Δy=${dyMm}mm`;
    deltaXEl.textContent = "-";
    bboxSizeEl.textContent = "-";
    bboxAreaEl.textContent = "-";
    outlineAreaEl.textContent = "-";
    messageEl.textContent = "";
    updateButtons([], 0);
    return;
  }
  const clampedIndex = Math.max(0, Math.min(currentIndex, frames.length - 1));
  const frame = frames[clampedIndex];
  const visiblePosition = Math.max(0, visible.indexOf(clampedIndex));
  const bbox = frame.bbox_pt || [0, 0, 100, 100];
  configureProjection(bbox);

  drawRaster(frame);
  drawLine(frame.vertical_line_pt, "#3366cc", 2, [8, 8]);
  drawLine(frame.centroid_line_pt, "#111111", 2, [12, 6]);
  (frame.outer_paths_pt || []).forEach((path) => drawPath(path, "#d01919", 2.5, []));
  (frame.hole_paths_pt || []).forEach((path) => drawPath(path, "#1b7f1b", 2, [6, 6]));
  (frame.boss_paths_pt || []).forEach((path) => drawPath(path, "#ff8a00", 1.5, [4, 6]));
  drawPoint(frame.boss_center_pt, "#3366cc", 6);
  // outer_centroid_pt: 구멍 기준 외곽선 도형의 중심점
  if (frame.outer_centroid_pt) {
    const [ox, oy] = project(frame.outer_centroid_pt);
    ctx.save();
    ctx.strokeStyle = "#e07b00";
    ctx.lineWidth = 2.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.arc(ox, oy, 8, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }
  drawPoint(frame.centroid_pt, "#111111", 6);  // 최종 형상(외곽 − 구멍)의 중심점 (hole 기준)

  stageEl.textContent = frame.stage || "stage";
  statusEl.textContent = frame.status || "running";
  iterationEl.textContent = frame.iteration == null ? "iter -" : `iter ${frame.iteration}`;
  viewEl.textContent = viewMode === "balance" ? "balance steps" : "all frames";
  stepEl.textContent = `${visiblePosition + 1} / ${visible.length}`;
  thetaEl.textContent = frame.theta_rad == null ? "-" : frame.theta_rad.toFixed(6);
  presetEl.textContent = frame.preset_name || "-";
  bossCenterEl.textContent = formatPoint(frame.boss_center_pt);
  outerCentroidEl.textContent = formatPoint(frame.outer_centroid_pt);
  centroidEl.textContent = formatPoint(frame.centroid_pt);
  
  // Centroid shift: 외곽선 중심 → 최종 형상 중심 (hole 기준 이동량)
  if (frame.outer_centroid_pt && frame.centroid_pt) {
    const dx = frame.centroid_pt[0] - frame.outer_centroid_pt[0];
    const dy = frame.centroid_pt[1] - frame.outer_centroid_pt[1];
    // pt → mm 변환 (72pt = 25.4mm)
    const mmPerPt = 25.4 / 72;
    const dxMm = (dx * mmPerPt).toFixed(3);
    const dyMm = (dy * mmPerPt).toFixed(3);
    centroidShiftEl.textContent = `?x=${dxMm}mm ?y=${dyMm}mm`;
  } else {
    centroidShiftEl.textContent = "-";
  }
  
  deltaXEl.textContent = formatDeltaX(frame);
  
  if (frame.bbox_width_mm != null && frame.bbox_height_mm != null) {
    bboxSizeEl.textContent = `${frame.bbox_width_mm.toFixed(2)} x ${frame.bbox_height_mm.toFixed(2)} mm`;
  } else {
    bboxSizeEl.textContent = "-";
  }
  bboxAreaEl.textContent = frame.bbox_area_mm2 == null ? "-" : `${frame.bbox_area_mm2.toFixed(2)} mm²`;
  outlineAreaEl.textContent = frame.outline_area_mm2 == null ? "-" : `${frame.outline_area_mm2.toFixed(2)} mm²`;
  messageEl.textContent = frame.message || "";
  framesEl.textContent = `${clampedIndex + 1} / ${frames.length}`;
  updateButtons(visible, visiblePosition);
}

function moveToVisible(delta) {
  const visible = clampCurrentIndexToVisible();
  if (!visible.length) return;
  liveMode = false;
  const currentPosition = Math.max(0, visible.indexOf(currentIndex));
  const nextPosition = Math.max(0, Math.min(visible.length - 1, currentPosition + delta));
  currentIndex = visible[nextPosition];
  renderFrame();
}

function jumpToEdge(target) {
  const visible = clampCurrentIndexToVisible();
  if (!visible.length) return;
  liveMode = false;
  currentIndex = target === "first" ? visible[0] : visible[visible.length - 1];
  renderFrame();
}

function setViewMode(mode) {
  if (mode === "balance" && !hasBalanceFrames()) {
    return;
  }
  viewMode = mode;
  if (liveMode) {
    const visible = clampCurrentIndexToVisible();
    if (visible.length) {
      currentIndex = visible[visible.length - 1];
    }
  }
  renderFrame();
}

function syncInitialViewMode() {
  if (viewMode === null) {
    viewMode = hasBalanceFrames() ? "balance" : "all";
    return;
  }
  if (viewMode === "balance" && !hasBalanceFrames()) {
    viewMode = "all";
  }
}

function handleKeyboard(event) {
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    moveToVisible(-1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    moveToVisible(1);
  } else if (event.key === "Home") {
    event.preventDefault();
    jumpToEdge("first");
  } else if (event.key === "End") {
    event.preventDefault();
    jumpToEdge("last");
  } else if (event.key === " ") {
    event.preventDefault();
    liveMode = true;
    const visible = clampCurrentIndexToVisible();
    if (visible.length) {
      currentIndex = visible[visible.length - 1];
    }
    renderFrame();
  }
}

async function pollState() {
  try {
    const response = await fetch("/state");
    const state = await response.json();
    commandEl.textContent = state.command;
    outputsEl.textContent = state.outputs_text;
    errorEl.textContent = state.error || "none";
    frames = state.frames || [];
    syncInitialViewMode();
    if (liveMode) {
      const visible = clampCurrentIndexToVisible();
      if (visible.length) {
        currentIndex = visible[visible.length - 1];
      }
    } else if (currentIndex >= frames.length) {
      currentIndex = Math.max(0, frames.length - 1);
    }
    renderFrame();
  } catch (error) {
    errorEl.textContent = String(error);
  }
}

allFramesButton.addEventListener("click", () => setViewMode("all"));
balanceStepsButton.addEventListener("click", () => setViewMode("balance"));
firstButton.addEventListener("click", () => jumpToEdge("first"));
prevButton.addEventListener("click", () => moveToVisible(-1));
nextButton.addEventListener("click", () => moveToVisible(1));
lastButton.addEventListener("click", () => jumpToEdge("last"));

liveButton.addEventListener("click", () => {
  liveMode = true;
  const visible = clampCurrentIndexToVisible();
  if (visible.length) {
    currentIndex = visible[visible.length - 1];
  }
  renderFrame();
});

document.addEventListener("keydown", handleKeyboard);

setInterval(pollState, 100);
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
