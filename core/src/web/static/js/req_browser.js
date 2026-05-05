/**
 * req_browser.js — requirement browser interactivity.
 */
(function () {
    'use strict';

    let _docId = null;
    let _root  = '';
    let _prevReq = null;  // {docId, reqId} for Back navigation from compare

    window.initReqBrowser = function (docId, rootPath) {
        _docId = docId;
        _root  = rootPath || '';
    };

    /* -----------------------------------------------------------------------
     * Tree toggle (expand / collapse children)
     * --------------------------------------------------------------------- */
    window.rbToggle = function (toggleEl) {
        const node     = toggleEl.closest('.tree-node');
        const children = node && node.querySelector(':scope > .tree-children');
        if (!children) return;
        const open = children.style.display === 'block';
        children.style.display = open ? 'none' : 'block';
        toggleEl.textContent   = open ? '▶' : '▼';
    };

    /* -----------------------------------------------------------------------
     * Select requirement from tree click (row element passed)
     * --------------------------------------------------------------------- */
    window.rbSelectReq = function (reqId, rowEl) {
        document.querySelectorAll('.tree-row.active').forEach(function (el) {
            el.classList.remove('active');
        });
        if (rowEl) rowEl.classList.add('active');
        _loadDetail(_docId, reqId);
    };

    /* -----------------------------------------------------------------------
     * Select requirement by ID (from cross-ref "View" buttons)
     * Finds the tree row and highlights it, then loads detail.
     * --------------------------------------------------------------------- */
    window.rbSelectReqById = function (reqId) {
        const rowEl = document.getElementById('tree-row-' + reqId);
        if (rowEl) {
            // Expand all ancestor tree-children panels so the row is visible
            let parent = rowEl.parentElement;
            while (parent) {
                if (parent.classList.contains('tree-children')) {
                    parent.style.display = 'block';
                    // flip the sibling toggle arrow
                    const sibRow = parent.previousElementSibling;
                    if (sibRow) {
                        const arrow = sibRow.querySelector('.tree-toggle');
                        if (arrow) arrow.textContent = '▼';
                    }
                }
                parent = parent.parentElement;
            }
            rowEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            window.rbSelectReq(reqId, rowEl);
        } else {
            // Row not in current tree — just load the detail without highlighting
            _loadDetail(_docId, reqId);
        }
    };

    /* -----------------------------------------------------------------------
     * Load requirement detail into the right panel
     * --------------------------------------------------------------------- */
    function _loadDetail(docId, reqId) {
        _prevReq = { docId: _docId, reqId: reqId };
        const panel = document.getElementById('detail-panel');
        if (!panel) return;
        panel.innerHTML = '<div class="text-center py-4 text-muted"><span class="spinner-border spinner-border-sm me-2"></span>Loading…</div>';
        fetch(_root + '/req-browser/' + encodeURIComponent(docId) + '/req/' + encodeURIComponent(reqId))
            .then(function (r) { return r.text(); })
            .then(function (html) { panel.innerHTML = html; })
            .catch(function (err) {
                panel.innerHTML = '<div class="alert alert-danger small">Failed to load: ' + err + '</div>';
            });
    }

    /* -----------------------------------------------------------------------
     * Open cross-doc comparison panel
     * --------------------------------------------------------------------- */
    window.rbOpenCompare = function (aDoc, aReq, bDoc, bReq) {
        const panel = document.getElementById('detail-panel');
        if (!panel) return;
        _prevReq = { docId: aDoc, reqId: aReq };
        panel.innerHTML = '<div class="text-center py-4 text-muted"><span class="spinner-border spinner-border-sm me-2"></span>Loading comparison…</div>';
        const url = _root + '/req-browser/compare' +
            '?a_doc=' + encodeURIComponent(aDoc) +
            '&a_req=' + encodeURIComponent(aReq) +
            '&b_doc=' + encodeURIComponent(bDoc) +
            '&b_req=' + encodeURIComponent(bReq);
        fetch(url)
            .then(function (r) { return r.text(); })
            .then(function (html) { panel.innerHTML = html; })
            .catch(function (err) {
                panel.innerHTML = '<div class="alert alert-danger small">Comparison failed: ' + err + '</div>';
            });
    };

    /* -----------------------------------------------------------------------
     * Back from compare to the source requirement detail
     * --------------------------------------------------------------------- */
    window.rbBackToReq = function () {
        if (_prevReq) {
            window.rbSelectReqById(_prevReq.reqId);
        }
    };

}());
