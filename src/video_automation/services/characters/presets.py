"""立ち絵 PSD の使い方（レシピ）。

PSDTool の約束（名前の先頭 ``!`` は必ず 1 つ選ぶ・``*`` は兄弟の中から 1 つ選ぶ）で作られた
PSD を、表情（目・眉・口・頬・記号）と腕のポーズの「部品」に分ける設定。

- ``base``: 部品以外の表示（衣装・小物など）。グループの道筋 → 表示する子の名前の一覧
- ``slots``: 差し替える部品。グループの道筋と、選択肢 → 表示する子（グループからの相対の道筋）
- ``expressions``: 意味のある表情 → 部品の選択肢（口は閉じ・小・大の 3 段）
- ``poses``: 意味のあるポーズ → 腕の選択肢
- ``faces``: 元の絵が向いている向き（left=画面の左を向く）。画面の中央を向くよう左右反転に使う

レシピは PSD の層の名前だけを参照する（絵そのものは含まない）。同じ作者の別の PSD にも、
名前が同じなら使える。自動のレシピ（auto_recipe）はキーワードで推定する。
"""

from __future__ import annotations

from typing import Any

# 意味のある表情とポーズ（台詞から選ぶ）。パックに無いものは neutral を使う
EXPRESSIONS = ("neutral", "happy", "excited", "surprised", "thinking", "troubled", "confident")
POSES = (
    "neutral",
    "point",
    "present",
    "wave",
    "think",
    "confident",
    "oops",
    "cheer",
    "troubled",
    "whisper",
)


def _eyes_set(white: str, pupil: str, group: str = "*目セット", pupils: str = "!黒目") -> list[str]:
    return [group, f"{group}/{white}", f"{group}/{pupils}", f"{group}/{pupils}/{pupil}"]


