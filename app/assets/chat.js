// ======================= WANG LAB CHATBOT + UI LOGIC =======================
// Styles: Glassmorphism (Target Design)
// Logic: Toggles + API Chat (Consolidated)

let appInitialized = false;

function initApp() {
  // 1. Elements
  const sendBtn = document.getElementById("sendBtn");
  const chatInput = document.getElementById("chatInput");
  const chatMessages = document.getElementById("chatMessages");
  const sidebarToggle = document.querySelector(".toggle-control");
  const submitWrapper = document.getElementById("submit-wrapper");
  const chatToggle = document.getElementById("chat-toggle");
  const chatPanel = document.querySelector(".chatbot-panel");

  // Ensure key elements exist before init
  if (!sendBtn || !chatInput || !chatMessages) {
    return setTimeout(initApp, 250);
  }

  // Prevent multiple initializations
  if (appInitialized) return;
  appInitialized = true;

  // ---------------------------------------------------------------------------
  // TOGGLE LOGIC (SIDEBAR & CHAT)
  // ---------------------------------------------------------------------------

  // Sidebar
  // Sidebar
  if (sidebarToggle && submitWrapper) {
    sidebarToggle.addEventListener('click', (e) => {
      e.preventDefault();
      submitWrapper.classList.toggle('collapsed');

      // Toggle Expanded Map Height
      const leftColumn = document.getElementById('left-column-temp');
      if (leftColumn) {
        leftColumn.classList.toggle('expanded-map');
      }

      const isCollapsed = submitWrapper.classList.contains('collapsed');
      sidebarToggle.innerHTML = isCollapsed ? '❯' : '❮';
    });
  }

  // Chat Panel
  if (chatToggle && chatPanel) {
    chatToggle.addEventListener('click', (e) => {
      e.preventDefault();

      // Toggle layout container to resize width
      const chatContainer = document.getElementById('right-column-temp');
      if (chatContainer) chatContainer.classList.toggle('collapsed');

      chatPanel.classList.toggle('collapsed');
      const isCollapsed = chatPanel.classList.contains('collapsed');
      chatToggle.innerHTML = isCollapsed ? '💬' : '✕';
    });
  }

  // ---------------------------------------------------------------------------
  // BODY-LEVEL TOOLTIP (escapes overflow:hidden parents)
  // ---------------------------------------------------------------------------
  let _tip = null;
  document.addEventListener('mouseover', e => {
    const el = e.target.closest('.rag-pathway-name');
    if (!el || !el.dataset.tooltip) return;
    _tip = document.createElement('div');
    _tip.className = 'pathway-tooltip';
    _tip.textContent = el.dataset.tooltip;
    document.body.appendChild(_tip);
    const r = el.getBoundingClientRect();
    const tipW = _tip.offsetWidth;
    let left = r.left;
    // keep within viewport
    if (left + tipW > window.innerWidth - 8) left = window.innerWidth - tipW - 8;
    _tip.style.left = left + 'px';
    _tip.style.top  = (r.top - _tip.offsetHeight - 6) + 'px';
  });
  document.addEventListener('mouseout', e => {
    if (e.target.closest('.rag-pathway-name') && _tip) {
      _tip.remove(); _tip = null;
    }
  });

  // ---------------------------------------------------------------------------
  // HELPER: Get Session/Token from URL
  // ---------------------------------------------------------------------------
  function getSessionID() {
    // URL format: /app/{token}/...
    const parts = window.location.pathname.split('/');
    // parts -> ["", "app", "{token}", ...]
    if (parts.length > 2 && parts[2]) return parts[2];
    console.error("Could not find token in URL Path!", window.location.pathname);
    return "default";
  }

  // ---------------------------------------------------------------------------
  // HELPER: ROI Paths (Placeholder / Mirror)
  // ---------------------------------------------------------------------------
  function getROIPaths() {
    const mirrorEl = document.querySelector('[id="roi-data-mirror"]');
    if (mirrorEl && mirrorEl.dataset.dashStore) {
      try {
        const parsed = JSON.parse(mirrorEl.dataset.dashStore);
        if (parsed.paths && parsed.paths.length) return parsed.paths;
      } catch { }
    }
    return [];
  }

  // ---------------------------------------------------------------------------
  // HELPER: Add Message to UI
  // ---------------------------------------------------------------------------
  function cleanMarkdownText(text) {
    if (!text) return "";
    return String(text)
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/__([^_]+)__/g, "$1")
      .replace(/\*([^*\n]+)\*/g, "$1")
      .replace(/_([^_\n]+)_/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/^\s*[-*+]\s+/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function addMessage(text, sender = "user", imagePaths = []) {
    const msg = document.createElement("div");
    msg.classList.add("chat-message", sender);
    msg.textContent = cleanMarkdownText(text);
    msg.style.opacity = 0;
    chatMessages.appendChild(msg);

    // Smooth fade-in
    requestAnimationFrame(() => {
      msg.style.transition = "opacity 0.3s ease";
      msg.style.opacity = 1;
      chatMessages.scrollTop = chatMessages.scrollHeight;
    });

    // Append ROI thumbnails if any
    if (imagePaths && imagePaths.length > 0) {
      const thumbs = document.createElement("div");
      thumbs.className = "roi-thumbs";

      imagePaths.forEach((path) => {
        const token = getSessionID();
        const img = document.createElement("img");
        // Cache-bust so the browser always fetches the latest crop file
        const freshSrc = `/app/${token}/preview?path=${encodeURIComponent(path)}&t=${Date.now()}`;
        img.src = freshSrc;
        img.onclick = () => window.open(freshSrc, "_blank");
        thumbs.appendChild(img);
      });

      msg.appendChild(thumbs);
    }
  }

  // ---------------------------------------------------------------------------
  // RAG UI BUILDERS
  // ---------------------------------------------------------------------------

  function buildRagTraceCard(trace, label) {
    const card = document.createElement("div");
    card.className = "rag-trace-card";

    const header = document.createElement("div");
    header.className = "rag-trace-header";
    const title = document.createElement("span");
    title.className = "rag-trace-title";
    title.textContent = "AGENT TRACE";
    const badge = document.createElement("span");
    badge.className = "rag-badge";
    badge.textContent = "MOCK";
    header.appendChild(title);
    header.appendChild(badge);
    card.appendChild(header);

    (trace || []).forEach(step => {
      const row = document.createElement("div");
      row.className = "rag-trace-row";
      const check = document.createElement("span");
      check.className = "rag-trace-check";
      check.textContent = "✓";
      const text = document.createElement("span");
      text.className = "rag-trace-text";
      text.textContent = step.step;
      row.appendChild(check);
      row.appendChild(text);
      if (step.detail) {
        const detail = document.createElement("span");
        detail.className = "rag-trace-detail";
        detail.textContent = step.detail;
        row.appendChild(detail);
      }
      card.appendChild(row);
    });

    return card;
  }

  function buildPathwayPanel(pathways, label) {
    if (!pathways || pathways.length === 0) return null;

    const panel = document.createElement("div");
    panel.className = "rag-pathway-panel";

    // Header
    const header = document.createElement("div");
    header.className = "rag-deg-header";
    const left = document.createElement("div");
    left.className = "rag-deg-header-left";
    const labelEl = document.createElement("span");
    labelEl.className = "rag-deg-label";
    labelEl.textContent = `ENRICHED PATHWAYS · ${label || "selection"}`;
    const badge = document.createElement("span");
    badge.className = "rag-badge";
    badge.textContent = "MOCK";
    left.appendChild(labelEl);
    left.appendChild(badge);
    const scoreLabel = document.createElement("span");
    scoreLabel.className = "rag-deg-fc-label";
    scoreLabel.textContent = "-log₁₀p";
    header.appendChild(left);
    header.appendChild(scoreLabel);
    panel.appendChild(header);

    const divider = document.createElement("div");
    divider.className = "rag-deg-divider";
    panel.appendChild(divider);

    const maxScore = Math.max(...pathways.map(p => p.neg_log10p), 1);
    pathways.forEach(p => {
      const row = document.createElement("div");
      row.className = "rag-pathway-row";

      // Source tag (GO / Reactome / KEGG)
      if (p.source) {
        const src = document.createElement("span");
        src.className = "rag-pathway-source";
        src.textContent = p.source.replace(/^(GO|KEGG):[^\s]+/, m => m.split(':')[0]);
        row.appendChild(src);
      }

      const name = document.createElement("span");
      name.className = "rag-pathway-name";
      name.textContent = p.name;
      name.dataset.tooltip = p.name;

      const barWrap = document.createElement("div");
      barWrap.className = "rag-deg-bar-wrap";
      const bar = document.createElement("div");
      bar.className = "rag-pathway-bar";
      bar.style.width = `${Math.round((p.neg_log10p / maxScore) * 100)}%`;
      barWrap.appendChild(bar);

      const val = document.createElement("span");
      val.className = "rag-deg-val";
      val.textContent = p.neg_log10p.toFixed(1);

      row.appendChild(name);
      row.appendChild(barWrap);
      row.appendChild(val);
      panel.appendChild(row);
    });

    return panel;
  }

  function buildDegPanel(degs, label, citations) {
    if (!degs || degs.length === 0) return null;

    const panel = document.createElement("div");
    panel.className = "rag-deg-panel";

    // Header
    const header = document.createElement("div");
    header.className = "rag-deg-header";
    const left = document.createElement("div");
    left.className = "rag-deg-header-left";
    const labelEl = document.createElement("span");
    labelEl.className = "rag-deg-label";
    labelEl.textContent = `TOP DEGs · ${label || "selection"}`;
    const badge = document.createElement("span");
    badge.className = "rag-badge";
    badge.textContent = "MOCK";
    left.appendChild(labelEl);
    left.appendChild(badge);
    const fcLabel = document.createElement("span");
    fcLabel.className = "rag-deg-fc-label";
    fcLabel.textContent = "log₂FC";
    header.appendChild(left);
    header.appendChild(fcLabel);
    panel.appendChild(header);

    // Divider
    const divider = document.createElement("div");
    divider.className = "rag-deg-divider";
    panel.appendChild(divider);

    // Bars
    const maxFc = Math.max(...degs.map(d => d.log2fc), 1);
    degs.forEach(d => {
      const row = document.createElement("div");
      row.className = "rag-deg-row";
      const gene = document.createElement("span");
      gene.className = "rag-deg-gene";
      gene.textContent = d.gene;
      const barWrap = document.createElement("div");
      barWrap.className = "rag-deg-bar-wrap";
      const bar = document.createElement("div");
      bar.className = "rag-deg-bar";
      bar.style.width = `${Math.round((d.log2fc / maxFc) * 100)}%`;
      barWrap.appendChild(bar);
      const val = document.createElement("span");
      val.className = "rag-deg-val";
      val.textContent = d.log2fc.toFixed(1);
      row.appendChild(gene);
      row.appendChild(barWrap);
      row.appendChild(val);
      panel.appendChild(row);
    });

    // Citation chips
    if (citations && citations.length > 0) {
      const chips = document.createElement("div");
      chips.className = "rag-citation-row";
      citations.forEach(c => {
        const chip = document.createElement("span");
        chip.className = "rag-citation-chip";
        chip.textContent = `[${c.id}] ${c.journal} · PMID ${c.pmid}`;
        chips.appendChild(chip);
      });
      panel.appendChild(chips);
    }

    return panel;
  }

  // ---------------------------------------------------------------------------
  // CORE: AI Response Handler
  // ---------------------------------------------------------------------------
  async function aiRespond(userText, wasLokiBtnEnabled = false) {
    const roiPaths = getROIPaths();
    const token = getSessionID();

    // 1. Thinking Indicator
    const thinking = document.createElement("div");
    thinking.classList.add("chat-message", "ai");
    thinking.textContent = "…";
    chatMessages.appendChild(thinking);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    let dotCount = 0;
    const anim = setInterval(() => {
      dotCount = (dotCount + 1) % 4;
      thinking.textContent = ".".repeat(dotCount || 1);
    }, 350);

    // HELPER: Detect Active Viewer Layer
    function getActiveLayerName() {
      // New VivViewer control. It renders a small panel with a select whose
      // options are "Image Layer 1", "Image Layer 2", etc. Return the zero-based
      // numeric layer index so the backend can crop the same layer the user sees.
      const selects = document.querySelectorAll("select");
      for (const select of selects) {
        const optionLabels = Array.from(select.options || []).map(o => o.textContent.trim());
        if (optionLabels.some(label => label.startsWith("Image Layer"))) {
          const activeLayer = Number(select.value);
          if (!Number.isNaN(activeLayer)) {
            return activeLayer;
          }
        }
      }

      // Find all checked inputs in the layer control
      const checkedInputs = document.querySelectorAll('.leaflet-control-layers-selector:checked');
      let activeName = "Original";

      checkedInputs.forEach(input => {
        // Leaflet structure: <label> <input> <span> Name </span> </label>
        // We need to find the text of the span sibling
        const span = input.nextElementSibling;
        if (span) {
          const name = span.textContent.trim();
          // Prioritize specific overlays
          if (name.includes("cell type")) activeName = "Cell Type";
          else if (name.includes("cell selection")) activeName = "Cell Selection";
          else if (name.includes("cell detection")) activeName = "Cell Detection";
          else if (name.includes("_min") || name.includes("_max")) activeName = "Gene Expression"; // Simple heuristic
          else if (name !== "base layer" && activeName === "Original") activeName = name; // Fallback
        }
      });
      return activeName;
    }

    try {
      // 2. Send Request
      const modelSelect = document.getElementById("chatModelSelect");
      const selectedModel = modelSelect && modelSelect.value ? modelSelect.value : "ollama:qwen3-vl:30b";

      const payload = {
        model: selectedModel,
        prompt: userText,
        images: roiPaths,
        session_id: token,
        active_layer: getActiveLayerName() // Send active layer to backend
      };

      const res = await fetch(`/app/${token}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error(res.statusText || "Failed to submit message");

      const data = await res.json();
      if (data.status === "error") throw new Error(data.message);

      // RAG: show all data panels immediately (before thinking dots)
      // Order: 1. AGENT TRACE  2. PATHWAY PANEL  3. DEG PANEL  4. LLM response
      let ragMetadata = data.rag_metadata || null;
      if (ragMetadata) {
        const traceCard = buildRagTraceCard(ragMetadata.trace, ragMetadata.label);
        chatMessages.insertBefore(traceCard, thinking);

        const pathwayPanel = buildPathwayPanel(ragMetadata.pathways, ragMetadata.label);
        if (pathwayPanel) chatMessages.insertBefore(pathwayPanel, thinking);

        const degPanel = buildDegPanel(ragMetadata.degs, ragMetadata.label, ragMetadata.citations);
        if (degPanel) chatMessages.insertBefore(degPanel, thinking);

        chatMessages.scrollTop = chatMessages.scrollHeight;
      }

      // --- NEW: Retroactively add images to User Message if returned by Backend ---
      // The backend now returns 'images' and 'roi_image' which may have been generated server-side.
      let returnedImages = [];
      if (data.roi_image) {
        returnedImages.push(data.roi_image);
      }
      // REMOVED Fallback to data.images to strictly follow "only show preview if area select"
      // else if (data.images && data.images.length > 0) {
      //   returnedImages = data.images;
      // }

      // [DISABLED] Retroactive user-side image append to keep user message clean
      /*
      if (returnedImages.length > 0) {
        // Find the last user message (it was just added)
        const userMessages = document.querySelectorAll('.chat-message.user');
        const lastUserMsg = userMessages[userMessages.length - 1];

        if (lastUserMsg) {
          // Check if thumbs already exist (avoid duplicate)
          if (!lastUserMsg.querySelector('.chat-thumbnails')) {
            const thumbs = document.createElement("div");
            thumbs.classList.add("chat-thumbnails");
            thumbs.style.display = "flex";
            thumbs.style.gap = "5px";
            thumbs.style.marginTop = "5px";

            returnedImages.forEach(path => {
              const img = document.createElement("img");
              img.src = `/app/${token}/preview?path=${encodeURIComponent(path)}`;
              img.style.width = "60px";
              img.style.height = "60px";
              img.style.borderRadius = "4px";
              img.style.objectFit = "cover";
              img.style.cursor = "pointer";
              img.style.border = "1px solid #ccc";

              // Click to expand
              img.onclick = () => window.open(img.src, '_blank');
              thumbs.appendChild(img);
            });
            lastUserMsg.appendChild(thumbs);
          }
        }
      }
      */
      // ---------------------------------------------------------------------------

      // 3. Poll for Completion (Streaming)
      let aiMsgElement = null;
      let lastContentLength = 0;

      // Use the state passed from handleSend (captured BEFORE disabling)
      const lokiBtn = document.getElementById("start-loki-analysis-btn");

      const pollInterval = setInterval(async () => {
        try {
          const pollRes = await fetch(`/app/${token}/chat/poll`);
          const pollData = await pollRes.json();
          // console.log("Poll status:", pollData.status, "Length:", pollData.response ? pollData.response.length : 0);

          if (pollData.status === "streaming" || pollData.status === "done") {

            // 1. Initialize Message Bubble if needed
            if (!aiMsgElement) {
              clearInterval(anim); // Stop thinking dots
              thinking.remove();   // Remove thinking bubble

              // Create actual AI message bubble
              // We use a modified version of addMessage logic here inline for control, 
              // or we can modify addMessage to return the element.
              // Let's use addMessage and assume it puts it at the end.
              addMessage("", "ai", pollData.images);
              const allMsgs = chatMessages.querySelectorAll(".chat-message.ai");
              aiMsgElement = allMsgs[allMsgs.length - 1]; // Get the one we just added
            }

            // Keep Loki button disabled during streaming
            if (lokiBtn && pollData.status === "streaming") {
              lokiBtn.disabled = true;
            }

            // 2. Update Content
            if (pollData.response && pollData.response.length > lastContentLength) {
              // Only update if content changed/grew
              aiMsgElement.textContent = cleanMarkdownText(pollData.response);
              lastContentLength = pollData.response.length;
              chatMessages.scrollTop = chatMessages.scrollHeight;
            }

            // 3. Handle Completion
            if (pollData.status === "done") {
              clearInterval(pollInterval);
              // Ensure final content is set
              aiMsgElement.textContent = cleanMarkdownText(pollData.response);

              // Re-enable Loki button ONLY if it was enabled before the chat
              if (lokiBtn && wasLokiBtnEnabled) {
                lokiBtn.disabled = false;
              }

              // Always replace thumbs on done so we show the final correct crop,
              // not whatever stale path was present during the streaming phase.
              if (pollData.images && pollData.images.length > 0) {
                const existing = aiMsgElement.querySelector(".roi-thumbs");
                if (existing) existing.remove();
                const thumbs = document.createElement("div");
                thumbs.className = "roi-thumbs";
                pollData.images.forEach((path) => {
                  const img = document.createElement("img");
                  const freshSrc = `/app/${token}/preview?path=${encodeURIComponent(path)}&t=${Date.now()}`;
                  img.src = freshSrc;
                  img.onclick = () => window.open(freshSrc, "_blank");
                  thumbs.appendChild(img);
                });
                aiMsgElement.appendChild(thumbs);
              }

              ragMetadata = null;
            }

          } else if (pollData.status === "error") {
            throw new Error(pollData.message);
          }
        } catch (pollErr) {
          console.error("Polling Error:", pollErr);
          // Only stop if it's a hard error or too many retries (not implemented here)
          // For now, we don't stop looking for the stream unless it's a catastrophic failure
          if (pollErr.message.includes("backend unavailable")) {
            // Maybe wait?
          } else {
            // If we haven't started streaming yet, show error
            if (!aiMsgElement) {
              clearInterval(anim);
              clearInterval(pollInterval);
              thinking.remove();
              addMessage(`(error) ${pollErr.message}`, "ai");
            }
          }
        }
      }, 100); // Super fast polling (100ms) for smooth streaming

    } catch (err) {
      console.error("Chat Error:", err);
      clearInterval(anim);
      thinking.remove();
      addMessage(`(error) ${err.message}`, "ai");
    }
  }

  // ---------------------------------------------------------------------------
  // EVENT HANDLERS
  // ---------------------------------------------------------------------------
  async function handleSend() {
    const text = chatInput.value.trim();
    if (!text) return;

    // Capture Loki button state BEFORE disabling
    const lokiBtn = document.getElementById("start-loki-analysis-btn");
    const wasEnabled = lokiBtn ? !lokiBtn.disabled : false;

    // Disable Loki Analysis button immediately when sending message
    if (lokiBtn) {
      lokiBtn.disabled = true;
    }

    addMessage(text, "user");
    chatInput.value = "";
    await aiRespond(text, wasEnabled);
  }

  sendBtn.addEventListener("click", handleSend);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSend();
    }
  });

  // Clear Session
  const clearSessionBtn = document.getElementById("clearSessionBtn");
  if (clearSessionBtn) {
    clearSessionBtn.addEventListener("click", async () => {
      if (!confirm("Clear chat history? This cannot be undone.")) return;

      const token = getSessionID();
      try {
        const res = await fetch(`/app/${token}/chat/clear`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: token })
        });
        const data = await res.json();
        if (data.status === "success") {
          chatMessages.innerHTML = "";
          addMessage("✅ Session cleared.", "ai");
        } else {
          addMessage("❌ Failed: " + data.message, "ai");
        }
      } catch (err) {
        addMessage("❌ Error clearing session.", "ai");
      }
    });
  }
  // ---------------------------------------------------------------------------
  // GLOBAL POLLER (To catch external triggers like Loki Analysis Button)
  // ---------------------------------------------------------------------------
  let lastKnownResponse = "";

  function startGlobalPolling() {
    const token = getSessionID();

    setInterval(async () => {
      // Don't poll if we are actively waiting for a response in aiRespond logic?
      // Actually, separate logic is fine, but we need to deduplicate.

      try {
        const res = await fetch(`/app/${token}/chat/poll`);
        const data = await res.json();

        if (data.status === "done") {
          // Check visibility flag (default true)
          if (data.visible === false) {
            // Optimization: Update lastKnownResponse so we don't re-poll/re-notify this hidden msg
            if (data.response) lastKnownResponse = data.response;
            return;
          }

          // Simple deduplication: Check if text is same as last known or last message in DOM
          const currentText = data.response;

          if (currentText && currentText !== lastKnownResponse) {
            // Check if it's already the last message in the DOM to be safe
            const lastMsgEl = chatMessages.lastElementChild;
            if (lastMsgEl && lastMsgEl.textContent.includes(cleanMarkdownText(currentText))) {
              lastKnownResponse = currentText;
              return;
            }

            // If getting here, it's a new message
            addMessage(currentText, "ai");
            lastKnownResponse = currentText;
          }
        }
      } catch (e) {
        // Silent fail on poll errors to not spam console
      }
    }, 3000);
  }

  // Start polling
  // startGlobalPolling();
}

// ---------------------------------------------------------------------------
// HERO SECTION: Parallax \u0026 Smooth Scroll
// ---------------------------------------------------------------------------
function initHero() {
  const scrollBtn = document.getElementById('scroll-to-app-btn');
  const heroSection = document.getElementById('hero-section');

  // Smooth scroll to main app
  if (scrollBtn) {
    scrollBtn.addEventListener('click', function () {
      const mainApp = document.getElementById('main-app');
      if (mainApp) {
        mainApp.scrollIntoView({
          behavior: 'smooth',
          block: 'start'
        });
      }
    });
  }

  // Parallax effect on scroll
  if (heroSection) {
    let ticking = false;

    window.addEventListener('scroll', function () {
      if (!ticking) {
        window.requestAnimationFrame(function () {
          const scrolled = window.pageYOffset;
          const heroHeight = heroSection.offsetHeight;

          // Only apply parallax when hero is visible
          if (scrolled < heroHeight) {
            // Move hero slower than scroll (parallax effect)
            heroSection.style.transform = `translateY(${scrolled * 0.5}px)`;

            // Fade out as user scrolls
            const opacity = 1 - (scrolled / heroHeight);
            heroSection.style.opacity = Math.max(0, opacity);
          }

          ticking = false;
        });

        ticking = true;
      }
    });

    // ---------------------------------------------------------------------------
    // CANVAS PARTICLE EFFECT (Antigravity Pattern + Data Packets)
    // ---------------------------------------------------------------------------
    const canvas = document.getElementById('hero-canvas');
    if (canvas) {
      const ctx = canvas.getContext('2d');
      let width, height;

      let particles = [];
      let packets = [];

      // Mouse tracking
      let mouseX = -9999;
      let mouseY = -9999;

      heroSection.addEventListener('mousemove', (e) => {
        const rect = heroSection.getBoundingClientRect();
        mouseX = e.clientX - rect.left;
        mouseY = e.clientY - rect.top;
      });

      heroSection.addEventListener('mouseleave', () => {
        mouseX = -9999;
        mouseY = -9999;
      });

      const resize = () => {
        width = canvas.width = heroSection.offsetWidth;
        height = canvas.height = heroSection.offsetHeight;
        initParticles();
      };

      class Particle {
        constructor() {
          this.x = Math.random() * width;
          this.y = Math.random() * height;
          this.size = Math.random() * 2 + 1;
        }

        draw() {
          ctx.fillStyle = 'rgba(0, 113, 227, 0.6)'; // Blue particles
          ctx.beginPath();
          ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
          ctx.closePath();
          ctx.fill();
        }
      }

      class DataPacket {
        constructor() {
          if (particles.length === 0) return;
          this.currIdx = Math.floor(Math.random() * particles.length);
          this.targetIdx = -1;
          this.progress = 0;
          this.speed = 0.04;
          this.life = 0;
          // [CHANGE] Increased base hops
          this.maxLife = 10;
        }

        update() {
          if (particles.length === 0) return;

          if (this.targetIdx === -1) {
            // Find a neighbor
            let neighbors = [];
            let p1 = particles[this.currIdx];
            if (!p1) {
              this.currIdx = Math.floor(Math.random() * particles.length);
              return;
            }

            // Helper to get connection reach (must match animate loop logic)
            const getReach = (p) => {
              const dx = p.x - mouseX;
              const dy = p.y - mouseY;
              const dist = Math.sqrt(dx * dx + dy * dy);
              let r = 90;
              if (dist < 300) r += (300 - dist) * 0.6;
              return r;
            };

            for (let i = 0; i < particles.length; i++) {
              if (i === this.currIdx) continue;
              let p2 = particles[i];
              let dx = p1.x - p2.x;
              let dy = p1.y - p2.y;
              let dist = Math.sqrt(dx * dx + dy * dy);

              // STRICT VISUAL CHECK:
              // The draw loop only draws if j > i and dist < reach(i)
              // So line exists if:
              // (curr < i && dist < reach(curr)) OR (i < curr && dist < reach(i))
              let visible = false;
              if (this.currIdx < i) {
                if (dist < getReach(p1)) visible = true;
              } else {
                if (dist < getReach(p2)) visible = true;
              }

              if (visible) {
                neighbors.push(i);
              }
            }
            if (neighbors.length > 0) {
              this.targetIdx = neighbors[Math.floor(Math.random() * neighbors.length)];
            } else {
              this.currIdx = Math.floor(Math.random() * particles.length);
            }
          } else {
            // Move
            this.progress += this.speed;
            if (this.progress >= 1) {
              this.currIdx = this.targetIdx;
              this.targetIdx = -1;
              this.progress = 0;
              this.life++;

              // [CHANGE] RECHARGE LOGIC
              let p = particles[this.currIdx];
              if (!p) {
                this.currIdx = Math.floor(Math.random() * particles.length);
                return;
              }
              let dx = p.x - mouseX;
              let dy = p.y - mouseY;
              if (Math.sqrt(dx * dx + dy * dy) < 300) {
                this.maxLife += 5;
                if (this.maxLife > 40) this.maxLife = 40;
              }
            }
          }
        }

        draw() {
          if (this.targetIdx !== -1) {
            let p1 = particles[this.currIdx];
            let p2 = particles[this.targetIdx];
            if (!p1 || !p2) {
              this.targetIdx = -1;
              return;
            }
            let x = p1.x + (p2.x - p1.x) * this.progress;
            let y = p1.y + (p2.y - p1.y) * this.progress;

            ctx.fillStyle = '#ff9500'; // Orange spark
            ctx.shadowBlur = 10;
            ctx.shadowColor = '#ff9500';
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.closePath();
            ctx.fill();
            ctx.shadowBlur = 0;
          }
        }
      }

      function initParticles() {
        particles = [];
        let numberOfParticles = (width * height) / 8000;
        for (let i = 0; i < numberOfParticles; i++) {
          particles.push(new Particle());
        }
      }

      // Trigger immediately (small delay to ensure init)
      setTimeout(() => {
        if (particles.length > 0 && packets.length === 0) {
          packets.push(new DataPacket());
        }
      }, 1000);

      setInterval(() => {
        if (particles.length > 0 && packets.length === 0) {
          packets.push(new DataPacket());
        }
      }, 10000);

      function animate() {
        ctx.clearRect(0, 0, width, height);

        // Update Packets
        packets = packets.filter(p => p.life < p.maxLife);
        packets.forEach(p => { p.update(); p.draw(); });

        for (let i = 0; i < particles.length; i++) {
          particles[i].draw();

          const dxMouse = particles[i].x - mouseX;
          const dyMouse = particles[i].y - mouseY;
          const distMouse = Math.sqrt(dxMouse * dxMouse + dyMouse * dyMouse);

          let connectDistance = 90;
          if (distMouse < 300) {
            connectDistance += (300 - distMouse) * 0.6;
          }

          for (let j = i; j < particles.length; j++) {
            const dx = particles[i].x - particles[j].x;
            const dy = particles[i].y - particles[j].y;
            const distance = Math.sqrt(dx * dx + dy * dy);

            if (distance < connectDistance) {
              ctx.beginPath();
              // Opacity based on distance
              let opacity = (1 - distance / connectDistance) * 0.5;
              if (distMouse < 300) opacity *= 1.5;

              ctx.strokeStyle = `rgba(0, 113, 227, ${opacity})`;
              ctx.lineWidth = 1;
              ctx.moveTo(particles[i].x, particles[i].y);
              ctx.lineTo(particles[j].x, particles[j].y);
              ctx.stroke();
              ctx.closePath();
            }
          }

          if (distMouse < 150) {
            ctx.beginPath();
            ctx.strokeStyle = `rgba(0, 113, 227, ${(1 - distMouse / 150) * 0.3})`;
            ctx.lineWidth = 0.5;
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(mouseX, mouseY);
            ctx.stroke();
          }
        }
        requestAnimationFrame(animate);
      }

      window.addEventListener('resize', resize);
      resize();
      animate();
    }
  }
  // ---------------------------------------------------------------------------
  // LEAFLET RESIZE FIX (Blank Map Issue)
  // ---------------------------------------------------------------------------
  function fixLeafletMap() {
    // Trigger a window resize event to force Leaflet to recalculate container size
    setTimeout(() => {
      window.dispatchEvent(new Event('resize'));
    }, 500);

    setTimeout(() => {
      window.dispatchEvent(new Event('resize'));
    }, 1500);
  }

  // 1. Initial Trigger
  fixLeafletMap();

  // 2. Trigger on Re-visualize click
  const revisualizeBtn = document.getElementById("visual-input");
  if (revisualizeBtn) {
    revisualizeBtn.addEventListener("click", () => {
      setTimeout(fixLeafletMap, 2000);
      setTimeout(fixLeafletMap, 4000);
    });
  }

  // 3. Observer
  const mutationObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      if (mutation.addedNodes) {
        mutation.addedNodes.forEach((node) => {
          if (node.id === "map-output" || (node.querySelector && node.querySelector("#map-output"))) {
            fixLeafletMap();
          }
        });
      }
    });
  });

  const mainApp = document.getElementById('main-app');
  if (mainApp) {
    mutationObserver.observe(mainApp, { childList: true, subtree: true });
  }
}

// Start
setTimeout(initApp, 500);
// setTimeout(initHero, 600); // Hero section disabled
