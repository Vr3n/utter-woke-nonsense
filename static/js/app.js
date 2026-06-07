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

document.body.addEventListener("htmx:beforeSwap", function (evt) {
  if (evt.detail.xhr.status === 400 && evt.detail.target.id === "upload-region") {
    evt.detail.shouldSwap = true;
    evt.detail.isError = false;
  }
});

function toggleActiveSaveDropdown() {
  document.getElementById("save-dropdown-menu").classList.toggle("hidden");
}

document.addEventListener("click", function (e) {
  if (e.target.id === "modal-overlay") {
    closeModal();
  }
  var dropdown = document.getElementById("active-save-dropdown");
  if (dropdown && !dropdown.contains(e.target)) {
    var menu = document.getElementById("save-dropdown-menu");
    if (menu) menu.classList.add("hidden");
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

Dropzone.autoDiscover = false;

function initUploadDropzone() {
  var form = document.getElementById("snapshot-upload-form");
  if (!form || form.dropzone) return;

  var dz = new Dropzone(form, {
    paramName: "source_file",
    maxFiles: 1,
    maxFilesize: 256,
    acceptedFiles: ".csv,.html,.htm",
    autoProcessQueue: false,
    addRemoveLinks: false,
    dictDefaultMessage: "",
    dictFallbackMessage: "",
    dictFileTooBig: "File is too big ({{filesize}}MiB). Max: {{maxFilesize}}MiB.",
    dictInvalidFileType: "Only CSV and HTML files are accepted.",
  });

  dz.on("addedfile", function () {
    if (dz.files.length > 1) {
      dz.removeFile(dz.files[0]);
    }
  });

  dz.on("sending", function (file, xhr, formData) {
    formData.append("csrfmiddlewaretoken", form.querySelector('[name="csrfmiddlewaretoken"]').value);
    formData.append("snapshot_type", form.querySelector('[name="snapshot_type"]').value);
    formData.append("ingame_date", form.querySelector('[name="ingame_date"]').value);
    formData.append("season", form.querySelector('[name="season"]').value);
    formData.append("data_label", form.querySelector('[name="data_label"]').value);
  });

  dz.on("success", function (file, response) {
    var region = document.getElementById("upload-region");
    region.innerHTML = response;
    if (window.htmx) htmx.process(region);
    dz.destroy();
  });

  dz.on("error", function (file, response, xhr) {
    if (xhr && xhr.status === 400) {
      var region = document.getElementById("upload-region");
      region.innerHTML = response;
      if (window.htmx) htmx.process(region);
      dz.destroy();
      initUploadDropzone();
    }
  });

  document.getElementById("submit-upload").addEventListener("click", function (e) {
    e.preventDefault();
    if (dz.files.length === 0) return;
    dz.processQueue();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initUploadDropzone);
} else {
  initUploadDropzone();
}