ZUNDAMON: dict[str, Any] = {
    "match": ["!枝豆", "!口", "!目", "!眉"],
    "name": "ずんだもん",
    "author": "坂本アヒル",
    "faces": "left",
    "base": {
        "": ["尻尾的なアレ", "*服装1", "!口", "!顔色", "!目", "!眉", "!枝豆", "記号など"],
        "*服装1": ["*いつもの服", "!左腕", "!右腕"],
        "!枝豆": ["*枝豆通常"],
    },
    "slots": {
        "arm_l": {
            "group": "*服装1/!左腕",
            "options": {
                k: [f"*{k}"]
                for k in ("基本", "腰", "手を挙げる", "考える", "口元", "ひそひそ", "苦しむ")
            },
        },
        "arm_r": {
            "group": "*服装1/!右腕",
            "options": {
                k: [f"*{k}"]
                for k in ("基本", "腰", "指差し", "手を挙げる", "口元", "マイク", "苦しむ")
            },
        },
        "cheek": {
            "group": "!顔色",
            "options": {
                "なし": ["*(非表示)"],
                "ほっぺ": ["*ほっぺ"],
                "ほっぺ赤め": ["*ほっぺ赤め"],
                "青ざめ": ["*青ざめ"],
            },
        },
        "eyes": {
            "group": "!目",
            "options": {
                "open": _eyes_set("*普通白目", "*カメラ目線"),
                "wide": _eyes_set("*見開き白目", "*普通目"),
                "closed": ["*UU"],
                "smile": ["*にっこり"],
                "up": ["*上向き"],
                "relaxed": ["*なごみ目"],
                "jito": ["*ジト目"],
            },
        },
        "brows": {
            "group": "!眉",
            "options": {
                "普通": ["*普通眉"],
                "上がり": ["*上がり眉"],
                "困り": ["*困り眉1"],
                "困り2": ["*困り眉2"],
                "怒り": ["*怒り眉"],
            },
        },
        "mouth": {
            "group": "!口",
            "options": {
                k: [f"*{k}"]
                for k in (
                    "ほあー",
                    "ほあ",
                    "ほー",
                    "むふ",
                    "△",
                    "んあー",
                    "んへー",
                    "んー",
                    "はへえ",
                    "お",
                    "ゆ",
                    "むー",
                )
            },
        },
        "symbol": {
            "group": "記号など",
            "options": {"なし": [], "汗": ["汗1"], "汗2": ["汗2"], "涙": ["涙"]},
        },
    },
    "blink": "closed",
    "expressions": {
        "neutral": {
            "eyes": "open",
            "brows": "普通",
            "cheek": "ほっぺ",
            "mouth": ["むふ", "ほあ", "ほあー"],
        },
        "happy": {
            "eyes": "smile",
            "brows": "普通",
            "cheek": "ほっぺ赤め",
            "mouth": ["むふ", "ほあ", "ほあー"],
            "blink": False,
        },
        "excited": {
            "eyes": "open",
            "brows": "上がり",
            "cheek": "ほっぺ赤め",
            "mouth": ["むふ", "ほあー", "ほあー"],
        },
        "surprised": {
            "eyes": "wide",
            "brows": "上がり",
            "cheek": "ほっぺ",
            "mouth": ["お", "お", "ほー"],
        },
        "thinking": {
            "eyes": "up",
            "brows": "困り2",
            "cheek": "ほっぺ",
            "mouth": ["んー", "ほー", "ほー"],
        },
        "troubled": {
            "eyes": "open",
            "brows": "困り",
            "cheek": "ほっぺ",
            "mouth": ["むー", "んへー", "んあー"],
            "symbol": "汗",
        },
        "confident": {
            "eyes": "relaxed",
            "brows": "上がり",
            "cheek": "ほっぺ",
            "mouth": ["むふ", "ほあ", "ほあー"],
            "blink": False,
        },
    },
    "poses": {
        "neutral": {"arm_l": "基本", "arm_r": "腰"},
        "point": {"arm_l": "基本", "arm_r": "指差し"},
        "present": {"arm_l": "腰", "arm_r": "指差し"},
        "wave": {"arm_l": "基本", "arm_r": "手を挙げる"},
        "think": {"arm_l": "考える", "arm_r": "腰"},
        "confident": {"arm_l": "腰", "arm_r": "腰"},
        "oops": {"arm_l": "基本", "arm_r": "口元"},
        "cheer": {"arm_l": "手を挙げる", "arm_r": "腰"},
        "troubled": {"arm_l": "苦しむ", "arm_r": "基本"},
        "whisper": {"arm_l": "ひそひそ", "arm_r": "腰"},
    },
    # 手を振る・差すときに動かす腕（ポーズ → 部品の名前）
    "gesture_arm": {"wave": "arm_r", "point": "arm_r", "present": "arm_r"},
}

