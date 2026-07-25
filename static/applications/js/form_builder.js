(function () {
  const QUESTION_TYPES_WITH_OPTIONS = new Set(["choice", "multi_choice", "choice_grid"]);
  const TERMS_ACCEPTANCE_TYPE = "terms_acceptance";
  const DEFAULT_TERMS_QUESTION_TEXT = "¿Aceptas los términos y condiciones?";
  const ADVANCED_FIELDS = [
    "field-show_if_conditions", "field-end_form_rules", "field-confirm_value", "field-pre_hr",
    "field-pre_text", "field-slug", "field-position", "field-active",
  ];

  const rowFor = (card, name) => card.querySelector("." + name);
  let pendingSectionAfter = null;
  let pendingQuestionAfter = null;
  let sectionOrganizer = null;

  function positionBuilderRail(card) {
    const group = document.querySelector("#questions-group");
    const rail = group?.querySelector(":scope > .ce-builder-rail");
    if (!group || !rail) return;
    const target = card || group.querySelector(".ce-structure-card.ce-card-active:not([hidden])");
    if (!target || target.hidden) {
      rail.style.top = "4px";
      return;
    }
    const groupRect = group.getBoundingClientRect();
    const cardRect = target.getBoundingClientRect();
    rail.style.top = `${Math.max(4, cardRect.top - groupRect.top + 4)}px`;
  }

  function activateCard(card) {
    document.querySelectorAll("#questions-group .ce-structure-card.ce-card-active")
      .forEach((item) => item.classList.remove("ce-card-active"));
    card?.classList.add("ce-card-active");
    window.requestAnimationFrame(() => positionBuilderRail(card));
  }

  function questionContainer() {
    return (
      document.querySelector("#questions-group .inline-related:not(.empty-form)")?.parentElement
      || document.querySelector("#questions-group .add-row")?.parentElement
      || null
    );
  }

  function sectionToken(card) {
    const idInput = card.querySelector("input[id$='-id']");
    if (idInput?.value) return `id:${idInput.value}`;
    const title = card.querySelector("input[id$='-title']");
    return title?.id ? title.id.replace(/^id_/, "").replace(/-title$/, "") : "";
  }

  function updateStructure() {
    const container = questionContainer();
    if (!container) return;
    let currentSectionToken = "";
    let questionPosition = 1;
    let sectionPosition = 1;
    Array.from(container.children).forEach((card) => {
      if (card.hidden || !card.classList?.contains("ce-structure-card")) return;
      if (card.classList.contains("ce-section-card")) {
        currentSectionToken = sectionToken(card);
        const position = card.querySelector("input[id$='-position']");
        const label = card.querySelector(".ce-section-label");
        if (label) label.textContent = `Section ${sectionPosition}`;
        if (position) position.value = sectionPosition++;
        return;
      }
      const position = card.querySelector("input[id$='-position']");
      if (position) position.value = questionPosition++;
      const token = card.querySelector("input[id$='-section_token']");
      const section = card.querySelector("select[id$='-section']");
      if (token) token.value = currentSectionToken;
      if (section) {
        section.value = currentSectionToken.startsWith("id:")
          ? currentSectionToken.slice(3)
          : "";
      }
    });
    positionBuilderRail(container.querySelector(".ce-structure-card.ce-card-active:not([hidden])"));
    document.querySelector("#content-main form")?.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function wirePointerSort(handle, item, itemSelector, onEnd) {
    let active = false;
    let moved = false;
    let pointerId = null;
    let clientX = 0;
    let clientY = 0;
    let autoScrollFrame = null;
    handle.draggable = false;

    function moveItemAtPointer() {
      const candidates = Array.from(item.parentElement?.querySelectorAll(`:scope > ${itemSelector}`) || [])
        .filter((candidate) => candidate !== item && !candidate.hidden);
      if (!candidates.length) return;
      const directTarget = document.elementFromPoint(clientX, clientY)?.closest(itemSelector);
      const target = directTarget && directTarget !== item && directTarget.parentElement === item.parentElement
        ? directTarget
        : candidates.reduce((closest, candidate) => {
          const rect = candidate.getBoundingClientRect();
          const distance = Math.abs(clientY - (rect.top + rect.height / 2));
          return !closest || distance < closest.distance ? { candidate, distance } : closest;
        }, null)?.candidate;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      const before = clientY < rect.top + rect.height / 2;
      const nextSibling = before ? target : target.nextSibling;
      if (nextSibling === item || (!nextSibling && item === item.parentElement.lastElementChild)) return;
      target.parentElement.insertBefore(item, nextSibling);
      moved = true;
      if (item.classList.contains("ce-card-active")) positionBuilderRail(item);
    }

    function autoScroll() {
      if (!active) return;
      let amount = 0;
      if (clientY < 80) amount = -Math.max(8, Math.round((80 - clientY) / 3));
      else if (clientY > window.innerHeight - 80) {
        amount = Math.max(8, Math.round((clientY - (window.innerHeight - 80)) / 3));
      }
      if (amount) {
        window.scrollBy(0, amount);
        moveItemAtPointer();
      }
      autoScrollFrame = window.requestAnimationFrame(autoScroll);
    }

    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      event.preventDefault();
      active = true;
      moved = false;
      pointerId = event.pointerId;
      clientX = event.clientX;
      clientY = event.clientY;
      item.classList.add("ce-pointer-dragging");
      document.addEventListener("pointermove", move, true);
      document.addEventListener("pointerup", finish, true);
      document.addEventListener("pointercancel", finish, true);
      autoScrollFrame = window.requestAnimationFrame(autoScroll);
    });

    function move(event) {
      if (!active || (pointerId !== null && event.pointerId !== pointerId)) return;
      event.preventDefault();
      clientX = event.clientX;
      clientY = event.clientY;
      moveItemAtPointer();
    }

    function finish(event) {
      if (!active || (pointerId !== null && event.pointerId !== pointerId)) return;
      active = false;
      pointerId = null;
      if (autoScrollFrame !== null) window.cancelAnimationFrame(autoScrollFrame);
      autoScrollFrame = null;
      document.removeEventListener("pointermove", move, true);
      document.removeEventListener("pointerup", finish, true);
      document.removeEventListener("pointercancel", finish, true);
      item.classList.remove("ce-pointer-dragging");
      if (moved) onEnd?.();
    }
  }

  function optionEditor(row, config = {}) {
    if (!row) return;
    const itemName = config.itemName || "Option";
    if (row.dataset.optionsEnhanced === "1") {
      row.querySelectorAll(".ce-option-row input").forEach((input) => { input.placeholder = itemName; });
      const add = row.querySelector(".ce-add-option");
      if (add) add.textContent = `Add ${itemName.toLowerCase()}`;
      return;
    }
    const textarea = row.querySelector("textarea");
    if (!textarea) return;
    row.dataset.optionsEnhanced = "1";
    textarea.classList.add("ce-options-source");

    const editor = document.createElement("div");
    editor.className = "ce-options-editor";
    const list = document.createElement("div");
    list.className = "ce-option-list";
    editor.appendChild(list);

    function refreshMarkers() {
      Array.from(list.querySelectorAll(".ce-option-row")).forEach((option, index) => {
        const marker = option.querySelector(".ce-option-marker");
        if (marker) marker.textContent = `${index + 1}.`;
      });
    }

    function sync() {
      refreshMarkers();
      textarea.value = Array.from(list.querySelectorAll("input"))
        .map((input) => input.value.trim()).filter(Boolean).join("\n");
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function addOption(value, focus) {
      const option = document.createElement("div");
      option.className = "ce-option-row";
      const drag = document.createElement("span");
      drag.className = "ce-option-drag";
      drag.title = `Drag to reorder ${itemName.toLowerCase()}`;
      drag.setAttribute("aria-label", drag.title);
      drag.textContent = "⋮⋮";
      const marker = document.createElement("span");
      marker.className = "ce-option-marker";
      marker.setAttribute("aria-hidden", "true");
      const input = document.createElement("input");
      input.type = "text";
      input.value = value || "";
      input.placeholder = itemName;
      input.addEventListener("input", sync);
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); addOption("", true); }
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ce-option-remove";
      remove.setAttribute("aria-label", "Remove option");
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        option.remove();
        if (!list.children.length) addOption("", true);
        sync();
      });
      drag.addEventListener("dragstart", (event) => {
        event.dataTransfer?.setData("text/plain", "option");
        if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
        option.classList.add("ce-option-dragging");
      });
      drag.addEventListener("dragend", () => {
        option.classList.remove("ce-option-dragging");
        sync();
      });
      wirePointerSort(drag, option, ".ce-option-row", sync);
      option.append(drag, marker, input, remove);
      list.appendChild(option);
      refreshMarkers();
      if (focus) input.focus();
    }

    list.addEventListener("dragover", (event) => {
      const dragging = list.querySelector(".ce-option-dragging");
      if (!dragging) return;
      event.preventDefault();
      const options = Array.from(list.querySelectorAll(".ce-option-row:not(.ce-option-dragging)"));
      const next = options.find((item) => (
        event.clientY < item.getBoundingClientRect().top + item.offsetHeight / 2
      ));
      list.insertBefore(dragging, next || null);
    });

    (textarea.value.split("\n").filter((line) => line.trim()).length
      ? textarea.value.split("\n").filter((line) => line.trim()) : [""]
    ).forEach((value) => addOption(value, false));

    const add = document.createElement("button");
    add.type = "button";
    add.className = "ce-add-option";
    add.textContent = `Add ${itemName.toLowerCase()}`;
    add.addEventListener("click", () => addOption("", true));
    editor.appendChild(add);
    row.appendChild(editor);
  }

  function enhanceRichTextarea(textarea) {
    if (!textarea || textarea.dataset.richEditor === "1") return;
    const fieldName = String(textarea.name || "");
    const supportsInlineImages = (
      fieldName === "description"
      || fieldName.endsWith("-description")
      || fieldName === "thanks_approved_message"
    );
    if (
      fieldName.endsWith("-answer_options")
      || fieldName.endsWith("-grid_rows")
      || fieldName.endsWith("-show_if_conditions")
      || fieldName.endsWith("-end_form_rules")
    ) return;

    textarea.dataset.richEditor = "1";
    textarea.classList.add("ce-rich-source");
    const shell = document.createElement("div");
    shell.className = "ce-rich-editor";
    const toolbar = document.createElement("div");
    toolbar.className = "ce-rich-toolbar";
    toolbar.setAttribute("role", "toolbar");
    toolbar.setAttribute("aria-label", "Text formatting");
    const editor = document.createElement("div");
    editor.className = "ce-rich-content";
    editor.contentEditable = "true";
    editor.setAttribute("role", "textbox");
    editor.setAttribute("aria-multiline", "true");

    const marker = /^\s*<div\s+data-ce-rich-text=["']1["']\s*>([\s\S]*)<\/div>\s*$/i;
    const match = String(textarea.value || "").match(marker);
    if (match) {
      const template = document.createElement("template");
      template.innerHTML = match[1];
      template.content.querySelectorAll("script,style,iframe,object,embed,video,audio").forEach((node) => node.remove());
      template.content.querySelectorAll("*").forEach((node) => {
        Array.from(node.attributes).forEach((attr) => {
          if (attr.name.toLowerCase().startsWith("on")) node.removeAttribute(attr.name);
        });
      });
      template.content.querySelectorAll("img").forEach((image) => {
        if (
          !supportsInlineImages
          || !/^data:image\/(?:png|jpe?g|webp|gif);base64,/i.test(image.getAttribute("src") || "")
        ) {
          image.remove();
          return;
        }
        Array.from(image.attributes).forEach((attr) => {
          if (!["src", "alt", "title", "style", "data-ce-align", "data-ce-width", "data-ce-oversize"].includes(attr.name.toLowerCase())) {
            image.removeAttribute(attr.name);
          }
        });
      });
      editor.appendChild(template.content.cloneNode(true));
    } else {
      editor.textContent = textarea.value || "";
      editor.innerHTML = editor.innerHTML.replace(/\r?\n/g, "<br>");
    }

    let savedRange = null;
    let selectedImage = null;
    let resizeOverlay = null;
    function rememberSelection() {
      const selection = window.getSelection();
      if (selection?.rangeCount && editor.contains(selection.anchorNode)) {
        savedRange = selection.getRangeAt(0).cloneRange();
      }
    }
    function restoreSelection() {
      if (!savedRange) {
        editor.focus();
        return;
      }
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(savedRange);
    }
    function sync() {
      const fontSizes = { "1": "0.75em", "2": "0.875em", "3": "1em", "4": "1.25em", "5": "1.5em", "6": "2em", "7": "3em" };
      editor.querySelectorAll("font[size]").forEach((font) => {
        const span = document.createElement("span");
        span.style.fontSize = fontSizes[font.getAttribute("size")] || "1em";
        while (font.firstChild) span.appendChild(font.firstChild);
        font.replaceWith(span);
      });
      selectedImage?.classList.remove("ce-rich-image-selected");
      textarea.value = `<div data-ce-rich-text="1">${editor.innerHTML}</div>`;
      selectedImage?.classList.add("ce-rich-image-selected");
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
      rememberSelection();
    }
    function command(name, value = null) {
      restoreSelection();
      document.execCommand(name, false, value);
      sync();
      editor.focus();
    }

    const bold = document.createElement("button");
    bold.type = "button";
    bold.className = "ce-rich-bold";
    bold.innerHTML = "<strong>B</strong>";
    bold.title = "Bold";
    bold.addEventListener("mousedown", (event) => event.preventDefault());
    bold.addEventListener("click", () => command("bold"));

    const size = document.createElement("select");
    size.title = "Font size";
    [["3", "Normal"], ["2", "Small"], ["4", "Large"], ["5", "Extra large"]].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value; option.textContent = label; size.appendChild(option);
    });
    size.addEventListener("mousedown", rememberSelection);
    size.addEventListener("change", () => { command("fontSize", size.value); size.value = "3"; });

    const spacing = document.createElement("select");
    spacing.title = "Line spacing";
    [["", "Line spacing"], ["1", "Single"], ["1.5", "1.5"], ["2", "Double"]].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value; option.textContent = label; spacing.appendChild(option);
    });
    spacing.addEventListener("mousedown", rememberSelection);
    spacing.addEventListener("change", () => {
      if (!spacing.value) return;
      restoreSelection();
      document.execCommand("formatBlock", false, "div");
      const selection = window.getSelection();
      let node = selection?.anchorNode;
      if (node?.nodeType === Node.TEXT_NODE) node = node.parentElement;
      const block = node?.closest?.("div,p") || editor;
      if (editor.contains(block) || block === editor) block.style.lineHeight = spacing.value;
      spacing.value = "";
      sync();
      editor.focus();
    });

    toolbar.append(size, bold, spacing);

    if (supportsInlineImages) {
      const imageInput = document.createElement("input");
      imageInput.type = "file";
      imageInput.accept = "image/png,image/jpeg,image/webp,image/gif";
      imageInput.className = "ce-rich-image-input";

      const imageButton = document.createElement("button");
      imageButton.type = "button";
      imageButton.className = "ce-rich-image-button";
      imageButton.textContent = "Image";
      imageButton.title = "Insert an image, or drag one directly into the text";
      imageButton.addEventListener("mousedown", rememberSelection);
      imageButton.addEventListener("click", () => imageInput.click());

      const imageControls = document.createElement("span");
      imageControls.className = "ce-rich-image-controls";
      imageControls.hidden = true;

      const imageWidthLabel = document.createElement("label");
      imageWidthLabel.className = "ce-rich-image-width";
      imageWidthLabel.title = "Drag to resize the selected image";
      const imageWidthText = document.createElement("span");
      imageWidthText.textContent = "Width 50%";
      const imageWidth = document.createElement("input");
      imageWidth.type = "range";
      imageWidth.min = "10";
      imageWidth.max = "160";
      imageWidth.step = "5";
      imageWidth.value = "50";
      imageWidth.title = "Selected image width";
      imageWidthLabel.append(imageWidthText, imageWidth);

      const imageAlignment = document.createElement("select");
      imageAlignment.title = "Selected image alignment";
      [["left", "Align left"], ["center", "Align center"], ["right", "Align right"]]
        .forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          imageAlignment.appendChild(option);
        });

      const imageSize = document.createElement("select");
      imageSize.title = "Selected image size";
      [
        ["", "Image size"],
        ["30", "Small (30%)"],
        ["50", "Medium (50%)"],
        ["75", "Large (75%)"],
        ["100", "Full width (100%)"],
        ["125", "Extra large (125%)"],
        ["150", "Maximum page size (150%)"],
      ]
        .forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          imageSize.appendChild(option);
        });

      const imageHeightText = document.createElement("span");
      imageHeightText.className = "ce-rich-image-height";
      imageHeightText.textContent = "Height auto";

      const resetProportions = document.createElement("button");
      resetProportions.type = "button";
      resetProportions.textContent = "Reset proportions";
      resetProportions.title = "Restore the image's original proportions";

      const removeImage = document.createElement("button");
      removeImage.type = "button";
      removeImage.textContent = "Remove";
      removeImage.title = "Remove selected image";

      resizeOverlay = document.createElement("div");
      resizeOverlay.className = "ce-rich-image-resize";
      resizeOverlay.hidden = true;

      function updateResizeOverlay() {
        if (!selectedImage || !editor.contains(selectedImage) || !shell.isConnected) {
          resizeOverlay.hidden = true;
          return;
        }
        const imageRect = selectedImage.getBoundingClientRect();
        const shellRect = shell.getBoundingClientRect();
        resizeOverlay.hidden = false;
        resizeOverlay.style.left = `${imageRect.left - shellRect.left}px`;
        resizeOverlay.style.top = `${imageRect.top - shellRect.top}px`;
        resizeOverlay.style.width = `${imageRect.width}px`;
        resizeOverlay.style.height = `${imageRect.height}px`;
      }

      function setSelectedImageWidth(width, save = false) {
        if (!selectedImage) return;
        const boundedWidth = Math.min(160, Math.max(10, Math.round(width)));
        selectedImage.style.width = `${boundedWidth}%`;
        selectedImage.style.maxWidth = boundedWidth > 100 ? "none" : "100%";
        selectedImage.dataset.ceWidth = String(boundedWidth);
        if (boundedWidth > 100) {
          selectedImage.dataset.ceOversize = "1";
        } else {
          delete selectedImage.dataset.ceOversize;
        }
        imageWidth.value = String(boundedWidth);
        imageWidthText.textContent = `Width ${boundedWidth}%`;
        imageSize.value = ["30", "50", "75", "100", "125", "150"].includes(String(boundedWidth))
          ? String(boundedWidth)
          : "";
        requestAnimationFrame(updateResizeOverlay);
        if (save) sync();
      }

      function setSelectedImageHeight(height, save = false) {
        if (!selectedImage) return;
        if (height === "auto") {
          selectedImage.style.height = "auto";
          imageHeightText.textContent = "Height auto";
        } else {
          const boundedHeight = Math.min(2400, Math.max(40, Math.round(height)));
          selectedImage.style.height = `${boundedHeight}px`;
          imageHeightText.textContent = `Height ${boundedHeight}px`;
        }
        requestAnimationFrame(updateResizeOverlay);
        if (save) sync();
      }

      ["nw", "ne", "sw", "se"].forEach((corner) => {
        const handle = document.createElement("button");
        handle.type = "button";
        handle.className = `ce-rich-image-handle ce-rich-image-handle--${corner}`;
        handle.dataset.corner = corner;
        handle.title = "Drag horizontally for width and vertically for height; hold Shift to preserve proportions";
        handle.setAttribute("aria-label", `Resize image from ${corner.toUpperCase()} corner`);
        handle.addEventListener("pointerdown", (event) => {
          if (!selectedImage) return;
          event.preventDefault();
          event.stopPropagation();
          const image = selectedImage;
          const startRect = image.getBoundingClientRect();
          const startX = event.clientX;
          const startY = event.clientY;
          const aspectRatio = startRect.height ? startRect.width / startRect.height : 1;
          const horizontalDirection = corner.includes("e") ? 1 : -1;
          const verticalDirection = corner.includes("s") ? 1 : -1;
          handle.setPointerCapture?.(event.pointerId);
          document.body.classList.add("ce-rich-image-resizing");

          const move = (moveEvent) => {
            if (selectedImage !== image) return;
            const horizontalDelta = horizontalDirection * (moveEvent.clientX - startX);
            const editorWidth = Math.max(1, editor.getBoundingClientRect().width);
            let nextWidth = startRect.width + horizontalDelta;
            let nextHeight = startRect.height
              + verticalDirection * (moveEvent.clientY - startY);
            if (moveEvent.shiftKey) {
              const verticalWidthDelta = (
                verticalDirection * (moveEvent.clientY - startY) * aspectRatio
              );
              const pixelDelta = Math.abs(horizontalDelta) >= Math.abs(verticalWidthDelta)
                ? horizontalDelta
                : verticalWidthDelta;
              nextWidth = startRect.width + pixelDelta;
              nextHeight = nextWidth / aspectRatio;
            }
            setSelectedImageWidth((nextWidth / editorWidth) * 100);
            setSelectedImageHeight(nextHeight);
          };
          const finish = () => {
            document.removeEventListener("pointermove", move);
            document.removeEventListener("pointerup", finish);
            document.removeEventListener("pointercancel", finish);
            document.body.classList.remove("ce-rich-image-resizing");
            if (selectedImage === image) sync();
            requestAnimationFrame(updateResizeOverlay);
          };
          document.addEventListener("pointermove", move);
          document.addEventListener("pointerup", finish);
          document.addEventListener("pointercancel", finish);
        });
        resizeOverlay.appendChild(handle);
      });

      function selectedAlignment(image) {
        if (["left", "center", "right"].includes(image.dataset.ceAlign || "")) {
          return image.dataset.ceAlign;
        }
        if (image.style.cssFloat === "right") return "right";
        if (image.style.cssFloat === "left") return "left";
        if (image.style.marginLeft === "auto" && image.style.marginRight === "0px") return "right";
        if (image.style.marginLeft === "auto" && image.style.marginRight === "auto") return "center";
        return "left";
      }

      function selectImage(image) {
        selectedImage?.classList.remove("ce-rich-image-selected");
        selectedImage = image && editor.contains(image) ? image : null;
        imageControls.hidden = !selectedImage;
        if (!selectedImage) {
          resizeOverlay.hidden = true;
          return;
        }
        selectedImage.classList.add("ce-rich-image-selected");
        const width = Math.min(160, Math.max(10, Math.round(parseFloat(selectedImage.style.width || "50"))));
        if (width > 100) {
          selectedImage.dataset.ceOversize = "1";
          selectedImage.style.maxWidth = "none";
        } else {
          delete selectedImage.dataset.ceOversize;
        }
        imageWidth.value = String(width);
        imageWidthText.textContent = `Width ${width}%`;
        imageSize.value = ["30", "50", "75", "100", "125", "150"].includes(String(width)) ? String(width) : "";
        imageAlignment.value = selectedAlignment(selectedImage);
        const savedHeight = parseFloat(selectedImage.style.height || "");
        imageHeightText.textContent = Number.isFinite(savedHeight)
          ? `Height ${Math.round(savedHeight)}px`
          : "Height auto";
        requestAnimationFrame(updateResizeOverlay);
      }

      function applyAlignment(image, alignment) {
        image.dataset.ceAlign = alignment;
        image.style.display = "block";
        image.style.cssFloat = alignment === "center" ? "none" : alignment;
        image.style.marginLeft = alignment === "right" || alignment === "center" ? "auto" : "0px";
        image.style.marginRight = alignment === "left" || alignment === "center" ? "auto" : "0px";
        image.style.marginBottom = alignment === "center" ? "1rem" : "1.25rem";
        if (alignment === "left") image.style.marginRight = "1.5rem";
        if (alignment === "right") image.style.marginLeft = "1.5rem";
      }

      function optimizeImage(file) {
        return new Promise((resolve, reject) => {
          if (!file || !/^image\/(?:png|jpeg|webp|gif)$/i.test(file.type || "")) {
            reject(new Error("Choose a PNG, JPEG, WebP, or GIF image."));
            return;
          }
          if (file.size > 12 * 1024 * 1024) {
            reject(new Error("The image must be smaller than 12 MB."));
            return;
          }
          const reader = new FileReader();
          reader.onerror = () => reject(new Error("The image could not be read."));
          reader.onload = () => {
            const source = new window.Image();
            source.onerror = () => reject(new Error("The image could not be opened."));
            source.onload = () => {
              const maxDimension = 1400;
              const scale = Math.min(1, maxDimension / Math.max(source.naturalWidth, source.naturalHeight));
              const width = Math.max(1, Math.round(source.naturalWidth * scale));
              const height = Math.max(1, Math.round(source.naturalHeight * scale));
              const canvas = document.createElement("canvas");
              canvas.width = width;
              canvas.height = height;
              canvas.getContext("2d").drawImage(source, 0, 0, width, height);
              resolve({
                src: canvas.toDataURL("image/webp", 0.84),
                portrait: height > width,
              });
            };
            source.src = String(reader.result || "");
          };
          reader.readAsDataURL(file);
        });
      }

      async function insertImage(file, insertionRange = savedRange) {
        try {
          const optimized = await optimizeImage(file);
          const defaultWidth = optimized.portrait ? 30 : 50;
          editor.focus();
          const range = insertionRange && editor.contains(insertionRange.commonAncestorContainer)
            ? insertionRange
            : document.createRange();
          if (!insertionRange || !editor.contains(insertionRange.commonAncestorContainer)) {
            range.selectNodeContents(editor);
            range.collapse(false);
          }
          const image = document.createElement("img");
          image.src = optimized.src;
          image.alt = "";
          image.style.width = `${defaultWidth}%`;
          image.dataset.ceWidth = String(defaultWidth);
          image.style.maxWidth = "100%";
          image.style.height = "auto";
          applyAlignment(image, optimized.portrait ? "right" : "center");
          range.deleteContents();
          range.insertNode(image);
          const spacer = document.createElement("br");
          image.after(spacer);
          range.setStartAfter(spacer);
          range.collapse(true);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          savedRange = range.cloneRange();
          selectImage(image);
          sync();
        } catch (error) {
          window.alert(error.message || "The image could not be inserted.");
        } finally {
          imageInput.value = "";
        }
      }

      function rangeFromPoint(event) {
        if (document.caretRangeFromPoint) return document.caretRangeFromPoint(event.clientX, event.clientY);
        if (document.caretPositionFromPoint) {
          const position = document.caretPositionFromPoint(event.clientX, event.clientY);
          if (position) {
            const range = document.createRange();
            range.setStart(position.offsetNode, position.offset);
            range.collapse(true);
            return range;
          }
        }
        return savedRange;
      }

      imageInput.addEventListener("change", () => {
        const range = savedRange?.cloneRange();
        if (imageInput.files?.[0]) insertImage(imageInput.files[0], range);
      });
      imageWidth.addEventListener("input", () => {
        setSelectedImageWidth(Number(imageWidth.value));
      });
      imageWidth.addEventListener("change", () => {
        if (!selectedImage) return;
        sync();
      });
      imageSize.addEventListener("change", () => {
        if (!selectedImage || !imageSize.value) return;
        setSelectedImageWidth(Number(imageSize.value), true);
      });
      imageAlignment.addEventListener("change", () => {
        if (!selectedImage) return;
        applyAlignment(selectedImage, imageAlignment.value);
        sync();
        requestAnimationFrame(updateResizeOverlay);
      });
      resetProportions.addEventListener("click", () => {
        if (!selectedImage) return;
        setSelectedImageHeight("auto", true);
      });
      removeImage.addEventListener("click", () => {
        if (!selectedImage) return;
        const image = selectedImage;
        selectImage(null);
        image.remove();
        sync();
      });
      editor.addEventListener("click", (event) => {
        selectImage(event.target instanceof HTMLImageElement ? event.target : null);
      });
      editor.addEventListener("dragover", (event) => {
        if (!Array.from(event.dataTransfer?.items || []).some((item) => item.kind === "file" && item.type.startsWith("image/"))) return;
        event.preventDefault();
        editor.classList.add("ce-rich-drop-active");
      });
      editor.addEventListener("dragleave", () => editor.classList.remove("ce-rich-drop-active"));
      editor.addEventListener("drop", (event) => {
        editor.classList.remove("ce-rich-drop-active");
        const file = Array.from(event.dataTransfer?.files || []).find((item) => item.type.startsWith("image/"));
        if (!file) return;
        event.preventDefault();
        const range = rangeFromPoint(event);
        insertImage(file, range?.cloneRange());
      });
      editor.addEventListener("paste", (event) => {
        const file = Array.from(event.clipboardData?.files || []).find((item) => item.type.startsWith("image/"));
        if (!file) return;
        event.preventDefault();
        const range = savedRange?.cloneRange();
        insertImage(file, range);
      });
      window.addEventListener("resize", updateResizeOverlay);
      window.addEventListener("scroll", updateResizeOverlay, true);

      imageControls.append(
        imageSize,
        imageWidthLabel,
        imageHeightText,
        imageAlignment,
        resetProportions,
        removeImage,
      );
      toolbar.append(imageButton, imageControls, imageInput);
    }

    shell.append(toolbar, editor);
    if (resizeOverlay) shell.appendChild(resizeOverlay);
    textarea.insertAdjacentElement("afterend", shell);
    editor.addEventListener("input", sync);
    editor.addEventListener("keyup", rememberSelection);
    editor.addEventListener("mouseup", rememberSelection);
    editor.addEventListener("paste", (event) => {
      if (event.defaultPrevented) return;
      event.preventDefault();
      document.execCommand("insertText", false, event.clipboardData?.getData("text/plain") || "");
    });
  }

  function enhanceRichTextareas(root) {
    const scope = root || document;
    if (scope.matches?.("textarea")) enhanceRichTextarea(scope);
    scope.querySelectorAll?.("textarea").forEach(enhanceRichTextarea);
  }

  function updateQuestionPositions() {
    updateStructure();
  }

  function setOptionsVisibility(card) {
    const type = card.querySelector("select[id$='-field_type']");
    const optionRow = rowFor(card, "field-answer_options");
    const gridRow = rowFor(card, "field-grid_rows");
    const termsRow = rowFor(card, "field-terms_content");
    if (!type || !optionRow) return;
    const hasOptions = QUESTION_TYPES_WITH_OPTIONS.has(type.value);
    const isGrid = type.value === "choice_grid";
    optionRow.classList.toggle("ce-builder-hidden", !hasOptions);
    gridRow?.classList.toggle("ce-builder-hidden", !isGrid);
    termsRow?.classList.toggle("ce-builder-hidden", type.value !== TERMS_ACCEPTANCE_TYPE);
    if (type.value === TERMS_ACCEPTANCE_TYPE) {
      const questionText = card.querySelector("input[id$='-text']");
      if (questionText && !questionText.value.trim()) {
        questionText.value = DEFAULT_TERMS_QUESTION_TEXT;
        questionText.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
    const optionLabel = optionRow.querySelector(":scope > div > label, :scope > label");
    if (optionLabel) optionLabel.textContent = isGrid ? "Columns" : "Answer options";
    if (hasOptions) optionEditor(optionRow, { itemName: isGrid ? "Column" : "Option" });
    if (isGrid) optionEditor(gridRow, { itemName: "Row" });
    card.classList.toggle("ce-multiple-options", type.value === "multi_choice");
    card.classList.toggle("ce-grid-options", isGrid);
  }

  function copyQuestion(source, target) {
    const sourceFields = source.querySelectorAll("input, textarea, select");
    sourceFields.forEach((field) => {
      const suffix = field.name?.split("-").pop();
      if (!suffix || ["id", "DELETE", "position", "slug"].includes(suffix)) return;
      const targetField = target.querySelector(`[name$="-${suffix}"]`);
      if (!targetField) return;
      if (field.type === "checkbox") targetField.checked = field.checked;
      else targetField.value = field.value;
      targetField.dispatchEvent(new Event("change", { bubbles: true }));
    });
    target.querySelectorAll(".ce-options-source").forEach((targetSource) => {
      const suffix = targetSource.name?.split("-").pop();
      const sourceValue = source.querySelector(`[name$="-${suffix}"]`)?.value;
      if (sourceValue === undefined) return;
      targetSource.value = sourceValue;
      const targetRow = targetSource.closest(`.field-${suffix}`);
      targetRow?.querySelector(".ce-options-editor")?.remove();
      if (targetRow) {
        targetRow.dataset.optionsEnhanced = "0";
        const targetIsGrid = target.querySelector("select[id$='-field_type']")?.value === "choice_grid";
        optionEditor(targetRow, {
          itemName: suffix === "grid_rows" ? "Row" : targetIsGrid ? "Column" : "Option",
        });
      }
    });
  }

  function enhanceQuestion(card) {
    if (card.dataset.simpleBuilder === "1" || !card.querySelector("select[id$='-field_type']")) return;
    card.dataset.simpleBuilder = "1";
    card.classList.add("ce-structure-card", "ce-question-card");
    const dragHandle = document.createElement("div");
    dragHandle.className = "ce-drag-handle";
    dragHandle.title = "Drag to reorder";
    dragHandle.textContent = "⠿";
    card.prepend(dragHandle);
    dragHandle.addEventListener("dragstart", (event) => {
      event.dataTransfer?.setData("text/plain", "question");
      if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
      card.classList.add("ce-dragging", "ce-structure-dragging");
    });
    dragHandle.addEventListener("dragend", () => {
      card.classList.remove("ce-dragging", "ce-structure-dragging");
      updateQuestionPositions();
    });
    wirePointerSort(dragHandle, card, ".ce-structure-card", updateStructure);
    card.addEventListener("pointerdown", () => activateCard(card));
    ADVANCED_FIELDS.forEach((name) => rowFor(card, name)?.classList.add("ce-builder-hidden"));

    const textRow = rowFor(card, "field-text");
    const typeRow = rowFor(card, "field-field_type");
    if (textRow && typeRow) {
      const header = document.createElement("div");
      header.className = "ce-question-header";
      textRow.parentNode.insertBefore(header, textRow);
      header.append(textRow, typeRow);
    }

    setOptionsVisibility(card);
    card.querySelector("select[id$='-field_type']")?.addEventListener("change", () => setOptionsVisibility(card));

    const actions = document.createElement("div");
    actions.className = "ce-question-actions";
    const advanced = document.createElement("button");
    advanced.type = "button";
    advanced.className = "ce-more-options";
    advanced.textContent = "More options";
    advanced.addEventListener("click", () => {
      const open = ADVANCED_FIELDS.some((name) => rowFor(card, name)?.classList.contains("ce-builder-hidden"));
      ADVANCED_FIELDS.forEach((name) => rowFor(card, name)?.classList.toggle("ce-builder-hidden", !open));
      advanced.textContent = open ? "Hide options" : "More options";
    });

    const duplicate = document.createElement("button");
    duplicate.type = "button";
    duplicate.className = "ce-icon-action";
    duplicate.title = "Duplicate question";
    duplicate.textContent = "Duplicate";
    duplicate.addEventListener("click", () => {
      document.querySelector("#questions-group .add-row a")?.click();
      const cards = document.querySelectorAll("#questions-group .inline-related:not(.empty-form)");
      const target = cards[cards.length - 1];
      if (target && target !== card) { enhanceQuestion(target); copyQuestion(card, target); }
    });

    const required = rowFor(card, "field-required");
    if (required) required.classList.add("ce-required-control");
    const removeQuestion = document.createElement("button");
    removeQuestion.type = "button";
    removeQuestion.className = "ce-icon-action ce-delete-question";
    removeQuestion.title = "Delete question";
    removeQuestion.textContent = "Delete";
    removeQuestion.addEventListener("click", () => {
      const deleteInput = card.querySelector("input[id$='-DELETE']");
      if (deleteInput) deleteInput.checked = true;
      card.hidden = true;
      updateQuestionPositions();
      card.closest("form")?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    actions.append(advanced, duplicate, removeQuestion);
    if (required) actions.appendChild(required);
    card.appendChild(actions);

    const errorMessages = Array.from(card.querySelectorAll(".errorlist li"))
      .map((item) => item.textContent.trim()).filter(Boolean);
    if (errorMessages.length) {
      card.classList.add("ce-card-invalid");
      const summary = document.createElement("div");
      summary.className = "ce-card-error-summary";
      summary.setAttribute("role", "alert");
      summary.textContent = `This question could not be saved: ${Array.from(new Set(errorMessages)).join(" ")}`;
      dragHandle.after(summary);
      let revealedAdvancedError = false;
      ADVANCED_FIELDS.forEach((name) => {
        const row = rowFor(card, name);
        if (row?.querySelector(".errorlist")) {
          row.classList.remove("ce-builder-hidden");
          revealedAdvancedError = true;
        }
      });
      if (revealedAdvancedError) advanced.textContent = "Hide options";
    }
  }

  function enhanceSection(card) {
    if (!card || card.dataset.sectionBuilder === "1" || card.querySelector("select[id$='-field_type']")) return;
    if (!card.querySelector("input[id$='-title']")) return;
    card.dataset.sectionBuilder = "1";
    card.classList.add("ce-structure-card", "ce-section-card");
    const label = document.createElement("div");
    label.className = "ce-section-label";
    label.textContent = "Section";
    card.prepend(label);
    card.addEventListener("pointerdown", () => activateCard(card));
    card.querySelector("input[id$='-title']")?.addEventListener("input", refreshSectionOrganizer);
    const actions = document.createElement("div");
    actions.className = "ce-question-actions ce-section-actions";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Delete section";
    remove.addEventListener("click", () => {
      const deleteInput = card.querySelector("input[id$='-DELETE']");
      if (deleteInput) deleteInput.checked = true;
      card.hidden = true;
      updateStructure();
      refreshSectionOrganizer();
    });
    actions.appendChild(remove);
    card.appendChild(actions);
  }

  function enhanceAll(root) {
    const scope = root || document;
    const cards = [];
    if (scope.matches?.(".inline-related:not(.empty-form)")) cards.push(scope);
    cards.push(...scope.querySelectorAll(".inline-related:not(.empty-form)"));
    cards.forEach((card) => {
      enhanceQuestion(card);
      enhanceSection(card);
    });
  }

  function initializeSectionLayout() {
    const container = questionContainer();
    const sectionsGroup = document.querySelector("#sections-group");
    if (!container || !sectionsGroup || sectionsGroup.dataset.interleaved === "1") return;
    sectionsGroup.dataset.interleaved = "1";
    const addRow = Array.from(container.children).find((child) => child.classList?.contains("add-row"));
    const questions = Array.from(container.querySelectorAll(":scope > .ce-question-card"));
    const sections = Array.from(sectionsGroup.querySelectorAll(".inline-related:not(.empty-form)"));
    sections.sort((a, b) => {
      const av = Number(a.querySelector("input[id$='-position']")?.value || 0);
      const bv = Number(b.querySelector("input[id$='-position']")?.value || 0);
      return av - bv;
    });
    sections.forEach((sectionCard) => {
      enhanceSection(sectionCard);
      const token = sectionToken(sectionCard);
      const id = token.startsWith("id:") ? token.slice(3) : "";
      const firstAssigned = questions.find((question) => (
        question.querySelector("input[id$='-section_token']")?.value === `id:${id}`
      ));
      container.insertBefore(sectionCard, firstAssigned || addRow || null);
    });
    sectionsGroup.classList.add("ce-section-source");
    updateStructure();
  }

  function sectionBlock(sectionCard) {
    const block = [sectionCard];
    let sibling = sectionCard.nextElementSibling;
    while (sibling && !sibling.classList?.contains("ce-section-card")) {
      if (sibling.classList?.contains("ce-question-card")) block.push(sibling);
      sibling = sibling.nextElementSibling;
    }
    return block;
  }

  function reorderSectionBlocks(tokens) {
    const container = questionContainer();
    if (!container) return;
    const addRow = Array.from(container.children).find((child) => child.classList?.contains("add-row"));
    const sectionsByToken = new Map(
      Array.from(container.querySelectorAll(":scope > .ce-section-card:not([hidden])"))
        .map((card) => [sectionToken(card), card])
    );
    tokens.forEach((token) => {
      const sectionCard = sectionsByToken.get(token);
      if (!sectionCard) return;
      const fragment = document.createDocumentFragment();
      sectionBlock(sectionCard).forEach((card) => fragment.appendChild(card));
      container.insertBefore(fragment, addRow || null);
    });
    updateStructure();
  }

  function syncSectionOrganizerOrder() {
    const rows = Array.from(sectionOrganizer?.querySelectorAll(".ce-section-order-row") || []);
    reorderSectionBlocks(rows.map((row) => row.dataset.sectionToken || ""));
    rows.forEach((row, index) => {
      const number = row.querySelector(".ce-section-order-number");
      if (number) number.textContent = String(index + 1);
      const buttons = row.querySelectorAll(".ce-section-order-actions button");
      if (buttons[0]) buttons[0].disabled = index === 0;
      if (buttons[1]) buttons[1].disabled = index === rows.length - 1;
    });
  }

  function moveSectionOrganizerRow(row, offset) {
    const sibling = offset < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!sibling) return;
    if (offset < 0) row.parentElement.insertBefore(row, sibling);
    else row.parentElement.insertBefore(sibling, row);
    syncSectionOrganizerOrder();
  }

  function refreshSectionOrganizer() {
    const body = sectionOrganizer?.querySelector("tbody");
    const container = questionContainer();
    if (!body || !container) return;
    body.replaceChildren();
    const sections = Array.from(
      container.querySelectorAll(":scope > .ce-section-card:not([hidden])")
    );
    sections.forEach((card, index) => {
      const row = document.createElement("tr");
      row.className = "ce-section-order-row";
      row.dataset.sectionToken = sectionToken(card);

      const numberCell = document.createElement("td");
      numberCell.className = "ce-section-order-number";
      numberCell.textContent = String(index + 1);

      const handleCell = document.createElement("td");
      const handle = document.createElement("button");
      handle.type = "button";
      handle.className = "ce-section-order-handle";
      handle.title = "Drag section and all of its questions";
      handle.setAttribute("aria-label", handle.title);
      handle.textContent = "⋮⋮";
      handleCell.appendChild(handle);

      const titleCell = document.createElement("td");
      const title = document.createElement("button");
      title.type = "button";
      title.className = "ce-section-order-title";
      title.textContent = card.querySelector("input[id$='-title']")?.value.trim() || "Untitled section";
      title.title = "Jump to this section";
      title.addEventListener("click", () => {
        activateCard(card);
        card.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      titleCell.appendChild(title);

      const actionsCell = document.createElement("td");
      actionsCell.className = "ce-section-order-actions";
      const up = document.createElement("button");
      up.type = "button";
      up.title = "Move section up";
      up.setAttribute("aria-label", up.title);
      up.textContent = "↑";
      up.disabled = index === 0;
      up.addEventListener("click", () => moveSectionOrganizerRow(row, -1));
      const down = document.createElement("button");
      down.type = "button";
      down.title = "Move section down";
      down.setAttribute("aria-label", down.title);
      down.textContent = "↓";
      down.disabled = index === sections.length - 1;
      down.addEventListener("click", () => moveSectionOrganizerRow(row, 1));
      actionsCell.append(up, down);

      row.append(numberCell, handleCell, titleCell, actionsCell);
      body.appendChild(row);
      wirePointerSort(handle, row, ".ce-section-order-row", syncSectionOrganizerOrder);
    });
    const empty = sectionOrganizer.querySelector(".ce-section-organizer-empty");
    if (empty) empty.hidden = sections.length > 0;
  }

  function addSectionOrganizer() {
    if (sectionOrganizer || !document.querySelector("#questions-group")) return;
    sectionOrganizer = document.createElement("aside");
    sectionOrganizer.className = "ce-section-organizer";
    sectionOrganizer.setAttribute("aria-label", "Section order");
    sectionOrganizer.innerHTML = `
      <div class="ce-section-organizer-header">
        <h2>Sections</h2>
        <p>Drag a row to move the section and all its questions together.</p>
      </div>
      <div class="ce-section-organizer-scroll">
        <table>
          <thead><tr><th>#</th><th></th><th>Section</th><th>Move</th></tr></thead>
          <tbody></tbody>
        </table>
        <p class="ce-section-organizer-empty" hidden>No sections yet.</p>
      </div>
    `;
    (document.querySelector("#content-main") || document.body).prepend(sectionOrganizer);
    refreshSectionOrganizer();
  }

  function simplifyAddButtons() {
    document.querySelectorAll(".add-row a").forEach((link) => {
      const group = link.closest(".inline-group");
      if (group?.id?.includes("question")) link.textContent = "+ Add question";
      if (group?.id?.includes("section")) link.textContent = "+ Add section";
    });
  }

  function addRail() {
    if (document.querySelector(".ce-builder-rail")) return;
    const group = document.querySelector("#questions-group");
    if (!group) return;
    const rail = document.createElement("div");
    rail.className = "ce-builder-rail";
    const question = document.createElement("button");
    question.type = "button"; question.title = "Add question"; question.textContent = "+";
    question.addEventListener("click", () => {
      pendingQuestionAfter = group.querySelector(".ce-card-active") || null;
      group.querySelector(".add-row a")?.click();
    });
    const section = document.createElement("button");
    section.type = "button"; section.title = "Add section"; section.textContent = "▤";
    section.addEventListener("click", () => {
      pendingSectionAfter = group.querySelector(".ce-card-active") || null;
      document.querySelector("#sections-group .add-row a")?.click();
    });
    rail.append(question, section);
    group.appendChild(rail);
    const initialCard = group.querySelector(".ce-structure-card.ce-card-active:not([hidden])")
      || group.querySelector(".ce-structure-card:not([hidden])");
    if (initialCard) activateCard(initialCard);
    else positionBuilderRail(null);
  }

  function enableReordering() {
    const group = document.querySelector("#questions-group");
    if (!group) return;
    group.addEventListener("dragover", (event) => {
      event.preventDefault();
      const dragging = group.querySelector(".ce-structure-dragging");
      if (!dragging) return;
      const container = dragging.parentElement;
      if (!container) return;
      const cards = Array.from(container.querySelectorAll(":scope > .ce-structure-card:not(.ce-structure-dragging):not([hidden])"));
      const next = cards.find((card) => event.clientY < card.getBoundingClientRect().top + card.offsetHeight / 2);
      const addRow = Array.from(container.children).find((child) => child.classList?.contains("add-row"));
      container.insertBefore(dragging, next || addRow || null);
    });
    group.addEventListener("drop", (event) => {
      if (group.querySelector(".ce-structure-dragging")) event.preventDefault();
    });
  }

  function placeAddedCard(row) {
    const card = row?.matches?.(".inline-related") ? row : row?.querySelector?.(".inline-related");
    if (!card || card.classList.contains("empty-form")) return;
    enhanceAll(card);
    const container = questionContainer();
    if (!container) return;
    if (card.classList.contains("ce-section-card")) {
      const reference = pendingSectionAfter;
      container.insertBefore(card, reference?.parentElement === container ? reference.nextSibling : container.querySelector(".add-row"));
      pendingSectionAfter = null;
    } else if (card.classList.contains("ce-question-card") && pendingQuestionAfter) {
      const reference = pendingQuestionAfter;
      if (reference.parentElement === container) container.insertBefore(card, reference.nextSibling);
      pendingQuestionAfter = null;
    }
    activateCard(card);
    updateStructure();
  }

  function setHeaderLinks() {
    const preview = document.querySelector("[data-preview-form]");
    const previewSource = document.querySelector(".field-preview_link a");
    if (preview) previewSource?.href ? preview.href = previewSource.href : preview.hidden = true;
    const responses = document.querySelector("[data-responses-link]");
    const responseSource = document.querySelector(".field-survey_data_link a");
    if (responses) responseSource?.href ? responses.href = responseSource.href : responses.hidden = true;
  }

  function saveState() {
    const form = document.querySelector("#content-main form");
    const status = document.querySelector("[data-save-status]");
    if (!form || !status) return;
    form.addEventListener("input", () => { status.textContent = "Unsaved changes"; status.classList.add("dirty"); });
    form.addEventListener("submit", () => { status.textContent = "Saving…"; status.classList.remove("dirty"); });
  }

  document.addEventListener("DOMContentLoaded", () => {
    enhanceAll(document); enhanceRichTextareas(document); initializeSectionLayout(); simplifyAddButtons(); addRail(); addSectionOrganizer(); enableReordering(); setHeaderLinks(); saveState();
    const firstInvalid = document.querySelector("#questions-group .ce-card-invalid");
    if (firstInvalid) window.requestAnimationFrame(() => firstInvalid.scrollIntoView({ block: "center" }));
    document.addEventListener("formset:added", (event) => {
      placeAddedCard(event.target); enhanceRichTextareas(event.target); simplifyAddButtons(); refreshSectionOrganizer();
    });
    if (window.django?.jQuery) {
      window.django.jQuery(document).on("formset:added", function (_event, row) {
        placeAddedCard(row?.[0]); enhanceRichTextareas(row?.[0]);
        simplifyAddButtons(); refreshSectionOrganizer();
      });
    }
    window.addEventListener("resize", () => positionBuilderRail(null));
  });
})();
