function showToast(message, level) {
  level = level || "info";
  var colors = {
    success: "border-cyan-800 bg-cyan-950/50 text-cyan-300",
    error: "border-red-800 bg-red-950/50 text-red-300",
    info: "border-zinc-700 bg-zinc-900 text-zinc-300",
  };
  var toast = document.createElement("div");
  toast.className =
    "fixed top-6 right-6 z-[100] border px-6 py-4 text-sm uppercase tracking-[0.2em] shadow-2xl transition-all duration-500 " +
    (colors[level] || colors.info);
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(function () {
    toast.style.opacity = "0";
    setTimeout(function () { toast.remove(); }, 500);
  }, 3500);
}

function openModal() {
  document.getElementById("modal-overlay").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
  document.getElementById("modal-content").innerHTML = "";
}

document.body.addEventListener("htmx:afterSwap", function (e) {
  if (e.detail.target.id === "modal-content") {
    openModal();
  }
});

document.body.addEventListener("htmx:afterSettle", function (e) {
  var cards = e.detail.target.querySelectorAll(".animate-fade-slide-up");
  for (var i = 0; i < cards.length; i++) {
    cards[i].style.animationDelay = i * 100 + "ms";
  }
});

document.addEventListener("click", function (e) {
  if (e.target.id === "modal-overlay") {
    closeModal();
  }
});

document.addEventListener("keydown", function (e) {
  if (
    e.key === "Escape" &&
    !document.getElementById("modal-overlay").classList.contains("hidden")
  ) {
    closeModal();
  }
});

document.addEventListener("message", function (e) {
  showToast(e.detail.message, e.detail.level);
});

document.body.addEventListener("save-created", function (e) {
  closeModal();
});
