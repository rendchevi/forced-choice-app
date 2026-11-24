"""Proposal functions for the chain.

A config names a proposal as ``"<family>/<name>"``, e.g. ``"graph/dmcmcp"``.
That is the file ``proposal/<family>/<name>.py``, which must define exactly one
subclass of the class defined in ``proposal/<family>/base.py``.
"""

import importlib
import inspect


def _classes_defined_in(module):
    return [c for c in vars(module).values()
            if inspect.isclass(c) and c.__module__ == module.__name__]


def load_proposal_class(name: str):
    family, sep, file = name.partition("/")
    if not sep or not family.isidentifier() or not file.isidentifier() or file == "base":
        raise ValueError(f"propose_fn {name!r} must look like '<family>/<name>', e.g. 'graph/dmcmcp'")
    try:
        base_module = importlib.import_module(f"proposal.{family}.base")
        module = importlib.import_module(f"proposal.{family}.{file}")
    except ModuleNotFoundError as e:
        raise ValueError(f"propose_fn {name!r}: no such file ({e})") from e
    bases = _classes_defined_in(base_module)
    if len(bases) != 1:
        raise ValueError(f"proposal/{family}/base.py must define exactly one class, found {len(bases)}")
    found = [c for c in _classes_defined_in(module) if issubclass(c, bases[0]) and c is not bases[0]]
    if len(found) != 1:
        raise ValueError(f"proposal/{family}/{file}.py must define exactly one subclass of "
                         f"{bases[0].__name__}, found {[c.__name__ for c in found]}")
    return found[0]
