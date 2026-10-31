"""In-silico experimental-design benchmark (PLAN §10). Provenance: simulated.

Modules: ``world`` (hidden-truth worlds, noisy observations, held-out scenarios, redundancy),
``fitting`` (multi-experiment MAP + Laplace calibration), ``agent_adapter`` (agents' offline
policy on calibrated evidence), ``optimizer`` (information-gain / dedup scoring) and
``benchmark`` (policies, metrics, aggregation).
"""
