/**
 * app.js — Dashboard polling, task triggers, and UI helpers.
 */

// ---------------------------------------------------------------------------
// Task trigger (Step 1 & 2 buttons)
// ---------------------------------------------------------------------------

function triggerTask(pid, endpoint) {
  fetch(`/project/${pid}/${endpoint}`, { method: 'POST' })
    .then(r => r.json())
    .then(j => {
      if (j.ok) {
        location.reload();
      } else {
        alert('Error: ' + (j.error || 'Unknown error'));
      }
    })
    .catch(err => alert('Network error: ' + err));
}


// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------

let _pollTimer     = null;
let _lastLogCount  = 0;
let _elapsedSecs   = 0;
let _elapsedTimer  = null;

function startPolling(pid) {
  if (_pollTimer) return;
  _elapsedSecs = 0;
  startElapsedTimer();
  _pollTimer = setInterval(() => pollStatus(pid), 2000);
  pollStatus(pid); // immediate first call
}

function stopPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
}

function pollStatus(pid) {
  fetch(`/project/${pid}/status`)
    .then(r => r.json())
    .then(data => {
      if (!data.ok) return;

      updateProgressBar(data.task.progress_pct);
      appendNewLogs(data.task.log);

      if (!data.task.running) {
        stopPolling();
        // Reload the page to show the updated UI state
        setTimeout(() => location.reload(), 400);
      }
    })
    .catch(() => {}); // silently ignore network hiccups
}

function updateProgressBar(pct) {
  const fill = document.getElementById('progress-fill');
  if (fill) fill.style.width = pct + '%';
}

function appendNewLogs(allLogs) {
  const box = document.getElementById('log-box');
  if (!box || !allLogs) return;
  if (allLogs.length > _lastLogCount) {
    const newLines = allLogs.slice(_lastLogCount);
    _lastLogCount = allLogs.length;
    box.textContent += newLines.join('\n') + '\n';
    box.scrollTop = box.scrollHeight;
  }
}

// ---------------------------------------------------------------------------
// Elapsed timer
// ---------------------------------------------------------------------------

function startElapsedTimer() {
  const el = document.getElementById('elapsed');
  if (!el) return;
  _elapsedTimer = setInterval(() => {
    _elapsedSecs++;
    const m = Math.floor(_elapsedSecs / 60);
    const s = String(_elapsedSecs % 60).padStart(2, '0');
    el.textContent = `${m}:${s}`;
  }, 1000);
}

// ---------------------------------------------------------------------------
// Generator batch action
// ---------------------------------------------------------------------------

window.createBatchFromGenerator = function () {
  var prompt = (document.getElementById('generator-prompt') || { value: '' }).value.trim();
  var useStyle = (document.getElementById('generator-style') || { checked: true }).checked;
  var qtyEl = document.getElementById('generator-qty');
  var qty = parseInt((qtyEl && qtyEl.value) || '5', 10);
  if (!qty || qty < 1) qty = 1;
  if (qty > 20) qty = 20;

  var jobs = [];
  for (var i = 0; i < qty; i++) {
    var fd = new FormData();
    fd.append('prompt', prompt);
    fd.append('use_channel_style', useStyle ? 'true' : 'false');
    fd.append('quantity', String(qty));
    jobs.push(fetch('/project/new', { method: 'POST', body: fd }).then(function (r) { return r.json(); }));
  }

  Promise.all(jobs)
    .then(function (rows) {
      var created = rows.filter(function (r) { return r && r.ok; });
      if (!created.length) {
        alert('Could not create projects.');
        return;
      }
      location.reload();
    })
    .catch(function (err) {
      alert('Batch creation failed: ' + err);
    });
};
