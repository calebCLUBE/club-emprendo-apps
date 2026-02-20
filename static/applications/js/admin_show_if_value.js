(function () {
  const qSelect = document.getElementById("id_show_if_question");
  const vSelect = document.getElementById("id_show_if_value");
  if (!qSelect || !vSelect) return;

  let choiceMap = {};
  try {
    choiceMap = JSON.parse(vSelect.dataset.showIfChoices || "{}") || {};
  } catch (e) {
    choiceMap = {};
  }

  const placeholder = vSelect.dataset.placeholder || "— Selecciona valor —";

  const addOption = (select, value, label) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  };

  function rebuild() {
    const qid = qSelect.value || "";
    const opts = choiceMap[qid] || [];
    const current = vSelect.dataset.currentValue || "";

    vSelect.innerHTML = "";
    addOption(vSelect, "", placeholder);

    opts.forEach((o) => {
      const val = Array.isArray(o) ? o[0] : o.value;
      const label = Array.isArray(o) ? o[1] : o.label || val;
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
})();
