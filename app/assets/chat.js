let appInitialized = false;

function initApp() {
  const sendBtn = document.getElementById("sendBtn");
  const chatInput = document.getElementById("chatInput");
  const chatMessages = document.getElementById("chatMessages");
  const sidebarToggle = document.querySelector(".toggle-control");
  const submitWrapper = document.getElementById("submit-wrapper");
  const chatToggle = document.getElementById("chat-toggle");
  const chatPanel = document.querySelector(".chatbot-panel");

  if (!sendBtn || !chatInput || !chatMessages) {
    return setTimeout(initApp, 250);
  }

  if (appInitialized) return;
  appInitialized = true;

  if (sidebarToggle && submitWrapper) {
    sidebarToggle.addEventListener('click', (e) => {
      e.preventDefault();
      submitWrapper.classList.toggle('collapsed');

      const leftColumn = document.getElementById('left-column-temp');
      if (leftColumn) {
        leftColumn.classList.toggle('expanded-map');
      }

      const isCollapsed = submitWrapper.classList.contains('collapsed');
      sidebarToggle.innerHTML = isCollapsed ? '❯' : '❮';
    });
  }

  if (chatToggle && chatPanel) {
    chatToggle.addEventListener('click', (e) => {
      e.preventDefault();

      const chatContainer = document.getElementById('right-column-temp');
      if (chatContainer) chatContainer.classList.toggle('collapsed');

      chatPanel.classList.toggle('collapsed');
      const isCollapsed = chatPanel.classList.contains('collapsed');
      chatToggle.innerHTML = isCollapsed ? '💬' : '✕';
    });
  }

  // Attach tooltips to body so parent overflow does not clip them.
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
    if (left + tipW > window.innerWidth - 8) left = window.innerWidth - tipW - 8;
    _tip.style.left = left + 'px';
    _tip.style.top  = (r.top - _tip.offsetHeight - 6) + 'px';
  });
  document.addEventListener('mouseout', e => {
    if (e.target.closest('.rag-pathway-name') && _tip) {
      _tip.remove(); _tip = null;
    }
  });

  function getSessionID() {
    // Support current and legacy workspace URLs.
    const parts = window.location.pathname.split('/');
    if (parts.length > 2 && (parts[1] === "workspaces" || parts[1] === "app") && parts[2]) {
      return parts[2];
    }
    console.error("Could not find workspace in URL path.", window.location.pathname);
    return "default";
  }

  function getAppBasePath() {
    return `/workspaces/${getSessionID()}`;
  }

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

    requestAnimationFrame(() => {
      msg.style.transition = "opacity 0.3s ease";
      msg.style.opacity = 1;
      chatMessages.scrollTop = chatMessages.scrollHeight;
    });

    if (imagePaths && imagePaths.length > 0) {
      const thumbs = document.createElement("div");
      thumbs.className = "roi-thumbs";

      imagePaths.forEach((path) => {
        const token = getSessionID();
        const img = document.createElement("img");
        const freshSrc = `${getAppBasePath()}/preview?path=${encodeURIComponent(path)}&t=${Date.now()}`;
        img.src = freshSrc;
        img.onclick = () => window.open(freshSrc, "_blank");
        thumbs.appendChild(img);
      });

      msg.appendChild(thumbs);
    }

    return msg;
  }

  // Maps each TraceStep.status (rag.contracts) to a real icon + color instead
  // of the old hardcoded green check, so a failed/empty/skipped step actually
  // looks different from a successful one.
  const RAG_TRACE_STATUS_ICON = {
    ok: { glyph: "✓", cls: "rag-trace-status-ok" },
    error: { glyph: "✗", cls: "rag-trace-status-error" },
    empty: { glyph: "○", cls: "rag-trace-status-empty" },
    skipped: { glyph: "⤳", cls: "rag-trace-status-skipped" },
  };

  function buildRagTraceCard(trace, label) {
    const card = document.createElement("div");
    card.className = "rag-trace-card";

    const header = document.createElement("div");
    header.className = "rag-trace-header";
    const title = document.createElement("span");
    title.className = "rag-trace-title";
    title.textContent = "AGENT TRACE";
    const hint = document.createElement("span");
    hint.className = "rag-trace-hint";
    hint.textContent = "click a step to inspect";
    header.appendChild(title);
    header.appendChild(hint);
    card.appendChild(header);

    (trace || []).forEach(step => {
      const row = document.createElement("div");
      row.className = "rag-trace-row";

      const iconInfo = RAG_TRACE_STATUS_ICON[step.status] || RAG_TRACE_STATUS_ICON.ok;
      const check = document.createElement("span");
      check.className = `rag-trace-check ${iconInfo.cls}`;
      check.textContent = iconInfo.glyph;
      const text = document.createElement("span");
      text.className = "rag-trace-text";
      text.textContent = step.step;
      row.appendChild(check);
      row.appendChild(text);
      if (step.tool) {
        const toolBadge = document.createElement("span");
        toolBadge.className = "rag-trace-tool-badge";
        toolBadge.textContent = step.tool;
        row.appendChild(toolBadge);
      }
      if (step.detail) {
        const detail = document.createElement("span");
        detail.className = "rag-trace-detail";
        detail.textContent = step.detail;
        row.appendChild(detail);
      }
      card.appendChild(row);

      // Collapsed by default so the card looks exactly as compact as before;
      // click reveals what the step actually took in and returned. This is
      // the "reasoning can be inspected" surface — retrieve/route/tool-call/
      // synthesize all set input_summary/output_summary in rag.contracts.
      if (step.input_summary || step.output_summary) {
        const expand = document.createElement("div");
        expand.className = "rag-trace-expand";
        const addExpandLine = (labelText, valueText) => {
          const line = document.createElement("div");
          line.className = "rag-trace-expand-line";
          const label = document.createElement("span");
          label.className = "rag-trace-expand-label";
          label.textContent = labelText;
          line.appendChild(label);
          // input_summary can contain the user's own chat message verbatim
          // (the routing step logs question[:120]) — textContent only, never
          // innerHTML, so a message can't inject markup into this panel.
          line.appendChild(document.createTextNode(valueText));
          expand.appendChild(line);
        };
        if (step.input_summary) addExpandLine("in", step.input_summary);
        if (step.output_summary) addExpandLine("out", step.output_summary);
        card.appendChild(expand);

        row.classList.add("rag-trace-row-expandable");
        row.setAttribute("role", "button");
        row.setAttribute("tabindex", "0");
        const toggle = () => {
          const isOpen = expand.classList.toggle("rag-trace-expand-open");
          row.classList.toggle("rag-trace-row-open", isOpen);
        };
        row.addEventListener("click", toggle);
        row.addEventListener("keydown", e => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        });
      }
    });

    return card;
  }

  function buildPathwayPanel(pathways, label) {
    if (!pathways || pathways.length === 0) return null;

    const panel = document.createElement("div");
    panel.className = "rag-pathway-panel";

    const header = document.createElement("div");
    header.className = "rag-deg-header";
    const left = document.createElement("div");
    left.className = "rag-deg-header-left";
    const labelEl = document.createElement("span");
    labelEl.className = "rag-deg-label";
    labelEl.textContent = `ENRICHED PATHWAYS · ${label || "selection"}`;
    left.appendChild(labelEl);
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

    const header = document.createElement("div");
    header.className = "rag-deg-header";
    const left = document.createElement("div");
    left.className = "rag-deg-header-left";
    const labelEl = document.createElement("span");
    labelEl.className = "rag-deg-label";
    labelEl.textContent = `TOP DEGs · ${label || "selection"}`;
    left.appendChild(labelEl);
    const fcLabel = document.createElement("span");
    fcLabel.className = "rag-deg-fc-label";
    fcLabel.textContent = "log₂FC";
    header.appendChild(left);
    header.appendChild(fcLabel);
    panel.appendChild(header);

    const divider = document.createElement("div");
    divider.className = "rag-deg-divider";
    panel.appendChild(divider);

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

    if (citations && citations.length > 0) {
      const chips = document.createElement("div");
      chips.className = "rag-citation-row";
      citations.forEach(c => {
        const label = `[${c.id}] ${c.journal} · PMID ${c.pmid}`;
        let chip;
        if (c.url) {
          chip = document.createElement("a");
          chip.href = c.url;
          chip.target = "_blank";
          chip.rel = "noopener noreferrer";
          if (c.title) chip.title = c.title;
        } else {
          chip = document.createElement("span");
        }
        chip.className = "rag-citation-chip";
        chip.textContent = label;
        chips.appendChild(chip);
      });
      panel.appendChild(chips);
    }

    return panel;
  }

  async function aiRespond(userText) {
    const roiPaths = getROIPaths();
    const token = getSessionID();

    const thinking = document.createElement("div");
    thinking.classList.add("chat-message", "ai", "chat-thinking");
    thinking.setAttribute("role", "status");
    thinking.setAttribute("aria-live", "polite");

    const thinkingDots = document.createElement("span");
    thinkingDots.className = "chat-thinking-dots";
    for (let i = 0; i < 3; i += 1) {
      const dot = document.createElement("span");
      thinkingDots.appendChild(dot);
    }

    const thinkingLabel = document.createElement("span");
    thinkingLabel.className = "chat-thinking-label";
    thinkingLabel.textContent = "Analyzing context";

    thinking.appendChild(thinkingDots);
    thinking.appendChild(thinkingLabel);
    chatMessages.appendChild(thinking);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    function getActiveLayerName() {
      // Return the zero-based layer index used by the crop backend.
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

      const checkedInputs = document.querySelectorAll('.leaflet-control-layers-selector:checked');
      let activeName = "Original";

      checkedInputs.forEach(input => {
        const span = input.nextElementSibling;
        if (span) {
          const name = span.textContent.trim();
          if (name.includes("cell type")) activeName = "Cell Type";
          else if (name.includes("cell selection")) activeName = "Cell Selection";
          else if (name.includes("cell detection")) activeName = "Cell Detection";
          else if (name.includes("_min") || name.includes("_max")) activeName = "Gene Expression";
          else if (name !== "base layer" && activeName === "Original") activeName = name;
        }
      });
      return activeName;
    }

    try {
      const modelSelect = document.getElementById("chatModelSelect");
      const selectedModel = modelSelect && modelSelect.value ? modelSelect.value : "ollama:qwen2.5:0.5b";

      const payload = {
        model: selectedModel,
        prompt: userText,
        images: roiPaths,
        session_id: token,
        active_layer: getActiveLayerName()
      };

      const res = await fetch(`${getAppBasePath()}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error(res.statusText || "Failed to submit message");

      const data = await res.json();
      if (data.status === "error") throw new Error(data.message);

      // Render evidence panels before the streamed response.
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

      let returnedImages = [];
      if (data.roi_image) {
        returnedImages.push(data.roi_image);
      }
      let aiMsgElement = null;
      let lastContentLength = 0;

      await new Promise((resolve) => {
        const pollStartedAt = Date.now();
        const maxPollMs = 120000;
        const pollEveryMs = 500;
        const pollInterval = setInterval(async () => {
          try {
            if (Date.now() - pollStartedAt > maxPollMs) {
              throw new Error("Chat is still running after 120 seconds. Retry with a smaller ROI or restart Ollama.");
            }

            const pollRes = await fetch(`${getAppBasePath()}/chat/poll`);
            if (!pollRes.ok) throw new Error(pollRes.statusText || "Failed to poll chat response");
            const pollData = await pollRes.json();
            if (pollData.status === "streaming" || pollData.status === "done") {

              if (!aiMsgElement) {
                thinking.remove();
                aiMsgElement = addMessage("", "ai", pollData.images);
              }

              if (pollData.response && pollData.response.length > lastContentLength) {
                aiMsgElement.textContent = cleanMarkdownText(pollData.response);
                lastContentLength = pollData.response.length;
                chatMessages.scrollTop = chatMessages.scrollHeight;
              }

              if (pollData.status === "done") {
                clearInterval(pollInterval);
                aiMsgElement.textContent = cleanMarkdownText(pollData.response);

                // Replace any thumbnail created from an intermediate response.
                if (pollData.images && pollData.images.length > 0) {
                  const existing = aiMsgElement.querySelector(".roi-thumbs");
                  if (existing) existing.remove();
                  const thumbs = document.createElement("div");
                  thumbs.className = "roi-thumbs";
                  pollData.images.forEach((path) => {
                    const img = document.createElement("img");
                    const freshSrc = `${getAppBasePath()}/preview?path=${encodeURIComponent(path)}&t=${Date.now()}`;
                    img.src = freshSrc;
                    img.onclick = () => window.open(freshSrc, "_blank");
                    thumbs.appendChild(img);
                  });
                  aiMsgElement.appendChild(thumbs);
                }

                ragMetadata = null;
                resolve();
              }

            } else if (pollData.status === "error") {
              throw new Error(pollData.message);
            }
          } catch (pollErr) {
            console.error("Polling Error:", pollErr);
            clearInterval(pollInterval);
            thinking.remove();
            if (!aiMsgElement) {
              addMessage(`(error) ${pollErr.message}`, "ai");
            } else {
              aiMsgElement.textContent = `(error) ${pollErr.message}`;
            }
            resolve();
          }
        }, pollEveryMs);
      });

    } catch (err) {
      console.error("Chat Error:", err);
      thinking.remove();
      addMessage(`(error) ${err.message}`, "ai");
    }
  }

  let sendInFlight = false;

  function setChatInputValue(value) {
    const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    valueSetter.call(chatInput, value);
    chatInput.dispatchEvent(new Event("input", { bubbles: true }));
    chatInput.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function handleSend() {
    if (sendInFlight) return;

    const text = chatInput.value.trim();
    if (!text) return;

    sendInFlight = true;
    sendBtn.disabled = true;
    setChatInputValue("");

    try {
      addMessage(text, "user");
      await aiRespond(text);
    } finally {
      sendInFlight = false;
      sendBtn.disabled = false;
      chatInput.focus();
    }
  }

  sendBtn.addEventListener("click", handleSend);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSend();
    }
  });

  const clearSessionBtn = document.getElementById("clearSessionBtn");
  if (clearSessionBtn) {
    clearSessionBtn.addEventListener("click", async () => {
      if (!confirm("Clear chat history? This cannot be undone.")) return;

      const token = getSessionID();
      try {
        const res = await fetch(`${getAppBasePath()}/chat/clear`, {
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
  let lastKnownResponse = "";

  function startGlobalPolling() {
    const token = getSessionID();

    setInterval(async () => {
      try {
        const res = await fetch(`${getAppBasePath()}/chat/poll`);
        const data = await res.json();

        if (data.status === "done") {
          if (data.visible === false) {
            if (data.response) lastKnownResponse = data.response;
            return;
          }

          const currentText = data.response;

          if (currentText && currentText !== lastKnownResponse) {
            const lastMsgEl = chatMessages.lastElementChild;
            if (lastMsgEl && lastMsgEl.textContent.includes(cleanMarkdownText(currentText))) {
              lastKnownResponse = currentText;
              return;
            }

            addMessage(currentText, "ai");
            lastKnownResponse = currentText;
          }
        }
      } catch (e) {
        // Polling is best-effort and should not add console noise.
      }
    }, 3000);
  }

}

function initHero() {
  const scrollBtn = document.getElementById('scroll-to-app-btn');
  const heroSection = document.getElementById('hero-section');

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

  if (heroSection) {
    let ticking = false;

    window.addEventListener('scroll', function () {
      if (!ticking) {
        window.requestAnimationFrame(function () {
          const scrolled = window.pageYOffset;
          const heroHeight = heroSection.offsetHeight;

          if (scrolled < heroHeight) {
            heroSection.style.transform = `translateY(${scrolled * 0.5}px)`;

            const opacity = 1 - (scrolled / heroHeight);
            heroSection.style.opacity = Math.max(0, opacity);
          }

          ticking = false;
        });

        ticking = true;
      }
    });

    const canvas = document.getElementById('hero-canvas');
    if (canvas) {
      const ctx = canvas.getContext('2d');
      let width, height;

      let particles = [];
      let packets = [];

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
          ctx.fillStyle = 'rgba(0, 113, 227, 0.6)';
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
          this.maxLife = 10;
        }

        update() {
          if (particles.length === 0) return;

          if (this.targetIdx === -1) {
            let neighbors = [];
            let p1 = particles[this.currIdx];
            if (!p1) {
              this.currIdx = Math.floor(Math.random() * particles.length);
              return;
            }

            // Match the reach calculation used when drawing connections.
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

              // Only route packets over connections rendered by the draw loop.
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
            this.progress += this.speed;
            if (this.progress >= 1) {
              this.currIdx = this.targetIdx;
              this.targetIdx = -1;
              this.progress = 0;
              this.life++;

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

            ctx.fillStyle = '#ff9500';
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
  function fixLeafletMap() {
    // Leaflet needs a resize event after its container changes.
    setTimeout(() => {
      window.dispatchEvent(new Event('resize'));
    }, 500);

    setTimeout(() => {
      window.dispatchEvent(new Event('resize'));
    }, 1500);
  }

  fixLeafletMap();

  const revisualizeBtn = document.getElementById("visual-input");
  if (revisualizeBtn) {
    revisualizeBtn.addEventListener("click", () => {
      setTimeout(fixLeafletMap, 2000);
      setTimeout(fixLeafletMap, 4000);
    });
  }

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

setTimeout(initApp, 500);
