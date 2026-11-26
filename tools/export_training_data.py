"""貯めた「直す前 → 直した後」のセリフを、追加学習（LoRA）用のデータに書き出す。

使い方（アプリのフォルダで）:
    .venv\\Scripts\\python.exe tools\\export_training_data.py [corrections.jsonl] [出力フォルダ]

省略時は data/training/corrections.jsonl を読み、data/training/export/ に次を書く。
- sft.jsonl: 会話形式（system=口調の規則 / user=テーマと直前のセリフ / assistant=直した後）
- dpo.jsonl: 好みの組（prompt / chosen=直した後 / rejected=直す前）
- scripts_sft.jsonl: 台本 1 本ぶんの会話形式（system / user = 台本を作る AI に実際に送った指示、
  assistant = 規則・推敲を通った完成版の台本の JSON）。scripts.jsonl から、口調の問題が残らず
  壊れた形も無い台本だけを使う
- summary.txt: 件数（段階・話し手ごと）

同じ組は 1 件にまとめる。データは PC の中だけで使い、公開しない。
直した後でも壊れている形（「聞かせてくれのだ」など）は外す。
学習するのはセリフ回し（口調・言い回し）だけ。内容の誤りの修正（段階 fact）は使わない
（LoRA で事実を覚えさせても正確にならず、もっともらしい誤りが増えるため）。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

SKIP_STAGES = frozenset({"fact"})  # 学習に使わない段階

# 直した後でも日本語として壊れている形（口調の自動の手当ての誤り）。学ばせると誤りを覚えるので外す
_BROKEN = [
    re.compile(p)
    for p in (
        r"(くれ|ください|でしょ|だろ|しよう|ましょう|せよ|ぞ|ぜ|っす|ろ)な?のだ",
        r"(?<![静確豊僅遥])(?<!わず)(?<!ほの)かのだ",
        r"[ねよ]なのだ",
        r"かなのだ[？?]",
        r"(たく|しく|なく)のだ",
        r"のだわ",
        r"(?<![そあこど])のわ(?![けり])",
        r"(なさい|ましょう|ください)わ",
        r"よっす|ねっす",
        r"のだの|なのだなのだ|のだのだ",
        r"だの[。！？!?]",
    )
]


def broken(before: str, after: str) -> bool:
    """直した後のセリフが壊れているか（直す前から同じ形があるものは口調の問題ではないので除く）。"""
    for rx in _BROKEN:
        m = rx.search(after)
        if m and m.group(0) not in before:
            return True
    # 「しなければ」を「しない」に変えると意味が逆になる
    return (
        "なければ" in before
        and "なければ" not in after
        and bool(re.search(r"しない|ないの", after))
        and not re.search(r"なくて|ないと|べき|必要", after)
    )


SYSTEM = (
    "あなたは掛け合い解説動画の台本作家です。"
    "キャラクターの口調の規則を守り、自然な日本語で書きます。"
)


def _prompt(row: dict) -> tuple[str, str]:
    system = SYSTEM + ("\n口調の規則:\n" + row["rules"] if row.get("rules") else "")
    ctx = "\n".join(row.get("context") or []) or "（最初のセリフ）"
    user = (
        f"テーマ: {row.get('topic') or '（未指定）'}\n直前のセリフ:\n{ctx}\n\n"
        f"{row['speaker']} の次のセリフを 1 つ書いてください。"
    )
    return system, user


def export(src: Path, out: Path) -> dict[str, int]:
    rows = []
    seen = set()
    dropped = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (r.get("speaker"), r.get("before"), r.get("after"))
        if r.get("stage") in SKIP_STAGES:
            continue
        if not r.get("after") or not r.get("before") or key in seen:
            continue
        if r["after"] == r["before"] or broken(r["before"], r["after"]):
            dropped += 1
            continue
        seen.add(key)
        rows.append(r)
    out.mkdir(parents=True, exist_ok=True)
    with (
        (out / "sft.jsonl").open("w", encoding="utf-8") as sft,
        (out / "dpo.jsonl").open("w", encoding="utf-8") as dpo,
    ):
        for r in rows:
            system, user = _prompt(r)
            msgs = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
                {"role": "assistant", "content": r["after"]},
            ]
            sft.write(json.dumps({"messages": msgs}, ensure_ascii=False) + "\n")
            dpo.write(
                json.dumps(
                    {
                        "prompt": system + "\n\n" + user,
                        "chosen": r["after"],
                        "rejected": r["before"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    stages = Counter(r.get("stage", "") for r in rows)
    speakers = Counter(r.get("speaker", "") for r in rows)
    lines = [f"合計 {len(rows)} 件（壊れた形・変化なしで外した {dropped} 件）"]
    lines += [f"段階 {k}: {v}" for k, v in stages.most_common()]
    lines += [f"話し手 {k}: {v}" for k, v in speakers.most_common()]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"total": len(rows), "dropped": dropped, **{f"stage_{k}": v for k, v in stages.items()}}


def export_scripts(
    src: Path, out: Path, skip_models: tuple[str, ...] = ("tuned",)
) -> dict[str, int]:
    """台本 1 本ぶんの組を書き出す。学習したモデル自身の台本（skip_models）は使わない。"""
    kept = dropped = 0
    seen: set[str] = set()
    out.mkdir(parents=True, exist_ok=True)
    with (out / "scripts_sft.jsonl").open("w", encoding="utf-8") as f:
        for line in src.read_text(encoding="utf-8").splitlines() if src.is_file() else []:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            texts = [t["text"] for sc in r["output"]["scenes"] for t in sc["turns"]]
            bad = (
                r.get("issues", 0) > 0
                or len(texts) < 4
                or any(x in r.get("model", "") for x in skip_models)
                or any(rx.search(t) for t in texts for rx in _BROKEN)
            )
            key = json.dumps(texts, ensure_ascii=False)
            if bad or key in seen:
                dropped += 1
                continue
            seen.add(key)
            msgs = [
                *r["messages"],
                {
                    "role": "assistant",
                    "content": json.dumps(r["output"], ensure_ascii=False),
                },
            ]
            f.write(json.dumps({"messages": msgs}, ensure_ascii=False) + "\n")
            kept += 1
    return {"scripts": kept, "scripts_dropped": dropped}


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "data" / "training"
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "corrections.jsonl"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "export"
    if not src.is_file():
        print(f"記録がまだありません: {src}")
        return
    res = export(src, out)
    res.update(export_scripts(src.with_name("scripts.jsonl"), out))
    with (out / "summary.txt").open("a", encoding="utf-8") as f:
        f.write(f"台本 1 本ぶん: {res['scripts']} 本（外した {res['scripts_dropped']} 本）\n")
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
