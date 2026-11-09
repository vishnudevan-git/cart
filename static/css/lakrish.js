// LaKrish Mart — product card interactions

function getCsrf() {
  // Prefer the hidden input rendered by {% csrf_token %} in the DOM
  const el = document.querySelector('[name=csrfmiddlewaretoken]');
  if (el) return el.value;
  // Exact cookie name match (avoids matching lakrish_demo_csrftoken)
  const c = document.cookie.split('; ').find(s => s.startsWith('csrftoken='));
  return c ? c.split('=')[1] : '';
}

// Variant select → update price / stock display
document.addEventListener('change', function(e) {
  if (!e.target.classList.contains('variant-select')) return;
  const pid = e.target.dataset.product;
  const opt = e.target.options[e.target.selectedIndex];
  const price   = Math.round(parseFloat(opt.dataset.price) || 0);
  const mrp     = Math.round(parseFloat(opt.dataset.mrp) || 0);
  const disc    = parseInt(opt.dataset.discount);
  const stock   = parseInt(opt.dataset.stock);
  const showMrp = String(e.target.dataset.showMrp || '0') === '1';

  const spEl   = document.getElementById('sp-'    + pid);
  const mrpEl  = document.getElementById('mrp-'   + pid);
  const stEl   = document.getElementById('stock-' + pid);
  const addBtn = document.getElementById('add-btn-' + pid);

  if (spEl)  spEl.textContent = '₹' + price;
  if (mrpEl) {
    if (showMrp && disc > 0) { mrpEl.textContent = '₹' + mrp; mrpEl.classList.remove('d-none'); }
    else          { mrpEl.classList.add('d-none'); }
  }
  if (stEl) {
    stEl.innerHTML = stock > 0
      ? '<span class="text-success">● In Stock</span>'
      : '<span class="text-danger">● Out of Stock</span>';
  }
  if (addBtn) {
    if (stock === 0) { addBtn.classList.add('disabled'); }
    else             { addBtn.classList.remove('disabled'); }
  }

  // Update adding state UI based on new selected variant
  if (typeof updateCardUI === 'function') {
      updateCardUI(pid);
  }
});

// Global Cart State
window.cartState = {};

// On load, fetch cart state and update UI
document.addEventListener('DOMContentLoaded', () => {
    fetch('/cart/api/state/')
    .then(r => r.json())
    .then(data => {
        window.cartState = data;
        updateAllCards();
    }).catch(e => console.error(e));
});

function getSelectedVariant(pid) {
  const vsel = document.getElementById('vsel-' + pid);
  if (!vsel) return null;
  return vsel.tagName === 'SELECT' ? vsel.options[vsel.selectedIndex].value : vsel.value;
}

function updateCardUI(pid) {
  const variantId = getSelectedVariant(pid);
  if (!variantId) return;

  const qty = window.cartState[variantId] || 0;
  const addBtn = document.getElementById('add-btn-' + pid);
  const qtyGroup = document.getElementById('qty-group-' + pid);
  const qtyInput = document.getElementById('qty-' + pid);

  if (addBtn && qtyGroup) {
      if (qty > 0) {
          addBtn.classList.add('d-none');
          qtyGroup.classList.remove('d-none');
          if (qtyInput) qtyInput.value = qty;
      } else {
          addBtn.classList.remove('d-none');
          qtyGroup.classList.add('d-none');
          if (qtyInput) qtyInput.value = 1;
      }
  }
}

function updateAllCards() {
    document.querySelectorAll('[id^="card-"]').forEach(card => {
        const pid = card.id.replace('card-', '');
        updateCardUI(pid);
    });
}

function addFirstToCart(e, pid) {
  if (e) e.preventDefault();
  updateCartQty(e, pid, 1, true);
}

