// ======================== First-run Spatial Omics Tutorial ========================

(function () {
  const STORAGE_KEY = "spatialOmicsTutorialPromptAnswered";
  const TUTORIAL_IMAGE_S3_PATH = "s3://alextrywebsite/tutorial/loki_tutorial_hskin_melanoma_downsampled.ome.tif";
  const STEP_DELAY_MS = 90;

  let currentStep = 0;
  let overlay = null;
  let highlight = null;
  let bubble = null;
  let resizeHandler = null;
  let requiredClickCleanup = null;
  let promptAnsweredFallback = false;
  let tutorialImagePrepared = false;

  const steps = [
    {
      title: "Upload Image",
      copy: "Start here by dropping your H&E image into the upload box, or click the box to browse for a file. Wait until upload and processing finish.",
      before: () => setUploadPanelCollapsed(false),
      target: () => document.querySelector(".s3-upload-wrapper"),
      placement: "right"
    },
    {
      title: "Re-visualize",
      copy: "Tutorial mode loads a small sample image for you. Wait for preparation to finish, then click Re-visualize Image to open it in the viewer.",
      before: () => {
        setUploadPanelCollapsed(false);
        prepareTutorialImage();
      },
      target: () => document.getElementById("visual-input"),
      requiresClick: true,
      clickInstruction: "Click Re-visualize Image to unlock the next step.",
      clickedInstruction: "Re-visualize clicked. You can continue once the viewer starts loading.",
      placement: "right"
    },
    {
      title: "Select An Area",
      copy: "Use the rectangle or polygon drawing tool in the viewer, then mark the tissue area you want the copilot to inspect.",
      before: () => setUploadPanelCollapsed(true),
      target: () => document.querySelector('button[title="Draw rectangle ROI"]') || getViewerTarget(),
      placement: "right"
    },
    {
      title: "Ask The Chatbot",
      copy: "Type your question in the chatbot. If you selected an area, that region is sent with your question.",
      before: () => setUploadPanelCollapsed(true),
      target: () => document.querySelector(".chat-input") || document.getElementById("chatInput"),
      placement: "left"
    },
    {
      title: "View Results",
      copy: "After analysis results are available, click Re-visualize Image again. The viewer will refresh and show the result layer when it is available.",
      before: () => setUploadPanelCollapsed(false),
      target: () => document.getElementById("visual-input"),
      placement: "right"
    },
    {
      title: "Layer And Opacity",
      copy: "Use Active Layer to choose which layer is on top. Use Overlay Opacity to blend the result with the original image.",
      before: () => setUploadPanelCollapsed(true),
      target: () => findViewerControl("active") || findViewerControl("opacity") || getViewerTarget(),
      placement: "left"
    },
    {
      title: "Ask About The Right Layer",
      copy: "Before selecting an area for the chatbot, choose the layer you want as the Active Layer. The chatbot crop follows that active layer.",
      before: () => setUploadPanelCollapsed(true),
      target: () => findViewerControl("active") || document.querySelector('button[title="Draw rectangle ROI"]') || getViewerTarget(),
      placement: "left"
    },
    {
      title: "Choose View Mode",
      copy: "Use View Mode to switch between Single Layer and Side by Side, depending on whether you want blended overlays or comparison panes.",
      before: () => setUploadPanelCollapsed(true),
      target: () => findViewerControl("view") || getViewerTarget(),
      placement: "left"
    },
    {
      title: "End Session",
      copy: "When you are completely done with your own analysis, END SESSION clears the uploaded image, chat history, selections, and temporary results.",
      before: () => setUploadPanelCollapsed(false),
      target: () => document.getElementById("clear-cache"),
      placement: "right"
    }
  ];

  function findViewerControl(kind) {
    const selects = Array.from(document.querySelectorAll("select"));
    const viewMode = selects.find(select => {
      const labels = Array.from(select.options || []).map(option => option.textContent.trim());
      return labels.includes("Single Layer") && labels.includes("Side by Side");
    });
    const activeLayer = selects.find(select => {
      const labels = Array.from(select.options || []).map(option => option.textContent.trim());
      return labels.some(label => label.startsWith("Image Layer"));
    });

    if (kind === "view") return viewMode;
    if (kind === "active") return activeLayer;
    if (kind === "opacity") return document.querySelector('input[type="range"][min="0"][max="1"]');
    return viewMode || activeLayer;
  }

  function getViewerTarget() {
    const mapOutput = document.getElementById("map-output");
    if (hasUsefulRect(mapOutput)) return mapOutput;

    const viewer = document.getElementById("input-image");
    if (hasUsefulRect(viewer)) return viewer;

    const leftColumn = document.getElementById("left-column-temp");
    if (hasUsefulRect(leftColumn)) return leftColumn;

    return null;
  }

  function hasUsefulRect(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    return rect.width >= 120 && rect.height >= 120;
  }

  function setUploadPanelCollapsed(collapsed) {
    const submitWrapper = document.getElementById("submit-wrapper");
    const sidebarToggle = document.querySelector(".toggle-control");
    const leftColumn = document.getElementById("left-column-temp");
    if (!submitWrapper) return;

    submitWrapper.classList.toggle("collapsed", collapsed);
    if (leftColumn) leftColumn.classList.toggle("expanded-map", collapsed);
    if (sidebarToggle) sidebarToggle.innerHTML = collapsed ? "❯" : "❮";
  }

  function prepareTutorialImage() {
    const input = document.getElementById("upload-data-image-result-dash");
    if (!input) return;

    setNativeInputValue(input, TUTORIAL_IMAGE_S3_PATH);
    tutorialImagePrepared = true;
    updateTutorialUploadStatus();
  }

  function updateTutorialUploadStatus() {
    const wrapper = document.querySelector(".s3-upload-wrapper");
    const fileInfo = wrapper?.querySelector(".upload-file-info");
    const fileName = wrapper?.querySelector(".file-name");
    const fileSize = wrapper?.querySelector(".file-size");
    const progressContainer = wrapper?.querySelector(".upload-progress-container");
    const progressBar = wrapper?.querySelector(".upload-progress");
    const progressText = wrapper?.querySelector(".upload-progress-text");
    const statusDiv = wrapper?.querySelector(".upload-status");

    wrapper?.classList.add("has-file", "processing-phase");
    if (fileInfo) fileInfo.style.display = "block";
    if (fileName) fileName.textContent = "Tutorial sample image";
    if (fileSize) fileSize.textContent = " 7.4 MB";
    if (progressContainer) progressContainer.style.display = "block";
    if (progressBar) progressBar.style.width = "100%";
    if (progressText) progressText.textContent = "Ready";
    if (statusDiv) {
      statusDiv.textContent = "Tutorial image selected. Backend preparation is starting...";
      statusDiv.className = "upload-status success";
    }
  }

  function resetTutorialUploadStatus() {
    if (!tutorialImagePrepared) return;

    setUploadPanelCollapsed(false);

    const wrapper = document.querySelector(".s3-upload-wrapper");
    const fileInfo = wrapper?.querySelector(".upload-file-info");
    const fileName = wrapper?.querySelector(".file-name");
    const fileSize = wrapper?.querySelector(".file-size");
    const progressContainer = wrapper?.querySelector(".upload-progress-container");
    const progressBar = wrapper?.querySelector(".upload-progress");
    const progressText = wrapper?.querySelector(".upload-progress-text");
    const statusDiv = wrapper?.querySelector(".upload-status");
    const cancelBtn = wrapper?.querySelector(".upload-cancel-btn");
    const fileInput = wrapper?.querySelector(".upload-file-input");
    const uploadResult = wrapper?.querySelector(".upload-result") || document.getElementById("upload-data-image-result-dash");

    wrapper?.classList.remove("has-file", "uploading", "processing-phase");
    if (fileInfo) fileInfo.style.display = "none";
    if (fileName) fileName.textContent = "";
    if (fileSize) fileSize.textContent = "";
    if (progressContainer) progressContainer.style.display = "none";
    if (progressBar) {
      progressBar.style.width = "0%";
      progressBar.style.backgroundColor = "";
    }
    if (progressText) progressText.textContent = "0%";
    if (statusDiv) {
      statusDiv.textContent = "";
      statusDiv.className = "upload-status";
    }
    if (cancelBtn) cancelBtn.style.display = "none";
    if (fileInput) fileInput.value = "";
    if (uploadResult && uploadResult.value === TUTORIAL_IMAGE_S3_PATH) {
      setNativeInputValue(uploadResult, "");
    }

    tutorialImagePrepared = false;
  }

  function setNativeInputValue(element, value) {
    const valueSetter = Object.getOwnPropertyDescriptor(element, "value")?.set;
    const prototype = Object.getPrototypeOf(element);
    const prototypeValueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;

    if (prototypeValueSetter && valueSetter !== prototypeValueSetter) {
      prototypeValueSetter.call(element, value);
    } else if (valueSetter) {
      valueSetter.call(element, value);
    } else {
      element.value = value;
    }

    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function initTutorial() {
    const openBtn = document.getElementById("tutorial-open-btn");
    if (openBtn && !openBtn.dataset.tutorialBound) {
      openBtn.dataset.tutorialBound = "true";
      openBtn.addEventListener("click", () => startTutorial(true));
    }

    if (!getPromptAnswered()) {
      setTimeout(showPrompt, 900);
    }
  }

  function showPrompt() {
    if (document.querySelector(".tutorial-modal-backdrop") || overlay) return;

    const backdrop = document.createElement("div");
    backdrop.className = "tutorial-modal-backdrop";
    backdrop.innerHTML = `
      <div class="tutorial-prompt" role="dialog" aria-modal="true" aria-labelledby="tutorial-prompt-title">
        <h3 id="tutorial-prompt-title">Want a quick tutorial?</h3>
        <p>A short step-by-step walkthrough will point to image upload, spatial expression upload, viewer, layers, opacity, and chatbot controls.</p>
        <div class="tutorial-prompt-actions">
          <button type="button" class="tutorial-btn tutorial-btn-secondary" data-tutorial-answer="no">No, skip</button>
          <button type="button" class="tutorial-btn tutorial-btn-primary" data-tutorial-answer="yes">Yes, show me</button>
        </div>
      </div>
    `;

    backdrop.addEventListener("click", (event) => {
      const answer = event.target && event.target.dataset && event.target.dataset.tutorialAnswer;
      if (!answer) return;

      setPromptAnswered(answer);
      backdrop.remove();
      if (answer === "yes") startTutorial(true);
    });

    document.body.appendChild(backdrop);
  }

  function startTutorial(scrollToApp) {
    cleanup();
    steps.forEach(step => {
      step.beforeDone = false;
    });

    if (scrollToApp) {
      const mainApp = document.getElementById("main-app");
      if (mainApp) mainApp.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    currentStep = 0;
    overlay = document.createElement("div");
    overlay.className = "tutorial-backdrop";

    highlight = document.createElement("div");
    highlight.className = "tutorial-highlight";

    bubble = document.createElement("div");
    bubble.className = "tutorial-bubble";

    document.body.appendChild(overlay);
    document.body.appendChild(highlight);
    document.body.appendChild(bubble);

    resizeHandler = () => renderStep();
    window.addEventListener("resize", resizeHandler);
    window.addEventListener("scroll", resizeHandler, true);

    setTimeout(renderStep, STEP_DELAY_MS);
  }

  function renderStep() {
    if (!overlay || !highlight || !bubble) return;

    const step = steps[currentStep];
    if (step.before && !step.beforeDone) {
      step.before();
      step.beforeDone = true;
      setTimeout(renderStep, 420);
      return;
    }

    const target = step.target();
    const rect = getTargetRect(target);
    const placement = getPlacement(step.placement, rect);
    setupRequiredClick(step, target);
    const clickIsRequired = Boolean(step.requiresClick && !step.clicked);

    highlight.style.top = `${rect.top}px`;
    highlight.style.left = `${rect.left}px`;
    highlight.style.width = `${rect.width}px`;
    highlight.style.height = `${rect.height}px`;

    bubble.dataset.placement = placement;
    bubble.innerHTML = `
      <div class="tutorial-step-count">Step ${currentStep + 1} of ${steps.length}</div>
      <div class="tutorial-title">${step.title}</div>
      <div class="tutorial-copy">${step.copy}</div>
      ${target ? "" : '<div class="tutorial-note">This control appears after the viewer is loaded.</div>'}
      ${step.requiresClick ? `<div class="tutorial-note">${step.clicked ? step.clickedInstruction : step.clickInstruction}</div>` : ""}
      <div class="tutorial-actions">
        <button type="button" class="tutorial-btn tutorial-btn-secondary tutorial-skip" data-tutorial-action="skip">Skip</button>
        ${currentStep > 0 ? '<button type="button" class="tutorial-btn tutorial-btn-secondary" data-tutorial-action="back">Back</button>' : ""}
        <button type="button" class="tutorial-btn tutorial-btn-primary" data-tutorial-action="next" ${clickIsRequired ? "disabled" : ""}>${currentStep === steps.length - 1 ? "Finish" : "Okay"}</button>
      </div>
    `;

    const bubbleRect = bubble.getBoundingClientRect();
    const pos = getBubblePosition(rect, bubbleRect, placement);
    bubble.style.top = `${pos.top}px`;
    bubble.style.left = `${pos.left}px`;

    bubble.onclick = (event) => {
      const action = event.target && event.target.dataset && event.target.dataset.tutorialAction;
      if (!action) return;
      if (action === "skip") return cleanup();
      if (action === "back") {
        clearRequiredClickListener();
        step.beforeDone = false;
        step.clicked = false;
        currentStep = Math.max(0, currentStep - 1);
        steps[currentStep].beforeDone = false;
        steps[currentStep].clicked = false;
        return renderStep();
      }
      if (action === "next" && step.requiresClick && !step.clicked) return;
      if (currentStep >= steps.length - 1) return cleanup();
      clearRequiredClickListener();
      step.beforeDone = false;
      currentStep += 1;
      steps[currentStep].beforeDone = false;
      setTimeout(renderStep, STEP_DELAY_MS);
    };
  }

  function setupRequiredClick(step, target) {
    clearRequiredClickListener();
    if (!step.requiresClick || step.clicked || !target) return;

    const onClick = () => {
      step.clicked = true;
      clearRequiredClickListener();
      setTimeout(renderStep, 120);
    };

    target.addEventListener("click", onClick, { once: true });
    requiredClickCleanup = () => target.removeEventListener("click", onClick);
  }

  function clearRequiredClickListener() {
    if (requiredClickCleanup) {
      requiredClickCleanup();
      requiredClickCleanup = null;
    }
  }

  function getTargetRect(target) {
    const viewportPadding = 14;
    if (!target) {
      return {
        top: Math.max(120, window.innerHeight * 0.35),
        left: Math.max(20, window.innerWidth * 0.5 - 180),
        width: Math.min(360, window.innerWidth - 40),
        height: 160
      };
    }

    target.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    const rect = target.getBoundingClientRect();
    if (rect.width < 120 || rect.height < 48) {
      const viewerTarget = target.id === "input-image" ? getViewerTarget() : null;
      if (viewerTarget && viewerTarget !== target) return getTargetRect(viewerTarget);
    }

    return {
      top: Math.max(viewportPadding, rect.top - 8),
      left: Math.max(viewportPadding, rect.left - 8),
      width: Math.min(window.innerWidth - viewportPadding * 2, rect.width + 16),
      height: Math.min(window.innerHeight - viewportPadding * 2, rect.height + 16)
    };
  }

  function getPlacement(preferred, rect) {
    if (preferred === "left" && rect.left > 370) return "left";
    if (preferred === "right" && window.innerWidth - (rect.left + rect.width) > 370) return "right";
    if (rect.top > 250) return "top";
    return "bottom";
  }

  function getBubblePosition(rect, bubbleRect, placement) {
    const margin = 18;
    const maxLeft = window.innerWidth - bubbleRect.width - 16;
    const maxTop = window.innerHeight - bubbleRect.height - 16;
    let left = rect.left;
    let top = rect.top + rect.height + margin;

    if (placement === "right") {
      left = rect.left + rect.width + margin;
      top = rect.top;
    } else if (placement === "left") {
      left = rect.left - bubbleRect.width - margin;
      top = rect.top;
    } else if (placement === "top") {
      left = rect.left;
      top = rect.top - bubbleRect.height - margin;
    }

    return {
      left: Math.max(16, Math.min(left, maxLeft)),
      top: Math.max(16, Math.min(top, maxTop))
    };
  }

  function cleanup() {
    resetTutorialUploadStatus();
    steps.forEach(step => {
      step.beforeDone = false;
      step.clicked = false;
    });
    clearRequiredClickListener();

    document.querySelector(".tutorial-modal-backdrop")?.remove();
    overlay?.remove();
    highlight?.remove();
    bubble?.remove();
    overlay = null;
    highlight = null;
    bubble = null;

    if (resizeHandler) {
      window.removeEventListener("resize", resizeHandler);
      window.removeEventListener("scroll", resizeHandler, true);
      resizeHandler = null;
    }
  }

  function getPromptAnswered() {
    try {
      return localStorage.getItem(STORAGE_KEY) || promptAnsweredFallback;
    } catch (error) {
      return promptAnsweredFallback;
    }
  }

  function setPromptAnswered(value) {
    promptAnsweredFallback = value;
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch (error) {
      // Storage is optional; the Help button still lets users restart the tour.
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTutorial);
  } else {
    initTutorial();
  }

  setTimeout(initTutorial, 800);
})();
