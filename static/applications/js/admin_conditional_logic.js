(function () {
  function parse(raw, fallback) {
    try { return JSON.parse(raw || "") || fallback; } catch (_) { return fallback; }
  }

  function initRoot(root) {
    if (!root || root.dataset.inited === "1") return;
    root.dataset.inited = "1";
    const questions = parse(root.dataset.questions, []).filter(
      (question) => Array.isArray(question.choices) && question.choices.length
    );
    const conjunction = (root.dataset.conjunction || "OR").toUpperCase();
    const input = root.querySelector("[data-cond-input]");
    const enabled = root.querySelector("[data-logic-enabled]");
    const panel = root.querySelector("[data-logic-panel]");
    const rows = root.querySelector("[data-cond-rows]");
    const add = root.querySelector("[data-add-cond]");
    const noQuestions = root.querySelector("[data-no-questions]");

    function option(value, label, selected) {
      const item = document.createElement("option");
      item.value = value;
      item.textContent = label;
      item.selected = String(value) === String(selected || "");
      return item;
    }

    function questionSelect(current) {
      const select = document.createElement("select");
      select.appendChild(option("", "Choose a question", current));
      questions.forEach((question) => {
        select.appendChild(option(question.id, question.text || "Untitled question", current));
      });
      return select;
    }

    function answerSelect(questionId, current) {
      const select = document.createElement("select");
      select.appendChild(option("", "Choose an answer", current));
      const question = questions.find((item) => String(item.id) === String(questionId));
      (question?.choices || []).forEach((choice) => {
        select.appendChild(option(choice.value, choice.label || choice.value, current));
      });
      return select;
    }

    function sync() {
      const conditions = enabled.checked
        ? Array.from(rows.querySelectorAll("[data-cond-row]")).map((row) => ({
            question_id: Number(row.querySelector("[data-question]").value || 0),
            value: row.querySelector("[data-answer]").value || "",
          })).filter((item) => item.question_id && item.value)
        : [];
      input.value = JSON.stringify(conditions);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function normalizeJoins() {
      Array.from(rows.children).forEach((child, index) => {
        child.querySelector(".ce-logic__join")?.toggleAttribute("hidden", index === 0);
      });
    }

    function addRow(condition = {}) {
      const row = document.createElement("div");
      row.className = "ce-logic__row";
      row.dataset.condRow = "1";
      const join = document.createElement("span");
      join.className = "ce-logic__join";
      join.textContent = conjunction === "AND" ? "AND" : "OR";
      const qSelect = questionSelect(condition.question_id);
      qSelect.dataset.question = "1";
      let aSelect = answerSelect(condition.question_id, condition.value);
      aSelect.dataset.answer = "1";
      const isText = document.createElement("span");
      isText.className = "ce-logic__is";
      isText.textContent = "is";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ce-logic__remove";
      remove.title = "Remove condition";
      remove.setAttribute("aria-label", "Remove condition");
      remove.textContent = "×";
      qSelect.addEventListener("change", () => {
        const replacement = answerSelect(qSelect.value, "");
        replacement.dataset.answer = "1";
        replacement.addEventListener("change", sync);
        aSelect.replaceWith(replacement);
        aSelect = replacement;
        sync();
      });
      aSelect.addEventListener("change", sync);
      remove.addEventListener("click", () => {
        row.remove();
        normalizeJoins();
        if (!rows.children.length) addRow();
        sync();
      });
      row.append(join, qSelect, isText, aSelect, remove);
      rows.appendChild(row);
      normalizeJoins();
    }

    function setEnabled(on) {
      enabled.checked = on;
      panel.hidden = !on;
      if (on && !rows.children.length) addRow();
      sync();
    }

    enabled.addEventListener("change", () => setEnabled(enabled.checked));
    add.addEventListener("click", () => { addRow(); sync(); });
    const initial = parse(input.value, []);
    initial.forEach(addRow);
    noQuestions.hidden = Boolean(questions.length);
    add.hidden = !questions.length;
    setEnabled(initial.length > 0);
  }

  function initAll(root) {
    (root || document).querySelectorAll(".ce-logic").forEach(initRoot);
  }

  document.addEventListener("DOMContentLoaded", () => initAll(document));
  document.addEventListener("formset:added", (event) => initAll(event.target));
  if (window.django?.jQuery) {
    window.django.jQuery(document).on("formset:added", function (_event, row) {
      initAll(row?.[0] || document);
    });
  }
})();
