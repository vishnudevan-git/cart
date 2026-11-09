document.addEventListener('DOMContentLoaded', function() {
    const usernameInput = document.querySelector('input[name="username"]');
    const emailInput = document.querySelector('input[name="email"]');
    const phoneInput = document.querySelector('input[name="phone"]');

    if (!usernameInput) return;

    // Create error element for Bootstrap validation
    let errorEl = document.createElement('div');
    errorEl.className = 'invalid-feedback fw-semibold';
    errorEl.textContent = '❌ Must be a valid mobile number or email ID.';
    usernameInput.parentNode.appendChild(errorEl);

    // Provide immediate feedback on blur
    usernameInput.addEventListener('blur', function() {
        const val = usernameInput.value.trim();
        if (!val) return;

        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        const phoneRegex = /^\+?[0-9]{10,15}$/;

        if (val.toLowerCase() === 'admin') {
            usernameInput.classList.remove('is-invalid');
            usernameInput.classList.add('is-valid');
            return;
        }

        if (!emailRegex.test(val) && !phoneRegex.test(val)) {
            usernameInput.classList.add('is-invalid');
            usernameInput.classList.remove('is-valid');
        }
    });

    // Auto-update phone or email as the user types
    usernameInput.addEventListener('input', function() {
        const val = usernameInput.value.trim();
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        const phoneRegex = /^\+?[0-9]{10,15}$/;

        // Remove error state immediately if they fixed it
        if (val.toLowerCase() === 'admin' || emailRegex.test(val) || phoneRegex.test(val)) {
            usernameInput.classList.remove('is-invalid');
            usernameInput.classList.add('is-valid');
        } else if (usernameInput.classList.contains('is-valid')) {
           // if they mess it up after it was valid, remove valid state but don't show invalid yet
           usernameInput.classList.remove('is-valid');
        }

        // Auto Populate corresponding fields
        if (emailRegex.test(val)) {
            if (emailInput && emailInput.value !== val) {
                emailInput.value = val;
            }
        } 
        if (phoneRegex.test(val)) {
            if (phoneInput && phoneInput.value !== val) {
                phoneInput.value = val;
            }
        }
    });
});
