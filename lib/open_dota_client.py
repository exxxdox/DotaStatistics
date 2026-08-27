import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

_log = logging.getLogger(__name__)
DEFAULT_HERO_STATS_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / "res" / "daily_hero_stats_cache.json"
)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 522, 524}
STAT_PERIOD_DAYS = 30
CACHE_SCHEMA_VERSION = 2


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class OpenDotaApiError(RuntimeError):
    """OpenDota 请求或响应不符合预期。"""


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
        refresh_workers: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.timeout = timeout
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

    def _load_or_refresh_monthly_stats(self) -> list[HeroWinRateStat]:
        self.used_cached_hero_stats = False
        self._set_current_stats_period()
        document = self._load_cache_document()
        days = document["days"]
        target_dates = self._target_dates()
        missing_dates = [day for day in target_dates if day.isoformat() not in days]

        updates, refresh_error = self._fetch_missing_days(missing_dates)
        days.update(updates)
        # 只保留当前窗口，避免缓存文件无限增长。
        document["days"] = {
            day.isoformat(): days[day.isoformat()]
            for day in target_dates
            if day.isoformat() in days
        }

        if refresh_error is not None:
            # 已成功的日切片也立即落盘，下次只补剩余日期。
            self._save_cache_document(document)
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
        self, missing_dates: list[date]
    ) -> tuple[dict[str, list[dict[str, Any]]], OpenDotaApiError | None]:
        if not missing_dates:
            return {}, None

        updates: dict[str, list[dict[str, Any]]] = {}
        first_error: OpenDotaApiError | None = None
        worker_count = min(self.refresh_workers, len(missing_dates))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self._fetch_daily_stats, day): day
                for day in missing_dates
            }
            for future in as_completed(futures):
                day = futures[future]
                try:
                    rows = future.result()
                    # 保存前先完整校验，禁止坏数据污染增量缓存。
                    self._aggregate_monthly_stats(rows)
                    updates[day.isoformat()] = rows
                except OpenDotaApiError as error:
                    first_error = first_error or error
        return updates, first_error

    def _fetch_daily_stats(self, target_date: date) -> list[dict[str, Any]]:
        start = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            tzinfo=timezone.utc,
        )
        start_timestamp = int(start.timestamp())
        end_timestamp = int((start + timedelta(days=1)).timestamp())
        sql = f"""
WITH daily AS (
    SELECT picked.hero_id, picked.won
    FROM public_matches AS match
    CROSS JOIN LATERAL (
        SELECT radiant.hero_id, match.radiant_win AS won
        FROM unnest(match.radiant_team) AS radiant(hero_id)
        UNION ALL
        SELECT dire.hero_id, NOT match.radiant_win AS won
        FROM unnest(match.dire_team) AS dire(hero_id)
    ) AS picked
    WHERE match.start_time >= {start_timestamp}
      AND match.start_time < {end_timestamp}
)
SELECT
    hero_id,
    COUNT(*)::bigint AS games,
    SUM(CASE WHEN won THEN 1 ELSE 0 END)::bigint AS wins
FROM daily
GROUP BY hero_id
""".strip()
        return self.explorer(sql)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    f"{self.BASE_URL}{path}", params=params, timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as error:
                status_code = getattr(error.response, "status_code", None)
                retryable = (
                    status_code is None or status_code in RETRYABLE_STATUS_CODES
                )
                if retryable and attempt < self.max_retries:
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

        raise AssertionError("OpenDota 重试循环异常退出")

    @staticmethod
    def _response_error_detail(response: Any) -> str:
        try:
            payload = response.json()
            detail = payload.get("error") or payload.get("err")
            if not isinstance(detail, str):
                return ""
            # 上游正文不可信，只保留单行短文本用于诊断。
            return " ".join(detail.split())[:200]
        except (AttributeError, TypeError, ValueError):
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
        except OSError:
            _log.exception("写入 OpenDota 英雄日统计缓存失败")

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
            return cached
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
