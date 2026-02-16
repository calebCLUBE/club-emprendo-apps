document.addEventListener("DOMContentLoaded", () => {
  function updateValueInput(selectEl) {
    const form = selectEl.closest("form");
    if (!form) return;
    const valueSelect = form.querySelector("[data-show-if-value]");
    if (!valueSelect) return;
    const selected = selectEl.options[selectEl.selectedIndex];
    const valsRaw = selected ? (selected.dataset.optValues || "") : "";
    const labelsRaw = selected ? (selected.dataset.optLabels || "") : "";
    const values = valsRaw.split("|").filter(Boolean);
    const labels = labelsRaw.split("|").filter(Boolean);

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

  function initSectionConditions(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-show-if-question]").forEach((sel) => {
      sel.addEventListener("change", () => {
        updateValueInput(sel);
      });
      updateValueInput(sel);
    });
  }

  document.addEventListener("DOMContentLoaded", () => initSectionConditions(document));
  if (window.htmx) {
    document.body.addEventListener("htmx:afterSwap", (e) => {
      initSectionConditions(e.target);
    });
  }
});
