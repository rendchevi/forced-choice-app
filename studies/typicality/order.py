"""Each participant's item order: stimuli shuffled, n_attention attention checks spread evenly between them."""

import hashlib
import random


def pick_attention(items: list, k: int, rng: random.Random, key="att_id") -> list:
    """k attention items for one session. With k <= len(items): rng.sample (all distinct).
    With more: rounds of rng.sample over all items (the last round only as long as needed), so
    every item is used at most ceil(k / len(items)) times; a round never starts with the item
    that ended the previous one, so the same check never comes twice in a row."""
    if k > 0 and not items:
        raise ValueError("attention checks requested but the attention file has no items")
    out = []
    while len(out) < k:
        rnd = rng.sample(items, min(len(items), k - len(out)))
        if out and len(items) > 1 and rnd[0][key] == out[-1][key]:
            if len(rnd) > 1:
                rnd[0], rnd[1] = rnd[1], rnd[0]
            else:
                rnd[0] = rng.choice([it for it in items if it[key] != out[-1][key]])
        out += rnd
    return out


def even_order(stimulus_ids, attention_items: list, n_attention: int, seed_key: str) -> list:
    """[("stimulus", stimulus_id) | ("attention", item)]: stimuli in a random order (seeded by
    ``seed_key``, so a session's order is reproducible); n_attention checks picked by
    pick_attention; check k (1..K) goes after stimulus floor(k*n/(K+1))."""
    rng = random.Random(int.from_bytes(hashlib.sha256(seed_key.encode()).digest()[:8], "big"))
    stim = sorted(stimulus_ids)
    rng.shuffle(stim)
    att = pick_attention(sorted(attention_items, key=lambda a: a["att_id"]), n_attention, rng)
    n, k = len(stim), len(att)
    after = [j * n // (k + 1) for j in range(1, k + 1)]
    out = []
    for i in range(n + 1):
        out += [("attention", att[j]) for j, a in enumerate(after) if a == i]
        if i < n:
            out.append(("stimulus", stim[i]))
    return out
