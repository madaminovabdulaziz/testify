/* Editor behaviors: counters, image toggle/upload, dirty warning, publish dialog. */
(function () {
  "use strict";
  var P = window.PANEL || {};
  var form = document.getElementById("editor-form");
  if (!form) return;

  var dirty = false;
  var uploadsInFlight = 0;
  var saveBtn = document.getElementById("save-btn");

  form.addEventListener("input", function () { dirty = true; });
  form.addEventListener("submit", function () { dirty = false; });
  window.addEventListener("beforeunload", function (e) {
    if (!dirty) return;
    e.preventDefault();
    e.returnValue = "";
  });

  /* ---- char counters on question textareas ---- */
  document.querySelectorAll("[data-counter-for]").forEach(function (counter) {
    var field = form.elements[counter.dataset.counterFor];
    if (!field) return;
    var max = parseInt(counter.dataset.max, 10);
    var update = function () {
      var len = field.value.length;
      if (len < max * 0.8) { counter.textContent = ""; return; }
      counter.textContent = len + " / " + max;
      counter.classList.toggle("over", len > max);
    };
    field.addEventListener("input", update);
    update();
  });

  /* ---- caption budget for image questions ---- */
  function updateBudget(pos) {
    var budget = document.querySelector('[data-budget-for="' + pos + '"]');
    var toggle = form.elements["q" + pos + "_has_image"];
    if (!budget || !toggle) return;
    if (!toggle.checked) { budget.hidden = true; return; }
    var len = P.captionOverhead;
    ["text", "a", "b", "c", "d"].forEach(function (suffix) {
      var f = form.elements["q" + pos + "_" + (suffix === "text" ? "text" : suffix)];
      if (f) len += f.value.trim().length;
    });
    budget.hidden = false;
    budget.textContent = "Текст + варианты: " + len + " / " + P.captionMax +
      " символов (лимит подписи к фото)";
    budget.classList.toggle("over", len > P.captionMax);
  }

  document.querySelectorAll(".image-row").forEach(function (row) {
    var pos = row.dataset.pos;
    var toggle = row.querySelector(".has-image-toggle");
    toggle.addEventListener("change", function () {
      row.classList.toggle("enabled", toggle.checked);
      updateBudget(pos);
    });
    ["q" + pos + "_text", "q" + pos + "_a", "q" + pos + "_b", "q" + pos + "_c", "q" + pos + "_d"]
      .forEach(function (name) {
        var f = form.elements[name];
        if (f) f.addEventListener("input", function () { updateBudget(pos); });
      });
    updateBudget(pos);
  });

  /* ---- AJAX image upload ---- */
  document.querySelectorAll(".image-file").forEach(function (input) {
    input.addEventListener("change", function () {
      var file = input.files[0];
      if (!file) return;
      var pos = input.dataset.pos;
      var row = input.closest(".image-row");
      var status = row.querySelector(".image-status");

      var data = new FormData();
      data.append("image", file);

      uploadsInFlight++;
      saveBtn.disabled = true;
      status.className = "image-status";
      status.textContent = "Загрузка…";

      fetch("/panel/tests/" + P.testId + "/questions/" + pos + "/image", {
        method: "POST",
        headers: { "X-CSRF-Token": P.csrf, "X-Requested-With": "fetch" },
        body: data
      })
        .then(function (resp) { return resp.json().then(function (j) { return { ok: resp.ok, j: j }; }); })
        .then(function (r) {
          if (r.ok) {
            status.className = "image-status ok";
            status.textContent = "Изображение загружено";
            var img = row.querySelector(".image-thumb");
            if (!img) {
              img = document.createElement("img");
              img.className = "image-thumb";
              row.querySelector(".image-controls").prepend(img);
            }
            img.src = r.j.preview_url;
          } else {
            status.className = "image-status err";
            status.textContent = r.j.error || "Не удалось загрузить.";
          }
        })
        .catch(function () {
          status.className = "image-status err";
          status.textContent = "Сеть недоступна. Попробуйте ещё раз.";
        })
        .finally(function () {
          uploadsInFlight--;
          if (uploadsInFlight === 0) saveBtn.disabled = false;
          input.value = "";
        });
    });
  });

  /* ---- publish dialog ---- */
  var dialog = document.getElementById("publish-dialog");
  var openBtn = document.getElementById("publish-open");
  var cancelBtn = document.getElementById("publish-cancel");
  if (dialog && openBtn) {
    openBtn.addEventListener("click", function () { dialog.showModal(); });
    cancelBtn.addEventListener("click", function () { dialog.close(); });
    dialog.querySelector("form").addEventListener("submit", function () { dirty = false; });
  }
})();
