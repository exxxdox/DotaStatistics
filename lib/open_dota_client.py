import json
import logging
import os
import threading
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any

import requests

from data_center import hero_stats_cache_path
from lib.utils import whetherWin

_log = logging.getLogger(__name__)
# 运行时缓存与镜像内静态资源分离，容器重建后仍可由数据卷恢复。
DEFAULT_HERO_STATS_CACHE_PATH = hero_stats_cache_path
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 522, 524}
STAT_PERIOD_DAYS = 30
CACHE_SCHEMA_VERSION = 2
AGENT_QUERY_TIMEOUT_SECONDS = 10.0
MAX_EXTERNAL_TEXT_CHARS = 160
# 查询结果最终只保留有限字段；解析前仍要限制原文大小，防止异常上游耗尽内存。
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
RESPONSE_CHUNK_BYTES = 64 * 1024

PLAYER_VIEW_FIELDS = {
    "win_loss": ("win", "lose"),
    "heroes": (
        "hero_id", "last_played", "games", "win", "with_games", "with_win",
        "against_games", "against_win",
    ),
    "peers": (
        "account_id", "personaname", "last_played", "games", "win",
        "with_games", "with_win", "against_games", "against_win",
    ),
    "totals": ("field", "n", "sum"),
    "ratings": ("account_id", "match_id", "rank_tier", "time"),
    "rankings": ("hero_id", "score", "percent_rank", "card"),
}
HERO_VIEW_FIELDS = {
    "matchups": ("hero_id", "games_played", "wins"),
    "durations": ("duration_bin", "games_played", "wins"),
    "recent_matches": (
        "match_id", "duration", "start_time", "radiant_team_id", "radiant_name",
        "dire_team_id", "dire_name", "leagueid", "league_name", "series_id",
        "series_type", "radiant_score", "dire_score", "radiant_win", "radiant",
    ),
}
TEAM_VIEW_FIELDS = {
    "overview": (
        "team_id", "rating", "wins", "losses", "last_match_time", "name", "tag",
    ),
    "matches": (
        "match_id", "radiant", "radiant_win", "radiant_score", "dire_score",
        "duration", "start_time", "leagueid", "league_name", "cluster",
        "opposing_team_id", "opposing_team_name",
    ),
    "players": (
        "account_id", "name", "games_played", "wins", "is_current_team_member",
    ),
    "heroes": ("hero_id", "name", "games_played", "wins"),
}
GLOBAL_VIEW_FIELDS = {
    "live_matches": (
        "match_id", "game_time", "average_mmr", "game_mode", "league_id",
        "series_id", "team_id_radiant", "team_name_radiant", "team_id_dire",
        "team_name_dire",
    ),
    "pro_matches": (
        "match_id", "duration", "start_time", "radiant_team_id", "radiant_name",
        "dire_team_id", "dire_name", "leagueid", "league_name", "series_id",
        "series_type", "radiant_score", "dire_score", "radiant_win",
    ),
    "public_matches": (
        "match_id", "match_seq_num", "radiant_win", "start_time", "duration",
        "lobby_type", "game_mode", "avg_rank_tier", "num_rank_tier", "cluster",
    ),
    "top_players": (
        "account_id", "personaname", "rank_tier", "leaderboard_rank",
    ),
    "hero_stats": (
        "hero_id", "id", "localized_name", "primary_attr", "attack_type",
        "pro_pick", "pro_win", "pro_ban", "turbo_picks", "turbo_wins",
        "1_pick", "1_win", "2_pick", "2_win", "3_pick", "3_win", "4_pick",
        "4_win", "5_pick", "5_win", "6_pick", "6_win", "7_pick", "7_win",
        "8_pick", "8_win",
    ),
    "pro_players": (
        "account_id", "personaname", "name", "full_history_time", "country_code",
        "fantasy_role", "team_id", "team_name", "team_tag", "is_locked",
        "is_pro", "locked_until",
    ),
}


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class OpenDotaApiError(RuntimeError):
    """OpenDota 请求或响应不符合预期。"""


