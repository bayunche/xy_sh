"""浏览器 Cookie 抓取（CDP）纯函数单测。"""
from xy_gate.browser import cookies_to_header, cookie_is_logged_in


def test_cookies_to_header_dedup():
    cookies = [
        {"name": "unb", "value": "123"},
        {"name": "_m_h5_tk", "value": "abc_123"},
        {"name": "unb", "value": "456"},          # 重复：取首个
        {"name": "empty", "value": ""},            # 空值剔除
        {"name": " x ", "value": "1"},             # 名称带空白剔除
    ]
    header = cookies_to_header(cookies)
    assert "unb=123" in header
    assert "_m_h5_tk=abc_123" in header
    assert "unb=456" not in header
    assert "empty" not in header


def test_logged_in_check():
    assert cookie_is_logged_in("unb=1; " + "x=y; " * 60) is True
    assert cookie_is_logged_in("unb=1") is False            # 太短
    assert cookie_is_logged_in("cookie2=abc; " * 50) is False  # 缺 unb