METAN: dict[str, Any] = {
    "match": ["ツインドリル右", "!口", "!目", "!眉", "*白ロリ服"],
    "name": "四国めたん",
    "author": "坂本アヒル",
    "faces": "left",
    "base": {
        "": [
            "ツインドリル右",
            "ツインドリル左",
            "*白ロリ服",
            "!顔色",
            "!口",
            "!目",
            "!眉",
            "頭部アクセサリ",
            "!前髪もみあげ",
            "記号など",
        ],
        "*白ロリ服": ["!体", "!右腕", "!左腕"],
        "頭部アクセサリ": ["髪留めハート", "ヘッドドレス"],
    },
    "slots": {
        "arm_r": {
            "group": "*白ロリ服/!右腕",
            "options": {k: [f"*{k}"] for k in ("普通", "指差す", "手をかざす")},
        },
        "arm_l": {
            "group": "*白ロリ服/!左腕",
            "options": {k: [f"*{k}"] for k in ("普通", "抱える", "マイク", "ひそひそ", "口元に指")},
        },
        "cheek": {
            "group": "!顔色",
            "options": {
                "なし": ["*(非表示)"],
                "普通": ["*普通2"],
                "赤面": ["*赤面"],
                "青ざめ": ["*青ざめ"],
            },
        },
        "eyes": {
            "group": "!目",
            "options": {
                "open": _eyes_set("*普通白目", "*カメラ目線"),
                "wide": _eyes_set("*見開き白目", "*カメラ目線"),
                "closed": ["*目閉じ"],
                "smile": ["*目閉じ2"],
                "up": ["*見上げ"],
                "side": _eyes_set("*普通白目", "*目そらし"),
            },
        },
        "brows": {
            "group": "!眉",
            "options": {
                "ごきげん": ["*太眉ごきげん"],
                "こまり": ["*太眉こまり"],
                "おこ": ["*太眉おこ"],
                "細ごきげん": ["*ごきげん"],
                "細こまり": ["*こまり"],
            },
        },
        "mouth": {
            "group": "!口",
            "options": {
                k: [f"*{k}"]
                for k in (
                    "もむー",
                    "んー",
                    "うえー",
                    "いー",
                    "む",
                    "△",
                    "ゆ",
                    "お",
                    "にやり",
                    "▽",
                    "ほほえみ",
                    "わあー",
                )
            },
        },
        "symbol": {
            "group": "記号など",
            "options": {"なし": [], "汗": ["汗"], "涙": ["涙"]},
        },
    },
    "blink": "closed",
    "expressions": {
        "neutral": {
            "eyes": "open",
            "brows": "ごきげん",
            "cheek": "普通",
            "mouth": ["ほほえみ", "▽", "わあー"],
        },
        "happy": {
            "eyes": "smile",
            "brows": "ごきげん",
            "cheek": "赤面",
            "mouth": ["ほほえみ", "▽", "わあー"],
            "blink": False,
        },
        "excited": {
            "eyes": "open",
            "brows": "ごきげん",
            "cheek": "赤面",
            "mouth": ["ほほえみ", "わあー", "わあー"],
        },
        "surprised": {
            "eyes": "wide",
            "brows": "細こまり",
            "cheek": "普通",
            "mouth": ["お", "お", "わあー"],
        },
        "thinking": {
            "eyes": "up",
            "brows": "こまり",
            "cheek": "普通",
            "mouth": ["む", "ゆ", "お"],
        },
        "troubled": {
            "eyes": "open",
            "brows": "こまり",
            "cheek": "普通",
            "mouth": ["もむー", "うえー", "うえー"],
            "symbol": "汗",
        },
        "confident": {
            "eyes": "side",
            "brows": "ごきげん",
            "cheek": "普通",
            "mouth": ["にやり", "▽", "わあー"],
        },
    },
    "poses": {
        "neutral": {"arm_l": "普通", "arm_r": "普通"},
        "point": {"arm_l": "普通", "arm_r": "指差す"},
        "present": {"arm_l": "普通", "arm_r": "手をかざす"},
        "wave": {"arm_l": "普通", "arm_r": "手をかざす"},
        "think": {"arm_l": "口元に指", "arm_r": "普通"},
        "confident": {"arm_l": "抱える", "arm_r": "普通"},
        "oops": {"arm_l": "口元に指", "arm_r": "普通"},
        "cheer": {"arm_l": "抱える", "arm_r": "手をかざす"},
        "troubled": {"arm_l": "抱える", "arm_r": "普通"},
        "whisper": {"arm_l": "ひそひそ", "arm_r": "普通"},
    },
    "gesture_arm": {"wave": "arm_r", "point": "arm_r", "present": "arm_r"},
}

PRESETS = [ZUNDAMON, METAN]


def find_preset(names: set[str]) -> dict[str, Any] | None:
    """PSD の層の名前の集合に合うレシピ（match の名前がすべてあるもの）。"""
    for p in PRESETS:
        if all(m in names for m in p["match"]):
            return p
    return None
