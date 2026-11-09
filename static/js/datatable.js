/**
 * datatable.js — Global DataTable helpers for Lakrish admin panel.
 *
 * Features
 * ────────
 *  1. AUTO-SEARCH (input[name="q"])
 *     • 400 ms debounce + Enter key; strips legacy onchange attributes.
 *     • Also auto-submits filter <select> elements in the same form.
 *
 *  2. BULK SELECTION (multi-master checkbox support)
 *     • Finds #selectAll, .master-check, or any thead checkbox automatically.
 *     • Conflict-guard: set window._bulkScriptOwner = true in an inline script
 *       to keep page-level bulk logic (e.g. user_list) in charge.
 *
 *  3. SAFEGUARDS
 *     a. Clears selections on filter change.
 *     b. Confirms bulk actions via the global showConfirm() modal.
 *     c. Dims the table tbody while the request is in-flight.
 */
(function () {
  'use strict';

  /* ── helpers ────────────────────────────────────────────────────────────── */

  var _pendingSubmitBtn = null; // last submit button clicked inside #bulkForm

  function confirm_(msg, title, type, icon, onConfirm) {
    if (typeof showConfirm === 'function') {
      showConfirm({ title: title, message: msg, type: type, icon: icon, onConfirm: onConfirm });
    } else if (window.confirm(msg)) {
      onConfirm();
    }
  }

  /* ── 1. AUTO-SEARCH ─────────────────────────────────────────────────────── */

  function initAutoSearch() {
    document.querySelectorAll('input[name="q"]').forEach(function (input) {
      var form = input.closest('form');
      if (!form) return;

      // Strip legacy inline handlers so the form doesn't double-fire.
      input.removeAttribute('onchange');
      input.removeAttribute('onkeyup');
      input.onchange = null;

      var timer = null;

      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          clearTimeout(timer);
          clearSelections();
          dimTable(true);
          form.submit();
        }
      });

      input.addEventListener('input', function () {
        clearSelections();
        clearTimeout(timer);
        timer = setTimeout(function () {
          dimTable(true);
          form.submit();
        }, 400);
      });

      // Auto-submit filter <select> elements in the same form.
      // Skip the bulk-action select so it doesn't trigger a filter reload.
      form.querySelectorAll('select[name]').forEach(function (sel) {
        if (sel.hasAttribute('data-bulk-action-select')) return;
        sel.removeAttribute('onchange');
        sel.onchange = null;
        sel.addEventListener('change', function () {
          clearSelections();
          clearTimeout(timer);
          dimTable(true);
          form.submit();
        });
      });
    });
  }

  /* ── 2. BULK SELECTION ──────────────────────────────────────────────────── */
  //
  // Supported HTML conventions (any subset is fine on a given page):
  //   Master checkboxes  →  id="selectAll"  OR  class="master-check"
  //                         OR  any  thead input[type="checkbox"]  (auto-detected)
  //   Row checkboxes     →  class="row-check"
  //   Bulk container     →  id="bulkBar"  (hidden when nothing selected)
  //   Bulk form          →  id="bulkForm"
  //   Count label        →  id="selectedCount"
  //   Exec button        →  id="bulkExecBtn"
  //
  // Conflict guard: if the page provides its own inline bulk script (e.g. user_list),
  // it sets  window._bulkScriptOwner = true  BEFORE DOMContentLoaded fires.
  // datatable.js will skip initBulkSelection() in that case.

  function initBulkSelection() {
    // Yield to any page-level inline bulk script.
    if (window._bulkScriptOwner) return;

    var bulkBar       = document.getElementById('bulkBar');
    var bulkForm      = document.getElementById('bulkForm');
    var selectedCount = document.getElementById('selectedCount');
    var execBtn       = document.getElementById('bulkExecBtn');

    // Collect ALL master checkboxes on the page.
    var masterCBs = Array.from(
      document.querySelectorAll('#selectAll, .master-check, thead input[type="checkbox"]:not(.row-check)')
    ).filter(function (el, idx, arr) {
      return arr.indexOf(el) === idx; // deduplicate
    });

    if (!masterCBs.length) return;

    /* Initial state ─────────────────────────────────────────────────────── */
    if (bulkBar) {
      bulkBar.style.display = 'none';
      _setActionEls(bulkBar, false);
    }

    /* Helpers ───────────────────────────────────────────────────────────── */

    function rowChecks() {
      return Array.from(document.querySelectorAll('.row-check'));
    }

    function updateUI() {
      var checks  = rowChecks();
      var n       = checks.filter(function (c) { return c.checked; }).length;

      // Sync every master checkbox to reflect the current selection state.
      masterCBs.forEach(function (mc) {
        if (n === 0) {
          mc.checked = false; mc.indeterminate = false;
        } else if (n === checks.length) {
          mc.checked = true;  mc.indeterminate = false;
        } else {
          mc.checked = false; mc.indeterminate = true;
        }
      });

      // Count label.
      if (selectedCount) selectedCount.textContent = n > 0 ? n + ' selected' : '';

      // Bulk bar visibility + action-element state.
      if (bulkBar) {
        bulkBar.style.display = n > 0 ? '' : 'none';
        _setActionEls(bulkBar, n > 0);
      }

      if (execBtn) execBtn.disabled = n === 0;
    }

    /* Wire master checkboxes ────────────────────────────────────────────── */
    masterCBs.forEach(function (mc) {
      mc.addEventListener('change', function () {
        rowChecks().forEach(function (c) { c.checked = mc.checked; });
        updateUI();
      });
    });

    /* Row checkbox (delegated on document) ──────────────────────────────── */
    document.addEventListener('change', function (e) {
      if (e.target.classList.contains('row-check')) updateUI();
    });

    /* Track last-clicked submit button inside bulkForm ──────────────────── */
    if (bulkForm) {
      bulkForm.querySelectorAll('button[type="submit"]').forEach(function (btn) {
        btn.addEventListener('click', function () { _pendingSubmitBtn = btn; });
      });
    }

    /* Bulk form confirmation ─────────────────────────────────────────────── */
    if (bulkForm) {
      bulkForm.addEventListener('submit', function (e) {
        // Row-level action buttons reuse the CSRF token from the bulk form but
        // override its destination with formaction. They are not bulk actions.
        if (e.submitter && e.submitter.hasAttribute('formaction')) return;

        var checks = rowChecks().filter(function (c) { return c.checked; });
        if (checks.length === 0) { e.preventDefault(); return; }

        var btn        = _pendingSubmitBtn;
        var label      = (btn && (btn.dataset.actionLabel || btn.textContent.trim())) || 'this action';
        var actionType = (btn && btn.dataset.confirmType) || 'warning';
        var icon       = actionType === 'danger' ? '🗑️' : '⚠️';
        var msg        = checks.length + ' item' + (checks.length > 1 ? 's' : '') +
                         ' will be affected by "' + label.trim() + '". Continue?';

        // Skip if the button already carries data-confirm (base_dashboard handles it).
        if (btn && btn.dataset.confirm) return;

        e.preventDefault();
        var form = bulkForm;
        confirm_(msg, 'Confirm Bulk Action', actionType, icon, function () {
          dimTable(true);
          form.submit();
        });
      });
    }

    /* Bulk <select> + exec button ───────────────────────────────────────── */
    document.querySelectorAll('select[data-bulk-action-select]').forEach(function (sel) {
      sel.addEventListener('change', function () {
        if (execBtn) {
          execBtn.disabled = !sel.value || rowChecks().filter(function (c) { return c.checked; }).length === 0;
        }
      });
    });
  }

  /* ── 3. TABLE DIMMING ───────────────────────────────────────────────────── */

  function dimTable(on) {
    var tbody = document.querySelector('table.table tbody');
    if (!tbody) return;
    tbody.style.transition    = 'opacity 0.2s';
    tbody.style.opacity       = on ? '0.45' : '';
    tbody.style.pointerEvents = on ? 'none'  : '';
  }

  /* ── 4. CLEAR SELECTIONS ────────────────────────────────────────────────── */

  function clearSelections() {
    if (window._bulkScriptOwner) return; // let page-level script own this
    document.querySelectorAll('.row-check').forEach(function (c) { c.checked = false; });
    document.querySelectorAll('#selectAll, .master-check, thead input[type="checkbox"]:not(.row-check)')
      .forEach(function (mc) { mc.checked = false; mc.indeterminate = false; });
    var bb = document.getElementById('bulkBar');
    if (bb) { bb.style.display = 'none'; _setActionEls(bb, false); }
    var sc = document.getElementById('selectedCount');
    if (sc) sc.textContent = '';
  }

  /* ── private helpers ────────────────────────────────────────────────────── */

  function _setActionEls(container, enabled) {
    container.querySelectorAll(
      'button[type="submit"], button[data-bulk-action], select[data-bulk-action-select]'
    ).forEach(function (el) { el.disabled = !enabled; });
    var eb = document.getElementById('bulkExecBtn');
    if (eb) eb.disabled = !enabled;
  }

  /* ── INIT ───────────────────────────────────────────────────────────────── */

  document.addEventListener('DOMContentLoaded', function () {
    initAutoSearch();
    initBulkSelection();
  });

})();
