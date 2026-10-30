document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".flash-close").forEach((button) => {
    button.addEventListener("click", () => button.parentElement.remove());
  });

  const addLine = document.getElementById("add-bill-line");
  if (addLine) {
    addLine.addEventListener("click", () => {
      const container = document.querySelector(".bill-lines");
      const line = container.querySelector(".bill-line").cloneNode(true);
      line.querySelectorAll("input").forEach((input) => {
        input.value = "";
      });
      container.appendChild(line);
    });
  }

  document.addEventListener("click", (event) => {
    if (event.target.matches(".remove-line")) {
      const lines = document.querySelectorAll(".bill-line");
      if (lines.length > 1) {
        event.target.closest(".bill-line").remove();
      }
    }
  });

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const submit = form.querySelector('button[type="submit"]:not([name]), button.primary[type="submit"]');
      if (submit && !submit.dataset.busy) {
        submit.dataset.busy = "true";
        submit.setAttribute("aria-busy", "true");
      }
    });
  });
});
