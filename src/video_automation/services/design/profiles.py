"""用途ごとのデザインの型（スキル video-design-by-purpose の許可表と数値をコードにしたもの）。

型は 4 つの系統（落ち着いた系・情報系・勢い系・表現系）の既定値に、用途ごとの差分を重ねて作る。
色は「背景（暗）・背景（明）・面・メイン・アクセント・文字・弱い文字」の役割で持ち、
話者や図表の色もメインとアクセントの明暗違いで作る（色相を増やさない）。
"""

from video_automation.schemas.design import (
    ANY,
    DENY,
    Component,
    DesignProfile,
    Family,
    Palette,
    ProfileSummary,
    Purpose,
    SafeArea,
    SubtitleSpec,
    Typography,
)

C = Component

# ---------------------------------------------------------------- 系統ごとの部品の許可
_CALM = {
    C.SUBTITLE: ANY,
    C.SPEAKER_LABEL: ANY,
    C.TITLE_CARD: 1,
    C.LOWER_THIRD: 3,
    C.BACKGROUND_MOTION: ANY,
    C.TRANSITION_CUT: ANY,
}
_INFO = {
    C.SUBTITLE: ANY,
    C.SPEAKER_LABEL: ANY,
    C.TITLE_CARD: 1,
    C.CARD: ANY,
    C.CHART: ANY,
    C.IMAGE: ANY,
    C.KEYWORD: ANY,
    C.CHAPTER_LABEL: ANY,
    C.LOWER_THIRD: 2,
    C.EMPHASIS_TEXT: 3,
    C.KINETIC_TEXT: 1,
    C.BACKGROUND_MOTION: ANY,
    C.TRANSITION_CUT: ANY,
    C.CHARACTER: ANY,
}
_ENERGETIC = {
    C.SUBTITLE: ANY,
    C.TITLE_CARD: ANY,
    C.CARD: 3,
    C.CHART: 3,
    C.IMAGE: ANY,
    C.KEYWORD: ANY,
    C.LOWER_THIRD: 2,
    C.EMPHASIS_TEXT: ANY,
    C.KINETIC_TEXT: ANY,
    C.BACKGROUND_MOTION: ANY,
    C.TRANSITION_CUT: ANY,
    C.TRANSITION_SHAPE: ANY,
    C.TRANSITION_WHIP: ANY,
    C.SHAKE_PUNCH: ANY,
    C.FLASH: 6,
    C.GLITCH: 4,
    C.BLOOM: ANY,
    C.SFX: ANY,
    C.CHARACTER: ANY,
}
_EXPRESSIVE = {
    C.TITLE_CARD: ANY,
    C.IMAGE: ANY,
    C.EMPHASIS_TEXT: ANY,
    C.KINETIC_TEXT: ANY,
    C.LYRIC_TEXT: ANY,
    C.BACKGROUND_MOTION: ANY,
    C.TRANSITION_CUT: ANY,
    C.TRANSITION_SHAPE: ANY,
    C.TRANSITION_WHIP: ANY,
    C.SHAKE_PUNCH: ANY,
    C.FLASH: ANY,
    C.GLITCH: ANY,
    C.BLOOM: ANY,
    C.LETTERBOX: ANY,
    C.BEAT_CUT: ANY,
    C.CHARACTER: ANY,
}
_FAMILY_COMPONENTS = {
    Family.CALM: _CALM,
    Family.INFO: _INFO,
    Family.ENERGETIC: _ENERGETIC,
    Family.EXPRESSIVE: _EXPRESSIVE,
}

