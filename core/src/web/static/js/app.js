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
 * Req-ID bubbles (strand req-id-bubbles).
 *
 * Hover previews, click pins. Hover alone is too trigger-happy here — answers
 * are dense with req IDs, so crossing a paragraph would flash panels open —
 * hence the open delay, and the grace period that lets the pointer travel into
 * the panel so its text can actually be selected. A clicked bubble is PINNED
 * and ignores mouse-out entirely; that is the path for reading and copying.
 *
 * Every open path funnels through Bootstrap's show.bs.collapse, which is where
 * the `bubbleopen` event that htmx listens for is dispatched — so hover, click
 * and keyboard focus all load the body by one route.
 *
 * Listeners are delegated from document because the answer markup is injected
 * into the page, not present at load.
 */
(function () {
    var OPEN_DELAY = 220;   // ms of dwell before a hover counts as intent
    var CLOSE_GRACE = 180;  // ms to let the pointer cross into the panel
    // Hover-open only where hovering is real. Touch reports (hover: none), and
    // opening on a synthesised hover there would fight the tap.
    var canHover = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    var openTimer = null, closeTimer = null;

    function panelOf(host) { return host.querySelector(":scope > .collapse, :scope > .collapsing"); }
    function instance(panel) { return bootstrap.Collapse.getOrCreateInstance(panel, {toggle: false}); }

    function open(host) {
        var panel = panelOf(host);
        if (panel && !panel.classList.contains("show")) instance(panel).show();
    }

    function close(host) {
        var panel = panelOf(host);
        if (!panel || panel.dataset.pinned === "1") return;
        if (panel.classList.contains("show")) instance(panel).hide();
    }

    function closeOpenBubbles(except) {
        document.querySelectorAll(".req-bubble > .collapse.show").forEach(function (panel) {
            if (except && panel === except) return;
            delete panel.dataset.pinned;
            instance(panel).hide();
        });
    }

    function clearTimers() {
        clearTimeout(openTimer); clearTimeout(closeTimer);
        openTimer = closeTimer = null;
    }

    // One fetch route for every open path.
    document.addEventListener("show.bs.collapse", function (e) {
        var host = e.target.parentElement;
        if (!host || !host.classList.contains("req-bubble")) return;
        var badge = host.querySelector(".req-bubble-badge");
        if (badge) badge.dispatchEvent(new CustomEvent("bubbleopen", {bubbles: false}));
    });

    // Keep the panel on screen: anchor left by default, flip to the right edge
    // when a badge sits far enough right that the panel would clip.
    document.addEventListener("shown.bs.collapse", function (e) {
        var panel = e.target;
        var host = panel.parentElement;
        if (!host || !host.classList.contains("req-bubble")) return;
        closeOpenBubbles(panel);
        panel.classList.remove("req-bubble-flip");
        if (panel.getBoundingClientRect().right > document.documentElement.clientWidth - 8) {
            panel.classList.add("req-bubble-flip");
        }
    });

    if (canHover) {
        document.addEventListener("mouseover", function (e) {
            var host = e.target.closest(".req-bubble");
            if (!host) return;
            clearTimers();
            openTimer = setTimeout(function () { open(host); }, OPEN_DELAY);
        });

        document.addEventListener("mouseout", function (e) {
            var host = e.target.closest(".req-bubble");
            if (!host) return;
            // moving WITHIN the same bubble (badge -> panel) is not a leave
            if (e.relatedTarget && e.relatedTarget.closest &&
                e.relatedTarget.closest(".req-bubble") === host) return;
            clearTimers();
            closeTimer = setTimeout(function () { close(host); }, CLOSE_GRACE);
        });
    }

    // Click pins: a pinned panel survives mouse-out, so its text can be read
    // and copied without the pointer having to stay inside it. The badge does
    // NOT carry data-bs-toggle — Bootstrap's own toggle would CLOSE a panel
    // hover had already opened, so clicking a preview would dismiss the thing
    // the user just reached for. JS owns show/hide so click always means
    // "keep this open".
    function togglePin(host) {
        var panel = panelOf(host);
        if (!panel) return;
        clearTimers();
        if (panel.classList.contains("show")) {
            if (panel.dataset.pinned === "1") { delete panel.dataset.pinned; instance(panel).hide(); }
            else { panel.dataset.pinned = "1"; }      // hover preview -> pinned
        } else {
            panel.dataset.pinned = "1";
            instance(panel).show();
        }
    }

    document.addEventListener("click", function (e) {
        var host = e.target.closest(".req-bubble");
        if (!host) { closeOpenBubbles(null); return; }
        if (!e.target.closest(".req-bubble-badge")) return;  // clicks inside the panel select text
        e.preventDefault();
        togglePin(host);
    });

    // Keyboard: focus previews, Enter/Space pins, Escape closes everything.
    document.addEventListener("focusin", function (e) {
        var host = e.target.closest && e.target.closest(".req-bubble");
        if (host) { clearTimers(); open(host); }
    });

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { clearTimers(); closeOpenBubbles(null); return; }
        if (e.key !== "Enter" && e.key !== " ") return;
        var badge = e.target.closest && e.target.closest(".req-bubble-badge");
        if (!badge) return;
        e.preventDefault();
        togglePin(badge.closest(".req-bubble"));
    });

    // Bootstrap only syncs aria-expanded for panels IT toggled; we drive the
    // panel ourselves, so the badge state (and the caret that keys off it)
    // has to be kept in step by hand.
    function syncBadge(panel, expanded) {
        var host = panel.parentElement;
        if (!host || !host.classList.contains("req-bubble")) return;
        var badge = host.querySelector(".req-bubble-badge");
        if (badge) badge.setAttribute("aria-expanded", expanded ? "true" : "false");
    }
    document.addEventListener("shown.bs.collapse", function (e) { syncBadge(e.target, true); });
    document.addEventListener("hidden.bs.collapse", function (e) { syncBadge(e.target, false); });
})();
