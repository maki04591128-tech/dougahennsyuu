import pytest

from video_automation.schemas.asset_source import LicenseVerdict
from video_automation.services.assets.license import (
    creative_commons,
    evaluate,
    from_cc_url,
    from_short_name,
    strip_html,
)


@pytest.mark.parametrize(
    ("name", "url", "code", "verdict"),
    [
        ("CC BY-SA 4.0", None, "cc-by-sa", LicenseVerdict.ALLOWED),
        ("CC BY 2.0", None, "cc-by", LicenseVerdict.ALLOWED),
        ("Public domain", None, "pdm", LicenseVerdict.ALLOWED),
        ("CC0", None, "cc0", LicenseVerdict.ALLOWED),
        ("Creative Commons 0", None, "cc0", LicenseVerdict.ALLOWED),
        ("Attribution", None, "cc-by", LicenseVerdict.ALLOWED),
        ("Attribution NonCommercial", None, "cc-by-nc", LicenseVerdict.NEEDS_ACK),
        ("CC BY-NC-ND 3.0", None, "cc-by-nc-nd", LicenseVerdict.NEEDS_ACK),
        ("Sampling+", None, "cc-sampling+", LicenseVerdict.NEEDS_ACK),
        ("GFDL", None, "unknown", LicenseVerdict.NEEDS_ACK),
        ("", None, "unknown", LicenseVerdict.NEEDS_ACK),
        (
            "whatever",
            "http://creativecommons.org/licenses/by-nd/4.0/",
            "cc-by-nd",
            LicenseVerdict.NEEDS_ACK,
        ),
        ("x", "https://creativecommons.org/publicdomain/zero/1.0/", "cc0", LicenseVerdict.ALLOWED),
    ],
)
def test_normalize_and_evaluate(name, url, code, verdict):
    info = from_short_name(name, url)
    assert info.code == code
    assert evaluate(info)[0] == verdict


def test_cc_details():
    by_sa = creative_commons("by-sa", "2.0")
    assert by_sa.share_alike and by_sa.attribution_required and by_sa.commercial_use
    assert by_sa.url == "https://creativecommons.org/licenses/by-sa/2.0/"
    nd = from_cc_url("https://creativecommons.org/licenses/by-nd/4.0/")
    assert nd is not None and nd.modification is False and nd.version == "4.0"
    reasons = evaluate(creative_commons("by-nc-nd"))[1]
    assert any("非営利" in r for r in reasons) and any("改変禁止" in r for r in reasons)
    assert not creative_commons("cc0").attribution_required


def test_strip_html():
    assert strip_html('<a href="x">Taro &amp; Co</a>\n ') == "Taro & Co"
    assert strip_html("<br>") is None
