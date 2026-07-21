function serves_applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try {
        localStorage.setItem("serves-theme", theme);
    } catch (e) {}
}

function serves_toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    serves_applyTheme(current === "dark" ? "light" : "dark");
}

document.addEventListener("DOMContentLoaded", function () {
    let theme = "dark";
    try {
        theme = localStorage.getItem("serves-theme") || "dark";
    } catch (e) {}
    serves_applyTheme(theme);

    const btn = document.getElementById("theme-toggle");
    if (btn) {
        btn.addEventListener("click", serves_toggleTheme);
    }
});
