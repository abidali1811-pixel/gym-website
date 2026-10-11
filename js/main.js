(function () {
  document.documentElement.classList.add("motion-ready");

  const toggle = document.querySelector(".nav-toggle");
  const nav = document.querySelector(".main-nav");
  const yearEl = document.getElementById("year");
  const header = document.querySelector(".site-header");
  const progress = document.querySelector(".scroll-progress");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const updateScrollUI = () => {
    const scrollable = document.documentElement.scrollHeight - window.innerHeight;
    const amount = scrollable > 0 ? Math.min(1, window.scrollY / scrollable) : 0;
    if (progress) progress.style.transform = `scaleX(${amount})`;
    if (header) header.classList.toggle("is-scrolled", window.scrollY > 18);
  };
  let scrollFrame = 0;
  window.addEventListener("scroll", () => {
    if (scrollFrame) return;
    scrollFrame = window.requestAnimationFrame(() => {
      updateScrollUI();
      scrollFrame = 0;
    });
  }, { passive: true });
  updateScrollUI();

  const revealItems = document.querySelectorAll(".reveal, .class-card, .price-card, .about-card");
  if ("IntersectionObserver" in window && !reduceMotion) {
    const revealObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.14, rootMargin: "0px 0px -36px 0px" });
    revealItems.forEach((item, index) => {
      item.classList.add("reveal");
      item.style.transitionDelay = `${Math.min(index % 5, 4) * 55}ms`;
      revealObserver.observe(item);
    });
  } else {
    revealItems.forEach((item) => item.classList.add("is-visible"));
  }

  if (!reduceMotion && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    document.querySelectorAll(".gallery-card, .class-card, .price-card, .about-card").forEach((card) => {
      let frame = 0;
      card.addEventListener("pointermove", (event) => {
        if (frame) return;
        frame = window.requestAnimationFrame(() => {
          const bounds = card.getBoundingClientRect();
          const x = (event.clientX - bounds.left) / bounds.width;
          const y = (event.clientY - bounds.top) / bounds.height;
          const rotateY = (x - 0.5) * 5;
          const rotateX = (0.5 - y) * 5;
          card.style.setProperty("--glow-x", `${x * 100}%`);
          card.style.setProperty("--glow-y", `${y * 100}%`);
          card.style.transform = `perspective(1100px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateY(-3px)`;
          frame = 0;
        });
      });
      card.addEventListener("pointerleave", () => {
        if (frame) window.cancelAnimationFrame(frame);
        frame = 0;
        card.style.transform = "";
      });
    });
  }

  if (yearEl) {
    yearEl.textContent = String(new Date().getFullYear());
  }

  const formStatus = document.getElementById("form-status");
  if (formStatus && new URLSearchParams(window.location.search).get("submitted") === "1") {
    formStatus.textContent = "Thanks! Your visit request has been saved.";
    formStatus.classList.add("form-success");
  }

  if (toggle && nav) {
    toggle.addEventListener("click", () => {
      const open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });

    nav.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        nav.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

})();
