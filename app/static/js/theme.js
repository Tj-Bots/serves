function serves_systemTheme() {
    try {
        return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    } catch (e) {
        return "dark";
    }
}

function serves_applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try {
        localStorage.setItem("serves-theme", theme);
    } catch (e) {}
}

function serves_toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || serves_systemTheme();
    serves_applyTheme(current === "dark" ? "light" : "dark");
}

document.addEventListener("DOMContentLoaded", function () {
    let stored = null;
    try {
        stored = localStorage.getItem("serves-theme");
    } catch (e) {}
    // אם המשתמש לא בחר במפורש, לא נשמור את ברירת המחדל ל-localStorage -
    // כך שאם מצב המערכת שלו משתנה (למשל מצב לילה אוטומטי), האתר ימשיך
    // לעקוב אחריו במקום להינעל על מה שזוהה בטעינה הראשונה.
    document.documentElement.setAttribute("data-theme", stored || serves_systemTheme());

    const btn = document.getElementById("theme-toggle");
    if (btn) {
        btn.addEventListener("click", serves_toggleTheme);
    }
});
