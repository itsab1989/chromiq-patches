"""Contrast-maximising patch shuffle for the "shuffled copy" export.

i1Profiler lays an imported patch set out exactly in list order and has no
randomise/optimise step for imports, so a generator-ordered chart (smooth
ramps, cube order) prints with long runs of near-identical neighbours — the
hardest possible layout to measure reliably. The optional shuffled copy
re-orders the patch DATA (the .ti1 list itself, not just the on-sheet
placement), so every downstream file — TIFF pages, colour list, i1Profiler
.txt/.pxf — carries the mixed order.

The permutation is optimised, not random: it maximises the smallest RGB
distance between any two patches that end up physically adjacent (consecutive
within a strip, and side-by-side across neighbouring strips), and it is scored
with the same strip metrics ChromIQ's tag-as-randomised gate uses — strip vs.
its own reverse ("symmetry", reading direction decidable) and pairwise strip
distance ("confusability", the right strip decidable). Distances are Euclidean
RGB on the 0..100 device scale, matching those calibrated thresholds.

Deterministic: the same patch set and strip length always yield the same
shuffled order (fixed base seed), so re-saving a chart reproduces its copy.
"""
from __future__ import annotations

import random
import time

import numpy as np

# Same calibrated floors as workflow.ti2_relayout's tag-as-randomised gate
# (_SYM_THRESHOLD / _CONF_THRESHOLD) — used for reporting, not as hard gates:
# a patch set of near-identical colours can't be fixed by ordering alone.
SYM_FLOOR = 25.0
CONF_FLOOR = 40.0

_BASE_SEED = 0x5EED
_REPAIR_BUDGET_S = 1.5  # hard time cap on the swap-repair pass
_GOOD_ENOUGH = 55.0     # stop repairing once the worst neighbour is this far


def _strip_chunks(arr: np.ndarray, steps: int) -> list[np.ndarray]:
    return [arr[i:i + steps] for i in range(0, len(arr), steps)]


def strip_scores(arr: np.ndarray, steps: int) -> tuple[float, float]:
    """(min_symmetry, min_confusability) — the analyzer math on an ordered
    patch array, with strips = consecutive chunks of *steps* patches."""
    chunks = [c for c in _strip_chunks(arr, steps) if len(c) >= 2]
    if len(chunks) < 2:
        return float("inf"), float("inf")
    min_sym = min(float(np.linalg.norm(c - c[::-1], axis=1).mean())
                  for c in chunks)
    length = min(len(c) for c in chunks)
    st = np.stack([c[:length] for c in chunks])
    rev = st[:, ::-1, :]
    min_conf = float("inf")
    for i in range(len(st)):
        d_fwd = np.linalg.norm(st - st[i], axis=2).mean(axis=1)
        d_rev = np.linalg.norm(rev - st[i], axis=2).mean(axis=1)
        d_fwd[i] = np.inf
        min_conf = min(min_conf, float(d_fwd.min()), float(d_rev.min()))
    return min_sym, min_conf


def _edge_dists(arr: np.ndarray, steps: int) -> np.ndarray:
    """Distances of all physically adjacent pairs: consecutive within a strip
    plus same-row patches of neighbouring strips."""
    n = len(arr)
    parts: list[np.ndarray] = []
    if n >= 2:
        seq = np.linalg.norm(arr[1:] - arr[:-1], axis=1)
        keep = (np.arange(1, n) % steps) != 0   # drop pairs spanning a strip break
        parts.append(seq[keep])
    if n > steps:
        parts.append(np.linalg.norm(arr[steps:] - arr[:-steps], axis=1))
    if not parts or not sum(len(p) for p in parts):
        return np.array([float("inf")])
    return np.concatenate(parts)


def min_neighbour_contrast(arr: np.ndarray, steps: int) -> float:
    return float(_edge_dists(arr, steps).min())


def _neighbour_positions(n: int, steps: int, p: int) -> list[int]:
    nb = []
    if p % steps != 0:
        nb.append(p - 1)
    if (p + 1) % steps != 0 and p + 1 < n:
        nb.append(p + 1)
    if p - steps >= 0:
        nb.append(p - steps)
    if p + steps < n:
        nb.append(p + steps)
    return nb


# Descending contrast-threshold ladder for the constrained-random builder.
# τ=0 degenerates to a plain uniform shuffle, so the ladder always ends in
# the pure-random baseline.
_TAU_LADDER = (60.0, 50.0, 42.0, 35.0, 28.0, 22.0, 17.0, 12.0, 8.0,
               5.0, 3.0, 1.5, 0.0)


