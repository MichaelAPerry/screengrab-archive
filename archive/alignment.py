"""Fuzzy alignment of transcript lines (speakers, no timing) to subtitle cues (timing, no speakers).

Wording differs between the two layers, lines are split differently, and either side
can have lines the other lacks. So instead of exact matching we run a monotonic
dynamic-programming alignment (like a diff) where:

  * one transcript line may match a run of 1..MAX_SPAN consecutive cue parts,
    or a run of up to MAX_SPAN short transcript lines may match one cue part,
  * either side may skip items at no cost,
  * a match only counts if its fuzzy similarity clears MIN_SCORE,
  * the search is restricted to a diagonal band for speed.

Each matched line gets start = first cue start, end = last cue end, and the
similarity (0-1) as its timing_confidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from archive.models import Cue
from archive.normalize import normalize
from archive.subtitles import split_dialogue_turns

MAX_SPAN = 3
MIN_SCORE = 0.65
BAND = 0.2      # fraction of the longer sequence
MIN_BAND = 40


@dataclass
class Match:
    line_index: int
    start: int
    end: int
    confidence: float
    cue_speaker: str | None = None


def similarity(a: str, b: str) -> float:
    """0-1 similarity of two already-normalized strings, robust to extra/missing words."""
    if not a or not b:
        return 0.0
    ratio = fuzz.ratio(a, b)
    # token_set_ratio rewards containment ("thats what fog choir practice is for" vs "fog choir")
    # but is too generous for very short strings, so weight it by length balance.
    balance = min(len(a), len(b)) / max(len(a), len(b))
    tset = fuzz.token_set_ratio(a, b) * (0.3 + 0.7 * balance)
    return max(ratio, tset) / 100.0


def expand_cues(cues: list[Cue]) -> list[Cue]:
    out: list[Cue] = []
    for c in cues:
        out.extend(split_dialogue_turns(c))
    return out


def align(lines: list[str], cues: list[Cue]) -> list[Match]:
    """Align transcript texts to cues. Returns one Match per aligned line (others omitted)."""
    parts = expand_cues(cues)
    a = [normalize(t) for t in lines]
    b = [normalize(c.text) for c in parts]
    n, m = len(a), len(b)
    if not n or not m:
        return []

    band = max(MIN_BAND, int(BAND * max(n, m)))
    NEG = float("-inf")
    # score[i][j]: best total using first i lines and first j cue parts
    score = [[NEG] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, float] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    score[0][0] = 0.0

    def in_band(i: int, j: int) -> bool:
        return abs(i * m / n - j) <= band

    for i in range(n + 1):
        for j in range(m + 1):
            cur = score[i][j]
            if cur == NEG or not in_band(i, j):
                continue
            # skip a transcript line
            if i < n and cur > score[i + 1][j]:
                score[i + 1][j], back[i + 1][j] = cur, (i, j, 0.0)
            # skip a cue part
            if j < m and cur > score[i][j + 1]:
                score[i][j + 1], back[i][j + 1] = cur, (i, j, 0.0)
            if i < n and a[i]:
                joined = ""
                for k in range(1, MAX_SPAN + 1):
                    if j + k > m:
                        break
                    joined = (joined + " " + b[j + k - 1]).strip()
                    s = similarity(a[i], joined)
                    if s >= MIN_SCORE:
                        # length-weighted so long confident matches dominate the path
                        gain = s * (1 + min(len(a[i]), 80) / 40)
                        if cur + gain > score[i + 1][j + k]:
                            score[i + 1][j + k], back[i + 1][j + k] = cur + gain, (i, j, s)
                    if len(joined) > len(a[i]) * 1.6 + 10:
                        break
                # several short transcript lines covering one cue part
                if j < m and b[j]:
                    joined = a[i]
                    for k in range(2, MAX_SPAN + 1):
                        if i + k > n:
                            break
                        joined = (joined + " " + a[i + k - 1]).strip()
                        s = similarity(joined, b[j])
                        if s >= MIN_SCORE:
                            gain = s * (1 + min(len(joined), 80) / 40)
                            if cur + gain > score[i + k][j + 1]:
                                score[i + k][j + 1], back[i + k][j + 1] = cur + gain, (i, j, s)
                        if len(joined) > len(b[j]) * 1.6 + 10:
                            break

    # walk back from the best end cell
    i, j = n, m
    if score[n][m] == NEG:
        i, j = max(((x, y) for x in range(n + 1) for y in range(m + 1)), key=lambda p: score[p[0]][p[1]])
    matches: list[Match] = []
    while back[i][j] is not None:
        pi, pj, s = back[i][j]
        if s > 0:
            span = parts[pj:j]
            speakers = {c.speaker for c in span if c.speaker}
            speaker = speakers.pop() if len(speakers) == 1 else None
            if i - pi == 1:
                matches.append(Match(pi, span[0].start, span[-1].end, round(s, 3), speaker))
            else:
                # split the cue's interval between the lines by text length
                lens = [max(len(a[x]), 1) for x in range(pi, i)]
                t, total, dur = float(span[0].start), sum(lens), span[-1].end - span[0].start
                for off, ln in enumerate(lens):
                    d = dur * ln / total
                    matches.append(Match(pi + off, round(t), round(t + d), round(s * 0.9, 3), None))
                    t += d
        i, j = pi, pj
    matches.sort(key=lambda mt: mt.line_index)
    return matches
