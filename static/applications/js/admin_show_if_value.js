(function () {
  function parseJSON(raw, fallback) {
    try {
      return JSON.parse(raw || "") || fallback;
    } catch (_) {
      return fallback;
    }
  }

  function parseChoices(selectEl) {
    if (!selectEl) return {};
    return parseJSON(selectEl.dataset.showIfChoices, {});
  }

  function parseChoicesMap(selectEl) {
    if (!selectEl) return {};
    return parseJSON(selectEl.dataset.showIfChoicesMap, {});
  }

  function addOption(select, value, label) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }

  function optionValue(opt) {
    return Array.isArray(opt) ? opt[0] : opt.value;
  }

  function optionLabel(opt) {
    const value = optionValue(opt);
    return Array.isArray(opt) ? opt[1] : (opt.label || value);
  }

  function buildChoiceMapFromConditionsWidget() {
    const root = document.querySelector(".ce-cond-widget[data-questions]");
    if (!root) return {};

    const questions = parseJSON(root.getAttribute("data-questions"), []);
    if (!Array.isArray(questions)) return {};

    const map = {};
    questions.forEach((q) => {
      const qid = String(q.id || "");
      if (!qid) return;

      let opts = [];
      if (q.field_type === "boolean") {
        opts = [
          ["yes", "si"],
          ["no", "no"],
        ];
      } else if (q.field_type === "choice" || q.field_type === "multi_choice") {
        opts = (q.choices || []).map((c) => [c.value, c.label || c.value]);
      }

      map[qid] = opts;
    });

    return map;
  }

  function getChoiceMap(qSelect, vSelect) {
    const fromValue = parseChoices(vSelect);
    if (Object.keys(fromValue).length) return fromValue;

    const fromQuestion = parseChoicesMap(qSelect);
    if (Object.keys(fromQuestion).length) return fromQuestion;

    const fromConditionsWidget = buildChoiceMapFromConditionsWidget();
    if (Object.keys(fromConditionsWidget).length) return fromConditionsWidget;

    const anyMappedSelect = document.querySelector(
      "select[id$='show_if_question'][data-show-if-choices-map]"
    );
    return parseChoicesMap(anyMappedSelect);
  }

  function wirePair(qSelect, vSelect) {
    if (!qSelect || !vSelect || qSelect.dataset.showIfValueWired === "1") return;
    qSelect.dataset.showIfValueWired = "1";

    const placeholder = vSelect.dataset.placeholder || "— elige una respuesta —";
    if (!vSelect.dataset.currentValue) {
      vSelect.dataset.currentValue = vSelect.value || "";
    }

    function rebuild() {
      const choiceMap = getChoiceMap(qSelect, vSelect);
      const qid = qSelect.value || "";
      const opts = choiceMap[qid] || [];
      const current = vSelect.dataset.currentValue || "";

      vSelect.innerHTML = "";
      addOption(vSelect, "", placeholder);

      opts.forEach((o) => {
        const val = optionValue(o);
        addOption(vSelect, val || "", optionLabel(o) || val || "");
      });

      if (current && !opts.some((o) => optionValue(o) === current)) {
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

  function wireAll(root) {
    const container = root && root.querySelectorAll ? root : document;
    const questionSelects = Array.from(
      container.querySelectorAll("select[id$='show_if_question']")
    );

    questionSelects.forEach((qSel) => {
      const prefix = qSel.id.replace(/show_if_question$/, "");
      const vSel = document.getElementById(prefix + "show_if_value");
      if (vSel) wirePair(qSel, vSel);
    });
  }

  function init() {
    wireAll(document);

    document.addEventListener("formset:added", (event) => {
      const row = event?.detail?.formsetRow || event?.target;
      wireAll(row);
    });

    if (window.django && window.django.jQuery) {
      window.django.jQuery(document).on("formset:added", function (_event, $row) {
        wireAll($row && $row.length ? $row[0] : document);
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
