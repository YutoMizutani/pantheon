#!/usr/bin/env python3
"""_signals — signal-vocabulary loader for the self-improvement hooks.

層の分離 (なぜこのファイルがあるか):
  acceptance 検出の**機構**（全文一致・debounce・cost gate）は環境非依存で
  フレーム層 (git 同梱)。それに対し、機構が照合する
  **語彙**は較正定数 — 「特定ユーザーが完了/訂正をどう言うか」を encode し、
  根拠はそのユーザーの transcript corpus にある。根拠が運べない定数を verbatim
  で配布しない、という規約に従い、語彙はローカル層に置く:

    .claude/hooks/local/signals.json          — あなたの語彙 (gitignore 済み)
    .claude/hooks/local/signals.json.example  — 原環境で較正済みの日本語パック (tracked・サンプル)

  完全 opt-in: 同梱デフォルト (_DEFAULTS) は**空**で、signals.json が無い間は
  検出が一切発火しない。フレーム層 (機構) は語彙について意見を持たない — 「ok」の
  ような語ですら意味は環境依存で (ある環境では reflection 予約語、別の環境では
  単なる承認「やっていいよ」)、デフォルト発火させると意味衝突と誤発火 (acceptance
  は背景サブエージェント spawn = costly) を生む。配布先は (a) 初回セットアップで
  エージェントが「完了をどう言うか」を聞き取って signals.json を作る、または
  (b) signals.json.example を local/signals.json にコピーする、で起動する。
  自環境語彙の作り方 (再導出手順) は docs/self-improvement-loop.md
  「シグナル語彙の較正」節。

スキーマ (すべて省略可・未指定キーはデフォルト維持):
  {
    "acceptance": {
      "exact":    ["完了", ...],   // 全文一致 (verbatim・大文字小文字区別)
      "exact_ci": ["ok", ...]      // 全文一致 (case-insensitive)
    }
  }

マージ規則は **leaf キー単位の置換** (append ではない)。signals.json が
"acceptance.exact_ci" を定義したらデフォルトのリストは丸ごと差し替わる —
これによりユーザーはデフォルト語の**削除**もできる。未定義キーはデフォルト。

env override: FRAME_SIGNALS_FILE=<path> で読み込み先を差し替える
(テストの hermetic 化用。FRAME_ROUTING_PREFIX と同系列の transport knob)。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# 同梱デフォルト = 空 (完全 opt-in)。語彙はユーザー固有の較正値であり、
# フレーム層が決め打ちすると意味衝突 (例: 「ok」を承認語として使う環境での誤発火)
# と costly な誤起動を生む。配布先は signals.json (初回セットアップでの聞き取り or
# example のコピー) を置いて初めて検出が起動する。語彙サンプルは
# local/signals.json.example を参照。
_DEFAULTS: dict = {
    "acceptance": {
        "exact": [],
        "exact_ci": [],
    },
    # correction 語彙 — inject_correction_nudge.py が消費する軽量 nudge 用
    # (2026-06-17 二層化で復活。global queue は退役済 — docs/design-self-improvement-two-tier-intake.md)。
    # 空デフォルト = opt-in (signals.json に correction block が無ければ nudge は発火しない)。
    "correction": {
        "patterns": [],
        "third_party_negation": [],
    },
}


def _signals_file() -> Path:
    env = os.environ.get("FRAME_SIGNALS_FILE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "local" / "signals.json"


def _load_raw() -> dict:
    """Read signals.json; on missing/invalid file fall back to {} (= defaults).
    Never raises — a broken config must degrade to defaults, not kill hooks."""
    p = _signals_file()
    try:
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[_signals] ignoring unreadable {p}: {exc}\n")
        return {}
    return data if isinstance(data, dict) else {}


def _merged() -> dict:
    """Defaults overridden per LEAF key by signals.json (replace, not append)."""
    raw = _load_raw()
    out: dict = {}
    for section, defaults in _DEFAULTS.items():
        sec_raw = raw.get(section)
        sec_raw = sec_raw if isinstance(sec_raw, dict) else {}
        sec_out = {}
        for key, default_list in defaults.items():
            val = sec_raw.get(key)
            if isinstance(val, list) and all(isinstance(s, str) for s in val):
                sec_out[key] = val
            else:
                if val is not None:
                    sys.stderr.write(
                        f"[_signals] {section}.{key}: expected list[str], "
                        f"got {type(val).__name__} — using default\n"
                    )
                sec_out[key] = list(default_list)
        out[section] = sec_out
    return out


def acceptance_sets() -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(exact, exact_ci)`` for detect_acceptance_signal.
    ``exact`` is matched verbatim; ``exact_ci`` entries are lowercased here and
    must be compared against the lowercased prompt body."""
    acc = _merged()["acceptance"]
    return (
        frozenset(acc["exact"]),
        frozenset(s.lower() for s in acc["exact_ci"]),
    )


def correction_pattern_sets() -> dict:
    """Return compiled correction-detection patterns for inject_correction_nudge.

    Keys: ``patterns`` (critique phrases that signal the user is pointing out a
    Claude failure) and ``third_party_negation`` (exclusion spans, e.g.
    「Xがないとだめ」 = structural-state description, not a Claude critique).
    Vocabulary is user calibration (signals.json ``correction`` block); the
    empty default keeps detection opt-in. Topic/improvement words
    (自己改善 / 次から / 今後は / 再発防止 / 分析して…修正) are intentionally NOT
    correction triggers — including them self-fed the loop on meta-discussion
    (INC-2026-06-17-01)."""
    corr = _merged()["correction"]

    def _compile(key: str) -> tuple[re.Pattern[str], ...]:
        out = []
        for p in corr.get(key, []):
            try:
                out.append(re.compile(p))
            except re.error as exc:
                sys.stderr.write(f"[_signals] correction.{key}: bad regex {p!r}: {exc}\n")
        return tuple(out)

    return {
        "patterns": _compile("patterns"),
        "third_party_negation": _compile("third_party_negation"),
    }
