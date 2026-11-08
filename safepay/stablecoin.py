"""Stablecoin payments under the same guardrail as PayPal payments.

PayPal moves dollars and its own stablecoin, PYUSD; agents increasingly pay in
stablecoins too. SafePay judges both with one policy, one hash chain and one
receipt. A stablecoin payment adds a failure PayPal never has: money sent to an
address on the wrong network, or to a malformed address, is gone for good, and
no one can reverse it. So before any of the usual checks, a stablecoin payment
must name its network, the token must exist on that network, the network must
be one the policy allows, and the payee must be a well-formed address for that
network (Stellar addresses are checked down to their CRC16 checksum).

Amounts in a dollar stablecoin count one to one against the USD limits.
"""

from __future__ import annotations

import base64
import re

# Dollar stablecoins SafePay knows, and the networks each one is issued on.
# A policy can narrow the networks further (allowed_networks).
STABLECOINS: dict[str, frozenset[str]] = {
    "PYUSD": frozenset({"ethereum", "solana", "arbitrum", "stellar"}),
    "USDC": frozenset({"ethereum", "solana", "arbitrum", "stellar", "base", "polygon"}),
}

EVM = frozenset({"ethereum", "arbitrum", "base", "polygon"})

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def is_stablecoin(currency: str | None) -> bool:
    return (currency or "").upper() in STABLECOINS


def _crc16_xmodem(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def valid_stellar(addr: str) -> bool:
    """A Stellar account id (G...): base32, version byte, 32-byte key, CRC16."""
    if not re.fullmatch(r"G[A-Z2-7]{55}", addr or ""):
        return False
    try:
        raw = base64.b32decode(addr)
    except Exception:
        return False
    if len(raw) != 35 or raw[0] != 6 << 3:
        return False
    return _crc16_xmodem(raw[:33]) == int.from_bytes(raw[33:], "little")


def valid_evm(addr: str) -> bool:
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", addr or ""))


def valid_solana(addr: str) -> bool:
    if not re.fullmatch(f"[{_B58}]{{32,44}}", addr or ""):
        return False
    n = 0
    for ch in addr:
        n = n * 58 + _B58.index(ch)
    size = (n.bit_length() + 7) // 8
    lead = len(addr) - len(addr.lstrip("1"))
    return size + lead == 32


def valid_address(network: str, addr: str) -> bool:
    net = (network or "").lower()
    if net == "stellar":
        return valid_stellar(addr)
    if net == "solana":
        return valid_solana(addr)
    if net in EVM:
        return valid_evm(addr)
    return False


def check(intent, policy) -> tuple[list[str], list[dict]]:
    """Failures and evidence for a stablecoin payment; nothing for fiat."""
    cur = (intent.currency or "").upper()
    if cur not in STABLECOINS:
        return [], []
    net = (getattr(intent, "network", None) or "").strip().lower()
    allowed = [n.lower() for n in (getattr(policy, "allowed_networks", None) or [])]
    failures: list[str] = []
    if not net:
        failures.append(f"a {cur} payment must name its network "
                        f"({', '.join(sorted(STABLECOINS[cur]))})")
    elif net not in STABLECOINS[cur]:
        failures.append(f"{cur} is not issued on {net}; a transfer there would not arrive")
    elif allowed and net not in allowed:
        failures.append(f"network {net} is not in the allowed set {allowed}")
    elif not valid_address(net, intent.recipient or ""):
        failures.append(f"'{intent.recipient}' is not a valid {net} address; "
                        "a stablecoin sent to a bad address cannot be recovered")
    return failures, [{"rule": "stablecoin", "token": cur, "network": net or None,
                       "allowed_networks": allowed or sorted(STABLECOINS[cur])}]
