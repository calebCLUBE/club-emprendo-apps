(function () {
  const QUESTION_TYPES_WITH_OPTIONS = new Set(["choice", "multi_choice"]);
  const ADVANCED_FIELDS = [
    "field-show_if_conditions", "field-end_form_rules", "field-confirm_value", "field-pre_hr",
    "field-pre_text", "field-slug", "field-position", "field-active",
  ];

  const rowFor = (card, name) => card.querySelector("." + name);
  let pendingSectionAfter = null;
  let pendingQuestionAfter = null;

  function activateCard(card) {
    document.querySelectorAll("#questions-group .ce-structure-card.ce-card-active")
      .forEach((item) => item.classList.remove("ce-card-active"));
    card?.classList.add("ce-card-active");
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

  function optionEditor(row) {
    if (!row || row.dataset.optionsEnhanced === "1") return;
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
      drag.title = "Drag to reorder option";
      drag.setAttribute("aria-label", "Drag to reorder option");
      drag.textContent = "⋮⋮";
      const marker = document.createElement("span");
      marker.className = "ce-option-marker";
      marker.setAttribute("aria-hidden", "true");
      const input = document.createElement("input");
      input.type = "text";
      input.value = value || "";
      input.placeholder = "Option";
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
    add.textContent = "Add option";
    add.addEventListener("click", () => addOption("", true));
    editor.appendChild(add);
    row.appendChild(editor);
  }

  function updateQuestionPositions() {
    updateStructure();
  }

  function setOptionsVisibility(card) {
    const type = card.querySelector("select[id$='-field_type']");
    const row = rowFor(card, "field-answer_options");
    if (!type || !row) return;
    row.classList.toggle("ce-builder-hidden", !QUESTION_TYPES_WITH_OPTIONS.has(type.value));
    if (QUESTION_TYPES_WITH_OPTIONS.has(type.value)) optionEditor(row);
    card.classList.toggle("ce-multiple-options", type.value === "multi_choice");
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
    const sourceOptions = source.querySelector(".ce-options-source");
    const targetOptions = target.querySelector(".ce-options-source");
    if (sourceOptions && targetOptions) {
      targetOptions.value = sourceOptions.value;
      target.querySelector(".ce-options-editor")?.remove();
      const targetRow = targetOptions.closest(".field-answer_options");
      targetRow.dataset.optionsEnhanced = "0";
      optionEditor(targetRow);
    }
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
    const dragHandle = document.createElement("div");
    dragHandle.className = "ce-drag-handle ce-section-drag-handle";
    dragHandle.title = "Drag to move section";
    dragHandle.textContent = "⠿";
    card.prepend(label);
    card.prepend(dragHandle);
    dragHandle.addEventListener("dragstart", (event) => {
      event.dataTransfer?.setData("text/plain", "section");
      if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
      card.classList.add("ce-structure-dragging");
    });
    dragHandle.addEventListener("dragend", () => {
      card.classList.remove("ce-structure-dragging");
      updateStructure();
    });
    wirePointerSort(dragHandle, card, ".ce-structure-card", updateStructure);
    card.addEventListener("pointerdown", () => activateCard(card));
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
    enhanceAll(document); initializeSectionLayout(); simplifyAddButtons(); addRail(); enableReordering(); setHeaderLinks(); saveState();
    const firstInvalid = document.querySelector("#questions-group .ce-card-invalid");
    if (firstInvalid) window.requestAnimationFrame(() => firstInvalid.scrollIntoView({ block: "center" }));
    document.addEventListener("formset:added", (event) => {
      placeAddedCard(event.target); simplifyAddButtons();
    });
    if (window.django?.jQuery) {
      window.django.jQuery(document).on("formset:added", function (_event, row) {
        placeAddedCard(row?.[0]);
        simplifyAddButtons();
      });
    }
  });
})();
