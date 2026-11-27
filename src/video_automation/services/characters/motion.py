"""立ち絵の動き: 台詞から表情・ポーズ・身振りを選び、フレームごとの姿勢を決める。

動きはピクトグラムのアニメーションの考え方を使う。
- はっきりしたポーズ（キーポーズ）を決め、ポーズからポーズへ移る（中間はイージング）
- 予備動作（沈む）→ 動作（跳ぶ・振る）→ 行き過ぎ（オーバーシュート）→ 落ち着く → 止める
- 体全体（足元を支点に傾く・伸び縮み）と腕（肩を支点に回す）を分けて動かす
- 何もしない時間も呼吸と重心の揺れで少しだけ動かす（完全に止めない）
聞き手は話し手の文の終わりにうなずき、話し手の方へ少し体を向ける。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- 台詞 → 表情・ポーズ・身振り
_GREET = re.compile(
    r"こんにちは|こんばんは|^今日は|ようこそ|はじめまして|よろしく|またね|ありがとう|バイバイ"
)
_TROUBLE = re.compile(
    r"間違|失敗|うまくいかない|エラー|注意|気をつけ|困|だめ|ダメ|できない|しまった"
)
_SURPRISE = re.compile(r"^えっ|^ええ|すごい|本当に|まさか|なんと|びっくり|驚")
_QUESTION = re.compile(r"(？|\?|かな|かしら|だろう|でしょうか)[」』)）]?$")
_CONFIDENT = re.compile(r"完成|完璧|簡単|大丈夫|任せ|ばっちり|一通り|できあがり|できた")
_INSTRUCT = re.compile(
    r"押す|押して|押せ|押すと|クリック|選ぶ|選んで|開く|開いて|入れて|入力|タブ|ボタン|ここ|こちら|右上|左上|画面|メニュー"
)
_PRESENT = re.compile(
    r"できる|作れる|くれる|まとめて|表示|見せ|紹介|使える|わかる|出せる|なるの|なるわ"
)


@dataclass
class Beat:
    expression: str
    pose: str
    gestures: list[str]


def classify(text: str) -> Beat:
    """台詞の言葉から、表情・ポーズ・身振りを選ぶ（規則。LLM は使わない）。"""
    t = text.strip()
    excl = "！" in t or "!" in t
    if _GREET.search(t):
        return Beat("happy", "wave", ["wave"] + (["hop"] if excl else []))
    if _TROUBLE.search(t):
        return Beat("troubled", "troubled" if excl else "oops", ["shrink"])
    if _SURPRISE.search(t):
        return Beat("surprised", "oops", ["jump_back"])
    if _QUESTION.search(t):
        return Beat("thinking", "think", ["tilt"])
    if _CONFIDENT.search(t):
        return Beat(
            "confident" if not excl else "excited", "confident", ["nod"] + (["hop"] if excl else [])
        )
    if _INSTRUCT.search(t):
        return Beat("excited" if excl else "neutral", "point", ["lean"] + (["hop"] if excl else []))
    if _PRESENT.search(t):
        return Beat("happy", "present", ["lean"] + (["hop"] if excl else []))
    if excl:
        return Beat("excited", "cheer", ["hop"])
    return Beat("neutral", "neutral", ["lean"])


# ---------------------------------------------------------------- 身振り（キーフレーム）
# 値: dx, dy（キャラクターの高さに対する比）、rot（度。正=中央へ傾く）、
#     sx, sy（伸び縮み）、arm（腕の角度）
Key = tuple[int, dict[str, float], str]  # (フレーム@30fps, 値, そこへのイージング)

GESTURES: dict[str, list[Key]] = {
    # 予備動作（沈む）→ 跳ぶ（伸びる）→ 着地（つぶれる）→ 行き過ぎ → 戻る
    "hop": [
        (0, {}, "inout"),
        (4, {"sy": -0.05, "sx": 0.03, "dy": 0.008}, "out"),
        (10, {"dy": -0.04, "sy": 0.04, "sx": -0.02}, "out"),
        (15, {"sy": -0.04, "sx": 0.03}, "in"),
        (20, {"sy": 0.012, "sx": -0.006}, "out"),
        (26, {}, "inout"),
    ],
    "nod": [
        (0, {}, "inout"),
        (5, {"dy": 0.01, "sy": -0.014, "rot": 0.8}, "out"),
        (10, {"dy": -0.002, "sy": 0.004}, "inout"),
        (15, {"dy": 0.007, "sy": -0.01, "rot": 0.5}, "out"),
        (22, {}, "inout"),
    ],
    # 統合版 Q8: 振幅 50% のうなずき（強い句の頭）
    "nod_soft": [
        (0, {}, "inout"),
        (5, {"dy": 0.005, "sy": -0.007, "rot": 0.4}, "out"),
        (10, {"dy": -0.001, "sy": 0.002}, "inout"),
        (15, {"dy": 0.0035, "sy": -0.005, "rot": 0.25}, "out"),
        (22, {}, "inout"),
    ],
    # 驚いて後ろへ（中央から離れる）
    "jump_back": [
        (0, {}, "inout"),
        (3, {"dy": -0.022, "dx": -0.03, "sy": 0.025, "rot": -2.0}, "out"),
        (9, {"dx": -0.035, "rot": -1.0}, "in"),
        (13, {"dx": -0.034, "sy": -0.015}, "out"),
        (30, {}, "inout"),
    ],
    # 手を振る（腕を肩で回す）
    "wave": [
        (0, {"arm": 0}, "inout"),
        (5, {"arm": 14}, "out"),
        (11, {"arm": -7}, "inout"),
        (17, {"arm": 14}, "inout"),
        (23, {"arm": -7}, "inout"),
        (29, {"arm": 10}, "inout"),
        (36, {"arm": 0}, "inout"),
    ],
    # ポーズを変えたときの腕の「ポン」（行き過ぎて戻る）
    "pop": [
        (0, {"arm": -10, "arm_s": -0.08}, "inout"),
        (4, {"arm": 4, "arm_s": 0.03}, "out"),
        (8, {"arm": -1.5, "arm_s": -0.01}, "inout"),
        (12, {}, "inout"),
    ],
    # 表情が変わったときの小さな弾み
    "blip": [(0, {}, "inout"), (3, {"sy": 0.012, "dy": -0.004}, "out"), (8, {}, "inout")],
}
# 続けて保つ身振り（台詞の間ずっと）: 値, 入りのフレーム, 戻りのフレーム
SUSTAIN: dict[str, tuple[dict[str, float], int, int]] = {
    "lean": ({"rot": 2.0, "dx": 0.006}, 9, 12),
    "tilt": ({"rot": -2.6, "dx": -0.004}, 10, 12),
    "shrink": ({"sy": -0.02, "dy": 0.008, "rot": -0.8}, 8, 12),
    "listen": ({"rot": 0.9}, 12, 16),
}


def _ease(u: float, kind: str) -> float:
    u = min(1.0, max(0.0, u))
    if kind == "out":
        return 1 - (1 - u) ** 3
    if kind == "in":
        return u**3
    return 0.5 - 0.5 * math.cos(math.pi * u)


def sample(keys: list[Key], f: float, fps: float) -> dict[str, float]:
    """キーフレームの値（f はこの身振りの始まりからのフレーム）。"""
    k = fps / 30.0
    if f <= 0 or f >= keys[-1][0] * k:
        return {}
    for (f0, v0, _), (f1, v1, ease) in zip(keys, keys[1:], strict=False):
        a, b = f0 * k, f1 * k
        if a <= f < b:
            u = _ease((f - a) / max(1e-6, b - a), ease)
            out = {}
            for name in set(v0) | set(v1):
                out[name] = v0.get(name, 0.0) + (v1.get(name, 0.0) - v0.get(name, 0.0)) * u
            return out
    return {}


# ---------------------------------------------- 台詞の並び → キャラクターごとの予定
@dataclass
class Line:
    speaker: str
    text: str
    start: int
    end: int
    levels: list[int] | None = None  # 口の開き（0/1/2）。start からのフレームごと
    accent: int | None = None  # 統合版 Q8: アクセントの最も強い句の頭（start からのフレーム）


@dataclass
class Event:
    kind: str  # 身振りの名前
    start: int
    end: int = 0  # 続けて保つ身振りの終わり


@dataclass
class Schedule:
    """1 人のキャラクターの予定（区間ごとの表情・ポーズと、身振りの一覧）。"""

    spans: list[tuple[int, int, str, str]] = field(default_factory=list)  # start, end, expr, pose
    events: list[Event] = field(default_factory=list)
    mouth: dict[int, int] = field(default_factory=dict)  # フレーム → 口の開き
    speaking: list[tuple[int, int]] = field(default_factory=list)
    blinks: list[int] = field(default_factory=list)  # 統合版 Q8: 台詞に合わせたまばたきの始まり


def plan(name: str, lines: list[Line], fps: float, seed: int = 0) -> Schedule:
    """name のキャラクターの予定。話す台詞の表情・ポーズ・身振りと、聞き手の反応。"""
    sch = Schedule()
    hold = round(0.45 * fps)  # 話し終わってから元に戻すまで
    mine = [x for x in lines if x.speaker == name]
    for i, ln in enumerate(mine):
        beat = classify(ln.text)
        nxt = mine[i + 1].start if i + 1 < len(mine) else None
        end = ln.end + hold
        if nxt is not None and nxt - ln.end < round(1.2 * fps):
            end = nxt  # 続けて話すときは戻さない
        sch.spans.append((ln.start, end, beat.expression, beat.pose))
        sch.speaking.append((ln.start, ln.end))
        g0 = ln.start + round(0.1 * fps)
        # 統合版 Q8: アクセントの最も強い句の頭で、振幅 50% のうなずき（1 文に 1 回。
        # 身振りにうなずきがある文は足さない）。疑問・驚き・結論の台詞は頭の 5f 前にまばたき
        if ln.accent is not None and "nod" not in beat.gestures:
            sch.events.append(Event("nod_soft", ln.start + ln.accent))
        if beat.pose in ("think", "oops", "confident") and beat.expression != "troubled":
            sch.blinks.append(max(0, ln.start - round(5 * fps / 30)))
        for g in beat.gestures:
            if g in SUSTAIN:
                sch.events.append(Event(g, g0, max(g0 + 1, ln.end)))
            else:
                sch.events.append(Event(g, g0))
        if ln.levels:
            for k, lv in enumerate(ln.levels):
                if lv:
                    sch.mouth[ln.start + k] = lv
        else:  # 音声が読めないとき: 不規則に開閉
            for f in range(ln.start, ln.end):
                h = ((f // 3) * 2654435761 + seed * 97) & 0xFFFF
                if h % 5 < 3:
                    sch.mouth[f] = 1 if h % 7 else 2
    # 聞き手の反応: 相手の文の終わりにうなずき、話している間は相手へ少し向く
    others = [x for x in lines if x.speaker and x.speaker != name]
    for j, ln in enumerate(others):
        sch.events.append(Event("listen", ln.start + round(0.2 * fps), ln.end))
        if j % 2 == seed % 2 or ln.text.rstrip().endswith(("ね", "よ", "わ", "のだ", "！", "!")):
            sch.events.append(Event("nod", max(ln.start, ln.end - round(0.35 * fps))))
    sch.events.sort(key=lambda e: e.start)
    return sch


@dataclass
class State:
    expression: str
    pose: str
    mouth: int
    eyes_closed: bool
    speaking: float  # 0〜1（話している度合い。明るさに使う）
    # 統合版 F1: まばたきの何フレーム目か（0〜3。−1 はまばたきでない）。
    # 開 → 半目 1f → 閉 2f → 半目 1f → 開。半目の部品が無いときは閉じた目 3f（overlay で読み替え）
    blink_phase: int = -1
    dx: float = 0.0
    dy: float = 0.0
    rot: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    arm: float = 0.0  # 身振りで動かす腕の角度
    arm_s: float = 1.0
    pop_arms: tuple[str, ...] = ()


BLINK_FRAMES = 4  # 統合版 F1: 半目 1f → 閉 2f → 半目 1f


class Animator:
    """予定からフレームごとの姿勢を作る（フレームは昇順に呼ぶ）。"""

    def __init__(self, sch: Schedule, fps: float, seed: int = 0, blink_ok: Any = None) -> None:
        self.sch, self.fps, self.seed = sch, fps, seed
        self._speak = 0.0
        self._mouth_prev = 0
        self._mouth_hold = 0
        self._pose_prev: str | None = None
        self._expr_prev: str | None = None
        self._pops: list[tuple[int, tuple[str, ...]]] = []
        self._blips: list[int] = []
        self._blinks = self._blink_frames()

    def _blink_frames(self) -> dict[int, int]:
        """まばたきのフレーム → 何フレーム目か（0〜3。BLINK_FRAMES 枚）。"""
        out: dict[int, int] = {}
        end = max(
            [s[1] for s in self.sch.spans] + [e.end or e.start for e in self.sch.events] + [0]
        )
        end += round(20 * self.fps)
        f = round((1.2 + 0.7 * self.seed) * self.fps)
        r = 12345 + self.seed * 777
        while f < end:
            out.update({f + i: i for i in range(BLINK_FRAMES)})
            r = (r * 1103515245 + 12345) & 0x7FFFFFFF
            if r % 6 == 0:  # たまに 2 回続けて
                out.update({f + 6 + i: i for i in range(BLINK_FRAMES)})
            f += round((2.4 + (r % 1000) / 1000 * 3.0) * self.fps)
        for b in self.sch.blinks:  # 統合版 Q8: 台詞に合わせたまばたき（近い自然なまばたきは消す）
            for g in [x for x, ph in out.items() if ph == 0 and abs(x - b) < 12]:
                for i in range(BLINK_FRAMES):
                    out.pop(g + i, None)
            out.update({b + i: i for i in range(BLINK_FRAMES)})
        return out

    def _span(self, f: int) -> tuple[str, str]:
        for a, b, e, p in self.sch.spans:
            if a <= f < b:
                return e, p
        return "neutral", "neutral"

    def state(self, f: int, arms_of: Any = None) -> State:
        expr, pose = self._span(f)
        talking = any(a <= f < b for a, b in self.sch.speaking)
        self._speak += max(-1 / 6, min(1 / 6, (1.0 if talking else 0.0) - self._speak))
        # 口: 2 フレームより短い開閉はしない
        want = self.sch.mouth.get(f, 0)
        if want != self._mouth_prev and self._mouth_hold < 2:
            want = self._mouth_prev
        if want != self._mouth_prev:
            self._mouth_hold = 0
        self._mouth_hold += 1
        self._mouth_prev = want
        if self._pose_prev is not None and pose != self._pose_prev and arms_of is not None:
            a, b = arms_of(self._pose_prev), arms_of(pose)
            changed = tuple(k for k in b if a.get(k) != b.get(k))
            if changed:
                self._pops.append((f, changed))
        if self._expr_prev is not None and expr != self._expr_prev:
            self._blips.append(f)
        self._pose_prev, self._expr_prev = pose, expr
        phase = self._blinks.get(f, -1)
        st = State(expr, pose, want, phase in (1, 2), self._speak, blink_phase=phase)
        # 呼吸（伸び縮み）と重心の揺れ（傾き）
        t = f / self.fps
        st.sy += 0.006 * math.sin(2 * math.pi * t / 3.4 + self.seed)
        st.rot += 0.35 * math.sin(2 * math.pi * t / 5.3 + 1.7 * self.seed)
        st.dy += -0.0035 * (want / 2)  # 話すときの小さな上下
        acc: dict[str, float] = {}

        def add(v: dict[str, float]) -> None:
            for k, x in v.items():
                acc[k] = acc.get(k, 0.0) + x

        for ev in self.sch.events:
            if ev.start > f:
                break
            if ev.kind in SUSTAIN:
                val, att, rel = SUSTAIN[ev.kind]
                k = self.fps / 30.0
                if f < ev.start or f > ev.end + rel * k:
                    continue
                u = _ease((f - ev.start) / (att * k), "out") if f < ev.start + att * k else 1.0
                if f > ev.end:
                    u *= 1 - _ease((f - ev.end) / (rel * k), "inout")
                add({kk: vv * u for kk, vv in val.items()})
            else:
                add(sample(GESTURES[ev.kind], f - ev.start, self.fps))
        pops = [p for p in self._pops if f - p[0] < 13 * self.fps / 30]
        for f0, arms in pops:
            v = sample(GESTURES["pop"], f - f0 + 0.001, self.fps)
            st.arm += v.get("arm", 0.0)
            st.arm_s += v.get("arm_s", 0.0)
            st.pop_arms = arms
        for f0 in self._blips[-3:]:
            add(sample(GESTURES["blip"], f - f0 + 0.001, self.fps))
        st.dx += acc.get("dx", 0.0)
        st.dy += acc.get("dy", 0.0)
        st.rot += acc.get("rot", 0.0)
        st.sx += acc.get("sx", 0.0)
        st.sy += acc.get("sy", 0.0)
        st.arm += acc.get("arm", 0.0)
        return st


def mouth_levels(samples: Any, rate: int, fps: float, frames: int) -> list[int]:
    """音声（numpy の 1 次元配列）から、フレームごとの口の開き（0=閉じ・1=小・2=大）。"""
    import numpy as np

    x = np.asarray(samples, dtype=np.float32)
    if x.size == 0:
        return [0] * frames
    hop = rate / fps
    rms = np.zeros(frames, np.float32)
    for f in range(frames):
        a, b = int(f * hop), int((f + 1) * hop)
        seg = x[a:b]
        if seg.size:
            rms[f] = float(np.sqrt(np.mean(seg * seg)))
    ref = float(np.percentile(rms[rms > 0], 90)) if np.any(rms > 0) else 1.0
    ref = max(ref, 1e-6)
    lv = rms / ref
    return [0 if v < 0.16 else 1 if v < 0.55 else 2 for v in lv]


# 統合版 S2: 口の形を音素から決める。あ → 大、お → 丸、う → すぼめ、い・え → 中、
# ん・ま行／ば行／ぱ行の子音・無音 → 閉じる。口の形の部品が無い立ち絵では、
# 開きの段（あ・お → 2、い・う・え → 1、閉じる → 0）に置き換える
VOWEL_LEVEL = {"a": 2, "o": 2, "i": 1, "u": 1, "e": 1}
CLOSED_CONSONANTS = ("m", "b", "p")
VISEME_LEAD_FRAMES = 1  # 口を音より 1f 早く動かす
VISEME_MIN_FRAMES = 2  # これより短い形は前の形を続ける


def viseme_levels(query: Any, fps: float, frames: int) -> list[int]:
    """VOICEVOX のクエリ（AudioQuery）から、フレームごとの口の開き（0/1/2）。"""
    speed = query.speedScale if query.speedScale > 0 else 1.0
    out = [0] * frames
    t = (query.prePhonemeLength or 0.0) / speed

    def paint(t0: float, t1: float, lv: int) -> None:
        a = max(0, round(t0 * fps) - VISEME_LEAD_FRAMES)
        b = min(frames, round(t1 * fps) - VISEME_LEAD_FRAMES)
        for f in range(a, b):
            out[f] = lv

    for ap in query.accent_phrases:
        for m in ap.moras:
            cl = (m.consonant_length or 0.0) / speed
            vl = (m.vowel_length or 0.0) / speed
            v = (m.vowel or "").lower()
            if m.consonant and cl > 0:
                closed = m.consonant.lower().startswith(CLOSED_CONSONANTS)
                # 閉じない子音の間は、次の母音の形で口を作り始める
                paint(t, t + cl, 0 if closed else VOWEL_LEVEL.get(v, 0))
            paint(t + cl, t + cl + vl, VOWEL_LEVEL.get(v, 0))  # ん（N）・っ（cl）は閉じる
            t += cl + vl
        if ap.pause_mora is not None:
            pause = (
                query.pauseLength if query.pauseLength is not None else ap.pause_mora.vowel_length
            )
            t += (pause or 0.0) * query.pauseLengthScale / speed
    # 2f 未満の形は前の形を続ける（ぱくぱくとちらつかない）
    i = 0
    while i < frames:
        j = i
        while j < frames and out[j] == out[i]:
            j += 1
        if j - i < VISEME_MIN_FRAMES and i > 0:
            for k in range(i, j):
                out[k] = out[i - 1]
        i = j
    return out
