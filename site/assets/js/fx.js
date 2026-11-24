// Brausau effects for all pages. Everything here is decoration: the pages
// work and read the same without JavaScript.
(() => {
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = window.matchMedia("(pointer: fine)").matches;
  const rand = (min, max) => min + Math.random() * (max - min);
  const pick = (items) => items[Math.floor(Math.random() * items.length)];
  const PALETTE = ["#d5ff00", "#ff2f92", "#6ff7ff", "#f4efdf"];
  const root = document.documentElement;
  const isDetailPage = Boolean(document.querySelector(".beer-profile"));
  const beerColor = () =>
    getComputedStyle(document.querySelector("main") || root).getPropertyValue("--beer-color").trim() || "#d5ff00";

  // Browser storage can be unavailable (private mode, blocked site data).
  const store = {
    get(key, fallback) {
      try {
        const value = window.localStorage.getItem(key);
        return value === null ? fallback : JSON.parse(value);
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        window.localStorage.setItem(key, JSON.stringify(value));
      } catch {
        // Not saved; the effect just won't be remembered.
      }
    },
  };

  // --- Sound (synthesised with Web Audio; off until switched on) -----------
  let soundOn = store.get("brausau-sound", false);
  let audio = null;

  function ctx() {
    if (!soundOn) return null;
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return null;
    if (!audio) {
      audio = new AudioContext({ latencyHint: "interactive" });
      loadSoundFiles(audio);
    }
    if (audio.state === "suspended") audio.resume();
    return audio;
  }

  function noise(ac, seconds) {
    const buffer = ac.createBuffer(1, Math.ceil(ac.sampleRate * seconds), ac.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    const source = ac.createBufferSource();
    source.buffer = buffer;
    return source;
  }

  function envelope(ac, start, peak, decay) {
    const gain = ac.createGain();
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(peak, start + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + decay);
    gain.connect(ac.destination);
    return gain;
  }

  function tone(ac, { from, to = from, start = ac.currentTime, glide = 0.1, peak = 0.2, decay = 0.3, type = "sine" }) {
    const osc = ac.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(from, start);
    osc.frequency.exponentialRampToValueAtTime(to, start + glide);
    osc.connect(envelope(ac, start, peak, decay));
    osc.start(start);
    osc.stop(start + decay + 0.05);
  }

  // Synthesised sounds: used for events without a recording, and as a
  // fallback when a sound file can't be loaded.
  const synth = {
    // Opening a bottle: a small metallic tick, then a steady hiss of gas that
    // fades out (no pitch sweep, so it doesn't "fly").
    pssst() {
      const ac = ctx();
      if (!ac) return;
      const t = ac.currentTime;
      const tick = noise(ac, 0.02);
      const tickFilter = ac.createBiquadFilter();
      tickFilter.type = "highpass";
      tickFilter.frequency.value = 2000;
      tick.connect(tickFilter).connect(envelope(ac, t, 0.35, 0.02));
      tick.start(t);
      tone(ac, { from: 2600, start: t, peak: 0.04, decay: 0.05, type: "triangle" });

      const hiss = noise(ac, 0.9);
      const highpass = ac.createBiquadFilter();
      highpass.type = "highpass";
      highpass.frequency.value = 2500;
      const band = ac.createBiquadFilter();
      band.type = "bandpass";
      band.frequency.value = 5500;
      band.Q.value = 0.6;
      const gain = ac.createGain();
      const start = t + 0.015;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.6, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.28, start + 0.25);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.8);
      hiss.connect(highpass).connect(band).connect(gain).connect(ac.destination);
      hiss.start(start);
    },
    // Two glasses: bright partials, the second glass slightly detuned and late.
    clink() {
      const ac = ctx();
      if (!ac) return;
      const t = ac.currentTime;
      [[0, 1], [0.014, 1.035]].forEach(([delay, detune]) => {
        [2093, 3136, 4230, 5274].forEach((freq, i) => {
          tone(ac, { from: freq * detune, start: t + delay, peak: 0.12 / (i + 1), decay: 1.4 - i * 0.25 });
        });
      });
    },
    // Crown caps rattling around in the box.
    rattle() {
      const ac = ctx();
      if (!ac) return;
      const t = ac.currentTime;
      for (let i = 0; i < 14; i++) {
        const start = t + rand(0, 0.55);
        const click = noise(ac, 0.03);
        const filter = ac.createBiquadFilter();
        filter.type = "highpass";
        filter.frequency.value = rand(2500, 5000);
        click.connect(filter).connect(envelope(ac, start, rand(0.15, 0.35), 0.03));
        click.start(start);
        tone(ac, { from: rand(3000, 4600), start, peak: 0.03, decay: 0.08, type: "triangle" });
      }
    },
    // A burp: a low, rattling buzz shaped by a mouth-like filter. Every one
    // is a little different.
    burp() {
      const ac = ctx();
      if (!ac) return;
      const t = ac.currentTime;
      const length = rand(0.35, 0.75);
      const base = rand(70, 115);

      const voice = ac.createOscillator();
      voice.type = "sawtooth";
      voice.frequency.setValueAtTime(base * 1.3, t);
      voice.frequency.linearRampToValueAtTime(base, t + 0.07);
      voice.frequency.linearRampToValueAtTime(base * rand(0.7, 0.9), t + length);

      // Irregular flutter of the throat: amplitude modulation at 20-40 Hz.
      const throat = ac.createGain();
      throat.gain.value = 0.55;
      const flutter = ac.createOscillator();
      flutter.type = "square";
      flutter.frequency.setValueAtTime(rand(20, 40), t);
      flutter.frequency.linearRampToValueAtTime(rand(14, 26), t + length);
      const flutterDepth = ac.createGain();
      flutterDepth.gain.value = 0.45;
      flutter.connect(flutterDepth).connect(throat.gain);

      const mouth = ac.createBiquadFilter();
      mouth.type = "bandpass";
      mouth.Q.value = 2.2;
      mouth.frequency.setValueAtTime(rand(420, 560), t);
      mouth.frequency.linearRampToValueAtTime(rand(260, 360), t + length);
      const chest = ac.createBiquadFilter();
      chest.type = "lowpass";
      chest.frequency.value = 320;
      const chestLevel = ac.createGain();
      chestLevel.gain.value = 0.35;

      const env = ac.createGain();
      env.gain.setValueAtTime(0.0001, t);
      env.gain.exponentialRampToValueAtTime(1, t + 0.03);
      env.gain.setValueAtTime(1, t + length * 0.55);
      env.gain.exponentialRampToValueAtTime(0.0001, t + length);
      env.connect(ac.destination);

      voice.connect(throat);
      throat.connect(mouth).connect(env);
      throat.connect(chest).connect(chestLevel).connect(env);
      voice.start(t);
      flutter.start(t);
      voice.stop(t + length + 0.05);
      flutter.stop(t + length + 0.05);
    },
    thump() {
      const ac = ctx();
      if (!ac) return;
      const t = ac.currentTime;
      tone(ac, { from: 150, to: 42, glide: 0.22, peak: 0.7, decay: 0.3, start: t });
      const smack = noise(ac, 0.08);
      const filter = ac.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = 900;
      smack.connect(filter).connect(envelope(ac, t, 0.5, 0.08));
      smack.start(t);
    },
  };

  // Recorded sounds in assets/sounds/. On load, each file is trimmed to start
  // at its first audible sample (so it plays right on the click), levelled
  // to the same peak volume, and optionally cut short with a fade-out.
  const SOUND_DIR = new URL("../sounds/", document.currentScript?.src || window.location.href);
  const SOUND_FILES = {
    pssst: { files: ["bottle-opening.mp3"], volume: 0.9 },
    burp: { files: ["burp.mp3"], volume: 1, maxLength: 1.6, oneAtATime: true },
    clink: { files: ["clink.mp3"], volume: 0.8 },
  };
  const ONSET_THRESHOLD = 0.06; // relative to the file's peak (about -24 dB)
  const PRE_ROLL = 0.004; // seconds kept before the first audible sample
  const recordings = {};
  const playing = {};

  function analyse(buffer) {
    let peak = 0;
    for (let c = 0; c < buffer.numberOfChannels; c++) {
      for (const sample of buffer.getChannelData(c)) peak = Math.max(peak, Math.abs(sample));
    }
    let onset = buffer.length;
    for (let c = 0; c < buffer.numberOfChannels; c++) {
      const data = buffer.getChannelData(c);
      for (let i = 0; i < Math.min(onset, data.length); i++) {
        if (Math.abs(data[i]) >= peak * ONSET_THRESHOLD) {
          onset = i;
          break;
        }
      }
    }
    return {
      buffer,
      offset: Math.max(0, onset / buffer.sampleRate - PRE_ROLL),
      gain: peak > 0 ? Math.min(4, 0.9 / peak) : 1,
    };
  }

  function loadSoundFiles(ac) {
    for (const [name, { files }] of Object.entries(SOUND_FILES)) {
      recordings[name] = Promise.all(
        files.map((file) =>
          fetch(new URL(file, SOUND_DIR))
            .then((response) => {
              if (!response.ok) throw new Error(`${file}: HTTP ${response.status}`);
              return response.arrayBuffer();
            })
            .then((data) => ac.decodeAudioData(data))
            .then(analyse)
            .catch(() => null)
        )
      ).then((loaded) => loaded.filter(Boolean));
    }
  }

  function playRecording(name, fallback) {
    const ac = ctx();
    if (!ac) return;
    const settings = SOUND_FILES[name];
    recordings[name].then((loaded) => {
      if (!loaded.length) return fallback();
      const { buffer, offset, gain } = pick(loaded);
      const start = ac.currentTime;
      const remaining = buffer.duration - offset;
      const length = Math.min(remaining, settings.maxLength || remaining);

      if (settings.oneAtATime && playing[name]) {
        const previous = playing[name];
        previous.gain.gain.setTargetAtTime(0.0001, start, 0.02);
        previous.source.stop(start + 0.1);
      }

      const source = ac.createBufferSource();
      source.buffer = buffer;
      const level = ac.createGain();
      const volume = gain * settings.volume;
      level.gain.setValueAtTime(volume, start);
      if (length < remaining) {
        const fade = Math.min(0.25, length / 3);
        level.gain.setValueAtTime(volume, start + length - fade);
        level.gain.linearRampToValueAtTime(0.0001, start + length);
      }
      source.connect(level).connect(ac.destination);
      source.start(start, offset, length);
      playing[name] = { source, gain: level };
      source.addEventListener("ended", () => {
        if (playing[name]?.source === source) delete playing[name];
      });
    });
  }

  const sfx = {
    pssst: () => playRecording("pssst", synth.pssst),
    burp: () => playRecording("burp", synth.burp),
    clink: () => playRecording("clink", synth.clink),
    rattle: () => synth.rattle(),
    thump: () => synth.thump(),
  };

  // Start decoding the files at the first touch or key press, so they are
  // ready by the time the click that plays them arrives.
  const warmUp = () => { if (soundOn) ctx(); };
  window.addEventListener("pointerdown", warmUp, { capture: true, passive: true });
  window.addEventListener("keydown", warmUp, { capture: true });

  // --- Confetti and click bubbles ------------------------------------------
  function burst(x, y, color, count = 34) {
    if (calm) return;
    for (let i = 0; i < count; i++) {
      const particle = document.createElement("span");
      particle.className = `fx-particle ${pick(["", "round", "round", "star"])}`;
      const angle = rand(0, Math.PI * 2);
      const distance = rand(60, 240);
      particle.style.cssText = [
        `--x: ${x}px`,
        `--y: ${y}px`,
        `--s: ${rand(7, 16).toFixed(0)}px`,
        `--c: ${i % 3 === 0 ? color : pick(PALETTE)}`,
        `--dx: ${(Math.cos(angle) * distance).toFixed(0)}px`,
        `--dy: ${(Math.sin(angle) * distance + 90).toFixed(0)}px`, // a bit of gravity
        `--rot: ${rand(-720, 720).toFixed(0)}deg`,
        `--d: ${rand(0.6, 1.1).toFixed(2)}s`,
      ].join(";");
      particle.addEventListener("animationend", () => particle.remove());
      document.body.appendChild(particle);
    }
  }

  function bubbles(x, y, color) {
    if (calm) return;
    for (let i = 0; i < 9; i++) {
      const bubble = document.createElement("span");
      bubble.className = "fx-bubble";
      bubble.style.cssText = [
        `--x: ${(x + rand(-14, 14)).toFixed(0)}px`,
        `--y: ${(y + rand(-6, 6)).toFixed(0)}px`,
        `--s: ${rand(6, 18).toFixed(0)}px`,
        `--c: ${color}`,
        `--rise: ${rand(-90, -190).toFixed(0)}px`,
        `--drift: ${rand(-30, 30).toFixed(0)}px`,
        `--d: ${rand(0.7, 1.3).toFixed(2)}s`,
        `animation-delay: ${rand(0, 0.15).toFixed(2)}s`,
      ].join(";");
      bubble.addEventListener("animationend", () => bubble.remove());
      document.body.appendChild(bubble);
    }
  }

  const SPECIAL_CLICKS = ".beer-card, .shake, .fx-prost, .fx-dock, .stamp, .viva";
  document.addEventListener("click", (event) => {
    if (event.target.closest(SPECIAL_CLICKS)) return;
    bubbles(event.clientX, event.clientY, beerColor());
    sfx.burp();
  });

  // "Prost!" stickers clink like glasses.
  document.querySelectorAll(".hs-a, .tag-b, .sticker-b").forEach((sticker) => {
    sticker.classList.add("fx-prost");
    sticker.addEventListener("click", () => {
      const box = sticker.getBoundingClientRect();
      burst(box.left + box.width / 2, box.top + box.height / 2, beerColor(), 22);
      sfx.clink();
      sticker.classList.remove("fx-clinked");
      void sticker.offsetWidth;
      sticker.classList.add("fx-clinked");
    });
  });

  // Plain left-clicks on these links get an effect before navigating;
  // modified clicks (new tab, etc.) behave normally.
  function delayedNavigation(link, event, effect, delay) {
    const plainClick = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    if (calm || !plainClick) return;
    event.preventDefault();
    effect();
    // With sound on, wait a little longer so the hiss isn't cut off.
    setTimeout(() => { window.location.href = link.href; }, soundOn ? delay + 250 : delay);
  }

  const viva = document.querySelector(".viva");
  if (viva) {
    viva.addEventListener("click", (event) => {
      delayedNavigation(viva, event, () => {
        const box = viva.getBoundingClientRect();
        burst(box.left + box.width / 2, box.top + box.height / 2, "#ff2f92", 40);
        sfx.pssst();
      }, 420);
    });
  }

  // --- A crown cap that follows the cursor ---------------------------------
  if (finePointer && !calm) {
    const follower = document.createElement("span");
    follower.className = "fx-follower";
    follower.setAttribute("aria-hidden", "true");
    follower.innerHTML = "<span></span>";
    document.body.appendChild(follower);
    const target = { x: -100, y: -100 };
    const pos = { x: -100, y: -100 };
    let spin = 0;
    let running = false;
    const step = () => {
      const dx = target.x - pos.x;
      const dy = target.y - pos.y;
      pos.x += dx * 0.14;
      pos.y += dy * 0.14;
      spin += dx * 0.35;
      follower.style.transform = `translate(${pos.x.toFixed(1)}px, ${pos.y.toFixed(1)}px) rotate(${spin.toFixed(0)}deg)`;
      if (Math.abs(dx) + Math.abs(dy) > 0.3) {
        requestAnimationFrame(step);
      } else {
        running = false;
      }
    };
    document.addEventListener("pointermove", (event) => {
      if (event.pointerType !== "mouse") return;
      target.x = event.clientX + 18;
      target.y = event.clientY + 18;
      follower.style.setProperty("--c", beerColor());
      follower.classList.add("visible");
      if (!running) {
        running = true;
        requestAnimationFrame(step);
      }
    });
    document.addEventListener("pointerleave", () => follower.classList.remove("visible"));
    document.documentElement.addEventListener("mouseleave", () => follower.classList.remove("visible"));
  }

  // --- Drunk mode ----------------------------------------------------------
  // Every opened beer within two hours raises the level; the page starts to
  // sway, see double, and finally blur. "Ausnüchtern" resets it.
  const VISITS_KEY = "brausau-visits";
  const WINDOW_MS = 2 * 60 * 60 * 1000;
  const now = Date.now();
  let visits = store.get(VISITS_KEY, []).filter((time) => typeof time === "number" && now - time < WINDOW_MS);
  if (isDetailPage) visits.push(now);
  store.set(VISITS_KEY, visits);
  const levelFor = (count) => (count >= 10 ? 3 : count >= 6 ? 2 : count >= 3 ? 1 : 0);
  let level = levelFor(visits.length);
  let swayRunning = false;

  function sway(time) {
    if (level === 0) {
      swayRunning = false;
      ["--tipsy-rot", "--tipsy-x", "--tipsy-ghost", "--tipsy-blur", "--tipsy-hue"].forEach((name) => root.style.removeProperty(name));
      return;
    }
    const t = time / 1000;
    const strength = [0, 0.6, 1.3, 2.2][level];
    root.style.setProperty("--tipsy-rot", `${(Math.sin(t * 0.9) * strength).toFixed(2)}deg`);
    root.style.setProperty("--tipsy-x", `${(Math.sin(t * 0.6 + 1) * strength * 9).toFixed(1)}px`);
    root.style.setProperty("--tipsy-ghost", `${(Math.sin(t * 1.3) * strength * 5 + strength * 2).toFixed(1)}px`);
    root.style.setProperty("--tipsy-blur", `${Math.max(0, Math.sin(t * 0.7) * 1.6).toFixed(2)}px`);
    root.style.setProperty("--tipsy-hue", `${(Math.sin(t * 0.4) * 25).toFixed(0)}deg`);
    requestAnimationFrame(sway);
  }

  function applyLevel() {
    root.classList.remove("tipsy-1", "tipsy-2", "tipsy-3");
    if (level > 0 && !calm) root.classList.add(`tipsy-${level}`);
    if (level > 0 && !calm && !swayRunning) {
      swayRunning = true;
      requestAnimationFrame(sway);
    }
    updateDock();
  }

  // --- Control dock: sound switch and drunk level --------------------------
  const dock = document.createElement("div");
  dock.className = "fx-dock";
  dock.innerHTML = `
    <button type="button" class="fx-sound" aria-pressed="false"></button>
    <span class="fx-level" hidden>
      <span class="fx-level-label"></span>
      <button type="button" class="fx-sober">Ausnüchtern</button>
    </span>`;
  document.body.appendChild(dock);
  const soundButton = dock.querySelector(".fx-sound");
  const levelBox = dock.querySelector(".fx-level");

  function updateDock() {
    soundButton.textContent = soundOn ? "🔊 Ton an" : "🔇 Ton aus";
    soundButton.setAttribute("aria-pressed", String(soundOn));
    levelBox.hidden = level === 0;
    const label = ["", "Angeheitert", "Beschwipst", "Voll wie 1000 Mann"][level];
    const labelBox = levelBox.querySelector(".fx-level-label");
    labelBox.textContent = "🍺".repeat(level);
    labelBox.setAttribute("aria-label", label);
    const text = document.createElement("span");
    text.className = "fx-level-text";
    text.textContent = ` ${label}`;
    labelBox.appendChild(text);
  }

  soundButton.addEventListener("click", () => {
    soundOn = !soundOn;
    store.set("brausau-sound", soundOn);
    updateDock();
    sfx.clink();
  });
  dock.querySelector(".fx-sober").addEventListener("click", () => {
    visits = [];
    store.set(VISITS_KEY, visits);
    level = 0;
    applyLevel();
    const box = soundButton.getBoundingClientRect();
    bubbles(box.left + box.width / 2, box.bottom, "#6ff7ff");
    sfx.burp();
  });
  applyLevel();

  // --- Beer list ------------------------------------------------------------
  const grid = document.getElementById("beer-grid");
  if (grid) {
    const scatter = () => {
      const chaos = window.matchMedia("(max-width: 620px)").matches ? 0.55 : 1;
      const cards = Array.from(grid.children);
      for (let i = cards.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [cards[i], cards[j]] = [cards[j], cards[i]];
      }
      cards.forEach((card) => {
        const set = (name, value) => card.style.setProperty(name, value);
        set("--tilt", `${(rand(-15, 15) * chaos).toFixed(1)}deg`);
        set("--shift-x", `${(rand(-20, 20) * chaos).toFixed(1)}px`);
        set("--shift-y", `${(rand(-18, 18) * chaos).toFixed(1)}px`);
        set("--card-scale", (1 + rand(-0.21, 0.21) * chaos).toFixed(2));
        set("--cap-rotate", `${rand(0, 360).toFixed(0)}deg`);
        set("--wobble", `${(rand(8, 34) * pick([-1, 1])).toFixed(0)}deg`);
        set("--wobble-duration", `${rand(2.8, 6.6).toFixed(2)}s`);
        set("--wobble-delay", `${(-rand(0, 6)).toFixed(2)}s`);
        set("--glow-duration", `${rand(3.2, 6.2).toFixed(2)}s`);
        set("--glow-delay", `${(-rand(0, 5)).toFixed(2)}s`);
        grid.appendChild(card);
      });
    };
    scatter();

    // "Schütteln!" shakes the board and deals the caps again.
    const shakeButton = document.getElementById("shake");
    if (shakeButton) {
      shakeButton.hidden = false;
      shakeButton.addEventListener("click", () => {
        sfx.rattle();
        if (calm) return scatter();
        grid.classList.remove("shaking");
        void grid.offsetWidth; // restart the animation
        grid.classList.add("shaking");
        setTimeout(scatter, 260);
        const box = shakeButton.getBoundingClientRect();
        burst(box.left + box.width / 2, box.top + box.height / 2, "#ff2f92", 18);
      });
      grid.addEventListener("animationend", (event) => {
        if (event.animationName === "gridShake") grid.classList.remove("shaking");
      });
    }

    // Caps tilt towards the pointer.
    grid.addEventListener("pointermove", (event) => {
      const card = event.target.closest(".beer-card");
      if (!card || calm) return;
      const box = card.getBoundingClientRect();
      const x = (event.clientX - box.left) / box.width - 0.5;
      const y = (event.clientY - box.top) / box.height - 0.5;
      card.style.setProperty("--rx", `${(-y * 50).toFixed(1)}deg`);
      card.style.setProperty("--ry", `${(x * 50).toFixed(1)}deg`);
    });
    grid.addEventListener("pointerout", (event) => {
      const card = event.target.closest(".beer-card");
      if (card && !card.contains(event.relatedTarget)) {
        card.style.removeProperty("--rx");
        card.style.removeProperty("--ry");
      }
    });

    // Clicking a cap pops it off the board before the detail page opens.
    grid.addEventListener("click", (event) => {
      const card = event.target.closest(".beer-card");
      if (!card) return;
      delayedNavigation(card, event, () => {
        const box = card.getBoundingClientRect();
        const color = getComputedStyle(card).getPropertyValue("--beer-color").trim() || "#d5ff00";
        card.classList.add("popped");
        burst(box.left + box.width / 2, box.top + box.height / 2, color);
        sfx.pssst();
      }, 560);
    });
    // Coming back with the browser's back button restores the page from
    // cache, popped cap included, so put it back on the board.
    window.addEventListener("pageshow", () => {
      grid.querySelectorAll(".popped").forEach((card) => card.classList.remove("popped"));
    });
  }

  // --- Beer detail ----------------------------------------------------------
  // Ransom-note title: every letter cut from a different magazine.
  const title = document.querySelector(".hero h1");
  if (title) {
    const text = title.textContent.trim();
    title.setAttribute("aria-label", text);
    title.textContent = "";
    text.split(/(\s+)/).forEach((part) => {
      if (/^\s+$/.test(part)) {
        title.append(" ");
        return;
      }
      const word = document.createElement("span");
      word.className = "ransom-word";
      word.setAttribute("aria-hidden", "true");
      for (const char of part) {
        const letter = document.createElement("span");
        letter.className = `ransom ${pick(["", "", "", "r1", "r2", "r3", "r4"])}`.trim();
        letter.textContent = char;
        letter.style.setProperty("--r", `${rand(-9, 9).toFixed(1)}deg`);
        letter.style.setProperty("--y", `${rand(-0.06, 0.06).toFixed(3)}em`);
        word.appendChild(letter);
      }
      title.appendChild(word);
    });
  }

  // Bubbles rising in the glass, in the beer's colour.
  const glass = document.querySelector(".glass-visual");
  if (glass && !calm) {
    const glassBubbles = document.createElement("span");
    glassBubbles.className = "bubbles";
    glassBubbles.setAttribute("aria-hidden", "true");
    for (let i = 0; i < 18; i++) {
      const bubble = document.createElement("span");
      bubble.style.cssText = [
        `--x: ${rand(28, 72).toFixed(0)}%`,
        `--size: ${rand(5, 15).toFixed(0)}px`,
        `--dur: ${rand(2.6, 6).toFixed(2)}s`,
        `--delay: ${(-rand(0, 6)).toFixed(2)}s`,
        `--drift: ${rand(-26, 26).toFixed(0)}px`,
      ].join(";");
      glassBubbles.appendChild(bubble);
    }
    glass.appendChild(glassBubbles);
  }

  // The stamp can be slammed again (with a thump when sound is on).
  const stamp = document.querySelector(".stamp");
  if (stamp) {
    stamp.addEventListener("click", () => {
      sfx.thump();
      if (calm) return;
      const main = document.querySelector("main");
      [stamp, main].forEach((el) => {
        el.style.animation = "none";
        void el.offsetWidth;
        el.style.animation = "";
      });
      stamp.style.animationDelay = "0s";
      main.style.animationDelay = ".42s";
    });
  }

  // Plain numbers (Sud, Alc, IBU, …) count up; dates and text stay as they are.
  if (!calm) {
    document.querySelectorAll(".detail dd").forEach((dd) => {
      const match = dd.textContent.match(/^(\d+)(?:([.,])(\d+))?(\D*)$/);
      if (!match) return;
      const original = dd.textContent;
      const [, whole, separator = "", fraction = "", unit] = match;
      const target = parseFloat(`${whole}.${fraction || 0}`);
      const started = performance.now() + 500;
      const duration = 1100;
      const tick = (now) => {
        const progress = Math.min(Math.max((now - started) / duration, 0), 1);
        const eased = 1 - (1 - progress) ** 4;
        const value = (target * eased).toFixed(fraction.length);
        dd.textContent = progress < 1
          ? (separator ? value.replace(".", separator) : value) + unit
          : original;
        if (progress < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      // If animation frames are paused (e.g. a background tab), never leave a
      // half-counted number behind.
      setTimeout(() => { dd.textContent = original; }, 500 + duration + 400);
    });
  }
})();