def _constrained_random(pts: np.ndarray, steps: int, rng: random.Random,
                        tau: float) -> tuple[np.ndarray, int]:
    """Fill the reading order front-to-back, picking *uniformly at random*
    among the unused patches at least *tau* away from every already-placed
    physical neighbour (previous patch in the strip, same-row patch of the
    previous strip) and 2/3·tau from the patch two back (anti A-B-A).

    Uniform-above-threshold is the crucial difference from a max-min greedy:
    always chasing the farthest candidate converges to a quasi-periodic
    alternation of extremes, which is exactly what the strip metrics punish
    (an alternating strip reads the same reversed, and neighbouring strips
    come out alike). A plain random shuffle clears the strip floors easily —
    this builder keeps that property and only prunes low-contrast neighbours.

    When no candidate clears *tau* the farthest one is taken and counted as a
    violation; the caller walks *tau* down until a build has none."""
    n = len(pts)
    alive = np.ones(n, dtype=bool)
    idx = np.arange(n)
    order = np.empty(n, dtype=int)
    first = rng.randrange(n)
    order[0] = first
    alive[first] = False
    violations = 0
    for pos in range(1, n):
        cand = idx[alive]
        pool = pts[cand]
        d = np.full(len(cand), np.inf)
        if pos % steps != 0:
            d = np.minimum(d, np.linalg.norm(pool - pts[order[pos - 1]], axis=1))
            if pos % steps >= 2:
                d = np.minimum(d, 1.5 * np.linalg.norm(
                    pool - pts[order[pos - 2]], axis=1))
        if pos >= steps:
            d = np.minimum(d, np.linalg.norm(pool - pts[order[pos - steps]], axis=1))
        ok = np.flatnonzero(d >= tau)
        if len(ok):
            pick = cand[ok[rng.randrange(len(ok))]]
        elif np.isfinite(d).any():
            violations += 1
            pick = cand[int(np.argmax(d))]
        else:
            pick = cand[rng.randrange(len(cand))]
        order[pos] = pick
        alive[pick] = False
    return order, violations


def _repair(pts: np.ndarray, order: np.ndarray, steps: int,
            rng: random.Random) -> np.ndarray:
    """Swap-based hill climb on the weakest adjacency. The greedy pass runs
    out of good choices near the end of the order, so its global minimum sits
    in the tail — take the worst adjacent pair and look for a position swap
    that lifts it without creating a new pair below the old minimum."""
    order = order.copy()
    arr = pts[order]
    n = len(arr)
    if n < 4:
        return order

    def local_min(p: int, q: int | None = None) -> float:
        m = float("inf")
        for a in ((p,) if q is None else (p, q)):
            for b in _neighbour_positions(n, steps, a):
                m = min(m, float(np.linalg.norm(arr[a] - arr[b])))
        return m

    # Rounds-capped so the result stays deterministic; the wall-clock deadline
    # is only a safety valve for absurdly large charts.
    deadline = time.monotonic() + _REPAIR_BUDGET_S
    seq_pairs = [(i, i + 1) for i in range(n - 1) if (i + 1) % steps != 0]
    lat_pairs = [(i, i + steps) for i in range(n - steps)]
    all_pairs = seq_pairs + lat_pairs
    for _ in range(400):
        if time.monotonic() > deadline:
            break
        dists = _edge_dists(arr, steps)
        worst = float(dists.min())
        if worst >= _GOOD_ENOUGH:
            break
        p, q = all_pairs[int(np.argmin(dists))]
        best_gain, best_swap = worst, None
        candidates = {rng.randrange(n) for _ in range(96)}
        for e in (p, q):
            for c in candidates:
                if c == e:
                    continue
                arr[e], arr[c] = arr[c].copy(), arr[e].copy()
                new_min = local_min(e, c)
                arr[e], arr[c] = arr[c].copy(), arr[e].copy()
                if new_min > best_gain:
                    best_gain, best_swap = new_min, (e, c)
        if best_swap is None:
            break
        e, c = best_swap
        order[e], order[c] = order[c], order[e]
        arr[e], arr[c] = arr[c].copy(), arr[e].copy()
    return order


def contrast_shuffle(program: list[tuple], steps: int,
                     *, seed: int | None = None) -> list[tuple]:
    """Return *program* (list of 0..100 device tuples) re-ordered for maximum
    contrast between physically adjacent patches and between whole strips.

    *steps* is the number of patches per strip (the engine layout's
    ``steps_in_pass``); pass ``len(program)`` when unknown — the order then
    optimises consecutive contrast only.
    """
    pts = np.asarray([tuple(p)[:3] for p in program], dtype=float)
    n = len(pts)
    if n < 3:
        return list(program)
    steps = max(1, int(steps) if steps else n)
    base = _BASE_SEED if seed is None else int(seed)

    best_order, best_key = None, None
    for t, tau in enumerate(_TAU_LADDER):
        rng = random.Random(base + 7919 * t + n)
        order, violations = _constrained_random(pts, steps, rng, tau)
        arr = pts[order]
        sym, conf = strip_scores(arr, steps)
        # Clearing the strip floors outranks squeezing out more neighbour
        # contrast — a chart whose strips can be told apart but with a slightly
        # closer worst pair beats the reverse.
        floors = min(sym / SYM_FLOOR, 1.0) + min(conf / CONF_FLOOR, 1.0)
        key = (floors, min_neighbour_contrast(arr, steps), conf, sym)
        if best_key is None or key > best_key:
            best_order, best_key = order, key
        # The first violation-free build already guarantees min-adjacency ≥ tau;
        # lower rungs can't add contrast, only entropy — keep descending only
        # while the strip floors are still unmet.
        if violations == 0 and floors >= 2.0:
            break
    best_order = _repair(pts, best_order, steps,
                         random.Random(base ^ 0xC0FFEE))
    return [program[i] for i in best_order]


def contrast_report(program: list[tuple], steps: int) -> tuple[float, float, float]:
    """(min neighbour contrast, min symmetry, min confusability) for *program*
    in its current order — for logging/comparison."""
    pts = np.asarray([tuple(p)[:3] for p in program], dtype=float)
    steps = max(1, int(steps) if steps else len(pts))
    return (min_neighbour_contrast(pts, steps), *strip_scores(pts, steps))