# ---------------------------------------------------------------- 字幕・安全範囲・書体
_SUB = {
    Family.CALM: SubtitleSpec(
        size_ratio=0.058,
        size_range=(0.05, 0.07),
        band="fitted",
        max_chars_per_line=16,
        max_lines=2,
        chars_per_sec=6.0,
    ),
    Family.INFO: SubtitleSpec(
        size_ratio=0.054,
        size_range=(0.045, 0.065),
        band="fitted",
        max_chars_per_line=18,
        max_lines=2,
        chars_per_sec=7.0,
    ),
    Family.ENERGETIC: SubtitleSpec(
        size_ratio=0.062,
        size_range=(0.05, 0.085),
        band="none",
        max_chars_per_line=14,
        max_lines=2,
        chars_per_sec=8.0,
    ),
    Family.EXPRESSIVE: SubtitleSpec(
        size_ratio=0.05,
        size_range=(0.04, 0.07),
        band="none",
        max_chars_per_line=20,
        max_lines=1,
        chars_per_sec=8.0,
    ),
}
_LANDSCAPE = SafeArea(top=0.05, bottom=0.05, left=0.05, right=0.05)
_VERTICAL = SafeArea(top=0.1, bottom=0.2, left=0.05, right=0.12)  # 1080x1920 の UI 位置
_MOTION = {Family.CALM: 0, Family.INFO: 1, Family.ENERGETIC: 3, Family.EXPRESSIVE: 4}
_HUES = {Family.CALM: 3, Family.INFO: 3, Family.ENERGETIC: 4, Family.EXPRESSIVE: 4}
_CONCURRENT = {Family.CALM: 1, Family.INFO: 3, Family.ENERGETIC: 3, Family.EXPRESSIVE: 2}
# 背景だけの時間の上限（秒）。落ち着いた系・表現系は映像そのものが主役なので制限しない
_EMPTY = {Family.CALM: None, Family.INFO: 10.0, Family.ENERGETIC: 5.0, Family.EXPRESSIVE: None}
_UD = Typography(body_font="BIZ UDPGothic", heading_font="BIZ UDPGothic", max_fonts=1)
_GOTHIC = Typography(body_font="Meiryo", heading_font="Meiryo", max_fonts=2)


def _pal(
    base: str,
    base2: str,
    surface: str,
    main: str,
    accent: str,
    text: str,
    muted: str,
    speakers: list[str] | None = None,
    chart: list[str] | None = None,
) -> Palette:
    return Palette(
        base=base,
        base2=base2,
        surface=surface,
        main=main,
        accent=accent,
        text=text,
        text_muted=muted,
        speakers=speakers or [main, accent],
        chart=chart or [accent, main, muted],
    )


# ---------------------------------------------------------------- 配色（役割ごと。色相は 2〜3）
# 文字に使う色（text・text_muted・main・accent・話者・図表）は、base と base2 の両方に対して
# コントラスト比 4.5 以上（統合版 G1。OKLCH で色相を保ち明るさだけを変えて置き換えた）
P_CALM = _pal("#101318", "#1C222B", "#14181F", "#8FB8DE", "#E9C46A", "#F5F7FA", "#B5BEC9")
P_INFO = _pal(
    "#0C1A2E",
    "#173554",
    "#102238",
    "#4CB3E6",
    "#F2B544",
    "#F4F7FB",
    "#9DB0C4",
    speakers=["#4CB3E6", "#F2B544", "#8ED1F0", "#F7D38A"],
    chart=["#F2B544", "#4CB3E6", "#869FBC", "#8ED1F0"],
)
P_LECTURE = _pal("#E9EDF2", "#FFFFFF", "#FFFFFF", "#1F4E79", "#A55200", "#1D232B", "#5B6673")
P_TUTORIAL = _pal("#15171C", "#2A2F38", "#1E2229", "#5FB3A1", "#F0B429", "#F3F5F7", "#A3ACB8")
P_RECIPE = _pal("#2A1E17", "#4A3326", "#2F231B", "#E9A46A", "#8CC084", "#FFF8F0", "#D8C3B0")
P_NEWS = _pal(
    "#0A1730",
    "#13284F",
    "#0E2244",
    "#4F90FA",
    "#FF535E",
    "#FFFFFF",
    "#B8C4D6",
    speakers=["#4F90FA", "#9DB8E8"],
)
P_CORP = _pal("#0F1C2B", "#24384F", "#13243A", "#59A5F4", "#2EC4B6", "#F4F7FA", "#A9B7C6")
P_POP = _pal("#111111", "#2B2B2B", "#1A1A1A", "#FFD23F", "#FF5865", "#FFFFFF", "#CFCFCF")
P_GAME = _pal("#0D0F1A", "#23194A", "#14172A", "#8E73FF", "#39E5A0", "#FFFFFF", "#B9B4D9")
P_VLOG = _pal("#1E1B18", "#3A322B", "#25211D", "#F4E3C1", "#F28C6B", "#FFFDF8", "#CFC4B5")
P_KIDS = _pal("#FFF6E5", "#FFFFFF", "#FFFFFF", "#BC4D00", "#0075B8", "#2B2B2B", "#6B6B6B")
P_PRODUCT = _pal("#0E0F12", "#262A33", "#16181D", "#3B92FF", "#FFB020", "#FFFFFF", "#B7BDC8")
P_MV = _pal("#07070C", "#1A1030", "#0F0B1A", "#C9B6FF", "#FF5DA2", "#F7F5FF", "#A99FC4")
P_TRAILER = _pal("#050505", "#1E1A14", "#0E0C0A", "#D6C29A", "#E05847", "#F5F1E8", "#A89F8E")


