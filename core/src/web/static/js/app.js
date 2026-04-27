/**
 * NORA Web UI — client-side utilities.
 */

// HTMX configuration
document.body.addEventListener("htmx:configRequest", function (event) {
    // Ensure HTMX requests include the correct base path.
    // The root_path is embedded in the page by the template.
    const rootPath = document.body.dataset.rootPath || "";
    if (rootPath && !event.detail.path.startsWith(rootPath)) {
        event.detail.path = rootPath + event.detail.path;
    }
});

// Poll system status on page load
document.addEventListener("DOMContentLoaded", function () {
    refreshStatus();
});

function refreshStatus() {
    const rootPath = document.body.dataset.rootPath || "";
    const dot = document.getElementById("system-status-dot");
    const label = document.getElementById("system-status-label");
    if (!dot || !label) return;

    fetch(rootPath + "/api/health")
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data.status === "ok") {
                dot.className = "status-dot online";
                label.textContent = data.ollama ? "Ollama connected" : "Ollama unavailable";
            } else {
                dot.className = "status-dot offline";
                label.textContent = "Error";
            }
        })
        .catch(function () {
            dot.className = "status-dot offline";
            label.textContent = "Unreachable";
        });
}
