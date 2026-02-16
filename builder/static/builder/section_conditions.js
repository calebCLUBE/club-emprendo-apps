document.addEventListener("DOMContentLoaded", () => {
  function updateValueInput(selectEl) {
    const form = selectEl.closest("form");
    if (!form) return;
    const valueSelect = form.querySelector("[data-show-if-value]");
    if (!valueSelect) return;
    const selected = selectEl.options[selectEl.selectedIndex];
    const optsRaw = selected ? (selected.dataset.options || "") : "";
    const options = optsRaw.split("|").filter(Boolean);

    valueSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Selecciona valor";
    valueSelect.appendChild(placeholder);

    valueSelect.disabled = options.length === 0;
    options.forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = opt;
      valueSelect.appendChild(o);
    });
  }

  document.querySelectorAll("[data-show-if-question]").forEach((sel) => {
    sel.addEventListener("change", () => updateValueInput(sel));
    // initialize
    updateValueInput(sel);
  });
});
