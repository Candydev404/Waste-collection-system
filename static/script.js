/* Waste Collection Request System - small, dependency-free enhancements. */
(function () {
  "use strict";

  // Client-side check on the request form (the server validates again).
  var requestForm = document.getElementById("requestForm");
  if (requestForm) {
    requestForm.addEventListener("submit", function (event) {
      if (!requestForm.checkValidity()) {
        event.preventDefault();
        event.stopPropagation();
      }
      requestForm.classList.add("was-validated");
    });
    // Clear the server-side error styling once the user edits a field.
    requestForm.querySelectorAll(".is-invalid").forEach(function (field) {
      field.addEventListener("input", function () { field.classList.remove("is-invalid"); });
      field.addEventListener("change", function () { field.classList.remove("is-invalid"); });
    });
  }

  // Show / hide password on the login page.
  var toggle = document.getElementById("togglePassword");
  var passwordField = document.getElementById("password");
  if (toggle && passwordField) {
    toggle.addEventListener("click", function () {
      var show = passwordField.type === "password";
      passwordField.type = show ? "text" : "password";
      toggle.setAttribute("aria-pressed", show ? "true" : "false");
      toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
      toggle.querySelector("i").className = show ? "bi bi-eye-slash" : "bi bi-eye";
    });
  }

  // Copy the reference number on the success page.
  var copyButton = document.getElementById("copyReference");
  if (copyButton) {
    copyButton.addEventListener("click", function () {
      var target = document.getElementById(copyButton.getAttribute("data-copy-target"));
      var label = copyButton.querySelector("span");
      if (!target) { return; }
      var text = target.textContent.trim();
      var done = function (message) {
        label.textContent = message;
        setTimeout(function () { label.textContent = "Copy"; }, 2000);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () { done("Copied"); },
          function () { done("Press and hold to copy"); }
        );
      } else {
        // Fallback: select the text so the user can copy it manually.
        var range = document.createRange();
        range.selectNodeContents(target);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        done("Selected");
      }
    });
  }

  // Print buttons.
  document.querySelectorAll("[data-print]").forEach(function (button) {
    button.addEventListener("click", function () { window.print(); });
  });

  // Auto-submit the sort selector.
  document.querySelectorAll("[data-autosubmit]").forEach(function (select) {
    select.addEventListener("change", function () {
      if (select.form) { select.form.submit(); }
    });
  });

  // Delete confirmation: fill the shared modal, then let the user confirm.
  var modalElement = document.getElementById("deleteModal");
  if (modalElement && window.bootstrap) {
    var modal = new bootstrap.Modal(modalElement);
    var form = document.getElementById("deleteForm");
    var referenceLabel = document.getElementById("deleteReference");
    var nextField = document.getElementById("deleteNext");

    document.querySelectorAll("[data-delete-url]").forEach(function (button) {
      button.addEventListener("click", function () {
        form.setAttribute("action", button.getAttribute("data-delete-url"));
        referenceLabel.textContent = button.getAttribute("data-delete-reference") || "";
        nextField.value = button.getAttribute("data-delete-next") || "";
        modal.show();
      });
    });
  }
})();
