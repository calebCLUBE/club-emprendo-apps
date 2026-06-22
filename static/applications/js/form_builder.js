(function () {
  const QUESTION_TYPES_WITH_OPTIONS = new Set(["choice", "multi_choice"]);
  const ADVANCED_FIELDS = [
    "field-show_if_conditions",
    "field-confirm_value",
    "field-pre_hr",
    "field-pre_text",
    "field-slug",
    "field-position",
    "field-active",
  ];

  function rowFor(card, className) {
    return card.querySelector("." + className);
  }

  function setOptionsVisibility(card) {
    const type = card.querySelector("select[id$='-field_type']");
    const options = rowFor(card, "field-answer_options");
    if (!type || !options) return;
    options.classList.toggle("ce-builder-hidden", !QUESTION_TYPES_WITH_OPTIONS.has(type.value));
  }

  function enhanceQuestion(card) {
    if (card.dataset.simpleBuilder === "1") return;
    if (!card.querySelector("select[id$='-field_type']")) return;
    card.dataset.simpleBuilder = "1";

    ADVANCED_FIELDS.forEach((name) => rowFor(card, name)?.classList.add("ce-builder-hidden"));
    setOptionsVisibility(card);
    card.querySelector("select[id$='-field_type']")?.addEventListener("change", () => setOptionsVisibility(card));

    const actions = document.createElement("div");
    actions.className = "ce-question-actions";
    const advanced = document.createElement("button");
    advanced.type = "button";
    advanced.textContent = "More options";
    advanced.addEventListener("click", () => {
      const opening = ADVANCED_FIELDS.some((name) => rowFor(card, name)?.classList.contains("ce-builder-hidden"));
      ADVANCED_FIELDS.forEach((name) => rowFor(card, name)?.classList.toggle("ce-builder-hidden", !opening));
      advanced.textContent = opening ? "Hide options" : "More options";
    });
    actions.appendChild(advanced);
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

  function setPreviewLink() {
    const link = document.querySelector("[data-preview-form]");
    const adminPreview = document.querySelector(".field-preview_link a");
    if (!link) return;
    if (adminPreview?.href) link.href = adminPreview.href;
    else link.hidden = true;
  }

  document.addEventListener("DOMContentLoaded", () => {
    enhanceAll(document);
    simplifyAddButtons();
    setPreviewLink();
    document.addEventListener("formset:added", (event) => {
      enhanceAll(event.target);
      simplifyAddButtons();
    });
  });
})();
