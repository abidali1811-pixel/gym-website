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
  const phoneInput = document.querySelector('.contact-form input[name="phone"]');
  if (phoneInput) {
    const phoneError = document.getElementById("phone-error");
    const validationMessage = "Enter exactly 10 digits.";
    let phoneTouched = false;
    const validatePhone = (showError) => {
      const valid = /^[0-9]{10}$/.test(phoneInput.value);
      phoneInput.setCustomValidity(phoneInput.value && !valid ? validationMessage : "");
      phoneInput.setAttribute("aria-invalid", String(!valid && showError));
      if (phoneError) {
        phoneError.textContent = validationMessage;
        phoneError.hidden = valid || !showError;
      }
      return valid;
    };
    phoneInput.addEventListener("beforeinput", (event) => {
      if (!event.inputType.startsWith("insert")) return;
      const inserted = event.data ?? event.dataTransfer?.getData("text") ?? "";
      const start = phoneInput.selectionStart ?? phoneInput.value.length;
      const end = phoneInput.selectionEnd ?? start;
      const existingDigits = phoneInput.value.replace(/\D/g, "").length;
      const selectedDigits = phoneInput.value.slice(start, end).replace(/\D/g, "").length;
      const insertedDigits = inserted.replace(/\D/g, "").length;
      if (existingDigits - selectedDigits + insertedDigits > 10) {
        event.preventDefault();
        if (phoneError) {
          phoneError.textContent = "Only 10 digits are allowed. Enter exactly 10 digits to submit.";
          phoneError.hidden = false;
        }
      }
    });
    phoneInput.addEventListener("input", () => {
      phoneInput.value = phoneInput.value.replace(/\D/g, "").slice(0, 10);
      validatePhone(phoneTouched);
    });
    phoneInput.addEventListener("blur", () => {
      phoneTouched = true;
      validatePhone(true);
    });
    phoneInput.addEventListener("invalid", () => {
      phoneTouched = true;
      validatePhone(true);
    });
    phoneInput.form?.addEventListener("submit", (event) => {
      if (!validatePhone(true)) {
        event.preventDefault();
        phoneInput.reportValidity();
      }
    });
  }
  const formParams = new URLSearchParams(window.location.search);
  if (formStatus && formParams.get("submitted") === "1") {
    formStatus.textContent = "Thanks! Your visit request has been saved.";
    formStatus.classList.add("form-success");
  } else if (formStatus && formParams.get("form_error") === "invalid") {
    formStatus.textContent = "Please check your name and email, and enter exactly 10 mobile digits.";
    formStatus.classList.add("form-error");
  } else if (formStatus && formParams.get("form_error") === "captcha") {
    formStatus.textContent = "The security check was incorrect or expired. Please try again.";
    formStatus.classList.add("form-error");
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
