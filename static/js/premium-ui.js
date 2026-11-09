(function () {
  const MOTION_KEY = 'lakrishMotionProfile';
  const MOTION_VALUES = new Set(['low', 'medium', 'high']);

  function applyRevealHooks() {
    const selector = [
      '.product-card',
      '.category-card',
      '.card',
      '.topbar',
      '.navbar.sticky-top'
    ].join(',');

    document.querySelectorAll(selector).forEach((el) => {
      // Never fade-in a card that contains a data table — its rows must be
      // visible immediately (the fade was leaving admin tables blank until
      // scrolled into view). Skip these entirely.
      if (el.querySelector('.table-wrap, table')) return;
      if (!el.classList.contains('reveal-on-scroll')) {
        el.classList.add('reveal-on-scroll');
      }
    });
  }

  function resolveMotionProfile() {
    const stored = localStorage.getItem(MOTION_KEY);
    if (MOTION_VALUES.has(stored)) return stored;

    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return 'low';
    }
    return 'medium';
  }

  function setMotionProfile(profile) {
    const safeProfile = MOTION_VALUES.has(profile) ? profile : 'medium';
    document.documentElement.setAttribute('data-motion-profile', safeProfile);
    localStorage.setItem(MOTION_KEY, safeProfile);
    return safeProfile;
  }

  function initMotionProfile() {
    const active = resolveMotionProfile();
    document.documentElement.setAttribute('data-motion-profile', active);

    // Expose a tiny global API for quick tuning in devtools/settings hooks.
    window.setMotionProfile = setMotionProfile;
  }

  function initRevealObserver() {
    applyRevealHooks();
    const targets = document.querySelectorAll('.reveal-on-scroll');
    if (!targets.length) return;

    if (!('IntersectionObserver' in window)) {
      targets.forEach((el) => el.classList.add('is-visible'));
      return;
    }

    const io = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -8% 0px' });

    targets.forEach((el) => io.observe(el));

    // SAFETY NET: the reveal effect starts elements at opacity:0. If the
    // observer never fires for an element (e.g. a data table below the fold
    // that the user doesn't scroll to), that content would stay invisible —
    // which looked like "no customers" on the admin tables. Force-reveal any
    // element still hidden shortly after load so nothing can get stuck.
    setTimeout(function () {
      targets.forEach(function (el) { el.classList.add('is-visible'); });
    }, 600);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initMotionProfile();
    initRevealObserver();
  });
})();
