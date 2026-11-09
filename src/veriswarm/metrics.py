"""Information measures and descriptive statistics, standard library only."""

from __future__ import annotations

import bisect
import math
import random
from collections import Counter


def _h2(p: float) -> float:
    if p <= 0 or p >= 1:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def binary_entropy(p: float) -> float:
    return _h2(p)


def empirical_mutual_information(pairs: list[tuple]) -> float:
    """Plug-in I(X;Y) in bits. Biased upward for small samples; not a capacity."""
    n = len(pairs)
    if n == 0:
        return 0.0
    joint = Counter(pairs)
    px = Counter(x for x, _ in pairs)
    py = Counter(y for _, y in pairs)
    mi = 0.0
    for (x, y), c in joint.items():
        mi += (c / n) * math.log2(c * n / (px[x] * py[y]))
    return max(0.0, mi)


def mutual_information_from_counts(joint: Counter) -> float:
    """The same plug-in estimate, from a joint count table."""
    n = sum(joint.values())
    if n == 0:
        return 0.0
    px: Counter = Counter()
    py: Counter = Counter()
    for (x, y), c in joint.items():
        px[x] += c
        py[y] += c
    mi = sum((c / n) * math.log2(c * n / (px[x] * py[y])) for (x, y), c in joint.items() if c)
    return max(0.0, mi)


def bootstrap_mi_interval(
    group_counts: list[Counter], seed: int, n_boot: int = 2000, level: float = 0.95
) -> tuple[float, float]:
    """Percentile interval for plug-in information, resampling whole seed groups."""
    rng = random.Random(seed)
    k = len(group_counts)
    values = []
    for _ in range(n_boot):
        joint: Counter = Counter()
        for g in rng.choices(group_counts, k=k):
            joint.update(g)
        values.append(mutual_information_from_counts(joint))
    values.sort()
    return values[int((1 - level) / 2 * n_boot)], values[min(n_boot - 1, int((1 + level) / 2 * n_boot))]


def channel_information(matrix: list[list[float]], inputs: list[float]) -> float:
    """Exact I(X;Y) in bits for a channel P(y|x) and an input distribution."""
    ny = len(matrix[0])
    q = [sum(inputs[x] * matrix[x][y] for x in range(len(matrix))) for y in range(ny)]
    total = 0.0
    for x, px in enumerate(inputs):
        for y in range(ny):
            if px > 0 and matrix[x][y] > 0:
                total += px * matrix[x][y] * math.log2(matrix[x][y] / q[y])
    return max(0.0, total)


def miller_madow_correction(pairs: list[tuple]) -> float:
    """First-order bias term (in bits) for the plug-in estimate above."""
    n = len(pairs)
    if n == 0:
        return 0.0
    kxy = len(set(pairs))
    kx = len({x for x, _ in pairs})
    ky = len({y for _, y in pairs})
    return max(0.0, (kxy - kx - ky + 1) / (2 * n * math.log(2)))


def z_channel_capacity(p: float) -> float:
    """Capacity in bits/use of a Z-channel whose 'free' input reads busy with prob p."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 0.0
    return math.log2(1 + (1 - p) * p ** (p / (1 - p)))


def blahut_arimoto(matrix: list[list[float]], tol: float = 1e-12, max_iter: int = 200000) -> float:
    """Capacity in bits/use of a discrete memoryless channel P(y|x).

    Works in the input alphabet as given, so it also serves as an independent
    check of the closed form above.
    """
    nx, ny = len(matrix), len(matrix[0])
    r = [1.0 / nx] * nx
    capacity = 0.0
    for _ in range(max_iter):
        q = [sum(r[x] * matrix[x][y] for x in range(nx)) for y in range(ny)]
        c = []
        for x in range(nx):
            exponent = 0.0
            for y in range(ny):
                if matrix[x][y] > 0:
                    exponent += matrix[x][y] * math.log(matrix[x][y] / q[y])
            c.append(math.exp(exponent))
        total = sum(r[x] * c[x] for x in range(nx))
        lower = math.log2(total)
        upper = math.log2(max(c))
        r = [r[x] * c[x] / total for x in range(nx)]
        capacity = lower
        if upper - lower < tol:
            break
    return max(0.0, capacity)


def wilson_interval(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    phat = successes / n
    denom = 1 + z**2 / n
    centre = (phat + z**2 / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_group_interval(
    group_values: list[float], seed: int, n_boot: int = 10000, level: float = 0.95
) -> tuple[float, float]:
    """Percentile interval resampling whole groups, since rounds cluster within them."""
    if len(set(group_values)) <= 1:
        v = group_values[0] if group_values else 0.0
        return (v, v)
    rng = random.Random(seed)
    k = len(group_values)
    means = sorted(sum(rng.choices(group_values, k=k)) / k for _ in range(n_boot))
    lo = means[int((1 - level) / 2 * n_boot)]
    hi = means[min(n_boot - 1, int((1 + level) / 2 * n_boot))]
    return (lo, hi)


def auc(positive: list[float], negative: list[float]) -> float:
    """Probability a positive score exceeds a negative one, ties counted half."""
    if not positive or not negative:
        return 0.5
    ordered = sorted(negative)
    wins = 0.0
    for a in positive:
        below = bisect.bisect_left(ordered, a)
        equal = bisect.bisect_right(ordered, a) - below
        wins += below + 0.5 * equal
    return wins / (len(positive) * len(negative))


def bootstrap_auc_interval(
    positive: list[float], negative: list[float], seed: int, n_boot: int = 1000
) -> tuple[float, float]:
    rng = random.Random(seed)
    values = []
    for _ in range(n_boot):
        p = rng.choices(positive, k=len(positive))
        n = rng.choices(negative, k=len(negative))
        values.append(auc(p, n))
    values.sort()
    return values[int(0.025 * n_boot)], values[min(n_boot - 1, int(0.975 * n_boot))]
