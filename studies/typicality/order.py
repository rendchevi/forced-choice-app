"""Each participant's item order: stimuli shuffled, attention items spread evenly between them."""

import hashlib
import random


def even_order(stimulus_ids, attention_ids, seed_key: str) -> list:
    """Stimuli in a random order (seeded by ``seed_key``, so a session's order is reproducible);
    attention item k (1..K, themselves in random order) goes after stimulus floor(k*n/(K+1))."""
    rng = random.Random(int.from_bytes(hashlib.sha256(seed_key.encode()).digest()[:8], "big"))
    stim, att = sorted(stimulus_ids), sorted(attention_ids)
    rng.shuffle(stim)
    rng.shuffle(att)
    n, k = len(stim), len(att)
    after = [j * n // (k + 1) for j in range(1, k + 1)]
    out = []
    for i in range(n + 1):
        out += [att[j] for j, a in enumerate(after) if a == i]
        if i < n:
            out.append(stim[i])
    return out
