(function () {
    const appId = window.SERVES_APP_ID;
    if (!appId) return;

    const terminal = document.getElementById("terminal");
    const statusBadge = document.getElementById("status-badge");

    function appendLine(text) {
        const line = document.createElement("div");
        line.className = "log-line";
        if (text.startsWith("[serves]")) {
            line.classList.add("system");
        }
        line.textContent = text;
        terminal.appendChild(line);
        const atBottom = terminal.scrollHeight - terminal.clientHeight <= terminal.scrollTop + 40;
        if (atBottom) {
            terminal.scrollTop = terminal.scrollHeight;
        }
    }

    function updateStatusFromLine(text) {
        if (!statusBadge) return;
        const lower = text.toLowerCase();
        if (lower.includes("application is running")) {
            statusBadge.textContent = "running";
            statusBadge.className = "badge badge-running";
        } else if (lower.includes("exit code 0")) {
            statusBadge.textContent = "stopped";
            statusBadge.className = "badge badge-stopped";
        } else if (lower.includes("error") || lower.includes("failed")) {
            statusBadge.textContent = "failed";
            statusBadge.className = "badge badge-failed";
        }
    }

    function connect() {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(`${proto}//${location.host}/ws/logs/${appId}`);

        ws.onmessage = (event) => {
            appendLine(event.data);
            updateStatusFromLine(event.data);
        };

        ws.onclose = () => {
            setTimeout(connect, 3000);
        };

        ws.onerror = () => ws.close();
    }

    terminal.scrollTop = terminal.scrollHeight;
    connect();
})();
