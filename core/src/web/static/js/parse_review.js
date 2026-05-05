/**
 * parse_review.js — 3-pane parse review interactivity.
 *
 * Exposes:
 *   window.initParseReview(docId, rootPath)  called after _view.html is injected
 *   prAccept(idx), prReject(idx), prShowAddMenu(event, idx)  — onclick targets
 *   prSave(), prReport(), prAddMissedAcr()   — toolbar buttons
 */
(function () {
    'use strict';

    /* -----------------------------------------------------------------------
     * Module-level state (reset on each initParseReview call)
     * --------------------------------------------------------------------- */
    let _docId   = null;
    let _root    = '';
    // blockIdx -> 'accepted' | 'rejected' | 'added'
    let _status  = {};
    // idx -> {reason: string}  for user-added annotations
    let _added   = {};

    /* -----------------------------------------------------------------------
     * Init — called once _view.html is in the DOM
     * --------------------------------------------------------------------- */
    window.initParseReview = function (docId, rootPath) {
        _docId  = docId;
        _root   = rootPath || '';
        _status = {};
        _added  = {};

        // Restore any existing review state from the serialised review JSON
        const existing = window._prReview || {};
        _restoreFromReview(existing);

        _bindSyncScroll();
        _bindContextMenu();
        _renderStats();
    };

    /* -----------------------------------------------------------------------
     * Restore status from previously saved corrections
     * --------------------------------------------------------------------- */
    function _restoreFromReview(review) {
        const corr = (review.corrections) || {};

        // False-positive drops were rejected
        for (const fp of (corr.false_positive_drops || [])) {
            const idx = _findBlockIdxByPage(fp.pages);
            if (idx !== null) _setStatus(idx, 'rejected');
        }

        // User-added drops
        for (const md of (corr.missed_drops || [])) {
            const idx = _findBlockIdxByPage(md.pages);
            if (idx !== null) {
                _added[idx] = { reason: md.expected_reason };
                _setStatus(idx, 'added');
            }
        }
    }

    /** Find first block on a given page string ("5" or "5-7"). Returns idx or null. */
    function _findBlockIdxByPage(pageStr) {
        if (!pageStr) return null;
        const startPage = parseInt(String(pageStr).split('-')[0], 10);
        const el = document.querySelector(`#pane-3-body [data-page="${startPage}"]`);
        return el ? parseInt(el.dataset.idx, 10) : null;
    }

    /* -----------------------------------------------------------------------
     * Accept / Reject / Add
     * --------------------------------------------------------------------- */
    window.prAccept = function (idx) {
        _setStatus(idx, 'accepted');
        _renderStats();
    };

    window.prReject = function (idx) {
        _setStatus(idx, 'rejected');
        _renderStats();
    };

    function _setStatus(idx, status) {
        _status[idx] = status;
        const el = document.getElementById('rv-' + idx);
        if (!el) return;
        el.classList.remove('rev-accepted', 'rev-rejected', 'rev-added');
        if (status === 'accepted') el.classList.add('rev-accepted');
        if (status === 'rejected') el.classList.add('rev-rejected');
        if (status === 'added')    el.classList.add('rev-added');
    }

    /* -----------------------------------------------------------------------
     * Context menu — right-click to add annotation on any pane-3 block
     * --------------------------------------------------------------------- */
    let _ctxTargetIdx = null;

    window.prShowAddMenu = function (event, idx) {
        event.preventDefault();
        event.stopPropagation();
        _ctxTargetIdx = idx;
        _showCtxMenu(event.clientX, event.clientY);
    };

    function _bindContextMenu() {
        const body = document.getElementById('pane-3-body');
        if (!body) return;

        body.addEventListener('contextmenu', function (e) {
            e.preventDefault();
            // Find the closest review-block
            const blk = e.target.closest('.review-block');
            if (!blk) return;
            _ctxTargetIdx = parseInt(blk.dataset.idx, 10);
            _showCtxMenu(e.clientX, e.clientY);
        });

        // Close on outside click
        document.addEventListener('click', _hideCtxMenu, true);
    }

    function _showCtxMenu(x, y) {
        const menu = document.getElementById('pr-ctx-menu');
        if (!menu) return;
        menu.classList.remove('d-none');
        // Clamp to viewport
        const vw = window.innerWidth, vh = window.innerHeight;
        const mw = 220, mh = 180;
        menu.style.left = Math.min(x, vw - mw) + 'px';
        menu.style.top  = Math.min(y, vh - mh) + 'px';

        // Wire items (remove old listeners first by replacing nodes)
        menu.querySelectorAll('.ctx-menu-item').forEach(function (item) {
            const clone = item.cloneNode(true);
            item.parentNode.replaceChild(clone, item);
            clone.addEventListener('click', function () {
                const reason = clone.dataset.reason;
                _hideCtxMenu();
                if (reason && reason !== '__cancel') {
                    _applyAddedAnnotation(_ctxTargetIdx, reason);
                }
            });
        });
    }

    function _hideCtxMenu() {
        const menu = document.getElementById('pr-ctx-menu');
        if (menu) menu.classList.add('d-none');
    }

    function _applyAddedAnnotation(idx, reason) {
        if (idx === null || idx === undefined) return;
        _added[idx] = { reason };
        _setStatus(idx, 'added');

        // Insert a small badge into the block if it doesn't already show one
        const el = document.getElementById('rv-' + idx);
        if (el) {
            const existing = el.querySelector('.ann-badge-added-' + reason);
            if (!existing) {
                const badge = document.createElement('span');
                badge.className = 'ann-badge me-1';
                badge.style.background = '#f59e0b';
                badge.style.color = '#fff';
                badge.textContent = '+' + reason.replace('_', '-').toUpperCase();
                // Insert before the block content (after page badge and existing badges)
                const ref = el.querySelector('p, .table-responsive, span.text-muted');
                el.insertBefore(badge, ref || null);
            }
        }
        _renderStats();
    }

    /* -----------------------------------------------------------------------
     * Sync scroll
     * --------------------------------------------------------------------- */
    function _bindSyncScroll() {
        const toggle = document.getElementById('sync-scroll-toggle');
        if (!toggle) return;

        const bodies = [
            document.getElementById('pane-1-body'),
            document.getElementById('pane-2-body'),
            document.getElementById('pane-3-body'),
        ].filter(Boolean);

        let syncing = false;

        bodies.forEach(function (src) {
            src.addEventListener('scroll', function () {
                if (!toggle.checked || syncing) return;
                syncing = true;
                const ratio = src.scrollTop / Math.max(1, src.scrollHeight - src.clientHeight);
                bodies.forEach(function (tgt) {
                    if (tgt !== src) {
                        tgt.scrollTop = ratio * (tgt.scrollHeight - tgt.clientHeight);
                    }
                });
                syncing = false;
            });
        });
    }

    /* -----------------------------------------------------------------------
     * Stats display
     * --------------------------------------------------------------------- */
    function _renderStats() {
        const el = document.getElementById('rv-stats');
        if (!el) return;
        const accepted = Object.values(_status).filter(s => s === 'accepted').length;
        const rejected = Object.values(_status).filter(s => s === 'rejected').length;
        const added    = Object.values(_status).filter(s => s === 'added').length;
        el.textContent =
            (accepted ? accepted + ' accepted  ' : '') +
            (rejected ? rejected + ' rejected  ' : '') +
            (added    ? added    + ' added'       : '');
    }

    /* -----------------------------------------------------------------------
     * Build corrections JSON from current UI state
     * --------------------------------------------------------------------- */
    function _buildCorrections() {
        const fpDrops = [];
        const missedDrops = [];

        // Walk pane-3 blocks
        document.querySelectorAll('#pane-3-body .review-block').forEach(function (el) {
            const idx  = parseInt(el.dataset.idx, 10);
            const page = el.dataset.page;
            const status = _status[idx];
            const anns = JSON.parse(el.dataset.anns || '[]');

            if (status === 'rejected' && anns.length > 0) {
                // Each annotation on this block is a false positive
                anns.forEach(function (ann) {
                    if (ann.type.startsWith('dropped_')) {
                        fpDrops.push({ pages: page, reason: ann.reason || ann.type.replace('dropped_', '') });
                    }
                });
            }

            if (status === 'added' && _added[idx]) {
                missedDrops.push({
                    pages: page,
                    expected_reason: _added[idx].reason,
                });
            }
        });

        // Acronym corrections
        const acrWrong = [], acrExtra = [], acrMissed = [];
        document.querySelectorAll('.acr-correction').forEach(function (input) {
            const acr      = input.dataset.acronym;
            const original = input.dataset.original;
            const corrected = input.value.trim();
            const statusSel = input.closest('.d-flex')?.querySelector('.acr-status');
            const statusVal = statusSel ? statusSel.value : 'ok';

            if (statusVal === 'wrong' && corrected && corrected !== original) {
                acrWrong.push({ acronym: acr, correct: corrected });
            } else if (statusVal === 'extra') {
                acrExtra.push({ acronym: acr });
            }
        });

        document.querySelectorAll('.missed-acr-row').forEach(function (row) {
            const inputs = row.querySelectorAll('input');
            const acr  = inputs[0]?.value.trim() || '';
            const exp  = inputs[1]?.value.trim() || '';
            if (acr) acrMissed.push({ acronym: acr, expansion: exp });
        });

        return {
            false_positive_drops: fpDrops,
            missed_drops: missedDrops,
            toc_error: null,
            revhist_error: null,
            glossary_error: null,
            acronym_wrong_expansion: acrWrong,
            acronym_missed: acrMissed,
            acronym_extra: acrExtra,
        };
    }

    function _buildReviewPayload() {
        return {
            doc_id:          _docId,
            reviewer:        (document.getElementById('rv-reviewer')?.value || '').trim(),
            review_date:     (document.getElementById('rv-date')?.value     || '').trim(),
            overall_verdict: (document.getElementById('rv-verdict')?.value  || '').trim(),
            corrections:     _buildCorrections(),
            notes:           (document.getElementById('rv-notes')?.value    || '').trim(),
        };
    }

    /* -----------------------------------------------------------------------
     * Save
     * --------------------------------------------------------------------- */
    window.prSave = function () {
        const payload = _buildReviewPayload();
        const indicator = document.getElementById('save-indicator');
        if (indicator) indicator.textContent = 'Saving…';

        fetch(_root + '/parse-review/' + encodeURIComponent(_docId) + '/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        .then(r => r.json())
        .then(function (data) {
            if (indicator) indicator.textContent = data.ok ? '✓ Saved' : '✗ ' + data.error;
            setTimeout(function () {
                if (indicator) indicator.textContent = '';
            }, 3000);
        })
        .catch(function (err) {
            if (indicator) indicator.textContent = '✗ ' + err;
        });
    };

    /* -----------------------------------------------------------------------
     * Report
     * --------------------------------------------------------------------- */
    window.prReport = function () {
        const payload = _buildReviewPayload();
        const out = document.getElementById('report-output');
        if (out) out.innerHTML = '<div class="text-muted small py-1">Generating…</div>';

        fetch(_root + '/parse-review/' + encodeURIComponent(_docId) + '/report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        .then(r => r.text())
        .then(function (html) {
            if (out) out.innerHTML = html;
        })
        .catch(function (err) {
            if (out) out.innerHTML = '<div class="alert alert-danger small">Report failed: ' + err + '</div>';
        });
    };

    /* -----------------------------------------------------------------------
     * Add missed acronym row
     * --------------------------------------------------------------------- */
    window.prAddMissedAcr = function () {
        const container = document.getElementById('missed-acr-rows');
        if (!container) return;
        const row = document.createElement('div');
        row.className = 'd-flex gap-1 mb-1 missed-acr-row';
        row.innerHTML =
            '<input type="text" class="form-control form-control-sm" placeholder="Acronym" style="max-width:90px">' +
            '<input type="text" class="form-control form-control-sm" placeholder="Expansion">' +
            '<button class="btn btn-xs btn-outline-danger" onclick="this.closest(\'.missed-acr-row\').remove()">✕</button>';
        container.appendChild(row);
    };

}());
