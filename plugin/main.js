/**
 * Empornium Megapack Builder - In-Stash Plugin UI Injection
 * Hooks into Stash scene selection and opens an embedded review modal.
 */

(function () {
  "use strict";

  const PLUGIN_ID = "empornium-megapack";
  const BUTTON_ID = "empornium-megapack-btn";
  const HISTORY_BUTTON_ID = "empornium-history-btn";
  const MODAL_ID = "empornium-megapack-modal";

  const INJECTED_STYLE_ID = "empornium-review-injected-style";

  let activeEscHandler = null;

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function getSelectedSceneIds() {
    const checkedInputs = Array.from(document.querySelectorAll(
      ".scene-card input[type='checkbox']:checked, " +
      "tr.scene-row input[type='checkbox']:checked, " +
      "input.card-check:checked, " +
      "input.search-item-check:checked, " +
      "input.wall-item-check:checked, " +
      "tr input[type='checkbox']:checked"
    ));

    const ids = checkedInputs.map((el) => {
      if (el.value && !isNaN(Number(el.value)) && Number(el.value) > 0) {
        return String(el.value);
      }
      const card = el.closest(".scene-card, tr.scene-row, .wall-item, tr");
      if (card) {
        const id = card.getAttribute("data-scene-id") || card.getAttribute("data-id");
        if (id) return String(id);
        const link = card.querySelector('a[href^="/scenes/"]');
        if (link) {
          const match = link.getAttribute("href")?.match(/\/scenes\/(\d+)/);
          if (match) return match[1];
        }
      }
      return null;
    }).filter(Boolean);

    // Fallback: check URL if viewing a single scene
    if (ids.length === 0) {
      const match = window.location.pathname.match(/\/scenes\/(\d+)/);
      if (match) {
        ids.push(match[1]);
      }
    }

    return Array.from(new Set(ids));
  }

  async function createToken(sceneIds) {
    const intIds = sceneIds
      .map((id) => parseInt(id, 10))
      .filter((id) => !isNaN(id) && id > 0);

    if (intIds.length === 0) {
      throw new Error("No valid scene IDs provided");
    }

    // The sidecar binds 127.0.0.1:9941 only — always target the loopback
    // explicitly. Hostname-derived URLs break when Stash is served from a
    // non-loopback host (and violate the CSP connect-src allowlist), and
    // Stash's /plugin/{id}/ route serves static assets, never the sidecar API.
    const endpoints = [
      "http://127.0.0.1:9941/api/token",
      "http://localhost:9941/api/token"
    ];

    let lastError = null;
    for (const url of endpoints) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 400);
        const response = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sceneIds: intIds }),
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        if (response.ok) {
          const data = await response.json();
          if (data && data.token) {
            return data.token;
          }
        }
      } catch (err) {
        lastError = err;
      }
    }
    throw lastError || new Error("Failed to create scene token on any endpoint");
  }

  async function openMegapackModal(sceneIds, mode) {
    // Remove existing modal if any
    closeMegapackModal();

    const resolvedMode = mode || (sceneIds.length === 1 ? "single" : "megapack");
    window._emporniumSceneIds = sceneIds.map((id) => parseInt(id, 10)).filter((id) => !isNaN(id) && id > 0);
    window._emporniumMode = resolvedMode;
    window._emporniumCloseModal = closeMegapackModal;

    const overlay = document.createElement("div");
    overlay.id = MODAL_ID;
    overlay.className = "empornium-modal-overlay";

    const container = document.createElement("div");
    container.className = "empornium-modal-container";

    const isSingle = resolvedMode === "single";
    const modalTitle = resolvedMode === "history"
      ? "Empornium Past Runs & History"
      : (isSingle ? "Empornium Single-Scene Uploader" : "Empornium Megapack Builder");
    const badgeText = resolvedMode === "history"
      ? "History"
      : `${sceneIds.length} scene(s) selected`;

    const header = document.createElement("div");
    header.className = "empornium-modal-header";
    header.innerHTML = `
      <div class="empornium-modal-title">
        <span class="empornium-logo">${resolvedMode === "history" ? "📚" : (isSingle ? "🎬" : "📦")}</span>
        <span>${modalTitle}</span>
        <span class="empornium-badge">${badgeText}</span>
      </div>
      <button class="empornium-modal-close" title="Close (Esc)">&times;</button>
    `;

    const body = document.createElement("div");
    body.className = "empornium-modal-body";

    const spinner = document.createElement("div");
    spinner.className = "empornium-modal-spinner";
    spinner.style.cssText = "padding: 32px; text-align: center; color: var(--text-color, #aaa);";
    spinner.innerHTML = `
      <div style="font-size: 24px; margin-bottom: 8px;">⏳</div>
      <div>Loading review interface…</div>
    `;

    body.appendChild(spinner);
    container.appendChild(header);
    container.appendChild(body);
    overlay.appendChild(container);
    document.body.appendChild(overlay);

    // Event handlers
    const closeBtn = header.querySelector(".empornium-modal-close");
    closeBtn.addEventListener("click", closeMegapackModal);

    function requestSafeClose() {
      if (typeof window._emporniumCanClose === "function" && !window._emporniumCanClose()) {
        if (!confirm("A task is running or a build result is open. Are you sure you want to close?")) {
          return;
        }
      }
      closeMegapackModal();
    }

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) {
        requestSafeClose();
      }
    });

    activeEscHandler = (e) => {
      if (e.key === "Escape") {
        requestSafeClose();
      }
    };
    window.addEventListener("keydown", activeEscHandler);

    const renderBlockedFallback = (reason) => {
      const safeReason = escapeHtml(reason || "This content was blocked by browser security policy.");
      const safeToken = escapeHtml(token || "none (legacy transport)");
      body.innerHTML = `
        <div class="empornium-blocked-fallback" style="padding: 24px; text-align: center; color: var(--text-color, #e0e0e0); background: var(--bg-color, #1e1e1e); border-radius: 8px; margin: 20px;">
          <div style="font-size: 28px; margin-bottom: 12px;">⚠️</div>
          <h3 style="margin-bottom: 8px; color: #ff5252;">Content Loading Blocked</h3>
          <p style="margin-bottom: 16px; color: #aaa;">${safeReason}</p>
          <div style="font-size: 12px; color: #777; margin-bottom: 16px; font-family: monospace;">
            Debug: token=${safeToken}
          </div>
          <button class="btn btn-primary empornium-retry-btn" style="padding: 8px 16px; cursor: pointer; background: #3b82f6; color: white; border: none; border-radius: 4px;">
            Retry Loading
          </button>
        </div>
      `;
      const retryBtn = body.querySelector(".empornium-retry-btn");
      if (retryBtn) {
        retryBtn.addEventListener("click", () => {
          openMegapackModal(sceneIds, resolvedMode);
        });
      }
    };

    // Attempt token creation
    let token = null;
    if (resolvedMode !== "history" && sceneIds && sceneIds.length > 0) {
      try {
        token = await createToken(sceneIds);
        window._emporniumToken = token;
      } catch (err) {
        window._emporniumToken = "";
      }
    } else {
      window._emporniumToken = "";
    }

    try {
      // 1. Fetch review.html directly with robust base URL resolution
      let origin = window.location.origin;
      if (!origin || origin === "null" || origin.startsWith("about:")) {
        origin = window.location.protocol && window.location.host ? `${window.location.protocol}//${window.location.host}` : "http://localhost:9999";
      }
      const htmlUrl = `${origin.replace(/\/+$/, "")}/plugin/${PLUGIN_ID}/assets/review.html`;
      const response = await fetch(htmlUrl);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} loading ${htmlUrl}`);
      }
      const htmlText = await response.text();

      if (!document.getElementById(MODAL_ID)) return;

      const parser = new DOMParser();
      const doc = parser.parseFromString(htmlText, "text/html");

      // Inject style if not already injected
      const styleTag = doc.querySelector("style");
      if (styleTag) {
        let styleEl = document.getElementById(INJECTED_STYLE_ID);
        if (!styleEl) {
          styleEl = document.createElement("style");
          styleEl.id = INJECTED_STYLE_ID;
          document.head.appendChild(styleEl);
        }
        styleEl.textContent = styleTag.textContent;
      }

      // Inject body contents
      body.innerHTML = doc.body.innerHTML;

      // Bind close button inside review header if present
      const btnHeaderClose = body.querySelector("#btn-header-close");
      if (btnHeaderClose) {
        btnHeaderClose.addEventListener("click", closeMegapackModal);
      }

      // 2. Initialize review logic
      if (typeof window.initEmporniumReview === "function") {
        window.initEmporniumReview(window._emporniumSceneIds, token, resolvedMode);
      } else {
        const scriptUrl = `${origin.replace(/\/+$/, "")}/plugin/${PLUGIN_ID}/assets/review.js`;
        const jsResponse = await fetch(scriptUrl);
        if (!jsResponse.ok) {
          throw new Error(`HTTP ${jsResponse.status} loading ${scriptUrl}`);
        }
        const jsText = await jsResponse.text();
        const scriptEl = document.createElement("script");
        scriptEl.textContent = jsText;
        document.body.appendChild(scriptEl);

        if (typeof window.initEmporniumReview === "function") {
          window.initEmporniumReview(window._emporniumSceneIds, token, resolvedMode);
        }
      }
    } catch (err) {
      renderBlockedFallback(err.message || String(err));
    }
  }

  function closeMegapackModal() {
    const existing = document.getElementById(MODAL_ID);
    if (existing) {
      existing.remove();
    }
    if (activeEscHandler) {
      window.removeEventListener("keydown", activeEscHandler);
      activeEscHandler = null;
    }
  }

  async function openHistoryModal() {
    await openMegapackModal([], "history");
  }

  function injectMegapackButton() {
    // Look for Stash bulk action bar or top nav menu
    const targetContainer = document.querySelector(
      ".btn-toolbar, .selection-actions, .filter-container, nav.navbar"
    );

    if (!targetContainer) return;

    if (!document.getElementById(BUTTON_ID)) {
      const btn = document.createElement("button");
      btn.id = BUTTON_ID;
      btn.className = "btn btn-secondary empornium-trigger-btn";
      btn.innerHTML = `<span class="mr-1">📦</span> Empornium Uploader`;
      btn.title = "Build Empornium Megapack Builder from selected scenes";

      btn.addEventListener("click", async () => {
        const selectedIds = getSelectedSceneIds();
        if (selectedIds.length === 0) {
          alert("Please select at least one scene to build a megapack.");
          return;
        }
        const inferredMode = selectedIds.length === 1 ? "single" : "megapack";
        await openMegapackModal(selectedIds, inferredMode);
      });

      targetContainer.appendChild(btn);
    }

    if (!document.getElementById(HISTORY_BUTTON_ID)) {
      const histBtn = document.createElement("button");
      histBtn.id = HISTORY_BUTTON_ID;
      histBtn.className = "btn btn-secondary empornium-trigger-btn";
      histBtn.innerHTML = `<span class="mr-1">📚</span> Empornium History`;
      histBtn.title = "View Empornium past build history";

      histBtn.addEventListener("click", async () => {
        await openHistoryModal();
      });

      targetContainer.appendChild(histBtn);
    }
  }

  // Observer to inject button as UI updates
  const observer = new MutationObserver(() => {
    injectMegapackButton();
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });

  // Initial check
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectMegapackButton);
  } else {
    injectMegapackButton();
  }

  // Listen for iframe / direct close messages
  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "EMPORNIUM_CLOSE_MODAL") {
      closeMegapackModal();
    }
  });

  window.openHistoryModal = openHistoryModal;
  window.openMegapackModal = openMegapackModal;
  window.closeMegapackModal = closeMegapackModal;
})();
