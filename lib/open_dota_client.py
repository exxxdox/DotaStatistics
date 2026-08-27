import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_log = logging.getLogger(__name__)
DEFAULT_HERO_STATS_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / "res" / "hero_stats_cache.json"
)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 522, 524}


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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "DotaStatistics/0.1")
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.cache_path = cache_path
        self.max_cache_age_seconds = max_cache_age_seconds
        self._sleep = sleep
        self.used_cached_hero_stats = False

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

    def get_all_rank_win_rate_leaders(
        self,
        top_count: int = 10,
        min_games: int = 100,
    ) -> list[HeroWinRateStat]:
        if top_count < 1 or min_games < 1:
            raise ValueError("top_count 和 min_games 必须大于 0")

        self.used_cached_hero_stats = False
        try:
            payload = self._get("/heroStats")
        except OpenDotaApiError:
            payload = self._load_cached_hero_stats()
            if payload is None:
                raise
            self.used_cached_hero_stats = True
            _log.warning("OpenDota heroStats 不可用，使用最近一次成功缓存")

        if not isinstance(payload, list):
            raise OpenDotaApiError("OpenDota heroStats 返回格式错误")

        # heroStats 已按 1—8 段位预聚合，在线生成时无需扫描并展开海量比赛。
        stats = [self._parse_all_rank_stat(row) for row in payload]
        if not self.used_cached_hero_stats:
            # 只有完整解析成功的数据才有资格替换最后成功缓存。
            self._save_hero_stats_cache(payload)
        eligible = [stat for stat in stats if stat.games >= min_games]
        eligible.sort(key=lambda stat: (-stat.win_rate, -stat.games, stat.hero_id))
        return eligible[:top_count]

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
                raise OpenDotaApiError(f"OpenDota 请求失败: {error}") from error
            except ValueError as error:
                raise OpenDotaApiError(f"OpenDota 响应不是有效 JSON: {error}") from error

        raise AssertionError("OpenDota 重试循环异常退出")

    def _save_hero_stats_cache(self, payload: list[dict[str, Any]]) -> None:
        if self.cache_path is None:
            return
        temporary_path = self.cache_path.with_suffix(".json.tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(
                    {"cached_at": time.time(), "data": payload},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            # 同一文件系统原子替换，避免服务中断留下半份 JSON。
            temporary_path.replace(self.cache_path)
        except OSError:
            _log.exception("写入 OpenDota heroStats 缓存失败")

    def _load_cached_hero_stats(self) -> list[dict[str, Any]] | None:
        if self.cache_path is None:
            return None
        try:
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            cached_at = float(cached["cached_at"])
            payload = cached["data"]
            if time.time() - cached_at > self.max_cache_age_seconds:
                return None
            return payload if isinstance(payload, list) else None
        except (OSError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _parse_all_rank_stat(row: dict[str, Any]) -> HeroWinRateStat:
        try:
            games = sum(int(row.get(f"{rank}_pick", 0)) for rank in range(1, 9))
            wins = sum(int(row.get(f"{rank}_win", 0)) for rank in range(1, 9))
            return HeroWinRateStat(
                hero_id=int(row["id"]),
                hero_name=str(row["localized_name"]),
                games=games,
                wins=wins,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OpenDotaApiError(f"无法解析英雄胜率数据: {row}") from error
