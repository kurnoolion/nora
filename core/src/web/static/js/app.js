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


/*
 * Req-ID bubbles (strand req-id-bubbles). The panel floats over the page, so
 * it needs the dismissal affordances an inline panel does not: click-outside
 * and Escape. Listeners are delegated from document because the answer markup
 * is injected into the page, not present at load.
 */
(function () {
    function closeOpenBubbles(except) {
        document.querySelectorAll(".req-bubble > .collapse.show").forEach(function (panel) {
            if (except && panel === except) return;
            bootstrap.Collapse.getOrCreateInstance(panel).hide();
        });
    }

    // Keep the panel on screen: anchor left by default, flip to the right
    // edge when a badge sits far enough right that the panel would clip.
    document.addEventListener("shown.bs.collapse", function (e) {
        var panel = e.target;
        var host = panel.parentElement;
        if (!host || !host.classList.contains("req-bubble")) return;
        closeOpenBubbles(panel);
        panel.classList.remove("req-bubble-flip");
        var rect = panel.getBoundingClientRect();
        if (rect.right > document.documentElement.clientWidth - 8) {
            panel.classList.add("req-bubble-flip");
        }
    });

    document.addEventListener("click", function (e) {
        // A click on the badge is handled by Bootstrap's own toggle.
        if (e.target.closest(".req-bubble")) return;
        closeOpenBubbles(null);
    });

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closeOpenBubbles(null);
    });
})();

