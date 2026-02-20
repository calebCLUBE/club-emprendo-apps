(function () {
  function parseChoices(selectEl) {
    try {
      return JSON.parse(selectEl.dataset.showIfChoices || "{}") || {};
    } catch (_) {
      return {};
    }
  }

  function addOption(select, value, label) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }

  function wirePair(qSelect, vSelect) {
    const placeholder = vSelect.dataset.placeholder || "— Selecciona valor —";
    const choiceMap = parseChoices(vSelect);

    function rebuild() {
      const qid = qSelect.value || "";
      const opts = choiceMap[qid] || [];
      const current = vSelect.dataset.currentValue || "";

      vSelect.innerHTML = "";
      addOption(vSelect, "", placeholder);

      opts.forEach((o) => {
        const val = Array.isArray(o) ? o[0] : o.value;
        const label = Array.isArray(o) ? o[1] : (o.label || val);
        addOption(vSelect, val || "", label || val || "");
      });

      if (current && !opts.some((o) => (Array.isArray(o) ? o[0] : o.value) === current)) {
        addOption(vSelect, current, `${current} (actual)`);
      }

      vSelect.value = current || "";
    }

    rebuild();

    qSelect.addEventListener("change", () => {
      vSelect.dataset.currentValue = "";
      rebuild();
    });

    vSelect.addEventListener("change", () => {
      vSelect.dataset.currentValue = vSelect.value;
    });
  }

  // Support multiple inline forms: look for any field names ending in show_if_question/value
  const questionSelects = Array.from(document.querySelectorAll("select[id$='show_if_question']"));
  questionSelects.forEach((qSel) => {
    // The corresponding value select shares the same prefix
    const prefix = qSel.id.replace(/show_if_question$/, "");
    const vSel = document.getElementById(prefix + "show_if_value");
    if (vSel) {
      wirePair(qSel, vSel);
    }
  });
})();
