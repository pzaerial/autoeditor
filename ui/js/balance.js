import { syncGainControls } from "./editor.js";
import { formToProject, showGroupStates } from "./settings.js";
import { state } from "./state.js";
import { $, api, el, fmt, post, toast } from "./util.js";
import { refreshAll } from "./views.js";

// ---------------------------------------------------------------- auto-editor
//
// Two opt-in passes and one group of plain settings. The passes stay switched
// off until asked for, and their fields go dead with them, so the panel says
// what is running without hiding what is available.

export function showAutoEditorState() {
  const p = state.project;
  const bits = [];
  if (p.balance.enabled) bits.push(`balancing to ${p.balance.target_lufs} LUFS`);
  if (p.defaults.trim_silence) bits.push("trimming silence");
  else {
    const clips = p.clips.filter((c) => c.trim_silence).length;
    if (clips) bits.push(`trimming silence on ${clips} clip(s)`);
  }
  $("autoeditor-state").textContent = bits.join("  ·  ");
  showGroupStates();
}

// ---------------------------------------------------------------- balance levels
//
// Clips recorded in different sessions rarely match, and matching them by ear
// one at a time is the tedious part of assembling an episode.
//
// Levelling writes each clip's `audio_gain_db` -- the same number shown in the
// Volume column and editable there. There is deliberately no second "measured"
// field: two numbers for one outcome meant the Clips page could not say what a
// clip would actually sound like, and an edit could be silently overruled.
//
// So this measures; it is not an effect. Switching it on levels what is here
// and every clip added afterwards; a value you type afterwards is yours and is
// left alone unless you ask for a re-measure.

// Matches MAX_GAIN_DB in loudness.py; only used to word the summary.
const MAX_BOOST_DB = 24;

export function showBalanceResults(rows) {
  const box = $("balance-results");
  box.innerHTML = "";
  rows.forEach((row) => {
    const line = el("div", "balance-row" +
      (row.gain === null ? " skipped" : "") + (row.capped ? " capped" : ""));
    line.appendChild(el("span", "who", `${row.index + 1}. ${row.label}`));
    line.appendChild(el("span", "was", row.lufs === undefined ? "" : `${row.lufs} LUFS`));
    line.appendChild(el("span", "gain",
      row.gain === null ? "-" : `${row.gain > 0 ? "+" : ""}${row.gain} dB`));
    line.appendChild(el("span", "why", row.note || ""));
    box.appendChild(line);
  });
}

let balancing = false;
let balancePoll = null;

function showBalanceProgress(status) {
  const running = status && status.state === "running";
  $("balance-progress").classList.toggle("hidden", !running);
  if (!running) return;
  $("balance-bar").style.width = (status.pct || 0) + "%";
  // Totals are only known once every clip has been probed.
  $("balance-progress-text").textContent = status.total_seconds
    ? `${Math.round(status.pct)}%  ${fmt(status.done_seconds)} / ${fmt(status.total_seconds)}` +
      (status.current ? `  ·  ${status.current}` : "")
    : "Looking at the clips…";
}

/**
 * Measure and level. `onlyUnmeasured` limits it to clips not yet measured,
 * which is what adding a clip to an already-levelled project should do.
 *
 * The work happens in a job on the backend rather than inside the request:
 * reading a two-hour recording's audio is a real wait, and a request can only
 * say "measuring" and hope, where a job can say how far it has got and be
 * called off.
 */
export async function runBalance({ onlyUnmeasured } = {}) {
  if (balancing) return;
  const note = $("balance-note");
  // With one number, "not yet levelled" is "still sitting at 0 dB" -- anything
  // else is either a measurement already taken or a level someone chose.
  const pending = state.project.clips.filter((c) => !onlyUnmeasured || !c.audio_gain_db);
  if (!pending.length) {
    if (!onlyUnmeasured) {
      note.textContent = "Add some clips first.";
      note.className = "note bad";
    }
    return;
  }

  balancing = true;
  $("balance-run").disabled = true;
  note.textContent = `Measuring ${pending.length} clip(s)…`;
  note.className = "note";

  const target = state.project.balance.target_lufs;
  try {
    await post("/api/balance-audio", {
      project: state.project,
      target,
      only_unmeasured: !!onlyUnmeasured,
    });
  } catch (err) {
    note.textContent = err.message;
    note.className = "note bad";
    balancing = false;
    $("balance-run").disabled = false;
    return;
  }

  clearInterval(balancePoll);
  balancePoll = setInterval(() => pollBalance(target), 400);
  pollBalance(target);
}

