document.addEventListener("DOMContentLoaded", function () {
    const toggle = document.getElementById("nav-toggle");
    const menu = document.getElementById("nav-menu");
    if (toggle && menu) {
        toggle.addEventListener("click", function (e) {
            e.stopPropagation();
            const isOpen = menu.classList.toggle("open");
            toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });
        document.addEventListener("click", function (e) {
            if (!menu.contains(e.target) && !toggle.contains(e.target)) {
                menu.classList.remove("open");
                toggle.setAttribute("aria-expanded", "false");
            }
        });
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") {
                menu.classList.remove("open");
                toggle.setAttribute("aria-expanded", "false");
            }
        });
    }

    const copyBtn = document.getElementById("copy-logs");
    const terminal = document.getElementById("terminal");
    if (copyBtn && terminal) {
        copyBtn.addEventListener("click", function () {
            const lines = Array.from(terminal.querySelectorAll(".log-line")).map((el) => el.textContent);
            const text = lines.join("\n");
            navigator.clipboard.writeText(text).then(function () {
                copyBtn.classList.add("copied");
                setTimeout(function () {
                    copyBtn.classList.remove("copied");
                }, 1500);
            }).catch(function () {});
        });
    }
});
