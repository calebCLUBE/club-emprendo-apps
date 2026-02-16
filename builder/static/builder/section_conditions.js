document.addEventListener("DOMContentLoaded", () => {
  function updateValueInput(selectEl) {
    const form = selectEl.closest("form");
    if (!form) return;
    const valueInput = form.querySelector("[data-show-if-value]");
    if (!valueInput) return;
    const selected = selectEl.options[selectEl.selectedIndex];
    const optsRaw = selected ? (selected.dataset.options || "") : "";
    const options = optsRaw.split("|").filter(Boolean);

    // Clear datalist
    const listId = valueInput.getAttribute("list");
    const datalist = listId ? document.getElementById(listId) : null;
    if (datalist) {
      datalist.innerHTML = "";
      options.forEach((opt) => {
        const o = document.createElement("option");
        o.value = opt;
        datalist.appendChild(o);
      });
    }

    // If options exist, and current value is empty, pick first
    if (options.length && !valueInput.value) {
      valueInput.value = options[0];
    }
  }

  document.querySelectorAll("[data-show-if-question]").forEach((sel) => {
    sel.addEventListener("change", () => updateValueInput(sel));
    // initialize
    updateValueInput(sel);
  });
});
