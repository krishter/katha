// katha.life story page. Progressive enhancement only: without this file (or without GSAP) every
// section is in its final state. Native scrolling throughout; pinning is ScrollTrigger, never scroll-jacking.
(() => {
  const root = document.documentElement;
  const story = document.getElementById('story');
  const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- "Hear Katha": only shown when the approved sample exists (brief §9.1) ----
  const hear = document.querySelector('[data-hear]');
  if (hear) {
    const src = hear.dataset.hear;
    fetch(src, { method: 'HEAD' })
      .then((res) => {
        if (!res.ok || !(res.headers.get('content-type') || '').startsWith('audio')) return;
        const audio = new Audio(src);
        const set = (on) => hear.setAttribute('aria-pressed', String(on));
        audio.addEventListener('ended', () => set(false));
        hear.addEventListener('click', () => {
          if (audio.paused) { audio.play(); set(true); }
          else { audio.pause(); audio.currentTime = 0; set(false); }
        });
        hear.hidden = false;
      })
      .catch(() => {});
  }

  // ---- Ground: place the dawn gradient where the .dawn band actually sits ----
  const dawn = document.querySelector('.dawn');
  function placeDawn() {
    const base = story.getBoundingClientRect().top;
    const r = dawn.getBoundingClientRect();
    story.style.setProperty('--dawn-start', `${r.top - base}px`);
    story.style.setProperty('--dawn-len', `${r.height}px`);
    return { top: r.top - base, bottom: r.bottom - base };
  }
  placeDawn();

  if (!window.gsap || !window.ScrollTrigger) {
    addEventListener('resize', placeDawn);
    return;
  }
  gsap.registerPlugin(ScrollTrigger);

  // ---- The thread: one line from the hero waveform onward, drawn by scroll position ----
  const thread = story.querySelector('.thread');
  const threadPath = thread.querySelector('path');
  const ink = thread.querySelector('#thread-ink');
  let samples = [];
  let total = 0;
  let penY = 0;

  function buildThread() {
    const zone = placeDawn();
    if (getComputedStyle(thread).display === 'none') return;
    const base = story.getBoundingClientRect();
    const gutterX = Math.max(24, (base.width - 1180) / 2 + 30);
    const pts = [...story.querySelectorAll('[data-thread]')]
      .map((el) => {
        // Marks are empty placeholders: their fractions refer to the section that holds them.
        const r = (el.classList.contains('thread-mark') ? el.parentElement : el).getBoundingClientRect();
        const tx = el.dataset.tx || '.5';
        const ty = parseFloat(el.dataset.ty || '.5');
        return {
          x: tx === 'gutter' ? gutterX : r.left - base.left + r.width * parseFloat(tx),
          y: r.top - base.top + r.height * ty,
        };
      })
      .sort((a, b) => a.y - b.y);
    if (pts.length < 2) return;

    let d = `M${pts[0].x},${pts[0].y}`;
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1];
      const b = pts[i];
      const h = (b.y - a.y) / 2;
      d += ` C${a.x},${a.y + h} ${b.x},${b.y - h} ${b.x},${b.y}`;
    }
    const height = story.offsetHeight;
    thread.setAttribute('viewBox', `0 0 ${base.width} ${height}`);
    thread.style.height = `${height}px`;
    threadPath.setAttribute('d', d);
    ink.setAttribute('y1', zone.top + (zone.bottom - zone.top) * 0.25);
    ink.setAttribute('y2', zone.bottom);

    total = threadPath.getTotalLength();
    samples = [];
    for (let i = 0, n = 800; i <= n; i++) {
      const len = (total * i) / n;
      samples.push({ len, y: threadPath.getPointAtLength(len).y });
    }
    penY = pts[0].y;
    threadPath.style.strokeDasharray = `${total} ${total}`;
    drawThread();
  }

  // The pen stays where the waveform started on screen; the line is drawn up to it.
  function drawThread() {
    if (!samples.length) return;
    if (reducedMotion()) { threadPath.style.strokeDashoffset = 0; return; }
    const tip = window.scrollY + penY;
    let lo = 0;
    let hi = samples.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (samples[mid].y < tip) lo = mid + 1; else hi = mid;
    }
    threadPath.style.strokeDashoffset = total - samples[lo].len;
  }

  ScrollTrigger.addEventListener('refresh', buildThread);
  ScrollTrigger.create({ start: 0, end: 'max', onUpdate: drawThread });
  if (document.fonts) document.fonts.ready.then(() => ScrollTrigger.refresh());

  // ---- Scroll-driven scenes (motion allowed only) ----
  const mm = gsap.matchMedia();
  mm.add(
    { desktop: '(min-width: 769px)', motion: '(prefers-reduced-motion: no-preference)' },
    (ctx) => {
      const { desktop, motion } = ctx.conditions;
      root.classList.toggle('motion', motion);
      if (!motion) return;

      // Act I, beat 1: the waveform flattens into the start of the thread.
      gsap.to('.hero-wave .bar', {
        scaleY: 0.05, transformOrigin: '50% 50%', ease: 'none',
        stagger: { each: 0.012, from: 'edges' },
        scrollTrigger: { trigger: '.hero', start: 'top top', end: '55% top', scrub: true },
      });
      gsap.to('.hero-copy, .scroll-cue', {
        opacity: 0, y: -30, ease: 'none',
        scrollTrigger: { trigger: '.hero', start: '25% top', end: '70% top', scrub: true },
      });

      // Act I, beat 2: three lines in sequence while the clocks turn.
      const lines = gsap.utils.toArray('.distance .line');
      const tl = gsap.timeline({
        defaults: { ease: 'none' },
        scrollTrigger: desktop
          ? { trigger: '.distance .pin-wrap', start: 'top top', end: '+=220%', pin: true, scrub: true }
          : { trigger: '.distance', start: 'top 65%', end: 'bottom 70%', scrub: true },
      });
      lines.forEach((line, i) => tl.fromTo(line, { opacity: 0.12, y: 18 }, { opacity: 1, y: 0, duration: 1 }, i * 1.3));
      tl.fromTo('#d-min-a', { rotation: 0 }, { rotation: 360, svgOrigin: '170 220', duration: 3.6 }, 0)
        .fromTo('#d-min-b', { rotation: 0 }, { rotation: 360, svgOrigin: '470 220', duration: 3.6 }, 0)
        .fromTo('.distance .call-end', { opacity: 0, scale: 0.5, transformOrigin: '50% 50%' }, { opacity: 1, scale: 1, duration: 0.6 }, 1.5);

      // Dawn: the sunrise lifts as the ground brightens.
      gsap.fromTo('.dawn-sun', { yPercent: 45 }, {
        yPercent: -5, ease: 'none',
        scrollTrigger: { trigger: '.dawn', start: 'top bottom', end: 'bottom top', scrub: true },
      });

      // Act II, beat 3: the setup fills in.
      gsap.fromTo('.phone-setup .field', { opacity: 0, y: 10 }, {
        opacity: 1, y: 0, stagger: 0.4, ease: 'none',
        scrollTrigger: {
          trigger: desktop ? '.setup' : '.phone-setup',
          start: desktop ? 'top 35%' : 'top 75%',
          end: desktop ? 'center 45%' : 'bottom 70%',
          scrub: true,
        },
      });

      // Act II, beat 4: the consent lines arrive one by one.
      gsap.fromTo('.consent .reveal', { opacity: 0.12 }, {
        opacity: 1, stagger: 0.5, ease: 'none',
        scrollTrigger: { trigger: '.consent .statement', start: 'top 80%', end: 'bottom 45%', scrub: true },
      });
      gsap.fromTo('.yeses li', { opacity: 0, y: 12 }, {
        opacity: 1, y: 0, stagger: 0.2, ease: 'none',
        scrollTrigger: { trigger: '.yeses', start: 'top 90%', end: 'top 60%', scrub: true },
      });

      // Act II, beat 5: the exchange plays on her phone.
      const status = document.querySelector('.chat-status');
      const chat = gsap.timeline({
        defaults: { ease: 'none' },
        scrollTrigger: {
          trigger: desktop ? '.morning' : '.phone-chat',
          start: desktop ? 'top 20%' : 'top 75%',
          end: desktop ? 'bottom 85%' : 'bottom 55%',
          scrub: true,
          onUpdate: (self) => { status.textContent = self.progress > 0.8 ? 'listening…' : 'online'; },
        },
      });
      gsap.utils.toArray('.chat .msg').forEach((msg, i) => {
        chat.fromTo(msg, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 1 }, i * 1.5);
      });

      return () => { status.textContent = 'listening…'; };
    },
  );
})();
