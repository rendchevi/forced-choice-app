"""x* drawn uniformly from the neighbours of x."""

import random

from proposal.graph.base import GraphProposal


class UniformNeighbor(GraphProposal):
    """q(x*|x) = 1/deg(x) for each neighbour x* of x.

    Symmetric, q(x*|x) = q(x|x*), only when the graph is regular, which is what lets a
    2AFC chain target the participant's choice distribution without a Hastings correction.
    """

    def __init__(self, graph):
        super().__init__(graph)
        isolated = [n for n, d in graph.degree() if d == 0]
        if isolated:
            raise ValueError(f"uniform_neighbor: nodes without neighbours, the chain would stop there: {isolated}")

    def propose(self, x, rng: random.Random):
        # sorted: the same seed gives the same x* whatever the edge order in the graph file
        return rng.choice(sorted(self.graph.neighbors(x)))

    def check(self) -> list[str]:
        degrees = {d for _, d in self.graph.degree()}
        if len(degrees) > 1:
            return [f"uniform_neighbor: graph is not regular (degree {min(degrees)}..{max(degrees)}), "
                    "so the proposal is not symmetric"]
        return []
