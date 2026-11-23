"""指示文から動画の用途を判定する（規則ベース。外部の AI に頼らず、理由を説明できる）。

1. 指示文の明示の語（「MV」「ショート」「解説」など）で点数を付ける。長い語ほど重い
2. 素材のヒント（縦長・音楽だけ・台本あり）で補う
3. 「解説だけど MV っぽく」のように「〜風・〜っぽく・〜みたいに」で別の用途が添えられたときは、
   主の用途はそのままにして、添えられた用途から借りる部品の候補を出す（明示の指示だけ例外を許す）
4. 確信が持てないときは needs_confirmation=True。尋ねられないときは、候補のうち動きが最も
   控えめな型を選ぶ（強い演出を誤って付けるより害が小さい）
"""

import re
import unicodedata

from video_automation.schemas.design import (
    ClassifyRequest,
    ClassifyResult,
    Component,
    Purpose,
    PurposeCandidate,
)
from video_automation.services.design.profiles import PROFILES

_MIX = re.compile(r"(風|っぽく|っぽい|みたいに|みたいな|のように|のような|調で|テイスト)")
# 借りてよいのは見た目・動きの部品だけ（字幕や情報の部品の有無は主の用途に従う）
_BORROWABLE = {
    Component.KINETIC_TEXT,
    Component.EMPHASIS_TEXT,
    Component.TRANSITION_SHAPE,
    Component.TRANSITION_WHIP,
    Component.SHAKE_PUNCH,
    Component.FLASH,
    Component.GLITCH,
    Component.BLOOM,
    Component.LETTERBOX,
    Component.BEAT_CUT,
    Component.SFX,
}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def _hits(text: str, keywords: list[str]) -> list[str]:
    out = []
    for k in keywords:
        k2 = _norm(k)
        if re.fullmatch(r"[a-z0-9]+", k2):  # 英字の短い語（mv・pr）は語の境目で
            if re.search(rf"(?<![a-z0-9]){re.escape(k2)}(?![a-z0-9])", text):
                out.append(k)
        elif k2 in text:
            out.append(k)
    return out


def classify(req: ClassifyRequest) -> ClassifyResult:
    text = _norm(req.text)
    scores: dict[Purpose, float] = {}
    reasons: dict[Purpose, list[str]] = {}
    mixed: set[Purpose] = set()
    for purpose, prof in PROFILES.items():
        hits = _hits(text, prof.keywords)
        if not hits:
            continue
        s = 0.0
        for k in hits:
            weight = 1.0 + min(len(k), 6) / 6  # 長い語ほど重い
            # 「〜風」「〜っぽく」が直後に続く語は、主ではなく添えられた用途
            pos = text.find(_norm(k))
            tail = text[pos + len(_norm(k)) : pos + len(_norm(k)) + 6] if pos >= 0 else ""
            if _MIX.match(tail):
                mixed.add(purpose)
                reasons.setdefault(purpose, []).append(f"「{k}」に「〜風・〜っぽく」が続く")
                continue
            s += weight
            reasons.setdefault(purpose, []).append(f"「{k}」")
        if s:
            scores[purpose] = s
    # 素材のヒント
    if req.vertical:
        scores[Purpose.SHORT] = scores.get(Purpose.SHORT, 0) + 1.5
        reasons.setdefault(Purpose.SHORT, []).append("縦長の素材")
    if req.has_music and not req.has_script:
        for p in (Purpose.MV, Purpose.LYRIC):
            scores[p] = scores.get(p, 0) + 0.8
            reasons.setdefault(p, []).append("音楽だけの素材")
    if req.has_script:
        scores[Purpose.EXPLAINER] = scores.get(Purpose.EXPLAINER, 0) + 0.6
        reasons.setdefault(Purpose.EXPLAINER, []).append("台本がある")

    # 「字幕を付けて」は他の用途にも付く言い方。他の用途が見つかったら字幕動画は候補から外す
    if len(scores) > 1 and Purpose.SUBTITLE in scores:
        scores.pop(Purpose.SUBTITLE)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], PROFILES[kv[0]].motion_level))
    cands = [
        PurposeCandidate(purpose=p, score=round(s, 2), reasons=reasons.get(p, []))
        for p, s in ranked
    ]
    if not ranked:
        fallback = Purpose.EXPLAINER if req.has_script else Purpose.SUBTITLE
        return ClassifyResult(
            purpose=fallback,
            confidence=0.0,
            candidates=[],
            needs_confirmation=True,
            mixed_from=sorted(mixed),
            note="用途を示す語が見つかりません。用途を確認してください"
            f"（確認できないときは動きの控えめな「{PROFILES[fallback].label}」で作ります）",
        )
    top, top_s = ranked[0]
    second_s = ranked[1][1] if len(ranked) > 1 else 0.0
    close = second_s >= top_s * 0.8
    chosen = top
    if close:  # 拮抗するときは、拮抗する候補のうち動きが最も控えめな型を選ぶ
        near = [p for p, s in ranked if s >= top_s * 0.8]
        chosen = min(near, key=lambda p: PROFILES[p].motion_level)
    confidence = round(top_s / (top_s + second_s), 2) if top_s else 0.0
    mixed.discard(chosen)
    extras: list[Component] = []
    base = PROFILES[chosen]
    for m in sorted(mixed):
        for c in PROFILES[m].components:
            if c in _BORROWABLE and not base.allows(c) and c not in extras:
                extras.append(c)
    note = ""
    if close:
        names = "・".join(PROFILES[p].label for p, s in ranked if s >= top_s * 0.8)
        note = f"用途の候補が拮抗しています（{names}）。確認してください"
    if mixed:
        names = "・".join(PROFILES[m].label for m in sorted(mixed))
        note = (note + " " if note else "") + (
            f"「{names}」の雰囲気を混ぜる指示があります。"
            "借りる部品を確認して extras に入れてください"
        )
    return ClassifyResult(
        purpose=chosen,
        confidence=confidence,
        candidates=cands,
        needs_confirmation=close or top_s < 1.0,
        mixed_from=sorted(mixed),
        suggested_extras=extras,
        note=note,
    )