class _CachePersistenceError(OpenDotaApiError):
    """英雄统计缓存无法安全持久化。"""


@dataclass(frozen=True)
class HeroWinRateStat:
    hero_id: int
    hero_name: str
    games: int
    wins: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


class OpenDotaApiClient:
    """OpenDota API 客户端，集中处理鉴权、超时和响应校验。"""

    BASE_URL = "https://api.opendota.com/api"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        cache_path: Path | None = DEFAULT_HERO_STATS_CACHE_PATH,
        max_cache_age_seconds: int = 48 * 60 * 60,
        refresh_workers: int = 1,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _utc_now,
        hero_name_resolver: Callable[[int], str | None] | None = None,
    ) -> None:
        self.timeout = timeout
        self.hero_name_resolver = hero_name_resolver
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "DotaStatistics/0.1")
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.cache_path = cache_path
        self.max_cache_age_seconds = max_cache_age_seconds
        self.refresh_workers = refresh_workers
        self._sleep = sleep
        self._now = now
        self._cache_lock = threading.Lock()
        self.used_cached_hero_stats = False
        self.stats_period_start: date | None = None
        self.stats_period_end: date | None = None

        resolved_api_key = api_key or os.environ.get("OPENDOTA_API_KEY")
        if resolved_api_key:
            # 使用文档支持的 Bearer 头，避免密钥出现在 URL 和访问日志中。
            self.session.headers["Authorization"] = f"Bearer {resolved_api_key}"

    def explorer(self, sql: str) -> list[dict[str, Any]]:
        payload = self._get("/explorer", params={"sql": sql})
        if not isinstance(payload, dict):
            raise OpenDotaApiError("OpenDota explorer 返回格式错误")
        if payload.get("err"):
            raise OpenDotaApiError(f"OpenDota explorer 查询失败: {payload['err']}")

        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise OpenDotaApiError("OpenDota explorer 响应缺少 rows")
        return rows

    def get_recent_month_win_rate_leaders(
        self,
        top_count: int = 10,
        min_games: int = 100,
    ) -> list[HeroWinRateStat]:
        if top_count < 1 or min_games < 1:
            raise ValueError("top_count 和 min_games 必须大于 0")
        if self.refresh_workers < 1:
            raise ValueError("refresh_workers 必须大于 0")

        # 同一个机器人实例可能同时收到多个查询；串行刷新可避免
        # 两批日查询重复消耗配额或互相覆盖缓存文件。
        with self._cache_lock:
            stats = self._load_or_refresh_monthly_stats()
        eligible = [stat for stat in stats if stat.games >= min_games]
        eligible.sort(key=lambda stat: (-stat.win_rate, -stat.games, stat.hero_id))
        return eligible[:top_count]

    def get_all_rank_win_rate_leaders(
        self,
        top_count: int = 10,
        min_games: int = 100,
    ) -> list[HeroWinRateStat]:
        """兼容旧调用名称，统计口径已调整为最近 30 个完整自然日。"""
        return self.get_recent_month_win_rate_leaders(top_count, min_games)

    def get_heroes(self) -> dict[int, str]:
        """返回 OpenDota 英雄 ID 到英文名的映射，用于解析中文名缺失的英雄。"""
        payload = self._get("/heroes")
        return {
            int(hero["id"]): hero["name"]
            for hero in payload
            if isinstance(hero, dict) and "id" in hero and "name" in hero
        }

    def get_player_data(
        self,
        account_id: int,
        view: str,
        *,
        days: int,
        limit: int,
    ) -> Any:
        """查询玩家只读数据；固定视图阻止模型构造任意 API 路径。"""
        self._validate_positive_int(account_id, "account_id")
        self._validate_range(days, "days", 1, 365)
        self._validate_range(limit, "limit", 1, 20)
        allowed_views = {"profile", "counts", *PLAYER_VIEW_FIELDS}
        if not isinstance(view, str) or view not in allowed_views:
            raise ValueError("view 不是允许的玩家查询")

        endpoints = {
            "profile": "",
            "win_loss": "/wl",
            "heroes": "/heroes",
            "peers": "/peers",
            "totals": "/totals",
            "counts": "/counts",
            "ratings": "/ratings",
            "rankings": "/rankings",
        }
        path = f"/players/{account_id}{endpoints[view]}"
        params = (
            {"date": days, "limit": limit}
            if view in {"win_loss", "heroes", "peers", "totals", "counts"}
            else None
        )
        # Agent 查询快速失败，避免一次对话被上游重试阻塞数分钟。
        payload = self._get(
            path,
            params=params,
            timeout=AGENT_QUERY_TIMEOUT_SECONDS,
            max_retries=0,
        )
        if view == "profile":
            return self._project_player_profile(payload)
        if view == "counts":
            return self._project_counts(payload, limit)
        fields = PLAYER_VIEW_FIELDS[view]
        if view == "win_loss":
            return self._project_object(payload, fields, "玩家胜负")
        return self._project_rows(payload, fields, limit, f"玩家 {view}")

    def get_match_data(self, match_id: int) -> dict[str, Any]:
        """返回单场核心数据，排除聊天、头像和其他无界字段。"""
        self._validate_positive_int(match_id, "match_id")
        payload = self._get(
            f"/matches/{match_id}",
            timeout=AGENT_QUERY_TIMEOUT_SECONDS,
            max_retries=0,
        )
        result = self._project_object(
            payload,
            (
                "match_id", "duration", "start_time", "radiant_win",
                "radiant_score", "dire_score", "game_mode", "lobby_type",
                "leagueid", "league_name", "patch", "region",
            ),
            "比赛",
        )
        players = payload.get("players")
        if players is not None:
            result["players"] = self._project_rows(
                players,
                (
                    "account_id", "player_slot", "hero_id", "kills", "deaths",
                    "assists", "gold_per_min", "xp_per_min", "hero_damage",
                    "tower_damage", "hero_healing", "last_hits", "denies", "level",
                    "total_gold", "total_xp", "isRadiant", "win", "lose",
                    "leaver_status",
                ),
                10,
                "比赛玩家",
            )
        return result

    def get_hero_data(self, hero_id: int, view: str, *, limit: int) -> Any:
        """查询英雄只读数据；排行榜使用固定的全局 rankings 端点。"""
        self._validate_positive_int(hero_id, "hero_id")
        self._validate_range(limit, "limit", 1, 20)
        allowed_views = {*HERO_VIEW_FIELDS, "rankings"}
        if not isinstance(view, str) or view not in allowed_views:
            raise ValueError("view 不是允许的英雄查询")

        if view == "rankings":
            path = "/rankings"
            params = {"hero_id": hero_id}
        else:
            endpoint = "matches" if view == "recent_matches" else view
            path = f"/heroes/{hero_id}/{endpoint}"
            params = None
        payload = self._get(
            path,
            params=params,
            timeout=AGENT_QUERY_TIMEOUT_SECONDS,
            max_retries=0,
        )
        if view == "rankings":
            return self._project_hero_rankings(payload, limit)
        return self._project_rows(
            payload, HERO_VIEW_FIELDS[view], limit, f"英雄 {view}"
        )

    def get_team_data(self, team_id: int, view: str, *, limit: int) -> Any:
        """查询职业战队只读数据，结果限制为模型所需的核心字段。"""
        self._validate_positive_int(team_id, "team_id")
        self._validate_range(limit, "limit", 1, 20)
        if not isinstance(view, str) or view not in TEAM_VIEW_FIELDS:
            raise ValueError("view 不是允许的战队查询")

        suffix = "" if view == "overview" else f"/{view}"
        payload = self._get(
            f"/teams/{team_id}{suffix}",
            timeout=AGENT_QUERY_TIMEOUT_SECONDS,
            max_retries=0,
        )
        if view == "overview":
            return self._project_object(payload, TEAM_VIEW_FIELDS[view], "战队")
        return self._project_rows(
            payload, TEAM_VIEW_FIELDS[view], limit, f"战队 {view}"
        )

    def get_global_data(self, view: str, *, limit: int) -> Any:
        """查询固定的 OpenDota 全局只读数据，不接受任意资源名称。"""
        self._validate_range(limit, "limit", 1, 20)
        paths = {
            "live_matches": "/live",
            "pro_matches": "/proMatches",
            "public_matches": "/publicMatches",
            "top_players": "/playersByRank",
            "hero_stats": "/heroStats",
            "pro_players": "/proPlayers",
        }
        if not isinstance(view, str) or view not in paths:
            raise ValueError("view 不是允许的全局查询")
        payload = self._get(
            paths[view],
            timeout=AGENT_QUERY_TIMEOUT_SECONDS,
            max_retries=0,
        )
        return self._project_rows(
            payload, GLOBAL_VIEW_FIELDS[view], limit, f"全局 {view}"
        )

    def get_recent_matches(self, account_id: int, limit: int = 5) -> str | None:
        """默认展示最近五场天梯比赛；上游异常时返回 None。"""
        try:
            # Agent 查询槽只有四个，慢请求不能继承普通查询的 30 秒超时和重试。
            payload = self._get(
                f"/players/{account_id}/recentMatches",
                timeout=AGENT_QUERY_TIMEOUT_SECONDS,
                max_retries=0,
            )
        except OpenDotaApiError:
            _log.warning("查询近期比赛失败: account_id=%s", account_id)
            return None
        if not isinstance(payload, list):
            _log.warning("近期比赛响应格式错误: account_id=%s", account_id)
            return None
        result = "\n"
        size = 0
        for game in payload:
            if not isinstance(game, dict):
                continue
            if game.get("lobby_type") != 7:
                continue
            radiant_win = game.get("radiant_win")
            player_slot = game.get("player_slot")
            mr_lin = (
                "哦嘎嘎嘎嘎暴利,赢了一把."
                if whetherWin(radiant_win, player_slot)
                else "曾恶心啊,尽力了."
            )
            hero_id = game.get("hero_id")
            hero_damage = game.get("hero_damage")
            hero_healing = game.get("hero_healing")
            deaths = game.get("deaths")
            kills = game.get("kills")
            assists = game.get("assists")
            gold_per_min = game.get("gold_per_min")
            result += (
                f"{mr_lin} 英雄:{self._hero_name(hero_id)} "
                f"伤害:{hero_damage} 杀:{kills} 死:{deaths} "
                f"助攻:{assists} gpm:{gold_per_min}\n"
            )
            size += 1
            if size >= limit:
                break
        return result

    def get_matches_by_date(self, account_id: int, date: int) -> str | None:
        """格式化指定日期的天梯比赛列表；上游异常时返回 None。"""
        try:
            payload = self._get(
                f"/players/{account_id}/matches",
                params={"date": date},
                timeout=AGENT_QUERY_TIMEOUT_SECONDS,
                max_retries=0,
            )
        except OpenDotaApiError:
            _log.warning("查询玩家比赛失败: account_id=%s", account_id)
            return None
        if not isinstance(payload, list):
            _log.warning("玩家比赛响应格式错误: account_id=%s", account_id)
            return None
        result = ""
        for game in payload:
            if not isinstance(game, dict):
                continue
            if game.get("lobby_type") != 7:
                continue
            radiant_win = game.get("radiant_win")
            player_slot = game.get("player_slot")
            mr_lin = "游戏" + ("胜利" if whetherWin(radiant_win, player_slot) else "失败")
            hero_id = game.get("hero_id")
            deaths = game.get("deaths")
            kills = game.get("kills")
            assists = game.get("assists")
            duration = game.get("duration")
            side = (
                "天辉"
                if isinstance(player_slot, int) and player_slot < 128
                else "夜魇"
            )
            result += (
                f"{mr_lin} 英雄:{self._hero_name(hero_id)} "
                f"击杀:{kills} 死亡:{deaths} 助攻:{assists} "
                f"持续时间{duration}秒 {side} "
                f"gpm {game.get('gold_per_min')} xpm {game.get('xp_per_min')} "
                f"对英雄伤害{game.get('hero_damage')} "
                f"对英雄治疗{game.get('hero_healing')} "
                f"建筑伤害{game.get('tower_damage')}\n"
            )
        if not result:
            return ""
        return f"{result}此玩家数据结束\n"

    def _hero_name(self, hero_id: int) -> str:
        """解析英雄名；解析器缺失或未命中时使用占位名，避免把 None 拼进展示。"""
        if self.hero_name_resolver is not None:
            resolved_name = self.hero_name_resolver(hero_id)
            if resolved_name:
                return resolved_name
        return f"英雄 {hero_id}"

    def _load_or_refresh_monthly_stats(self) -> list[HeroWinRateStat]:
        self.used_cached_hero_stats = False
        self._set_current_stats_period()
        document = self._load_cache_document()
        days = document["days"]
        target_dates = self._target_dates()
        missing_dates = [day for day in target_dates if day.isoformat() not in days]

        # 只保留当前窗口，避免缓存文件无限增长。
        document["days"] = {
            day.isoformat(): days[day.isoformat()]
            for day in target_dates
            if day.isoformat() in days
        }

        def save_day(day: date, rows: list[dict[str, Any]]) -> None:
            # 每日成功即原子落盘，后续请求阻塞或进程中断也不会丢失回填进度；
            # 完整月度快照留到所有日期就绪后再替换。
            document["days"][day.isoformat()] = rows
            self._save_cache_document(document)

        refresh_error = self._fetch_missing_days(missing_dates, save_day)

        if refresh_error is not None:
            # 权限或磁盘错误不能伪装成上游故障，否则每次查询都会重新消耗配额。
            if isinstance(refresh_error, _CachePersistenceError):
                raise refresh_error
            snapshot = self._load_recent_snapshot(document)
            if snapshot is None:
                raise refresh_error
            self.used_cached_hero_stats = True
            _log.warning("OpenDota 日统计刷新不完整，使用最近一次成功月度快照")
            return self._aggregate_monthly_stats(snapshot)

        missing_after_refresh = [
            day for day in target_dates if day.isoformat() not in document["days"]
        ]
        if missing_after_refresh:
            raise OpenDotaApiError("最近 30 天英雄日统计缓存不完整")

        rows = [
            row
            for day in target_dates
            for row in document["days"][day.isoformat()]
        ]
        stats = self._aggregate_monthly_stats(rows)
        snapshot_rows = [
            {"hero_id": stat.hero_id, "games": stat.games, "wins": stat.wins}
            for stat in stats
        ]
        document["snapshot"] = {
            "cached_at": self._now().timestamp(),
            "period_start": self.stats_period_start.isoformat(),
            "period_end": self.stats_period_end.isoformat(),
            "data": snapshot_rows,
        }
        self._save_cache_document(document)
        return stats

    def _set_current_stats_period(self) -> None:
        # 排除尚未结束的 UTC 当天，保证每个日切片不会在一天内反复变化。
        self.stats_period_end = self._now().date() - timedelta(days=1)
        self.stats_period_start = self.stats_period_end - timedelta(
            days=STAT_PERIOD_DAYS - 1
        )

    def _target_dates(self) -> list[date]:
        if self.stats_period_start is None:
            raise AssertionError("统计周期尚未初始化")
        return [
            self.stats_period_start + timedelta(days=offset)
            for offset in range(STAT_PERIOD_DAYS)
        ]

    def _fetch_missing_days(
        self,
        missing_dates: list[date],
        on_day: Callable[[date, list[dict[str, Any]]], None],
    ) -> OpenDotaApiError | None:
        if not missing_dates:
            return None

        worker_count = min(self.refresh_workers, len(missing_dates))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            pending_dates = iter(missing_dates)
            futures: dict[Future[list[dict[str, Any]]], date] = {
                executor.submit(self._fetch_daily_stats, day): day
                for day in islice(pending_dates, worker_count)
            }
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: futures[item]):
                    day = futures.pop(future)
                    try:
                        rows = future.result()
                        # 保存前先完整校验，禁止坏数据污染增量缓存。
                        self._aggregate_monthly_stats(rows)
                        # 消费线程串行写缓存，不再重复累计调用方不会使用的日数据。
                        on_day(day, rows)
                    except OpenDotaApiError as error:
                        # 上游或增量落盘失败后不再发起其余日期请求。
                        for pending in futures:
                            pending.cancel()
                        return error

                    next_day = next(pending_dates, None)
                    if next_day is not None:
                        futures[executor.submit(self._fetch_daily_stats, next_day)] = (
                            next_day
                        )
        return None

    def _fetch_daily_stats(self, target_date: date) -> list[dict[str, Any]]:
        start = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            tzinfo=timezone.utc,
        )
        return self._fetch_interval_stats(start, start + timedelta(days=1))

    def _fetch_interval_stats(
        self,
        start: datetime,
        end: datetime,
        remaining_splits: int = 2,
    ) -> list[dict[str, Any]]:
        start_timestamp = int(start.timestamp())
        end_timestamp = int(end.timestamp())
        sql = f"""
WITH filtered_matches AS (
    SELECT radiant_win, radiant_team, dire_team
    FROM public_matches
    WHERE start_time >= {start_timestamp}
      AND start_time < {end_timestamp}
),
picks AS (
    SELECT unnest(radiant_team) AS hero_id, radiant_win AS won
    FROM filtered_matches
    UNION ALL
    SELECT unnest(dire_team) AS hero_id, NOT radiant_win AS won
    FROM filtered_matches
)
SELECT
    hero_id,
    COUNT(*)::bigint AS games,
    COUNT(*) FILTER (WHERE won)::bigint AS wins
FROM picks
GROUP BY hero_id
""".strip()
        try:
            return self.explorer(sql)
        except OpenDotaApiError as error:
            if remaining_splits <= 0 or not self._is_query_timeout(error):
                raise

            # Explorer 负载高时可能终止单日聚合；缩小时间窗口比原样重试
            # 更能稳定利用 start_time 索引，两个结果可在本地无损相加。
            midpoint = start + (end - start) / 2
            left_rows = self._fetch_interval_stats(
                start, midpoint, remaining_splits - 1
            )
            right_rows = self._fetch_interval_stats(
                midpoint, end, remaining_splits - 1
            )
            merged = self._aggregate_monthly_stats(left_rows + right_rows)
            return [
                {
                    "hero_id": stat.hero_id,
                    "games": stat.games,
                    "wins": stat.wins,
                }
                for stat in merged
            ]

    @staticmethod
    def _is_query_timeout(error: OpenDotaApiError) -> bool:
        message = str(error).casefold()
        return "statement timeout" in message or "query read timeout" in message

    @staticmethod
    def _validate_positive_int(value: Any, name: str) -> None:
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} 必须是正整数")

    @staticmethod
    def _validate_range(value: Any, name: str, minimum: int, maximum: int) -> None:
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} 必须是 {minimum} 至 {maximum} 的整数")

    @staticmethod
    def _safe_scalar(value: Any) -> Any:
        if isinstance(value, str):
            # 外部名称只作为数据展示；移除控制与零宽字符，避免隐藏指令进入模型。
            cleaned = "".join(
                char for char in value
                if unicodedata.category(char) not in {"Cc", "Cf"}
            )
            return cleaned[:MAX_EXTERNAL_TEXT_CHARS]
        if value is None or type(value) in {bool, int, float}:
            return value
        return None

    @classmethod
    def _project_object(
        cls,
        payload: Any,
        fields: tuple[str, ...],
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise OpenDotaApiError(f"OpenDota {label}响应格式错误")
        result: dict[str, Any] = {}
        for field in fields:
            if field not in payload:
                continue
            value = cls._safe_scalar(payload[field])
            if value is not None or payload[field] is None:
                result[field] = value
        return result

    @classmethod
    def _project_rows(
        cls,
        payload: Any,
        fields: tuple[str, ...],
        limit: int,
        label: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            raise OpenDotaApiError(f"OpenDota {label}响应格式错误")
        return [
            cls._project_object(row, fields, label)
            for row in payload[:limit]
            if isinstance(row, dict)
        ]

    @classmethod
    def _project_player_profile(cls, payload: Any) -> dict[str, Any]:
        result = cls._project_object(
            payload,
            ("rank_tier", "leaderboard_rank", "computed_mmr", "computed_mmr_turbo"),
            "玩家资料",
        )
        profile = payload.get("profile")
        if isinstance(profile, dict):
            result["profile"] = cls._project_object(
                profile,
                (
                    "account_id", "personaname", "name", "plus", "cheese",
                    "last_login", "loccountrycode", "is_contributor", "is_subscriber",
                ),
                "玩家资料",
            )
        return result

    @classmethod
    def _project_counts(cls, payload: Any, limit: int) -> dict[str, dict[str, Any]]:
        if not isinstance(payload, dict):
            raise OpenDotaApiError("OpenDota 玩家 counts 响应格式错误")
        result: dict[str, dict[str, Any]] = {}
        for category in (
            "leaver_status", "game_mode", "lobby_type", "lane_role", "region", "patch"
        ):
            counts = payload.get(category)
            if not isinstance(counts, dict):
                continue
            # 分类键来自 OpenDota；限制长度和数量，避免异常响应撑大模型上下文。
            projected_counts: dict[str, Any] = {}
            for key, value in list(counts.items())[:limit]:
                if isinstance(value, dict):
                    projected = cls._project_object(
                        value, ("games", "win"), "玩家 counts"
                    )
                    if projected:
                        projected_counts[str(key)[:32]] = projected
                elif type(value) in {int, float}:
                    projected_counts[str(key)[:32]] = value
            result[category] = projected_counts
        return result

    @classmethod
    def _project_hero_rankings(cls, payload: Any, limit: int) -> dict[str, Any]:
        result = cls._project_object(payload, ("hero_id",), "英雄排行榜")
        rankings = payload.get("rankings")
        if rankings is not None:
            result["rankings"] = cls._project_rows(
                rankings,
                ("account_id", "score", "percent_rank", "card", "personaname"),
                limit,
                "英雄排行榜",
            )
        return result

    @staticmethod
    def _read_response_json(response: Any) -> Any:
        """流式读取并限制响应体大小，投影前不让异常上游撑大内存。"""
        body = bytearray()
        for chunk in response.iter_content(RESPONSE_CHUNK_BYTES):
            if not chunk:
                continue
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise OpenDotaApiError("OpenDota 响应体过大")
        try:
            # json.loads 接受字节串，并会自动识别 UTF-8。
            return json.loads(bytes(body))
        except ValueError as error:
            raise OpenDotaApiError(f"OpenDota 响应不是有效 JSON: {error}") from error

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> Any:
        request_timeout = self.timeout if timeout is None else timeout
        retry_count = self.max_retries if max_retries is None else max_retries
        for attempt in range(retry_count + 1):
            response = None
            try:
                response = self.session.get(
                    f"{self.BASE_URL}{path}",
                    params=params,
                    timeout=request_timeout,
                    # 流式读取，字节上限必须早于完整 JSON 解析生效。
                    stream=True,
                )
                response.raise_for_status()
                return self._read_response_json(response)
            except requests.RequestException as error:
                status_code = getattr(error.response, "status_code", None)
                retryable = (
                    status_code is None or status_code in RETRYABLE_STATUS_CODES
                )
                if retryable and attempt < retry_count:
                    # 上游偶发超时很常见；短退避重试，避免瞬时故障直接影响用户。
                    self._sleep(self.retry_backoff * (2**attempt))
                    continue
                if status_code is not None:
                    detail = self._response_error_detail(error.response)
                    suffix = f", detail={detail}" if detail else ""
                    # 不输出完整 URL，避免超长 SQL 或查询参数污染生产日志。
                    message = (
                        f"OpenDota 请求失败: HTTP {status_code}, path={path}{suffix}"
                    )
                else:
                    message = f"OpenDota 网络请求失败: {type(error).__name__}"
                raise OpenDotaApiError(message) from error
            except ValueError as error:
                raise OpenDotaApiError(f"OpenDota 响应不是有效 JSON: {error}") from error
            finally:
                if response is not None:
                    response.close()

        raise AssertionError("OpenDota 重试循环异常退出")

    @staticmethod
    def _response_error_detail(response: Any) -> str:
        try:
            payload = OpenDotaApiClient._read_response_json(response)
            detail = payload.get("error") or payload.get("err")
            if not isinstance(detail, str):
                return ""
            # 上游正文不可信，只保留单行短文本用于诊断。
            return " ".join(detail.split())[:200]
        except (AttributeError, TypeError, OpenDotaApiError, requests.RequestException):
            return ""

    def _save_cache_document(self, document: dict[str, Any]) -> None:
        if self.cache_path is None:
            return
        temporary_path = self.cache_path.with_suffix(".json.tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(document, ensure_ascii=False),
                encoding="utf-8",
            )
            # 同一文件系统原子替换，避免服务中断留下半份 JSON。
            temporary_path.replace(self.cache_path)
        except OSError as error:
            raise _CachePersistenceError("写入 OpenDota 英雄日统计缓存失败") from error

    def _load_cache_document(self) -> dict[str, Any]:
        if self.cache_path is None:
            return {"version": CACHE_SCHEMA_VERSION, "days": {}, "snapshot": None}
        try:
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if (
                not isinstance(cached, dict)
                or cached.get("version") != CACHE_SCHEMA_VERSION
                or not isinstance(cached.get("days"), dict)
            ):
                raise ValueError("缓存版本或结构不匹配")
            valid_days: dict[str, list[dict[str, Any]]] = {}
            for day, rows in cached["days"].items():
                if not isinstance(day, str) or not isinstance(rows, list):
                    continue
                try:
                    self._aggregate_monthly_stats(rows)
                except OpenDotaApiError:
                    # 单日损坏只使该日重新抓取，完整快照仍可用于故障回退。
                    continue
                valid_days[day] = rows
            return {
                "version": CACHE_SCHEMA_VERSION,
                "days": valid_days,
                "snapshot": cached.get("snapshot"),
            }
        except (OSError, TypeError, ValueError):
            # 旧版月度快照口径不同，不能混入新的逐日 public_matches 数据。
            return {"version": CACHE_SCHEMA_VERSION, "days": {}, "snapshot": None}

    def _load_recent_snapshot(
        self, document: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        try:
            snapshot = document["snapshot"]
            cached_at = float(snapshot["cached_at"])
            if self._now().timestamp() - cached_at > self.max_cache_age_seconds:
                return None
            payload = snapshot["data"]
            if not isinstance(payload, list):
                return None
            self.stats_period_start = date.fromisoformat(snapshot["period_start"])
            self.stats_period_end = date.fromisoformat(snapshot["period_end"])
            return payload
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _aggregate_monthly_stats(
        rows: list[dict[str, Any]],
    ) -> list[HeroWinRateStat]:
        totals: dict[int, list[int]] = {}
        try:
            for row in rows:
                hero_id = int(row["hero_id"])
                games = int(row["games"])
                wins = int(row["wins"])
                if games < 0 or wins < 0 or wins > games:
                    raise ValueError("胜负场次不合法")
                aggregate = totals.setdefault(hero_id, [0, 0])
                aggregate[0] += games
                aggregate[1] += wins
        except (KeyError, TypeError, ValueError) as error:
            raise OpenDotaApiError("无法解析 OpenDota 英雄胜率数据") from error

        return [
            HeroWinRateStat(
                hero_id=hero_id,
                # 正常运行时由报表层的本地中英文英雄表解析，这里保留可靠后备名。
                hero_name=f"英雄 {hero_id}",
                games=games,
                wins=wins,
            )
            for hero_id, (games, wins) in totals.items()
        ]
