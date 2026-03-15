/**
 * panel.js — Unified right-panel for the calendar dashboard.
 *
 * openPanel(pid)   — load & show the detail panel for a project
 * closePanel()     — close the panel
 * createProject(date) — create a new project (AJAX), then open its panel
 */

(function () {
  'use strict';

  // ── State ──────────────────────────────────────────────────────────────────
  var _currentPid    = null;
  var _pollTimer     = null;
  var _selectedTitle = null;
  var _thumbTextPosition = 'top';
  var _assetVersion  = Date.now();
  var _loopMode      = 'kling';
  var _loopDuration  = 30;
  var _songCount     = 18;
  var _songTotal     = 18;
  var _ccDays        = new Set();
  var _ccMonths      = 1;
  var _currentView   = 'calendar';

  // ── Helpers ────────────────────────────────────────────────────────────────
  function f(id) { return document.getElementById(id); }

  function post(url, data) {
    var fd = data instanceof FormData ? data : new FormData();
    if (data && !(data instanceof FormData)) {
      Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    }
    return fetch(url, { method: 'POST', body: fd }).then(function (r) { return r.json(); });
  }

  function fileUrl(basename) {
    if (!basename) return null;
    return '/files/' + basename + '?v=' + _assetVersion;
  }

  function statusLabel(status) {
    return { idle: 'Idle', running: 'Generating', done: 'Done', error: 'Error' }[status] || status;
  }

  function pipelineStatusLabel(p) {
    if (p.step === 1) {
      if (p.files && p.files.thumbnail) return 'Thumbnail Ready';
      if (p.files && p.files.raw_image) return 'Drafting Thumbnail';
      return 'Needs Selection';
    }
    if (p.step === 2 && p.status === 'running') return 'Looping';
    if (p.step === 3 && p.status === 'running') return 'Rendering';
    if (p.step === 3 && (p.status === 'done' || (p.files && p.files.final_video))) return 'Video Ready!';
    if (p.step >= 4) return 'Ready to Schedule';
    return 'In Progress';
  }

  function formatDate(ds) {
    if (!ds) return 'Unscheduled';
    var d = new Date(ds + 'T00:00:00');
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Completed Project Summary ─────────────────────────────────────────────

  function renderSummaryContent(p) {
    var seo = p.seo || {};
    var yt  = p.youtube || {};
    var out = '';

    out += '<div class="panel-summary-published">&#10003; Published!</div>';
    out += '<div class="panel-summary">';

    // Media row: thumbnail + video side by side
    var _loopPreview = p.files.final_video || p.files.loop_a || p.files.loop30;
    if (p.files.thumbnail || _loopPreview) {
      out += '<div class="panel-summary-media">';
      if (p.files.thumbnail) {
        out += '<div>';
        out += '<div class="panel-preview-label">Thumbnail</div>';
        out += '<img src="' + fileUrl(p.files.thumbnail) + '" class="panel-summary-thumb" alt="">';
        out += '</div>';
      }
      if (_loopPreview) {
        out += '<div>';
        out += '<div class="panel-preview-label">Video Preview</div>';
        out += '<video src="' + fileUrl(_loopPreview) + '" controls loop class="panel-summary-video"></video>';
        out += '</div>';
      }
      out += '</div>';
    }

    // SEO metadata
    out += '<div class="panel-summary-seo">';
    var displayTitle = seo.title || p.title || '';
    if (displayTitle) {
      out += '<div class="panel-summary-field">';
      out += '<span class="panel-section-label">Title</span>';
      out += '<p style="font-size:14px;font-weight:600;line-height:1.4;margin:0">' + esc(displayTitle) + '</p>';
      out += '</div>';
    }
    if (seo.description) {
      out += '<div class="panel-summary-field">';
      out += '<span class="panel-section-label">Description <span style="color:var(--muted);font-weight:400;font-size:10px;text-transform:none;letter-spacing:0">(click to expand)</span></span>';
      out += '<div class="panel-summary-desc" onclick="toggleSummaryDesc(this)">';
      out += esc(seo.description).replace(/\n/g, '<br>');
      out += '</div>';
      out += '</div>';
    }
    if (seo.tags && seo.tags.length) {
      out += '<div class="panel-summary-field">';
      out += '<span class="panel-section-label">Tags</span>';
      out += '<div class="panel-tag-chips">';
      seo.tags.forEach(function (tag) {
        out += '<span class="panel-tag-chip">' + esc(tag) + '</span>';
      });
      out += '</div></div>';
    }
    out += '</div>'; // /panel-summary-seo

    // Footer: schedule date + YouTube link
    out += '<div class="panel-summary-footer">';
    if (yt.scheduled_publish_at) {
      // Strip Z/timezone suffix so the datetime is treated as local (not UTC)
      var pubD = new Date(yt.scheduled_publish_at.replace('Z', '').replace(/[+-]\d{2}:\d{2}$/, ''));
      out += '<span>&#128197; ' + pubD.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) + '</span>';
    }
    if (yt.video_url) {
      out += '<span>&#9654; <a href="' + esc(yt.video_url) + '" target="_blank">' + esc(yt.video_url) + '</a></span>';
    }
    out += '</div>';

    // Asset links
    out += '<div class="panel-asset-links" style="margin-top:2px">';
    if (p.files.thumbnail)              out += '<a href="' + fileUrl(p.files.thumbnail)  + '" target="_blank">Thumbnail</a>';
    if (p.files.loop_a || p.files.loop30) out += '<a href="' + fileUrl(p.files.loop_a || p.files.loop30) + '" target="_blank">Loop Video</a>';
    if (p.files.final_video)            out += '<a href="' + fileUrl(p.files.final_video) + '" target="_blank">Full Video</a>';
    out += '</div>';

    // Actions
    out += '<div class="panel-actions">';
    out += '<button class="btn btn-ghost btn-sm" onclick="panelGenerateSeo(\'' + esc(p.id) + '\')">Refresh SEO</button>';
    if (yt.video_id) {
      out += '<button class="btn btn-ghost btn-sm" onclick="panelSyncYtDate(\'' + esc(p.id) + '\')">Sync Date</button>';
    }
    out += '<button class="btn btn-danger btn-sm" style="background:transparent;border:1px solid var(--danger);color:var(--danger)" onclick="panelDelete(\'' + esc(p.id) + '\')">Delete</button>';
    out += '</div>';

    out += '</div>'; // /panel-summary
    return out;
  }

  window.toggleSummaryDesc = function (el) {
    el.classList.toggle('expanded');
  };

  // ── View toggle (Calendar / List) ─────────────────────────────────────────

  window.setView = function (mode) {
    _currentView = mode;
    localStorage.setItem('ufz-view', mode);
    var calSection = f('cal-section');
    var listView   = f('list-view');
    var btnCal     = f('btn-cal');
    var btnList    = f('btn-list');
    if (mode === 'list') {
      if (calSection) calSection.classList.add('hidden');
      if (listView)   { listView.classList.remove('hidden'); renderListView(); }
      if (btnCal)     btnCal.classList.remove('active');
      if (btnList)    btnList.classList.add('active');
    } else {
      if (calSection) calSection.classList.remove('hidden');
      if (listView)   listView.classList.add('hidden');
      if (btnCal)     btnCal.classList.add('active');
      if (btnList)    btnList.classList.remove('active');
    }
  };

  function renderListView() {
    var listEl   = f('list-view');
    if (!listEl) return;
    var projects = window.ALL_PROJECTS || [];
    if (!projects.length) {
      listEl.innerHTML = '<p style="color:var(--muted);padding:20px 0">No projects yet. Use the generator to create your first batch.</p>';
      return;
    }

    // Sort: scheduled first by date, then unscheduled
    var sorted = projects.slice().sort(function (a, b) {
      if (a.scheduled_date && b.scheduled_date) return a.scheduled_date.localeCompare(b.scheduled_date);
      if (a.scheduled_date)  return -1;
      if (b.scheduled_date)  return 1;
      return 0;
    });

    var html = '<table>';
    html += '<thead><tr>';
    html += '<th></th>';
    html += '<th>Title</th>';
    html += '<th>Date</th>';
    html += '<th>Step</th>';
    html += '<th>Status</th>';
    html += '</tr></thead><tbody>';

    sorted.forEach(function (p) {
      var thumbSrc  = p.files.thumbnail || p.files.raw_image;
      var isDone    = p.step === 4 && p.youtube && p.youtube.upload_status === 'done';
      var stepLabel = isDone ? 'Done' : 'Step ' + p.step + '/4';
      var stepCls   = isDone ? 'list-step-badge list-step-done' : 'list-step-badge';
      var stCls     = p.status === 'done' ? 'list-status-done' : p.status === 'running' ? 'list-status-running' : p.status === 'error' ? 'list-status-error' : '';
      var displayTitle = (p.seo && p.seo.title) || p.title || 'Untitled';

      html += '<tr onclick="openPanel(\'' + esc(p.id) + '\')">';
      html += '<td style="width:72px">';
      if (thumbSrc) {
        html += '<img class="list-thumb" src="/files/' + esc(thumbSrc) + '?v=' + _assetVersion + '" alt="">';
      } else {
        html += '<span class="list-thumb-empty"></span>';
      }
      html += '</td>';
      html += '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(displayTitle) + '</td>';
      html += '<td style="white-space:nowrap">' + esc(p.scheduled_date ? formatDate(p.scheduled_date) : 'Unscheduled') + '</td>';
      html += '<td><span class="' + stepCls + '">' + stepLabel + '</span></td>';
      html += '<td><span class="' + stCls + '">' + esc(p.status) + '</span></td>';
      html += '</tr>';
    });

    html += '</tbody></table>';
    listEl.innerHTML = html;
  }

  // ── Control Center ────────────────────────────────────────────────────────

  window.ccToggleDay = function (day, btn) {
    if (_ccDays.has(day)) {
      _ccDays.delete(day);
      btn.classList.remove('active');
    } else {
      _ccDays.add(day);
      btn.classList.add('active');
    }
    ccUpdateCount();
  };

  window.ccSetMonths = function (n, btn) {
    _ccMonths = n;
    document.querySelectorAll('.cc-tab').forEach(function (b) { b.classList.remove('active'); });
    btn.classList.add('active');
    ccUpdateCount();
  };

  function ccUpdateCount() {
    var countEl = f('cc-count');
    if (!countEl) return;
    if (_ccDays.size === 0) { countEl.textContent = '0 projects'; return; }

    var dayMap = { monday:1, tuesday:2, wednesday:3, thursday:4, friday:5, saturday:6, sunday:0 };
    var selectedNums = [];
    _ccDays.forEach(function (d) { if (dayMap[d] !== undefined) selectedNums.push(dayMap[d]); });

    var today = new Date();
    var end   = new Date(today);
    end.setMonth(end.getMonth() + _ccMonths);

    var count = 0;
    var cur   = new Date(today);
    cur.setDate(cur.getDate() + 1);
    while (cur <= end) {
      if (selectedNums.indexOf(cur.getDay()) !== -1) count++;
      cur.setDate(cur.getDate() + 1);
    }
    countEl.textContent = count + ' project' + (count !== 1 ? 's' : '');
  }

  window.ccCreateSchedule = function () {
    if (_ccDays.size === 0) { alert('Select at least one day of the week.'); return; }
    var days = Array.from(_ccDays);
    var fd   = new FormData();
    days.forEach(function (d) { fd.append('days', d); });
    fd.append('months', String(_ccMonths));
    fetch('/api/batch-schedule', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { alert(d.error || 'Failed to create schedule.'); return; }
        var msg = 'Created ' + d.created + ' project' + (d.created !== 1 ? 's' : '');
        if (d.skipped) msg += ', skipped ' + d.skipped + ' already-occupied date' + (d.skipped !== 1 ? 's' : '') + '.';
        alert(msg);
        location.reload();
      });
  };

  // ── Panel open/close ───────────────────────────────────────────────────────
  window.openPanel = function (pid) {
    _currentPid = pid;
    history.replaceState(null, '', '#' + pid);
    f('detail-panel').classList.add('panel-open');
    f('panel-backdrop').classList.add('active');
    f('panel-body').innerHTML = '<div class="panel-content"><div class="panel-running"><div class="spinner"></div> Loading&hellip;</div></div>';
    loadPanel();
  };

  window.closePanel = function () {
    _currentPid = null;
    stopPolling();
    f('detail-panel').classList.remove('panel-open');
    f('panel-backdrop').classList.remove('active');
    f('panel-body').innerHTML = '';
    history.replaceState(null, '', location.pathname + location.search);
  };

  window.createProject = function (scheduledDate) {
    var fd = new FormData();
    if (scheduledDate) fd.append('scheduled_date', scheduledDate);
    fetch('/project/new', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { alert(d.error || 'Could not create project.'); return; }
        openPanel(d.pid);
      });
  };

  // ── Load project data and render panel ────────────────────────────────────
  function loadPanel() {
    if (!_currentPid) return;
    fetch('/api/project/' + _currentPid)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { f('panel-body').innerHTML = '<div class="panel-content"><div class="panel-error">Project not found.</div></div>'; return; }
        renderPanel(d);
        if (d.status === 'running') {
          startPolling();
        } else {
          stopPolling();
        }
      });
  }

  function startPolling() {
    stopPolling();
    _pollTimer = setInterval(loadPanel, 2000);
  }

  function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  // ── Render panel HTML ─────────────────────────────────────────────────────
  function renderPanel(p) {
    _songTotal = p.song_config && p.song_config.count ? p.song_config.count : 18;
    _thumbTextPosition = (p.thumbnail_config && p.thumbnail_config.text_position) || 'top';
    _assetVersion = Date.now();

    var isFullyDone = (p.step === 4 && p.youtube && p.youtube.upload_status === 'done');
    var html = '';

    // ── Header ──────────────────────────────────────────────────────────────
    html += '<div class="panel-header">';
    html += '<div class="panel-header-left">';
    html += '<div class="panel-title">' + esc(p.title || 'New Video') + '</div>';
    html += '<div class="panel-meta">';
    html += '<span>' + formatDate(p.scheduled_date) + '</span>';
    html += '<span class="status-badge status-' + p.status + '">' + statusLabel(p.status) + '</span>';
    html += '</div>';
    html += '</div>';
    html += '<button class="panel-close" onclick="closePanel()">&#215;</button>';
    html += '</div>';

    // ── Step bar (4 steps) ───────────────────────────────────────────────────
    html += '<div class="panel-steps">';
    var steps = [['1','Thumbnail'], ['2','Video Loop'], ['3','Render 1-Hour'], ['4','SEO & Publish']];
    var videoReady = (p.step === 3 && (p.status === 'done' || !!(p.files && p.files.final_video)));
    steps.forEach(function (s, i) {
      var n = i + 1;
      var isDone    = !isFullyDone && p.step > n;
      var isActive  = !isFullyDone && p.step === n;
      // Step 4 is "ready" (clickable next step) when the 1-hour video is done
      var isNextReady = (n === 4 && videoReady);
      var cls = isFullyDone ? 'panel-step-done'
              : isDone      ? 'panel-step-done'
              : isActive    ? 'panel-step-active'
              : isNextReady ? 'panel-step-active'
              : '';
      // Completed steps: go back. Step 4 when video is ready: proceed to SEO.
      var clickable = isDone
        ? ' style="cursor:pointer" title="Click to redo this step" onclick="panelGoBack(\'' + esc(p.id) + '\',' + n + ')"'
        : isNextReady
          ? ' style="cursor:pointer" title="Proceed to SEO & Upload" onclick="panelApproveStep3(\'' + esc(p.id) + '\')"'
          : '';
      html += '<div class="panel-step ' + cls + '"' + clickable + '>';
      html += '<div class="panel-step-num">' + (p.step > n ? '&#10003;' : s[0]) + '</div>';
      html += '<span class="panel-step-label">' + s[1] + '</span>';
      html += '</div>';
      if (i < 3) {
        html += '<div class="panel-step-connector ' + (p.step > n ? 'panel-step-connector-done' : '') + '"></div>';
      }
    });
    html += '</div>';

    // ── Content ───────────────────────────────────────────────────────────────
    html += '<div class="panel-content">';

    // Fully-completed project gets a read-only summary instead of the step workflow
    if (isFullyDone) {
      html += renderSummaryContent(p);
      html += '</div>'; // /panel-content
      f('panel-body').innerHTML = html;
      return;
    }

    // Schedule picker
    html += '<div>';
    html += '<div class="panel-section-label">Schedule</div>';
    html += '<div class="panel-schedule-row">';
    html += '<input type="date" id="panel-date" value="' + esc(p.scheduled_date || '') + '">';
    html += '<button class="btn btn-ghost btn-sm" onclick="panelSetDate(\'' + esc(p.id) + '\')">Set Date</button>';
    if (p.scheduled_date) {
      html += '<button class="btn btn-ghost btn-sm" onclick="panelClearDate(\'' + esc(p.id) + '\')">Unschedule</button>';
    }
    html += '</div>';
    html += '</div>';

    // Error banner
    if (p.status === 'error' && p.task.error) {
      html += '<div class="panel-error">' + esc(p.task.error) + '</div>';
    }

    // Running state
    if (p.status === 'running') {
      var runMsg = 'Working\u2026';
      if (p.task.step_running === 1 && !p.candidate_images.length) runMsg = 'Generating images via fal.ai\u2026';
      else if (p.task.step_running === 1 && p.candidate_images.length && !p.files.raw_image) runMsg = 'Creating background\u2026';
      else if (p.task.step_running === 1 && p.files.raw_image) runMsg = 'Generating thumbnail\u2026';
      else if (p.task.step_running === 2) runMsg = 'Generating both video loops\u2026';
      else if (p.task.step_running === '2_slot_a') runMsg = 'Regenerating Slot A\u2026';
      else if (p.task.step_running === '2_slot_b') runMsg = 'Regenerating Slot B\u2026';
      else if (p.task.step_running === '2b') runMsg = 'Generating music via Suno (Chrome is open)\u2026';
      else if (p.task.step_running === '2b_beatoven') runMsg = 'Generating music via Beatoven (fal.ai)\u2026';
      else if (p.task.step_running === '2b_stable_audio') runMsg = 'Generating music via Stable Audio (fal.ai)\u2026';
      else if (p.task.step_running === 3) runMsg = 'Building 1-hour video (~10\u201315 min)\u2026';
      else if (p.task.step_running === '4seo') runMsg = 'Generating SEO metadata\u2026';
      else if (p.task.step_running === '4upload') runMsg = 'Uploading to YouTube\u2026';

      var logLines = p.task.log || [];
      var hasError = logLines.some(function (l) { return l.indexOf('ERROR') !== -1; });

      html += '<div class="panel-running"><div class="spinner"></div><span>' + runMsg + '</span>';
      if (hasError) {
        html += '<button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="panelResetTask(\'' + esc(p.id) + '\')">Stop &amp; Reset</button>';
      }
      html += '</div>';
      html += '<div class="panel-progress"><div class="panel-progress-fill" style="width:' + (p.task.progress_pct || 0) + '%"></div></div>';
      if (logLines.length) {
        html += '<pre class="panel-log">' + esc(logLines.slice(-8).join('\n')) + '</pre>';
      }
    }

    var mediaSrc = p.files.final_video || p.files.loop || p.files.thumbnail || p.files.raw_image || p.files.background;
    var mediaType = (p.files.final_video || p.files.loop) ? 'video' : 'image';
    html += '<div class="panel-command-center">';
    html += '<div class="panel-section-label">Project Command Center</div>';
    if (p.step === 1) {
      var step1Preview = p.files.thumbnail || p.files.background || p.files.raw_image;
      html += '<div class="panel-command-grid panel-command-grid-single">';
      html += '<div class="panel-command-media">';
      html += '<h4>Thumbnail Focus</h4>';
      if (step1Preview) {
        html += '<img src="' + fileUrl(step1Preview) + '" alt="Thumbnail preview" style="width:100%;border-radius:8px">';
      } else {
        html += '<div class="hint">No image selected yet. Generate images to start.</div>';
      }
      html += '<div class="panel-command-meta">Status: <strong>' + pipelineStatusLabel(p) + '</strong></div>';
      html += '</div>';
      html += '</div>';
    } else {
      html += '<div class="panel-command-grid">';
      html += '<div class="panel-command-media">';
      html += '<h4>Media Preview</h4>';
      if (mediaSrc) {
        if (mediaType === 'video') {
          html += '<video src="' + fileUrl(mediaSrc) + '" controls style="width:100%;border-radius:8px"></video>';
        } else {
          html += '<img src="' + fileUrl(mediaSrc) + '" alt="Preview" style="width:100%;border-radius:8px">';
        }
      } else {
        html += '<div class="hint">No media yet. Start with Generate Images.</div>';
      }
      html += '<div class="panel-command-meta">Status: <strong>' + pipelineStatusLabel(p) + '</strong></div>';
      html += '</div>';
      html += '<div class="panel-command-seo">';
      html += '<h4>SEO Optimizer</h4>';
      if (p.step >= 4) {
        html += '<p class="hint">Refresh title/description from your style profile and competitor patterns.</p>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelGenerateSeo(\'' + esc(p.id) + '\')">Refresh SEO</button>';
        if (p.seo && (p.seo.title || p.seo.description)) {
          html += '<p class="hint" style="margin-top:8px">SEO saved for this project.</p>';
        }
      } else {
        html += '<p class="hint">SEO tools unlock at Step 4 (SEO & Publish).</p>';
      }
      html += '<h4 style="margin-top:14px">Asset Links</h4>';
      html += '<div class="panel-asset-links">';
      if (p.files.thumbnail) html += '<a href="' + fileUrl(p.files.thumbnail) + '" target="_blank">Thumbnail</a>';
      if (p.files.loop_a || p.files.loop30) html += '<a href="' + fileUrl(p.files.loop_a || p.files.loop30) + '" target="_blank">Loop Video</a>';
      if (p.files.final_video) html += '<a href="' + fileUrl(p.files.final_video) + '" target="_blank">Full Video</a>';
      html += '</div>';
      html += '</div>';
      html += '<div class="panel-command-phone">';
      html += '<h4>YouTube Sync</h4>';
      html += '<div class="yt-phone">';
      if (p.files.thumbnail) html += '<img src="' + fileUrl(p.files.thumbnail) + '" alt="thumb">';
      html += '<strong>' + esc((p.seo && p.seo.title) || p.title || 'Untitled Upload') + '</strong>';
      html += '<p>' + esc(((p.seo && p.seo.description) || 'SEO description preview will appear here.').slice(0, 120)) + '...</p>';
      html += '</div>';
      html += '</div>';
    }
    html += '</div>';
    html += '</div>';

    // ── Step 1 states ────────────────────────────────────────────────────────
    if (p.step === 1 && p.status !== 'running') {

      if (p.files.thumbnail && p.files.raw_image) {
        // State: thumbnail ready — approve to go to step 2
        html += '<div>';
        html += '<div class="panel-section-label">Preview</div>';
        html += '<div class="panel-thumb-row">';
        if (p.files.background) {
          html += '<div class="panel-thumb-box"><div class="panel-preview-label">Background</div><img src="' + fileUrl(p.files.background) + '" alt=""></div>';
        } else {
          html += '<div class="panel-thumb-box"><div class="panel-preview-label">Selected</div><img src="' + fileUrl(p.files.raw_image) + '" alt=""></div>';
        }
        html += '<div class="panel-thumb-box"><div class="panel-preview-label">Thumbnail</div><img src="' + fileUrl(p.files.thumbnail) + '" alt=""></div>';
        html += '</div>';
        html += '</div>';
        html += '<div style="margin-top:8px">';
        html += '<div class="panel-preview-label">Text position</div>';
        html += '<div class="panel-pos-row">';
        html += '<button class="panel-pos-btn ' + (_thumbTextPosition === 'top' ? 'active' : '') + '" onclick="panelSetTextPosition(\'top\')">Top</button>';
        html += '<button class="panel-pos-btn ' + (_thumbTextPosition === 'middle' ? 'active' : '') + '" onclick="panelSetTextPosition(\'middle\')">Middle</button>';
        html += '<button class="panel-pos-btn ' + (_thumbTextPosition === 'bottom' ? 'active' : '') + '" onclick="panelSetTextPosition(\'bottom\')">Bottom</button>';
        html += '</div>';
        html += '</div>';
        html += '<div class="panel-actions">';
        html += '<button class="btn btn-success" onclick="panelApproveStep1(\'' + esc(p.id) + '\')">Approve &rarr; Step 2</button>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelUpdateThumbPosition(\'' + esc(p.id) + '\', \'' + esc(p.title || '') + '\')">Update Thumbnail Position</button>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelRegen(\'' + esc(p.id) + '\')">New Images</button>';
        html += '</div>';

      } else if (p.files.raw_image && p.files.background) {
        // State: image selected, background ready — pick a title
        html += '<p class="hint">Selected image and background are already shown in Media Preview above.</p>';
        html += '<div>';
        html += '<div class="panel-section-label">Pick thumbnail text</div>';
        if (p.title_suggestions && p.title_suggestions.length) {
          html += '<div class="panel-title-chips" id="title-chips">';
          p.title_suggestions.forEach(function (t) {
            var sel = (_selectedTitle === t) ? ' selected' : '';
            html += '<button class="panel-title-chip' + sel + '" onclick="panelPickTitle(this, \'' + esc(t) + '\')">' + esc(t) + '</button>';
          });
          html += '</div>';
        } else {
          html += '<p style="font-size:13px;color:var(--muted)">No suggestions yet. Regenerate images to get title suggestions.</p>';
        }
        html += '<div class="panel-actions" style="margin-top:8px">';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelRefreshSuggestions(\'' + esc(p.id) + '\')">Review Channel & Refresh Suggestions</button>';
        html += '</div>';
        html += '<div style="margin-top:8px">';
        html += '<div class="panel-preview-label">Text position</div>';
        html += '<div class="panel-pos-row">';
        html += '<button class="panel-pos-btn ' + (_thumbTextPosition === 'top' ? 'active' : '') + '" onclick="panelSetTextPosition(\'top\')">Top</button>';
        html += '<button class="panel-pos-btn ' + (_thumbTextPosition === 'middle' ? 'active' : '') + '" onclick="panelSetTextPosition(\'middle\')">Middle</button>';
        html += '<button class="panel-pos-btn ' + (_thumbTextPosition === 'bottom' ? 'active' : '') + '" onclick="panelSetTextPosition(\'bottom\')">Bottom</button>';
        html += '</div>';
        html += '</div>';
        var customVal = (_selectedTitle && !p.title_suggestions.includes(_selectedTitle)) ? esc(_selectedTitle) : '';
        html += '<div style="margin-top:10px">';
        html += '<input type="text" id="panel-custom-title" class="panel-custom-title-input" placeholder="Or type your own thumbnail text/title\u2026" value="' + customVal + '" oninput="panelCustomTitle(this)">';
        html += '</div>';
        html += '<p class="hint" style="margin-top:6px">Custom entry is allowed. It does not need to match a suggestion.</p>';
        html += '</div>';
        html += '<div class="panel-actions">';
        html += '<button class="btn btn-primary" onclick="panelSetTitle(\'' + esc(p.id) + '\')">Generate Thumbnail</button>';
        html += '</div>';

      } else if (p.candidate_images && p.candidate_images.length) {
        // State: 4 images ready — pick one
        html += '<div class="panel-section-label">Pick an image</div>';
        html += '<div class="panel-candidates" id="panel-cands">';
        p.candidate_images.forEach(function (bn, i) {
          html += '<div class="panel-candidate" id="cand-' + i + '" onclick="panelSelectCandidate(this, \'' + esc(p.id) + '\', \'' + esc(bn) + '\')">';
          html += '<img src="' + fileUrl(bn) + '" alt="Option ' + (i+1) + '">';
          html += '</div>';
        });
        html += '</div>';
        html += '<div class="panel-actions" style="margin-top:4px">';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelRegen(\'' + esc(p.id) + '\')">Generate New Images</button>';
        html += '</div>';

      } else {
        // State: not started
        html += '<p class="hint">Style is pulled from your channel profile automatically.</p>';
        html += '<div class="panel-actions">';
        html += '<button class="btn btn-primary" onclick="panelStartStep1(\'' + esc(p.id) + '\')">Generate Images</button>';
        html += '</div>';
      }
    }

    // ── Step 2 ───────────────────────────────────────────────────────────────
    var _slotRegen = p.task && (p.task.step_running === '2_slot_a' || p.task.step_running === '2_slot_b');
    if (p.step === 2 && (p.status !== 'running' || _slotRegen)) {
      var hasA = p.files && p.files.loop_a;
      var hasB = p.files && p.files.loop_b;
      var modelOpts =
        '<option value="kling_v16">Kling v1.6 (~$0.25)</option>' +
        '<option value="kling_v21">Kling v2.1 (~$0.28)</option>' +
        '<option value="seedance_lite">Seedance Lite (~$0.18, cam-fixed)</option>' +
        '<option value="seedance_pro">Seedance Pro (~$0.62, cam-fixed)</option>' +
        '<option value="hailuo_pro">Hailuo-02 Pro (~$0.48)</option>';

      if (hasA || hasB) {
        // ── Slot A ────────────────────────────────────────────────────────
        var _regenA = p.task && p.task.step_running === '2_slot_a';
        var _regenB = p.task && p.task.step_running === '2_slot_b';

        html += '<div class="panel-section-label">Slot A — ' + esc(p.files.loop_a_model || 'Kling v1.6') + (_regenA ? ' <span style="color:#f59e0b;font-size:10px">&#9679; generating…</span>' : '') + '</div>';
        if (_regenA) {
          html += '<div style="padding:18px 0;text-align:center;color:var(--muted);font-size:12px;margin-bottom:6px"><div class="spinner" style="display:inline-block;margin-right:8px"></div>Regenerating Slot A…</div>';
        } else if (hasA) {
          html += '<video src="' + fileUrl(p.files.loop_a) + '" controls loop style="width:100%;border-radius:6px;max-height:160px;margin-bottom:6px"></video>';
        } else {
          html += '<div style="padding:12px;color:var(--muted);font-size:12px;margin-bottom:6px">Failed — regenerate below</div>';
        }
        html += '<div style="display:flex;gap:6px;margin-bottom:14px">';
        html += '<select id="panel-slot-a-model" class="form-select" style="flex:1;font-size:12px" ' + (_regenA ? 'disabled' : '') + '>';
        html += modelOpts.replace('value="' + (p.files.loop_a_model || 'kling_v16') + '"', 'value="' + (p.files.loop_a_model || 'kling_v16') + '" selected');
        html += '</select>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelRegenSlot(\'' + esc(p.id) + '\',\'a\')" ' + (_regenA || _regenB ? 'disabled' : '') + '>&#8635; Regen</button>';
        html += '</div>';

        // ── Slot B ────────────────────────────────────────────────────────
        html += '<div class="panel-section-label">Slot B — ' + esc(p.files.loop_b_model || 'Seedance Pro') + (_regenB ? ' <span style="color:#f59e0b;font-size:10px">&#9679; generating…</span>' : '') + '</div>';
        if (_regenB) {
          html += '<div style="padding:18px 0;text-align:center;color:var(--muted);font-size:12px;margin-bottom:6px"><div class="spinner" style="display:inline-block;margin-right:8px"></div>Regenerating Slot B…</div>';
        } else if (hasB) {
          html += '<video src="' + fileUrl(p.files.loop_b) + '" controls loop style="width:100%;border-radius:6px;max-height:160px;margin-bottom:6px"></video>';
        } else {
          html += '<div style="padding:12px;color:var(--muted);font-size:12px;margin-bottom:6px">Failed — regenerate below</div>';
        }
        html += '<div style="display:flex;gap:6px;margin-bottom:14px">';
        html += '<select id="panel-slot-b-model" class="form-select" style="flex:1;font-size:12px" ' + (_regenB ? 'disabled' : '') + '>';
        html += modelOpts.replace('value="' + (p.files.loop_b_model || 'seedance_pro') + '"', 'value="' + (p.files.loop_b_model || 'seedance_pro') + '" selected');
        html += '</select>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelRegenSlot(\'' + esc(p.id) + '\',\'b\')" ' + (_regenA || _regenB ? 'disabled' : '') + '>&#8635; Regen</button>';
        html += '</div>';

        html += '<div class="panel-actions">';
        html += '<button class="btn btn-success" onclick="panelApproveStep2(\'' + esc(p.id) + '\',\'a\')" ' + (_regenA || _regenB || !hasA ? 'disabled' : '') + '>&#10003; Use Slot A</button>';
        html += '<button class="btn btn-success" onclick="panelApproveStep2(\'' + esc(p.id) + '\',\'b\')" ' + (_regenA || _regenB || !hasB ? 'disabled' : '') + '>&#10003; Use Slot B</button>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelStartStep2(\'' + esc(p.id) + '\')" ' + (_regenA || _regenB ? 'disabled' : '') + '>&#8635; Both</button>';
        html += '</div>';
      } else {
        // Not started
        if (p.files && p.files.background) {
          html += '<div class="panel-thumb-box"><img src="' + fileUrl(p.files.background) + '" alt="" style="border-radius:6px;margin-bottom:10px"></div>';
        }
        html += '<div class="panel-actions">';
        html += '<button class="btn btn-primary" onclick="panelStartStep2(\'' + esc(p.id) + '\')">&#10024; Generate Loops</button>';
        html += '</div>';
        html += '<p style="font-size:11px;color:var(--muted);margin-top:6px">Generates Kling v1.6 + Seedance Pro in sequence (~$0.87, 2&ndash;4 min).</p>';
      }
    }

    // ── Step 3 ───────────────────────────────────────────────────────────────
    if (p.step === 3) {
      if (p.status === 'done') {
        html += '<div class="panel-done">';
        html += '<p>&#10003; 1-hour video ready!</p>';
        if (p.files.final_video) html += '<div class="final-path">' + esc(p.files.final_video) + '</div>';
        html += '</div>';
        html += '<div class="panel-actions" style="margin-top:8px">';
        html += '<button class="btn btn-primary" onclick="panelApproveStep3(\'' + esc(p.id) + '\')">Proceed to SEO &amp; Upload &rarr;</button>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelRerenderStep3(\'' + esc(p.id) + '\')" style="margin-left:8px">&#8635; Re-render</button>';
        html += '</div>';

      } else if (p.status !== 'running') {
        // Music generation optional panel
        html += '<div class="panel-suno">';
        html += '<h4>Generate Music <span class="badge-optional">Optional</span></h4>';
        html += '<p class="hint" style="font-size:12px;margin-bottom:10px">Skip if you already have music in the music/ folder.</p>';
        // Tab bar — Suno tab only shown to operator
        html += '<div style="display:flex;gap:6px;margin-bottom:10px">';
        html += '<button id="panel-tab-stable" class="btn btn-primary btn-sm" onclick="panelMusicTab(\'stable_audio\')">&#10024; Stable Audio (fal.ai)</button>';
        if (typeof IS_OPERATOR !== 'undefined' && IS_OPERATOR) {
          html += '<button id="panel-tab-suno" class="btn btn-ghost btn-sm" onclick="panelMusicTab(\'suno\')">Suno (Chrome)</button>';
        }
        html += '</div>';
        // Stable Audio options (default)
        html += '<div id="panel-beatoven-opts">';
        html += '<div class="panel-row" style="gap:10px;align-items:flex-end">';
        html += '<div class="panel-field" style="flex:1"><label>Music style</label>';
        html += '<input type="text" id="panel-suno-prompt" value="electronic, ambient, deep, slow, warm, instrumental" style="width:100%"></div>';
        html += '<div class="panel-field"><label>Tracks</label><input type="number" id="panel-track-count" value="18" min="1" max="30" style="width:60px"></div>';
        html += '</div>';
        html += '<p class="hint" style="font-size:11px;margin:4px 0 8px">fal.ai Stable Audio &bull; ~3 min per track &bull; No Chrome needed</p>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelStartMusic(\'' + esc(p.id) + '\',\'stable_audio\')">&#9654; Generate Music</button>';
        html += '</div>';
        // Suno options (hidden by default, operator only)
        html += '<div id="panel-suno-opts" style="display:none">';
        html += '<p class="hint" style="font-size:11px;margin-bottom:8px">Opens Chrome on your desktop. Do not close it. Takes 10–30 min.</p>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelStartMusic(\'' + esc(p.id) + '\',\'suno\')">&#9654; Generate via Suno</button>';
        html += '</div>';
        html += '</div>';

        // Music browser
        html += '<div class="panel-section-label" style="display:flex;justify-content:space-between;align-items:center">';
        html += '<span>Music Tracks</span>';
        html += '<button class="btn btn-ghost btn-sm" style="font-size:11px" onclick="panelLoadTracks()">&#8635; Refresh</button>';
        html += '</div>';
        html += '<div id="panel-track-list" style="max-height:220px;overflow-y:auto;margin-bottom:12px">';
        html += '<p style="font-size:12px;color:var(--muted)">Loading tracks…</p>';
        html += '</div>';

        // Build video form
        var cfg = p.song_config || {};
        html += '<div>';
        html += '<div class="panel-section-label">Build 1-Hour Video</div>';
        html += '<div class="panel-row" style="gap:14px">';
        html += '<div class="panel-field"><label>Songs</label><input type="number" id="panel-song-count" value="' + (cfg.count || 18) + '" min="1" max="99" style="width:70px"></div>';
        html += '<div class="panel-field"><label>Crossfade (s)</label><input type="number" id="panel-crossfade" value="' + (cfg.crossfade_sec || 2) + '" min="0" max="10" step="0.5" style="width:70px"></div>';
        html += '</div>';
        var curStyle = cfg.overlay_style || (typeof CHANNEL_OVERLAY_STYLE !== 'undefined' ? CHANNEL_OVERLAY_STYLE : 'default');
        html += '<div class="panel-field" style="margin-top:10px">';
        html += '<label>Now Playing Overlay</label>';
        html += '<select id="panel-overlay-style" style="width:100%">';
        html += '<option value="default"'  + (curStyle === 'default'  ? ' selected' : '') + '>Default (glassmorphism panel)</option>';
        html += '<option value="minimal"'  + (curStyle === 'minimal'  ? ' selected' : '') + '>Minimal (text only, no panel)</option>';
        html += '<option value="none"'     + (curStyle === 'none'     ? ' selected' : '') + '>Off (no overlay)</option>';
        html += '</select>';
        html += '</div>';
        html += '</div>';
        html += '<div class="panel-actions">';
        html += '<button class="btn btn-primary" onclick="panelStartStep3(\'' + esc(p.id) + '\')">Start Build</button>';
        html += '</div>';
        html += '<p class="hint" style="font-size:12px">~10&ndash;15 minutes to encode.</p>';
      }
    }

    // ── Step 4 — SEO & YouTube upload ────────────────────────────────────────
    if (p.step === 4) {
      var seo = p.seo || {};
      var yt  = p.youtube || {};

      // YouTube done state
      if (yt.upload_status === 'done') {
        html += '<div class="panel-done">';
        html += '<p>&#10003; Published to YouTube!</p>';
        if (yt.video_url) {
          html += '<a href="' + esc(yt.video_url) + '" target="_blank" class="panel-yt-link">' + esc(yt.video_url) + '</a>';
        }
        html += '</div>';

      } else if (p.status !== 'running') {
        // ── OAuth connection status ──────────────────────────────────────────
        html += '<div class="panel-oauth-row" id="panel-oauth-row">';
        html += '<span class="panel-oauth-label">YouTube Account</span>';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelCheckOauth()" id="panel-oauth-btn">Check Connection</button>';
        html += '</div>';

        if (yt.upload_error) {
          html += '<div class="panel-error">' + esc(yt.upload_error) + '</div>';
        }

        // ── SEO fields ────────────────────────────────────────────────────────
        html += '<div class="panel-section-label" style="margin-top:8px">SEO Metadata';
        html += '<button class="btn btn-ghost btn-sm" style="margin-left:8px;font-size:11px" onclick="panelGenerateSeo(\'' + esc(p.id) + '\')">Auto-Generate</button>';
        html += '</div>';

        html += '<div class="panel-field" style="gap:5px">';
        html += '<label>Title</label>';
        html += '<input type="text" id="seo-title" value="' + esc(seo.title || p.title || '') + '" style="width:100%">';
        html += '</div>';

        html += '<div class="panel-field" style="gap:5px;margin-top:8px">';
        html += '<label>Description</label>';
        html += '<textarea id="seo-desc" rows="6" style="width:100%;resize:vertical">' + esc(seo.description || '') + '</textarea>';
        html += '</div>';

        html += '<div class="panel-field" style="gap:5px;margin-top:8px">';
        html += '<label>Tags (comma-separated)</label>';
        html += '<input type="text" id="seo-tags" value="' + esc((seo.tags || []).join(', ')) + '" style="width:100%">';
        html += '</div>';

        html += '<div class="panel-actions" style="margin-top:6px">';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelSaveSeo(\'' + esc(p.id) + '\')">Save SEO</button>';
        html += '</div>';

        // ── Schedule publish time ─────────────────────────────────────────────
        html += '<div style="margin-top:12px">';
        html += '<div class="panel-section-label">Publish Schedule';
        html += '<button class="btn btn-ghost btn-sm" style="margin-left:8px;font-size:11px" onclick="panelSuggestSlot(\'' + esc(p.id) + '\')">Suggest Slot</button>';
        html += '</div>';
        html += '<div class="panel-schedule-row">';
        // Convert UTC ISO string to datetime-local value (strip Z)
        var publishVal = (yt.scheduled_publish_at || '').replace('Z', '').slice(0, 16);
        html += '<input type="datetime-local" id="panel-publish-at" value="' + esc(publishVal) + '">';
        html += '<button class="btn btn-ghost btn-sm" onclick="panelSetPublishTime(\'' + esc(p.id) + '\')">Set</button>';
        if (yt.scheduled_publish_at) {
          html += '<button class="btn btn-ghost btn-sm" onclick="panelClearPublishTime(\'' + esc(p.id) + '\')">Clear</button>';
        }
        html += '</div>';
        if (yt.scheduled_publish_at) {
          html += '<p class="hint" style="font-size:12px;margin-top:4px">Scheduled: ' + esc(yt.scheduled_publish_at) + '</p>';
        } else {
          html += '<p class="hint" style="font-size:12px;margin-top:4px">Leave empty to publish immediately (as public).</p>';
        }
        html += '</div>';

        // ── Upload button ─────────────────────────────────────────────────────
        html += '<div class="panel-actions" style="margin-top:14px">';
        if (yt.upload_status === 'uploading') {
          html += '<button class="btn btn-primary" disabled>Uploading&hellip;</button>';
        } else {
          html += '<button class="btn btn-success" onclick="panelUpload(\'' + esc(p.id) + '\')">&#8593; Upload to YouTube</button>';
        }
        html += '</div>';
      }
    }

    // ── Delete link at bottom ────────────────────────────────────────────────
    html += '<div style="border-top:1px solid var(--border);padding-top:12px;margin-top:4px">';
    html += '<button class="panel-delete-link" onclick="panelDelete(\'' + esc(p.id) + '\')">Delete project</button>';
    html += '</div>';

    html += '</div>'; // /panel-content

    f('panel-body').innerHTML = html;

    // Auto-load music track list when Step 3 panel is shown
    if (p.step === 3 && p.status !== 'running') {
      window.panelLoadTracks();
    }

    // Restore selected title chip if set
    if (_selectedTitle) {
      var chips = document.querySelectorAll('.panel-title-chip');
      chips.forEach(function (c) {
        if (c.textContent.trim() === _selectedTitle) c.classList.add('selected');
      });
    }
  }

  // ── Panel actions ─────────────────────────────────────────────────────────

  window.panelSetDate = function (pid) {
    var d = f('panel-date');
    if (!d) return;
    var fd = new FormData();
    fd.append('scheduled_date', d.value);
    fetch('/project/' + pid + '/schedule', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (j.ok) { location.reload(); } });
  };

  window.panelClearDate = function (pid) {
    var fd = new FormData();
    fd.append('scheduled_date', '');
    fetch('/project/' + pid + '/schedule', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (j.ok) { location.reload(); } });
  };

  window.panelStartStep1 = function (pid) {
    post('/project/' + pid + '/step1/start').then(function (j) {
      if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error); }
    });
  };

  window.panelRegen = function (pid) {
    post('/project/' + pid + '/step1/regenerate').then(function (j) {
      if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error); }
    });
  };

  window.panelRefreshSuggestions = function (pid) {
    post('/project/' + pid + '/step1/refresh-suggestions').then(function (j) {
      if (j.ok) { loadPanel(); }
      else { alert(j.error || 'Could not refresh suggestions.'); }
    });
  };

  window.panelSelectCandidate = function (el, pid, basename) {
    // find the full path from candidates — we pass basename to the server which
    // resolves it from OUTPUT_DIR
    document.querySelectorAll('.panel-candidate').forEach(function (c) { c.classList.remove('selected'); });
    el.classList.add('selected');

    var fd = new FormData();
    // The server's step1/select accepts full paths or basenames and finds the file
    // We store the basename; server will glob OUTPUT_DIR for it.
    fd.append('image_path', basename);
    fetch('/project/' + pid + '/step1/select', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) { alert(j.error || 'Failed.'); return; }
        loadPanel(); startPolling();
      });
  };

  window.panelPickTitle = function (el, title) {
    _selectedTitle = title;
    document.querySelectorAll('.panel-title-chip').forEach(function (c) { c.classList.remove('selected'); });
    el.classList.add('selected');
    var inp = document.getElementById('panel-custom-title');
    if (inp) inp.value = '';
  };

  window.panelCustomTitle = function (inp) {
    var val = inp.value.trim();
    _selectedTitle = val || null;
    document.querySelectorAll('.panel-title-chip').forEach(function (c) { c.classList.remove('selected'); });
  };

  window.panelSetTitle = function (pid) {
    // Fall back to custom input value if nothing is selected via chip
    var inp = document.getElementById('panel-custom-title');
    if (!_selectedTitle && inp) { _selectedTitle = inp.value.trim() || null; }
    if (!_selectedTitle) { alert('Pick a title or type your own.'); return; }
    var fd = new FormData();
    fd.append('title', _selectedTitle);
    fd.append('text_position', _thumbTextPosition);
    fetch('/project/' + pid + '/step1/set-title', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) { alert(j.error || 'Failed.'); return; }
        loadPanel(); startPolling();
      });
  };

  window.panelSetTextPosition = function (position) {
    _thumbTextPosition = position;
    document.querySelectorAll('.panel-pos-btn').forEach(function (b) { b.classList.remove('active'); });
    document.querySelectorAll('.panel-pos-btn').forEach(function (b) {
      if (b.textContent.trim().toLowerCase() === position) b.classList.add('active');
    });
  };

  window.panelUpdateThumbPosition = function (pid, title) {
    if (!title) { alert('No title found for this thumbnail.'); return; }
    var btns = Array.from(document.querySelectorAll('.panel-actions .btn'));
    btns.forEach(function (b) { b.disabled = true; });
    var fd = new FormData();
    fd.append('title', title);
    fd.append('text_position', _thumbTextPosition);
    fetch('/project/' + pid + '/step1/set-title', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) { alert(j.error || 'Failed.'); btns.forEach(function (b) { b.disabled = false; }); return; }
        loadPanel(); startPolling();
      })
      .catch(function (err) {
        alert('Failed: ' + err);
        btns.forEach(function (b) { b.disabled = false; });
      });
  };

  window.panelApproveStep1 = function (pid) {
    post('/project/' + pid + '/step1/approve').then(function (j) {
      if (j.ok) { loadPanel(); } else { alert(j.error); }
    });
  };

  window.panelRegenSlot = function (pid, slot) {
    var modelEl = f('panel-slot-' + slot + '-model');
    var model   = modelEl ? modelEl.value : (slot === 'a' ? 'kling_v16' : 'seedance_pro');
    post('/project/' + pid + '/step2/slot/' + slot, { model: model })
      .then(function (j) {
        if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error); }
      });
  };

  window.panelStartStep2 = function (pid) {
    post('/project/' + pid + '/step2/start')
      .then(function (j) {
        if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error); }
      });
  };

  window.panelApproveStep2 = function (pid, slot) {
    post('/project/' + pid + '/step2/approve', { slot: slot || 'a' }).then(function (j) {
      if (j.ok) { loadPanel(); } else { alert(j.error); }
    });
  };

  window.panelMusicTab = function (tab) {
    var bEl  = f('panel-beatoven-opts');
    var sEl  = f('panel-suno-opts');
    var bBtn = f('panel-tab-stable');
    var sBtn = f('panel-tab-suno');
    if (bEl)  bEl.style.display  = tab === 'suno' ? 'none' : '';
    if (sEl)  sEl.style.display  = tab === 'suno' ? '' : 'none';
    if (bBtn) bBtn.className = tab !== 'suno' ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm';
    if (sBtn) sBtn.className = tab === 'suno' ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm';
  };

  window.panelStartMusic = function (pid, provider) {
    var prompt = (f('panel-suno-prompt') || {value:'electronic, ambient, deep, slow, warm, instrumental'}).value.trim();
    if (!prompt) { alert('Enter a music style.'); return; }
    var data = { suno_prompt: prompt, provider: provider };
    if (provider !== 'suno') {
      data.track_count = (f('panel-track-count') || {value:'18'}).value;
    }
    post('/project/' + pid + '/step2b/start', data)
      .then(function (j) {
        if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error); }
      });
  };

  // Keep legacy alias in case anything still calls panelStartSuno
  window.panelStartSuno = function (pid) { window.panelStartMusic(pid, 'suno'); };

  window.panelStartStep3 = function (pid) {
    var count        = parseInt((f('panel-song-count')     || {value:'18'}).value) || 18;
    var crossfade    = parseFloat((f('panel-crossfade')    || {value:'2'}).value)  || 2;
    var overlayStyle = (f('panel-overlay-style') || {value:'default'}).value;
    post('/project/' + pid + '/step3/start', {
      song_count:    count,
      crossfade_sec: crossfade,
      overlay_style: overlayStyle,
    }).then(function (j) {
      if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error); }
    });
  };

  window.panelLoadTracks = function () {
    var el = f('panel-track-list');
    if (!el) return;
    fetch('/api/songs')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.files || !d.files.length) {
          el.innerHTML = '<p style="font-size:12px;color:var(--muted)">No MP3s in music/ folder yet.</p>';
          return;
        }
        var h = '<div style="font-size:11px;color:var(--muted);margin-bottom:6px">' + d.total + ' track' + (d.total !== 1 ? 's' : '') + '</div>';
        d.files.forEach(function (name) {
          var stem = name.replace(/\.mp3$/i, '').replace(/_/g, ' ');
          h += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;background:rgba(255,255,255,0.04);border-radius:6px;padding:6px 8px">';
          h += '<audio controls preload="none" style="flex:1;height:28px;min-width:0" src="/api/songs/' + encodeURIComponent(name) + '"></audio>';
          h += '<span style="font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px" title="' + esc(name) + '">' + esc(stem) + '</span>';
          h += '<button class="btn btn-ghost btn-sm" style="font-size:11px;padding:2px 6px;color:#e55;flex-shrink:0" onclick="panelDeleteTrack(\'' + esc(name) + '\')">&#10005;</button>';
          h += '</div>';
        });
        el.innerHTML = h;
      })
      .catch(function () {
        el.innerHTML = '<p style="font-size:12px;color:#e55">Failed to load tracks.</p>';
      });
  };

  window.panelDeleteTrack = function (name) {
    if (!confirm('Remove "' + name + '" from the music folder?')) return;
    fetch('/api/songs/' + encodeURIComponent(name) + '/delete', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.ok) { window.panelLoadTracks(); } else { alert(j.error); }
      });
  };

  window.panelDelete = function (pid) {
    if (!confirm('Delete this project? This cannot be undone.')) return;
    var fd = new FormData();
    fetch('/project/' + pid + '/delete', {
      method: 'POST',
      body: fd,
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function () { closePanel(); location.reload(); });
  };

  // ── Step 3 approve → Step 4 ───────────────────────────────────────────────
  window.panelApproveStep3 = function (pid) {
    post('/project/' + pid + '/step3/approve').then(function (j) {
      if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error); }
    });
  };

  window.panelRerenderStep3 = function (pid) {
    if (!confirm('Re-render the 1-hour video?\n\nThis will overwrite the existing render and use the approved animated loop as the background.')) return;
    post('/project/' + pid + '/step3/rerender').then(function (j) {
      if (j.ok) { loadPanel(); }
      else { alert(j.error || 'Could not reset Step 3.'); }
    });
  };

  // ── Step 4 actions ────────────────────────────────────────────────────────

  window.panelCheckOauth = function () {
    fetch('/api/oauth/status')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var btn = f('panel-oauth-btn');
        var row = f('panel-oauth-row');
        if (!row) return;
        if (d.connected) {
          row.innerHTML = '<span class="panel-oauth-label">YouTube Account</span>' +
            '<span class="panel-oauth-connected">&#10003; Connected</span>' +
            '<button class="btn btn-ghost btn-sm" onclick="panelDisconnectOauth()">Disconnect</button>';
        } else if (!d.configured) {
          row.innerHTML = '<span class="panel-oauth-label">YouTube Account</span>' +
            '<span class="panel-oauth-error">Add youtube_oauth credentials to config.json</span>';
        } else {
          row.innerHTML = '<span class="panel-oauth-label">YouTube Account</span>' +
            '<a href="/oauth/start" class="btn btn-primary btn-sm">Connect YouTube</a>';
        }
      });
  };

  window.panelDisconnectOauth = function () {
    if (!confirm('Disconnect YouTube account?')) return;
    post('/oauth/revoke').then(function () { loadPanel(); });
  };

  window.panelGenerateSeo = function (pid) {
    post('/project/' + pid + '/step4/generate-seo').then(function (j) {
      if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error); }
    });
  };

  window.panelSaveSeo = function (pid) {
    var title = (f('seo-title')  || {value: ''}).value.trim();
    var desc  = (f('seo-desc')   || {value: ''}).value;
    var tags  = (f('seo-tags')   || {value: ''}).value.trim();
    post('/project/' + pid + '/step4/save-seo', {
      seo_title:       title,
      seo_description: desc,
      seo_tags:        tags,
    }).then(function (j) {
      if (!j.ok) { alert(j.error); }
    });
  };

  window.panelSuggestSlot = function (pid) {
    fetch('/api/suggest-slot')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        var el = f('panel-publish-at');
        if (el) {
          el.value = d.slot.replace('Z', '').slice(0, 16);
        }
        // Auto-save so the user doesn't have to click "Set" separately
        if (pid) {
          var val = d.slot; // already UTC ISO with Z
          post('/project/' + pid + '/step4/set-publish-time', { publish_at: val })
            .then(function (j) { if (j.ok) { loadPanel(); } });
        }
      });
  };

  window.panelSetPublishTime = function (pid) {
    var el = f('panel-publish-at');
    if (!el) return;
    // Convert datetime-local back to UTC ISO (append Z)
    var val = el.value ? el.value + ':00Z' : '';
    post('/project/' + pid + '/step4/set-publish-time', { publish_at: val })
      .then(function (j) {
        if (!j.ok) { alert(j.error); }
        else { loadPanel(); }
      });
  };

  window.panelClearPublishTime = function (pid) {
    post('/project/' + pid + '/step4/set-publish-time', { publish_at: '' })
      .then(function (j) {
        if (j.ok) { loadPanel(); }
      });
  };

  window.panelGoBack = function (pid, step) {
    var labels = {1: 'Thumbnail', 2: 'Video Loop', 3: 'Render 1-Hour'};
    if (!confirm('Go back to Step ' + step + ' (' + (labels[step] || step) + ')?\n\nYour existing files are kept — you can redo or re-approve this step.')) return;
    post('/project/' + pid + '/go-back/' + step)
      .then(function (j) {
        if (j.ok) { loadPanel(); }
        else { alert(j.error || 'Could not go back.'); }
      });
  };

  window.panelSyncYtDate = function (pid) {
    post('/project/' + pid + '/sync-yt-date')
      .then(function (j) {
        if (j.ok) {
          alert('Calendar date synced to ' + j.scheduled_date + ' from YouTube.');
          loadPanel();
          location.reload();   // refresh calendar chips
        } else {
          alert('Sync failed: ' + (j.error || 'Unknown error'));
        }
      });
  };

  window.panelUpload = function (pid) {
    var el  = f('panel-publish-at');
    var raw = el ? el.value.trim() : '';

    if (!raw) {
      // No publish time → video goes live immediately as public
      if (!confirm('No publish time is set.\n\nThis video will go LIVE on YouTube immediately (public).\n\nContinue?')) return;
      post('/project/' + pid + '/step4/upload').then(function (j) {
        if (j.ok) { loadPanel(); startPolling(); } else { alert(j.error || 'Upload failed.'); }
      });
    } else {
      // Auto-save the current publish time before uploading
      if (!confirm('Upload this video to YouTube now?\n\nIt will be scheduled as private and go live at the selected time.')) return;
      var val = raw.slice(0, 16) + ':00Z';  // ensure "YYYY-MM-DDTHH:MM:00Z"
      post('/project/' + pid + '/step4/set-publish-time', { publish_at: val })
        .then(function (j) {
          if (!j.ok) { alert(j.error || 'Could not save publish time.'); return; }
          post('/project/' + pid + '/step4/upload').then(function (j2) {
            if (j2.ok) { loadPanel(); startPolling(); } else { alert(j2.error || 'Upload failed.'); }
          });
        });
    }
  };

  window.panelResetTask = function (pid) {
    stopPolling();
    post('/project/' + pid + '/reset-task').then(function (j) {
      if (j.ok) { loadPanel(); } else { alert(j.error || 'Could not reset — task may still be running.'); }
    });
  };

  // Restore view preference from localStorage on page load
  (function () {
    var saved = localStorage.getItem('ufz-view') || 'calendar';
    if (saved === 'list') {
      setTimeout(function () { setView('list'); }, 0);
    }
  })();

  // ── Calendar accordion toggle ──────────────────────────────────────────────
  window.toggleCalendar = function () {
    var body  = f('cal-accordion-body');
    var arrow = f('cal-arrow');
    if (!body) return;
    var isNowHidden = body.classList.toggle('hidden');
    if (arrow) arrow.classList.toggle('open', !isNowHidden);
    localStorage.setItem('ufz-cal-open', isNowHidden ? '0' : '1');
  };

  // Restore calendar open/closed state on load
  (function () {
    if (localStorage.getItem('ufz-cal-open') === '1') {
      var body  = f('cal-accordion-body');
      var arrow = f('cal-arrow');
      if (body)  body.classList.remove('hidden');
      if (arrow) arrow.classList.add('open');
    }
  })();

})();