function buyNowFromCard(e, pid) {
  if (e) e.preventDefault();
  const variantId = getSelectedVariant(pid);
  if (!variantId) {
    alert('Please select a variant first.');
    return;
  }

  // If already in cart, just go to checkout — don't add again
  if (window.cartState && window.cartState[variantId] > 0) {
    window.location.href = '/orders/checkout/';
    return;
  }

  const csrfToken = getCsrf();
  const btn = e ? e.target.closest('button') : null;
  const origText = btn ? btn.innerHTML : '';
  if (btn) { btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Please wait...'; btn.disabled = true; }

  // Use set_qty to force exactly 1 (not stack on existing)
  fetch(`/cart/api/set_qty/${variantId}/`, {
    method: 'POST',
    headers: {
      'X-CSRFToken': csrfToken,
      'X-Requested-With': 'XMLHttpRequest',
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: 'quantity=1',
  })
  .then(r => {
    if (!r.ok) throw new Error('Server error: ' + r.status);
    return r.json();
  })
  .then(data => {
    if (data.success) {
      window.location.href = '/orders/checkout/';
    } else {
      alert(data.error || 'Could not add to cart. Please try again.');
      if (btn) { btn.innerHTML = origText; btn.disabled = false; }
    }
  })
  .catch(err => {
    console.error('Buy Now error:', err);
    alert('Something went wrong. Please try again.');
    if (btn) { btn.innerHTML = origText; btn.disabled = false; }
  });
}

// Reset all Buy Now buttons when user navigates back (browser cache restores loading state)
window.addEventListener('pageshow', function(e) {
  if (e.persisted) {
    document.querySelectorAll('.btn-buy-now').forEach(function(btn) {
      if (btn.disabled) {
        btn.innerHTML = 'Buy Now';
        btn.disabled = false;
      }
    });
  }
});

function updateCartQty(e, pid, delta, isFirst=false) {
  if (e) e.preventDefault();
  const variantId = getSelectedVariant(pid);
  if (!variantId) return;

  let currentQty = window.cartState[variantId] || 0;
  const newQty = isFirst ? 1 : currentQty + delta;
  
  if (newQty < 0) return;

  const csrfToken = getCsrf();
  
  // Optimistic UI for addFirst
  const addBtn = document.getElementById('add-btn-' + pid);
  let originalHtml = '';
  if (isFirst && addBtn) {
      originalHtml = addBtn.innerHTML;
      addBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
      addBtn.classList.add('disabled');
  }

  fetch(`/cart/api/set_qty/${variantId}/`, {
    method: 'POST',
    headers: {
      'X-CSRFToken': csrfToken,
      'X-Requested-With': 'XMLHttpRequest',
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: `quantity=${newQty}`,
  })
  .then(r => r.json())
  .then(data => {
    if (isFirst && addBtn) {
        addBtn.classList.remove('disabled');
    }
    
    if (data.success) {
      window.cartState[variantId] = data.quantity;

      // Update all cart badges in navbar
      document.querySelectorAll('[data-cart-badge]').forEach(el => {
        el.textContent = data.cart_count;
        el.classList.remove('d-none');
      });

      if (isFirst && addBtn) {
        addBtn.classList.add('btn-added-state');
        addBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i> Added';
        // Keep success state briefly, then switch to qty stepper UI
        setTimeout(() => {
          addBtn.classList.remove('btn-added-state');
          addBtn.innerHTML = originalHtml;
          updateCardUI(pid);
        }, 700);
      } else {
        updateCardUI(pid);
      }
    } else {
      if (data.error) showToast(data.error, 'danger');
      if (isFirst && addBtn) {
        addBtn.innerHTML = originalHtml;
      }
    }
  })
  .catch(e => {
      if (isFirst && addBtn) {
          addBtn.innerHTML = originalHtml;
          addBtn.classList.remove('disabled');
      }
      showToast("Network error updating cart", 'danger');
  });
}

// Simple toast notification
function showToast(msg, type = 'success') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position:fixed;bottom:1rem;right:1rem;z-index:9999;display:flex;flex-direction:column;gap:.5rem';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `alert alert-${type} py-2 px-3 mb-0 shadow`;
  toast.style.cssText = 'min-width:220px;font-size:.875rem';
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}
