document.addEventListener("DOMContentLoaded", () => {
  function updateValueInput(selectEl) {
    const row = selectEl.closest(".condition-row");
    if (!row) return;
    const valueSelect = row.querySelector("[data-show-if-value]");
    if (!valueSelect) return;
    const selected = selectEl.options[selectEl.selectedIndex];
    const valsRaw = selected ? (selected.dataset.optValues || "") : "";
    const labelsRaw = selected ? (selected.dataset.optLabels || "") : "";
    const values = valsRaw.split("|").filter((x) => x !== "");
    const labels = labelsRaw.split("|").filter((x) => x !== "");

    const current = valueSelect.value || valueSelect.dataset.current || "";
    valueSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Selecciona valor";
    valueSelect.appendChild(placeholder);

    valueSelect.disabled = values.length === 0;
    values.forEach((opt, idx) => {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = labels[idx] || opt;
      if (opt.toLowerCase() === current.toLowerCase()) {
        o.selected = true;
      }
      valueSelect.appendChild(o);
    });
  }

  function wireRow(row) {
    const sel = row.querySelector("[data-show-if-question]");
    if (!sel) return;
    sel.addEventListener("change", () => updateValueInput(sel));
    updateValueInput(sel);
  }

  function addCondition(btn) {
    const container = btn.closest("form").querySelector("[data-cond-container]");
    if (!container) return;
    const template = container.querySelector(".condition-row");
    const clone = template.cloneNode(true);
    clone.querySelector("[data-show-if-question]").selectedIndex = 0;
    const valueSelect = clone.querySelector("[data-show-if-value]");
    valueSelect.innerHTML = '<option value=\"\">Selecciona valor</option>';
    valueSelect.dataset.current = "";
    container.appendChild(clone);
    wireRow(clone);
  }

  function initSectionConditions(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-cond-container]").forEach((container) => {
      container.querySelectorAll(".condition-row").forEach(wireRow);
    });
    scope.querySelectorAll("[data-add-cond]").forEach((btn) => {
      btn.addEventListener("click", () => addCondition(btn));
    });
  }

  document.addEventListener("DOMContentLoaded", () => initSectionConditions(document));
  if (window.htmx) {
    document.body.addEventListener("htmx:afterSwap", (e) => initSectionConditions(e.target));
  }
});
