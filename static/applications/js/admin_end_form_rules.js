(function () {
  function parse(raw, fallback) {
    try { return JSON.parse(raw || "") || fallback; } catch (_) { return fallback; }
  }

  function slugify(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "option";
  }

  function initRoot(root) {
    if (!root || root.dataset.inited === "1") return;
    root.dataset.inited = "1";
    const input = root.querySelector("[data-end-rules-input]");
    const enabled = root.querySelector("[data-end-rules-enabled]");
    const panel = root.querySelector("[data-end-rules-panel]");
    const list = root.querySelector("[data-end-rules-list]");
    const add = root.querySelector("[data-add-end-rule]");
    const initialAnswers = parse(root.dataset.answerOptions, []);
    const initialEmails = parse(root.dataset.storedEmails, []);

    function answerOptions() {
      const card = root.closest(".inline-related");
      const type = card?.querySelector("select[id$='-field_type']")?.value;
      if (type === "boolean") return [{ value: "yes", label: "Sí" }, { value: "no", label: "No" }];
      const source = card?.querySelector(".ce-options-source");
      if (!source) return initialAnswers;
      const labels = source.value.split("\n").map((item) => item.trim()).filter(Boolean);
      return labels.map((label, index) => ({
        value: initialAnswers[index]?.value || slugify(label),
        label,
      }));
    }

    function emailNames() {
      const live = Array.from(document.querySelectorAll("input[id^='id_stored_emails-'][id$='-name']"))
        .filter((field) => !field.closest(".inline-related")?.querySelector("input[id$='-DELETE']:checked"))
        .map((field) => field.value.trim()).filter(Boolean);
      return Array.from(new Set([...initialEmails, ...live]));
    }

    function fillSelect(select, items, placeholder, current) {
      select.innerHTML = "";
      const empty = document.createElement("option");
      empty.value = ""; empty.textContent = placeholder; select.appendChild(empty);
      items.forEach((item) => {
        const option = document.createElement("option");
        option.value = typeof item === "string" ? item : item.value;
        option.textContent = typeof item === "string" ? item : item.label;
        option.selected = option.value === String(current || "");
        select.appendChild(option);
      });
    }

    function sync() {
      const rules = enabled.checked ? Array.from(list.querySelectorAll("[data-end-rule]")).map((row) => ({
        value: row.querySelector("[data-rule-answer]").value,
        email_name: row.querySelector("[data-rule-email]").value,
      })).filter((rule) => rule.value) : [];
      input.value = JSON.stringify(rules);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function addRule(rule = {}) {
      const row = document.createElement("div");
      row.className = "ce-end-rule";
      row.dataset.endRule = "1";
      const answer = document.createElement("select");
      answer.dataset.ruleAnswer = "1";
      fillSelect(answer, answerOptions(), "If answer is…", rule.value);
      const email = document.createElement("select");
      email.dataset.ruleEmail = "1";
      fillSelect(email, emailNames(), "Do not send an email", rule.email_name);
      email.addEventListener("focus", () => fillSelect(email, emailNames(), "Do not send an email", email.value));
      const remove = document.createElement("button");
      remove.type = "button"; remove.className = "ce-logic__remove";
      remove.title = "Remove ending"; remove.textContent = "×";
      [answer, email].forEach((field) => {
        field.addEventListener("input", sync); field.addEventListener("change", sync);
      });
      remove.addEventListener("click", () => {
        row.remove();
        if (!list.children.length) addRule();
        sync();
      });
      const answerWrap = document.createElement("label");
      answerWrap.innerHTML = "<span>Trigger answer</span>"; answerWrap.appendChild(answer);
      const emailWrap = document.createElement("label");
      emailWrap.innerHTML = "<span>Stored email to send</span>"; emailWrap.appendChild(email);
      row.append(answerWrap, emailWrap, remove);
      list.appendChild(row);
    }

    function refreshAnswers() {
      list.querySelectorAll("[data-rule-answer]").forEach((select) => {
        fillSelect(select, answerOptions(), "If answer is…", select.value);
      });
    }

    const card = root.closest(".inline-related");
    card?.querySelector("select[id$='-field_type']")?.addEventListener("change", refreshAnswers);
    card?.querySelector("textarea[id$='-answer_options']")?.addEventListener("input", refreshAnswers);
    enabled.addEventListener("change", () => {
      panel.hidden = !enabled.checked;
      if (enabled.checked && !list.children.length) addRule();
      sync();
    });
    add.addEventListener("click", () => { addRule(); sync(); });
    const initial = parse(input.value, []);
    initial.forEach(addRule);
    enabled.checked = initial.length > 0;
    panel.hidden = !enabled.checked;
  }

  function initAll(root) {
    const scope = root || document;
    if (scope.matches?.(".ce-end-rules")) initRoot(scope);
    scope.querySelectorAll?.(".ce-end-rules").forEach(initRoot);
  }
  document.addEventListener("DOMContentLoaded", () => initAll(document));
  document.addEventListener("formset:added", (event) => initAll(event.target));
  if (window.django?.jQuery) {
    window.django.jQuery(document).on("formset:added", function (_event, row) {
      initAll(row?.[0] || document);
    });
  }
})();
