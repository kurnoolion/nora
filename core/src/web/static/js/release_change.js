/* Release-change answers: views and export (strand rel-history).
 *
 * The answer arrives as one `.rel-change` block (routes/history_lane.py,
 * `answer_markdown`): a head, a `.rc-counts` table, the plans as
 * `details.rc-plan` each holding one `.rc-sec` per kind, then the absent
 * plans. This script adds a toolbar with a view switch (Table / Tree /
 * Tree, expanded) and an Export menu (Raw Markdown, Text, single-page HTML,
 * PDF) with a choice of kinds to include. The content exists once in the
 * page: the Table view moves each plan's sections into a tabbed panel row
 * under its table row, the tree views move them back into the plan's
 * <details>. Without the script the tree view stands alone.
 *
 * Runs on every surface the answer appears on (the live stream's answer,
 * the History page's detail pane, the shared page) through a
 * MutationObserver, so no template has to call it. Answers stored before
 * the `.rel-change` block existed have no root to find and are left alone.
 */
(function () {
    "use strict";

    var TABS = [["changed", "Changed"], ["added", "Added"], ["removed", "Removed"],
                ["structural", "Structural"], ["bookkeeping", "Bookkeeping"]];
    var KINDS = TABS.concat([["absent", "Plans in one release only"]]);
    var COLS = ["changed", "added", "removed", "renumbered", "moved", "bookkeeping"];
    var FORMATS = [
        ["raw", "Raw (Markdown)", "Everything, organised by plan and kind; made for pasting into an LLM."],
        ["text", "Text", "The current view as plain text."],
        ["html", "Single-page HTML", "The current view, self-contained, no scripts."],
        ["pdf", "PDF", "The current view through the browser's print dialog."]];

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function btn(cls, text) { var b = el("button", cls, text); b.type = "button"; return b; }
    function list(nodes) { return Array.prototype.slice.call(nodes || []); }

    // Text of a node with each req-id bubble reduced to its id.
    function textOf(node) {
        if (node.nodeType === 3) return node.nodeValue;
        if (node.nodeType !== 1) return "";
        if (node.classList.contains("req-bubble")) {
            var badge = node.querySelector(".req-bubble-badge");
            return (badge || node).textContent.trim();
        }
        if (node.tagName === "BR") return "\n";
        var s = "";
        for (var i = 0; i < node.childNodes.length; i++) s += textOf(node.childNodes[i]);
        return s;
    }
    function clean(s) { return s.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim(); }
    function escapeHtml(s) {
        return s.replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;"}[c]; });
    }
    function countIn(row, col) {
        var td = row.children[COLS.indexOf(col) + 1];
        return td ? (parseInt(td.textContent, 10) || 0) : 0;
    }
    function tabCount(row, tab) {
        return tab === "structural" ? countIn(row, "renumbered") + countIn(row, "moved") : countIn(row, tab);
    }
    function planTitle(p) {
        var name = p.row && p.row.querySelector(".rc-plan-name");
        if (name) return name.textContent.trim();
        var sm = p.details.querySelector(":scope > summary");
        return sm ? sm.textContent.replace(/\s*·.*$/, "").trim() : p.id;
    }

    // Markdown or text lines for one `.rc-sec`. `all` includes the collapsed
    // id lists; otherwise a closed list is named but its ids are not shown.
    function serialize(sec, fmt, all, indent) {
        var out = [], pad = indent || "", md = fmt === "md";
        function line(depth, text) { return md ? text : pad + (depth ? "    " : "") + text; }
        function walk(children, depth) {
            list(children).forEach(function (c) {
                var tag = c.tagName;
                if (tag === "P") {
                    var strong = c.firstElementChild;
                    var lead = depth === 0 && c.children.length === 1 && strong.tagName === "STRONG"
                        && c.textContent.trim() === strong.textContent.trim();
                    var t = clean(textOf(c));
                    out.push(lead && md ? "### " + t : line(depth, t), "");
                } else if (tag === "UL" || tag === "OL") {
                    list(c.children).forEach(function (li) { out.push(line(depth, "- " + clean(textOf(li)))); });
                    out.push("");
                } else if (tag === "DETAILS") {
                    var sm = c.querySelector(":scope > summary");
                    var st = sm ? clean(textOf(sm)).replace(/\s*·\s*show$/, "") : "";
                    if (all || c.open) {
                        if (st) out.push(line(depth, st));
                        walk(list(c.children).filter(function (x) { return x.tagName !== "SUMMARY"; }), depth + 1);
                    } else {
                        out.push(line(depth, st + " (ids not shown)"), "");
                    }
                } else if (tag === "DIV") {
                    walk(c.children, depth + 1);
                } else if (tag !== "SUMMARY") {
                    out.push(line(depth, clean(textOf(c))), "");
                }
            });
        }
        walk(sec.children, 0);
        return out;
    }

    function download(name, content, type) {
        var blob = new Blob([content], {type: type + ";charset=utf-8"});
        var a = el("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    }
    function printHtml(html) {
        var w = window.open("", "_blank");
        if (!w) return false;
        w.document.open();
        w.document.write(html);
        w.document.close();
        w.focus();
        setTimeout(function () { try { w.print(); } catch (e) { /* the window was closed */ } }, 300);
        return true;
    }

    var EXPORT_CSS =
        "body{font:15px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:#1f2328;background:#fff;max-width:920px;margin:24px auto;padding:0 16px}" +
        ".rc-sec>p:first-child{margin:0 0 4px}.rc-body{margin:0 0 14px 6px;padding-left:16px;border-left:2px solid #c9cfc8}.rc-body p,.rc-body li{margin:0 0 8px}" +
        "code.rc-id{background:#eef1f3;border-radius:10px;padding:0 6px;font-size:.85em}" +
        "table{border-collapse:collapse;width:100%}th,td{padding:6px 10px;border-bottom:1px solid #d9dcd6;text-align:right}th:first-child,td:first-child{text-align:left}" +
        "th{font-size:.78rem;text-transform:uppercase;color:#6b7280}td.rc-plan-name{font-weight:600}" +
        ".rc-panel-box{border:1px solid #d9dcd6;border-radius:8px;margin:4px 0 14px;padding:8px 16px 4px}.rc-showing{color:#6b7280;font-size:.85em;margin:0 0 8px}" +
        "details.rc-plan{margin:0 0 8px}details>summary{font-weight:600;cursor:pointer}details.rc-ids>summary{font-weight:400;color:#6b7280;font-size:.9em}" +
        "@media print{details>summary{list-style:none}}";

    function Answer(root) {
        this.root = root;
        this.table = root.querySelector("table.rc-counts");
        this.tree = root.querySelector(".rc-tree");
        this.head = root.querySelector(".rc-head");
        this.plans = [];
        var self = this;
        list(root.querySelectorAll(".rc-tree > details.rc-plan")).forEach(function (d) {
            self.plans.push({
                id: d.dataset.plan, details: d, row: null, panel: null, panes: {}, tab: null,
                secs: list(d.children).filter(function (c) { return c.classList.contains("rc-sec"); })
            });
        });
        if (this.table) {
            list(this.table.querySelectorAll("tr.rc-row")).forEach(function (row) {
                var p = self.plan(row.dataset.plan);
                if (p) { p.row = row; self.buildPanel(p); }
            });
        }
        this.buildToolbar();
        this.wire();
        this.setView(this.table && this.plans.length ? "table" : "tree");
    }

    Answer.prototype.plan = function (id) {
        for (var i = 0; i < this.plans.length; i++) if (this.plans[i].id === id) return this.plans[i];
        return null;
    };
    Answer.prototype.sec = function (p, kind) {
        for (var i = 0; i < p.secs.length; i++) if (p.secs[i].dataset.kind === kind) return p.secs[i];
        return null;
    };
    Answer.prototype.hasKind = function (kind) {
        return !!this.root.querySelector('.rc-sec[data-kind="' + kind + '"]');
    };
    Answer.prototype.firstTab = function (p) {
        for (var k in p.panes) return k;
        return p.secs.length ? p.secs[0].dataset.kind : null;
    };

    // A hidden row under the plan's table row: tabs, one pane per kind.
    Answer.prototype.buildPanel = function (p) {
        var self = this;
        var tr = el("tr", "rc-panel"), td = el("td"), box = el("div", "rc-panel-box"), tabs = el("div", "rc-tabs");
        tr.hidden = true;
        tr.dataset.plan = p.id;
        td.colSpan = p.row.children.length;
        tabs.setAttribute("role", "tablist");
        TABS.forEach(function (t) {
            if (!self.sec(p, t[0])) return;
            var b = btn("rc-tab", t[1]);
            b.dataset.tab = t[0];
            b.setAttribute("role", "tab");
            b.setAttribute("aria-selected", "false");
            var n = tabCount(p.row, t[0]);
            if (n) b.appendChild(el("span", "rc-n", String(n)));
            tabs.appendChild(b);
            var pane = el("div", "rc-pane");
            pane.dataset.tab = t[0];
            pane.hidden = true;
            p.panes[t[0]] = pane;
        });
        var close = btn("rc-close", "Collapse ▴");
        close.dataset.close = "1";
        tabs.appendChild(close);
        box.appendChild(tabs);
        for (var k in p.panes) box.appendChild(p.panes[k]);
        td.appendChild(box);
        tr.appendChild(td);
        p.row.insertAdjacentElement("afterend", tr);
        p.panel = tr;
        var name = p.row.querySelector(".rc-plan-name");
        if (name) {
            var toggle = btn("rc-plan-toggle", name.textContent.trim());
            name.textContent = "";
            name.appendChild(toggle);
        }
        // hover summaries through Bootstrap when it is loaded; the title attribute stands otherwise
        if (window.bootstrap && window.bootstrap.Tooltip) {
            list(p.row.querySelectorAll(".rc-count[title]")).forEach(function (c) {
                new window.bootstrap.Tooltip(c, {container: "body", placement: "top", trigger: "hover focus"});
            });
        }
    };

    Answer.prototype.buildToolbar = function () {
        var self = this;
        var bar = el("div", "rc-toolbar"), views = el("div", "rc-views"), seg = el("div", "rc-seg");
        views.appendChild(el("span", "rc-label", "View"));
        [["table", "Table"], ["tree", "Tree"], ["expanded", "Tree, expanded"]].forEach(function (v) {
            if (v[0] === "table" && !(self.table && self.plans.length)) return;
            var b = btn("rc-view", v[1]);
            b.dataset.view = v[0];
            b.setAttribute("aria-pressed", "false");
            seg.appendChild(b);
        });
        views.appendChild(seg);
        bar.appendChild(views);
        var menu = el("div", "rc-menu"), mb = btn("rc-export", "Export ▾"), ul = el("ul", "rc-menu-list");
        mb.setAttribute("aria-haspopup", "true");
        mb.setAttribute("aria-expanded", "false");
        ul.hidden = true;
        FORMATS.forEach(function (f) {
            var li = el("li"), b = btn("rc-menu-item", f[1]);
            b.dataset.format = f[0];
            b.appendChild(el("small", null, f[2]));
            li.appendChild(b);
            ul.appendChild(li);
        });
        menu.appendChild(mb);
        menu.appendChild(ul);
        bar.appendChild(menu);
        this.root.insertBefore(bar, this.root.firstChild);
        this.bar = bar;
        this.menuBtn = mb;
        this.menuList = ul;
    };
    Answer.prototype.closeMenu = function () {
        this.menuList.hidden = true;
        this.menuBtn.setAttribute("aria-expanded", "false");
    };

    // Table: sections sit in the panel panes. Tree / expanded: back in the plan's <details>.
    Answer.prototype.setView = function (v) {
        var self = this;
        this.view = v;
        this.plans.forEach(function (p) {
            if (v === "table") {
                p.secs.forEach(function (s) { var pane = p.panes[s.dataset.kind]; if (pane) pane.appendChild(s); });
                self.showTab(p, p.tab);
            } else {
                p.secs.forEach(function (s) { p.details.appendChild(s); });
                if (p.panel) p.panel.hidden = true;
                p.details.open = v === "expanded" || !!p.tab;
                list(p.details.querySelectorAll("details.rc-ids")).forEach(function (d) { d.open = v === "expanded"; });
            }
        });
        if (this.table) this.table.hidden = v !== "table";
        if (this.tree) this.tree.hidden = v === "table";
        list(this.bar.querySelectorAll(".rc-view")).forEach(function (b) {
            b.setAttribute("aria-pressed", String(b.dataset.view === v));
        });
    };
    Answer.prototype.showTab = function (p, tab) {
        if (!p.panel) return;
        if (tab && !p.panes[tab]) tab = this.firstTab(p);
        p.tab = tab;
        p.panel.hidden = !tab;
        p.row.classList.toggle("open", !!tab);
        for (var k in p.panes) p.panes[k].hidden = k !== tab;
        list(p.panel.querySelectorAll(".rc-tab")).forEach(function (b) {
            b.setAttribute("aria-selected", String(b.dataset.tab === tab));
        });
        list(p.row.querySelectorAll(".rc-count")).forEach(function (c) {
            c.classList.toggle("active", !!tab && c.dataset.tab === tab);
        });
    };

    Answer.prototype.wire = function () {
        var self = this;
        this.root.addEventListener("click", function (e) {
            var t = e.target.closest("button");
            if (!t) return;
            if (t.classList.contains("rc-view")) { self.setView(t.dataset.view); return; }
            if (t.classList.contains("rc-export")) {
                self.menuList.hidden = !self.menuList.hidden;
                t.setAttribute("aria-expanded", String(!self.menuList.hidden));
                return;
            }
            if (t.classList.contains("rc-menu-item")) { self.closeMenu(); self.openExport(t.dataset.format); return; }
            var row = t.closest("tr.rc-row"), panel = t.closest("tr.rc-panel");
            if (row) {
                var p = self.plan(row.dataset.plan);
                if (!p) return;
                if (t.classList.contains("rc-count")) {
                    var tip = window.bootstrap && window.bootstrap.Tooltip && window.bootstrap.Tooltip.getInstance(t);
                    if (tip) tip.hide();
                    self.showTab(p, p.tab === t.dataset.tab ? null : t.dataset.tab);
                } else if (t.classList.contains("rc-plan-toggle")) {
                    self.showTab(p, p.tab ? null : self.firstTab(p));
                }
            } else if (panel) {
                var pp = self.plan(panel.dataset.plan);
                if (!pp) return;
                if (t.classList.contains("rc-tab")) self.showTab(pp, t.dataset.tab);
                else if (t.dataset.close) self.showTab(pp, null);
            }
        });
        document.addEventListener("click", function (e) {
            if (!self.menuList.hidden && !e.target.closest(".rc-menu")) self.closeMenu();
        });
        // `toggle` does not bubble; a capturing listener on the root still sees it.
        this.root.addEventListener("toggle", function (e) {
            var d = e.target;
            if (!d.classList || !d.classList.contains("rc-plan")) return;
            var p = self.plan(d.dataset.plan);
            if (!p) return;
            if (d.open) { if (!p.tab) p.tab = self.firstTab(p); } else { p.tab = null; }
        }, true);
    };

    // Which of a plan's sections are on screen; `ids` says whether closed id lists count as shown.
    Answer.prototype.visible = function (p) {
        var kinds = p.secs.map(function (s) { return s.dataset.kind; });
        if (this.view === "expanded") return {kinds: kinds, ids: true};
        if (this.view === "tree") return {kinds: p.details.open ? kinds : [], ids: false};
        return {kinds: p.tab ? [p.tab] : [], ids: false};
    };
    Answer.prototype.fileName = function (ext) {
        var d = this.root.dataset;
        var safe = function (s) { return (s || "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, ""); };
        return ["release-changes", safe(d.mno), safe(d.from), safe(d.to)].filter(Boolean).join("_") + "." + ext;
    };
    Answer.prototype.heading = function () {
        var d = this.root.dataset;
        return [d.mno, (d.from || d.to) ? d.from + " → " + d.to : ""].filter(Boolean).join(", ");
    };
    Answer.prototype.absent = function () { return this.root.querySelector('.rc-sec[data-kind="absent"]'); };

    // Everything, whatever the view: a file made for another LLM to read.
    Answer.prototype.rawMarkdown = function (inc) {
        var L = ["# Release changes: " + this.heading(), "",
                 "Source: NORA release-change answer. Each plan lists its requirements by kind of change; " +
                 "ids in [brackets] are requirement ids; \"~same\" marks a pair the model judged unchanged in meaning on this run.", "",
                 "## Summary", ""];
        if (this.head) list(this.head.querySelectorAll("p")).forEach(function (p) { L.push(clean(textOf(p)), ""); });
        if (this.table) {
            var hs = list(this.table.querySelectorAll("thead th")).map(function (th) { return th.textContent.trim(); });
            L.push("| " + hs.join(" | ") + " |", "|" + hs.map(function (_, i) { return i ? "---:|" : "---|"; }).join(""));
            list(this.table.querySelectorAll("tr.rc-row")).forEach(function (r) {
                L.push("| " + list(r.children).map(function (td) { return td.textContent.trim(); }).join(" | ") + " |");
            });
            L.push("");
        }
        this.plans.forEach(function (p) {
            var secs = p.secs.filter(function (s) { return inc(s.dataset.kind); });
            if (!secs.length) return;
            L.push("## " + planTitle(p), "");
            secs.forEach(function (s) { L.push.apply(L, serialize(s, "md", true)); });
        });
        var ab = this.absent();
        if (ab && inc("absent")) L.push.apply(L, serialize(ab, "md", true).map(function (l) { return l.replace(/^### /, "## "); }));
        return clean(L.join("\n")) + "\n";
    };

    // The current view as text: the table in Table view, the open plans' sections, the rest named only.
    Answer.prototype.plainText = function (inc) {
        var self = this, L = [this.heading(), ""];
        if (this.head) list(this.head.querySelectorAll("p")).forEach(function (p) { L.push(clean(textOf(p)), ""); });
        if (this.view === "table" && this.table) {
            var rows = list(this.table.querySelectorAll("thead tr, tr.rc-row")).map(function (r) {
                return list(r.children).map(function (c) { return c.textContent.trim(); });
            });
            var w = rows.reduce(function (m, r) { return Math.max(m, r[0].length); }, 0);
            rows.forEach(function (r) {
                L.push(r[0] + new Array(w - r[0].length + 3).join(" ") + r.slice(1).map(function (c) {
                    return new Array(Math.max(0, 17 - c.length) + 1).join(" ") + c; }).join(""));
            });
            L.push("");
        }
        this.plans.forEach(function (p) {
            var v = self.visible(p), kinds = v.kinds.filter(inc);
            var sm = p.details.querySelector(":scope > summary");
            if (self.view !== "table") L.push((v.kinds.length ? "▾ " : "▸ ") + (sm ? clean(textOf(sm)) : planTitle(p)));
            else if (kinds.length) L.push(planTitle(p));
            p.secs.forEach(function (s) {
                if (kinds.indexOf(s.dataset.kind) >= 0) L.push.apply(L, serialize(s, "txt", v.ids, "    "));
            });
            if (kinds.length || self.view !== "table") L.push("");
        });
        var ab = this.absent();
        if (ab && inc("absent")) L.push.apply(L, serialize(ab, "txt", true, ""));
        return clean(L.join("\n")) + "\n";
    };

    // The current view as one HTML page: styles inline, no scripts, bubbles as plain ids.
    Answer.prototype.pageHtml = function (inc) {
        var root = this.root.cloneNode(true);
        root.querySelector(".rc-toolbar").remove();
        list(root.querySelectorAll(".req-bubble")).forEach(function (b) { b.replaceWith(el("code", "rc-id", textOf(b))); });
        list(root.querySelectorAll(".rc-sec")).forEach(function (s) { if (!inc(s.dataset.kind)) s.remove(); });
        list(root.querySelectorAll(".rc-count, .rc-plan-toggle")).forEach(function (b) { b.replaceWith(el("span", null, b.textContent.trim())); });
        if (this.view === "table") {
            root.querySelector(".rc-tree").remove();
            list(root.querySelectorAll("tr.rc-panel")).forEach(function (tr) {
                var pane = tr.querySelector(".rc-pane:not([hidden])");
                if (tr.hidden || !pane || !pane.querySelector(".rc-sec")) { tr.remove(); return; }
                var tabs = tr.querySelector(".rc-tabs"), active = tabs.querySelector('.rc-tab[aria-selected="true"]');
                tabs.replaceWith(el("p", "rc-showing", "Showing: " + (active ? active.firstChild.textContent.trim() : "")));
                list(tr.querySelectorAll(".rc-pane[hidden]")).forEach(function (x) { x.remove(); });
                tr.removeAttribute("hidden");
                pane.removeAttribute("hidden");
            });
        } else {
            var table = root.querySelector("table.rc-counts");
            if (table) table.remove();
            root.querySelector(".rc-tree").removeAttribute("hidden");
            list(root.querySelectorAll("details.rc-plan")).forEach(function (d) {
                if (!d.open) list(d.querySelectorAll(".rc-sec")).forEach(function (s) { s.remove(); });
            });
        }
        list(root.querySelectorAll("[hidden]")).forEach(function (x) { x.remove(); });
        var drop = ["hx-get", "hx-target", "hx-trigger", "hx-swap", "data-rc-ready", "title",
                    "data-bs-original-title", "data-bs-toggle", "aria-describedby"];
        list(root.querySelectorAll("*")).concat([root]).forEach(function (x) { drop.forEach(function (a) { x.removeAttribute(a); }); });
        var title = "Release changes: " + this.heading();
        return "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">" +
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>" + escapeHtml(title) + "</title>" +
            "<style>" + EXPORT_CSS + "</style></head>\n<body>\n" + root.innerHTML + "\n</body></html>\n";
    };

    // The export sheet: which kinds to include (all by default), then download or print.
    Answer.prototype.openExport = function (format) {
        var self = this, label = {raw: "Raw (Markdown)", text: "Text", html: "Single-page HTML", pdf: "PDF"}[format];
        var ov = el("div", "rc-overlay"), sheet = el("div", "rc-sheet");
        sheet.setAttribute("role", "dialog");
        sheet.setAttribute("aria-label", "Export " + label);
        sheet.appendChild(el("h3", null, "Export · " + label));
        sheet.appendChild(el("p", "rc-sheet-note",
            format === "raw" ? "Everything, whatever the view, organised by plan and kind. Paste it into an LLM for further analysis."
            : format === "pdf" ? "What is on screen now, through the browser's print dialog; choose Save as PDF there."
            : "What is on screen now: in Table view the table and any open panel, in Tree view the open plans, in Tree, expanded everything."));
        var picks = el("div", "rc-picks");
        picks.appendChild(el("span", "rc-label", "Include"));
        KINDS.forEach(function (k) {
            if (!self.hasKind(k[0])) return;
            var lab = el("label"), cb = el("input");
            cb.type = "checkbox";
            cb.checked = true;
            cb.dataset.kind = k[0];
            lab.appendChild(cb);
            lab.appendChild(document.createTextNode(" " + k[1]));
            picks.appendChild(lab);
        });
        sheet.appendChild(picks);
        var msg = el("p", "rc-sheet-msg");
        msg.hidden = true;
        sheet.appendChild(msg);
        var actions = el("div", "rc-sheet-actions");
        var go = btn("btn btn-sm btn-primary", format === "pdf" ? "Print" : "Download");
        var cancel = btn("btn btn-sm btn-outline-secondary", "Cancel");
        actions.appendChild(cancel);
        actions.appendChild(go);
        sheet.appendChild(actions);
        ov.appendChild(sheet);
        document.body.appendChild(ov);
        function close() { ov.remove(); document.removeEventListener("keydown", onKey); }
        function onKey(e) { if (e.key === "Escape") close(); }
        document.addEventListener("keydown", onKey);
        cancel.addEventListener("click", close);
        ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
        go.addEventListener("click", function () {
            var sel = {};
            list(picks.querySelectorAll("input")).forEach(function (cb) { sel[cb.dataset.kind] = cb.checked; });
            var inc = function (k) { return sel[k] !== false; };
            if (format === "raw") download(self.fileName("md"), self.rawMarkdown(inc), "text/markdown");
            else if (format === "text") download(self.fileName("txt"), self.plainText(inc), "text/plain");
            else if (format === "html") download(self.fileName("html"), self.pageHtml(inc), "text/html");
            else if (!printHtml(self.pageHtml(inc))) {
                msg.textContent = "The browser blocked the print window. Allow pop-ups for this page and try again.";
                msg.hidden = false;
                return;
            }
            close();
        });
        go.focus();
    };

    function scan() {
        list(document.querySelectorAll(".rel-change:not([data-rc-ready])")).forEach(function (r) {
            r.dataset.rcReady = "1";
            try { new Answer(r); } catch (e) { if (window.console) console.error("release_change:", e); }
        });
    }
    function start() {
        scan();
        new MutationObserver(function (muts) {
            for (var i = 0; i < muts.length; i++) if (muts[i].addedNodes.length) { scan(); return; }
        }).observe(document.body, {childList: true, subtree: true});
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
