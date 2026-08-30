import { checkOutput, formToProject } from "./settings.js";
import { audioFollowsPicture, estimateLabel, state } from "./state.js";
import { $, api, clamp, el, fmt, post, toast } from "./util.js";
import { miniTimeline } from "./views.js";

// ---------------------------------------------------------------- render page

export function renderSummary() {
  formToProject();
  const table = $("render-summary");
  const p = state.project;
  const missing = p.clips.filter((c) => c.missing).length;

  const rows = [
    ["Title", p.title],
    ["Clips", `${p.clips.length}${missing ? ` (${missing} missing)` : ""}`],
    ["Estimated length", estimateLabel()],
    ["Format", `${p.output.resolution} @ ${p.output.fps}fps`],
    ["Encoder", p.output.encoder + (p.output.quality === null || p.output.quality === undefined
      ? "" : `  (quality ${p.output.quality})`)],
    ["Fades", `${p.defaults.fade_in}s in · ${p.defaults.fade_out}s out`],
    ["Levelling", p.balance.enabled ? `on, ${p.balance.target_lufs} LUFS` : "off"],
    ["Audio joins", (() => {
      const offset = p.clips.filter((c, i) => i > 0 && !audioFollowsPicture(c));
      return offset.length ? `${offset.length} offset from the picture` : "follow the picture";
    })()],
    ["Output", p.output.file || "(not set)"],
  ];

  table.innerHTML = "";
  rows.forEach(([key, value]) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, key));
    tr.appendChild(el("td", null, value));
    table.appendChild(tr);
  });

  miniTimeline($("render-timeline"));
  $("render-out-file").textContent = p.output.file || "(not set)";
  checkOutput($("render-out-check"));
}

$("btn-render").addEventListener("click", async () => {
  formToProject();
  if (!state.project.clips.length) return toast("Add some clips first.", true);
  if (!state.project.output.file) return toast("Set an output file.", true);
  if (state.project.clips.some((c) => c.missing))
    return toast("Some clips are missing — remove them before rendering.", true);

  state.render = { log: [], count: 0, samples: [], hasGpu: false };
  $("render-log").textContent = "";
  // A new run starts blue again, whatever the last one ended as.
  $("progress-bar").classList.remove("done", "failed");
  $("progress-bar").style.width = "0%";
  try {
    await post("/api/render", { project: state.project });
    startPolling();
  } catch (err) {
    toast(err.message, true);
  }
});

$("btn-cancel").addEventListener("click", async () => {
  try {
    await post("/api/render/cancel", {});
  } catch (err) {
    toast(err.message, true);
  }
});

$("btn-reveal").addEventListener("click", async () => {
  // Prefer where the render actually wrote. That path is absolute, whereas the
  // form field is whatever was typed -- and a relative one leaves the file
  // manager guessing at a working directory it does not share.
  const target = state.render.output || state.project.output.file;
  if (!target) return toast("Set an output file first.", true);
  try {
    // The desktop shell can take the foreground; a backend on localhost cannot,
    // so the folder it opens lands behind the app window. Prefer the bridge.
    if (window.desktop && await window.desktop.showItemInFolder(target)) return;
    await post("/api/reveal", { path: target });
  } catch (err) {
    toast(err.message, true);
  }
});

function startPolling() {
  clearInterval(state.poll);
  state.poll = setInterval(pollRender, 700);
  pollRender();
}

// -- the pipeline, so the silent minutes before encoding are accounted for

const STAGE_MARK = {
  pending: "·", running: "▸", done: "✓", failed: "✕", skipped: "–",
};

export function drawStages(stages) {
  const list = $("stage-list");
  list.innerHTML = "";
  (stages || []).forEach((stage) => {
    const item = el("li", "pipeline-step " + stage.state);
    item.appendChild(el("span", "mark", STAGE_MARK[stage.state] || "·"));
    item.appendChild(el("span", "name", stage.label));
    item.appendChild(el("span", "detail", stage.detail || ""));
    item.appendChild(el("span", "secs", stage.seconds ? `${stage.seconds}s` : ""));
    list.appendChild(item);
  });
}

// -- the log, fetched incrementally so a chatty ffmpeg stays cheap to poll

export function appendLog(entries) {
  if (!entries.length) return;
  state.render.log.push(...entries);
  if (state.render.log.length > 4000) {
    state.render.log.splice(0, state.render.log.length - 4000);
  }
  drawLog();
}

export function drawLog() {
  const node = $("render-log");
  const showFfmpeg = $("log-ffmpeg").checked;
  const lines = state.render.log.filter((e) => showFfmpeg || e.src !== "ffmpeg");
  node.innerHTML = "";
  lines.forEach((entry) => {
    const line = el("div", "line " + entry.src);
    line.appendChild(el("span", "stamp", fmt(entry.t)));
    line.appendChild(el("span", "text", entry.text));
    node.appendChild(line);
  });
  node.scrollTop = node.scrollHeight;
  $("log-count").textContent = `${lines.length} line(s)`;
}

