// katha.life story page. Progressive enhancement only: without this file (or without GSAP) every
// section is in its final state. Native scrolling throughout; pinning is ScrollTrigger, never scroll-jacking.
(() => {
  const root = document.documentElement;
  const story = document.getElementById('story');
  const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

  // ---- Pilot form: fetch submit with an inline status; posts normally without JS ----
  const form = $('#pilot-form');
  if (form) {
    const status = $('#form-status');
    const button = $('.submit', form);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      button.disabled = true;
      button.textContent = 'Sending…';
      status.className = 'form-status';
      status.textContent = '';
      try {
        const res = await fetch(form.action, { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' } });
        if (!res.ok) throw new Error(String(res.status));
        status.textContent = "Thank you. We have your request, and we'll write to you personally.";
        status.classList.add('ok');
        form.reset();
      } catch {
        status.textContent = "That didn't go through. Please try again in a moment.";
        status.classList.add('err');
      } finally {
        button.disabled = false;
        button.textContent = 'Request a pilot spot';
      }
    });
  }

  // ---- "Hear Katha": the build only un-hides it when the approved sample exists (brief §9.1) ----
  const hear = $('.hear');
  if (hear && !hear.hidden) {
    const audio = new Audio(hear.dataset.hear);
    const set = (on) => hear.setAttribute('aria-pressed', String(on));
    audio.addEventListener('ended', () => set(false));
    hear.addEventListener('click', () => {
      if (audio.paused) { audio.play(); set(true); } else { audio.pause(); audio.currentTime = 0; set(false); }
    });
  }

  // Scroll scenes start after the page has loaded and painted: every section already reads in its
  // final state, so first paint never waits on pin measurement or layout work.
  // (rAF then setTimeout: runs just after the next frame has been painted.)
  const afterPaint = () => requestAnimationFrame(() => setTimeout(scenes, 0));
  if (document.readyState === 'complete') afterPaint();
  else addEventListener('load', afterPaint, { once: true });

  function scenes() {
    if (!window.gsap || !window.ScrollTrigger) return;
    gsap.registerPlugin(ScrollTrigger);
    if (window.MotionPathPlugin) gsap.registerPlugin(MotionPathPlugin);

    // Split each "what fades" detail into letters, so each letter can become a dot.
    $$('.detail').forEach((li) => {
      const text = li.textContent.trim();
      li.textContent = '';
      const sr = document.createElement('span');
      sr.className = 'sr-only';
      sr.textContent = text;
      const vis = document.createElement('span');
      vis.setAttribute('aria-hidden', 'true');
      text.split(' ').forEach((word, i, words) => {
        const w = document.createElement('span');
        w.className = 'w';
        for (const ch of word) {
          const c = document.createElement('span');
          c.className = 'c';
          c.textContent = ch;
          w.append(c);
        }
        vis.append(w);
        if (i < words.length - 1) vis.append(' ');
      });
      li.append(sr, vis);
    });

    // ---- The thread: one line from the hero waveform to the bookshelf, drawn by scroll position ----
    const thread = $('.thread', story);
    const threadPath = $('path', thread);
    const ink = $('#thread-ink', thread);
    const nib = $('.nib', thread);
    const nibDots = $$('circle', nib);
    let samples = [];
    let total = 0;
    let penY = 0;

    function bandSpan(sel, base) {
      const r = $(sel).getBoundingClientRect();
      return { top: r.top - base.top, bottom: r.bottom - base.top };
    }

    function buildThread() {
      if (getComputedStyle(thread).display === 'none') return;
      const base = story.getBoundingClientRect();
      const gutterX = Math.max(24, (base.width - 1180) / 2 + 30);
      const pts = $$('[data-thread]', story)
        .map((el) => {
          // Marks are empty placeholders: their fractions refer to the section that holds them.
          const host = el.classList.contains('thread-mark') ? el.parentElement : el;
          const r = host.getBoundingClientRect();
          let x;
          if (el.dataset.xref) {
            const ref = $(el.dataset.xref).getBoundingClientRect();
            x = ref.left - base.left + ref.width / 2;
          } else if (el.dataset.tx === 'gutter') {
            x = gutterX;
          } else {
            x = r.left - base.left + r.width * parseFloat(el.dataset.tx || '.5');
          }
          const y = el.dataset.tvh
            ? r.top - base.top + innerHeight * parseFloat(el.dataset.tvh)
            : r.top - base.top + r.height * parseFloat(el.dataset.ty || '.5');
          return { x, y };
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

      // Gold on indigo, indigo on parchment: the ink changes inside the dawn and dusk bands.
      const dawn = bandSpan('.dawn', base);
      const dusk = bandSpan('.dusk', base);
      const stop = (y, c) => `<stop offset="${Math.min(1, Math.max(0, y / height))}" stop-color="${c}"/>`;
      ink.setAttribute('y2', height);
      ink.innerHTML = [
        stop(dawn.top + (dawn.bottom - dawn.top) * 0.3, '#F2C14E'),
        stop(dawn.top + (dawn.bottom - dawn.top) * 0.6, '#E8822F'),
        stop(dawn.bottom, '#241D5C'),
        stop(dusk.top + (dusk.bottom - dusk.top) * 0.4, '#241D5C'),
        stop(dusk.top + (dusk.bottom - dusk.top) * 0.7, '#E8822F'),
        stop(dusk.bottom, '#F2C14E'),
      ].join('');

      total = threadPath.getTotalLength();
      samples = [];
      for (let i = 0, n = 600; i <= n; i++) {
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
      if (reducedMotion()) { threadPath.style.strokeDashoffset = 0; nib.classList.remove('on'); return; }
      const tip = window.scrollY + penY;
      let lo = 0;
      let hi = samples.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (samples[mid].y < tip) lo = mid + 1; else hi = mid;
      }
      // Interpolate between samples: snapping to one made the tip saw up and down as you scrolled.
      const a = samples[Math.max(0, lo - 1)];
      const b = samples[lo];
      const f = b.y > a.y ? Math.min(1, Math.max(0, (tip - a.y) / (b.y - a.y))) : 1;
      const drawn = a.len + (b.len - a.len) * f;
      threadPath.style.strokeDashoffset = total - drawn;
      // The pen tip sits on the end of the drawn line (and rests at the shelf once the line is done).
      const p = threadPath.getPointAtLength(drawn);
      // cx/cy rather than a transform, so the gradient ink is read at the tip's real height.
      nibDots.forEach((c) => { c.setAttribute('cx', p.x); c.setAttribute('cy', p.y); });
      nib.classList.toggle('on', drawn > 2);
    }

    ScrollTrigger.addEventListener('refresh', buildThread);
    ScrollTrigger.create({ start: 0, end: 'max', onUpdate: drawThread });
    if (document.fonts) document.fonts.ready.then(() => ScrollTrigger.refresh());

    // Deterministic scatter, so the dots land in the same places on every refresh.
    const rand = (k, s) => { const x = Math.sin(k * 12.9898 + s * 78.233) * 43758.5453; return x - Math.floor(x); };

    function chatReveal(section, phone, desktop) {
      const msgs = $$('.msg', phone);
      const status = $('.chat-status', phone);
      const final = status.textContent;
      const tl = gsap.timeline({
        defaults: { ease: 'none' },
        scrollTrigger: {
          trigger: desktop ? section : phone,
          start: desktop ? 'top 20%' : 'top 75%',
          end: desktop ? 'bottom 85%' : 'bottom 55%',
          scrub: true,
          onUpdate: (self) => { if (final === 'listening…') status.textContent = self.progress > 0.8 ? 'listening…' : 'online'; },
        },
      });
      msgs.forEach((m, i) => tl.fromTo(m, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 1 }, i * 1.5));
      return () => { status.textContent = final; };
    }

    // ---- Scroll-driven scenes (motion allowed only) ----
    const mm = gsap.matchMedia();
    mm.add(
      { desktop: '(min-width: 769px)', motion: '(prefers-reduced-motion: no-preference)' },
      (ctx) => {
        const { desktop, motion } = ctx.conditions;
        root.classList.toggle('motion', motion);
        if (!motion) return;
        root.classList.add('anim');
        const cleanups = [];

        // ===== Act I =====
        gsap.to('.hero-wave .bar', {
          scaleY: 0.05, transformOrigin: '50% 50%', ease: 'none', stagger: { each: 0.012, from: 'edges' },
          scrollTrigger: { trigger: '.hero', start: 'top top', end: '55% top', scrub: true },
        });
        gsap.to('.hero-copy, .scroll-cue', {
          opacity: 0, y: -30, ease: 'none',
          scrollTrigger: { trigger: '.hero', start: '25% top', end: '70% top', scrub: true },
        });

        const lines = $$('.distance .line');
        const dl = gsap.timeline({
          defaults: { ease: 'none' },
          scrollTrigger: desktop
            ? { trigger: '.distance .pin-wrap', start: 'top top', end: '+=220%', pin: true, scrub: true }
            : { trigger: '.distance', start: 'top 65%', end: 'bottom 70%', scrub: true },
        });
        lines.forEach((line, i) => dl.fromTo(line, { opacity: 0.12, y: 18 }, { opacity: 1, y: 0, duration: 1 }, i * 1.3));
        dl.fromTo('#d-min-a', { rotation: 0 }, { rotation: 360, svgOrigin: '170 220', duration: 3.6 }, 0)
          .fromTo('#d-min-b', { rotation: 0 }, { rotation: 360, svgOrigin: '470 220', duration: 3.6 }, 0)
          .fromTo('.distance .call-end', { opacity: 0, scale: 0.5, transformOrigin: '50% 50%' }, { opacity: 1, scale: 1, duration: 0.6 }, 1.5);

        // What fades: each detail arrives, then breaks into dots that drift to the bottom of the screen.
        // Katha, introduced: the dots gather into Katha's voice.
        const pin = $('.fades-pin');
        const dust = $('.dust', pin);
        const details = $$('.detail', pin);
        const chars = details.map((d) => $$('.c', d));
        const flat = chars.flat();
        dust.replaceChildren(...flat.map(() => document.createElement('i')));
        const dots = [...dust.children];
        const targets = $$('.intro-wave circle', pin);
        // Measure every letter and target once per refresh (reads only), instead of once per tween,
        // so building the scene doesn't force a page layout for each of ~85 dots.
        let stamp = 0;
        let measured = -1;
        let pos = null;
        ScrollTrigger.addEventListener('refreshInit', () => { stamp++; });
        const layout = () => {
          if (measured === stamp && pos) return pos;
          const b = pin.getBoundingClientRect();
          const at = (el) => {
            const r = el.getBoundingClientRect();
            return [r.left - b.left + r.width / 2, r.top - b.top + r.height / 2];
          };
          pos = { chars: flat.map(at), targets: targets.map(at), w: b.width, h: b.height };
          measured = stamp;
          return pos;
        };
        const pile = (k) => {
          const { w, h } = layout();
          const r1 = rand(k, 1);
          const hump = Math.exp(-((r1 - 0.5) ** 2) / 0.04);
          return [w * (0.12 + 0.76 * r1), h - 18 - rand(k, 2) * 10 - hump * 30 * rand(k, 3)];
        };
        const fl = gsap.timeline({
          defaults: { ease: 'none' },
          scrollTrigger: { trigger: pin, start: 'top top', end: desktop ? '+=700%' : '+=560%', pin: true, scrub: true, invalidateOnRefresh: true },
        });
        let t = 0.3;
        details.forEach((detail, i) => {
          fl.fromTo(detail, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.6 }, t);
          const tb = t + 1.4;
          fl.to(chars[i], { opacity: 0, duration: 0.6, stagger: { each: 0.012, from: 'random' } }, tb);
          chars[i].forEach((c) => {
            const k = flat.indexOf(c);
            const j = tb + rand(k, 4) * 0.4;
            fl.fromTo(dots[k], { x: () => layout().chars[k][0], y: () => layout().chars[k][1], opacity: 0, scale: 1.8 }, { opacity: 0.9, scale: 1, duration: 0.2 }, j);
            fl.to(dots[k], { x: () => pile(k)[0], y: () => pile(k)[1], duration: 1.1, ease: 'power1.in' }, j + 0.15);
          });
          t = tb + 0.6;
        });
        t += 1.2;
        fl.to('.fades-open', { opacity: 0, duration: 0.6 }, t);
        fl.fromTo('.fades-close', { opacity: 0 }, { opacity: 1, duration: 0.8 }, t + 0.4);
        t += 3;
        fl.to('.fades-close', { opacity: 0, duration: 0.6 }, t);
        fl.fromTo('.fades-glow', { opacity: 0 }, { opacity: 1, duration: 3 }, t);
        dots.forEach((dot, k) => {
          const ti = k % targets.length;
          fl.to(dot, { x: () => layout().targets[ti][0], y: () => layout().targets[ti][1], duration: 1.4, ease: 'power2.inOut' }, t + 0.4 + rand(k, 5) * 0.4);
        });
        t += 2.3;
        fl.fromTo('.intro-wave-svg', { opacity: 0 }, { opacity: 1, duration: 0.4 }, t);
        fl.to(dots, { opacity: 0, duration: 0.3 }, t + 0.3);
        fl.fromTo('.intro > :not(.intro-wave)', { opacity: 0, y: 16 }, { opacity: 1, y: 0, duration: 0.8, stagger: 0.3 }, t + 0.3);
        fl.to({}, { duration: 1.2 });

        gsap.fromTo('.dawn-sun', { yPercent: 45 }, {
          yPercent: -5, ease: 'none',
          scrollTrigger: { trigger: '.dawn', start: 'top bottom', end: 'bottom top', scrub: true },
        });

        // ===== Act II =====
        gsap.fromTo('.phone-setup .field', { opacity: 0, y: 10 }, {
          opacity: 1, y: 0, stagger: 0.4, ease: 'none',
          scrollTrigger: {
            trigger: desktop ? '.setup' : '.phone-setup',
            start: desktop ? 'top 35%' : 'top 75%', end: desktop ? 'center 45%' : 'bottom 70%', scrub: true,
          },
        });
        $$('.consent, .hers').forEach((section) => {
          gsap.fromTo($$('.reveal', section), { opacity: 0.12 }, {
            opacity: 1, stagger: 0.5, ease: 'none',
            scrollTrigger: { trigger: $('.statement', section), start: 'top 80%', end: 'bottom 45%', scrub: true },
          });
        });
        gsap.fromTo('.yeses li', { opacity: 0, y: 12 }, {
          opacity: 1, y: 0, stagger: 0.2, ease: 'none',
          scrollTrigger: { trigger: '.yeses', start: 'top 90%', end: 'top 60%', scrub: true },
        });
        cleanups.push(chatReveal($('.morning'), $('.phone-morning'), desktop));

        // ===== Act III =====
        cleanups.push(chatReveal($('.remembers'), $('.phone-remember'), desktop));

        // ===== Act IV =====
        const page = $('.conv-card');
        const st = gsap.timeline({
          defaults: { ease: 'none' },
          scrollTrigger: desktop
            ? { trigger: '.shape-pin', start: 'top top', end: '+=170%', pin: true, scrub: true, invalidateOnRefresh: true }
            : { trigger: '.conv-card', start: 'top 80%', end: 'bottom 70%', scrub: true },
        });
        st.fromTo($$('.cc-foot'), { opacity: 0 }, { opacity: 1, duration: 0.6 }, 0.2);
        if (desktop) {
          $$('.fragments li').forEach((frag, i) => {
            const towards = (axis) => () => {
              const p = page.getBoundingClientRect();
              const f = frag.getBoundingClientRect();
              const dx = p.left + p.width / 2 - (f.left + f.width / 2);
              const dy = p.top + p.height * (0.35 + 0.1 * (i % 4)) - (f.top + f.height / 2);
              return axis === 'x' ? dx * 0.85 : dy * 0.85;
            };
            st.fromTo(frag, { opacity: 1, x: 0, y: 0, scale: 1 }, { opacity: 0, x: towards('x'), y: towards('y'), scale: 0.8, duration: 1 }, 0.3 + i * 0.25);
          });
        }
        st.fromTo($$('.cc-hook, .cc-excerpt, .cc-call'), { opacity: 0.06 }, { opacity: 1, duration: 0.8, stagger: 0.45 }, 0.9);
        st.to({}, { duration: 0.6 });

        gsap.fromTo('.glasses', { opacity: 0, y: -24, rotation: -4 }, {
          opacity: 1, y: 0, rotation: 0, ease: 'none',
          scrollTrigger: { trigger: '.glasses', start: 'top 95%', end: 'top 65%', scrub: true },
        });

        // Beat 10, after she has said yes: the card lifts out of her phone, follows the line, and lands in
        // the child's phone; the same card by email lands beside it.
        const card = desktop ? '#fw-card' : '#ft-card';
        const cardPath = desktop ? '#fw-path' : '#ft-path';
        // The <use> inside, not the group: GSAP would overwrite the group's translate.
        const env = desktop ? '#fw-env use' : '#ft-env use';
        const ft = gsap.timeline({
          defaults: { ease: 'none' },
          scrollTrigger: desktop
            ? { trigger: '.flight-pin', start: 'top top', end: '+=160%', pin: true, scrub: true }
            : { trigger: '.flight-tall', start: 'top 70%', end: 'bottom 75%', scrub: true },
        });
        if (window.MotionPathPlugin) {
          ft.fromTo(card, { opacity: 0 }, { opacity: 1, duration: 0.2 }, 0)
            .to(card, { motionPath: { path: cardPath, align: cardPath, alignOrigin: [0.5, 0.5] }, duration: 3, ease: 'power1.inOut' }, 0)
            .fromTo(card, { scale: 0.3, transformOrigin: '50% 50%' }, { scale: 1.2, duration: 1.5, ease: 'power1.out' }, 0)
            .to(card, { scale: 0.7, duration: 1.5, ease: 'power1.in' }, 1.5);
        }
        ft.fromTo(env, { opacity: 0, y: -40 }, { opacity: 1, y: 0, duration: 0.6, ease: 'power1.out' }, 2.6);

        // ===== Act V =====
        const spines = $$('.spine');
        const chapters = $$('.chapters li');
        const at = gsap.timeline({
          defaults: { ease: 'none' },
          scrollTrigger: desktop
            ? { trigger: '.archive-pin', start: 'top top', end: '+=230%', pin: true, scrub: true }
            : { trigger: '.archive', start: 'top 55%', end: 'bottom 75%', scrub: true },
        });
        spines.forEach((spine, i) => {
          const body = $('.spine-body', spine);
          at.fromTo(spine, { y: () => body.getBBox().height + 8 }, { y: 0, duration: 0.6 }, i * 0.5);
          at.fromTo(body, { attr: { fill: '#2E2770' } }, { attr: { fill: spine.dataset.lit }, duration: 0.35 }, i * 0.5 + 0.45);
          at.fromTo(chapters[i], { opacity: 0.25 }, { opacity: 1, duration: 0.35 }, i * 0.5 + 0.45);
        });
        at.to({}, { duration: 0.5 });

        // Bookend: a card read years later, then silence, then one line.
        gsap.fromTo('.keep', { opacity: 0, y: 20 }, {
          opacity: 1, y: 0, ease: 'none',
          scrollTrigger: { trigger: '.keep', start: 'top 85%', end: 'top 55%', scrub: true },
        });

        return () => {
          root.classList.remove('anim');
          cleanups.forEach((fn) => fn());
          dust.replaceChildren();
        };
      },
    );
  }
})();
