"""Base class for proposals that move along the edges of a stimulus graph."""

import random
from abc import ABC, abstractmethod

import networkx as nx


class GraphProposal(ABC):
    """Proposes x* given the current reference x, on a fixed graph whose nodes are stimulus ids.

    One instance is shared by every session that uses the same graph and params, so a
    subclass must keep no per-session state: all randomness comes from the ``rng`` argument,
    and the chain's history arrives as ``n_rejected``. Extra keyword arguments to
    ``__init__`` are the spec's ``propose_params``.
    """

    def __init__(self, graph: nx.Graph):
        self.graph = graph

    @abstractmethod
    def propose(self, x, rng: random.Random, n_rejected: int) -> tuple:
        """(x*, info) for reference x; n_rejected = proposals rejected in a row just before this one.

        info is a dict of raw facts about how x* was drawn; it is stored with the trial."""

    def check(self) -> list[str]:
        """Warnings about the graph/params, logged at boot. Raise for anything that cannot run."""
        return []
