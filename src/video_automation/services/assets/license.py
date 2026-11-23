"""ライセンス管理（P6-05）。取得元ごとの表記を共通形式に正規化し、利用可否を判定する。

判定方針（仕様 §10「ライセンス不明素材の使用：禁止または明示承認」）:
- allowed  : CC0 / パブリックドメイン / CC BY / CC BY-SA / Pexels・Pixabay ライセンス / 自作・生成物
- needs_ack: 不明・非営利限定（NC）・改変禁止（ND）・Sampling+ 等。ユーザーの明示承認が必要
LLM 由来の要求は明示承認できない（承認は UI/API のユーザー操作のみ）。
"""

import re
from html import unescape

from video_automation.schemas.asset_source import (
    AssetSearchResult,
    LicenseCheck,
    LicenseInfo,
    LicenseVerdict,
    SourceName,
)

PEXELS_LICENSE = LicenseInfo(
    code="pexels",
    name="Pexels License",
    url="https://www.pexels.com/license/",
    attribution_required=False,
    commercial_use=True,
    modification=True,
    notes=[
        "クレジット表記は任意（推奨）",
        "未加工のまま販売・再配布することは不可",
        "写っている人物・ブランドの扱いに注意（承認・不快な使い方の禁止）",
    ],
)

PIXABAY_LICENSE = LicenseInfo(
    code="pixabay",
    name="Pixabay Content License",
    url="https://pixabay.com/service/license-summary/",
    attribution_required=False,
    commercial_use=True,
    modification=True,
    notes=[
        "クレジット表記は任意（推奨）",
        "単体での販売・配布、商標等としての利用は不可",
        "写っている人物・ブランド・作品の権利に注意",
    ],
)

ACESTEP_LICENSE = LicenseInfo(
    code="acestep-generated",
    name="ACE-Step 1.5 で生成（モデル: MIT License）",
    url="https://github.com/ace-step/ACE-Step-1.5",
    attribution_required=False,
    commercial_use=None,
    modification=True,
    notes=[
        "AI生成であることを開示し、既存曲との類似（意図しない著作権侵害）に注意する"
        "（ACE-Step の免責事項）",
    ],
)

_CC_NAMES = {
    "cc0": "CC0 1.0",
    "pdm": "Public Domain Mark",
    "by": "CC BY",
    "by-sa": "CC BY-SA",
    "by-nc": "CC BY-NC",
    "by-nd": "CC BY-ND",
    "by-nc-sa": "CC BY-NC-SA",
    "by-nc-nd": "CC BY-NC-ND",
    "sampling+": "Sampling+",
    "nc-sampling+": "NC Sampling+",
}


def creative_commons(kind: str, version: str | None = None, url: str | None = None) -> LicenseInfo:
    """'by-sa' / 'cc0' / 'pdm' 等から LicenseInfo を作る。不明なら unknown。"""
    k = kind.lower().strip()
    if k not in _CC_NAMES:
        return unknown_license(f"未対応のライセンス表記: {kind}")
    public = k in ("cc0", "pdm")
    if url is None and not public and not k.endswith("sampling+"):
        url = f"https://creativecommons.org/licenses/{k}/{version or '4.0'}/"
    elif url is None and k == "cc0":
        url = "https://creativecommons.org/publicdomain/zero/1.0/"
    elif url is None and k == "pdm":
        url = "https://creativecommons.org/publicdomain/mark/1.0/"
    name = _CC_NAMES[k] + (f" {version}" if version and not public else "")
    return LicenseInfo(
        code="cc0" if k == "cc0" else "pdm" if k == "pdm" else f"cc-{k}",
        name=name,
        version=version,
        url=url,
        attribution_required=not public,
        commercial_use=None if k.endswith("sampling+") else "nc" not in k.split("-"),
        modification="nd" not in k.split("-"),
        share_alike="sa" in k.split("-"),
    )


