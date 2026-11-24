"""Discrete MCMC-with-People proposal on a b-regular stimulus graph (Hsu et al., 2019, Behav Res Methods 51).

Each proposal, for reference x and n_rejected proposals rejected in a row:
  1. stuck:  if n_stuck is set and n_rejected is a positive multiple of n_stuck,
             x* ~ uniform over X \\ {x};
  2. jump:   otherwise with probability p_jump, x* ~ uniform over X \\ {x};
  3. walk:   otherwise a random walk of 1 + K steps, P(K = k) = (1 - p_geom) p_geom^k, each step to a
             uniformly chosen neighbour; x* = the end node. A walk that ends at x is drawn again.

p_geom = 0 is the uniform-neighbour proposal; the expected walk length is 1 / (1 - p_geom).
Before the redraw, the walk proposes Q = (1 - p_geom) P (I - p_geom P)^-1 (P = random-walk matrix),
which is symmetric on a regular graph; the redraw makes it slightly asymmetric (by the spread of
Q_xx across nodes). The uniform jump is exactly symmetric. The stuck rule depends on the chain's
history, so the chain is then no longer guaranteed to converge to the target distribution.
"""

import random

from proposal.graph.base import GraphProposal


class DMCMCP(GraphProposal):
    def __init__(self, graph, p_geom: float = 0.0, p_jump: float = 0.0, n_stuck: int | None = None):
        super().__init__(graph)
        if not 0 <= p_geom < 1:
            raise ValueError(f"dmcmcp: p_geom must be in [0, 1), got {p_geom}")
        if not 0 <= p_jump <= 1:
            raise ValueError(f"dmcmcp: p_jump must be in [0, 1], got {p_jump}")
        if n_stuck is not None and (type(n_stuck) is not int or n_stuck < 1):
            raise ValueError(f"dmcmcp: n_stuck must be a positive integer or null (= never), got {n_stuck!r}")
        if graph.number_of_nodes() < 2:
            raise ValueError("dmcmcp: the graph needs at least 2 stimuli")
        isolated = [n for n, d in graph.degree() if d == 0]
        loops = [n for n in graph.nodes if graph.has_edge(n, n)]
        if isolated or loops:
            raise ValueError(f"dmcmcp: nodes without neighbours {isolated} or with self-loops {loops}")
        self.p_geom, self.p_jump, self.n_stuck = p_geom, p_jump, n_stuck
        # sorted: the same seed gives the same x* whatever the node/edge order in the graph file
        self.nodes = sorted(graph.nodes)
        self.index = {n: i for i, n in enumerate(self.nodes)}
        self.nbrs = {n: sorted(graph.neighbors(n)) for n in self.nodes}

    def _other(self, x, rng):
        """Uniform over every stimulus except x."""
        i = rng.randrange(len(self.nodes) - 1)
        return self.nodes[i + (i >= self.index[x])]

    def propose(self, x, rng: random.Random, n_rejected: int):
        if self.n_stuck is not None and n_rejected > 0 and n_rejected % self.n_stuck == 0:
            return self._other(x, rng), {"proposal_kind": "stuck", "n_steps": None}
        # p_jump = 0 draws nothing, so the walk uses the rng exactly as a jump-free proposal would
        if self.p_jump > 0 and rng.random() < self.p_jump:
            return self._other(x, rng), {"proposal_kind": "jump", "n_steps": None}
        while True:
            n_steps = 1
            while self.p_geom > 0 and rng.random() < self.p_geom:
                n_steps += 1
            y = x
            for _ in range(n_steps):
                y = rng.choice(self.nbrs[y])
            if y != x:
                return y, {"proposal_kind": "walk", "n_steps": n_steps}

    def check(self) -> list[str]:
        warnings = []
        degrees = {d for _, d in self.graph.degree()}
        if len(degrees) > 1:
            warnings.append(f"dmcmcp: graph is not regular (degree {min(degrees)}..{max(degrees)}), "
                            "so the walk proposal is not symmetric")
        if self.n_stuck is not None:
            warnings.append(f"dmcmcp: n_stuck={self.n_stuck} makes the proposal depend on the chain's history; "
                            "convergence to the target is not guaranteed")
        return warnings
