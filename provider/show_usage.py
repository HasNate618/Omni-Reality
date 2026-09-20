#!/usr/bin/env python3
"""Pretty-print yibu API usage from the local audit ledger.

Offline: reads only the local JSONL ledger, makes no API calls, spends no
credit. Missing token values print as `?` (unknown, never zero).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from yibu_audit import DEFAULT_AUDIT_LOG


def fmt(value: int | None) -> str:
    return "?" if value is None else str(value)


def safe_int(value: object) -> int | None:
    try:
        return None if value is None else int(str(value))
    except (TypeError, ValueError):
        return None


def safe_float(value: object) -> float:
    try:
        return float(str(value or 0))
    except (TypeError, ValueError):
        return 0.0


def load_rows(log: Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    corrupt = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            corrupt += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            corrupt += 1
    return rows, corrupt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, default=DEFAULT_AUDIT_LOG)
    args = parser.parse_args()
    if not args.log.is_file():
        raise SystemExit(f"audit log does not exist: {args.log}")

    rows, corrupt = load_rows(args.log)
    if corrupt:
        print(f"warning: skipped {corrupt} corrupt ledger line(s).")
    if not rows:
        print("ledger is empty — no calls recorded yet.")
        return 0

    print(f"{'purpose':24s} {'model':32s} {'ok':>3s} {'in':>5s} {'out':>5s} {'total':>6s} {'latency':>8s}")
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    missing = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for row in rows:
        print(
            f"{str(row.get('purpose')):24s} {str(row.get('model')):32s} "
            f"{'ok' if row.get('ok') else 'FAIL':>3s} "
            f"{fmt(safe_int(row.get('input_tokens'))):>5s} {fmt(safe_int(row.get('output_tokens'))):>5s} "
            f"{fmt(safe_int(row.get('total_tokens'))):>6s} {safe_float(row.get('latency_s')):>7.2f}s"
        )
        for field in totals:
            parsed = safe_int(row.get(field))
            if parsed is None:
                missing[field] += 1
            else:
                totals[field] += parsed
    print()
    print(
        f"calls={len(rows)} ok={sum(1 for r in rows if r.get('ok'))} "
        f"failed={sum(1 for r in rows if not r.get('ok'))} | "
        f"in={totals['input_tokens']} out={totals['output_tokens']} total={totals['total_tokens']} "
        f"(missing: in={missing['input_tokens']} out={missing['output_tokens']} total={missing['total_tokens']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
