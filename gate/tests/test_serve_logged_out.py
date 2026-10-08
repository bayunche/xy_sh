# -*- coding: utf-8 -*-
"""未登录模式：桌面版首启没有 accounts.yaml，serve 必须能起（stub 账号 +
daemon 跳过 WS），而不是 load_account 直接把 daemon 干崩。"""
from pathlib import Path

from xy_gate.config import Account, load_account


def test_load_account_optional_missing_file(tmp_path):
    """required=False：无 accounts.yaml → 空 stub 而非 KeyError。"""
    acc = load_account("default", config_dir=tmp_path, required=False)
    assert acc == Account(name="default", cookies="")
    assert acc.user_id == ""


def test_load_account_optional_no_unb(tmp_path):
    """required=False：Cookie 缺 unb → 空 stub 而非 ValueError。"""
    (tmp_path / "accounts.yaml").write_text(
        'accounts:\n  default:\n    cookies: "foo=1"\n', encoding="utf-8")
    acc = load_account("default", config_dir=tmp_path, required=False)
    assert acc.cookies == ""


def test_load_account_optional_valid_passthrough(tmp_path):
    """正常账号不受 required=False 影响。"""
    (tmp_path / "accounts.yaml").write_text(
        'accounts:\n  default:\n    cookies: "unb=123; x=1"\n', encoding="utf-8")
    acc = load_account("default", config_dir=tmp_path, required=False)
    assert "unb=123" in acc.cookies


def test_load_account_required_still_raises(tmp_path):
    """required=True（CLI 直连等场景）：保持原严格报错。"""
    try:
        load_account("default", config_dir=tmp_path)
    except KeyError:
        pass
    else:
        raise AssertionError("missing account should raise KeyError")
