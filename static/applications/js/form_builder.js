(function () {
  const QUESTION_TYPES_WITH_OPTIONS = new Set(["choice", "multi_choice"]);
  const ADVANCED_FIELDS = [
    "field-show_if_conditions", "field-confirm_value", "field-pre_hr",
    "field-pre_text", "field-slug", "field-position", "field-active",
  ];

  const rowFor = (card, name) => card.querySelector("." + name);

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
      drag.draggable = true;
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
    Array.from(document.querySelectorAll("#questions-group .inline-related:not(.empty-form)"))
      .filter((item) => !item.hidden && !item.querySelector("input[id$='-DELETE']:checked"))
      .forEach((item, index) => {
        const position = item.querySelector("input[id$='-position']");
        if (position) {
          position.value = index + 1;
          position.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
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
    const dragHandle = document.createElement("div");
    dragHandle.className = "ce-drag-handle";
    dragHandle.title = "Drag to reorder";
    dragHandle.textContent = "⠿";
    dragHandle.draggable = true;
    card.prepend(dragHandle);
    dragHandle.addEventListener("dragstart", (event) => {
      event.dataTransfer?.setData("text/plain", "question");
      if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
      card.classList.add("ce-dragging");
    });
    dragHandle.addEventListener("dragend", () => {
      card.classList.remove("ce-dragging");
      updateQuestionPositions();
    });
    card.addEventListener("pointerdown", () => {
      document.querySelectorAll("#questions-group .inline-related.ce-question-active")
        .forEach((item) => item.classList.remove("ce-question-active"));
      card.classList.add("ce-question-active");
    });
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
  }

  function enhanceAll(root) {
    (root || document).querySelectorAll(".inline-related").forEach(enhanceQuestion);
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
    question.addEventListener("click", () => group.querySelector(".add-row a")?.click());
    const section = document.createElement("button");
    section.type = "button"; section.title = "Add section"; section.textContent = "▤";
    section.addEventListener("click", () => document.querySelector("#sections-group .add-row a")?.click());
    rail.append(question, section);
    group.appendChild(rail);
  }

  function enableReordering() {
    const group = document.querySelector("#questions-group");
    if (!group) return;
    group.addEventListener("dragover", (event) => {
      event.preventDefault();
      const dragging = group.querySelector(".ce-dragging");
      if (!dragging) return;
      const container = dragging.parentElement;
      if (!container) return;
      const cards = Array.from(container.querySelectorAll(":scope > .inline-related:not(.empty-form):not(.ce-dragging)"));
      const next = cards.find((card) => event.clientY < card.getBoundingClientRect().top + card.offsetHeight / 2);
      const addRow = Array.from(container.children).find((child) => child.classList?.contains("add-row"));
      container.insertBefore(dragging, next || addRow || null);
    });
    group.addEventListener("drop", (event) => {
      if (group.querySelector(".ce-dragging")) event.preventDefault();
    });
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
    enhanceAll(document); simplifyAddButtons(); addRail(); enableReordering(); setHeaderLinks(); saveState();
    document.addEventListener("formset:added", (event) => {
      enhanceAll(event.target); simplifyAddButtons();
    });
  });
})();
