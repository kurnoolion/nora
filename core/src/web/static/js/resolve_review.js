/**
 * resolve_review.js — cross-reference resolution review interactivity.
 */
(function () {
    'use strict';

    let _docId = null;
    let _root  = '';

    window.initResolveReview = function (docId, rootPath) {
        _docId = docId;
        _root  = rootPath || '';
        _bindShowAllToggle();
        _applyDefaultFilter();
    };

    /* -----------------------------------------------------------------------
     * Show-all toggle — default: broken/unresolved only
     * --------------------------------------------------------------------- */
    function _applyDefaultFilter() {
        _filterRows(false);
    }

    function _bindShowAllToggle() {
        const toggle = document.getElementById('show-all-toggle');
        if (!toggle) return;
        toggle.addEventListener('change', function () {
            _filterRows(this.checked);
        });
    }

    function _filterRows(showAll) {
        document.querySelectorAll('.ref-row').forEach(function (row) {
            const status = row.dataset.status;
            if (showAll || status !== 'resolved') {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        });
    }

    /* -----------------------------------------------------------------------
     * Expand/collapse source text
     * --------------------------------------------------------------------- */
    window.toggleText = function (id, link) {
        const el = document.getElementById(id);
        if (!el) return;
        const expanded = el.classList.toggle('expanded');
        link.textContent = expanded ? 'less' : 'more';
    };

    /* -----------------------------------------------------------------------
     * Show/hide correction detail panel
     * --------------------------------------------------------------------- */
    window.toggleFbDetail = function (detailId, show) {
        const el = document.getElementById(detailId);
        if (el) el.style.display = show ? 'block' : 'none';
    };

    /* -----------------------------------------------------------------------
     * Build corrections from current UI state
     * --------------------------------------------------------------------- */
    function _buildCorrections() {
        const fbList = [], wtList = [], wpList = [], wsList = [];

        // False broken (internal)
        document.querySelectorAll('.int-fb-check:checked').forEach(function (cb) {
            const target = cb.dataset.target;
            const noteEl = document.querySelector(`.fb-note[data-target="${target}"]`);
            const note   = noteEl ? noteEl.value.trim() : '';
            const entry  = { target_req_id: target };
            if (note) entry.note = note;
            fbList.push(entry);
        });

        // Wrong target (internal resolved)
        document.querySelectorAll('.int-wt-check:checked').forEach(function (cb) {
            const src   = cb.dataset.src;
            const wrong = cb.dataset.wrong;
            const corrEl = document.querySelector(`.wt-correct[data-src="${src}"]`);
            const correct = corrEl ? corrEl.value.trim() : '';
            if (correct) {
                wtList.push({ source_req_id: src, wrong_target: wrong, correct_target: correct });
            }
        });

        // Wrong plan ID (cross-plan)
        document.querySelectorAll('.xp-wp-check:checked').forEach(function (cb) {
            const src   = cb.dataset.src;
            const wrong = cb.dataset.wrong;
            const corrEl = document.querySelector(`.wp-correct[data-src="${src}"]`);
            const correct = corrEl ? corrEl.value.trim() : '';
            if (correct) {
                wpList.push({ source_req_id: src, wrong_plan_id: wrong, correct_plan_id: correct });
            }
        });

        // Wrong spec / release (standards)
        document.querySelectorAll('.std-ws-check:checked').forEach(function (cb) {
            const src   = cb.dataset.src;
            const wrong = cb.dataset.wrong;
            const specEl = document.querySelector(`.ws-correct-spec[data-src="${src}"]`);
            const relEl  = document.querySelector(`.ws-correct-rel[data-src="${src}"]`);
            const correctSpec = specEl ? specEl.value.trim() : '';
            const correctRel  = relEl  ? relEl.value.trim()  : '';
            const entry = { source_req_id: src, wrong_spec: wrong };
            if (correctSpec) entry.correct_spec    = correctSpec;
            if (correctRel)  entry.correct_release = correctRel;
            wsList.push(entry);
        });

        return {
            internal_false_broken:  fbList,
            internal_wrong_target:  wtList,
            cross_plan_wrong_id:    wpList,
            standards_wrong_spec:   wsList,
        };
    }

    function _buildPayload() {
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
    window.rrSave = function () {
        const indicator = document.getElementById('save-indicator');
        if (indicator) indicator.textContent = 'Saving…';
        fetch(_root + '/resolve-review/' + encodeURIComponent(_docId) + '/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(_buildPayload()),
        })
        .then(r => r.json())
        .then(function (data) {
            if (indicator) indicator.textContent = data.ok ? '✓ Saved' : '✗ ' + data.error;
            setTimeout(function () { if (indicator) indicator.textContent = ''; }, 3000);
        })
        .catch(function (err) {
            if (indicator) indicator.textContent = '✗ ' + err;
        });
    };

    /* -----------------------------------------------------------------------
     * Report
     * --------------------------------------------------------------------- */
    window.rrReport = function () {
        const out = document.getElementById('report-output');
        if (out) out.innerHTML = '<div class="text-muted small py-1">Generating…</div>';
        fetch(_root + '/resolve-review/' + encodeURIComponent(_docId) + '/report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(_buildPayload()),
        })
        .then(r => r.text())
        .then(function (html) { if (out) out.innerHTML = html; })
        .catch(function (err) {
            if (out) out.innerHTML = '<div class="alert alert-danger small">Report failed: ' + err + '</div>';
        });
    };

}());