def unknown_license(note: str = "ライセンス情報を取得できませんでした") -> LicenseInfo:
    return LicenseInfo(
        code="unknown",
        name="不明",
        attribution_required=True,
        commercial_use=None,
        modification=None,
        notes=[note],
    )


_CC_URL = re.compile(
    r"creativecommons\.org/(?:licenses/(?P<kind>[a-z\-+]+)/(?P<ver>[\d.]+)"
    r"|publicdomain/(?P<pd>zero|mark)/(?P<pdver>[\d.]+))",
    re.I,
)


def from_cc_url(url: str) -> LicenseInfo | None:
    m = _CC_URL.search(url or "")
    if not m:
        return None
    if m.group("pd"):
        return creative_commons("cc0" if m.group("pd").lower() == "zero" else "pdm", url=url)
    return creative_commons(m.group("kind"), m.group("ver"), url=url)


def from_short_name(name: str, url: str | None = None) -> LicenseInfo:
    """'CC BY-SA 4.0' / 'Public domain' / 'CC0' / 'Attribution' 等（Wikimedia・Freesound）。"""
    if url:
        info = from_cc_url(url)
        if info:
            return info
    n = (name or "").strip().lower()
    if not n:
        return unknown_license()
    if n in ("cc0", "cc-zero", "creative commons 0") or n.startswith("cc0"):
        return creative_commons("cc0")
    if "public domain" in n or n in ("pd", "pdm"):
        return creative_commons("pdm", url=url)
    if n in ("attribution",):
        return creative_commons("by", url=url)
    if n in ("attribution noncommercial", "attribution non-commercial"):
        return creative_commons("by-nc", url=url)
    if n == "sampling+":
        return creative_commons("sampling+", url=url)
    m = re.match(r"cc[ -]by((?:-(?:nc|nd|sa))*)(?:[ -]([\d.]+))?", n)
    if m:
        return creative_commons("by" + m.group(1), m.group(2), url=url)
    return unknown_license(f"未対応のライセンス表記: {name}")


def evaluate(info: LicenseInfo) -> tuple[LicenseVerdict, list[str]]:
    reasons: list[str] = []
    if info.code == "unknown":
        reasons.append("ライセンスが不明です")
    if info.commercial_use is False:
        reasons.append("非営利目的に限定されています（収益化する動画では使えません）")
    elif info.commercial_use is None and info.code not in ("acestep-generated",):
        reasons.append("商用利用の可否が不明です")
    if info.modification is False:
        reasons.append("改変禁止です（切り抜き・字幕の重ね・加工ができない可能性があります）")
    if reasons:
        return LicenseVerdict.NEEDS_ACK, reasons
    return LicenseVerdict.ALLOWED, []


def attribution_text(r: AssetSearchResult) -> str:
    """TASL（Title / Author / Source / License）形式の帰属表示。"""
    lic = r.license
    if r.source == SourceName.PEXELS:
        who = r.creator or "Unknown"
        kind = "Video" if r.media_type == "video" else "Photo"
        return f"{kind} by {who} on Pexels ({r.landing_url})"
    if r.source == SourceName.PIXABAY:
        who = r.creator or "Unknown"
        return f"{r.media_type.capitalize()} by {who} from Pixabay ({r.landing_url})"
    title = f"“{r.title}”" if r.title else "Untitled"
    by = f" by {r.creator}" if r.creator else ""
    src = f" ({r.landing_url})" if r.landing_url else ""
    lic_part = f", {lic.name}" + (f" ({lic.url})" if lic.url else "")
    return f"{title}{by}{src}{lic_part}"


def check(r: AssetSearchResult) -> LicenseCheck:
    verdict, reasons = evaluate(r.license)
    return LicenseCheck(
        license=r.license, verdict=verdict, reasons=reasons, attribution=attribution_text(r)
    )


def strip_html(s: str | None) -> str | None:
    if s is None:
        return None
    t = unescape(re.sub(r"<[^>]+>", "", s))
    t = re.sub(r"\s+", " ", t).strip()
    return t or None