def _make(
    purpose: Purpose,
    family: Family,
    label: str,
    summary: str,
    palette: Palette,
    keywords: list[str],
    *,
    orientation: str = "landscape",
    typo: Typography | None = None,
    allow: dict[Component, int] | None = None,
    motion: int | None = None,
    subtitle: SubtitleSpec | None = None,
    hues: int | None = None,
) -> DesignProfile:
    comps = dict(_FAMILY_COMPONENTS[family])
    for k, v in (allow or {}).items():
        if v == DENY:
            comps.pop(k, None)
        else:
            comps[k] = v
    return DesignProfile(
        purpose=purpose,
        family=family,
        label=label,
        summary=summary,
        orientation=orientation,
        motion_level=_MOTION[family] if motion is None else motion,
        max_hues=hues or _HUES[family],
        max_concurrent=_CONCURRENT[family],
        palette=palette,
        typography=typo or _GOTHIC,
        subtitle=subtitle or _SUB[family],
        safe_area=_VERTICAL if orientation == "vertical" else _LANDSCAPE,
        components=comps,
        keywords=keywords,
        max_empty_sec=_EMPTY[family],
    )


P = Purpose
F = Family
PROFILES: dict[Purpose, DesignProfile] = {
    p.purpose: p
    for p in [
        # ---------------- 落ち着いた系
        _make(
            P.SUBTITLE,
            F.CALM,
            "字幕動画",
            "映像と話者が主役。文字は読ませるためだけ",
            P_CALM,
            ["字幕", "字幕を付け", "テロップを付け", "文字起こし", "書き起こし", "会話"],
        ),
        _make(
            P.INTERVIEW,
            F.CALM,
            "インタビュー・対談",
            "話者と言葉が主役。名前と肩書きを正確に",
            P_CALM,
            ["インタビュー", "対談", "取材", "鼎談", "トークセッション"],
            allow={C.LOWER_THIRD: ANY},
        ),
        _make(
            P.DOCUMENTARY,
            F.CALM,
            "ドキュメンタリー",
            "事実と映像の重み。装飾は最小",
            P_CALM,
            ["ドキュメンタリー", "記録映像", "密着"],
            allow={C.LOWER_THIRD: ANY, C.CHAPTER_LABEL: ANY, C.LETTERBOX: ANY},
        ),
        _make(
            P.STORY,
            F.CALM,
            "朗読・物語",
            "声と情景。文字は控えめに",
            P_CALM,
            ["朗読", "物語", "読み聞かせ", "オーディオドラマ", "怪談", "小説"],
            allow={C.CHARACTER: ANY, C.IMAGE: ANY, C.SPEAKER_LABEL: DENY},
        ),
        _make(
            P.PODCAST,
            F.CALM,
            "ラジオ・ポッドキャスト",
            "声が主役。話者名と話題だけ",
            P_CALM,
            ["ポッドキャスト", "ラジオ", "雑談配信", "音声配信"],
            allow={C.CHAPTER_LABEL: ANY, C.CHARACTER: ANY},
        ),
        # ---------------- 情報系
        _make(
            P.EXPLAINER,
            F.INFO,
            "解説",
            "図と要点で整理して伝える（キャラの掛け合いを含む）",
            P_INFO,
            [
                "解説",
                "説明して",
                "教えて",
                "とは",
                "仕組み",
                "ゆっくり",
                "ずんだもん",
                "掛け合い",
                "教養",
            ],
            allow={C.TRANSITION_SHAPE: 8, C.SHAKE_PUNCH: 3, C.BLOOM: 1, C.LETTERBOX: 1, C.SFX: 12},
        ),
        _make(
            P.LECTURE,
            F.INFO,
            "講義・スライド",
            "資料が主役。明るい紙面で読みやすく",
            P_LECTURE,
            ["講義", "授業", "講座", "スライド", "プレゼン", "セミナー", "研修"],
            typo=_UD,
            allow={C.EMPHASIS_TEXT: 2, C.KINETIC_TEXT: DENY},
        ),
        _make(
            P.TUTORIAL,
            F.INFO,
            "操作説明・ハウツー",
            "手順と画面。今どこを操作するかを示す",
            P_TUTORIAL,
            [
                "使い方",
                "操作",
                "手順",
                "ハウツー",
                "やり方",
                "設定方法",
                "チュートリアル",
                "画面録画",
            ],
            allow={C.EMPHASIS_TEXT: 5, C.KINETIC_TEXT: DENY, C.SFX: 10},
        ),
        _make(
            P.RECIPE,
            F.INFO,
            "料理・手順もの",
            "材料と手順を順番に。温かい色",
            P_RECIPE,
            ["レシピ", "料理", "作り方", "材料", "調理"],
            allow={C.EMPHASIS_TEXT: 5, C.SFX: 10, C.CHAPTER_LABEL: ANY},
        ),
        _make(
            P.NEWS,
            F.INFO,
            "ニュース・情報",
            "事実・数値・出典。中立と信頼",
            P_NEWS,
            ["ニュース", "速報", "報道", "情報番組", "時事"],
            typo=_UD,
            hues=2,
            allow={
                C.LOWER_THIRD: ANY,
                C.EMPHASIS_TEXT: DENY,
                C.KINETIC_TEXT: DENY,
                C.CHARACTER: DENY,
            },
        ),
        _make(
            P.CORPORATE,
            F.INFO,
            "会社紹介・採用",
            "信頼と人。整った紙面と控えめな動き",
            P_CORP,
            ["会社紹介", "企業紹介", "採用", "リクルート", "IR", "事業紹介", "社内"],
            allow={
                C.TRANSITION_SHAPE: 6,
                C.KINETIC_TEXT: 2,
                C.BLOOM: 1,
                C.LETTERBOX: 1,
                C.LOWER_THIRD: ANY,
                C.CHARACTER: DENY,
            },
        ),
        # ---------------- 勢い系
        _make(
            P.SHORT,
            F.ENERGETIC,
            "縦型ショート",
            "大きな要点と顔。最初の 1 秒で掴む",
            P_POP,
            ["ショート", "縦動画", "縦型", "tiktok", "リール", "shorts"],
            orientation="vertical",
        ),
        _make(
            P.GAME,
            F.ENERGETIC,
            "ゲーム実況",
            "プレイ画面が主役。反応を強弱で",
            P_GAME,
            ["ゲーム実況", "実況", "プレイ動画", "攻略"],
            allow={C.SPEAKER_LABEL: ANY, C.CARD: 2, C.CHART: DENY},
        ),
        _make(
            P.VLOG,
            F.ENERGETIC,
            "Vlog・日常",
            "空気感。柔らかい色と軽いテンポ",
            P_VLOG,
            ["vlog", "ブイログ", "日常", "ルーティン", "休日"],
            motion=2,
            allow={
                C.GLITCH: DENY,
                C.FLASH: DENY,
                C.CHART: DENY,
                C.CARD: DENY,
                C.BEAT_CUT: ANY,
                C.SHAKE_PUNCH: 3,
            },
        ),
        _make(
            P.RANKING,
            F.ENERGETIC,
            "ランキング・まとめ",
            "順位と見出しをはっきり",
            P_POP,
            ["ランキング", "トップ10", "まとめ動画", "ベスト10", "ベスト5"],
            allow={C.CHAPTER_LABEL: ANY, C.CARD: ANY, C.GLITCH: 2},
        ),
        _make(
            P.PRODUCT,
            F.ENERGETIC,
            "商品紹介・レビュー",
            "商品と利点。比較は図で",
            P_PRODUCT,
            ["商品紹介", "レビュー", "開封", "比較", "おすすめ", "使ってみた"],
            motion=2,
            allow={C.CARD: ANY, C.CHART: ANY, C.GLITCH: DENY, C.FLASH: 2},
        ),
        _make(
            P.PROMO,
            F.ENERGETIC,
            "PR・CM・告知",
            "印象と行動の呼びかけ。ブランドの色",
            P_PRODUCT,
            ["pr", "cm", "広告", "告知", "宣伝", "プロモーション", "キャンペーン"],
            allow={C.LETTERBOX: 2, C.BEAT_CUT: ANY, C.SUBTITLE: ANY, C.CARD: 2},
        ),
        _make(
            P.EVENT,
            F.ENERGETIC,
            "イベント・旅行ハイライト",
            "臨場感。音楽に合わせたカット",
            P_VLOG,
            ["ハイライト", "イベント", "旅行", "旅動画", "ダイジェスト", "アフタームービー"],
            allow={C.BEAT_CUT: ANY, C.LETTERBOX: 2, C.CHART: DENY, C.CARD: DENY},
        ),
        _make(
            P.KIDS,
            F.ENERGETIC,
            "子ども向け",
            "大きく・明るく・やさしく。刺激は控えめ",
            P_KIDS,
            ["子ども向け", "こども", "キッズ", "幼児", "知育", "童謡"],
            motion=2,
            allow={C.GLITCH: DENY, C.FLASH: DENY, C.SHAKE_PUNCH: 3, C.TRANSITION_WHIP: DENY},
        ),
        # ---------------- 表現系
        _make(
            P.MV,
            F.EXPRESSIVE,
            "ミュージックビデオ",
            "曲と世界観が主役。拍に合わせる",
            P_MV,
            ["mv", "ミュージックビデオ", "pv", "プロモーションビデオ", "楽曲の映像"],
        ),
        _make(
            P.LYRIC,
            F.EXPRESSIVE,
            "リリックビデオ",
            "歌詞を見せる。文字の動きが主役",
            P_MV,
            ["リリック", "歌詞動画", "歌詞を", "文字pv", "lyric"],
        ),
        _make(
            P.TRAILER,
            F.EXPRESSIVE,
            "予告編・ティザー",
            "期待を高める。間と強い一言",
            P_TRAILER,
            ["予告", "トレーラー", "ティザー", "teaser", "trailer"],
            allow={C.LYRIC_TEXT: DENY, C.SFX: ANY, C.SUBTITLE: 3},
        ),
    ]
}


def profile(purpose: Purpose) -> DesignProfile:
    return PROFILES[purpose]


def summarize(p: DesignProfile) -> ProfileSummary:
    allowed = [c for c, v in p.components.items() if v == ANY]
    limited = {c: v for c, v in p.components.items() if v > 0}
    denied = [c for c in Component if c not in p.components]
    return ProfileSummary(
        purpose=p.purpose,
        family=p.family,
        label=p.label,
        summary=p.summary,
        orientation=p.orientation,
        motion_level=p.motion_level,
        allowed=allowed,
        limited=limited,
        denied=denied,
    )