$("log-ffmpeg").addEventListener("change", drawLog);

// -- the utilisation graph: a small, secondary read on where the time goes

export function drawUtil() {
  const canvas = $("util-graph");
  const context = canvas.getContext("2d");
  const { width, height } = canvas;
  const samples = state.render.samples;
  context.clearRect(0, 0, width, height);

  const style = getComputedStyle(document.body);
  const line = style.getPropertyValue("--line").trim() || "#303643";
  const accent = style.getPropertyValue("--accent").trim() || "#5aa9ff";
  const good = style.getPropertyValue("--good").trim() || "#4ec98a";

  context.strokeStyle = line;
  context.lineWidth = 1;
  [0, 0.5, 1].forEach((f) => {
    const y = Math.round(height - f * (height - 2)) - 0.5;
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  });

  if (samples.length < 2) {
    $("util-now").textContent = "";
    return;
  }

  const span = Math.max(1, samples[samples.length - 1].t - samples[0].t);
  const x = (sample) => ((sample.t - samples[0].t) / span) * (width - 1);
  const y = (value) => height - 1 - (clamp(value, 0, 100) / 100) * (height - 3);

  const trace = (key, colour) => {
    const points = samples.filter((s) => s[key] !== null && s[key] !== undefined);
    if (points.length < 2) return false;
    context.strokeStyle = colour;
    context.lineWidth = 1.5;
    context.beginPath();
    points.forEach((sample, i) => {
      const px = x(sample), py = y(sample[key]);
      i ? context.lineTo(px, py) : context.moveTo(px, py);
    });
    context.stroke();
    return true;
  };

  const warn = style.getPropertyValue("--warn").trim() || "#ffb648";
  const hasCpu = trace("cpu", accent);
  const hasGpu = trace("gpu", good);
  const hasEnc = trace("enc", warn);

  const latest = (key) => {
    for (let i = samples.length - 1; i >= 0; i--) {
      if (samples[i][key] != null) return samples[i][key];
    }
    return null;
  };
  const bits = [];
  if (hasCpu) bits.push(`CPU ${Math.round(latest("cpu"))}%`);
  if (hasGpu) bits.push(`GPU ${Math.round(latest("gpu"))}%`);
  if (hasEnc) bits.push(`encoder ${Math.round(latest("enc"))}%`);
  $("util-now").textContent = bits.join("   ·   ");
  // The encoder is separate silicon from the shaders, so a hardware render can
  // sit at 100% encoder while "GPU" barely moves. Saying which is which stops
  // that reading as an idle card.
  $("util-note").textContent = state.render.hasGpu
    ? "CPU · GPU (graphics) · encoder (NVENC) — a hardware render loads the encoder, not the shaders."
    : "GPU load needs nvidia-smi on PATH; CPU only here.";
}

export async function pollRender() {
  let status;
  try {
    status = await api("/api/render?since=" + state.render.count);
  } catch {
    return;
  }

  const running = status.state === "probing" || status.state === "rendering";
  $("btn-render").disabled = running;
  $("btn-cancel").disabled = !running;
  const bar = $("progress-bar");
  bar.style.width = (status.progress.pct || 0) + "%";
  bar.classList.toggle("done", status.state === "done");
  bar.classList.toggle("failed", status.state === "error" || status.state === "cancelled");

  const p = status.progress;
  $("progress-text").textContent =
    status.state === "rendering"
      ? `${fmt(p.elapsed)} / ${fmt(p.total)}  (${Math.round(p.pct)}%)${p.speed ? "  @ " + p.speed : ""}`
      : status.state === "probing" ? "Analysing clips..."
      : status.state === "done" ? "Finished."
      : status.state === "error" ? "Failed."
      : status.state === "cancelled" ? "Cancelled."
      : "Idle";

  drawStages(status.stages);
  if (status.output) state.render.output = status.output;

  // The server hands back only what we have not seen; a reset job rewinds us.
  if (status.log_total < state.render.count) {
    state.render.log = [];
    state.render.count = 0;
  } else {
    state.render.count = status.log_total;
    appendLog(status.log || []);
  }

  state.render.samples = status.samples || [];
  state.render.hasGpu = !!status.has_gpu;
  drawUtil();

  $("done-actions").style.display = status.state === "done" ? "flex" : "none";

  if (!running) {
    clearInterval(state.poll);
    if (status.state === "done") toast("Render complete.");
    if (status.state === "error") toast(status.error, true);
  }
}
