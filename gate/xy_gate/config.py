"""配置装载：robot.yaml（行为/风控）+ accounts.yaml（登录态）。

约定：
- robot.yaml 与 accounts.yaml 放在仓库根 config/ 下，文件名固定；
- secrets（Cookie）只进 accounts.yaml，永远不进 git（.gitignore 已排除）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _opt_float(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_watchlist(path: Path) -> Dict[str, Dict[str, Any]]:
    """读 config/watchlist.yaml 的 items[]：{item_id: {listed_price, floor_price, title, keyword}}。"""
    data = _load_yaml(path)
    out: Dict[str, Dict[str, Any]] = {}
    for it in data.get("items") or []:
        if isinstance(it, dict) and it.get("item_id"):
            out[str(it["item_id"])] = {
                "title": str(it.get("title") or ""),
                "listed_price": _opt_float(it.get("listed_price")),
                "floor_price": _opt_float(it.get("floor_price")),
                "keyword": str(it.get("keyword") or ""),
            }
    return out


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是映射，实际是 {type(data).__name__}")
    return data


@dataclass
class BrainConfig:
    dsh_bin: str = "dsh"
    dsh_home: str = "data/dsh-home"   # dsh 隔离家目录（默认仓库/包内，绝不碰系统 ~/.dsh）
    api_base_url: str = ""            # LLM API 地址（空=官方 https://api.deepseek.com）
    model: str = ""                   # 模型名（空=dsh 默认；写入 settings.yaml agent-default-model）
    profile: str = "headless"
    workspace: str = "."          # dsh 工作目录（相对仓库根，一般为 "."）
    job_timeout_sec: int = 300
    max_concurrent: int = 2
    per_chat_cooldown_sec: int = 20


@dataclass
class ReplyConfig:
    quiet_start: Optional[str] = None   # "23:30"；None 表示无静默时段
    quiet_end: Optional[str] = None
    max_per_hour: int = 60
    keyword_rules: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DeliveryItem:
    item_id: str = ""
    title_contains: str = ""
    content: str = ""


@dataclass
class AutoConfirmConfig:
    enabled: bool = False
    max_amount_cny: float = 100.0
    item_whitelist: List[str] = field(default_factory=list)
    item_blacklist: List[str] = field(default_factory=list)
    buyer_blacklist: List[str] = field(default_factory=list)
    cooldown_minutes: int = 3


@dataclass
class RepriceConfig:
    """自动改价闸门（议价成交/审计调价共用；改价接口需鱼小铺）。"""
    enabled: bool = False            # 总开关（还要过边界/频次/底价三关）
    floor_ratio: float = 0.85        # 无 watchlist 底价时的兜底：挂价 × floor_ratio
    max_drop_pct: float = 20.0       # 单次降幅上限（%）
    max_per_day: int = 5             # 每日改价次数上限
    min_interval_min: int = 10       # 同商品两次改价最小间隔（分钟）


@dataclass
class SnipeWatch:
    """盯货（捡漏）订阅：关键词 + 价格阈值。命中即产生事件，付款永远人工。"""
    keyword: str = ""
    max_price: Optional[float] = None     # 价格上限（绝对值）
    max_ratio: Optional[float] = None     # 或：低于本页中位价的比值（如 0.7）
    note: str = ""


@dataclass
class SnipeConfig:
    enabled: bool = False
    interval_minutes: int = 30        # 扫描间隔
    watch: List[SnipeWatch] = field(default_factory=list)


@dataclass
class HotelQuoteConfig:
    """代订酒店报价：阶梯加价 / 转人工阈值 / 比价缓存 / 查价限频。

    tiers: [[价格上限, 加价%], ...]；超出最后一档按 5% 兜底。
    """
    tiers: List[List[float]] = field(default_factory=lambda: [[500, 10.0], [1500, 8.0]])
    manual_threshold_cny: float = 2500.0
    cache_ttl_min: int = 30
    max_queries_per_hour: int = 3

    def margin_pct(self, cost: float) -> float:
        for cap, pct in self.tiers:
            if cost <= float(cap):
                return float(pct)
        return 5.0


from .ota import OtaConfig  # noqa: E402


@dataclass
class RobotConfig:
    mode: str = "dry-run"          # dry-run | live
    listen_host: str = "127.0.0.1"
    listen_port: int = 8790
    account: str = "default"
    db_path: str = "data/xy_gate.sqlite3"
    brain: BrainConfig = field(default_factory=BrainConfig)
    reply: ReplyConfig = field(default_factory=ReplyConfig)
    delivery_items: List[DeliveryItem] = field(default_factory=list)
    auto_confirm: AutoConfirmConfig = field(default_factory=AutoConfirmConfig)
    reprice: RepriceConfig = field(default_factory=RepriceConfig)
    snipe: SnipeConfig = field(default_factory=SnipeConfig)
    hotel_quotes: HotelQuoteConfig = field(default_factory=HotelQuoteConfig)
    ota: OtaConfig = field(default_factory=OtaConfig)
    watchlist_path: str = "config/watchlist.yaml"   # 在售清单（底价/挂价，议价闸门用）
    audit_daily_at: Optional[str] = None    # "09:00"；None/空 = 关闭每日查价审计

    @property
    def live(self) -> bool:
        return self.mode == "live"


@dataclass
class Account:
    name: str
    cookies: str

    @property
    def user_id(self) -> str:
        """unb 即卖家数字用户 ID（消息会话的 myid）。"""
        from .cookies import cookie_value
        return cookie_value(self.cookies, "unb")


def load_robot_config(config_dir: Optional[Path] = None) -> RobotConfig:
    config_dir = config_dir or (REPO_ROOT / "config")
    raw = _load_yaml(config_dir / "robot.yaml")

    brain_raw = raw.get("brain") or {}
    reply_raw = raw.get("reply") or {}
    confirm_raw = raw.get("auto_confirm") or {}
    delivery_raw = raw.get("delivery") or {}

    quiet = reply_raw.get("quiet_hours") or [None, None]
    rules = []
    for r in reply_raw.get("keyword_rules") or []:
        if isinstance(r, dict) and r.get("match") and r.get("reply"):
            rules.append(r)

    items = []
    for it in delivery_raw.get("items") or []:
        if isinstance(it, dict) and (it.get("item_id") or it.get("title_contains")) and it.get("content"):
            items.append(DeliveryItem(
                item_id=str(it.get("item_id") or ""),
                title_contains=str(it.get("title_contains") or ""),
                content=str(it["content"]),
            ))

    reprice_raw = raw.get("reprice") or {}
    snipe_raw = raw.get("snipe") or {}
    hq_raw = raw.get("hotel_quotes") or {}
    tiers = [[float(a), float(b)] for a, b in (hq_raw.get("tiers") or [])
             if isinstance(a, (int, float)) and isinstance(b, (int, float))]
    watch = []
    for w in snipe_raw.get("watch") or []:
        if isinstance(w, dict) and w.get("keyword"):
            watch.append(SnipeWatch(
                keyword=str(w["keyword"]),
                max_price=_opt_float(w.get("max_price")),
                max_ratio=_opt_float(w.get("max_ratio")),
                note=str(w.get("note") or ""),
            ))

    listen = raw.get("listen") or "127.0.0.1:8790"
    host, _, port = listen.partition(":")
    cfg = RobotConfig(
        mode=str(raw.get("mode") or "dry-run").strip().lower(),
        listen_host=host or "127.0.0.1",
        listen_port=int(port or 8790),
        account=str(raw.get("account") or "default"),
        db_path=str(raw.get("db_path") or "data/xy_gate.sqlite3"),
        brain=BrainConfig(
            dsh_bin=str(brain_raw.get("dsh_bin") or os.environ.get("DSH_BIN") or "dsh"),
            dsh_home=str(brain_raw.get("dsh_home") if brain_raw.get("dsh_home") is not None
                         else (os.environ.get("DSH_HOME_ISOLATED") or "data/dsh-home")),
            api_base_url=str(brain_raw.get("api_base_url") or ""),
            model=str(brain_raw.get("model") or ""),
            profile=str(brain_raw.get("profile") or "headless"),
            workspace=str(brain_raw.get("workspace") or "."),
            job_timeout_sec=int(brain_raw.get("job_timeout_sec") or 300),
            max_concurrent=max(1, int(brain_raw.get("max_concurrent") or 2)),
            per_chat_cooldown_sec=int(brain_raw.get("per_chat_cooldown_sec") or 20),
        ),
        reply=ReplyConfig(
            quiet_start=quiet[0],
            quiet_end=quiet[1],
            max_per_hour=int(reply_raw.get("max_per_hour") or 60),
            keyword_rules=rules,
        ),
        delivery_items=items,
        auto_confirm=AutoConfirmConfig(
            enabled=bool(confirm_raw.get("enabled", False)),
            max_amount_cny=float(confirm_raw.get("max_amount_cny") or 100.0),
            item_whitelist=[str(x) for x in confirm_raw.get("item_whitelist") or []],
            item_blacklist=[str(x) for x in confirm_raw.get("item_blacklist") or []],
            buyer_blacklist=[str(x) for x in confirm_raw.get("buyer_blacklist") or []],
            cooldown_minutes=int(confirm_raw.get("cooldown_minutes") or 3),
        ),
        reprice=RepriceConfig(
            enabled=bool(reprice_raw.get("enabled", False)),
            floor_ratio=float(reprice_raw.get("floor_ratio") or 0.85),
            max_drop_pct=float(reprice_raw.get("max_drop_pct") or 20.0),
            max_per_day=int(reprice_raw.get("max_per_day") or 5),
            min_interval_min=int(reprice_raw.get("min_interval_min") or 10),
        ),
        snipe=SnipeConfig(
            enabled=bool(snipe_raw.get("enabled", False)),
            interval_minutes=int(snipe_raw.get("interval_minutes") or 30),
            watch=watch,
        ),
        ota=OtaConfig(**{k: v for k, v in (raw.get("ota") or {}).items()
                         if k in OtaConfig.__dataclass_fields__}),
        hotel_quotes=HotelQuoteConfig(
            tiers=tiers or [[500, 10.0], [1500, 8.0]],
            manual_threshold_cny=float(hq_raw.get("manual_threshold_cny") or 2500),
            cache_ttl_min=int(hq_raw.get("cache_ttl_min") or 30),
            max_queries_per_hour=int(hq_raw.get("max_queries_per_hour") or 3),
        ),
        watchlist_path=str(raw.get("watchlist_path") or "config/watchlist.yaml"),
        audit_daily_at=(str(raw["audit_daily_at"]) if raw.get("audit_daily_at") else None),
    )
    if cfg.mode not in ("dry-run", "live"):
        raise ValueError(f"mode 只能是 dry-run 或 live，当前: {cfg.mode}")
    return cfg


def load_account(name: str, config_dir: Optional[Path] = None) -> Account:
    config_dir = config_dir or (REPO_ROOT / "config")
    raw = _load_yaml(config_dir / "accounts.yaml")
    accounts = raw.get("accounts") or {}
    if name not in accounts:
        raise KeyError(
            f"accounts.yaml 中没有账号 '{name}'（现有: {list(accounts) or '无'}）。"
            "请复制 config/accounts.example.yaml 为 accounts.yaml 并填入 Cookie。"
        )
    cookies = str(accounts[name].get("cookies") or "").strip()
    if "unb=" not in cookies:
        raise ValueError(f"账号 '{name}' 的 Cookie 缺少 unb 字段（未登录态），请重新从浏览器复制整段 Cookie")
    return Account(name=name, cookies=cookies)


def _y(v: Any) -> str:
    """标量转 YAML 安全字符串。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or any(c in s for c in ":#{}[],&*'\"?\n") or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def render_robot_yaml(cfg: RobotConfig) -> str:
    """把配置序列化回 robot.yaml（设置页保存用；覆盖写，注释为模板注释）。"""
    rp, sn, ac = cfg.reprice, cfg.snipe, cfg.auto_confirm
    lines = [
        "# xy-dsh-robot 行为配置（由管理后台 settings 页生成）",
        "",
        f"mode: {_y(cfg.mode)}",
        f"listen: {_y(f'{cfg.listen_host}:{cfg.listen_port}')}",
        f"account: {_y(cfg.account)}",
        f"db_path: {_y(cfg.db_path)}",
        "",
        "brain:",
        f"  dsh_bin: {_y(cfg.brain.dsh_bin)}",
        f"  dsh_home: {_y(cfg.brain.dsh_home)}",
        f"  api_base_url: {_y(cfg.brain.api_base_url)}",
        f"  model: {_y(cfg.brain.model)}",
        f"  profile: {_y(cfg.brain.profile)}",
        f"  workspace: {_y(cfg.brain.workspace)}",
        f"  job_timeout_sec: {cfg.brain.job_timeout_sec}",
        f"  max_concurrent: {cfg.brain.max_concurrent}",
        f"  per_chat_cooldown_sec: {cfg.brain.per_chat_cooldown_sec}",
        "",
        "reply:",
        f"  quiet_hours: [{_y(cfg.reply.quiet_start or '')}, {_y(cfg.reply.quiet_end or '')}]",
        f"  max_per_hour: {cfg.reply.max_per_hour}",
        "  keyword_rules:",
    ]
    for r in cfg.reply.keyword_rules:
        keys = ", ".join(_y(k) for k in r.get("match", []))
        lines.append(f"    - match: [{keys}]")
        if r.get("item_id"):
            lines.append(f"      item_id: {_y(r['item_id'])}")
        lines.append(f"      reply: {_y(r.get('reply') or '')}")
    if not cfg.reply.keyword_rules:
        lines.append("    []")
    lines += [
        "",
        "delivery:",
        "  items:",
    ]
    for it in cfg.delivery_items:
        lines.append(f"    - item_id: {_y(it.item_id)}")
        lines.append(f"      title_contains: {_y(it.title_contains)}")
        content = it.content.replace("\n", "\\n")
        lines.append(f"      content: {_y(content)}")
    if not cfg.delivery_items:
        lines.append("    []")
    lines += [
        "",
        "auto_confirm:",
        f"  enabled: {_y(ac.enabled)}",
        f"  max_amount_cny: {ac.max_amount_cny}",
        f"  item_whitelist: [{', '.join(_y(x) for x in ac.item_whitelist)}]",
        f"  item_blacklist: [{', '.join(_y(x) for x in ac.item_blacklist)}]",
        f"  buyer_blacklist: [{', '.join(_y(x) for x in ac.buyer_blacklist)}]",
        f"  cooldown_minutes: {ac.cooldown_minutes}",
        "",
        "reprice:",
        f"  enabled: {_y(rp.enabled)}",
        f"  floor_ratio: {rp.floor_ratio}",
        f"  max_drop_pct: {rp.max_drop_pct}",
        f"  max_per_day: {rp.max_per_day}",
        f"  min_interval_min: {rp.min_interval_min}",
        "",
        "snipe:",
        f"  enabled: {_y(sn.enabled)}",
        f"  interval_minutes: {sn.interval_minutes}",
        "  watch:",
    ]
    for w in sn.watch:
        lines.append(f"    - keyword: {_y(w.keyword)}")
        if w.max_price is not None:
            lines.append(f"      max_price: {w.max_price}")
        if w.max_ratio is not None:
            lines.append(f"      max_ratio: {w.max_ratio}")
        lines.append(f"      note: {_y(w.note)}")
    if not sn.watch:
        lines.append("    []")
    lines += [
        "",
        "hotel_quotes:",
        f"  tiers: [{', '.join(f'[{int(a)},{b}]' for a, b in cfg.hotel_quotes.tiers)}]",
        f"  manual_threshold_cny: {cfg.hotel_quotes.manual_threshold_cny}",
        f"  cache_ttl_min: {cfg.hotel_quotes.cache_ttl_min}",
        f"  max_queries_per_hour: {cfg.hotel_quotes.max_queries_per_hour}",
        "ota:",
        f"  page_settle_sec: {cfg.ota.page_settle_sec}",
        f"  fx_twd_cny: {cfg.ota.fx_twd_cny}",
        f"  min_interval_sec: {cfg.ota.min_interval_sec}",
        "",
        f"audit_daily_at: {_y(cfg.audit_daily_at or '')}",
        "",
    ]
    return "\n".join(lines)
