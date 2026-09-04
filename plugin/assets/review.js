/**
 * Empornium Megapack Builder Review & Staging Interface
 * Handles scene inspection, drag-and-drop ordering, filesystem probing,
 * GraphQL file consolidation, megapack building, and BBCode generation.
 */

(function () {
  "use strict";

  const PLUGIN_ID = "empornium-megapack";
  // keep in sync with backend app.version (main.py FastAPI version) — bump when backend bumps
  const EXPECTED_SIDECAR_VERSION = "0.2.0";
  let currentMode = "megapack"; // "megapack" | "single"
  let hasUserEditedTitle = false;
  let scenes = [];
  let excludedSceneIds = new Set(); // scene ids removed from the pack by the user
  let duplicateGroups = []; // [{ key, index, label, members: [{sceneId, fileId, path, basename}] }]
  let duplicateBySceneId = new Map(); // sceneId -> { groupIndex, label, ordinal, total, key, basename }
  let selectedFileBySceneId = new Map(); // sceneId -> fileId / filePath
  let sceneSuperioritiesMap = new Map(); // sceneId -> [{ type, label, title }]
  let consolidatedFileIds = new Set(); // file ids successfully moved to output_dir
  let missingSourceSceneIds = new Set(); // sceneId (string) -> no known file exists on disk, per reconcileSourceFiles()
  let showOnlyConflicts = false;
  let probeResultsMap = {};
  let activeJobId = null;
  let activeRunId = null;
  let bufferedLogs = [];
  let wsLogStreamActive = false;
  // Set once a build finishes: the preview then holds the backend's BBCode
  // (image block included) and must not be overwritten by the local composer.
  let bbcodeIsFinal = false;
  let activeWs = null;
  let wsWatchdog = null;
  let activePollingJobId = null;
  let activePollInterval = null;
  let currentCoverUrl = null;
  const handledJobIds = new Set();
  let uiBusy = false;            // true from click until the operation reaches a terminal state
  let busyOperation = null;      // "build" | "mutation" — the lock tier
  let busyLabel = null;          // human label, e.g. "BuildMegapack"
  let busyAwaitingJob = false;   // a Stash job was dispatched; unlock is owned by handleJobUpdate
  let busyStartedAt = 0;         // Date.now() at lock, for the escape hatch
  let busyEscapeTimer = null;
  let cachedVocabulary = null;
  let vocabularyFetchAttempted = false;
  let vocabularyFetchFailed = false;

  // 1. Get Scene IDs from Query Parameters or Token Resolution
  const urlParams = new URLSearchParams(window.location.search);
  const tokenParam = urlParams.get("token") || "";
  const sceneIdsParam = urlParams.get("scenes") || "";
  const modeParam = urlParams.get("mode") || "";
  let sceneIds = [];
  let bbcodeUserEdited = false;
  let savedSelection = { start: 0, end: 0 };
  let savedScrollTop = 0;
  let currentPopoverTag = null;
  // Last BBCode written programmatically, so "reset" can put it back.
  let lastFinalBBCode = null;

  // updateBBCode() stops regenerating as soon as bbcodeUserEdited latches.
  // That is deliberate -- it is what stops a scene-selection change wiping
  // the user's edit -- but silently freezing the preview reads as a bug, so
  // say so on screen and offer the way back.
  function refreshBBCodeEditedNotice() {
    const notice = document.getElementById("bbcode-edited-notice");
    if (!notice) return;
    if (!bbcodeUserEdited) {
      notice.style.display = "none";
      return;
    }
    const label = document.getElementById("bbcode-edited-notice-text");
    const btn = document.getElementById("btn-bbcode-reset");
    if (label) {
      label.innerText = bbcodeIsFinal
        ? "✏️ Edited — this no longer matches the build output."
        : "✏️ Edited — the live preview has stopped updating.";
    }
    if (btn) {
      btn.innerText = bbcodeIsFinal ? "Restore build output" : "Resume live preview";
    }
    notice.style.display = "flex";
  }

  function markBBCodeEdited() {
    bbcodeUserEdited = true;
    refreshBBCodeEditedNotice();
  }

  // Discard the user's edits and go back to the generated text. Writes via
  // .value on purpose: that fires no input event, so the latch stays clear.
  function resetBBCodeToGenerated() {
    bbcodeUserEdited = false;
    const previewEl = document.getElementById("bbcode-preview");
    if (bbcodeIsFinal && lastFinalBBCode !== null) {
      if (previewEl) previewEl.value = lastFinalBBCode;
    } else {
      updateBBCode();
    }
    refreshBBCodeEditedNotice();
  }

  function initBBCodeToolbar() {
    const textarea = document.getElementById("bbcode-preview");
    if (!textarea || textarea.dataset.toolbarBound) return;
    textarea.dataset.toolbarBound = "true";

    textarea.addEventListener("input", () => {
      markBBCodeEdited();
      updateSavedSelection();
    });

    const resetBtn = document.getElementById("btn-bbcode-reset");
    if (resetBtn && !resetBtn.dataset.bound) {
      resetBtn.dataset.bound = "true";
      resetBtn.addEventListener("click", resetBBCodeToGenerated);
    }

    const updateSavedSelection = () => {
      savedSelection.start = textarea.selectionStart;
      savedSelection.end = textarea.selectionEnd;
    };

    textarea.addEventListener("keyup", updateSavedSelection);
    textarea.addEventListener("mouseup", updateSavedSelection);
    textarea.addEventListener("select", updateSavedSelection);
    textarea.addEventListener("focus", () => {
      if (popover && popover.style.display !== "none") {
        popover.style.display = "none";
        currentPopoverTag = null;
      }
      updateSavedSelection();
    });

    document.addEventListener("selectionchange", () => {
      if (document.activeElement === textarea) {
        updateSavedSelection();
      }
    });

    function applyBBCodeFormat(openTag, closeTag) {
      markBBCodeEdited();
      if (popover && popover.style.display !== "none") {
        popover.style.display = "none";
        currentPopoverTag = null;
      }

      const wasFocused = document.activeElement === textarea;
      textarea.focus({ preventScroll: true });

      let start = textarea.selectionStart;
      let end = textarea.selectionEnd;
      if (!wasFocused) {
        start = savedSelection.start;
        end = savedSelection.end;
        textarea.setSelectionRange(start, end);
      }

      const val = textarea.value;
      const selected = val.substring(start, end);
      const scrollTop = savedScrollTop !== undefined && !wasFocused ? savedScrollTop : textarea.scrollTop;

      if (openTag === "[hr]") {
        const replacement = "[hr]";
        document.execCommand("insertText", false, replacement);
        const caret = start + replacement.length;
        textarea.setSelectionRange(caret, caret);
      } else if (openTag === "[list]") {
        const trimmedSelected = selected.trim();
        if (trimmedSelected.length > 0) {
          const lines = trimmedSelected.split(/\r?\n/);
          const formatted = lines.map((line) => {
            const trimmed = line.trim();
            if (!trimmed) return "";
            return trimmed.startsWith("[*]") ? trimmed : `[*]${trimmed}`;
          }).join("\n");
          const replacement = `[list]\n${formatted}\n[/list]`;
          document.execCommand("insertText", false, replacement);
          const newStart = start + 7;
          const newEnd = newStart + formatted.length;
          textarea.setSelectionRange(newStart, newEnd);
        } else {
          const replacement = "[list]\n[*]\n[/list]";
          document.execCommand("insertText", false, replacement);
          const caret = start + 10;
          textarea.setSelectionRange(caret, caret);
        }
      } else if (selected.length > 0) {
        // Selection wrapping: wrap selection and keep text selected
        const replacement = `${openTag}${selected}${closeTag}`;
        document.execCommand("insertText", false, replacement);
        const newStart = start + openTag.length;
        const newEnd = newStart + selected.length;
        textarea.setSelectionRange(newStart, newEnd);
      } else {
        // Empty selection: insert tag pair and place caret between them
        const replacement = `${openTag}${closeTag}`;
        document.execCommand("insertText", false, replacement);
        const caret = start + openTag.length;
        textarea.setSelectionRange(caret, caret);
      }

      textarea.scrollTop = scrollTop;
      savedScrollTop = undefined;
      updateSavedSelection();
    }

    // Simple tag buttons
    const simpleTags = ["b", "i", "u", "s", "center", "img", "quote", "code", "hr", "list"];
    simpleTags.forEach((tag) => {
      const btn = document.getElementById(`btn-tag-${tag}`);
      if (btn) {
        btn.addEventListener("mousedown", (e) => e.preventDefault());
        btn.addEventListener("click", () => applyBBCodeFormat(`[${tag}]`, `[/${tag}]`));
      }
    });

    // Fixed choice selects: size & color
    const sizeSelect = document.getElementById("toolbar-size");
    if (sizeSelect) {
      sizeSelect.addEventListener("change", () => {
        const val = sizeSelect.value;
        if (val) {
          applyBBCodeFormat(`[size=${val}]`, "[/size]");
          sizeSelect.selectedIndex = 0;
        }
      });
    }

    const colorSelect = document.getElementById("toolbar-color");
    if (colorSelect) {
      colorSelect.addEventListener("change", () => {
        const val = colorSelect.value;
        if (val) {
          applyBBCodeFormat(`[color=${val}]`, "[/color]");
          colorSelect.selectedIndex = 0;
        }
      });
    }

    // Popover for url and spoiler
    const popover = document.getElementById("toolbar-popover");
    const popoverLabel = document.getElementById("toolbar-popover-label");
    const popoverInput = document.getElementById("toolbar-popover-input");
    const popoverConfirm = document.getElementById("toolbar-popover-confirm");
    const popoverCancel = document.getElementById("toolbar-popover-cancel");

    function openPopover(tag) {
      currentPopoverTag = tag;
      if (document.activeElement === textarea) {
        updateSavedSelection();
      }
      savedScrollTop = textarea.scrollTop;
      if (tag === "url") {
        if (popoverLabel) popoverLabel.textContent = "URL:";
        if (popoverInput) popoverInput.placeholder = "https://...";
      } else if (tag === "spoiler") {
        if (popoverLabel) popoverLabel.textContent = "Spoiler Title:";
        if (popoverInput) popoverInput.placeholder = "Optional title...";
      }
      if (popoverInput) popoverInput.value = "";
      if (popover) popover.style.display = "flex";
      if (popoverInput) popoverInput.focus();
    }

    function closePopover() {
      if (popover) popover.style.display = "none";
      currentPopoverTag = null;
      textarea.focus({ preventScroll: true });
      textarea.setSelectionRange(savedSelection.start, savedSelection.end);
      if (savedScrollTop !== undefined) {
        textarea.scrollTop = savedScrollTop;
      }
    }

    function submitPopover() {
      const tag = currentPopoverTag;
      const rawVal = popoverInput ? popoverInput.value : "";
      const val = rawVal.replace(/\s+/g, " ").trim();
      if (popover) popover.style.display = "none";
      currentPopoverTag = null;

      textarea.focus({ preventScroll: true });
      textarea.setSelectionRange(savedSelection.start, savedSelection.end);

      if (tag === "url") {
        const openTag = val ? `[url=${val}]` : "[url]";
        applyBBCodeFormat(openTag, "[/url]");
      } else if (tag === "spoiler") {
        const openTag = val ? `[spoiler=${val}]` : "[spoiler]";
        applyBBCodeFormat(openTag, "[/spoiler]");
      }
    }

    const urlBtn = document.getElementById("btn-tag-url");
    if (urlBtn) {
      urlBtn.addEventListener("mousedown", (e) => e.preventDefault());
      urlBtn.addEventListener("click", () => openPopover("url"));
    }

    const spoilerBtn = document.getElementById("btn-tag-spoiler");
    if (spoilerBtn) {
      spoilerBtn.addEventListener("mousedown", (e) => e.preventDefault());
      spoilerBtn.addEventListener("click", () => openPopover("spoiler"));
    }

    if (popoverConfirm) {
      popoverConfirm.addEventListener("click", submitPopover);
    }

    if (popoverCancel) {
      popoverCancel.addEventListener("click", closePopover);
    }

    if (popoverInput) {
      popoverInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          submitPopover();
        } else if (e.key === "Escape") {
          e.preventDefault();
          closePopover();
        }
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBBCodeToolbar);
  } else {
    initBBCodeToolbar();
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function generateRunId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return "run-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  // Mirrors metadata.empify() in backend/empornium_megapack/metadata.py exactly:
  // Unicode-aware strip of non [letter/digit/_/./space/-] (removed chars are NOT
  // separators), separator runs -> ".", trim edge dots, 32-char cap.
  // Parity-pinned by plugin/tests/test_empify_parity.spec.mjs.
  function empifyTag(raw) {
    return String(raw || "")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}_.\s-]+/gu, "")
      .replace(/[\s._-]+/g, ".")
      .replace(/^\.+|\.+$/g, "")
      .slice(0, 32);
  }

  // Change C: Empornium tag vocabulary resolution (mirrors backend tags.py resolve_tags)
  function resolveTags(sources, vocab = cachedVocabulary) {
    const tags = [];
    const unmapped = [];
    const ignored = [];

    const hasVocab = Boolean(vocab && vocab.map && (vocab.ignored instanceof Set || Array.isArray(vocab.ignored)));
    const ignoredSet = hasVocab
      ? (vocab.ignored instanceof Set ? vocab.ignored : new Set(vocab.ignored.map((s) => String(s).toLowerCase().trim())))
      : null;

    for (const src of sources || []) {
      const val = typeof src?.value === "string" ? src.value.trim() : (typeof src === "string" ? src.trim() : "");
      const kind = src?.kind || "scene_tag";
      if (!val) continue;

      if (kind === "performer" || kind === "studio" || kind === "derived") {
        const emp = empifyTag(val);
        if (emp) tags.push(emp);
      } else if (kind === "scene_tag") {
        if (!hasVocab) {
          // Unfiltered fallback when vocabulary is unavailable (degrade visibly)
          const emp = empifyTag(val);
          if (emp) tags.push(emp);
        } else {
          const lower = val.toLowerCase();
          if (ignoredSet && ignoredSet.has(lower)) {
            ignored.push(src.value || val);
          } else if (vocab.map && Object.prototype.hasOwnProperty.call(vocab.map, lower)) {
            const mapped = vocab.map[lower];
            const tokens = Array.isArray(mapped)
              ? mapped
              : String(mapped).trim().split(/\s+/);
            for (const tok of tokens) {
              const cleanedTok = tok.trim();
              if (cleanedTok) tags.push(cleanedTok);
            }
          } else {
            unmapped.push(src.value || val);
          }
        }
      } else {
        unmapped.push(src.value || val);
      }
    }

    const uniqueTags = [...new Set(tags)].sort().slice(0, 60);
    const uniqueUnmapped = [...new Set(unmapped)];
    const uniqueIgnored = [...new Set(ignored)];

    return {
      tags: uniqueTags,
      unmapped: uniqueUnmapped,
      ignored: uniqueIgnored,
    };
  }

  // Change C: Fetch vocabulary once on load via backendEndpoints helper
  async function fetchVocabulary() {
    if (cachedVocabulary) return cachedVocabulary;
    vocabularyFetchAttempted = true;
    const endpoints = backendEndpoints("/api/tags/vocabulary");
    for (const url of endpoints) {
      try {
        const response = await fetch(url);
        if (response.ok) {
          const data = await response.json();
          if (data && typeof data === "object" && data.map && Array.isArray(data.ignored)) {
            cachedVocabulary = {
              map: data.map,
              ignored: new Set(data.ignored.map((s) => String(s).toLowerCase().trim())),
            };
            vocabularyFetchFailed = false;
            updateBBCode();
            return cachedVocabulary;
          }
        }
      } catch (err) {
        // endpoint unreachable, try next candidate
      }
    }
    vocabularyFetchFailed = true;
    updateBBCode();
    return null;
  }

  function findFailureSentinel(logs, runId) {
    if (!logs || !Array.isArray(logs) || !runId) return null;
    const target = `EMPORNIUM_TASK_FAILED ${runId}`;
    for (const log of logs) {
      if (!log) continue;
      const isError = log.level === "Error" || String(log.level || "").toUpperCase() === "ERROR";
      if (isError && typeof log.message === "string" && log.message.includes(target)) {
        const idx = log.message.indexOf(target);
        const afterTarget = log.message.substring(idx + target.length);
        const colonIdx = afterTarget.indexOf(":");
        let extractedError = "";
        if (colonIdx !== -1) {
          extractedError = afterTarget.substring(colonIdx + 1).trim();
        } else {
          extractedError = afterTarget.trim();
        }
        return extractedError || "Task execution failed on backend.";
      }
    }
    return null;
  }

  // Success-side counterpart to findFailureSentinel. Stash's job API does not
  // expose plugin stdout, so the backend publishes its result as a log line.
  function findResultSentinel(logs, runId) {
    if (!logs || !Array.isArray(logs) || !runId) return null;
    const target = `EMPORNIUM_TASK_RESULT ${runId}: `;
    // Walk backwards: the newest matching line wins if a run somehow repeats.
    for (let i = logs.length - 1; i >= 0; i--) {
      const log = logs[i];
      if (!log || typeof log.message !== "string") continue;
      const idx = log.message.indexOf(target);
      if (idx === -1) continue;
      const json = log.message.substring(idx + target.length).trim();
      try {
        const parsed = JSON.parse(json);
        if (parsed && typeof parsed === "object") return parsed;
      } catch (err) {
        console.warn("Could not parse EMPORNIUM_TASK_RESULT sentinel", err);
      }
      return null;
    }
    return null;
  }

  // Reassembles chunked base64 BBCode published on EMPORNIUM_TASK_BBCODE lines.
  // Large megapacks exceed single-line log budgets; this chunked stream is the
  // authoritative channel for the final rendered BBCode.
  function findBBCodeSentinel(logs, runId) {
    if (!logs || !Array.isArray(logs) || !runId) return null;
    const target = `EMPORNIUM_TASK_BBCODE ${runId} `;
    const chunks = {};
    let totalChunks = null;

    for (let i = 0; i < logs.length; i++) {
      const log = logs[i];
      if (!log || typeof log.message !== "string") continue;
      const idx = log.message.indexOf(target);
      if (idx === -1) continue;
      const rest = log.message.substring(idx + target.length).trim();
      const colonIdx = rest.indexOf(":");
      if (colonIdx === -1) continue;
      const ratioPart = rest.substring(0, colonIdx).trim();
      const chunkData = rest.substring(colonIdx + 1).trim();
      const parts = ratioPart.split("/");
      if (parts.length !== 2) continue;
      const curIndex = parseInt(parts[0], 10);
      const total = parseInt(parts[1], 10);
      if (isNaN(curIndex) || isNaN(total) || total <= 0) continue;
      if (totalChunks === null) {
        totalChunks = total;
      } else if (totalChunks !== total) {
        continue;
      }
      chunks[curIndex] = chunkData;
    }

    if (!totalChunks) return null;
    for (let i = 1; i <= totalChunks; i++) {
      if (typeof chunks[i] !== "string") {
        return null;
      }
    }

    try {
      let b64Str = "";
      for (let i = 1; i <= totalChunks; i++) {
        b64Str += chunks[i];
      }
      const binStr = atob(b64Str);
      const bytes = Uint8Array.from(binStr, (c) => c.charCodeAt(0));
      return new TextDecoder().decode(bytes);
    } catch (err) {
      console.warn("Could not decode EMPORNIUM_TASK_BBCODE chunks", err);
      return null;
    }
  }

  // GraphQL Helper
  async function executeGraphQL(query, variables = {}) {
    const response = await fetch("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables })
    });
    const resJson = await response.json();
    if (resJson.errors) {
      throw new Error(resJson.errors.map((e) => e.message).join("; "));
    }
    return resJson.data;
  }

  // Backend sidecar endpoint resolution (port 9941). The sidecar binds
  // 127.0.0.1 only, so every candidate targets the loopback explicitly:
  // hostname-derived URLs break when Stash is served from a non-loopback host
  // (and violate the CSP connect-src allowlist, which empornium-megapack.yml
  // restricts to 127.0.0.1:9941 / localhost:9941). Stash's /plugin/{id}/
  // route serves static assets only — it has never proxied the sidecar API.
  function backendEndpoints(apiPath) {
    return [
      `http://127.0.0.1:9941${apiPath}`,
      `http://localhost:9941${apiPath}`
    ];
  }

  // Token Resolution Helper
  async function resolveToken(token) {
    const endpoints = backendEndpoints(`/api/token/${encodeURIComponent(token)}`);

    let lastError = null;
    for (const url of endpoints) {
      try {
        const response = await fetch(url);
        if (response.ok) {
          const data = await response.json();
          if (data && Array.isArray(data.sceneIds)) {
            return data.sceneIds
              .map((id) => parseInt(id, 10))
              .filter((id) => !isNaN(id) && id > 0);
          }
        } else if (response.status === 404) {
          throw new Error("token not found (HTTP 404)");
        }
      } catch (err) {
        lastError = err;
        if (err.message && err.message.includes("404")) {
          throw err;
        }
      }
    }
    throw lastError || new Error("Failed to resolve token");
  }

  // Chunked Scene Fetching (25 IDs per batch)
  async function fetchScenesChunked(ids) {
    if (!ids || ids.length === 0) return [];

    const CHUNK_SIZE = 25;
    const allScenes = [];

    const FIND_SCENES_QUERY = `
      query FindScenes($ids: [Int!]) {
        findScenes(scene_filter: { id: { value: $ids, modifier: IN_LIST } }) {
          scenes {
            id
            title
            details
            date
            paths {
              screenshot
              preview
            }
            files {
              id
              path
              size
              height
              width
              duration
              video_codec
              oshash: fingerprint(type: "oshash")
            }
            performers {
              id
              name
            }
            tags {
              id
              name
            }
            studio {
              id
              name
            }
          }
        }
      }
    `;

    const FIND_SCENE_QUERY = `
      query FindScene($id: ID!) {
        findScene(id: $id) {
          id
          title
          details
          date
          paths {
            screenshot
            preview
          }
          files {
            id
            path
            size
            height
            width
            duration
            video_codec
            oshash: fingerprint(type: "oshash")
          }
          performers {
            id
            name
          }
          tags {
            id
            name
          }
          studio {
            id
            name
          }
        }
      }
    `;

    for (let i = 0; i < ids.length; i += CHUNK_SIZE) {
      const chunk = ids.slice(i, i + CHUNK_SIZE);
      let batchSuccess = false;
      let batchError = null;
      try {
        const data = await executeGraphQL(FIND_SCENES_QUERY, { ids: chunk });
        const scs = data?.findScenes?.scenes;
        if (Array.isArray(scs)) {
          for (const s of scs) {
            if (s) allScenes.push(s);
          }
          batchSuccess = true;
        }
      } catch (e) {
        batchError = e;
        batchSuccess = false;
      }

      if (!batchSuccess) {
        if (batchError) {
          const msg = batchError.message || "";
          const isSchemaMismatch = msg.includes("GRAPHQL_VALIDATION_FAILED") || msg.includes("IN_LIST") || msg.includes("Cannot query field \"findScenes\"");
          if (!isSchemaMismatch) {
            throw batchError;
          }
        }

        const singleResults = await Promise.all(
          chunk.map(async (id) => {
            try {
              const data = await executeGraphQL(FIND_SCENE_QUERY, { id: String(id) });
              return data?.findScene || null;
            } catch (e) {
              return null;
            }
          })
        );
        for (const sc of singleResults) {
          if (sc) allScenes.push(sc);
        }
      }
    }

    // Preserve requested ordering and deduplicate by ID
    const idSet = new Set(ids.map((id) => String(id)));
    const idMap = new Map();
    for (const sc of allScenes) {
      if (sc && idSet.has(String(sc.id)) && !idMap.has(String(sc.id))) {
        idMap.set(String(sc.id), sc);
      }
    }

    const orderedScenes = [];
    for (const id of ids) {
      const s = idMap.get(String(id));
      if (s) {
        orderedScenes.push(s);
      }
    }

    return orderedScenes;
  }

  // 2. Helper Functions for Paths, Active Scenes, Modes and Collision Detection
  function normalizeDirPath(p) {
    if (!p) return "";
    let norm = p.trim().replace(/[\/\\]+/g, "/").toLowerCase();
    if (norm.endsWith("/") && norm.length > 1 && !/^[a-z]:\/$/.test(norm)) {
      norm = norm.slice(0, -1);
    }
    return norm;
  }

  function getParentDir(filePath) {
    if (!filePath) return "";
    let norm = filePath.trim().replace(/[\/\\]+/g, "/").toLowerCase();
    const lastSlash = norm.lastIndexOf("/");
    if (lastSlash === -1) return "";
    if (lastSlash === 0) return "/";
    if (lastSlash === 2 && /^[a-z]:$/.test(norm.slice(0, 2))) {
      return norm.slice(0, 3);
    }
    return normalizeDirPath(norm.slice(0, lastSlash));
  }

  // Contract mirrors the backend's _is_under (task.py): recursive containment,
  // case-insensitive, SEGMENT-BOUNDARY prefix ("D:\Media" is not a prefix of
  // "D:\Media2\file.mp4"); equality counts as under. Keep both in sync.
  function isPathUnderSeed(childPath, seedDir) {
    const seedNorm = normalizeDirPath(seedDir);
    if (!seedNorm) return false;
    const child = (childPath || "").trim().replace(/[\/\\]+/g, "/").toLowerCase();
    if (!child) return false;
    if (child === seedNorm) return true;
    const seedNoTrail = seedNorm.length > 1 && seedNorm.endsWith("/") ? seedNorm.slice(0, -1) : seedNorm;
    return child.startsWith(`${seedNoTrail}/`);
  }

  const _RESERVED_WINDOWS = new Set([
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
  ]);
  const _INVALID_CHARS_RE = /[<>:"/\\|?*\x00-\x1f]/g;

  function sanitizeName(name, maxLen = 120) {
    if (typeof name !== "string") {
      name = String(name || "");
    }
    let cleaned = name.replace(_INVALID_CHARS_RE, "_").replace(/^[. ]+|[. ]+$/g, "");
    cleaned = cleaned.replace(/\s+/g, " ");
    if (!cleaned) {
      cleaned = "Untitled";
    }
    if (_RESERVED_WINDOWS.has(cleaned.toUpperCase())) {
      cleaned = "_" + cleaned;
    }
    if (cleaned.length > maxLen) {
      const lastDot = cleaned.lastIndexOf(".");
      if (lastDot !== -1) {
        const head = cleaned.slice(0, lastDot);
        const ext = cleaned.slice(lastDot + 1);
        if (ext && ext.length <= 16) {
          cleaned = head.slice(0, maxLen - 1 - ext.length) + "." + ext;
        } else {
          cleaned = cleaned.slice(0, maxLen);
        }
      } else {
        cleaned = cleaned.slice(0, maxLen);
      }
    }
    return cleaned.replace(/^[. ]+|[. ]+$/g, "") || "Untitled";
  }

  function getPackDestinationFolder(outputDir, title) {
    const rawOut = (outputDir || document.getElementById("output-dir")?.value || "").trim();
    if (!rawOut) return "";
    const isSingle = currentMode === "single";
    const rawTitle = (title || document.getElementById("pack-title")?.value || "").trim();
    const safeTitle = sanitizeName(rawTitle);
    const sep = rawOut.includes("/") && !rawOut.includes("\\") ? "/" : "\\";
    const cleanOut = rawOut.replace(/[\\/]+$/, "");
    const lastSeg = cleanOut.split(/[\\/]/).pop() || "";
    if (lastSeg.toLowerCase() === safeTitle.toLowerCase()) {
      return cleanOut;
    }
    return `${cleanOut}${sep}${safeTitle}`;
  }

  // Destination collision discovery (Stash-side metadata ENRICHMENT only —
  // the filesystem probe in pathExistsBatch is authoritative for existence).
  // The GraphQL `path INCLUDES` filter is substring matching and also matches
  // sibling folders (D:\Packs-old\) and subfolders, so the client-side filter
  // below requires BOTH a case-insensitive basename collision AND the
  // candidate's parent directory to equal the normalized destination; a file
  // outside the destination must never be offered for Replace.
  async function findDestinationCollisions(fileItems, destinationFolder) {
    const destNorm = normalizeDirPath(destinationFolder);
    if (!destNorm) return [];

    const query = `
      query FindDestinationCollisions($path: String!) {
        findScenes(
          scene_filter: { path: { value: $path, modifier: INCLUDES } }
          filter: { per_page: 1000 }
        ) {
          scenes {
            id
            title
            files {
              id
              path
              size
              duration
              height
              width
              video_codec
              oshash: fingerprint(type: "oshash")
            }
          }
        }
      }
    `;
    const data = await executeGraphQL(query, { path: destinationFolder });
    const found = (data && data.findScenes && data.findScenes.scenes) || [];

    const ownFileIds = new Set(fileItems.map((item) => String(item.id)));
    const incomingBasenames = new Set(
      fileItems.map((item) => ((item.path || "").split(/[\\/]/).pop() || "").toLowerCase())
    );

    const collisions = [];
    for (const scene of found) {
      if (!scene || !Array.isArray(scene.files)) continue;
      for (const file of scene.files) {
        if (!file || file.path == null) continue;
        if (ownFileIds.has(String(file.id))) continue;
        if (getParentDir(file.path) !== destNorm) continue;
        const bname = file.path.split(/[\\/]/).pop() || "";
        if (!incomingBasenames.has(bname.toLowerCase())) continue;
        // sceneFiles is carried so the caller can tell whether `file` is the
        // scene's PRIMARY file. Stash refuses `deleteFiles` on a primary file
        // ("cannot delete primary file <path>"), and Scene.files is returned
        // primary-first, so files[0] identifies it.
        collisions.push({ sceneId: scene.id, sceneTitle: scene.title, file, sceneFiles: scene.files });
      }
    }
    return collisions;
  }

  // Filesystem existence probe against the backend sidecar (POST /api/fs/exists).
  // Chunked at 100 paths per request (the endpoint's own bound; packs support
  // up to 200 scenes — one unchunked call would fail the whole pre-check).
  // FAIL-CLOSED: any non-200, network error, or malformed response throws —
  // a probe failure must NEVER be interpreted as "does not exist", or a
  // rename could clobber an occupied name.
  // Turn the recorded per-attempt results into ONE actionable message,
  // chosen by precedence:
  //   1. HTTP 404  → sidecar is running but outdated (does not serve
  //      /api/fs/exists); remediation: restart via start_backend.ps1.
  //   2. Network   → sidecar unreachable; remediation: start_backend.ps1.
  //   3. Other HTTP status → surfaced with the failing URL.
  //   4. Malformed body → otherwise-unclassified 200 responses.
  function classifyProbeFailure(attempts) {
    const notFound = attempts.find((a) => a.kind === "http" && a.status === 404);
    if (notFound) {
      return new Error(
        `Filesystem probe failed: the sidecar answered 404 at ${notFound.url} — the running sidecar is outdated and does not serve /api/fs/exists. Restart it with start_backend.ps1, then retry.`
      );
    }
    const networkAttempts = attempts.filter((a) => a.kind === "network");
    if (networkAttempts.length > 0) {
      const urls = networkAttempts.map((a) => a.url).join("; ");
      return new Error(
        `Filesystem probe failed: the sidecar backend is not reachable (no response at ${urls}). Start it with start_backend.ps1, then retry.`
      );
    }
    const httpAttempt = [...attempts].reverse().find((a) => a.kind === "http");
    if (httpAttempt) {
      return new Error(
        `Filesystem probe failed (HTTP ${httpAttempt.status}) at ${httpAttempt.url}`
      );
    }
    return new Error("Filesystem probe returned a malformed response");
  }

  async function pathExistsBatch(paths) {
    const CHUNK_SIZE = 100;
    const results = {};
    for (let i = 0; i < paths.length; i += CHUNK_SIZE) {
      const chunk = paths.slice(i, i + CHUNK_SIZE);
      const endpoints = backendEndpoints("/api/fs/exists");
      // Per-attempt record: {url, kind, status} with kind ∈ {"network",
      // "http", "malformed"}. "malformed" covers both an unparseable body
      // and a 200 whose body fails the results-shape check.
      const attempts = [];
      let chunkResults = null;
      for (const url of endpoints) {
        let response;
        try {
          response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ paths: chunk })
          });
        } catch (err) {
          attempts.push({ url, kind: "network", status: null });
          continue;
        }
        if (!response.ok) {
          attempts.push({ url, kind: "http", status: response.status });
          continue;
        }
        // Parse failures are "malformed", never "network" — hence the
        // json() call gets its own try/catch, separate from fetch().
        let data = null;
        try {
          data = await response.json();
        } catch (err) {
          attempts.push({ url, kind: "malformed", status: response.status });
          continue;
        }
        if (!data || !data.results || typeof data.results !== "object") {
          attempts.push({ url, kind: "malformed", status: response.status });
          continue;
        }
        chunkResults = data.results;
        break;
      }
      if (!chunkResults) {
        // FAIL-CLOSED: every failure path still throws — a probe failure
        // must NEVER resolve as "does not exist".
        throw classifyProbeFailure(attempts);
      }
      Object.assign(results, chunkResults);
    }
    return results;
  }

  // Next free Windows-style "stem (N).ext" name in folder, N starting at 1.
  // Probe failure propagates from pathExistsBatch (fail-closed).
  async function nextFreeName(folder, basename) {
    const dot = basename.lastIndexOf(".");
    const stem = dot > 0 ? basename.slice(0, dot) : basename;
    const ext = dot > 0 ? basename.slice(dot) : "";
    const sep = folder.includes("/") ? "/" : "\\";
    const cleanFolder = folder.replace(/[\\/]+$/, "");
    const PROBE_BATCH = 10;
    for (let start = 1; ; start += PROBE_BATCH) {
      const candidates = [];
      for (let n = start; n < start + PROBE_BATCH; n++) {
        candidates.push(`${cleanFolder}${sep}${stem} (${n})${ext}`);
      }
      const existsMap = await pathExistsBatch(candidates);
      for (const candidate of candidates) {
        if (!existsMap[candidate]) return candidate;
      }
    }
  }

  // =========================================================================
  // Destination-collision resolution dialog.
  // RECORDS CHOICES ONLY — it never moves, deletes, or renames anything; the
  // consolidation flow (later todo) executes the recorded choices.
  //
  // Input: one entry per collision:
  //   {
  //     incomingFile: { id, path, size, duration, height, width, video_codec, oshash },
  //     incomingSceneId, incomingSceneTitle,
  //     existingFile: { ...same file fields } | null, // null => Stash-unknown file
  //     existingSceneId, existingSceneTitle,          // null when existingFile is null
  //     existingPath: "D:\\Packs\\Example\\foo.mp4"    // occupied destination path
  //   }
  // Resolves to an array of { index, choice, incomingFileId, incomingSceneId,
  // existingFileId, existingSceneId, existingPath } with choice one of
  // "keep" | "replace" | "keepboth" — or null when the dialog is cancelled.
  // =========================================================================

  const DEST_COLLISION_CONSEQUENCES = {
    keep: "the scene stays unconsolidated; Build stays disabled until it is removed or resolved",
    replace: "deletes the existing file from disk and Stash; the emptied scene remains in Stash",
    keepboth: "the old copy stays in the destination; Build ignores it and keeps it out of the torrent — the file stays on disk until you remove it",
    useexisting: "the pack uses the copy already in the destination; nothing is moved or deleted"
  };

  // Stash refuses `deleteFiles` on a scene's primary file, so Replace is not
  // offerable for one. Most scenes have exactly one file, which is therefore
  // primary — this disables Replace far more often than it looks.
  const DEST_COLLISION_PRIMARY_NOTE =
    "Replace is unavailable: Stash refuses to delete a scene's primary file. " +
    "Keep both, or resolve the existing file in Stash first.";

  const DEST_COLLISION_SPEC_LABELS = {
    size: "Size",
    duration: "Length",
    resolution: "Resolution",
    codec: "Codec",
    bitrate: "Bitrate"
  };

  let destCollisionState = null;

  function stashUiOrigin() {
    // When the UI is served by the backend sidecar (port 9941),
    // window.location IS the sidecar and cannot reveal where Stash actually
    // runs, so scene links fall back to Stash's default port 9999
    // (http://<hostname>:9999). This is a documented limitation: if Stash
    // listens on a different port, those links will be wrong. On any other
    // origin the page is served by Stash itself, so window.location.origin
    // is already the Stash UI. executeGraphQL posts to a relative /graphql
    // and has no base URL, so it must never be used to derive an origin.
    if (window.location.port === "9941") {
      return `http://${window.location.hostname}:9999`;
    }
    return window.location.origin;
  }

  function formatBitrateMbps(size, duration) {
    const s = Number(size);
    const d = Number(duration);
    if (!Number.isFinite(s) || !Number.isFinite(d) || s <= 0 || d <= 0) return "";
    return `${((s * 8) / d / 1e6).toFixed(2)} Mbps`;
  }

  function destCollisionRenameAllowed() {
    const box = document.getElementById("opt-dest-rename");
    return !!(box && box.checked);
  }

  function destCollisionSpecRows(file) {
    const specs = {
      size: formatFileSize(file.size),
      duration: formatDuration(file.duration),
      resolution: formatResolution(file.height, file.width),
      codec: formatCodec(file.video_codec),
      bitrate: formatBitrateMbps(file.size, file.duration)
    };
    return Object.entries(specs)
      .map(([field, value]) => `
        <div class="dest-collision-spec" data-field="${field}">
          <span class="dest-collision-spec-label">${DEST_COLLISION_SPEC_LABELS[field]}</span>
          <span class="dest-collision-spec-value">${value ? escapeHtml(value) : "—"}</span>
        </div>`)
      .join("");
  }

  function destCollisionSceneLink(sceneId) {
    const id = parseInt(sceneId, 10);
    if (!id) return "";
    const href = `${stashUiOrigin()}/scenes/${id}`;
    return `<a class="dest-collision-scene-link" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">Open scene in Stash</a>`;
  }

  function destCollisionKeepbothTooltip(allowed) {
    return allowed
      ? "Move the incoming file under the next free stem (N).ext name"
      : "Enable 'Allow renaming on collision' to keep both";
  }

  function renderDestinationCollisionDialog() {
    const listEl = document.getElementById("dest-collision-list");
    if (!listEl || !destCollisionState) return;

    const renameAllowed = destCollisionRenameAllowed();

    listEl.innerHTML = destCollisionState.collisions.map((c, index) => {
      const incoming = c.incomingFile || {};
      const known = !!c.existingFile;
      const existing = c.existingFile || {};
      const incomingName = (incoming.path || "").split(/[\\/]/).pop() || "(unnamed file)";
      const existingName = known
        ? ((existing.path || "").split(/[\\/]/).pop() || "(unknown name)")
        : ((c.existingPath || "").split(/[\\/]/).pop() || "(unknown name)");
      const identical = !!(incoming.oshash && existing.oshash && incoming.oshash === existing.oshash);

      const sameScene = !!c.sameScene;
      const replaceBlocked = !!c.existingIsPrimary;

      const choices = [];
      if (sameScene) {
        // The incoming file and the occupying file belong to the same scene:
        // the scene already has a copy in the destination. Nothing is foreign,
        // so Replace is meaningless here — the choice is which copy the pack
        // should use.
        choices.push(`
          <label class="dest-collision-choice dest-collision-choice--useexisting" title="Use the copy already in the destination for this pack">
            <input type="radio" name="dest-collision-choice-${index}" value="useexisting" data-collision-index="${index}" checked>
            <span class="dest-collision-choice-body">
              <span class="dest-collision-choice-label">Use the copy already there</span>
              <span class="dest-collision-consequence">${escapeHtml(DEST_COLLISION_CONSEQUENCES.useexisting)}</span>
            </span>
          </label>`);
      } else if (known) {
        choices.push(`
          <label class="dest-collision-choice dest-collision-choice--keep" title="Keep the file already in the destination">
            <input type="radio" name="dest-collision-choice-${index}" value="keep" data-collision-index="${index}" checked>
            <span class="dest-collision-choice-body">
              <span class="dest-collision-choice-label">Keep existing</span>
              <span class="dest-collision-consequence">${escapeHtml(DEST_COLLISION_CONSEQUENCES.keep)}</span>
            </span>
          </label>`);
        choices.push(`
          <label class="dest-collision-choice dest-collision-choice--replace${replaceBlocked ? " dest-collision-choice--disabled" : ""}" title="${escapeHtml(replaceBlocked ? DEST_COLLISION_PRIMARY_NOTE : "Deletes the existing file from disk and Stash; the emptied scene remains in Stash")}">
            <input type="radio" name="dest-collision-choice-${index}" value="replace" data-collision-index="${index}"${replaceBlocked ? " disabled" : ""}>
            <span class="dest-collision-choice-body">
              <span class="dest-collision-choice-label">Replace</span>
              <span class="dest-collision-consequence">${escapeHtml(replaceBlocked ? DEST_COLLISION_PRIMARY_NOTE : DEST_COLLISION_CONSEQUENCES.replace)}</span>
            </span>
          </label>`);
      }
      choices.push(`
        <label class="dest-collision-choice dest-collision-choice--keepboth${renameAllowed ? "" : " dest-collision-choice--disabled"}" title="${escapeHtml(destCollisionKeepbothTooltip(renameAllowed))}">
          <input type="radio" name="dest-collision-choice-${index}" value="keepboth" data-collision-index="${index}"${renameAllowed ? "" : " disabled"}>
          <span class="dest-collision-choice-body">
            <span class="dest-collision-choice-label">Keep both (rename incoming)</span>
            <span class="dest-collision-consequence">${escapeHtml(DEST_COLLISION_CONSEQUENCES.keepboth)}</span>
          </span>
        </label>`);

      const unknownNote = known ? "" : `
        <div class="dest-collision-unknown-note">This file is not in the Stash library — it cannot be replaced from here. Enable renaming to keep both, or cancel to abort.</div>`;

      const sameSceneNote = sameScene ? `
        <div class="dest-collision-samescene-note">This scene already has a file in the destination — both copies belong to the same scene in Stash, so there is nothing foreign to replace. Compare them below and pick the copy the pack should use.</div>` : "";

      return `
        <div class="dest-collision-card" data-collision-index="${index}">
          <div class="dest-collision-card-head">
            <span class="badge ${sameScene ? "badge-info" : "badge-warning"}">${sameScene ? `Same scene ${index + 1}` : `Collision ${index + 1}`}</span>
            ${identical ? `<span class="badge badge-success dest-collision-identical">✔ identical content</span>` : ""}
          </div>
          ${sameSceneNote}
          <div class="dest-collision-columns">
            <div class="dest-collision-col dest-collision-col--incoming">
              <div class="dest-collision-col-header">Incoming</div>
              <div class="dest-collision-filename" data-field="filename">${escapeHtml(incomingName)}</div>
              ${destCollisionSpecRows(incoming)}
              ${destCollisionSceneLink(c.incomingSceneId)}
            </div>
            <div class="dest-collision-col dest-collision-col--existing">
              <div class="dest-collision-col-header">Already in destination</div>
              ${known ? `
                <div class="dest-collision-filename" data-field="filename">${escapeHtml(existingName)}</div>
                ${destCollisionSpecRows(existing)}
                ${destCollisionSceneLink(c.existingSceneId)}`
              : `
                <div class="dest-collision-unknown">Unknown file (not in the Stash library)</div>
                <div class="dest-collision-filename" data-field="filename">${escapeHtml(existingName)}</div>`}
            </div>
          </div>
          ${unknownNote}
          <div class="dest-collision-choices" role="radiogroup" aria-label="Resolution for ${escapeHtml(incomingName)}">
            ${choices.join("")}
          </div>
        </div>`;
    }).join("");

    updateDestCollisionConfirmState();
  }

  function updateDestCollisionConfirmState() {
    const modal = document.getElementById("dest-collision-modal");
    const confirmBtn = document.getElementById("btn-confirm-dest-collision");
    if (!modal || !confirmBtn || !destCollisionState) return;

    let allResolved = true;
    for (let i = 0; i < destCollisionState.collisions.length; i++) {
      const group = modal.querySelectorAll(`input[type='radio'][data-collision-index='${i}']`);
      const checked = Array.from(group).find((r) => r.checked && !r.disabled);
      if (!checked) {
        allResolved = false;
        break;
      }
    }
    confirmBtn.disabled = !allResolved;
    confirmBtn.title = allResolved
      ? ""
      : "Every collision needs a resolution — enable renaming for unknown files, or cancel.";
  }

  function onDestCollisionRenameToggle() {
    const modal = document.getElementById("dest-collision-modal");
    if (!modal || !destCollisionState) return;

    const renameAllowed = destCollisionRenameAllowed();
    modal.querySelectorAll("input[type='radio'][value='keepboth']").forEach((radio) => {
      radio.disabled = !renameAllowed;
      const label = radio.closest(".dest-collision-choice");
      if (label) {
        label.classList.toggle("dest-collision-choice--disabled", !renameAllowed);
        label.title = destCollisionKeepbothTooltip(renameAllowed);
      }
      const group = modal.querySelectorAll(`input[type='radio'][data-collision-index='${radio.dataset.collisionIndex}']`);
      const hasChecked = Array.from(group).some((r) => r.checked && r.value !== "keepboth");
      if (renameAllowed && !hasChecked) {
        radio.checked = true;
      } else if (!renameAllowed && !hasChecked) {
        radio.checked = false;
      }
    });
    updateDestCollisionConfirmState();
  }

  function bindDestinationCollisionDialog() {
    const modal = document.getElementById("dest-collision-modal");
    if (!modal || modal.dataset.destCollisionBound) return;
    modal.dataset.destCollisionBound = "true";

    modal.addEventListener("click", (e) => {
      if (e.target === modal) cancelDestinationCollisionDialog();
    });

    const cancelBtn = document.getElementById("btn-cancel-dest-collision");
    if (cancelBtn) cancelBtn.addEventListener("click", cancelDestinationCollisionDialog);

    const closeBtn = document.getElementById("btn-close-dest-collision");
    if (closeBtn) closeBtn.addEventListener("click", cancelDestinationCollisionDialog);

    const confirmBtn = document.getElementById("btn-confirm-dest-collision");
    if (confirmBtn) confirmBtn.addEventListener("click", confirmDestinationCollisionChoices);

    const renameBox = document.getElementById("opt-dest-rename");
    if (renameBox) renameBox.addEventListener("change", onDestCollisionRenameToggle);

    const list = document.getElementById("dest-collision-list");
    if (list) list.addEventListener("change", updateDestCollisionConfirmState);
  }

  function handleDestCollisionKeydown(e) {
    if (e.key === "Escape") {
      cancelDestinationCollisionDialog();
    }
  }

  function openDestinationCollisionDialog(collisions) {
    const modal = document.getElementById("dest-collision-modal");
    const items = Array.isArray(collisions) ? collisions : [];
    if (!modal) return Promise.resolve(null);
    if (items.length === 0) return Promise.resolve([]);

    destCollisionState = { collisions: items, resolve: null };

    // Renaming is opt-in per dialog session — always reset to the default.
    const renameBox = document.getElementById("opt-dest-rename");
    if (renameBox) renameBox.checked = false;

    bindDestinationCollisionDialog();
    renderDestinationCollisionDialog();

    modal.style.display = "flex";
    document.addEventListener("keydown", handleDestCollisionKeydown);

    return new Promise((resolve) => {
      destCollisionState.resolve = resolve;
      const firstRadio = modal.querySelector("input[type='radio']:not(:disabled)");
      const target = firstRadio || document.getElementById("btn-confirm-dest-collision");
      if (target) target.focus();
    });
  }

  function closeDestinationCollisionDialog(result) {
    const modal = document.getElementById("dest-collision-modal");
    if (modal) modal.style.display = "none";
    document.removeEventListener("keydown", handleDestCollisionKeydown);
    const state = destCollisionState;
    destCollisionState = null;
    if (state && state.resolve) {
      state.resolve(result);
    }
  }

  function cancelDestinationCollisionDialog() {
    closeDestinationCollisionDialog(null);
  }

  function confirmDestinationCollisionChoices() {
    const modal = document.getElementById("dest-collision-modal");
    if (!modal || !destCollisionState) return;
    const choices = destCollisionState.collisions.map((c, index) => {
      const selected = modal.querySelector(`input[type='radio'][data-collision-index='${index}']:checked`);
      return {
        index,
        choice: selected ? selected.value : null,
        incomingFileId: c.incomingFile ? c.incomingFile.id : null,
        incomingSceneId: c.incomingSceneId != null ? c.incomingSceneId : null,
        existingFileId: c.existingFile ? c.existingFile.id : null,
        existingSceneId: c.existingSceneId != null ? c.existingSceneId : null,
        existingPath: c.existingPath || ""
      };
    });
    closeDestinationCollisionDialog(choices);
  }

  function activeScenes() {
    return scenes.filter((s) => !excludedSceneIds.has(String(s.id)));
  }

  function setMode(mode, userInitiated = false) {
    currentMode = mode === "single" ? "single" : "megapack";
    window._emporniumMode = currentMode;
    const isSingle = currentMode === "single";

    const radioMegapack = document.getElementById("mode-megapack");
    const radioSingle = document.getElementById("mode-single");
    if (radioMegapack) radioMegapack.checked = !isSingle;
    if (radioSingle) radioSingle.checked = isSingle;

    const headerLogo = document.getElementById("header-logo");
    const headerTitle = document.getElementById("header-modal-title");
    if (headerLogo) headerLogo.textContent = isSingle ? "🎬" : "📦";
    if (headerTitle) headerTitle.textContent = isSingle ? "Empornium Single-Scene Uploader" : "Empornium Megapack Builder";

    const btnBuild = document.getElementById("btn-build");
    if (btnBuild) {
      btnBuild.textContent = isSingle ? "🚀 Build Single Scene" : "🚀 Build Megapack";
    }

    const groupConsolidate = document.getElementById("group-consolidate");
    if (groupConsolidate) {
      groupConsolidate.style.display = isSingle ? "none" : "";
    }

    const collisionBanner = document.getElementById("collision-banner");
    if (collisionBanner) {
      if (isSingle) {
        collisionBanner.style.display = "none";
      } else {
        updateCollisionBanner();
      }
    }

    const labelTitle = document.getElementById("label-pack-title");
    if (labelTitle) {
      labelTitle.textContent = isSingle ? "Release Title" : "Pack Title";
    }

    const labelOutputDir = document.getElementById("label-output-dir");
    if (labelOutputDir) {
      labelOutputDir.textContent = isSingle ? "Artifact Output Directory" : "Seed Directory — torrent is built here";
    }

    const outputDirHelper = document.getElementById("output-dir-helper");
    if (outputDirHelper) {
      outputDirHelper.style.display = "block";
      if (isSingle) {
        outputDirHelper.textContent = "Torrent, BBCode and contact sheet are written here. Your media file is not moved.";
      } else {
        outputDirHelper.textContent = "The torrent is built over this directory. Pack files inside it are included as-is; unrelated files are left untouched. Missing pack files can be moved in via Consolidate.";
      }
    }

    const packTitleInput = document.getElementById("pack-title");
    if (packTitleInput) {
      const active = activeScenes();
      const rawTitle = (active[0]?.title || "").trim();
      const primary = getPrimaryFile(active[0]);
      const rawPath = (primary?.path || "").trim();
      const baseName = rawPath ? rawPath.split(/[\\/]/).pop().replace(/\.[^/.]+$/, "") : "";
      const sceneTitle = rawTitle || baseName || "Untitled Scene";

      if (isSingle) {
        if (!hasUserEditedTitle || packTitleInput.value === "" || packTitleInput.value === "Untitled Scene") {
          packTitleInput.value = sceneTitle;
        }
      } else {
        if (!hasUserEditedTitle || (sceneTitle && packTitleInput.value === sceneTitle) || packTitleInput.value === "") {
          packTitleInput.value = "";
        }
      }
    }

    updateActionAvailability();
    updateBBCode();
  }

  function formatFileSize(bytes) {
    if (bytes == null || isNaN(bytes) || Number(bytes) <= 0) return "";
    const num = Number(bytes);
    if (num >= 1024 * 1024 * 1024) {
      return `${(num / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    }
    if (num >= 1024 * 1024) {
      return `${(num / (1024 * 1024)).toFixed(2)} MB`;
    }
    if (num >= 1024) {
      return `${(num / 1024).toFixed(2)} KB`;
    }
    return `${num} B`;
  }

  function formatDuration(seconds) {
    if (seconds == null || isNaN(seconds) || Number(seconds) <= 0) return "";
    const totalSec = Math.round(Number(seconds));
    const hrs = Math.floor(totalSec / 3600);
    const remSec = totalSec % 3600;
    const mins = Math.floor(remSec / 60);
    const secs = remSec % 60;
    if (hrs > 0) {
      return `${hrs}h ${mins}m ${secs}s`;
    }
    return `${mins}m ${secs}s`;
  }

  function formatResolution(height, width) {
    const h = Number(height) || 0;
    const w = Number(width) || 0;
    if (!h && !w) return "";
    const shortDim = (h > 0 && w > 0) ? Math.min(h, w) : (h || 0);
    const longDim = (h > 0 && w > 0) ? Math.max(h, w) : (w || 0);

    if (shortDim >= 2160 || longDim >= 3840) return "2160p";
    if (shortDim >= 1440 || longDim >= 2560) return "1440p";
    if (shortDim >= 1080 || longDim >= 1920) return "1080p";
    if (shortDim >= 720 || longDim >= 1280) return "720p";
    if (shortDim >= 540 || longDim >= 960) return "540p";
    if (shortDim >= 480 || longDim >= 854) return "480p";
    if (shortDim >= 360 || longDim >= 640) return "360p";
    if (shortDim >= 240 || longDim >= 426) return "240p";
    if (h > 0) return `${h}p`;
    if (w > 0) return `${w}w`;
    return "";
  }

  function getEffectiveResolution(height, width) {
    const h = Number(height) || 0;
    const w = Number(width) || 0;
    if (!h && !w) return 0;
    const shortDim = (h > 0 && w > 0) ? Math.min(h, w) : (h || 0);
    const longDim = (h > 0 && w > 0) ? Math.max(h, w) : (w || 0);

    if (shortDim >= 2160 || longDim >= 3840) return 2160;
    if (shortDim >= 1440 || longDim >= 2560) return 1440;
    if (shortDim >= 1080 || longDim >= 1920) return 1080;
    if (shortDim >= 720 || longDim >= 1280) return 720;
    if (shortDim >= 540 || longDim >= 960) return 540;
    if (shortDim >= 480 || longDim >= 854) return 480;
    if (shortDim >= 360 || longDim >= 640) return 360;
    if (shortDim >= 240 || longDim >= 426) return 240;
    if (h > 0) return h;
    if (w > 0) return Math.round((w * 9) / 16);
    return 0;
  }

  function formatCodec(codec) {
    if (!codec) return "";
    return String(codec).trim();
  }

  function getGroupLabel(index) {
    let label = "";
    let n = index;
    while (n >= 0) {
      label = String.fromCharCode(65 + (n % 26)) + label;
      n = Math.floor(n / 26) - 1;
    }
    return `Group ${label}`;
  }

  // Highest-resolution, then largest-size, file among the given candidates.
  // Shared by getPrimaryFile() (all of a scene's files) and
  // reconcileSourceFiles() (only the candidates confirmed present on disk).
  function pickBestFile(files) {
    let best = files[0];
    for (let i = 1; i < files.length; i++) {
      const f = files[i];
      const bestRes = getEffectiveResolution(best.height, best.width);
      const fRes = getEffectiveResolution(f.height, f.width);
      if (fRes > bestRes) {
        best = f;
      } else if (fRes === bestRes && (Number(f.size) || 0) > (Number(best.size) || 0)) {
        best = f;
      }
    }
    return best;
  }

  function getPrimaryFile(scene) {
    if (!scene || !scene.files || !Array.isArray(scene.files) || scene.files.length === 0) return {};
    const sId = String(scene.id);
    if (selectedFileBySceneId.has(sId)) {
      const targetId = String(selectedFileBySceneId.get(sId));
      const found = scene.files.find((f) => (f.id != null && String(f.id) === targetId) || f.path === targetId);
      if (found) return found;
    }
    return pickBestFile(scene.files);
  }

  // Cross-checks EVERY known file of every active scene (not just the
  // currently chosen primary) against the real filesystem in one batched
  // call. Fixes the class of bug where Stash's primary file record for a
  // scene has gone stale (renamed/deduped outside Stash) while a sibling
  // file record for the same scene still points at something real:
  //   - If the current pick is confirmed present, the scene is untouched.
  //   - If it's gone but another file Stash already knows about for that
  //     scene IS present, auto-repoint selectedFileBySceneId at it (the fix
  //     a human would otherwise make by hand via the version dropdown),
  //     preferring one already under the seed dir when there's a choice.
  //   - If nothing for the scene exists anywhere, the scene is recorded in
  //     missingSourceSceneIds instead of only failing later, deep in Build
  //     or (worse) inside task.py.
  // failClosed mirrors pathExistsBatch's own contract: when true (the
  // pre-Build gate), a probe failure re-throws so Build aborts rather than
  // risk treating "couldn't check" as "exists". When false (the passive
  // post-load pass), a probe failure is swallowed with a warning so an
  // unreachable sidecar doesn't brand every scene in the pack as missing
  // during ordinary review.
  async function reconcileSourceFiles({ failClosed = false } = {}) {
    const active = activeScenes();
    // Session-consolidated scenes are excluded from the probe ENTIRELY (not
    // just their result): moveFiles already placed the file and its
    // locally-known path may still be the pre-move one (moveFiles returns
    // only Boolean!, never an echoed path), so a probe would look stale for
    // no reason. This also matches the old build-time check's behavior of
    // making zero network calls once every active scene is consolidated.
    const scenesToCheck = [];
    const allPaths = [];
    for (const s of active) {
      const files = s.files || [];
      if (files.length === 0) continue;
      const current = getPrimaryFile(s);
      if (current.id && consolidatedFileIds.has(current.id)) continue;
      scenesToCheck.push(s);
      for (const f of files) {
        if (f && f.path) allPaths.push(f.path.trim());
      }
    }
    if (allPaths.length === 0) {
      missingSourceSceneIds = new Set();
      return { relinked: [], missing: [], outsideSeed: [] };
    }

    let existsMap;
    try {
      existsMap = await pathExistsBatch(allPaths);
    } catch (err) {
      if (failClosed) throw err;
      showStatus(`Source-file check skipped: ${err.message}`, 0, true);
      return { relinked: [], missing: [], outsideSeed: [], skipped: true };
    }

    const seedDir = (document.getElementById("output-dir")?.value || "").trim();
    const relinked = [];
    const outsideSeed = [];
    const missing = [];
    for (const s of scenesToCheck) {
      const files = s.files || [];
      const current = getPrimaryFile(s);
      if (current.path && existsMap[current.path.trim()] === true) continue;

      const existingCandidates = files.filter((f) => f.path && existsMap[f.path.trim()] === true);
      if (existingCandidates.length === 0) {
        missing.push({ sceneId: s.id, sceneTitle: s.title, path: current.path || (files[0] && files[0].path) || "" });
        continue;
      }
      const underSeed = seedDir ? existingCandidates.filter((f) => isPathUnderSeed(f.path, seedDir)) : [];
      const best = pickBestFile(underSeed.length > 0 ? underSeed : existingCandidates);
      selectedFileBySceneId.set(String(s.id), String(best.id ?? best.path));
      if (seedDir && underSeed.length === 0) {
        outsideSeed.push({ sceneId: s.id, sceneTitle: s.title, from: current.path || "", to: best.path, path: best.path });
      } else {
        relinked.push({ sceneId: s.id, sceneTitle: s.title, from: current.path || "", to: best.path });
      }
    }
    missingSourceSceneIds = new Set(missing.map((m) => String(m.sceneId)));
    return { relinked, missing, outsideSeed };
  }

  function describeReconcile({ relinked = [], missing = [], outsideSeed = [] } = {}) {
    const parts = [];
    if (relinked.length > 0) {
      const detail = relinked
        .map((r) => `${r.sceneTitle || `Scene ${r.sceneId}`} → ${(r.to.split(/[\\/]/).pop() || r.to)}`)
        .join("; ");
      parts.push(`Auto-relinked ${relinked.length} scene(s) to a renamed file found on disk: ${detail}`);
    }
    if (outsideSeed.length > 0) {
      const detail = outsideSeed
        .map((o) => {
          const label = o.sceneTitle || `Scene ${o.sceneId}`;
          const base = o.to ? (o.to.split(/[\\/]/).pop() || o.to) : (o.path ? (o.path.split(/[\\/]/).pop() || o.path) : null);
          return base ? `${label} (${base})` : label;
        })
        .join(", ");
      parts.push(`${outsideSeed.length} scene(s) only have surviving files outside the seed directory — run Consolidate to move them: ${detail}`);
    }
    if (missing.length > 0) {
      const detail = missing
        .map((m) => {
          const label = m.sceneTitle || `Scene ${m.sceneId}`;
          const base = m.path ? (m.path.split(/[\\/]/).pop() || m.path) : null;
          return base ? `${label} (${base})` : label;
        })
        .join(", ");
      parts.push(`${missing.length} scene(s) have no file on disk anywhere — remove them from the pack or restore the source: ${detail}`);
    }
    return parts.join(" | ");
  }

  function computeDuplicateGroups() {
    duplicateGroups = [];
    duplicateBySceneId = new Map();
    sceneSuperioritiesMap = new Map();

    const currentScenes = activeScenes();
    const sceneMap = new Map(currentScenes.map((s) => [String(s.id), s]));
    const fileKeyMap = new Map();

    for (const scene of currentScenes) {
      const sId = String(scene.id);
      for (const file of scene.files || []) {
        const p = (file.path || "").trim();
        if (!p) continue;
        const parts = p.split(/[\\/]/);
        const bname = parts.pop();
        if (!bname) continue;
        const key = bname.toLowerCase();
        if (!fileKeyMap.has(key)) {
          fileKeyMap.set(key, []);
        }
        fileKeyMap.get(key).push({
          sceneId: sId,
          fileId: file.id,
          path: p,
          basename: bname
        });
      }
    }

    let groupIndex = 0;
    for (const [key, members] of fileKeyMap.entries()) {
      const distinctScenesInGroup = [];
      const seenScenes = new Set();
      for (const m of members) {
        if (!seenScenes.has(m.sceneId)) {
          seenScenes.add(m.sceneId);
          distinctScenesInGroup.push(m);
        }
      }

      if (distinctScenesInGroup.length > 1) {
        const label = getGroupLabel(groupIndex);
        const group = {
          key,
          index: groupIndex,
          label,
          members
        };
        duplicateGroups.push(group);
        const total = distinctScenesInGroup.length;
        distinctScenesInGroup.forEach((member, sIdx) => {
          if (!duplicateBySceneId.has(member.sceneId)) {
            duplicateBySceneId.set(member.sceneId, {
              groupIndex,
              label,
              ordinal: sIdx + 1,
              total,
              key,
              basename: member.basename,
              fileId: member.fileId,
              path: member.path
            });
          }
        });

        // Compute Comparative Quality Differences within this collision group
        const groupMembersWithFiles = distinctScenesInGroup
          .map((m) => {
            const sc = sceneMap.get(String(m.sceneId));
            if (!sc) return null;
            const file = (sc.files || []).find((f) => String(f.id) === String(m.fileId)) || (sc.files || []).find((f) => f.path === m.path) || getPrimaryFile(sc);
            return {
              scene: sc,
              member: m,
              file
            };
          })
          .filter(Boolean);

        if (groupMembersWithFiles.length > 1) {
          const resolutions = groupMembersWithFiles
            .map((item) => getEffectiveResolution(item.file.height, item.file.width))
            .filter((r) => r > 0);
          const maxRes = resolutions.length > 0 ? Math.max(...resolutions) : 0;
          const minRes = resolutions.length > 0 ? Math.min(...resolutions) : 0;
          const hasResDiff = maxRes > minRes;

          const sizes = groupMembersWithFiles
            .map((item) => Number(item.file.size) || 0)
            .filter((sz) => sz > 0);
          const maxSize = sizes.length > 0 ? Math.max(...sizes) : 0;
          const minSize = sizes.length > 0 ? Math.min(...sizes) : 0;
          const hasSizeDiff = maxSize > minSize;

          const durations = groupMembersWithFiles
            .map((item) => Math.round(Number(item.file.duration) || 0))
            .filter((d) => d > 0);
          const maxDuration = durations.length > 0 ? Math.max(...durations) : 0;
          const minDuration = durations.length > 0 ? Math.min(...durations) : 0;
          const hasDurationDiff = maxDuration > minDuration;

          for (const item of groupMembersWithFiles) {
            const sid = String(item.scene.id);
            if (!sceneSuperioritiesMap.has(sid)) {
              sceneSuperioritiesMap.set(sid, []);
            }
            const sList = sceneSuperioritiesMap.get(sid);
            const targetFile = item.file;
            const sRes = getEffectiveResolution(targetFile.height, targetFile.width);
            const sSz = Number(targetFile.size) || 0;
            const sD = Math.round(Number(targetFile.duration) || 0);

            const addSuperiority = (type, badgeLabel, badgeTitle) => {
              const existing = sList.find((sup) => sup.type === type);
              if (existing) {
                if (!existing.title.includes(label)) {
                  existing.title += `, ${label}`;
                }
              } else {
                sList.push({
                  type,
                  label: badgeLabel,
                  title: badgeTitle
                });
              }
            };

            if (hasResDiff && sRes === maxRes) {
              const resFormatted = formatResolution(targetFile.height, targetFile.width);
              addSuperiority(
                "resolution",
                `⭐ Higher Resolution (${resFormatted})`,
                `Superior resolution (${resFormatted}) in ${label}`
              );
            }
            if (hasSizeDiff && sSz === maxSize) {
              addSuperiority(
                "size",
                `⭐ Larger Size (${formatFileSize(sSz)})`,
                `Larger file size (${formatFileSize(sSz)}) in ${label}`
              );
            }
            if (hasDurationDiff && sD === maxDuration) {
              addSuperiority(
                "duration",
                `⭐ Longer Runtime (${formatDuration(sD)})`,
                `Longer duration (${formatDuration(sD)}) in ${label}`
              );
            }
          }
        }

        groupIndex++;
      }
    }
  }

  function keepSceneInCollisionGroup(sceneId) {
    if (uiBusy) return;
    const targetIdStr = String(sceneId);
    const affectedGroups = duplicateGroups.filter((g) =>
      g.members.some((m) => String(m.sceneId) === targetIdStr)
    );

    if (affectedGroups.length === 0) return;

    for (const group of affectedGroups) {
      for (const member of group.members) {
        const sid = String(member.sceneId);
        if (sid !== targetIdStr) {
          excludedSceneIds.add(sid);
        }
      }
    }

    renderScenes();
    updateBBCode();
  }

  function updateCollisionBanner() {
    const banner = document.getElementById("collision-banner");
    const headline = document.getElementById("collision-headline");
    const detail = document.getElementById("collision-detail");
    const filterBtn = document.getElementById("btn-filter-conflicts");
    if (!banner || !headline || !detail) return;

    if (currentMode === "single" || duplicateGroups.length === 0) {
      banner.style.display = "none";
      if (showOnlyConflicts) {
        showOnlyConflicts = false;
      }
      return;
    }

    banner.style.display = "flex";
    const totalCollidingFiles = duplicateGroups.reduce((acc, g) => acc + g.members.length, 0);
    const numGroups = duplicateGroups.length;

    headline.textContent = `⚠️ Filename Collisions Detected (${numGroups} conflict group${numGroups > 1 ? "s" : ""}, ${totalCollidingFiles} colliding files)`;

    const groupSummaries = duplicateGroups.map((g) => `${g.label}: "${g.key}" (${g.members.length} scenes)`).join("; ");
    detail.textContent = `Multiple scenes share the same media filename. Consolidating or building now will overwrite files. Resolve conflicts below. (${groupSummaries})`;

    if (filterBtn) {
      filterBtn.textContent = showOnlyConflicts ? "Show all scenes" : "Show only conflicts";
      if (showOnlyConflicts) {
        filterBtn.classList.add("btn-active");
      } else {
        filterBtn.classList.remove("btn-active");
      }
    }
  }

  // Single source of truth for in-place build gating (todo 8's stage gating
  // reuses this): every active scene's CHOSEN primary file must sit under the
  // seed-dir field value (#output-dir) at ANY depth — recursive containment
  // via isPathUnderSeed, mirroring task.py's validate_pack_files_present so
  // the UI blocks before the backend does. Files consolidated this session
  // count as present even when their local path is stale (moveFiles already
  // relocated them; loadScenes may re-fetch pre-move metadata).
  function computeMissingSeedFiles() {
    const seedDir = (document.getElementById("output-dir")?.value || "").trim();
    const missing = [];
    for (const s of activeScenes()) {
      const f = getPrimaryFile(s);
      if (f.id && consolidatedFileIds.has(f.id)) continue;
      const p = (f.path || "").trim();
      if (!p || !isPathUnderSeed(p, seedDir)) {
        missing.push({
          path: p,
          name: p ? (p.split(/[\\/]/).pop() || p) : "(no media file)"
        });
      }
    }
    return { seedDir, missing };
  }

  // Human-readable gating reason: always the count, plus the exact missing
  // basenames when the list is short enough to stay a usable tooltip.
  function formatMissingSeedFilesReason(missing) {
    const count = missing.length;
    const names = missing.map((m) => m.name);
    const listSuffix = count > 0 && count <= 5 ? `: ${names.join(", ")}` : "";
    return `${count} file(s) missing from the seed directory${listSuffix}. Run Consolidate or add the missing files.`;
  }

  function updateActionAvailability() {
    if (uiBusy) {
      // Busy state is authoritative; skip gate derivation entirely.
      for (const id of ["btn-build", "btn-consolidate", "btn-probe"]) {
        const el = document.getElementById(id);
        if (el) { el.disabled = true; el.title = `${busyLabel} in progress — controls locked`; }
      }
      return;
    }

    const btnConsolidate = document.getElementById("btn-consolidate");
    const btnBuild = document.getElementById("btn-build");
    const radioMegapack = document.getElementById("mode-megapack");
    const radioSingle = document.getElementById("mode-single");
    const labelMegapack = document.getElementById("label-mode-megapack");
    const labelSingle = document.getElementById("label-mode-single");

    const active = activeScenes();
    const collisionCount = duplicateGroups.length;
    const rawAllFiles = active.flatMap((s) => (s.files || []).map((f) => (f.path || "").trim()).filter(Boolean));

    // 1. Update Mode Switcher Options
    if (radioSingle && labelSingle) {
      if (active.length === 1) {
        radioSingle.disabled = false;
        labelSingle.classList.remove("disabled");
        labelSingle.title = "Single Scene mode";
      } else {
        radioSingle.disabled = true;
        labelSingle.classList.add("disabled");
        labelSingle.title = active.length === 0
          ? "No scenes selected"
          : "Single Scene mode requires exactly 1 scene";
      }
    }

    if (radioMegapack && labelMegapack) {
      if (active.length > 1) {
        radioMegapack.disabled = false;
        labelMegapack.classList.remove("disabled");
        labelMegapack.title = "Megapack mode";
      } else {
        radioMegapack.disabled = true;
        labelMegapack.classList.add("disabled");
        labelMegapack.title = active.length === 0
          ? "No scenes in the pack"
          : "Megapack mode requires 2 or more scenes";
      }
    }

    // 2. Action buttons availability
    if (currentMode === "single") {
      let buildDisabled = false;
      let buildReason = "";

      if (active.length === 0) {
        buildDisabled = true;
        buildReason = "No scenes selected";
      } else if (active.length > 1) {
        buildDisabled = true;
        buildReason = "Single Scene mode requires exactly 1 scene";
      } else if (rawAllFiles.length === 0) {
        buildDisabled = true;
        buildReason = "Selected scene has no valid media file";
      } else if (rawAllFiles.length > 1) {
        buildDisabled = true;
        buildReason = `Single Scene mode requires exactly 1 media file (found ${rawAllFiles.length})`;
      } else if (missingSourceSceneIds.has(String(active[0].id))) {
        buildDisabled = true;
        buildReason = "This scene's file was not found on disk under any known path. Restore the source or remove the scene.";
      } else {
        // In-place parity (todo 7): the single scene's primary must sit under
        // the seed dir too — same recursive containment as megapack mode.
        const { missing } = computeMissingSeedFiles();
        if (missing.length > 0) {
          buildDisabled = true;
          buildReason = formatMissingSeedFilesReason(missing);
        }
      }

      if (btnBuild) {
        btnBuild.disabled = buildDisabled;
        btnBuild.title = buildDisabled ? buildReason : "Build single-scene torrent, contact sheet, and BBCode";
      }

      if (btnConsolidate) {
        btnConsolidate.disabled = true;
        btnConsolidate.title = "Consolidation is not used in Single Scene mode";
      }
    } else {
      // Megapack Mode
      let consolidateDisabled = false;
      let consolidateReason = "";

      let buildDisabled = false;
      let buildReason = "";

      if (active.length === 0) {
        consolidateDisabled = true;
        consolidateReason = "No scenes in the pack";

        buildDisabled = true;
        buildReason = "No scenes in the pack";
      } else if (collisionCount > 0) {
        consolidateDisabled = true;
        consolidateReason = `${collisionCount} filename collision${collisionCount > 1 ? "s" : ""} must be resolved first`;

        buildDisabled = true;
        buildReason = `${collisionCount} filename collision${collisionCount > 1 ? "s" : ""} must be resolved first`;
      } else if (missingSourceSceneIds.size > 0) {
        const names = active
          .filter((s) => missingSourceSceneIds.has(String(s.id)))
          .map((s) => s.title || `Scene ${s.id}`);
        const reason = `${missingSourceSceneIds.size} scene(s) have no file on disk anywhere: ${names.join(", ")}. Restore the source(s) or remove the scene(s) from the pack.`;
        consolidateDisabled = true;
        consolidateReason = reason;
        buildDisabled = true;
        buildReason = reason;
      } else {
        // In-place gating (todo 7): Build is enabled iff every active scene's
        // chosen primary sits under the seed dir (recursive). The old
        // "direct child of the pack-title subfolder" check is gone.
        const { missing } = computeMissingSeedFiles();
        consolidateDisabled = false;
        consolidateReason = "Consolidate files into destination directory via Stash GraphQL";

        if (missing.length > 0) {
          buildDisabled = true;
          buildReason = formatMissingSeedFilesReason(missing);
        } else {
          buildDisabled = false;
          buildReason = "Build megapack torrent, contact sheets, and BBCode";
        }
      }

      if (btnConsolidate) {
        btnConsolidate.disabled = consolidateDisabled;
        btnConsolidate.title = consolidateDisabled ? consolidateReason : "Consolidate files into destination directory via Stash GraphQL";
      }

      if (btnBuild) {
        btnBuild.disabled = buildDisabled;
        btnBuild.title = buildReason;
      }
    }
  }

  function removeSceneFromPack(sceneId) {
    if (uiBusy) return;
    excludedSceneIds.add(String(sceneId));
    renderScenes();
    updateBBCode();
  }

  function restoreAllScenes() {
    if (uiBusy) return;
    excludedSceneIds.clear();
    showOnlyConflicts = false;
    renderScenes();
    updateBBCode();
  }

  // 3. Fetch Scene Details via Stash GraphQL
  async function loadScenes(idsOverride, tokenOverride) {
    const loadingState = document.getElementById("loading-state");
    if (loadingState) {
      loadingState.innerHTML = "Loading selected scenes from Stash...";
    }

    try {
      const activeToken = tokenOverride || window._emporniumToken || tokenParam;
      if (activeToken) {
        sceneIds = await resolveToken(activeToken);
      } else if (idsOverride && Array.isArray(idsOverride) && idsOverride.length > 0) {
        sceneIds = idsOverride.map((id) => parseInt(id, 10)).filter((id) => !isNaN(id) && id > 0);
      } else if (Array.isArray(window._emporniumSceneIds) && window._emporniumSceneIds.length > 0) {
        sceneIds = window._emporniumSceneIds.map((id) => parseInt(id, 10)).filter((id) => !isNaN(id) && id > 0);
      } else if (sceneIdsParam) {
        sceneIds = sceneIdsParam
          .split(",")
          .filter(Boolean)
          .map((id) => parseInt(id, 10))
          .filter((id) => !isNaN(id) && id > 0);
      } else {
        sceneIds = [];
      }

      if (sceneIds.length === 0) {
        if (loadingState) {
          loadingState.innerText = "No scenes selected.";
        }
        return;
      }

      scenes = await fetchScenesChunked(sceneIds);

      // State integrity (F8): prune excludedSceneIds of IDs no longer present in Stash
      const currentIds = new Set(scenes.map((s) => String(s.id)));
      for (const id of excludedSceneIds) {
        if (!currentIds.has(id)) {
          excludedSceneIds.delete(id);
        }
      }

      const inferred = modeParam || window._emporniumMode || (sceneIds.length === 1 ? "single" : "megapack");
      setMode(inferred);

      renderScenes();
      updateBBCode();
    } catch (err) {
      const safeError = escapeHtml(err.message || String(err));
      const tokenInfo = (tokenOverride || window._emporniumToken || tokenParam) ? ` (token: ${escapeHtml(tokenOverride || window._emporniumToken || tokenParam)})` : "";
      if (loadingState) {
        loadingState.innerHTML = `
          <div style="color: var(--danger); padding: 16px; border: 1px solid var(--danger); border-radius: 4px; background: rgba(239, 68, 68, 0.1); margin: 20px auto; max-width: 500px;">
            <div style="font-weight: 600; margin-bottom: 8px;">⚠️ Failed to load scenes: ${safeError}${tokenInfo}</div>
            <button id="btn-retry-load" class="btn btn-secondary" style="margin-top: 8px; cursor: pointer; padding: 4px 12px;">🔄 Retry</button>
          </div>
        `;
        const retryBtn = document.getElementById("btn-retry-load");
        if (retryBtn) {
          retryBtn.addEventListener("click", () => {
            loadScenes(idsOverride, tokenOverride);
          });
        }
      }
    }
  }

  // 4. Render Scene Cards with Capability Badges, Duplicate Badges, Remove Button, and Drag-and-Drop
  function renderScenes() {
    computeDuplicateGroups();
    updateCollisionBanner();
    updateActionAvailability();

    const container = document.getElementById("scene-list");
    if (!container) return;
    container.innerHTML = "";

    if (scenes.length === 0) {
      container.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--text-muted);">No scenes found.</div>`;
      return;
    }

    const activeList = activeScenes();
    if (activeList.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px; color: var(--text-muted);">
          <div style="margin-bottom: 12px; font-size: 0.95rem;">All scenes have been removed from the pack.</div>
          <button type="button" id="btn-restore-all" class="btn btn-secondary" style="padding: 6px 14px;">↺ Restore all removed scenes</button>
        </div>
      `;
      const restoreBtn = document.getElementById("btn-restore-all");
      if (restoreBtn) {
        restoreBtn.addEventListener("click", restoreAllScenes);
      }
      return;
    }

    const displayList = showOnlyConflicts
      ? activeList.filter((s) => duplicateBySceneId.has(String(s.id)))
      : activeList;

    if (displayList.length === 0 && showOnlyConflicts) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px; color: var(--text-muted);">
          <div style="margin-bottom: 12px; font-size: 0.95rem;">No conflicting scenes found.</div>
          <button type="button" id="btn-show-all-conflicts" class="btn btn-secondary" style="padding: 6px 14px;">Show all active scenes</button>
        </div>
      `;
      const showAllBtn = document.getElementById("btn-show-all-conflicts");
      if (showAllBtn) {
        showAllBtn.addEventListener("click", () => {
          showOnlyConflicts = false;
          renderScenes();
          updateCollisionBanner();
        });
      }
      return;
    }

    displayList.forEach((scene) => {
      const originalIndex = scenes.indexOf(scene);
      const activeIdx = activeList.indexOf(scene);

      const card = document.createElement("div");
      card.className = "scene-card";
      card.draggable = true;
      card.dataset.index = originalIndex;
      card.dataset.sceneId = String(scene.id);
      card.setAttribute("data-scene-id", String(scene.id));

      const dupInfo = duplicateBySceneId.get(String(scene.id));
      if (dupInfo && currentMode !== "single") {
        card.classList.add("scene-card--duplicate");
      }

      const thumbUrl = scene.paths?.screenshot || scene.paths?.preview || "";
      const performers = (scene.performers || []).map((p) => p.name).join(", ") || "Unknown";
      const primaryFile = getPrimaryFile(scene);
      const filePath = primaryFile.path || "No file path";

      // Duplicate Badge (independent of probeData, hidden in single mode)
      let duplicateBadge = "";
      if (dupInfo && currentMode !== "single") {
        duplicateBadge = `<span class="badge badge-danger" title="Filename collision with other scene(s) in pack">⚠️ Duplicate filename · ${escapeHtml(dupInfo.label)} (${dupInfo.ordinal} of ${dupInfo.total})</span>`;
      }

      // Missing Source Badge: reconcileSourceFiles() found no file for this
      // scene anywhere on disk (not just outside the seed dir — gone).
      let missingSourceBadge = "";
      if (missingSourceSceneIds.has(String(scene.id))) {
        missingSourceBadge = `<span class="badge badge-danger" title="No file for this scene was found on disk under any known path. Restore the source file or remove this scene from the pack.">🚫 Source file missing</span>`;
      }

      // Quality / Superiority Badges
      let superiorBadgesHtml = "";
      if (dupInfo && currentMode !== "single") {
        const sups = sceneSuperioritiesMap.get(String(scene.id)) || [];
        if (sups.length > 0) {
          superiorBadgesHtml = sups
            .map(
              (sup) =>
                `<span class="badge badge-success badge-superior badge-superior-${sup.type}" title="${escapeHtml(sup.title)}">${escapeHtml(sup.label)}</span>`
            )
            .join(" ");
        }
      }

      // Probe Capability Badges
      const probeData = probeResultsMap[scene.id];
      let capabilityBadge = "";
      if (probeData) {
        if (!probeData.exists) {
          capabilityBadge = `<span class="badge badge-danger">❌ Missing File</span>`;
        } else if (probeData.is_duplicate_name) {
          capabilityBadge = `<span class="badge badge-danger">⚠️ Duplicate Name</span>`;
        } else if (probeData.can_hardlink) {
          capabilityBadge = `<span class="badge badge-success">⚡ Hardlink OK</span>`;
        } else {
          capabilityBadge = `<span class="badge badge-warning">📋 Copy Required</span>`;
        }
      }

      // Media Specifications (file size, duration, resolution, codec)
      const formattedSize = formatFileSize(primaryFile.size);
      const formattedDur = formatDuration(primaryFile.duration);
      const formattedRes = formatResolution(primaryFile.height, primaryFile.width);
      const formattedCodec = formatCodec(primaryFile.video_codec);

      const specsList = [];
      if (formattedRes) specsList.push(`<span class="spec-item spec-res">📐 ${escapeHtml(formattedRes)}</span>`);
      if (formattedCodec) specsList.push(`<span class="spec-item spec-codec">🎬 ${escapeHtml(formattedCodec)}</span>`);
      if (formattedSize) specsList.push(`<span class="spec-item spec-size">💾 ${escapeHtml(formattedSize)}</span>`);
      if (formattedDur) specsList.push(`<span class="spec-item spec-dur">⏱️ ${escapeHtml(formattedDur)}</span>`);

      let specsHtml = "";
      if (specsList.length > 0) {
        specsHtml = `<div class="scene-meta scene-specs">${specsList.join("")}</div>`;
      }

      // Multi-file attached version selector
      let filePickerHtml = "";
      if (scene.files && Array.isArray(scene.files) && scene.files.length > 1) {
        const optionsHtml = scene.files
          .map((f, fIdx) => {
            const isSelected = (primaryFile.id != null && f.id != null && String(f.id) === String(primaryFile.id)) || (f.path === primaryFile.path);
            const res = formatResolution(f.height, f.width) || "Video";
            const sz = formatFileSize(f.size) || "Unknown size";
            const p = f.path || `File #${fIdx + 1}`;
            return `<option value="${escapeHtml(String(f.id || f.path))}" ${isSelected ? "selected" : ""}>Version ${fIdx + 1}: ${escapeHtml(res)} · ${escapeHtml(sz)} — ${escapeHtml(p)}</option>`;
          })
          .join("");
        filePickerHtml = `
          <div class="scene-file-picker" style="margin-top: 6px; font-size: 0.78rem; display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
            <label style="font-weight: 600; color: var(--text-muted); flex-shrink: 0;">📁 Attached Version (${scene.files.length}):</label>
            <select class="scene-file-select" data-scene-id="${escapeHtml(String(scene.id))}" style="background: rgba(0,0,0,0.3); color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 2px 6px; font-size: 0.78rem; max-width: 100%; text-overflow: ellipsis;">
              ${optionsHtml}
            </select>
          </div>
        `;
      }

      // Keep This Button (for duplicate cards in megapack mode)
      let keepButtonHtml = "";
      if (dupInfo && currentMode !== "single") {
        keepButtonHtml = `<button type="button" class="btn btn-success scene-keep-btn" data-scene-id="${escapeHtml(String(scene.id))}" title="Keep this scene and remove conflicting duplicate(s) in this group" aria-label="Keep scene #${activeIdx + 1} and remove duplicates">✓ Keep This</button>`;
      }

      card.innerHTML = `
        <img class="scene-thumb" src="${escapeHtml(thumbUrl)}" alt="Thumbnail" />
        <div class="scene-info">
          <div class="scene-card-header">
            <div class="scene-title">#${activeIdx + 1} - ${escapeHtml(scene.title || "Untitled Scene")}</div>
            <div class="scene-card-actions">
              ${keepButtonHtml}
              <button type="button" class="btn btn-secondary scene-remove-btn" data-scene-id="${escapeHtml(String(scene.id))}" title="Remove from pack" aria-label="Remove scene #${activeIdx + 1} from pack">✕ Remove</button>
            </div>
          </div>
          <div class="scene-meta">
            <span>👤 ${escapeHtml(performers)}</span>
            <span>📅 ${escapeHtml(scene.date || "Unknown date")}</span>
            ${duplicateBadge}
            ${missingSourceBadge}
            ${superiorBadgesHtml}
            ${capabilityBadge}
          </div>
          ${specsHtml}
          ${filePickerHtml}
          <div class="scene-meta" style="font-size: 0.75rem; word-break: break-all;">
            📁 ${escapeHtml(filePath)}
          </div>
        </div>
      `;

      const fileSelect = card.querySelector(".scene-file-select");
      if (fileSelect) {
        fileSelect.addEventListener("change", (e) => {
          e.stopPropagation();
          const chosen = e.target.value;
          selectedFileBySceneId.set(String(scene.id), chosen);
          renderScenes();
          updateBBCode();
        });
      }

      const keepBtn = card.querySelector(".scene-keep-btn");
      if (keepBtn) {
        keepBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          keepSceneInCollisionGroup(scene.id);
        });
      }

      const removeBtn = card.querySelector(".scene-remove-btn");
      if (removeBtn) {
        removeBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          removeSceneFromPack(scene.id);
        });
      }

      card.addEventListener("dragstart", () => {
        if (uiBusy) return;
        card.classList.add("dragging");
      });
      card.addEventListener("dragend", () => {
        if (uiBusy) return;
        card.classList.remove("dragging");
        reorderScenes();
      });

      container.appendChild(card);
    });

    if (!container.dataset.dragBound) {
      container.dataset.dragBound = "true";
      container.addEventListener("dragover", (e) => {
        if (uiBusy) return;
        e.preventDefault();
        const dragging = document.querySelector(".dragging");
        if (!dragging) return;
        const afterElement = getDragAfterElement(container, e.clientX, e.clientY);
        if (afterElement == null) {
          container.appendChild(dragging);
        } else {
          container.insertBefore(dragging, afterElement);
        }
      });
    }

    // Change B: Newly rendered cards inherit busy lockout if operation in flight
    if (uiBusy) {
      applyBusyLock();
    }
  }

  function getDragAfterElement(container, x, y) {
    if (y === undefined) {
      y = x;
      x = 0;
    }
    const draggableElements = [...container.querySelectorAll(".scene-card:not(.dragging)")];
    if (draggableElements.length === 0) return null;

    // Detect multi-column layout via computed grid template tracks
    let isMultiColumn = false;
    try {
      const style = window.getComputedStyle(container);
      const cols = (style.gridTemplateColumns || "").split(/\s+/).filter(Boolean);
      isMultiColumn = cols.length > 1;
    } catch (_) {}

    // Group cards into visual row bands using vertical overlap tolerance (~5px)
    const rows = [];
    let currentRow = [];
    let rowTop = 0;
    let rowBottom = 0;

    for (const el of draggableElements) {
      const box = el.getBoundingClientRect();
      if (currentRow.length === 0) {
        currentRow.push({ el, box });
        rowTop = box.top;
        rowBottom = box.bottom;
      } else {
        const overlapsVertically = box.top <= rowBottom - 5 && box.bottom >= rowTop + 5;
        const topsAligned = Math.abs(box.top - rowTop) <= 10;
        if (overlapsVertically || topsAligned) {
          currentRow.push({ el, box });
          rowTop = Math.min(rowTop, box.top);
          rowBottom = Math.max(rowBottom, box.bottom);
        } else {
          rows.push({
            items: currentRow,
            top: rowTop,
            bottom: rowBottom
          });
          currentRow = [{ el, box }];
          rowTop = box.top;
          rowBottom = box.bottom;
        }
      }
    }
    if (currentRow.length > 0) {
      rows.push({
        items: currentRow,
        top: rowTop,
        bottom: rowBottom
      });
    }

    if (!isMultiColumn) {
      isMultiColumn = rows.some((r) => r.items.length > 1);
    }

    // Determine target row based on y coordinate
    let targetRowIndex = 0;
    if (y < rows[0].top) {
      targetRowIndex = 0;
    } else if (y > rows[rows.length - 1].bottom) {
      return null;
    } else {
      for (let i = 0; i < rows.length; i++) {
        const nextRow = rows[i + 1];
        const bandBottom = nextRow ? (rows[i].bottom + nextRow.top) / 2 : rows[i].bottom;
        if (y <= bandBottom) {
          targetRowIndex = i;
          break;
        }
        targetRowIndex = i;
      }
    }

    const targetRow = rows[targetRowIndex];
    const items = targetRow.items;

    // Single-column collapsed layout: use vertical midpoint (y)
    if (!isMultiColumn) {
      const box = items[0].box;
      const centerY = box.top + box.height / 2;
      if (y < centerY) {
        return items[0].el;
      } else {
        return targetRowIndex < rows.length - 1 ? rows[targetRowIndex + 1].items[0].el : null;
      }
    }

    // Multi-column grid: use horizontal midpoint (x) across column tracks in the row
    for (const item of items) {
      const centerX = item.box.left + item.box.width / 2;
      if (x < centerX) {
        return item.el;
      }
    }

    return targetRowIndex < rows.length - 1 ? rows[targetRowIndex + 1].items[0].el : null;
  }

  function reorderScenes() {
    if (uiBusy) return;
    const cards = [...document.querySelectorAll(".scene-card")];
    const reorderedActive = cards
      .map((c) => scenes[parseInt(c.dataset.index, 10)])
      .filter(Boolean);

    if (reorderedActive.length > 0) {
      const reorderedIds = new Set(reorderedActive.map((s) => String(s.id)));
      const newScenes = [];
      let activeCursor = 0;
      for (const s of scenes) {
        if (reorderedIds.has(String(s.id))) {
          newScenes.push(reorderedActive[activeCursor++]);
        } else {
          newScenes.push(s);
        }
      }
      scenes = newScenes;
    }
    renderScenes();
    updateBBCode();
  }

  // 5. Update BBCode Preview
  function updateBBCode() {
    if (bbcodeIsFinal || bbcodeUserEdited) return;

    // Change C: Degrade visibly if tag vocabulary is unavailable
    const bbcodeWarning = document.getElementById("bbcode-warning");
    if (vocabularyFetchFailed && !cachedVocabulary) {
      if (bbcodeWarning) {
        bbcodeWarning.textContent = "⚠️ Tag vocabulary unavailable — tags shown unfiltered";
        bbcodeWarning.style.display = "block";
      }
    } else if (bbcodeWarning && bbcodeWarning.textContent.includes("Tag vocabulary unavailable")) {
      bbcodeWarning.textContent = "";
      bbcodeWarning.style.display = "none";
    }

    const packTitleInput = document.getElementById("pack-title");
    const title = packTitleInput?.value || "";
    const notes = document.getElementById("pack-notes")?.value;
    const active = activeScenes();

    let bbcode = "";

    if (currentMode === "single") {
      const scene = active[0] || {};
      const primaryFile = scene.files?.[0] || {};
      const height = primaryFile.height || scene.height || null;
      const duration = primaryFile.duration || scene.duration || null;

      let resTag = "";
      if (height) {
        if (height >= 2160) resTag = "4k";
        else if (height >= 1080) resTag = "1080p";
        else if (height >= 720) resTag = "720p";
        else if (height >= 540) resTag = "540p";
        else if (height >= 480) resTag = "480p";
        else resTag = "SD";
      }

      let durTag = "";
      if (duration && duration > 0) {
        const totalSec = Math.round(duration);
        const mins = Math.floor(totalSec / 60);
        const secs = totalSec % 60;
        if (mins >= 60) {
          const hrs = Math.floor(mins / 60);
          const remMins = mins % 60;
          durTag = `${hrs}h ${remMins}m`;
        } else {
          durTag = `${mins}m ${secs}s`;
        }
      }

      const metaBadges = [];
      if (resTag) metaBadges.push(`[${resTag}]`);
      if (durTag) metaBadges.push(`[${durTag}]`);
      const metaSuffix = metaBadges.length > 0 ? ` ${metaBadges.join(" ")}` : "";

      const performers = (scene.performers || []).map((p) => p.name).join(", ");
      const studioName = scene.studio?.name;
      const rawSceneTags = (scene.tags || []).map((t) => (typeof t === "string" ? t : t?.name || "")).filter(Boolean);
      let tags = "";
      if (cachedVocabulary) {
        const sources = rawSceneTags.map((t) => ({ value: t, kind: "scene_tag" }));
        const resolved = resolveTags(sources, cachedVocabulary);
        tags = resolved.tags.join(", ");
      } else {
        tags = rawSceneTags.join(", ");
      }

      bbcode = `[center][b][size=5]${title}${metaSuffix}[/size][/b][/center]\n\n`;
      if (studioName) {
        bbcode += `[b]Studio:[/b] ${studioName}\n`;
      }
      bbcode += `[b]Performers:[/b] ${performers || "Unknown"}\n`;
      if (tags) {
        bbcode += `[b]Tags:[/b] ${tags}\n`;
      }
      bbcode += `\n[hr]\n`;
      if (notes && notes.trim()) {
        bbcode += `\n[quote]${notes.trim()}[/quote]\n`;
      }
    } else {
      const performers = [...new Set(active.flatMap((s) => (s.performers || []).map((p) => p.name)))];

      bbcode = `[center][b][size=5]${title}[/size][/b][/center]\n\n`;
      bbcode += `[b]Performers:[/b] ${performers.join(", ") || "Various"}\n`;
      bbcode += `[b]Total Scenes:[/b] ${active.length}\n`;
      if (notes && notes.trim()) {
        bbcode += `\n[quote]${notes.trim()}[/quote]\n`;
      }
      bbcode += `\n[hr]\n`;

      active.forEach((s, idx) => {
        const pNames = (s.performers || []).map((p) => p.name).join(", ");
        const pText = pNames ? ` (${pNames})` : "";
        bbcode += `\n${idx + 1}. [b]${s.title || "Scene"} [/b]${pText}`;
      });
    }

    const previewEl = document.getElementById("bbcode-preview");
    if (previewEl) {
      previewEl.value = bbcode;
    }
  }

  // 6. Probe Files via Stash Task (ProbeFiles)
  async function probeFiles() {
    const outputDir = document.getElementById("output-dir")?.value || "";
    const targetDir = currentMode === "single" ? outputDir : getPackDestinationFolder(outputDir);
    const active = activeScenes();
    const filesPayload = active.map((s) => {
      const f = getPrimaryFile(s);
      return {
        scene_id: s.id,
        path: f.path || ""
      };
    });

    const runId = generateRunId();
    const payload = {
      run_id: runId,
      target_dir: targetDir,
      seed_dir: outputDir,
      files: filesPayload
    };
    const probeScratchDir = (document.getElementById("scratch-dir")?.value || "").trim();
    if (probeScratchDir) {
      payload.scratch_dir = probeScratchDir;
    }

    showStatus("Probing filesystem for creation dates and hardlink compatibility...", 0.05);

    try {
      const query = `
        mutation RunProbe($plugin_id: ID!, $task_name: String!, $args: [PluginArgInput!]) {
          runPluginTask(
            plugin_id: $plugin_id,
            task_name: $task_name,
            args: $args
          )
        }
      `;
      const args = [
        { key: "mode", value: { str: "probe" } },
        { key: "payload", value: { str: JSON.stringify(payload) } }
      ];

      const data = await executeGraphQL(query, {
        plugin_id: PLUGIN_ID,
        task_name: "ProbeFiles",
        args: args
      });

      const jobId = data?.runPluginTask;
      if (jobId) {
        busyAwaitingJob = true;
        trackJobProgress(jobId, "ProbeFiles", payload);
      } else {
        showStatus("Filesystem probe dispatched. Check Stash Task Manager for live log.", 1.0);
      }
    } catch (err) {
      showStatus(`Probe error: ${err.message}`, 0, true);
    }
  }

  // 7. Direct GraphQL File Consolidation (moveFiles)
  const MOVE_FILES_MUTATION = `
    mutation MoveFiles($input: MoveFilesInput!) {
      moveFiles(input: $input)
    }
  `;

  // The REAL Stash schema mutation that deletes from disk AND database.
  // destroyFiles is DB-only (leaves the file on disk, so the follow-up move
  // would clobber it) and must NEVER be used here; there is no fileDestroy.
  const DELETE_FILES_MUTATION = `
    mutation DeleteFiles($ids: [ID!]!) {
      deleteFiles(ids: $ids)
    }
  `;

  // Patch local paths from Map<fileId, computed final path> — moveFiles
  // returns only Boolean! and never echoes a path, so the computed
  // destination (incl. destination_basename renames) is tracked here.
  function patchConsolidatedPaths(pathById) {
    for (const s of scenes) {
      if (s.files && Array.isArray(s.files)) {
        for (const f of s.files) {
          const finalPath = pathById.get(String(f.id));
          if (finalPath) {
            f.path = finalPath;
          }
        }
      }
    }
  }

  // Read-only destination-collision discovery for the files still to move.
  // Combines the Stash-side metadata enrichment (findDestinationCollisions)
  // with the authoritative filesystem probe (pathExistsBatch, fail-closed —
  // a probe failure throws and aborts the whole consolidation). Returns one
  // dialog-model entry per incoming file whose destination path is occupied,
  // either by a Stash-known file or by a filesystem-only file.
  async function discoverDestinationCollisions(toMove, destinationFolder) {
    const sep = destinationFolder.includes("/") ? "/" : "\\";
    const cleanDest = destinationFolder.replace(/[\\/]+$/, "");
    const destPathOf = (item) => `${cleanDest}${sep}${item.path.split(/[\\/]/).pop()}`;

    const stashCollisions = await findDestinationCollisions(toMove, destinationFolder);
    const existsMap = await pathExistsBatch(toMove.map(destPathOf));

    const collisions = [];
    const matchedExistingIds = new Set();
    for (const item of toMove) {
      const bname = (item.path.split(/[\\/]/).pop() || "").toLowerCase();
      const stashHit = stashCollisions.find((c) => {
        if (!c || !c.file || matchedExistingIds.has(String(c.file.id))) return false;
        return ((c.file.path || "").split(/[\\/]/).pop() || "").toLowerCase() === bname;
      });
      if (stashHit) {
        matchedExistingIds.add(String(stashHit.file.id));
        const hitFiles = Array.isArray(stashHit.sceneFiles) ? stashHit.sceneFiles : [];
        collisions.push({
          incomingFile: item.file,
          incomingSceneId: item.sceneId,
          incomingSceneTitle: item.sceneTitle,
          existingFile: stashHit.file,
          existingSceneId: stashHit.sceneId,
          existingSceneTitle: stashHit.sceneTitle,
          existingPath: stashHit.file.path,
          // Both files hang off the SAME scene: Stash already deduplicated
          // them, so the scene has a copy where we want it and there is
          // nothing foreign to replace.
          sameScene: String(stashHit.sceneId) === String(item.sceneId),
          // Stash rejects deleteFiles on a scene's primary file, so Replace
          // must not be offered for one. Scene.files is primary-first.
          existingIsPrimary:
            hitFiles.length > 0 && String(hitFiles[0].id) === String(stashHit.file.id)
        });
      } else if (existsMap[destPathOf(item)]) {
        collisions.push({
          incomingFile: item.file,
          incomingSceneId: item.sceneId,
          incomingSceneTitle: item.sceneTitle,
          existingFile: null,
          existingSceneId: null,
          existingSceneTitle: null,
          existingPath: destPathOf(item)
        });
      }
    }
    return collisions;
  }

  async function consolidateFiles() {
    refreshSidecarStatus(); // fire-and-forget: re-probe the badge on user action
    const rawOutput = (document.getElementById("output-dir")?.value || "").trim();
    if (!rawOutput) {
      alert("Please specify a destination directory.");
      return;
    }
    // In-place seeding: the destination IS the seed-directory field value —
    // no pack-title subfolder is appended. The torrent is built over this
    // directory as-is, so pack files already inside it must never be touched.
    const destinationFolder = rawOutput;

    const active = activeScenes();
    const fileItems = active
      .map((s) => {
        const f = getPrimaryFile(s);
        return {
          id: f.id,
          path: (f.path || "").trim(),
          sceneId: s.id,
          sceneTitle: s.title,
          file: f
        };
      })
      .filter((item) => item.id != null && item.path.length > 0);

    if (fileItems.length === 0) {
      alert("No files found to move.");
      return;
    }

    // Split BEFORE the backstop: only files NOT already under the seed dir
    // (recursive containment — nested files count as in-place) are missing
    // and get moved; only they can clobber each other at the destination.
    let toMove = fileItems.filter((item) => !isPathUnderSeed(item.path, destinationFolder));
    const inPlaceItems = fileItems.filter((item) => isPathUnderSeed(item.path, destinationFolder));
    // In-place files need no move — mark them so the build gate sees the pack
    // as complete. Truthful regardless of how the rest of the run ends.
    for (const item of inPlaceItems) {
      consolidatedFileIds.add(item.id);
    }

    // Nothing missing: every primary already sits inside the seed dir —
    // ZERO mutations and no confirm (there is no move to confirm).
    if (toMove.length === 0) {
      showStatus(`All ${fileItems.length} file(s) are already in '${destinationFolder}' — nothing to move.`, 1.0);
      return;
    }

    function findBasenameCollisions(items) {
      const counts = {};
      for (const item of items) {
        if (!item.path) continue;
        const parts = item.path.split(/[\\/]/);
        const bname = parts[parts.length - 1];
        if (!bname) continue;
        const norm = bname.toLowerCase();
        counts[norm] = (counts[norm] || 0) + 1;
      }
      return Object.keys(counts).filter((k) => counts[k] > 1);
    }
    function reportBasenameCollisions(collidingEntries) {
      const errorMsg = `Consolidation blocked: Basename collision detected (${collidingEntries.length} duplicate filenames across active scenes): ${collidingEntries.join(", ")}. Multiple files cannot be moved to '${destinationFolder}' with identical names without clobbering. Please resolve conflicting scenes using the banner above.`;
      showStatus(errorMsg, 0, true);
      const banner = document.getElementById("collision-banner");
      if (banner) {
        banner.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }

    const initialCollisions = findBasenameCollisions(toMove);
    if (initialCollisions.length > 0) {
      reportBasenameCollisions(initialCollisions);
      return;
    }

    let reconciled;
    try {
      reconciled = await reconcileSourceFiles({ failClosed: true });
    } catch (err) {
      showStatus(`Consolidation aborted: filesystem check failed — ${err.message}`, 0, true);
      return;
    }
    if (reconciled.missing.length > 0) {
      showStatus(`Consolidation aborted: ${describeReconcile(reconciled)}`, 0, true);
      renderScenes();
      return;
    }
    if (reconciled.relinked.length > 0 || (reconciled.outsideSeed && reconciled.outsideSeed.length > 0)) {
      renderScenes();
      const updatedActive = activeScenes();
      const updatedFileItems = updatedActive
        .map((s) => {
          const f = getPrimaryFile(s);
          return {
            id: f.id,
            path: (f.path || "").trim(),
            sceneId: s.id,
            sceneTitle: s.title,
            file: f
          };
        })
        .filter((item) => item.id != null && item.path.length > 0);

      toMove = updatedFileItems.filter((item) => !isPathUnderSeed(item.path, destinationFolder));
      const newlyInPlace = updatedFileItems.filter((item) => isPathUnderSeed(item.path, destinationFolder));
      for (const item of newlyInPlace) {
        consolidatedFileIds.add(item.id);
      }
      if (toMove.length === 0) {
        showStatus(`All ${updatedFileItems.length} file(s) are already in '${destinationFolder}' — nothing to move.`, 1.0);
        return;
      }
      const updatedCollisions = findBasenameCollisions(toMove);
      if (updatedCollisions.length > 0) {
        reportBasenameCollisions(updatedCollisions);
        return;
      }
    }

    const sep = destinationFolder.includes("/") ? "/" : "\\";
    const cleanDest = destinationFolder.replace(/[\\/]+$/, "");
    const destPathOf = (item) => `${cleanDest}${sep}${item.path.split(/[\\/]/).pop()}`;

    // Nothing missing: every primary already sits inside the seed dir —
    // ZERO mutations and no confirm (there is no move to confirm).
    if (toMove.length === 0) {
      showStatus(`All ${fileItems.length} file(s) are already in '${destinationFolder}' — nothing to move.`, 1.0);
      return;
    }

    // Read-only pre-check, BEFORE any mutation.
    showStatus("Checking destination for existing files...", 0);
    let collisions = [];
    try {
      collisions = await discoverDestinationCollisions(toMove, destinationFolder);
    } catch (err) {
      showStatus(`Consolidation aborted: destination check failed — ${err.message}`, 0, true);
      return;
    }

    // Cancel aborts the whole consolidation with no changes.
    let choices = [];
    if (collisions.length > 0) {
      choices = await openDestinationCollisionDialog(collisions);
      if (!choices) {
        showStatus("Consolidation cancelled — no files were moved.", 0);
        return;
      }
    }

    const modelByFileId = new Map(collisions.map((c) => [String(c.incomingFile.id), c]));
    const batchItems = [];
    const replacePlans = [];
    const renamePlans = [];
    const keptChoices = [];
    const useExistingChoices = [];
    for (const item of toMove) {
      const ch = choices.find((c) => String(c.incomingFileId) === String(item.id));
      if (!ch || !ch.choice) {
        batchItems.push(item);
      } else if (ch.choice === "replace") {
        replacePlans.push({ item, model: modelByFileId.get(String(item.id)), choice: ch });
      } else if (ch.choice === "keepboth") {
        renamePlans.push({ item, model: modelByFileId.get(String(item.id)), choice: ch });
      } else if (ch.choice === "useexisting") {
        useExistingChoices.push(ch);
      } else {
        keptChoices.push(ch);
      }
    }

    // Fail BEFORE any mutation. Stash rejects deleteFiles on a scene's primary
    // file, and the old code only discovered that mid-run — after the batched
    // move had already relocated everything else, leaving a half-consolidated
    // pack with no rollback. The dialog disables Replace for these, so this is
    // a backstop against a stale or hand-forged choice.
    const illegalReplace = replacePlans.find((p) => p.model && p.model.existingIsPrimary);
    if (illegalReplace) {
      const name = (illegalReplace.model.existingPath || "").split(/[\\/]/).pop() || "the existing file";
      showStatus(
        `Consolidation aborted before any changes: "${name}" is the primary file of its scene and Stash refuses to delete it. ` +
          `Choose Keep both, or resolve that file in Stash first.`,
        0,
        true
      );
      return;
    }

    const totalMoves = batchItems.length + replacePlans.length + renamePlans.length;
    const finalPaths = new Map();
    let movedCount = 0;

    if (totalMoves > 0 && !confirm(`Move/consolidate ${totalMoves} files into ${destinationFolder}?`)) {
      showStatus("Consolidation cancelled — no files were moved.", 0);
      return;
    }

    try {
      // Phase 0: "use the copy already there" — same-scene collisions. This is
      // a LOCAL selection change, not a mutation: the scene already has a file
      // in the destination, so the pack simply points at that copy instead of
      // moving a sibling on top of it. getPrimaryFile() honours
      // selectedFileBySceneId, so the build gate then sees the scene as
      // consolidated without any move.
      for (const ch of useExistingChoices) {
        if (ch.incomingSceneId == null || ch.existingFileId == null) continue;
        selectedFileBySceneId.set(String(ch.incomingSceneId), String(ch.existingFileId));
      }

      // Phase 1: collision-free files — ONE batched move.
      if (batchItems.length > 0) {
        showStatus(`Moving files via Stash GraphQL mutation... (${movedCount + batchItems.length}/${totalMoves})`, movedCount / totalMoves);
        const batchIds = batchItems.map((i) => i.id);
        await executeGraphQL(MOVE_FILES_MUTATION, { input: { ids: batchIds, destination_folder: destinationFolder } });
        for (const item of batchItems) {
          consolidatedFileIds.add(item.id);
          finalPaths.set(String(item.id), destPathOf(item));
        }
        movedCount += batchItems.length;
      }

      // Phase 2: Replace — confirm, deleteFiles (disk + DB), then the move.
      for (const plan of replacePlans) {
        showStatus(`Replacing existing file... (${movedCount + 1}/${totalMoves})`, movedCount / totalMoves);
        const incomingName = plan.item.path.split(/[\\/]/).pop();
        const existingName = (plan.model.existingPath || "").split(/[\\/]/).pop() || incomingName;
        const replaceMsg = `Replace existing file "${existingName}" (scene "${plan.model.existingSceneTitle || "Unknown scene"}") with the incoming file "${incomingName}" (scene "${plan.model.incomingSceneTitle || "Unknown scene"}")?\n\nThe existing file will be deleted from disk and Stash; the emptied scene remains in Stash (fileless, not deleted).`;
        if (!confirm(replaceMsg)) {
          throw new Error("replace cancelled by user");
        }
        await executeGraphQL(DELETE_FILES_MUTATION, { ids: [plan.choice.existingFileId] });
        await executeGraphQL(MOVE_FILES_MUTATION, { input: { ids: [plan.item.id], destination_folder: destinationFolder } });
        consolidatedFileIds.add(plan.item.id);
        finalPaths.set(String(plan.item.id), destPathOf(plan.item));
        movedCount++;
      }

      // Phase 3: Keep-both — names computed AFTER the batched move (an
      // earlier step cannot occupy a rename target), fail-closed on probe
      // error, ONE old→new confirm, then one moveFiles per file.
      const renamePairs = [];
      for (const plan of renamePlans) {
        const newName = await nextFreeName(destinationFolder, plan.item.path.split(/[\\/]/).pop());
        renamePairs.push({ plan, newName });
      }
      if (renamePairs.length > 0) {
        const renameMsg = `Rename and keep both? The following files will be moved under new names:\n\n${renamePairs.map((p) => `${p.plan.item.path} → ${p.newName}`).join("\n")}\n\nThe old copy stays in the pack subfolder; Build ignores it and excludes it from the torrent — it is not deleted.`;
        if (!confirm(renameMsg)) {
          throw new Error("rename cancelled by user");
        }
        for (const pair of renamePairs) {
          showStatus(`Renaming on move... (${movedCount + 1}/${totalMoves})`, movedCount / totalMoves);
          await executeGraphQL(MOVE_FILES_MUTATION, {
            input: {
              ids: [pair.plan.item.id],
              destination_folder: destinationFolder,
              destination_basename: pair.newName.split(/[\\/]/).pop()
            }
          });
          consolidatedFileIds.add(pair.plan.item.id);
          finalPaths.set(String(pair.plan.item.id), pair.newName);
          movedCount++;
        }
      }

      // Phase 4: Keep-existing — no call, the file is skipped.

      patchConsolidatedPaths(finalPaths);
      const usedExisting = useExistingChoices.length;
      // Post-run verification: every active scene's primary must now sit
      // under the seed dir; whatever does not is still missing (kept-existing
      // resolutions, failed moves).
      const stillMissing = [];
      for (const s of activeScenes()) {
        const f = getPrimaryFile(s);
        const p = (f.path || "").trim();
        if (p && !isPathUnderSeed(p, destinationFolder)) {
          stillMissing.push(p.split(/[\\/]/).pop() || p);
        }
      }
      if (totalMoves === 0 && usedExisting > 0) {
        showStatus(`Using the copy already in the destination for ${usedExisting} scene(s); nothing was moved.`, 1.0);
      } else if (usedExisting > 0) {
        showStatus(`Files moved successfully! ${movedCount} moved, ${inPlaceItems.length} already in place, ${stillMissing.length} still missing. ${usedExisting} scene(s) use the copy already in the destination.`, 1.0);
      } else {
        showStatus(`Files moved successfully! ${movedCount} moved, ${inPlaceItems.length} already in place, ${stillMissing.length} still missing.`, 1.0);
      }
      await loadScenes();

      // Persistent warning for every resolution that leaves a file behind in
      // the destination: Build ignores those files — they are excluded from
      // the torrent and NOT deleted, so they remain on disk until resolved.
      const leftoverPaths = [];
      for (const ch of keptChoices) {
        const model = modelByFileId.get(String(ch.incomingFileId));
        if (model && model.existingPath) leftoverPaths.push(model.existingPath);
      }
      for (const pair of renamePairs) {
        if (pair.plan.model && pair.plan.model.existingPath) leftoverPaths.push(pair.plan.model.existingPath);
      }
      if (leftoverPaths.length > 0) {
        showStatus(`Warning: ${leftoverPaths.length} file(s) remain in '${destinationFolder}' and were not replaced: ${leftoverPaths.join(", ")}. Build ignores them and excludes them from the torrent; the files stay on disk until you remove or resolve them.`, 0, true);
      }
    } catch (err) {
      patchConsolidatedPaths(finalPaths);
      const missingNames = toMove
        .filter((item) => !finalPaths.has(String(item.id)))
        .map((item) => item.path.split(/[\\/]/).pop() || item.path);
      const missingSuffix = missingNames.length > 0 ? ` Still missing: ${missingNames.join(", ")}.` : "";
      showStatus(`Consolidation stopped: ${err.message} — ${movedCount} of ${totalMoves} file(s) moved, ${totalMoves - movedCount} file(s) not moved. No rollback was attempted.${missingSuffix}`, 0, true);
    }
  }

  // 7b. Pasted / Dropped Cover Image Upload
  function handleCoverImageFile(file) {
    if (!file || !file.type || !file.type.startsWith("image/")) return;
    const statusEl = document.getElementById("cover-status");
    if (statusEl) {
      statusEl.style.display = "block";
      statusEl.innerText = "Processing cover image...";
      statusEl.style.color = "#9ca3af";
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        // Downscale client-side: cap at 2000px on the long edge
        const maxDim = 2000;
        let width = img.width;
        let height = img.height;
        if (width > maxDim || height > maxDim) {
          if (width > height) {
            height = Math.round((height * maxDim) / width);
            width = maxDim;
          } else {
            width = Math.round((width * maxDim) / height);
            height = maxDim;
          }
        }
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, width, height);

        const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
        const base64Data = dataUrl.replace(/^data:image\/jpeg;base64,/, "");
        runExclusive("mutation", "UploadCoverImage", () => uploadCoverImage(base64Data, file.name || "cover.jpg"));
      };
      img.onerror = () => {
        if (statusEl) {
          statusEl.style.display = "block";
          statusEl.innerText = "Failed to decode cover image.";
          statusEl.style.color = "#ef4444";
        }
      };
      img.src = e.target.result;
    };
    reader.onerror = () => {
      if (statusEl) {
        statusEl.style.display = "block";
        statusEl.innerText = "Failed to read cover image file.";
        statusEl.style.color = "#ef4444";
      }
    };
    reader.readAsDataURL(file);
  }

  async function uploadCoverImage(imageB64, filename = "cover.jpg") {
    const statusEl = document.getElementById("cover-status");
    if (statusEl) {
      statusEl.style.display = "block";
      statusEl.innerText = "Uploading cover image to HamsterImg...";
      statusEl.style.color = "#9ca3af";
    }
    const runId = generateRunId();
    const payload = {
      run_id: runId,
      image_b64: imageB64,
      filename: filename
    };
    try {
      const query = `
        mutation RunCoverUpload($plugin_id: ID!, $task_name: String!, $args: [PluginArgInput!]) {
          runPluginTask(
            plugin_id: $plugin_id,
            task_name: $task_name,
            args: $args
          )
        }
      `;
      const args = [
        { key: "mode", value: { str: "upload_cover" } },
        { key: "payload", value: { str: JSON.stringify(payload) } }
      ];
      const data = await executeGraphQL(query, {
        plugin_id: PLUGIN_ID,
        task_name: "UploadCoverImage",
        args: args
      });
      const jobId = data?.runPluginTask;
      if (jobId) {
        busyAwaitingJob = true;
        trackJobProgress(jobId, "UploadCoverImage", payload);
      } else {
        if (statusEl) {
          statusEl.style.display = "block";
          statusEl.innerText = "Cover upload task queued in Stash Task Manager!";
          statusEl.style.color = "#9ca3af";
        }
      }
    } catch (err) {
      if (statusEl) {
        statusEl.style.display = "block";
        statusEl.innerText = `Cover upload failed: ${err.message}`;
        statusEl.style.color = "#ef4444";
      }
    }
  }

  function removeCoverImage() {
    currentCoverUrl = null;
    const previewImg = document.getElementById("cover-preview");
    const previewContainer = document.getElementById("cover-preview-container");
    const removeBtn = document.getElementById("btn-remove-cover");
    const promptEl = document.getElementById("cover-paste-prompt");
    const statusEl = document.getElementById("cover-status");
    const fileInput = document.getElementById("cover-file-input");
    if (previewImg) previewImg.src = "";
    if (previewContainer) previewContainer.style.display = "none";
    if (removeBtn) removeBtn.style.display = "none";
    if (promptEl) promptEl.style.display = "block";
    if (statusEl) {
      statusEl.style.display = "none";
      statusEl.innerText = "";
    }
    if (fileInput) fileInput.value = "";
  }

  // 8. Trigger Megapack or Single-Scene Build Task (BuildMegapack / BuildSingleScene) & Track Progress
  async function buildMegapack() {
    bbcodeIsFinal = false;
    bbcodeUserEdited = false;
    lastFinalBBCode = null;
    refreshBBCodeEditedNotice();
    updateBBCode();

    const active = activeScenes();
    if (active.length === 0) {
      showStatus("Build aborted: No active scenes in selection.", 0, true);
      return;
    }

    const isSingle = currentMode === "single";
    const allFiles = active.map((s) => (getPrimaryFile(s).path || "").trim()).filter(Boolean);
    const rawAllFiles = active.flatMap((s) => (s.files || []).map((f) => (f.path || "").trim()).filter(Boolean));
    const outputDir = (document.getElementById("output-dir")?.value || "").trim();

    // Pre-flight client-side validation gate
    if (isSingle) {
      if (active.length !== 1 || rawAllFiles.length !== 1) {
        const reason = active.length !== 1
          ? `Single Scene mode requires exactly 1 scene (found ${active.length}).`
          : `Single Scene mode requires exactly 1 media file (found ${rawAllFiles.length}).`;
        showStatus(`Build aborted: ${reason}`, 0, true);
        return;
      }
    } else {
      if (allFiles.length === 0) {
        showStatus("Build aborted: No valid media files in selection.", 0, true);
        return;
      }
      if (duplicateGroups.length > 0) {
        showStatus(`Build aborted: ${duplicateGroups.length} unresolved filename collision(s). Resolve conflicts before building.`, 0, true);
        return;
      }
    }

    // In-place gating (todo 7): every active scene's chosen primary must sit
    // under the seed dir (recursive). Mirrors task.py's
    // validate_pack_files_present so the UI blocks before the backend does.
    // This is a cheap, local string check — it runs BEFORE the network
    // disk-existence probe below so a file that's simply not consolidated
    // yet (the common, expected case) never triggers a filesystem call.
    const { missing } = computeMissingSeedFiles();
    if (missing.length > 0) {
      showStatus(`Build aborted: ${formatMissingSeedFilesReason(missing)}`, 0, true);
      return;
    }

    // Authoritative on-disk existence probe (POST /api/fs/exists, chunked at
    // 100 paths, fail-closed on non-200/network error). A file the Stash
    // metadata places under the seed dir may still be absent on disk (stale
    // DB, deleted/renamed file) — block here, not deep in task.py. Checks
    // every file record Stash knows about for each scene, not just the
    // chosen primary: if the primary has gone stale but a sibling file for
    // the same scene is confirmed present, auto-relinks to it instead of
    // failing; only a scene with nothing left on disk anywhere aborts the
    // build, named exactly.
    let reconciled;
    try {
      reconciled = await reconcileSourceFiles({ failClosed: true });
    } catch (err) {
      showStatus(`Build aborted: filesystem check failed — ${err.message}`, 0, true);
      return;
    }
    if (reconciled.missing.length > 0 || (reconciled.outsideSeed && reconciled.outsideSeed.length > 0)) {
      showStatus(`Build aborted: ${describeReconcile(reconciled)}`, 0, true);
      renderScenes();
      return;
    }
    if (reconciled.relinked.length > 0) {
      renderScenes();
      showStatus(describeReconcile(reconciled), 0, false);
    }

    const packTitle = document.getElementById("pack-title")?.value || (isSingle ? "Untitled Scene" : "Megapack");
    const notes = document.getElementById("pack-notes")?.value;
    const tags = [...new Set(active.flatMap((s) => (s.tags || []).map((t) => t.name)))];
    const performers = [...new Set(active.flatMap((s) => (s.performers || []).map((p) => p.name)))];

    const uploadPreviews = document.getElementById("opt-upload-previews")?.checked || false;

    const runId = generateRunId();

    const payload = {
      run_id: runId,
      pack_title: packTitle,
      seed_dir: outputDir,
      upload_previews: uploadPreviews,
      notes: notes,
      tags: tags,
      performers: performers,
      single_scene: isSingle,
      cover_image_url: currentCoverUrl || undefined,
      scenes: active.map((s) => {
        const primaryFile = getPrimaryFile(s);
        return {
          id: s.id,
          title: s.title,
          path: primaryFile.path || "",
          size: primaryFile.size || 0,
          height: primaryFile.height || null,
          width: primaryFile.width || null,
          duration: primaryFile.duration || null,
          video_codec: primaryFile.video_codec || null,
          date: s.date || null,
          studio: s.studio?.name || null,
          performers: (s.performers || []).map((p) => ({ id: p.id, name: p.name })),
          tags: (s.tags || []).map((t) => t.name)
        };
      })
    };
    if (currentCoverUrl) {
      payload.cover_image_url = currentCoverUrl;
    }
    // scratch_dir (todo 8): the #scratch-dir input feeds the payload; the key
    // is omitted when empty — task.py's legacy fallback handles absence.
    const scratchDir = (document.getElementById("scratch-dir")?.value || "").trim();
    if (scratchDir) {
      payload.scratch_dir = scratchDir;
    }

    const taskName = isSingle ? "BuildSingleScene" : "BuildMegapack";
    const taskModeStr = isSingle ? "single" : "build";
    showStatus(`Starting ${taskName} task...`, 0.05);

    try {
      const query = `
        mutation RunBuild($plugin_id: ID!, $task_name: String!, $args: [PluginArgInput!]) {
          runPluginTask(
            plugin_id: $plugin_id,
            task_name: $task_name,
            args: $args
          )
        }
      `;
      const args = [
        { key: "mode", value: { str: taskModeStr } },
        { key: "payload", value: { str: JSON.stringify(payload) } }
      ];

      const data = await executeGraphQL(query, {
        plugin_id: PLUGIN_ID,
        task_name: taskName,
        args: args
      });

      const jobId = data?.runPluginTask;
      if (jobId) {
        busyAwaitingJob = true;
        trackJobProgress(jobId, taskName, payload);
      } else {
        showStatus(`${isSingle ? "Single scene" : "Megapack"} build task queued in Stash Task Manager!`, 0.85);
      }
    } catch (err) {
      showStatus(`Build trigger failed: ${err.message}`, 0, true);
    }
  }

  // 8. WebSocket Subscription and Polling Job Progress Tracker
  function trackJobProgress(jobId, taskType, payload = {}) {
    activeJobId = jobId;
    activeRunId = payload.run_id || null;
    bufferedLogs = [];
    wsLogStreamActive = false;
    showStatus(`Task ${taskType} queued (Job ID: ${jobId})...`, 0.05);

    if (wsWatchdog) {
      clearTimeout(wsWatchdog);
      wsWatchdog = null;
    }
    if (activeWs) {
      try {
        activeWs.close();
      } catch (_) {}
      activeWs = null;
    }

    try {
      const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${wsProto}//${location.host}/graphql`;
      const ws = new WebSocket(wsUrl, "graphql-transport-ws");
      activeWs = ws;
      let receivedSubMessage = false;

      wsWatchdog = setTimeout(() => {
        if (!receivedSubMessage) {
          console.warn(
            "WebSocket watchdog timed out after 2500ms with no subscription messages; falling back to polling."
          );
          if (activeWs === ws) {
            try {
              ws.close();
            } catch (_) {}
            activeWs = null;
            wsLogStreamActive = false;
            startJobPolling(jobId, taskType, payload);
          }
        }
      }, 2500);

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: "connection_init" }));
        ws.send(
          JSON.stringify({
            id: "job_sub",
            type: "subscribe",
            payload: {
              query: `subscription { jobsSubscribe { type job { id status progress error } } }`
            }
          })
        );
        ws.send(
          JSON.stringify({
            id: "log_sub",
            type: "subscribe",
            payload: {
              query: `subscription { loggingSubscribe { time level message } }`
            }
          })
        );
        wsLogStreamActive = true;
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "next") {
            if (msg.payload?.data?.loggingSubscribe) {
              // Stash's schema is `loggingSubscribe: [LogEntry!]!` -- each
              // message carries a batch, not a single entry. Pushing the array
              // itself made every buffered element an Array, so the sentinel
              // scans (which read .level/.message) matched nothing and a
              // failed build was reported as a success.
              const entries = msg.payload.data.loggingSubscribe;
              if (Array.isArray(entries)) {
                bufferedLogs.push(...entries);
              } else {
                bufferedLogs.push(entries);
              }
            }
            if (msg.payload?.data?.jobsSubscribe?.job) {
              receivedSubMessage = true;
              if (wsWatchdog) {
                clearTimeout(wsWatchdog);
                wsWatchdog = null;
              }
              const job = msg.payload.data.jobsSubscribe.job;
              if (job.id === jobId || String(job.id) === String(jobId)) {
                handleJobUpdate(job, taskType, payload);
              }
            }
          }
        } catch (e) {
          console.error("WS message error", e);
        }
      };

      ws.onerror = () => {
        if (wsWatchdog) {
          clearTimeout(wsWatchdog);
          wsWatchdog = null;
        }
        if (activeWs === ws) {
          activeWs = null;
        }
        wsLogStreamActive = false;
        if (!handledJobIds.has(String(jobId))) {
          startJobPolling(jobId, taskType, payload);
        }
      };

      ws.onclose = () => {
        if (wsWatchdog) {
          clearTimeout(wsWatchdog);
          wsWatchdog = null;
        }
        if (activeWs === ws) {
          activeWs = null;
        }
        wsLogStreamActive = false;
        if (!handledJobIds.has(String(jobId))) {
          startJobPolling(jobId, taskType, payload);
        }
      };
    } catch (e) {
      if (wsWatchdog) {
        clearTimeout(wsWatchdog);
        wsWatchdog = null;
      }
      wsLogStreamActive = false;
      if (!handledJobIds.has(String(jobId))) {
        startJobPolling(jobId, taskType, payload);
      }
    }
  }

  function startJobPolling(jobId, taskType, payload) {
    const jobIdStr = String(jobId);
    if (handledJobIds.has(jobIdStr)) {
      return;
    }
    if (String(activePollingJobId) === jobIdStr && activePollInterval !== null) {
      return;
    }
    if (activePollInterval) {
      clearInterval(activePollInterval);
      activePollInterval = null;
    }
    activePollingJobId = jobId;

    const poll = async () => {
      if (handledJobIds.has(jobIdStr)) {
        if (activePollInterval) {
          clearInterval(activePollInterval);
          activePollInterval = null;
        }
        if (String(activePollingJobId) === jobIdStr) {
          activePollingJobId = null;
        }
        return;
      }
      try {
        const resp = await executeGraphQL(
          `query FindJob($id: ID!) { findJob(input: { id: $id }) { id status progress error } }`,
          { id: jobId }
        );
        const job = resp?.findJob;
        if (job) {
          await handleJobUpdate(job, taskType, payload);
          if (["FINISHED", "CANCELLED", "FAILED"].includes(job.status)) {
            if (activePollInterval) {
              clearInterval(activePollInterval);
              activePollInterval = null;
            }
            if (String(activePollingJobId) === jobIdStr) {
              activePollingJobId = null;
            }
          }
        }
      } catch (err) {
        console.warn("Polling error", err);
      }
    };

    activePollInterval = setInterval(poll, 1500);
    poll();
  }

  async function handleJobUpdate(job, taskType, payload) {
    const progress = typeof job.progress === "number" ? job.progress : 0;
    showStatus(`Running ${taskType}: ${Math.round(progress * 100)}%`, progress);

    if (job.status === "FINISHED") {
      const jobIdStr = String(job.id);
      if (handledJobIds.has(jobIdStr)) {
        return;
      }
      handledJobIds.add(jobIdStr);

      try {
        if (wsWatchdog) {
          clearTimeout(wsWatchdog);
          wsWatchdog = null;
        }
        if (activeWs) {
          try {
            activeWs.close();
          } catch (_) {}
          activeWs = null;
        }
        if (activePollInterval) {
          clearInterval(activePollInterval);
          activePollInterval = null;
        }
        activePollingJobId = null;

        const runId = payload?.run_id || activeRunId;
        let sidecarResult = null;

        if (runId) {
          async function probeSidecarRun(rid) {
            const endpoints = backendEndpoints(`/api/run/${encodeURIComponent(rid)}`);
            let sawNetworkError = false;
            for (const url of endpoints) {
              try {
                const resp = await fetch(url);
                if (resp.status === 200) {
                  const data = await resp.json();
                  if (data && data.found === true) {
                    return { status: "found", result: data.result };
                  } else if (data && data.found === false) {
                    return { status: "not_found" };
                  } else {
                    return { status: "transport_error" };
                  }
                } else if (resp.status === 400) {
                  return { status: "bad_request" };
                } else {
                  return { status: "transport_error" };
                }
              } catch (err) {
                sawNetworkError = true;
              }
            }
            return sawNetworkError ? { status: "transport_error" } : { status: "not_found" };
          }

          let probe = await probeSidecarRun(runId);

          // If not found yet on a reachable sidecar, retry over ~5s (10 x 500ms)
          if (probe.status === "not_found") {
            const maxRetries = 10;
            const retryDelayMs = 500;
            for (let i = 0; i < maxRetries; i++) {
              await new Promise((r) => setTimeout(r, retryDelayMs));
              probe = await probeSidecarRun(runId);
              if (probe.status === "found" || probe.status === "transport_error" || probe.status === "bad_request") {
                break;
              }
            }
          }

          if (probe.status === "found" && probe.result) {
            sidecarResult = probe.result;
            if (sidecarResult.status === "failed") {
              const failureError = sidecarResult.error || "Task execution failed on backend.";
              if (taskType === "UploadCoverImage") {
                const statusEl = document.getElementById("cover-status");
                if (statusEl) {
                  statusEl.style.display = "block";
                  statusEl.innerText = `Cover upload failed: ${failureError}`;
                  statusEl.style.color = "#ef4444";
                }
              }
              showStatus(failureError, 0, true);
              return;
            } else {
              const combined = { ...payload, ...sidecarResult };
              onTaskComplete(taskType, combined);
              return; // Skip log scans entirely
            }
          }
        }

        // --- Log Sentinel Fallback Path ---
        let candidateLogs = bufferedLogs;
        let usedFallback = false;
        let fallbackFailed = false;

        if (!wsLogStreamActive || candidateLogs.length === 0) {
          try {
            const resp = await executeGraphQL(`query Logs { logs { time level message } }`);
            if (resp && Array.isArray(resp.logs)) {
              candidateLogs = resp.logs;
              usedFallback = true;
            } else {
              fallbackFailed = true;
            }
          } catch (err) {
            fallbackFailed = true;
          }
        }

        let failureError = runId ? findFailureSentinel(candidateLogs, runId) : null;
        if (!failureError && usedFallback && bufferedLogs.length > 0) {
          failureError = findFailureSentinel(bufferedLogs, runId);
        }

        if (failureError) {
          if (taskType === "UploadCoverImage") {
            const statusEl = document.getElementById("cover-status");
            if (statusEl) {
              statusEl.style.display = "block";
              statusEl.innerText = `Cover upload failed: ${failureError}`;
              statusEl.style.color = "#ef4444";
            }
          }
          showStatus(failureError, 0, true);
          return;
        }

        if (!wsLogStreamActive && fallbackFailed) {
          if (taskType === "UploadCoverImage") {
            const statusEl = document.getElementById("cover-status");
            if (statusEl) {
              statusEl.style.display = "block";
              statusEl.innerText = "⚠️ Cover upload completed, but log verification failed.";
              statusEl.style.color = "#ef4444";
            }
          }
          showStatus("⚠️ Task marked finished, but log verification failed (WebSocket and logs query unavailable). Check Stash logs.", 1.0, true);
          return;
        }

        // The request payload only describes what was asked for. Everything the
        // build actually produced -- remote image URLs, final BBCode, tracker
        // tags, pre-flight results -- comes back on the result sentinel.
        const result = runId ? findResultSentinel(candidateLogs, runId) : null;
        const chunkedBBCode = runId
          ? (findBBCodeSentinel(candidateLogs, runId) ||
             (usedFallback && bufferedLogs.length > 0 ? findBBCodeSentinel(bufferedLogs, runId) : null))
          : null;

        let combined = result ? { ...payload, ...result } : payload;
        if (chunkedBBCode) {
          combined = { ...combined, chunked_bbcode: chunkedBBCode };
        }
        onTaskComplete(taskType, combined);
      } finally {
        // Change B: Dispatched Stash job has reached terminal state; release busy lockout.
        setUiBusy(false);
      }
    } else if (job.status === "FAILED" || job.status === "CANCELLED") {
      const jobIdStr = String(job.id);
      handledJobIds.add(jobIdStr);
      if (wsWatchdog) {
        clearTimeout(wsWatchdog);
        wsWatchdog = null;
      }
      if (activeWs) {
        try {
          activeWs.close();
        } catch (_) {}
        activeWs = null;
      }
      if (activePollInterval) {
        clearInterval(activePollInterval);
        activePollInterval = null;
      }
      activePollingJobId = null;
      if (taskType === "UploadCoverImage") {
        const statusEl = document.getElementById("cover-status");
        if (statusEl) {
          statusEl.style.display = "block";
          statusEl.innerText = `Cover upload ${job.status}: ${job.error || "Unknown error"}`;
          statusEl.style.color = "#ef4444";
        }
      }
      showStatus(`Task ${taskType} ${job.status}: ${job.error || "Unknown error"}`, progress, true);
      // Change B: Terminal failure / cancellation releases busy lockout.
      setUiBusy(false);
    }
  }

  function setupCopyButton(btnId, getText) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.onclick = () => {
      if (btn.disabled) return;
      const text = getText();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard
          .writeText(text)
          .then(() => {
            const orig = btn.innerText;
            btn.innerText = "✅ Copied!";
            setTimeout(() => {
              btn.innerText = orig;
            }, 2000);
          })
          .catch(() => {});
      }
    };
  }

  function onTaskComplete(taskType, payload) {
    showStatus(`🎉 ${taskType} completed successfully!`, 1.0);

    if (taskType === "UploadCoverImage") {
      const statusEl = document.getElementById("cover-status");
      if (payload && payload.cover_url) {
        currentCoverUrl = payload.cover_url;
        const previewImg = document.getElementById("cover-preview");
        const previewContainer = document.getElementById("cover-preview-container");
        const removeBtn = document.getElementById("btn-remove-cover");
        const promptEl = document.getElementById("cover-paste-prompt");
        if (previewImg) previewImg.src = payload.cover_url;
        if (previewContainer) previewContainer.style.display = "block";
        if (removeBtn) removeBtn.style.display = "inline-block";
        if (promptEl) promptEl.style.display = "none";
        if (statusEl) {
          statusEl.style.display = "block";
          statusEl.innerText = "Cover uploaded successfully!";
          statusEl.style.color = "#10b981";
        }
      } else {
        if (statusEl) {
          statusEl.style.display = "block";
          statusEl.innerText = (payload && payload.error) || "Cover upload failed.";
          statusEl.style.color = "#ef4444";
        }
      }
      return;
    }

    if (taskType === "BuildMegapack" || taskType === "BuildSingleScene") {
      const isSingle = taskType === "BuildSingleScene" || currentMode === "single";
      const packTitle =
        payload?.pack_title || document.getElementById("pack-title")?.value || "";
      const torrentPath =
        typeof payload?.torrent_path === "string" && payload.torrent_path.trim()
          ? payload.torrent_path.trim()
          : null;
      const manifestPath =
        typeof payload?.manifest_path === "string" && payload.manifest_path.trim()
          ? payload.manifest_path.trim()
          : null;
      const submissionPath =
        typeof payload?.submission_path === "string" && payload.submission_path.trim()
          ? payload.submission_path.trim()
          : null;
      const bbcodePath =
        typeof payload?.bbcode_path === "string" && payload.bbcode_path.trim()
          ? payload.bbcode_path.trim()
          : null;

      // Tracker tags: from payload.tracker_tags or derived scene tags
      // The backend's merge_tags is authoritative. This fallback only runs when
      // the result sentinel was missing, and mirrors it as closely as the
      // browser can using the cached vocabulary if available.
      let tagsList = payload?.tracker_tags;
      let unmappedList = payload?.unmapped_tags;
      if (!tagsList || !Array.isArray(tagsList)) {
        const scenes = activeScenes();
        const sources = [];
        for (const s of scenes) {
          for (const t of s.tags || []) {
            const name = typeof t === "string" ? t : t?.name;
            if (name) sources.push({ value: name, kind: "scene_tag" });
          }
          for (const p of s.performers || []) {
            const name = typeof p === "string" ? p : p?.name;
            if (name) sources.push({ value: name, kind: "performer" });
          }
          if (s.studio?.name) {
            sources.push({ value: s.studio.name, kind: "studio" });
          }
        }
        if (payload?.tags && Array.isArray(payload.tags)) {
          for (const t of payload.tags) {
            if (typeof t === "string" && t.trim()) sources.push({ value: t.trim(), kind: "scene_tag" });
          }
        }
        if (payload?.performers && Array.isArray(payload.performers)) {
          for (const p of payload.performers) {
            if (typeof p === "string" && p.trim()) sources.push({ value: p.trim(), kind: "performer" });
          }
        }
        const resolved = resolveTags(sources, cachedVocabulary);
        tagsList = resolved.tags;
        if (!unmappedList) {
          unmappedList = resolved.unmapped;
        }
      }
      if (!Array.isArray(unmappedList)) {
        unmappedList = [];
      }
      const tagsString = tagsList.length > 0 ? tagsList.join(" ") : (isSingle ? "scene" : "megapack");

      // Preview status and image count
      const active = activeScenes();
      const imageUrls = Array.isArray(payload?.uploaded_urls) ? payload.uploaded_urls : [];
      const imageCount = imageUrls.length || (active.length > 0 ? active.length : 1);
      const uploadEnabled = Boolean(payload?.upload_previews);
      const bbcodeBox = document.getElementById("bbcode-preview");

      // The locally-composed preview omits the image block, which is only known
      // once the uploads finish. Replace it with what was actually written to
      // the .bbcode file so "Copy" hands over the real submission text.
      const finalBBCode = payload?.chunked_bbcode || (typeof payload?.bbcode === "string" && payload.bbcode ? payload.bbcode : null);
      const bbcodeWarning = document.getElementById("bbcode-warning");
      if (bbcodeBox && finalBBCode) {
        bbcodeBox.value = finalBBCode;
        bbcodeIsFinal = true;
        lastFinalBBCode = finalBBCode;
        refreshBBCodeEditedNotice();
        if (bbcodeWarning) bbcodeWarning.style.display = "none";
      } else if (payload?.bbcode_truncated) {
        if (bbcodeWarning) {
          const locationMsg = bbcodePath ? bbcodePath : "the artifact directory";
          bbcodeWarning.innerText = `⚠️ Preview is provisional (truncated). Read ${bbcodePath ? locationMsg : "the .bbcode file in " + locationMsg} for the complete BBCode.`;
          bbcodeWarning.style.display = "block";
        }
      } else if (bbcodeWarning) {
        bbcodeWarning.style.display = "none";
      }

      const bbcodeText = bbcodeBox ? bbcodeBox.value : "";

      // C3: Render unmapped tags collapsible under BBCode preview
      const collapsibleEl = document.getElementById("unmapped-tags-collapsible");
      const summaryEl = document.getElementById("unmapped-tags-summary");
      const listEl = document.getElementById("unmapped-tags-list");
      const copyUnmappedBtn = document.getElementById("btn-copy-unmapped");

      if (collapsibleEl && summaryEl && listEl) {
        if (unmappedList.length > 0) {
          collapsibleEl.removeAttribute("open"); // collapsed by default
          collapsibleEl.style.display = "block";
          summaryEl.textContent = `▸ ${unmappedList.length} Stash tag${unmappedList.length === 1 ? "" : "s"} have no Empornium equivalent (not sent)`;
          listEl.innerHTML = "";
          for (const tag of unmappedList) {
            const li = document.createElement("li");
            li.textContent = tag;
            listEl.appendChild(li);
          }
          if (copyUnmappedBtn && !copyUnmappedBtn.dataset.bound) {
            copyUnmappedBtn.dataset.bound = "true";
            copyUnmappedBtn.addEventListener("click", async (e) => {
              e.preventDefault();
              e.stopPropagation();
              try {
                await navigator.clipboard.writeText(unmappedList.join("\n"));
                const orig = copyUnmappedBtn.textContent;
                copyUnmappedBtn.textContent = "✓ Copied";
                setTimeout(() => {
                  copyUnmappedBtn.textContent = orig;
                }, 1500);
              } catch (err) {
                // Clipboard fallback
              }
            });
          }
        } else {
          collapsibleEl.style.display = "none";
          listEl.innerHTML = "";
        }
      }

      // Presentation size indicator next to BBCode preview
      const presSizeEl = document.getElementById("presentation-size-line");
      if (presSizeEl) {
        let presBytes = null;
        if (payload?.presentation_bytes !== undefined && payload.presentation_bytes !== null) {
          presBytes = Number(payload.presentation_bytes);
        } else if (payload?.preflight && Array.isArray(payload.preflight.checks)) {
          const presCheck = payload.preflight.checks.find((c) => c.id === "presentation_size");
          if (presCheck && presCheck.detail) {
            const match = presCheck.detail.match(/([\d.]+)\s*MiB/);
            if (match) {
              presBytes = parseFloat(match[1]) * 1048576;
            }
          }
        }

        if (presBytes !== null && !isNaN(presBytes)) {
          const capBytes = 25 * 1048576;
          const warningBytes = 0.9 * capBytes;
          const sizeMiB = (presBytes / 1048576).toFixed(1);
          presSizeEl.innerText = `Presentation Size: ${sizeMiB} MiB / 25 MiB`;
          presSizeEl.style.display = "block";
          if (presBytes > capBytes) {
            presSizeEl.style.color = "var(--danger)";
            presSizeEl.className = "status-text badge-danger";
          } else if (presBytes > warningBytes) {
            presSizeEl.style.color = "var(--warning)";
            presSizeEl.className = "status-text badge-warning";
          } else {
            presSizeEl.style.color = "var(--text-muted)";
            presSizeEl.className = "status-text";
          }
        } else {
          presSizeEl.style.display = "none";
        }
      }

      let isPreviewOnly = false;
      if (payload?.preview_only !== undefined) {
        isPreviewOnly = Boolean(payload.preview_only);
      } else {
        isPreviewOnly =
          !uploadEnabled || bbcodeText.includes("file:///") || bbcodeText.includes("PREVIEW ONLY");
      }

      const imageSummary = isPreviewOnly
        ? `${imageCount} image(s) (${imageCount} local file:/// preview(s))`
        : `${imageCount} image(s) (all remote on HamsterImg)`;

      // Pre-Flight Checklist evaluation (fail-closed if preflight checks missing)
      const isUnverified = !payload?.preflight || !Array.isArray(payload.preflight.checks);
      const checks = isUnverified ? [] : payload.preflight.checks;
      const isReady = isUnverified
        ? false
        : (payload.ready !== undefined
          ? Boolean(payload.ready)
          : (payload.preflight.ready !== undefined
            ? Boolean(payload.preflight.ready)
            : false));

      let checklistHtml = "";
      for (const c of checks) {
        let icon = "✅";
        let color = "#34d399";
        if (c.is_info) {
          icon = "ℹ️";
          color = "var(--text-muted)";
        } else if (c.is_warning) {
          icon = "⚠️";
          color = "#fbbf24";
        } else if (!c.passed) {
          icon = "❌";
          color = "#f87171";
        }
        checklistHtml += `<li id="check-${c.id}" style="color: ${color};"><span>${icon}</span> <strong>${escapeHtml(
          c.label
        )}:</strong> ${escapeHtml(c.detail)}</li>`;
      }

      // Resolve upload URL from configured site_url
      const rawSiteUrl = (payload?.site_url || payload?.empornium_site_url || "").trim();
      let uploadLinkHtml = "";
      if (rawSiteUrl) {
        let uploadUrl = rawSiteUrl;
        if (!uploadUrl.toLowerCase().endsWith(".php")) {
          uploadUrl = uploadUrl.replace(/\/+$/, "") + "/upload.php";
        }
        uploadLinkHtml = `
          <div id="upload-link-container" style="margin-top: 8px; display: flex; justify-content: flex-end;">
            <a id="btn-open-upload" href="${escapeHtml(
              uploadUrl
            )}" target="_blank" rel="noopener noreferrer" class="btn btn-primary" style="text-decoration: none; display: inline-flex; align-items: center; gap: 4px; padding: 4px 12px; font-size: 0.8rem; ${
          !isReady ? "pointer-events: none; opacity: 0.45;" : ""
        }" title="${
          isReady
            ? "Open Empornium upload form and copy torrent path to clipboard"
            : (isUnverified ? "Upload disabled: Build result is unverified" : "Upload disabled: Pre-flight checks did not pass")
        }">
              🌐 Open Empornium Upload Form
            </a>
          </div>
        `;
      }

      // Remote previews are listed so they can be opened and verified before
      // the description is pasted into the upload form. A full single-scene
      // gallery runs to a dozen-plus URLs, so the list stays collapsed and
      // internally scrollable rather than burying the checklist below it.
      let imageLinksHtml = "";
      if (imageUrls.length > 0) {
        const items = imageUrls
          .map((url) => {
            const safe = escapeHtml(String(url));
            if (/^https?:/i.test(String(url))) {
              return `<li><a href="${safe}" target="_blank" rel="noopener noreferrer" style="color: #93c5fd;">${safe}</a></li>`;
            }
            return `<li style="color: var(--text-muted);"><code>${safe}</code></li>`;
          })
          .join("");
        const label = imageUrls.length === 1 ? "1 image URL" : `${imageUrls.length} image URLs`;
        imageLinksHtml = `
          <details class="handoff-images">
            <summary>${label}</summary>
            <ul id="handoff-image-urls" class="handoff-image-list">${items}</ul>
          </details>
        `;
      }

      const summaryBox = document.getElementById("artifact-summary");
      const detailsBox = document.getElementById("artifact-details");

      const headerHtml = isUnverified
        ? `<div id="handoff-status-header" style="font-weight: 600; color: var(--danger); margin-bottom: 4px;">⚠️ Build Result Unverified — no result received from the backend</div>`
        : `<div id="handoff-status-header" style="font-weight: 600; color: ${
            isReady ? "var(--success)" : "var(--danger)"
          }; margin-bottom: 4px;">${
            isReady
              ? "🎉 Build Complete! — Ready for Manual Upload"
              : "⚠️ Build Complete! — " + (isSingle ? "Release" : "Pack") + " Not Ready for Upload"
          }</div>`;

      const unverifiedAlertHtml = isUnverified
        ? `<div id="unverified-build-alert" style="padding: 6px 10px; background: rgba(239, 68, 68, 0.1); border: 1px solid var(--danger); border-radius: 4px; color: var(--danger); font-size: 0.8rem; margin-bottom: 8px;">
            The build task finished in Stash, but no result payload was received from the backend sidecar or logs. The build may have failed. Please check the Stash logs.
          </div>`
        : "";

      const torrentDisplayHtml = torrentPath
        ? `<code id="handoff-torrent">${escapeHtml(torrentPath)}</code>`
        : `<span id="handoff-torrent" style="color: var(--text-muted); font-style: italic;">— not reported —</span>`;

      const manifestDisplayHtml = manifestPath
        ? `<code id="handoff-manifest">${escapeHtml(manifestPath)}</code>`
        : `<span id="handoff-manifest" style="color: var(--text-muted); font-style: italic;">— not reported —</span>`;

      const submissionDisplayHtml = submissionPath
        ? `<code id="handoff-submission">${escapeHtml(submissionPath)}</code>`
        : `<span id="handoff-submission" style="color: var(--text-muted); font-style: italic;">— not reported —</span>`;

      detailsBox.innerHTML = `
        ${headerHtml}
        ${unverifiedAlertHtml}

        <div id="preview-gate-alert" class="preview-gate-alert" style="display: ${
          !isUnverified && isPreviewOnly ? "block" : "none"
        };">
          🚫 <strong>${isSingle ? "Release" : "Pack"} Not Ready for Upload:</strong> BBCode contains local <code>file:///</code> URLs. Remote hosting is required so images render for other users and do not disclose local paths.<br>
          <em>Remedy: Enable preview upload, or re-run once the image host is reachable.</em>
        </div>

        <div id="category-reminder" class="category-reminder">📌 Category — you select this on the upload form.</div>

        <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 6px;">
          <div><strong>${isSingle ? "Release Title" : "Pack Title"}:</strong> <span id="handoff-title">${escapeHtml(packTitle)}</span></div>
          <button id="btn-copy-title" class="btn btn-secondary" style="padding: 2px 8px; font-size: 0.75rem;" ${
            !isReady ? 'disabled title="Copy disabled: ' + (isUnverified ? "Build result is unverified" : (isSingle ? "Release" : "Pack") + " is not ready for upload") + '"' : ""
          }>📋 Copy Title</button>
        </div>

        <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 4px;">
          <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px;"><strong>Tracker Tags:</strong> <span id="handoff-tags" style="font-family: monospace; font-size: 0.8rem; color: #93c5fd;">${escapeHtml(
            tagsString
          )}</span></div>
          <button id="btn-copy-tags" class="btn btn-secondary" style="padding: 2px 8px; font-size: 0.75rem;" ${
            !isReady ? 'disabled title="Copy disabled: ' + (isUnverified ? "Build result is unverified" : (isSingle ? "Release" : "Pack") + " is not ready for upload") + '"' : ""
          }>📋 Copy Tags</button>
        </div>

        <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 4px;">
          <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px;"><strong>Torrent File:</strong> ${torrentDisplayHtml}</div>
          <button id="btn-copy-torrent-path" class="btn btn-secondary" style="padding: 2px 8px; font-size: 0.75rem;" ${
            isUnverified || !torrentPath ? 'disabled title="Copy disabled: ' + (!torrentPath ? "Path not reported" : "Build result is unverified") + '"' : ""
          }>📋 Copy Path</button>
        </div>

        <div style="margin-top: 4px;"><strong>Preview Images:</strong> <span id="handoff-images">${escapeHtml(
          imageSummary
        )}</span></div>
        ${imageLinksHtml}
        <div style="margin-top: 2px;"><strong>Manifest:</strong> ${manifestDisplayHtml}</div>
        <div style="margin-top: 2px;"><strong>Submission JSON:</strong> ${submissionDisplayHtml}</div>

        <!-- Pre-Flight Checklist Section -->
        <div id="preflight-section" style="margin-top: 8px; border-top: 1px solid var(--card-border); padding-top: 6px;">
          <div style="font-weight: 600; font-size: 0.8rem; margin-bottom: 4px; color: var(--text-main);">📋 Pre-Flight Checklist:</div>
          <ul id="preflight-checklist" style="list-style: none; padding-left: 0; margin: 0; font-size: 0.75rem; display: flex; flex-direction: column; gap: 3px;">
            ${checklistHtml}
          </ul>
        </div>

        <!-- Empornium Upload Link (only present when configured) -->
        ${uploadLinkHtml}
      `;

      summaryBox.style.display = "flex";

      const copyBbcodeBtn = document.getElementById("btn-copy-bbcode");
      if (copyBbcodeBtn) {
        copyBbcodeBtn.disabled = !isReady;
        if (!isReady) {
          copyBbcodeBtn.title = isUnverified
            ? "Copy disabled: Build result is unverified"
            : `Copy disabled: ${isSingle ? "Release" : "Pack"} is not ready for upload`;
        } else {
          copyBbcodeBtn.title = "";
        }
      }

      setupCopyButton("btn-copy-title", () => document.getElementById("handoff-title")?.innerText || "");
      setupCopyButton("btn-copy-tags", () => document.getElementById("handoff-tags")?.innerText || "");
      setupCopyButton("btn-copy-torrent-path", () => (torrentPath ? document.getElementById("handoff-torrent")?.innerText || "" : ""));

      const uploadLink = document.getElementById("btn-open-upload");
      if (uploadLink) {
        uploadLink.addEventListener("click", () => {
          if (!isReady || !torrentPath) return;
          const torPath = document.getElementById("handoff-torrent")?.innerText || "";
          if (torPath && navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(torPath).catch(() => {});
          }
        });
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Change B: Centralized UI lockout system, runExclusive(), and setUiBusy()
  // ---------------------------------------------------------------------------
  async function runExclusive(tier, label, fn) {
    if (uiBusy) return;                 // hard guard: a second click is a no-op
    setUiBusy(true, tier, label);
    busyAwaitingJob = false;
    try {
      await fn();
    } catch (err) {
      showStatus(`${label} failed: ${err.message}`, 0, true);
      busyAwaitingJob = false;
    } finally {
      // A dispatched Stash job owns its own unlock (handleJobUpdate). Anything that
      // returned without dispatching — a validation abort, a network failure, a pure
      // GraphQL consolidation — unlocks here.
      if (!busyAwaitingJob) setUiBusy(false);
    }
  }

  function updateBusyEscapeHatch() {
    if (!uiBusy) return;
    const elapsedMs = Date.now() - busyStartedAt;
    if (elapsedMs >= 60000) {
      const totalSec = Math.floor(elapsedMs / 1000);
      const m = Math.floor(totalSec / 60);
      const s = totalSec % 60;
      const bannerText = document.getElementById("busy-banner-text");
      if (bannerText) {
        bannerText.textContent = `Controls locked for ${m}m ${s}s — `;
      }
      const unlockBtn = document.getElementById("btn-busy-unlock");
      if (unlockBtn) {
        unlockBtn.style.display = "inline-flex";
      }
    }
  }

  function applyBusyLock() {
    const isBusy = uiBusy;
    const isBuildTier = isBusy && busyOperation === "build";

    // Common controls locked in both "build" and "mutation" tiers
    const commonControlIds = [
      "mode-megapack",
      "mode-single",
      "pack-title",
      "pack-notes",
      "opt-upload-previews",
      "cover-file-input",
      "btn-remove-cover",
      "output-dir",
      "scratch-dir",
      "btn-browse-dir",
      "btn-browse-scratch",
      "btn-restore-all",
      "btn-keep-first",
      "btn-filter-conflicts",
      "btn-show-all-conflicts",
      "btn-probe",
      "btn-build",
      "btn-consolidate",
      "btn-sidecar-stop"
    ];

    for (const id of commonControlIds) {
      const el = document.getElementById(id);
      if (el) {
        if (isBusy) {
          el.disabled = true;
        } else {
          // Note: mode-megapack, mode-single, btn-build, btn-consolidate are
          // derived authoritatively by updateActionAvailability() on unlock.
          if (!["btn-build", "btn-consolidate", "mode-megapack", "mode-single"].includes(id)) {
            el.disabled = false;
          }
        }
      }
    }

    // Cover paste zone & file selection link
    const coverZone = document.getElementById("cover-paste-zone");
    if (coverZone) {
      coverZone.tabIndex = isBusy ? -1 : 0;
      coverZone.style.pointerEvents = isBusy ? "none" : "";
    }
    const linkChooseCover = document.getElementById("link-choose-cover");
    if (linkChooseCover) {
      linkChooseCover.style.pointerEvents = isBusy ? "none" : "";
    }

    // Scene cards: remove/keep buttons, file selector, and draggable flags
    const sceneRemoveBtns = document.querySelectorAll(".scene-remove-btn");
    for (const btn of sceneRemoveBtns) {
      btn.disabled = isBusy;
    }
    const sceneKeepBtns = document.querySelectorAll(".scene-keep-btn");
    for (const btn of sceneKeepBtns) {
      btn.disabled = isBusy;
    }
    const sceneFileSelects = document.querySelectorAll(".scene-file-select");
    for (const sel of sceneFileSelects) {
      sel.disabled = isBusy;
    }
    const sceneCards = document.querySelectorAll(".scene-card");
    for (const card of sceneCards) {
      card.draggable = !isBusy;
      card.setAttribute("draggable", isBusy ? "false" : "true");
    }

    // Build-tier only controls (BBCode editor, toolbar, and reset button)
    const bbcodePreview = document.getElementById("bbcode-preview");
    if (bbcodePreview) {
      bbcodePreview.disabled = isBuildTier;
    }
    const bbcodeToolbarElements = document.querySelectorAll("#bbcode-toolbar .toolbar-btn, #bbcode-toolbar .toolbar-select");
    for (const el of bbcodeToolbarElements) {
      el.disabled = isBuildTier;
    }
    const bbcodeResetBtn = document.getElementById("btn-bbcode-reset");
    if (bbcodeResetBtn) {
      bbcodeResetBtn.disabled = isBuildTier;
    }
  }

  function setUiBusy(on, tier = null, label = null) {
    uiBusy = Boolean(on);

    if (uiBusy) {
      busyOperation = tier || "build";
      busyLabel = label || (busyOperation === "build" ? "BuildMegapack" : "Operation");
      busyStartedAt = Date.now();
      document.body.classList.toggle("ui-busy", true);

      // Button feedback: swap starting button label to hourglass verb
      const btnFeedbackMap = {
        "BuildMegapack": { id: "btn-build", text: "⏳ Building…" },
        "BuildSingleScene": { id: "btn-build", text: "⏳ Building…" },
        "ProbeFiles": { id: "btn-probe", text: "⏳ Probing…" },
        "Consolidate": { id: "btn-consolidate", text: "⏳ Consolidating…" }
      };
      const feedback = btnFeedbackMap[busyLabel];
      if (feedback) {
        const btn = document.getElementById(feedback.id);
        if (btn) {
          if (!btn.dataset.idleLabel) {
            btn.dataset.idleLabel = btn.textContent;
          }
          btn.textContent = feedback.text;
        }
      }

      // Show busy banner
      const banner = document.getElementById("busy-banner");
      const bannerText = document.getElementById("busy-banner-text");
      const unlockBtn = document.getElementById("btn-busy-unlock");
      if (banner) banner.style.display = "flex";
      if (bannerText) {
        bannerText.textContent = `⏳ ${busyLabel} in progress — controls locked until it finishes or fails.`;
      }
      if (unlockBtn) unlockBtn.style.display = "none";

      const unmappedCollapsible = document.getElementById("unmapped-tags-collapsible");
      if (unmappedCollapsible) unmappedCollapsible.style.display = "none";

      // Arm 60-second escape hatch timer
      if (busyEscapeTimer) {
        clearInterval(busyEscapeTimer);
        busyEscapeTimer = null;
      }
      busyEscapeTimer = setInterval(updateBusyEscapeHatch, 1000);

      // Scroll progress section into view once per operation
      const progressSection = document.getElementById("progress-section");
      if (progressSection && typeof progressSection.scrollIntoView === "function") {
        progressSection.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }

      // Apply indeterminate class to progress bar
      const bar = document.getElementById("progress-bar");
      if (bar) {
        bar.classList.add("indeterminate");
      }

      // Lock controls
      applyBusyLock();
    } else {
      busyOperation = null;
      busyLabel = null;
      busyAwaitingJob = false;
      busyStartedAt = 0;
      document.body.classList.toggle("ui-busy", false);

      // Clear escape timer
      if (busyEscapeTimer) {
        clearInterval(busyEscapeTimer);
        busyEscapeTimer = null;
      }

      // Hide busy banner
      const banner = document.getElementById("busy-banner");
      const bannerText = document.getElementById("busy-banner-text");
      const unlockBtn = document.getElementById("btn-busy-unlock");
      if (banner) banner.style.display = "none";
      if (bannerText) bannerText.textContent = "";
      if (unlockBtn) unlockBtn.style.display = "none";

      // Clear indeterminate class from progress bar
      const bar = document.getElementById("progress-bar");
      if (bar) {
        bar.classList.remove("indeterminate");
      }

      // Restore button labels
      for (const id of ["btn-build", "btn-probe", "btn-consolidate"]) {
        const btn = document.getElementById(id);
        if (btn && btn.dataset.idleLabel) {
          btn.textContent = btn.dataset.idleLabel;
          delete btn.dataset.idleLabel;
        }
      }

      // Restore probe button title if set
      const btnProbe = document.getElementById("btn-probe");
      if (btnProbe) btnProbe.title = "";

      // Unlock controls and re-derive gated state
      applyBusyLock();
      updateActionAvailability();
      renderStageState();
    }
  }

  function showStatus(msg, progress = 0, isError = false) {
    const container = document.getElementById("progress-container");
    const bar = document.getElementById("progress-bar");
    const statusText = document.getElementById("status-text");

    if (container) container.style.display = "block";
    if (statusText) {
      statusText.style.display = "block";
      statusText.innerText = msg;
      statusText.style.color = isError ? "var(--danger)" : "var(--text-muted)";
    }
    if (bar) {
      bar.style.width = `${Math.round(progress * 100)}%`;
      // Sweep highlight while operation is busy/waiting on Stash (progress < 0.02);
      // switch to standard width presentation once real progress arrives.
      if (progress < 0.02 && !isError) {
        bar.classList.add("indeterminate");
      } else {
        bar.classList.remove("indeterminate");
      }
    }
  }

  // =========================================================================
  // 9. Wizard stage rail (todo 8): Setup -> Locations -> Scenes -> Actions
  // =========================================================================
  // Plain-JS stage state (no storage). The rail highlights the current stage,
  // checkmarks completed ones, and allows click-to-jump only to
  // already-reached stages; forward movement always goes through Next's
  // per-stage validation. Stage content is NEVER hidden (soft-focus): every
  // control stays rendered and interactable from any stage, so the scene
  // list, build tooltips, and status text remain usable — the gates live on
  // the Next button, not on visibility.
  const TOTAL_STAGES = 4;
  const STAGES_BY_NUMBER = { 1: "Setup", 2: "Locations", 3: "Scenes", 4: "Actions" };
  let currentStage = 1;
  let maxStageReached = 1;

  function getWizardStage() {
    return { currentStage, maxStageReached };
  }

  // Per-stage gate: "" when Next may advance, otherwise the blocking reason
  // shown via showStatus(..., true).
  function stageGateReason(stage) {
    if (stage === 1) {
      const title = (document.getElementById("pack-title")?.value || "").trim();
      if (!title) return "Pack title is empty. Enter a title before continuing.";
      return "";
    }
    if (stage === 2) {
      const seedDir = (document.getElementById("output-dir")?.value || "").trim();
      if (!seedDir) return "Seed directory is empty. Choose the directory the torrent is built over.";
      const scratchDir = (document.getElementById("scratch-dir")?.value || "").trim();
      if (!scratchDir) return "Scratch directory is empty. Choose where generated artifacts are written.";
      return "";
    }
    if (stage === 3) {
      if (activeScenes().length === 0) {
        return "No active scenes in the pack. Restore or add at least one scene before continuing.";
      }
      if (duplicateGroups.length > 0) {
        return `${duplicateGroups.length} unresolved filename collision(s). Resolve duplicates before continuing.`;
      }
      return "";
    }
    return ""; // Actions (4): always reachable once reached.
  }

  // Directory existence for the Locations gate. Deliberately NOT
  // /api/fs/exists: that endpoint is os.path.isfile()-only (main.py fs_exists)
  // and therefore reports false for EVERY directory — gating on it would
  // block the stage unconditionally. Stash's `directory` query is the same
  // same-origin mechanism the browse modal uses. FAIL-CLOSED: any GraphQL
  // error, null payload, or empty path counts as "not verified".
  async function directoryExists(path) {
    const query = `
      query StageDirCheck($path: String) {
        directory(path: $path) {
          path
        }
      }
    `;
    try {
      const data = await executeGraphQL(query, { path });
      const dir = data?.directory;
      return Boolean(dir && (dir.path || "").trim());
    } catch (err) {
      return false;
    }
  }

  function focusStagePanel(stage) {
    const panel = document.querySelector(`[data-stage-panel="${stage}"]`);
    if (panel && typeof panel.scrollIntoView === "function") {
      panel.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function renderStageState() {
    for (let n = 1; n <= TOTAL_STAGES; n++) {
      const item = document.getElementById(`stage-item-${n}`);
      if (item) {
        item.classList.toggle("stage-current", n === currentStage);
        item.classList.toggle("stage-completed", n < currentStage);
        item.classList.toggle("reached", n <= maxStageReached);
        item.setAttribute("aria-current", n === currentStage ? "step" : "false");
      }
      const panel = document.querySelector(`[data-stage-panel="${n}"]`);
      if (panel) {
        panel.classList.toggle("stage-current", n === currentStage);
      }
    }
    const backBtn = document.getElementById("btn-stage-back");
    if (backBtn) backBtn.disabled = currentStage <= 1;
    const nextBtn = document.getElementById("btn-stage-next");
    if (nextBtn) {
      nextBtn.disabled = currentStage >= TOTAL_STAGES;
      nextBtn.textContent = currentStage >= TOTAL_STAGES ? "Final stage" : "Next →";
    }
  }

  async function advanceStage() {
    if (currentStage >= TOTAL_STAGES) return;
    const reason = stageGateReason(currentStage);
    if (reason) {
      showStatus(`Stage ${currentStage} (${STAGES_BY_NUMBER[currentStage]}): ${reason}`, 0, true);
      focusStagePanel(currentStage);
      return;
    }
    if (currentStage === 2) {
      // Both directories must exist (fail-closed) before Scenes unlocks.
      const seedDir = (document.getElementById("output-dir")?.value || "").trim();
      const scratchDir = (document.getElementById("scratch-dir")?.value || "").trim();
      showStatus("Checking directories...", 0);
      const [seedOk, scratchOk] = await Promise.all([
        directoryExists(seedDir),
        directoryExists(scratchDir)
      ]);
      const unverified = [];
      if (!seedOk) unverified.push(`Seed directory "${seedDir}"`);
      if (!scratchOk) unverified.push(`Scratch directory "${scratchDir}"`);
      if (unverified.length > 0) {
        showStatus(`Stage 2 (Locations): ${unverified.join(" and ")} not found or could not be verified on disk. Check the path(s) before continuing.`, 0, true);
        focusStagePanel(2);
        return;
      }
    }
    currentStage += 1;
    if (currentStage > maxStageReached) maxStageReached = currentStage;
    renderStageState();
    showStatus(`Stage ${currentStage} (${STAGES_BY_NUMBER[currentStage]})`, 0, false);
    focusStagePanel(currentStage);
  }

  function retreatStage() {
    if (currentStage <= 1) return;
    currentStage -= 1;
    renderStageState();
    focusStagePanel(currentStage);
  }

  function jumpToStage(stage) {
    const n = parseInt(stage, 10);
    if (isNaN(n) || n < 1 || n > TOTAL_STAGES) return;
    if (n > maxStageReached || n === currentStage) return;
    currentStage = n;
    renderStageState();
    focusStagePanel(currentStage);
  }

  // =========================================================================
  // 10. Server Filesystem Directory Browser
  // =========================================================================
  let currentBrowsingPath = "";
  let currentBrowsingParent = null;
  let selectedBrowsingPath = "";
  let dirBrowserTargetId = "output-dir";

  function updateSelectedDisplay() {
    const displayEl = document.getElementById("dir-selected-display");
    if (displayEl) {
      displayEl.innerText = selectedBrowsingPath || currentBrowsingPath || "(None)";
    }
  }

  function openDirectoryBrowser(targetInputId = "output-dir") {
    if (uiBusy) return;
    const modal = document.getElementById("dir-browser-modal");
    if (!modal) return;
    dirBrowserTargetId = targetInputId || "output-dir";

    const currentInput = (document.getElementById(dirBrowserTargetId)?.value || "").trim();
    selectedBrowsingPath = currentInput || "";
    updateSelectedDisplay();

    modal.style.display = "flex";
    document.addEventListener("keydown", handleDirModalKeydown);

    const pathInput = document.getElementById("dir-current-path");
    if (pathInput) {
      pathInput.value = currentInput;
      setTimeout(() => pathInput.focus(), 50);
    }

    loadDirectory(currentInput || "");
  }

  function closeDirectoryBrowser() {
    const modal = document.getElementById("dir-browser-modal");
    if (modal) {
      modal.style.display = "none";
    }
    document.removeEventListener("keydown", handleDirModalKeydown);
  }

  function handleDirModalKeydown(e) {
    if (e.key === "Escape") {
      closeDirectoryBrowser();
    }
  }

  async function loadDirectory(targetPath) {
    const listContainer = document.getElementById("dir-browser-list");
    const pathInput = document.getElementById("dir-current-path");
    const statusBar = document.getElementById("dir-browser-status");
    const upBtn = document.getElementById("btn-dir-up");

    if (!listContainer) return;

    listContainer.innerHTML = `
      <div style="text-align: center; padding: 30px; color: var(--text-muted);">
        ⏳ Loading directory contents...
      </div>
    `;
    if (statusBar) statusBar.style.display = "none";

    try {
      const query = `
        query Directory($path: String) {
          directory(path: $path) {
            path
            parent
            directories
          }
        }
      `;
      const data = await executeGraphQL(query, { path: targetPath || null });
      const dirData = data?.directory;

      if (!dirData) {
        throw new Error("Directory information not returned by server.");
      }

      currentBrowsingPath = dirData.path || "";
      currentBrowsingParent = dirData.parent || null;

      if (pathInput) {
        pathInput.value = currentBrowsingPath;
      }

      if (currentBrowsingPath) {
        selectedBrowsingPath = currentBrowsingPath;
      }
      updateSelectedDisplay();

      if (upBtn) {
        const canGoUp = Boolean(
          (currentBrowsingParent != null && currentBrowsingParent !== currentBrowsingPath) ||
          (currentBrowsingPath && currentBrowsingPath.length > 0)
        );
        upBtn.disabled = !canGoUp;
      }

      renderDirectoryEntries(dirData.directories || []);
    } catch (err) {
      const errorMsg = err.message || String(err);
      if (statusBar) {
        statusBar.innerText = `⚠️ Failed to load directory "${targetPath || "Roots"}": ${errorMsg}`;
        statusBar.style.display = "block";
      }

      listContainer.innerHTML = `
        <div style="text-align: center; padding: 24px; color: var(--danger);">
          <div style="margin-bottom: 8px; font-weight: 600;">Failed to open directory</div>
          <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 16px;">${escapeHtml(errorMsg)}</div>
          <button type="button" class="btn btn-secondary" id="btn-dir-fallback-root" style="padding: 4px 12px; font-size: 0.85rem;">
            💽 Browse Drive Roots
          </button>
        </div>
      `;

      const rootBtn = document.getElementById("btn-dir-fallback-root");
      if (rootBtn) {
        rootBtn.addEventListener("click", () => loadDirectory(""));
      }
    }
  }

  function renderDirectoryEntries(directories) {
    const listContainer = document.getElementById("dir-browser-list");
    if (!listContainer) return;
    listContainer.innerHTML = "";

    if (!directories || directories.length === 0) {
      listContainer.innerHTML = `
        <div style="text-align: center; padding: 30px; color: var(--text-muted); font-style: italic;">
          📁 (Empty directory or no subdirectories found)
        </div>
      `;
      return;
    }

    directories.forEach((dirEntry) => {
      let entryFullPath = dirEntry;
      const isAbsolute = /^[a-zA-Z]:[\\/]/.test(dirEntry) || dirEntry.startsWith("/") || dirEntry.startsWith("\\\\");
      if (!isAbsolute && currentBrowsingPath) {
        const sep = currentBrowsingPath.includes("/") ? "/" : "\\";
        const base = currentBrowsingPath.endsWith(sep) ? currentBrowsingPath : currentBrowsingPath + sep;
        entryFullPath = base + dirEntry;
      }

      const isDrive = /^[a-zA-Z]:[\\/]?$/.test(dirEntry);
      const icon = isDrive ? "💽" : "📁";

      let entryDisplayName = dirEntry;
      if (isDrive) {
        entryDisplayName = dirEntry.includes("\\") || dirEntry.includes("/") ? dirEntry : dirEntry + "\\";
      } else {
        const cleaned = dirEntry.replace(/[\\/]+$/, "");
        const parts = cleaned.split(/[\\/]/);
        entryDisplayName = parts[parts.length - 1] || dirEntry;
      }

      const row = document.createElement("div");
      row.className = "dir-entry";
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.setAttribute("aria-label", `Folder ${entryDisplayName}`);
      if (selectedBrowsingPath === entryFullPath) {
        row.classList.add("selected");
      }
      row.dataset.path = entryFullPath;

      row.innerHTML = `
        <span class="dir-entry-icon">${icon}</span>
        <span class="dir-entry-name">${escapeHtml(entryDisplayName)}</span>
      `;

      const selectEntry = () => {
        document.querySelectorAll(".dir-entry").forEach((el) => el.classList.remove("selected"));
        row.classList.add("selected");
        selectedBrowsingPath = entryFullPath;
        updateSelectedDisplay();
      };

      row.addEventListener("click", selectEntry);

      row.addEventListener("dblclick", () => {
        loadDirectory(entryFullPath);
      });

      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          loadDirectory(entryFullPath);
        } else if (e.key === " " || e.key === "Spacebar" || e.key === "Space" || e.code === "Space") {
          e.preventDefault();
          selectEntry();
        }
      });

      listContainer.appendChild(row);
    });
  }

  function navigateUp() {
    if (currentBrowsingParent != null && currentBrowsingParent !== currentBrowsingPath) {
      loadDirectory(currentBrowsingParent);
      return;
    }
    if (currentBrowsingPath) {
      const cleaned = currentBrowsingPath.replace(/[\\/]+$/, "");
      const idx = Math.max(cleaned.lastIndexOf("/"), cleaned.lastIndexOf("\\"));
      if (idx > 0) {
        const parent = cleaned.slice(0, idx);
        if (/^[a-zA-Z]:$/.test(parent)) {
          loadDirectory(parent + "\\");
        } else {
          loadDirectory(parent);
        }
      } else if (idx === 0) {
        loadDirectory("/");
      } else {
        loadDirectory("");
      }
    } else {
      loadDirectory("");
    }
  }

  function confirmDirectorySelection() {
    const chosenPath = selectedBrowsingPath || currentBrowsingPath;
    if (chosenPath) {
      const targetInput = document.getElementById(dirBrowserTargetId);
      if (targetInput) {
        targetInput.value = chosenPath;
        targetInput.dispatchEvent(new Event("change", { bubbles: true }));
        targetInput.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
    closeDirectoryBrowser();
  }

  function initEmporniumReview(ids, token, mode) {
    if (ids && Array.isArray(ids)) {
      window._emporniumSceneIds = ids.map((id) => parseInt(id, 10)).filter((id) => !isNaN(id) && id > 0);
    }
    if (token) {
      window._emporniumToken = token;
    }
    const initialMode = mode || window._emporniumMode || urlParams.get("mode") || (ids?.length === 1 ? "single" : "megapack");
    currentStage = 1;
    maxStageReached = 1;
    bindDomEvents();
    renderStageState();
    setMode(initialMode);
    loadScenes(ids, token);
    prefillScratchDirFromHealth();
    refreshSidecarStatus();
  }

  // Window exports for tests and integrations
  window.initEmporniumReview = initEmporniumReview;
  window.startJobPolling = startJobPolling;
  window.onTaskComplete = onTaskComplete;
  window.loadScenes = loadScenes;
  window.resolveToken = resolveToken;
  window.fetchScenesChunked = fetchScenesChunked;
  window.openDirectoryBrowser = openDirectoryBrowser;
  window.closeDirectoryBrowser = closeDirectoryBrowser;
  window.loadDirectory = loadDirectory;
  window.confirmDirectorySelection = confirmDirectorySelection;
  window.removeSceneFromPack = removeSceneFromPack;
  window.restoreAllScenes = restoreAllScenes;
  window.keepSceneInCollisionGroup = keepSceneInCollisionGroup;
  window.formatFileSize = formatFileSize;
  window.formatDuration = formatDuration;
  window.formatResolution = formatResolution;
  window.getEffectiveResolution = getEffectiveResolution;
  window.formatCodec = formatCodec;
  window.activeScenes = activeScenes;
  window.computeDuplicateGroups = computeDuplicateGroups;
  window.consolidateFiles = consolidateFiles;
  window.refreshSidecarStatus = refreshSidecarStatus;
  window.stopSidecar = stopSidecar;
  window.consolidatedFileIds = consolidatedFileIds;
  window.buildMegapack = buildMegapack;
  window.setMode = setMode;
  window.updateBBCode = updateBBCode;
  window.updateActionAvailability = updateActionAvailability;
  window.generateRunId = generateRunId;
  window.findFailureSentinel = findFailureSentinel;
  window.findResultSentinel = findResultSentinel;
  window.handleJobUpdate = handleJobUpdate;
  window.trackJobProgress = trackJobProgress;
  window.findDestinationCollisions = findDestinationCollisions;
  window.pathExistsBatch = pathExistsBatch;
  window.nextFreeName = nextFreeName;
  window.openDestinationCollisionDialog = openDestinationCollisionDialog;
  window.closeDestinationCollisionDialog = closeDestinationCollisionDialog;
  window.stashUiOrigin = stashUiOrigin;
  window.formatBitrateMbps = formatBitrateMbps;
  window.sanitizeName = sanitizeName;
  window.getPackDestinationFolder = getPackDestinationFolder;
  window.empifyTag = empifyTag;
  window.resolveTags = resolveTags;
  window.fetchVocabulary = fetchVocabulary;
  window.getCachedVocabulary = () => cachedVocabulary;
  window.setCachedVocabulary = (v) => { cachedVocabulary = v; };
  window.isPathUnderSeed = isPathUnderSeed;
  window.computeMissingSeedFiles = computeMissingSeedFiles;
  window.findBBCodeSentinel = findBBCodeSentinel;
  window.handleCoverImageFile = handleCoverImageFile;
  window.uploadCoverImage = uploadCoverImage;
  window.removeCoverImage = removeCoverImage;
  window.getCurrentCoverUrl = () => currentCoverUrl;
  window.getWizardStage = getWizardStage;
  window.handledJobIds = handledJobIds;
  window.showStatus = showStatus;
  window.renderScenes = renderScenes;
  window.runExclusive = runExclusive;
  window.setUiBusy = setUiBusy;
  window.applyBusyLock = applyBusyLock;
  window.updateBusyEscapeHatch = updateBusyEscapeHatch;
  window.getUiBusy = () => uiBusy;
  window.getBusyOperation = () => busyOperation;
  window.setBusyStartedAt = (ts) => { busyStartedAt = ts; };
  window.onTaskComplete = onTaskComplete;
  window.handleJobUpdate = handleJobUpdate;

  // Prefill the scratch dir from the backend /health payload (todo 8 added
  // the field there). Best-effort: a down sidecar, non-200, or missing field
  // leaves the input untouched. NEVER clobbers user input: the value is only
  // written while the field is still empty.
  async function prefillScratchDirFromHealth() {
    const input = document.getElementById("scratch-dir");
    if (!input) return;
    const endpoints = backendEndpoints("/health");
    for (const url of endpoints) {
      let data = null;
      try {
        const response = await fetch(url);
        if (!response.ok) continue;
        data = await response.json();
      } catch (err) {
        continue;
      }
      const scratch = String(data?.scratch_dir || "").trim();
      if (scratch && !(input.value || "").trim()) {
        input.value = scratch;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      return;
    }
  }

  // Sidecar refresh state & candidate probe helper
  let activeSidecarRefreshPromise = null;
  const MAX_START_BACKEND_DISPATCHES = 3;
  let startBackendDispatchCount = 0;

  const JOB_QUEUE_QUERY = `
    query JobQueue {
      jobQueue {
        id
        status
        description
        startTime
      }
    }
  `;

  async function fetchJobQueue() {
    try {
      const data = await executeGraphQL(JOB_QUEUE_QUERY);
      if (!data) return null;
      if (data.jobQueue === null || data.jobQueue === undefined) {
        return [];
      }
      if (Array.isArray(data.jobQueue)) {
        return data.jobQueue;
      }
      return null;
    } catch (err) {
      // Fail safe: return null on query error or network failure
      return null;
    }
  }

  function isStartBackendDescription(desc) {
    if (typeof desc !== "string") return false;
    return /^Running plugin task:\s*StartBackend(?:\s|$)/.test(desc.trim());
  }

  function isPendingStartBackend(job) {
    if (!job || typeof job !== "object") return false;
    if (job.status !== "READY" && job.status !== "RUNNING") return false;
    return isStartBackendDescription(job.description);
  }

  function formatAheadTaskName(desc) {
    if (!desc || typeof desc !== "string") return "";
    return desc.replace(/^Running plugin task:\s*/, "").trim();
  }

  function formatStartTime(st) {
    if (!st) return "";
    try {
      const d = new Date(st);
      if (!isNaN(d.getTime())) {
        return d.toLocaleTimeString([], { hour12: false });
      }
    } catch (_) {}
    return String(st);
  }

  function getQueuedStatusText(queue, targetJob) {
    if (!Array.isArray(queue)) return "Sidecar: start queued — waiting in job queue";
    const runningJob = queue.find((j) => j.status === "RUNNING" && j !== targetJob);
    const aheadJob = runningJob || queue.find((j) => (j.status === "READY" || j.status === "RUNNING") && j !== targetJob);
    if (aheadJob) {
      const name = formatAheadTaskName(aheadJob.description) || "another job";
      const time = formatStartTime(aheadJob.startTime);
      return `Sidecar: start queued — waiting on ${name}${time ? ` (started ${time})` : ""}`;
    }
    return "Sidecar: start queued — waiting in job queue";
  }

  async function probeHealthCandidates() {
    const endpoints = backendEndpoints("/health");
    let version = null; // parsed from an ok+JSON candidate
    let sawRunning = false; // any ok HTTP response, even with a malformed body
    for (const url of endpoints) {
      try {
        const response = await fetch(url);
        if (!response.ok) continue;
        sawRunning = true;
        try {
          const data = await response.json();
          const v = String(data?.version ?? "").trim();
          if (v) {
            version = v;
            break;
          }
        } catch (jsonErr) {
          // Malformed body on ok response
        }
      } catch (err) {
        // Network error for this candidate
      }
    }
    return { ok: sawRunning || version !== null, version, sawRunning };
  }

  async function _doRefreshSidecarStatus() {
    const badge = document.getElementById("sidecar-status");
    const btnStop = document.getElementById("btn-sidecar-stop");
    if (!badge) return;

    const initialProbe = await probeHealthCandidates();
    if (initialProbe.ok) {
      startBackendDispatchCount = 0;
      if (initialProbe.version === EXPECTED_SIDECAR_VERSION) {
        badge.textContent = `Sidecar: connected (v${initialProbe.version})`;
        badge.className = "sidecar-status sidecar-ok";
      } else {
        badge.textContent = `Sidecar: outdated (v${initialProbe.version || "?"}, expected ${EXPECTED_SIDECAR_VERSION}) — restart via start_backend.ps1`;
        badge.className = "sidecar-status sidecar-warn";
      }
      if (btnStop) btnStop.style.display = "inline-flex";
      prefillScratchDirFromHealth();
      return;
    }

    if (btnStop) btnStop.style.display = "none";

    const queue = await fetchJobQueue();
    const pendingJob = Array.isArray(queue) ? queue.find(isPendingStartBackend) : null;

    if (pendingJob) {
      // F1: Skip dispatch if StartBackend is already READY or RUNNING
      if (pendingJob.status === "RUNNING") {
        badge.textContent = "Sidecar: starting…";
        badge.className = "sidecar-status sidecar-warn";
      } else {
        badge.textContent = getQueuedStatusText(queue, pendingJob);
        badge.className = "sidecar-status sidecar-warn";
      }
    } else {
      // Change B: Suppress StartBackend auto-dispatch while UI is busy to prevent queue flooding
      if (uiBusy) {
        const runningJob = Array.isArray(queue) ? queue.find((j) => j.status === "RUNNING") : null;
        if (runningJob) {
          badge.textContent = getQueuedStatusText(queue, null);
          badge.className = "sidecar-status sidecar-warn";
        } else {
          badge.textContent = "Sidecar: NOT RUNNING — run start_backend.ps1";
          badge.className = "sidecar-status sidecar-bad";
        }
        return;
      }

      // F3: Bound the retry loop
      if (startBackendDispatchCount >= MAX_START_BACKEND_DISPATCHES) {
        badge.textContent = "Sidecar: failed to start — run start_backend.ps1";
        badge.className = "sidecar-status sidecar-bad";
        return;
      }

      // Initial badge state before dispatching
      const runningJob = Array.isArray(queue) ? queue.find((j) => j.status === "RUNNING") : null;
      if (runningJob) {
        badge.textContent = getQueuedStatusText(queue, null);
        badge.className = "sidecar-status sidecar-warn";
      } else {
        badge.textContent = "Sidecar: starting…";
        badge.className = "sidecar-status sidecar-warn";
      }

      try {
        const query = `
          mutation RunStartBackend($plugin_id: ID!, $task_name: String!, $args: [PluginArgInput!]) {
            runPluginTask(
              plugin_id: $plugin_id,
              task_name: $task_name,
              args: $args
            )
          }
        `;
        await executeGraphQL(query, {
          plugin_id: PLUGIN_ID,
          task_name: "StartBackend",
          args: [{ key: "mode", value: { str: "start_backend" } }]
        });
        startBackendDispatchCount++;
      } catch (err) {
        badge.textContent = "Sidecar: failed to start — run start_backend.ps1";
        badge.className = "sidecar-status sidecar-bad";
        return;
      }
    }

    // Poll /health with bounded timeout (~10s, 500ms intervals)
    const pollDeadline = Date.now() + 10000;
    const pollInterval = 500;
    let running = false;
    let runningVersion = null;

    while (Date.now() < pollDeadline) {
      await new Promise((r) => setTimeout(r, pollInterval));
      const probe = await probeHealthCandidates();
      if (probe.ok) {
        running = true;
        runningVersion = probe.version;
        break;
      }
    }

    if (running) {
      startBackendDispatchCount = 0;
      if (runningVersion === EXPECTED_SIDECAR_VERSION) {
        badge.textContent = `Sidecar: connected (v${runningVersion})`;
        badge.className = "sidecar-status sidecar-ok";
      } else {
        badge.textContent = `Sidecar: outdated (v${runningVersion || "?"}, expected ${EXPECTED_SIDECAR_VERSION}) — restart via start_backend.ps1`;
        badge.className = "sidecar-status sidecar-warn";
      }
      if (btnStop) btnStop.style.display = "inline-flex";
      prefillScratchDirFromHealth();
    } else {
      // Health probe timed out. Consult queue before claiming failure.
      const finalQueue = await fetchJobQueue();
      const stillPending = Array.isArray(finalQueue) ? finalQueue.find(isPendingStartBackend) : null;
      const blockingJob = Array.isArray(finalQueue) ? finalQueue.find((j) => j.status === "RUNNING" && j !== stillPending) : null;

      if (stillPending) {
        if (stillPending.status === "RUNNING") {
          badge.textContent = "Sidecar: starting…";
          badge.className = "sidecar-status sidecar-warn";
        } else {
          badge.textContent = getQueuedStatusText(finalQueue, stillPending);
          badge.className = "sidecar-status sidecar-warn";
        }
      } else if (blockingJob) {
        badge.textContent = getQueuedStatusText(finalQueue, null);
        badge.className = "sidecar-status sidecar-warn";
      } else {
        badge.textContent = "Sidecar: failed to start — run start_backend.ps1";
        badge.className = "sidecar-status sidecar-bad";
      }
      if (btnStop) btnStop.style.display = "none";
    }
  }

  // Live sidecar status badge (header pill). Debounced so rapid or repeated
  // calls reuse in-flight task / poll cycles and never flood duplicate StartBackend tasks.
  async function refreshSidecarStatus() {
    if (activeSidecarRefreshPromise) {
      return activeSidecarRefreshPromise;
    }
    activeSidecarRefreshPromise = (async () => {
      try {
        await _doRefreshSidecarStatus();
      } catch (err) {
        // Best-effort: never throw into the UI.
      } finally {
        activeSidecarRefreshPromise = null;
      }
    })();
    return activeSidecarRefreshPromise;
  }

  // Graceful sidecar shutdown via POST /api/shutdown
  async function stopSidecar() {
    const btn = document.getElementById("btn-sidecar-stop");
    const badge = document.getElementById("sidecar-status");
    if (btn) btn.disabled = true;
    if (badge) {
      badge.textContent = "Sidecar: stopping…";
      badge.className = "sidecar-status sidecar-warn";
    }

    const endpoints = backendEndpoints("/api/shutdown");
    for (const url of endpoints) {
      try {
        const resp = await fetch(url, { method: "POST" });
        if (resp.ok) break;
      } catch (err) {
        // Best-effort: ignore network drops during process termination
      }
    }

    if (badge) {
      badge.textContent = "Sidecar: NOT RUNNING — run start_backend.ps1";
      badge.className = "sidecar-status sidecar-bad";
    }
    if (btn) {
      btn.style.display = "none";
      btn.disabled = false;
    }
  }

  function bindDomEvents() {
    const coverPasteZone = document.getElementById("cover-paste-zone");
    if (coverPasteZone && !coverPasteZone.dataset.bound) {
      coverPasteZone.dataset.bound = "true";
      coverPasteZone.addEventListener("paste", (e) => {
        if (uiBusy) return;
        const items = e.clipboardData?.items;
        if (!items) return;
        for (let i = 0; i < items.length; i++) {
          if (items[i].type && items[i].type.startsWith("image/")) {
            const file = items[i].getAsFile();
            if (file) {
              e.preventDefault();
              handleCoverImageFile(file);
              break;
            }
          }
        }
      });
      coverPasteZone.addEventListener("dragover", (e) => {
        if (uiBusy) return;
        e.preventDefault();
        coverPasteZone.style.borderColor = "#3b82f6";
      });
      coverPasteZone.addEventListener("dragleave", (e) => {
        e.preventDefault();
        coverPasteZone.style.borderColor = "#4b5563";
      });
      coverPasteZone.addEventListener("drop", (e) => {
        if (uiBusy) return;
        e.preventDefault();
        coverPasteZone.style.borderColor = "#4b5563";
        const files = e.dataTransfer?.files;
        if (files && files.length > 0) {
          for (let i = 0; i < files.length; i++) {
            if (files[i].type && files[i].type.startsWith("image/")) {
              handleCoverImageFile(files[i]);
              break;
            }
          }
        }
      });
    }

    const linkChooseCover = document.getElementById("link-choose-cover");
    const coverFileInput = document.getElementById("cover-file-input");
    if (linkChooseCover && !linkChooseCover.dataset.bound) {
      linkChooseCover.dataset.bound = "true";
      linkChooseCover.addEventListener("click", (e) => {
        if (uiBusy) return;
        e.preventDefault();
        e.stopPropagation();
        if (coverFileInput) coverFileInput.click();
      });
    }

    if (coverFileInput && !coverFileInput.dataset.bound) {
      coverFileInput.dataset.bound = "true";
      coverFileInput.addEventListener("change", (e) => {
        const files = e.target.files;
        if (files && files.length > 0) {
          handleCoverImageFile(files[0]);
        }
      });
    }

    const btnRemoveCover = document.getElementById("btn-remove-cover");
    if (btnRemoveCover && !btnRemoveCover.dataset.bound) {
      btnRemoveCover.dataset.bound = "true";
      btnRemoveCover.addEventListener("click", (e) => {
        e.preventDefault();
        removeCoverImage();
      });
    }

    const radioMegapack = document.getElementById("mode-megapack");
    if (radioMegapack && !radioMegapack.dataset.bound) {
      radioMegapack.dataset.bound = "true";
      radioMegapack.addEventListener("change", () => {
        if (radioMegapack.checked) setMode("megapack", true);
      });
    }

    const radioSingle = document.getElementById("mode-single");
    if (radioSingle && !radioSingle.dataset.bound) {
      radioSingle.dataset.bound = "true";
      radioSingle.addEventListener("change", () => {
        if (radioSingle.checked) setMode("single", true);
      });
    }

    const keepFirstBtn = document.getElementById("btn-keep-first");
    if (keepFirstBtn && !keepFirstBtn.dataset.bound) {
      keepFirstBtn.dataset.bound = "true";
      keepFirstBtn.addEventListener("click", () => {
        if (duplicateGroups.length === 0) return;
        for (const group of duplicateGroups) {
          if (group.members && group.members.length > 0) {
            const seenSceneIds = new Set();
            let firstSceneId = null;
            for (const member of group.members) {
              const sid = String(member.sceneId);
              if (!seenSceneIds.has(sid)) {
                seenSceneIds.add(sid);
                if (firstSceneId === null) {
                  firstSceneId = sid;
                } else {
                  excludedSceneIds.add(sid);
                }
              }
            }
          }
        }
        showOnlyConflicts = false;
        renderScenes();
        updateBBCode();
      });
    }

    const filterConflictsBtn = document.getElementById("btn-filter-conflicts");
    if (filterConflictsBtn && !filterConflictsBtn.dataset.bound) {
      filterConflictsBtn.dataset.bound = "true";
      filterConflictsBtn.addEventListener("click", () => {
        showOnlyConflicts = !showOnlyConflicts;
        renderScenes();
      });
    }

    const copyBbcodeBtn = document.getElementById("btn-copy-bbcode");
    if (copyBbcodeBtn && !copyBbcodeBtn.dataset.bound) {
      copyBbcodeBtn.dataset.bound = "true";
      copyBbcodeBtn.addEventListener("click", () => {
        if (copyBbcodeBtn.disabled) return;
        const text = document.getElementById("bbcode-preview")?.value || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard
            .writeText(text)
            .then(() => {
              copyBbcodeBtn.innerText = "✅ Copied!";
              setTimeout(() => {
                copyBbcodeBtn.innerText = "📋 Copy";
              }, 2000);
            })
            .catch(() => {});
        }
      });
    }

    const closeHeaderBtn = document.getElementById("btn-header-close");
    if (closeHeaderBtn && !closeHeaderBtn.dataset.bound) {
      closeHeaderBtn.dataset.bound = "true";
      closeHeaderBtn.addEventListener("click", () => {
        if (typeof window._emporniumCloseModal === "function") {
          window._emporniumCloseModal();
        } else if (window.parent && window.parent !== window) {
          window.parent.postMessage({ type: "EMPORNIUM_CLOSE_MODAL" }, "*");
        }
      });
    }

    const stopSidecarBtn = document.getElementById("btn-sidecar-stop");
    if (stopSidecarBtn && !stopSidecarBtn.dataset.bound) {
      stopSidecarBtn.dataset.bound = "true";
      stopSidecarBtn.addEventListener("click", stopSidecar);
    }

    const browseDirBtn = document.getElementById("btn-browse-dir");
    if (browseDirBtn && !browseDirBtn.dataset.bound) {
      browseDirBtn.dataset.bound = "true";
      browseDirBtn.addEventListener("click", () => openDirectoryBrowser("output-dir"));
    }

    const browseScratchBtn = document.getElementById("btn-browse-scratch");
    if (browseScratchBtn && !browseScratchBtn.dataset.bound) {
      browseScratchBtn.dataset.bound = "true";
      browseScratchBtn.addEventListener("click", () => openDirectoryBrowser("scratch-dir"));
    }

    const closeDirModalBtn = document.getElementById("btn-close-dir-modal");
    if (closeDirModalBtn && !closeDirModalBtn.dataset.bound) {
      closeDirModalBtn.dataset.bound = "true";
      closeDirModalBtn.addEventListener("click", closeDirectoryBrowser);
    }

    const cancelDirBtn = document.getElementById("btn-cancel-dir");
    if (cancelDirBtn && !cancelDirBtn.dataset.bound) {
      cancelDirBtn.dataset.bound = "true";
      cancelDirBtn.addEventListener("click", closeDirectoryBrowser);
    }

    const selectDirBtn = document.getElementById("btn-select-dir");
    if (selectDirBtn && !selectDirBtn.dataset.bound) {
      selectDirBtn.dataset.bound = "true";
      selectDirBtn.addEventListener("click", confirmDirectorySelection);
    }

    const dirUpBtn = document.getElementById("btn-dir-up");
    if (dirUpBtn && !dirUpBtn.dataset.bound) {
      dirUpBtn.dataset.bound = "true";
      dirUpBtn.addEventListener("click", navigateUp);
    }

    const dirRootsBtn = document.getElementById("btn-dir-roots");
    if (dirRootsBtn && !dirRootsBtn.dataset.bound) {
      dirRootsBtn.dataset.bound = "true";
      dirRootsBtn.addEventListener("click", () => loadDirectory(""));
    }

    const dirRefreshBtn = document.getElementById("btn-dir-refresh");
    if (dirRefreshBtn && !dirRefreshBtn.dataset.bound) {
      dirRefreshBtn.dataset.bound = "true";
      dirRefreshBtn.addEventListener("click", () => loadDirectory(currentBrowsingPath));
    }

    const dirGoBtn = document.getElementById("btn-dir-go");
    if (dirGoBtn && !dirGoBtn.dataset.bound) {
      dirGoBtn.dataset.bound = "true";
      dirGoBtn.addEventListener("click", () => {
        const p = (document.getElementById("dir-current-path")?.value || "").trim();
        loadDirectory(p);
      });
    }

    const dirPathInput = document.getElementById("dir-current-path");
    if (dirPathInput && !dirPathInput.dataset.bound) {
      dirPathInput.dataset.bound = "true";
      dirPathInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          const p = (e.target.value || "").trim();
          loadDirectory(p);
        }
      });
    }

    const dirModalOverlay = document.getElementById("dir-browser-modal");
    if (dirModalOverlay && !dirModalOverlay.dataset.bound) {
      dirModalOverlay.dataset.bound = "true";
      dirModalOverlay.addEventListener("click", (e) => {
        if (e.target === dirModalOverlay) {
          closeDirectoryBrowser();
        }
      });
    }

    const probeBtn = document.getElementById("btn-probe");
    if (probeBtn && !probeBtn.dataset.bound) {
      probeBtn.dataset.bound = "true";
      probeBtn.addEventListener("click", () => runExclusive("mutation", "ProbeFiles", probeFiles));
    }

    const consolidateBtn = document.getElementById("btn-consolidate");
    if (consolidateBtn && !consolidateBtn.dataset.bound) {
      consolidateBtn.dataset.bound = "true";
      consolidateBtn.addEventListener("click", () => runExclusive("mutation", "Consolidate", consolidateFiles));
    }

    const buildBtn = document.getElementById("btn-build");
    if (buildBtn && !buildBtn.dataset.bound) {
      buildBtn.dataset.bound = "true";
      buildBtn.addEventListener("click", () => runExclusive("build", "BuildMegapack", buildMegapack));
    }

    const btnBusyUnlock = document.getElementById("btn-busy-unlock");
    if (btnBusyUnlock && !btnBusyUnlock.dataset.bound) {
      btnBusyUnlock.dataset.bound = "true";
      btnBusyUnlock.addEventListener("click", () => {
        setUiBusy(false);
        showStatus("Controls unlocked manually. The Stash job may still be running — check the Task Manager before starting another build.", 0, false);
      });
    }

    const titleInput = document.getElementById("pack-title");
    if (titleInput && !titleInput.dataset.bound) {
      titleInput.dataset.bound = "true";
      titleInput.addEventListener("input", () => {
        hasUserEditedTitle = true;
        updateBBCode();
        updateActionAvailability();
      });
      titleInput.addEventListener("change", () => {
        hasUserEditedTitle = true;
        updateBBCode();
        updateActionAvailability();
      });
    }

    const notesInput = document.getElementById("pack-notes");
    if (notesInput && !notesInput.dataset.bound) {
      notesInput.dataset.bound = "true";
      notesInput.addEventListener("input", updateBBCode);
    }

    const outputDirInput = document.getElementById("output-dir");
    if (outputDirInput && !outputDirInput.dataset.boundDir) {
      outputDirInput.dataset.boundDir = "true";
      outputDirInput.addEventListener("input", updateActionAvailability);
      outputDirInput.addEventListener("change", updateActionAvailability);
    }

    const stageBackBtn = document.getElementById("btn-stage-back");
    if (stageBackBtn && !stageBackBtn.dataset.bound) {
      stageBackBtn.dataset.bound = "true";
      stageBackBtn.addEventListener("click", retreatStage);
    }

    const stageNextBtn = document.getElementById("btn-stage-next");
    if (stageNextBtn && !stageNextBtn.dataset.bound) {
      stageNextBtn.dataset.bound = "true";
      stageNextBtn.addEventListener("click", advanceStage);
    }

    for (let n = 1; n <= TOTAL_STAGES; n++) {
      const railItem = document.getElementById(`stage-item-${n}`);
      if (railItem && !railItem.dataset.bound) {
        railItem.dataset.bound = "true";
        railItem.addEventListener("click", () => jumpToStage(n));
      }
    }

    renderStageState();
    initBBCodeToolbar();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindDomEvents);
  } else {
    bindDomEvents();
  }

  // Initial Load
  loadScenes();
  prefillScratchDirFromHealth();
  refreshSidecarStatus();
  fetchVocabulary();
})();

