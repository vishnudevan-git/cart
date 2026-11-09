/**
 * Pincode auto-fill using India Post API.
 * Call: initPincodeLookup(pincodeInputId, fieldMap)
 * fieldMap: { city, taluk, district, state, postOffice, infoDiv, spinner }
 */
function initPincodeLookup(pincodeInputId, fieldMap) {
  const pincodeEl = document.getElementById(pincodeInputId);
  if (!pincodeEl) return;

  pincodeEl.addEventListener('input', function () {
    const pin = this.value.trim();
    const info = fieldMap.infoDiv ? document.getElementById(fieldMap.infoDiv) : null;
    const spin = fieldMap.spinner ? document.getElementById(fieldMap.spinner) : null;

    if (pin.length !== 6 || !/^\d{6}$/.test(pin)) {
      if (info) info.innerHTML = '';
      return;
    }

    if (spin) spin.style.display = '';
    if (info) info.innerHTML = '';

    fetch('/accounts/pincode/' + pin + '/')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (spin) spin.style.display = 'none';
        if (!data || !data[0] || data[0].Status !== 'Success') {
          if (info) info.innerHTML = '<span class="text-danger small"><i class="bi bi-x-circle me-1"></i>Invalid pincode</span>';
          return;
        }

        var offices = data[0].PostOffice;
        var first = offices[0];

        function fill(id, val) {
          var el = document.getElementById(id);
          if (el && val) el.value = val;
        }

        fill(fieldMap.district, first.District);
        fill(fieldMap.taluk, first.Taluk || first.Division || '');
        fill(fieldMap.state, first.State);
        if (fieldMap.city) {
          var cityEl = document.getElementById(fieldMap.city);
          if (cityEl && !cityEl.value) cityEl.value = first.District;
        }

        if (offices.length === 1) {
          fill(fieldMap.postOffice, first.Name);
          if (info) info.innerHTML =
            '<span class="badge bg-success me-1">\u2713 ' + first.District + '</span>' +
            '<span class="badge bg-secondary me-1">' + (first.Taluk || '') + ' Taluk</span>' +
            '<span class="badge bg-primary">' + first.State + '</span>' +
            '<div class="text-muted small mt-1">\uD83D\uDCEE ' + first.Name + '</div>';
        } else {
          // Build inline dropdown
          var opts = '';
          for (var i = 0; i < offices.length; i++) {
            var p = offices[i];
            opts += '<option value="' + p.Name + '" data-taluk="' + (p.Taluk||'') +
              '" data-district="' + p.District + '" data-state="' + p.State + '">' +
              p.Name + '</option>';
          }

          if (info) info.innerHTML =
            '<span class="badge bg-success me-1">\u2713 ' + first.District + '</span>' +
            '<span class="badge bg-secondary me-1">' + (first.Taluk || '') + ' Taluk</span>' +
            '<span class="badge bg-primary me-1">' + first.State + '</span>' +
            '<div class="mt-1"><label class="small text-muted">\uD83D\uDCEE Select Village / Post Office:</label>' +
            '<select class="form-select form-select-sm mt-1" id="poSelect_' + pincodeInputId + '">' + opts + '</select></div>';

          fill(fieldMap.postOffice, first.Name);

          // Bind inline dropdown change
          setTimeout(function() {
            var sel = document.getElementById('poSelect_' + pincodeInputId);
            if (sel) {
              sel.addEventListener('change', function() {
                var opt = this.options[this.selectedIndex];
                fill(fieldMap.postOffice, opt.value);
                fill(fieldMap.taluk, opt.dataset.taluk);
                fill(fieldMap.district, opt.dataset.district);
                fill(fieldMap.state, opt.dataset.state);
                if (fieldMap.city) {
                  var ce = document.getElementById(fieldMap.city);
                  if (ce) ce.value = opt.dataset.district;
                }
              });
            }
          }, 50);

          // Only force the village-picker MODAL when the caller actually has
          // address fields to fill (district/postOffice/etc). On the register
          // page there are none — pincode just shows an info badge — so the
          // modal was pure friction and is skipped there. The inline dropdown
          // above already covers callers that want a choice without a popup.
          var hasAddressTargets = !!(fieldMap.postOffice || fieldMap.district ||
                                     fieldMap.state || fieldMap.taluk || fieldMap.city);
          if (hasAddressTargets) {
            _showPOModal(offices, fill, fieldMap, info, pincodeInputId);
          }
        }
      })
      .catch(function() {
        if (spin) spin.style.display = 'none';
        if (info) info.innerHTML = '<span class="text-muted small">Could not fetch. Fill manually.</span>';
      });
  });

  if (pincodeEl.value && pincodeEl.value.length === 6) {
    pincodeEl.dispatchEvent(new Event('input'));
  }
}

function _showPOModal(offices, fill, fieldMap, info, pincodeInputId) {
  // Remove old modal
  var old = document.getElementById('poModal');
  if (old) old.remove();
  var oldBd = document.querySelectorAll('.modal-backdrop');
  oldBd.forEach(function(b) { b.remove(); });
  document.body.classList.remove('modal-open');
  document.body.style.overflow = '';

  var items = '';
  for (var i = 0; i < offices.length; i++) {
    var p = offices[i];
    var badge = '';
    items += '<button type="button" class="list-group-item list-group-item-action d-flex justify-content-between align-items-center po-pick" ' +
      'data-name="' + p.Name + '" data-taluk="' + (p.Taluk||'') + '" data-district="' + p.District + '" data-state="' + p.State + '">' +
      '<div><div class="fw-semibold">' + p.Name + '</div>' +
      '<div class="text-muted small">' + (p.Taluk||'') + ', ' + p.District + '</div></div>' +
      '</button>';
  }

  var html = '<div class="modal fade" id="poModal" tabindex="-1">' +
    '<div class="modal-dialog modal-dialog-centered modal-dialog-scrollable">' +
    '<div class="modal-content">' +
    '<div class="modal-header">' +
    '<h6 class="modal-title">\uD83D\uDCEE Select Village / Post Office</h6>' +
    '<button type="button" class="btn-close" data-bs-dismiss="modal"></button>' +
    '</div>' +
    '<div class="modal-body p-0">' +
    '<div class="list-group list-group-flush">' + items + '</div>' +
    '</div></div></div></div>';

  document.body.insertAdjacentHTML('beforeend', html);

  var modalEl = document.getElementById('poModal');
  var bsModal = new bootstrap.Modal(modalEl);

  modalEl.querySelectorAll('.po-pick').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var name = this.dataset.name;
      fill(fieldMap.postOffice, name);
      fill(fieldMap.taluk, this.dataset.taluk);
      fill(fieldMap.district, this.dataset.district);
      fill(fieldMap.state, this.dataset.state);
      if (fieldMap.city) {
        var ce = document.getElementById(fieldMap.city);
        if (ce) ce.value = this.dataset.district;
      }

      // Sync inline dropdown
      var sel = document.getElementById('poSelect_' + pincodeInputId);
      if (sel) sel.value = name;

      bsModal.hide();
    });
  });

  modalEl.addEventListener('hidden.bs.modal', function() {
    modalEl.remove();
  });

  bsModal.show();
}
