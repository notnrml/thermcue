"""Async FortyGuard client.

Differences from the vendor quickstart client, all deliberate:

* **Async.** The engine serves a WebSocket agent loop alongside HTTP requests; a
  blocking ``time.sleep`` poll would stall the event loop for minutes.
* **Cache-first with explicit provenance.** Every result carries whether it came
  from the network or from disk, and that flag reaches the UI badge. A cache
  read is never reported as a live read.
* **Declared degradation.** When the network fails and a cached entry exists,
  the call succeeds with ``freshness="cached"`` and a populated ``degraded``
  reason. When no cached entry exists it raises. Nothing is swallowed.
* **Every request has a timeout.** A hang during judging is an outage.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import httpx

from ..config import Settings, get_settings
from .cache import CacheEntry, DiskCache
from .credits import CreditLedger

_TERMINAL_SUCCESS = {"succeeded", "completed"}
_TERMINAL_FAILURE = {"failed", "error"}

#: Parameter names the env_params endpoint accepts, copied verbatim from the
#: vendor client so an unknown name fails locally rather than burning a call.
ENV_PARAMS_ANALYSES: tuple[str, ...] = (
    "heat_index_celsius",
    "apparent_temperature_celsius",
    "wet_bulb_temperature_celsius",
    "relative_humidity_percent",
    "precipitation_mm",
    "cloud_cover_octas",
    "air_quality:idx",
    "air_quality_no2:idx",
    "air_quality_o3:idx",
    "air_quality_pm2p5:idx",
    "air_quality_pm10:idx",
    "air_quality_so2:idx",
    "aqi_us_co",
    "methane_ppb",
    "co2_ppm",
    "elevation",
    "solar_irradiance",
)

ANALYTIC_TYPES: tuple[str, ...] = ("tcm", "time_of_measure", "exceedance", "persistence")


class FortyGuardError(RuntimeError):
    """Any FortyGuard failure that the caller must see."""


class TaskFailedError(FortyGuardError):
    """The API reported a terminal failure status for a submitted activity."""


class TaskTimeoutError(FortyGuardError):
    """The activity did not terminate inside the configured budget."""


class NoDataError(FortyGuardError):
    """A live call failed and there is nothing cached to fall back to."""


@dataclass(slots=True)
class FortyGuardResult:
    """A response plus everything needed to describe where it came from.

    ``degraded`` is non-empty only when a live call was attempted and failed but
    a cached entry rescued the request. It exists so degradation is visible to a
    human rather than inferred from a silent ``freshness`` flip.
    """

    endpoint: str
    payload: dict[str, Any]
    result: Any
    freshness: Literal["live", "cached"]
    activity_id: str | None = None
    degraded: str | None = None
    request_record: dict[str, Any] | None = field(default=None, repr=False)


class FortyGuardClient:
    """Cache-first async wrapper over the tOS Enterprise API."""

    def __init__(
        self,
        settings: Settings | None = None,
        cache: DiskCache | None = None,
        ledger: CreditLedger | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache or DiskCache(self.settings.cache_dir)
        self.ledger = ledger or CreditLedger(self.settings.cache_dir / "credit_ledger.jsonl")
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------ plumbing

    @property
    def base_url(self) -> str:
        return self.settings.fortyguard_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not self.settings.fortyguard_api_key:
            raise FortyGuardError(
                "FORTYGUARD_API_KEY is not set. The engine can still serve cached "
                "responses; set the key to make live calls."
            )
        return {
            "api-key": self.settings.fortyguard_api_key,
            "Content-Type": "application/json",
        }

    async def __aenter__(self) -> "FortyGuardClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.settings.fortyguard_timeout_s),
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise FortyGuardError(
                "FortyGuardClient must be used as an async context manager: "
                "`async with FortyGuardClient() as fg:`"
            )
        return self._client

    async def _request_with_retry(
        self, method: str, path: str, *, json_body: dict | None = None
    ) -> httpx.Response:
        """One HTTP call with exponential backoff and full jitter.

        Retries transport errors, 429, and 5xx. A 4xx other than 429 is a client
        mistake and is raised immediately rather than burning the retry budget.
        """
        client = self._require_client()
        last_exc: Exception | None = None
        for attempt in range(self.settings.fortyguard_max_retries):
            try:
                resp = await client.request(
                    method, path, json=json_body, headers=self._headers()
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
            else:
                if resp.is_success or resp.status_code == 404:
                    return resp
                if resp.status_code != 429 and resp.status_code < 500:
                    raise FortyGuardError(
                        f"{method} {path} -> {resp.status_code}: {resp.text[:400]}"
                    )
                last_exc = FortyGuardError(
                    f"{method} {path} -> {resp.status_code}: {resp.text[:400]}"
                )
            # Full jitter, capped: politeness to a sponsor API we do not own.
            delay = min(2**attempt, 16) * random.random()
            await asyncio.sleep(delay)
        raise FortyGuardError(f"{method} {path} failed after retries: {last_exc}")

    async def _submit(self, path: str, payload: dict) -> str:
        resp = await self._request_with_retry("POST", path, json_body=payload)
        body = resp.json()
        if body.get("error"):
            raise FortyGuardError(body.get("message", "Submission failed"))
        try:
            return body["data"]["activity_id"]
        except (KeyError, TypeError) as exc:
            raise FortyGuardError(f"Unexpected submission response shape: {body}") from exc

    async def get_status(self, activity_id: str) -> dict | None:
        """Return the status payload, or ``None`` while the activity is not yet
        visible. Right after submission the status endpoint can 404 for a few
        seconds; that is eventual consistency, not an error."""
        resp = await self._request_with_retry("GET", f"/v1/status/{activity_id}")
        if resp.status_code == 404:
            return None
        body = resp.json()
        if body.get("error"):
            raise FortyGuardError(body.get("message", "Status lookup failed"))
        return body["data"]

    async def wait_for(self, activity_id: str) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.fortyguard_task_timeout_s
        while True:
            data = await self.get_status(activity_id)
            if data is not None:
                status = str(data.get("status", "")).lower()
                if status in _TERMINAL_SUCCESS:
                    return data.get("result", data)
                if status in _TERMINAL_FAILURE:
                    raise TaskFailedError(
                        f"Activity {activity_id} failed: {data.get('message') or status}"
                    )
            if loop.time() >= deadline:
                raise TaskTimeoutError(
                    f"Activity {activity_id} did not terminate within "
                    f"{self.settings.fortyguard_task_timeout_s:.0f}s"
                )
            await asyncio.sleep(self.settings.fortyguard_poll_interval_s)

    async def call(
        self,
        endpoint: str,
        payload: dict,
        *,
        refresh: bool = False,
    ) -> FortyGuardResult:
        """Cache-first submit-and-poll.

        Order of preference: cache (unless ``refresh``), then live, then cache as
        a declared fallback. Hard offline mode never opens a socket.
        """
        cached: CacheEntry | None = self.cache.get(endpoint, payload)

        if cached is not None and not refresh:
            self.ledger.record_cache_hit(endpoint)
            return FortyGuardResult(
                endpoint=endpoint,
                payload=payload,
                result=cached.result,
                freshness="cached",
                activity_id=cached.activity_id,
            )

        if self.settings.offline:
            if cached is not None:
                self.ledger.record_cache_hit(endpoint)
                return FortyGuardResult(
                    endpoint=endpoint,
                    payload=payload,
                    result=cached.result,
                    freshness="cached",
                    activity_id=cached.activity_id,
                    degraded="THERMCUE_OFFLINE=1; served from disk cache without contacting the API.",
                )
            raise NoDataError(
                f"Offline mode and no cached entry for {endpoint} with this payload. "
                f"Run scripts/build_cache.py with a live key to populate the cache."
            )

        try:
            activity_id = await self._submit(endpoint, payload)
            result = await self.wait_for(activity_id)
        except FortyGuardError as exc:
            if cached is not None:
                self.ledger.record_cache_hit(endpoint)
                return FortyGuardResult(
                    endpoint=endpoint,
                    payload=payload,
                    result=cached.result,
                    freshness="cached",
                    activity_id=cached.activity_id,
                    degraded=f"Live call failed ({exc}); served cached response "
                    f"fetched {cached.fetched_at.isoformat()}.",
                )
            raise

        self.cache.put(endpoint, payload, result, activity_id=activity_id)
        # Credits are deducted by FortyGuard only on Completed, so the ledger
        # records here rather than at submission time.
        self.ledger.record_call(endpoint, activity_id=activity_id)
        return FortyGuardResult(
            endpoint=endpoint,
            payload=payload,
            result=result,
            freshness="live",
            activity_id=activity_id,
            request_record={
                "method": "POST",
                "url": f"{self.base_url}{endpoint}",
                "headers": {"api-key": "<redacted>", "Content-Type": "application/json"},
                "body": payload,
            },
        )

    # ------------------------------------------------------------ endpoints

    @staticmethod
    def _date_time(
        start_date: str,
        filter_type: int,
        start_time: str | None = None,
        end_time: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        block: dict[str, Any] = {"start_date": start_date, "filter_type": filter_type}
        if start_time is not None:
            block["start_time"] = start_time
        if end_time is not None:
            block["end_time"] = end_time
        if end_date is not None:
            block["end_date"] = end_date
        return block

    async def create_heatmap(
        self,
        polygon_aoi: dict,
        start_date: str,
        filter_type: int,
        granularity: int = 100,
        start_time: str | None = None,
        end_time: str | None = None,
        end_date: str | None = None,
        analytic_type: str = "tcm",
        threshold: float | None = None,
        direction: str | None = None,
        *,
        refresh: bool = False,
    ) -> FortyGuardResult:
        """POST /v1/heatmap.

        ``tcm`` tiles carry ``properties.average_temperature`` in **degrees
        Celsius**. The vendor client's docstring says Fahrenheit; the vendor
        README's units table, the ``threshold`` parameter and the bundled cached
        responses all say Celsius, and the cached San Jose July values (16-28)
        are only physical as Celsius. Celsius is what this engine assumes, and
        ``thermcue.thermal.assert_plausible_air_temp`` fails loudly if a live
        response ever contradicts that.
        """
        if analytic_type not in ANALYTIC_TYPES:
            raise ValueError(f"Unknown analytic_type {analytic_type!r}; valid: {ANALYTIC_TYPES}")
        if analytic_type in ("exceedance", "persistence"):
            if threshold is None:
                raise ValueError(f"analytic_type={analytic_type!r} requires threshold (degrees C)")
            if direction not in ("above", "below"):
                raise ValueError(f"analytic_type={analytic_type!r} requires direction above/below")
        if granularity not in (60, 80, 100):
            raise ValueError("granularity must be 60, 80 or 100 metres")

        payload: dict[str, Any] = {
            "polygon_aoi": polygon_aoi,
            "date_time": self._date_time(start_date, filter_type, start_time, end_time, end_date),
            "granularity": granularity,
            "analytic_type": analytic_type,
        }
        if threshold is not None:
            payload["threshold"] = threshold
        if direction is not None:
            payload["direction"] = direction
        return await self.call("/v1/heatmap", payload, refresh=refresh)

    async def environmental_parameters(
        self,
        latitude: float,
        longitude: float,
        temperature: float,
        start_date: str,
        filter_type: int,
        start_time: str | None = None,
        end_time: str | None = None,
        end_date: str | None = None,
        analysis: Iterable[str] | None = None,
        *,
        refresh: bool = False,
    ) -> FortyGuardResult:
        """POST /v1/env_params.

        Read the response carefully: the endpoint applies the single
        ``temperature`` anchor across all 24 hours and varies only humidity. So
        ``heat_index_celsius`` and ``wet_bulb_temperature_celsius`` are
        humidity-sensitivity curves at a fixed temperature, **not** a diurnal
        series. This engine consumes ``relative_humidity_percent`` (which does
        vary as real humidity) and ``solar_irradiance``, and derives wet bulb
        itself from the per-hour, per-zone air temperature. See
        ``thermcue.thermal``.
        """
        if analysis is not None:
            analysis = list(analysis)
            unknown = set(analysis) - set(ENV_PARAMS_ANALYSES)
            if unknown:
                raise ValueError(f"Unknown env_params analysis {unknown}")
        payload: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "temperature": temperature,
            "date_time": self._date_time(start_date, filter_type, start_time, end_time, end_date),
        }
        if analysis is not None:
            payload["analysis"] = analysis
        return await self.call("/v1/env_params", payload, refresh=refresh)

    async def satellite_segmentation(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        filter_type: int,
        granularity: int = 100,
        start_time: str | None = None,
        end_time: str | None = None,
        end_date: str | None = None,
        *,
        refresh: bool = False,
    ) -> FortyGuardResult:
        """POST /v1/satellite (Premium). Land-cover fractions feed the shade model."""
        payload = {
            "sat": {"latitude": latitude, "longitude": longitude},
            "date_time": self._date_time(start_date, filter_type, start_time, end_time, end_date),
            "granularity": granularity,
        }
        return await self.call("/v1/satellite", payload, refresh=refresh)

    async def street_view_segmentation(
        self,
        latitude: float,
        longitude: float,
        vertical_angle: float = 0.0,
        horizontal_angle: float = 0.0,
        back_view: bool = False,
        *,
        refresh: bool = False,
    ) -> FortyGuardResult:
        """POST /v1/streetview (Premium). Ground-level sky/tree/building shares."""
        payload = {
            "latitude": latitude,
            "longitude": longitude,
            "vertical_angle": vertical_angle,
            "horizontal_angle": horizontal_angle,
            "back_view": back_view,
        }
        return await self.call("/v1/streetview", payload, refresh=refresh)

    async def fetch_api_key_usage(self) -> dict:
        """POST /v1/system/fetch-api-key-usage. Not cached: credits are live state."""
        resp = await self._request_with_retry(
            "POST",
            "/v1/system/fetch-api-key-usage",
            json_body={"api_key": self.settings.fortyguard_api_key},
        )
        return resp.json()
