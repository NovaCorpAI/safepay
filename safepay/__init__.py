"""SafePay: a safety guardrail between an autonomous AI agent and PayPal.

SafePay intercepts an agent's payment intent and runs three checks before any
money moves: deterministic policy (limits, categories, allow/block lists), a
live-web merchant verification (Tavily), and goal/anomaly reasoning (NVIDIA
Nemotron on Nebius Token Factory). It allows, holds, or blocks, executes allowed
payments through the PayPal sandbox, and signs an audit record of every
decision.
"""

__version__ = "0.8.2"