export async function pollBalance(target) {
  let status;
  try {
    status = await api("/api/balance-audio");
  } catch {
    return;
  }
  showBalanceProgress(status);
  if (status.state === "running") return;

  clearInterval(balancePoll);
  balancing = false;
  $("balance-run").disabled = false;
  const note = $("balance-note");

  if (status.state === "cancelled") {
    note.textContent = "Measurement cancelled; nothing was changed.";
    note.className = "note warn";
    return;
  }
  if (status.state === "error") {
    note.textContent = status.error;
    note.className = "note bad";
    return;
  }

  let set = 0;
  status.clips.forEach((row) => {
    const clip = state.project.clips[row.index];
    if (!clip || row.gain === null || row.gain === undefined) return;
    clip.audio_gain_db = row.gain;
    set += 1;
  });
  showBalanceResults(status.clips);
  const skipped = status.clips.length - set;
  // A capped clip got a number but not the target, so it must not be counted
  // as levelled in the summary -- that is the one you would fix by hand.
  const capped = status.clips.filter((c) => c.capped).length;
  note.textContent =
    `Levelled ${set - capped} of ${status.clips.length} clip(s) to ${target} LUFS` +
    (capped ? `; ${capped} too quiet to reach it, even at the ${MAX_BOOST_DB} dB limit` : "") +
    (skipped ? `; ${skipped} could not be measured` : "") +
    ".";
  note.className = capped || skipped ? "note warn" : "note ok";
  refreshAll();
  if (state.selected >= 0) syncGainControls();
}

$("balance-cancel").addEventListener("click", () => {
  post("/api/balance-audio/cancel", {}).catch((e) => toast(e.message, true));
});

/**
 * Pick up a measurement already in flight -- a reload mid-run should show the
 * bar rather than sit blank. Only a *running* job is adopted: a finished or
 * cancelled one belongs to whoever started it, and reporting it here would put
 * someone else's result on a page that never asked for it.
 */
export async function adoptRunningBalance() {
  let status;
  try {
    status = await api("/api/balance-audio");
  } catch {
    return;
  }
  if (status.state !== "running") return;
  balancing = true;
  $("balance-run").disabled = true;
  showBalanceProgress(status);
  clearInterval(balancePoll);
  const target = state.project.balance.target_lufs;
  balancePoll = setInterval(() => pollBalance(target), 400);
}

$("balance-run").addEventListener("click", () => {
  formToProject();
  runBalance();
});

// This clears the one volume number, so it also clears anything typed by hand.
// Say that plainly rather than quietly discarding someone's work.
$("balance-reset").addEventListener("click", () => {
  const set = state.project.clips.filter((c) => c.audio_gain_db).length;
  if (set && !confirm(
    `Set all ${set} clip volume(s) back to 0 dB? This clears levels you typed too.`
  )) return;
  state.project.clips.forEach((clip) => (clip.audio_gain_db = 0));
  showBalanceResults([]);
  $("balance-note").textContent = "Every clip is back to 0 dB and plays at its own level.";
  $("balance-note").className = "note";
  refreshAll();
  if (state.selected >= 0) syncGainControls();
});

// Switching it on levels whatever has no level yet, and keeps doing that for
// clips added later. Switching it off only stops the measuring: the volumes it
// worked out are ordinary clip volumes now, and stay until they are changed.
$("bal-enabled").addEventListener("change", () => {
  formToProject();
  showAutoEditorState();
  showGroupStates();
  if (state.project.balance.enabled) runBalance({ onlyUnmeasured: true });
  else {
    $("balance-note").textContent = "";
    refreshAll();
    if (state.selected >= 0) syncGainControls();
  }
});

$("bal-target").addEventListener("change", () => {
  formToProject();
  if (state.project.balance.enabled) runBalance();
});
