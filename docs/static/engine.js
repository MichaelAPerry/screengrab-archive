"use strict";
/* In-browser search engine for the static public site.
 * Mirrors archive/search.py: same query syntax, normalisation and ranking heuristics
 * (with an IDF term weight standing in for SQLite's bm25). Data is per-season JSON
 * written by `python -m archive export-static`. */
(function () {
  const TIMING = ["none", "subtitle", "aligned", "manual"];
  const FILTER_KEYS = { speaker: "speaker", who: "speaker", character: "speaker", char: "speaker",
                        episode: "episode", ep: "episode", season: "season", s: "season" };
  const QUOTES = { "‘": "'", "’": "'", "“": '"', "”": '"', "—": " - ", "–": "-", "…": "..." };

  function normalize(text) {
    let t = String(text || "").normalize("NFKC").replace(/[‘’“”—–…]/g, (c) => QUOTES[c]);
    t = t.toLowerCase().replace(/<[^>]+>/g, "").replace(/\[[^\]]*\]|\([^)]*\)/g, " ");
    t = t.replace(/[^\p{L}\p{N}_\s']+/gu, " ").replace(/'/g, "");
    return t.replace(/\s+/g, " ").trim();
  }

  function parseQuery(raw) {
    const q = { words: [], phrases: [], speaker: null, episode: null, season: null };
    for (const m of String(raw || "").matchAll(/(\w+):("[^"]*"|\S+)|"([^"]*)"?|(\S+)/g)) {
      const [, key, val0, phrase, word] = m;
      if (key !== undefined) {
        const target = FILTER_KEYS[key.toLowerCase()], val = val0.replace(/"/g, "");
        if (target === "speaker" && val) q.speaker = val.toUpperCase();
        else if (target === "episode" && /^s?\d{1,2}e\d{1,2}$/i.test(val)) {
          const [s, e] = val.match(/\d+/g).map(Number);
          q.episode = `S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`;
        } else if (target === "season" && /^\d+$/.test(val)) q.season = Number(val);
        else q.words.push(...normalize(`${key} ${val}`).split(" ").filter(Boolean));
      } else if (phrase !== undefined) {
        const n = normalize(phrase);
        if (n.includes(" ")) q.phrases.push(n); else if (n) q.words.push(n);
      } else if (word) q.words.push(...normalize(word).split(" ").filter(Boolean));
    }
    return q;
  }

  const fmt = (ms) => {
    if (ms == null) return "";
    const s = Math.floor(ms / 1000), p = (n) => String(n).padStart(2, "0");
    return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
  };

  // porter-ish leniency: "bird" matches "birds", "birding"; exact tokens always match
  const stem = (w) => { const s = w.replace(/(ing|ed|es|s)$/, ""); return s.length >= 3 ? s : w; };
  const tokenMatches = (tok, w) => {
    if (tok === w) return true;
    const s = stem(w);
    return s.length >= 3 && tok.startsWith(s) && tok.length - s.length <= 3;
  };

  // "S06/S06E05/S06E05_000872345.webp" -> "S06E05-872345" (same rule as archive/share.py)
  function shareKey(image) {
    const m = /(S\d{2}E\d{2})_(\d+)\.\w+$/.exec(image || "");
    return m ? `${m[1]}-${Number(m[2])}` : null;
  }

  class StaticEngine {
    constructor(base) { this.base = base; this.lines = []; this.byId = new Map(); this.byEpisode = new Map(); this.df = new Map(); this.byKey = new Map(); }

    async load() {
      this.meta = await (await fetch(`${this.base}meta.json`)).json();
      const titles = Object.fromEntries(this.meta.episodes.map((e) => [e.episode_code, e]));
      const seasons = await Promise.all(this.meta.season_files.map(async (f) => (await fetch(`${this.base}${f}`)).json()));
      for (const season of seasons) {
        for (const [code, rows] of Object.entries(season.episodes)) {
          const ep = titles[code] || { episode_code: code };
          const list = [];
          for (const [id, seq, speaker, text, start, end, timing, conf, image] of rows) {
            const norm = normalize(text), toks = norm.split(" ").filter(Boolean);
            const line = { id, sequence: seq, speaker, text, start_ms: start, end_ms: end, timing_source: TIMING[timing] || "none",
              timing_confidence: conf, image, episode_code: code, season: ep.season, episode: ep.episode, title: ep.title,
              norm, toks, idx: list.length };
            list.push(line); this.lines.push(line); this.byId.set(id, line);
            const key = shareKey(image); if (key) this.byKey.set(key, id);
            for (const t of new Set(toks)) this.df.set(t, (this.df.get(t) || 0) + 1);
          }
          this.byEpisode.set(code, list);
        }
      }
      return this;
    }

    lineIdForKey(key) { return this.byKey.get(key) ?? null; }

    facets() { return { series: this.meta.series, episodes: this.meta.episodes, speakers: this.meta.speakers }; }

    result(l) {
      return { id: l.id, episode_code: l.episode_code, season: l.season, episode: l.episode, title: l.title,
        sequence: l.sequence, speaker: l.speaker, text: l.text, start_ms: l.start_ms, end_ms: l.end_ms,
        timestamp: fmt(l.start_ms), timing_source: l.timing_source, timing_confidence: l.timing_confidence,
        image: l.image, image_count: l.image ? 1 : 0 };
    }

    search(raw, { speaker, season, episode, limit = 24, offset = 0 } = {}) {
      const q = parseQuery(raw);
      if (speaker) q.speaker = speaker.toUpperCase();
      if (season !== undefined && season !== null && season !== "") q.season = Number(season);
      if (episode) q.episode = parseQuery(`episode:${episode}`).episode || q.episode;
      const filt = (l) => (!q.speaker || l.speaker === q.speaker) && (!q.episode || l.episode_code === q.episode)
        && (q.season === null || l.season === q.season);
      const hasText = q.words.length || q.phrases.length;
      if (!hasText) {
        if (!q.speaker && !q.episode && q.season === null) return { query: raw, total: 0, results: [], mode: "empty" };
        const hits = this.lines.filter(filt);
        return { query: raw, total: hits.length, results: hits.slice(offset, offset + limit).map((l) => this.result(l)), mode: "browse" };
      }
      const N = this.lines.length || 1;
      const idf = (w) => { let df = 0; for (const [t, n] of this.df) if (tokenMatches(t, w)) df += n; return Math.log(1 + N / (1 + df)); };
      const weights = Object.fromEntries(q.words.map((w) => [w, idf(w)]));
      const run = (any) => {
        const out = [];
        for (const l of this.lines) {
          if (!filt(l)) continue;
          const padded = ` ${l.norm} `;
          if (!q.phrases.every((p) => padded.includes(` ${p} `))) continue;
          const hit = q.words.filter((w) => l.toks.some((t) => tokenMatches(t, w)));
          if (any ? (q.words.length && !hit.length) : hit.length < q.words.length) continue;
          out.push([l, hit]);
        }
        return out;
      };
      let mode = "all", hits = run(false);
      if (!hits.length && q.words.length >= 2) { mode = "any"; hits = run(true); }
      const full = [...q.phrases, ...q.words].join(" ");
      const terms = new Set(full.split(" "));
      const score = ([l, hit]) => {
        let s = hit.reduce((a, w) => a + weights[w], 0) + q.phrases.length * 5;
        if (full && ` ${l.norm} `.includes(` ${full} `)) s += 10;
        if (l.norm === full) s += 5;
        s += 3 * Math.max(0, 1 - l.toks.length / 25);
        s += 2 * l.toks.filter((t) => terms.has(t)).length / Math.max(terms.size, 1);
        return s;
      };
      hits = hits.map((h) => [h[0], score(h)]).sort((a, b) => b[1] - a[1]);
      return { query: raw, total: hits.length, results: hits.slice(offset, offset + limit).map(([l]) => this.result(l)), mode };
    }

    dialogue(id, seconds = 10, fallback = 6) {
      const l = this.byId.get(Number(id));
      if (!l) return null;
      const list = this.byEpisode.get(l.episode_code), span = seconds * 1000, ctx = [];
      for (let i = Math.max(0, l.idx - 40); i < Math.min(list.length, l.idx + 41); i++) {
        const w = list[i];
        const keep = l.start_ms != null && w.start_ms != null ? Math.abs(w.start_ms - l.start_ms) <= span : Math.abs(i - l.idx) <= fallback;
        if (keep) ctx.push({ id: w.id, sequence: w.sequence, speaker: w.speaker, text: w.text, start_ms: w.start_ms,
          end_ms: w.end_ms, timing_source: w.timing_source, timestamp: fmt(w.start_ms), selected: w.id === l.id });
      }
      return { line: this.result(l), context: ctx, video_url: null,
        images: l.image ? [{ filename: l.image, timestamp_ms: null, timestamp: "", is_primary: 1 }] : [] };
    }
  }

  window.ArchiveEngine = { StaticEngine, normalize, parseQuery, shareKey };
})();
