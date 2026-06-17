"""
SWAN Data Logger

Logs sensor values from many nodes into PostgreSQL.

Storage:
- `logging_runs` stores one row per run and its config.
- `sensor_readings` is a LIST-partitioned parent table on `run_id`.
- every run gets its own `sensor_readings_<run>_<hash>` partition.

Intervals:
- sensor interval overrides node interval.
- node interval overrides the request default interval.
- reading timestamps are snapped to the smallest interval in the run.
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

import asyncpg
import httpx
from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, validator


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("data-logger")

PG_HOST = os.getenv("DB_HOST", "db")
PG_PORT = int(os.getenv("DB_PORT", "5432"))
PG_USER = os.getenv("DB_USER", "swan")
PG_PASS = os.getenv("DB_PASS", "swanpass")
PG_DB = os.getenv("DB_NAME", "swan_data")

NODE_MANAGER_URL = os.getenv("NODE_MANAGER_URL", "http://host.docker.internal:8000")
DEFAULT_DB_NAME_BASE = os.getenv("LOGGER_DB_NAME_BASE", "swan_log")
DEFAULT_INTERVAL_SECONDS = int(os.getenv("LOGGER_DEFAULT_INTERVAL_SECONDS", "5"))
MAX_FETCH_CONCURRENCY = int(os.getenv("LOGGER_MAX_FETCH_CONCURRENCY", "20"))
POLL_GRANULARITY_SECONDS = float(os.getenv("LOGGER_POLL_GRANULARITY_SECONDS", "0.25"))

RUNS_TABLE = "logging_runs"
READINGS_TABLE = "sensor_readings"
SCHEMA_LOCK_NAME = "swan_data_logger_schema"
DEFAULT_READINGS_PARTITION = f"{READINGS_TABLE}_default"


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------
app = FastAPI(title="SWAN Data Logger")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

db_pool: Optional[asyncpg.Pool] = None
logging_task: Optional[asyncio.Task] = None
stop_event: Optional[asyncio.Event] = None
run_meta: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------
class SensorConfig(BaseModel):
    name: str = Field(..., min_length=1)
    interval_ms: Optional[int] = Field(default=None, gt=0, exclude=True)
    interval_seconds: Optional[int] = Field(default=None, gt=0)

    @validator("name")
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("sensor name cannot be empty")
        return value

    @validator("interval_seconds", always=True)
    def interval_from_ms(cls, value: Optional[int], values: dict[str, Any]) -> Optional[int]:
        if value is not None:
            return value
        interval_ms = values.get("interval_ms")
        return None if interval_ms is None else max(1, round(interval_ms / 1000))


class NodeConfig(BaseModel):
    node_id: str = Field(..., min_length=1)
    sensors: list[SensorConfig]
    interval_ms: Optional[int] = Field(default=None, gt=0, exclude=True)
    interval_seconds: Optional[int] = Field(default=None, gt=0)

    @validator("node_id")
    def strip_node_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("node_id cannot be empty")
        return value

    @validator("sensors", pre=True)
    def normalize_sensors(cls, value: Any) -> list[Any]:
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",")]
        if not isinstance(value, list):
            raise ValueError("sensors must be a list")

        sensors = []
        for item in value:
            if isinstance(item, str) and item.strip():
                sensors.append({"name": item.strip()})
            elif isinstance(item, dict):
                sensors.append(item)
            else:
                raise ValueError("each sensor must be a name or object")
        if not sensors:
            raise ValueError("at least one sensor is required")
        return sensors

    @validator("interval_seconds", always=True)
    def interval_from_ms(cls, value: Optional[int], values: dict[str, Any]) -> Optional[int]:
        if value is not None:
            return value
        interval_ms = values.get("interval_ms")
        return None if interval_ms is None else max(1, round(interval_ms / 1000))


class StartRequest(BaseModel):
    db_name_base: str = Field(default=DEFAULT_DB_NAME_BASE, min_length=1)
    interval_ms: Optional[int] = Field(default=None, gt=0, exclude=True)
    interval_seconds: int = Field(default=DEFAULT_INTERVAL_SECONDS, gt=0)
    nodes: list[NodeConfig]

    @validator("db_name_base")
    def clean_db_name_base(cls, value: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_-]", "_", value.strip())
        if not value:
            raise ValueError("db_name_base is empty after sanitization")
        return value

    @validator("interval_seconds", always=True)
    def interval_from_ms(cls, value: int, values: dict[str, Any]) -> int:
        interval_ms = values.get("interval_ms")
        return value if interval_ms is None else max(1, round(interval_ms / 1000))


class StatusResponse(BaseModel):
    running: bool
    db_name: Optional[str] = None
    table_name: Optional[str] = None
    partition_name: Optional[str] = None
    run_id: Optional[str] = None
    started_at: Optional[datetime] = None
    grid_interval_seconds: Optional[int] = None
    nodes: Optional[list[NodeConfig]] = None


# ---------------------------------------------------------------------------
# Request and config helpers
# ---------------------------------------------------------------------------
class DuplicateJSONKeyError(ValueError):
    pass


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data = {}
    for key, value in pairs:
        if key in data:
            raise DuplicateJSONKeyError(f"Duplicate JSON key: {key}")
        data[key] = value
    return data


async def parse_start_request(request: Request) -> StartRequest:
    """Parse manually so duplicate JSON keys do not get silently overwritten."""
    try:
        data = json.loads(await request.body(), object_pairs_hook=reject_duplicate_json_keys)
        return StartRequest(**data)
    except DuplicateJSONKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc.msg}")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json()))


def build_run_config(payload: StartRequest) -> tuple[list[dict[str, Any]], int]:
    nodes = []
    for node in payload.nodes:
        node_interval = node.interval_seconds or payload.interval_seconds
        sensors = [
            {
                "name": sensor.name,
                "interval_seconds": sensor.interval_seconds or node_interval,
                "next_due_at": None,
            }
            for sensor in node.sensors
        ]
        nodes.append({"node_id": node.node_id, "sensors": sensors})

    grid_interval = min(sensor["interval_seconds"] for node in nodes for sensor in node["sensors"])
    incompatible = [
        f"{node['node_id']}.{sensor['name']}={sensor['interval_seconds']}s"
        for node in nodes
        for sensor in node["sensors"]
        if sensor["interval_seconds"] % grid_interval != 0
    ]
    if incompatible:
        raise HTTPException(
            status_code=422,
            detail=(
                "All sensor intervals must be multiples of the lowest interval "
                f"({grid_interval}s): {', '.join(incompatible)}"
            ),
        )
    return nodes, grid_interval


def public_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "node_id": node["node_id"],
            "sensors": [
                {"name": sensor["name"], "interval_seconds": sensor["interval_seconds"]}
                for sensor in node["sensors"]
            ],
        }
        for node in nodes
    ]


def quantize_timestamp(start_time: datetime, when: datetime, interval_seconds: int) -> datetime:
    seconds = max(0, int((when - start_time).total_seconds()))
    return start_time + timedelta(seconds=(seconds // interval_seconds) * interval_seconds)


def coerce_numeric(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def partition_name_for_run(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", run_id).lower()
    digest = hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:10]
    prefix = f"{READINGS_TABLE}_"
    return f"{prefix}{safe[:63 - len(prefix) - len(digest) - 1]}_{digest}"


def unique_index_name_for_run(run_id: str) -> str:
    digest = hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:16]
    return f"uidx_{READINGS_TABLE}_{digest}"


async def lock_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", SCHEMA_LOCK_NAME)


async def create_pool() -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASS,
        database=PG_DB,
        min_size=1,
        max_size=10,
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_schema(conn)
    return pool


async def create_raw_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASS,
        database=PG_DB,
        min_size=1,
        max_size=10,
    )


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await lock_schema(conn)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
            run_id TEXT PRIMARY KEY,
            db_name_base TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            stopped_at TIMESTAMPTZ,
            config JSONB NOT NULL
        );
        """
    )

    if await readings_table_exists(conn) and not await readings_table_is_run_partitioned(conn):
        await rename_incompatible_readings_table(conn)

    await create_partitioned_readings_parent(conn)
    await create_default_readings_partition(conn)
    await ensure_parent_indexes(conn)


async def readings_table_exists(conn: asyncpg.Connection) -> bool:
    return await table_exists(conn, READINGS_TABLE)


async def table_exists(conn: asyncpg.Connection, table_name: str) -> bool:
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND c.relname = $1
        )
        """,
        table_name,
    )


async def readings_table_is_run_partitioned(conn: asyncpg.Connection) -> bool:
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_partitioned_table pt ON pt.partrelid = c.oid
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(pt.partattrs)
            WHERE n.nspname = current_schema()
              AND c.relname = $1
              AND c.relkind = 'p'
              AND pt.partstrat = 'l'
              AND a.attname = 'run_id'
        )
        """,
        READINGS_TABLE,
    )


async def rename_incompatible_readings_table(conn: asyncpg.Connection) -> None:
    legacy_table = f"{READINGS_TABLE}_legacy_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
    log.warning("Renaming incompatible %s table to %s before creating run-partitioned schema", READINGS_TABLE, legacy_table)
    await conn.execute(f"ALTER TABLE {READINGS_TABLE} RENAME TO {legacy_table}")
    await rename_legacy_indexes(conn, legacy_table)


async def rename_legacy_indexes(conn: asyncpg.Connection, legacy_table: str) -> None:
    rows = await conn.fetch(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename = $1
          AND indexname LIKE $2
        """,
        legacy_table,
        f"idx_{READINGS_TABLE}%",
    )
    for row in rows:
        old_name = row["indexname"]
        digest = hashlib.sha1(f"{legacy_table}_{old_name}".encode("utf-8")).hexdigest()[:8]
        new_name = f"{old_name[:54]}_{digest}"
        await conn.execute(f"ALTER INDEX {old_name} RENAME TO {new_name}")


async def create_partitioned_readings_parent(conn: asyncpg.Connection) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {READINGS_TABLE} (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY,
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            sensor TEXT NOT NULL,
            value NUMERIC,
            timestamp_utc TIMESTAMPTZ NOT NULL
        ) PARTITION BY LIST (run_id);
        """
    )


async def create_default_readings_partition(conn: asyncpg.Connection) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DEFAULT_READINGS_PARTITION}
        PARTITION OF {READINGS_TABLE} DEFAULT;
        """
    )


async def ensure_parent_indexes(conn: asyncpg.Connection) -> None:
    await conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{READINGS_TABLE}_run_node_sensor_time
        ON {READINGS_TABLE} (run_id, node_id, sensor, timestamp_utc);
        """
    )
    await conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{READINGS_TABLE}_run_time
        ON {READINGS_TABLE} (run_id, timestamp_utc);
        """
    )


async def create_run_partition_on_conn(conn: asyncpg.Connection, run_id: str) -> str:
    partition_name = partition_name_for_run(run_id)
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {partition_name}
        PARTITION OF {READINGS_TABLE}
        FOR VALUES IN ({sql_literal(run_id)});
        """
    )
    await conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {unique_index_name_for_run(run_id)}
        ON {partition_name} (node_id, sensor, timestamp_utc);
        """
    )
    return partition_name


async def create_run_partition(pool: asyncpg.Pool, run_id: str) -> str:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_schema(conn)
            return await create_run_partition_on_conn(conn, run_id)


async def create_run_and_record_start(
    pool: asyncpg.Pool,
    payload: StartRequest,
    run_id: str,
    started_at: datetime,
    nodes: list[dict[str, Any]],
    grid_interval_seconds: int,
) -> str:
    config_base = {
        "db_name_base": payload.db_name_base,
        "default_interval_seconds": payload.interval_seconds,
        "grid_interval_seconds": grid_interval_seconds,
        "nodes": public_nodes(nodes),
    }
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_schema(conn)
            partition_name = await create_run_partition_on_conn(conn, run_id)
            config = {**config_base, "partition_name": partition_name}
            await conn.execute(
                f"""
                INSERT INTO {RUNS_TABLE} (run_id, db_name_base, started_at, config)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                run_id,
                payload.db_name_base,
                started_at,
                json.dumps(config),
            )
            return partition_name


async def record_run_stop(pool: asyncpg.Pool, run_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE {RUNS_TABLE} SET stopped_at = $2 WHERE run_id = $1",
            run_id,
            datetime.now(timezone.utc),
        )


async def insert_readings(pool: asyncpg.Pool, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    try:
        await execute_insert_readings(pool, rows)
    except asyncpg.CheckViolationError:
        run_id = rows[0][0]
        log.warning("Missing readings partition for run_id=%s; creating partition and retrying insert once", run_id)
        await create_run_partition(pool, run_id)
        await execute_insert_readings(pool, rows)


async def execute_insert_readings(pool: asyncpg.Pool, rows: list[tuple[Any, ...]]) -> None:
    async with pool.acquire() as conn:
        await conn.executemany(
            f"""
            INSERT INTO {READINGS_TABLE} (run_id, node_id, sensor, value, timestamp_utc)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )


def reading_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "node_id": row["node_id"],
        "sensor": row["sensor"],
        "value": float(row["value"]) if row["value"] is not None else None,
        "timestamp_utc": row["timestamp_utc"].isoformat(),
    }


def build_reading_filters(
    run_id: str,
    node_id: Optional[str] = None,
    sensor: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
) -> tuple[list[str], list[Any]]:
    filters = ["run_id = $1"]
    values: list[Any] = [run_id]

    for column, value in (("node_id", node_id), ("sensor", sensor)):
        if value:
            values.append(value)
            filters.append(f"{column} = ${len(values)}")
    if from_ts:
        values.append(from_ts)
        filters.append(f"timestamp_utc >= ${len(values)}")
    if to_ts:
        values.append(to_ts)
        filters.append(f"timestamp_utc <= ${len(values)}")

    return filters, values


async def fetch_readings(
    pool: asyncpg.Pool,
    filters: list[str],
    values: list[Any],
    limit: Optional[int] = None,
) -> list[asyncpg.Record]:
    limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
    async with pool.acquire() as conn:
        try:
            return await conn.fetch(
                f"""
                SELECT id, run_id, node_id, sensor, value, timestamp_utc
                FROM {READINGS_TABLE}
                WHERE {' AND '.join(filters)}
                ORDER BY timestamp_utc ASC
                {limit_clause}
                """,
                *values,
            )
        except asyncpg.UndefinedTableError:
            return []


async def fetch_reading_counts(
    pool: asyncpg.Pool,
    filters: list[str],
    values: list[Any],
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        try:
            return await conn.fetch(
                f"""
                SELECT run_id, node_id, sensor, COUNT(*)::BIGINT AS datapoints
                FROM {READINGS_TABLE}
                WHERE {' AND '.join(filters)}
                GROUP BY run_id, node_id, sensor
                ORDER BY node_id ASC, sensor ASC
                """,
                *values,
            )
        except asyncpg.UndefinedTableError:
            return []


def affected_rows(command_tag: str) -> int:
    try:
        return int(command_tag.split()[-1])
    except (IndexError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Node polling worker
# ---------------------------------------------------------------------------
async def fetch_node(node_id: str, timeout: int = 5) -> dict[str, Any]:
    url = f"{NODE_MANAGER_URL}/nodes/{node_id}/sync-read?timeout={timeout}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 2.0, connect=5.0)) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def due_sensors(node: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    return [sensor for sensor in node["sensors"] if sensor["next_due_at"] is None or sensor["next_due_at"] <= now]


def sleep_until_next_sensor(nodes: list[dict[str, Any]], now: datetime) -> float:
    due_times = [
        sensor["next_due_at"]
        for node in nodes
        for sensor in node["sensors"]
        if sensor["next_due_at"] is not None
    ]
    if not due_times:
        return POLL_GRANULARITY_SECONDS
    return max(0.01, min((due_at - now).total_seconds() for due_at in due_times))


async def logging_worker(pool: asyncpg.Pool, cfg: dict[str, Any]) -> None:
    """Fetch each due node once and write all due sensor readings for that node."""
    global run_meta, stop_event

    nodes = cfg["nodes"]
    run_id = cfg["run_id"]
    start_time = cfg["start_time_utc"]
    grid_interval = cfg["grid_interval_seconds"]
    semaphore = asyncio.Semaphore(MAX_FETCH_CONCURRENCY)

    async def read_node(node: dict[str, Any], sensors: list[dict[str, Any]], read_at: datetime) -> list[tuple[Any, ...]]:
        node_id = node["node_id"]
        timeout = min(max(sensor["interval_seconds"] for sensor in sensors), 30)

        try:
            async with semaphore:
                payload = await fetch_node(node_id, timeout=timeout)
            sync_data = payload.get("sync_data", {}) if isinstance(payload, dict) else {}
            values = sync_data.get("s", {}) if isinstance(sync_data, dict) else {}
        except Exception as exc:
            log.warning("Failed fetch for %s: %s", node_id, exc)
            values = {}

        reading_ts = quantize_timestamp(start_time, read_at, grid_interval)
        rows = []
        for sensor in sensors:
            rows.append((run_id, node_id, sensor["name"], coerce_numeric(values.get(sensor["name"])), reading_ts))
            sensor["next_due_at"] = read_at + timedelta(seconds=sensor["interval_seconds"])
        return rows

    log.info(
        "Worker started: run=%s nodes=%d sensors=%d grid=%ds",
        run_id,
        len(nodes),
        sum(len(node["sensors"]) for node in nodes),
        grid_interval,
    )

    try:
        while stop_event and not stop_event.is_set():
            read_at = datetime.now(timezone.utc)
            jobs = [(node, due_sensors(node, read_at)) for node in nodes]
            jobs = [(node, sensors) for node, sensors in jobs if sensors]

            if jobs:
                batches = await asyncio.gather(*(read_node(node, sensors, read_at) for node, sensors in jobs))
                await insert_readings(pool, [row for batch in batches for row in batch])

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_until_next_sensor(nodes, datetime.now(timezone.utc)))
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        log.info("Worker cancelled")
    except Exception:
        log.exception("Worker failed")
        run_meta = None
        if stop_event:
            stop_event.set()
    finally:
        log.info("Worker stopped")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/logging/start")
async def start_logging(request: Request, _payload_schema: StartRequest = Body(...)):
    global db_pool, logging_task, stop_event, run_meta

    payload = await parse_start_request(request)
    if logging_task and not logging_task.done():
        raise HTTPException(status_code=409, detail="A logging run is already active")

    nodes, grid_interval = build_run_config(payload)
    run_id = f"{payload.db_name_base}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"

    started_at = datetime.now(timezone.utc)
    try:
        db_pool = await create_pool()
        partition_name = await create_run_and_record_start(db_pool, payload, run_id, started_at, nodes, grid_interval)
    except asyncpg.UniqueViolationError:
        if db_pool:
            await db_pool.close()
            db_pool = None
        raise HTTPException(
            status_code=409,
            detail="Run ID already exists for this minute; change run name or wait until the next minute",
        )
    except Exception as exc:
        log.exception("Failed to initialize database")
        if db_pool:
            await db_pool.close()
            db_pool = None
        raise HTTPException(status_code=500, detail=f"Failed to initialize DB: {exc}")

    run_meta = {
        "db_name": PG_DB,
        "table_name": READINGS_TABLE,
        "partition_name": partition_name,
        "run_id": run_id,
        "started_at": started_at,
        "grid_interval_seconds": grid_interval,
        "nodes": nodes,
    }
    stop_event = asyncio.Event()
    logging_task = asyncio.create_task(
        logging_worker(
            db_pool,
            {
                "run_id": run_id,
                "nodes": nodes,
                "start_time_utc": started_at,
                "grid_interval_seconds": grid_interval,
            },
        )
    )

    return {
        "status": "started",
        "db_name": PG_DB,
        "table_name": READINGS_TABLE,
        "partition_name": partition_name,
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "grid_interval_seconds": grid_interval,
        "nodes": public_nodes(nodes),
    }


@app.post("/logging/stop")
async def stop_logging():
    global db_pool, logging_task, stop_event, run_meta

    if not logging_task or logging_task.done():
        raise HTTPException(status_code=404, detail="No active logging run")

    if stop_event:
        stop_event.set()
    await logging_task
    logging_task = None

    if db_pool and run_meta:
        await record_run_stop(db_pool, run_meta["run_id"])
        await db_pool.close()

    db_pool = None
    run_meta = None
    return {"status": "stopped"}


@app.get("/logging/status", response_model=StatusResponse)
async def logging_status():
    if not logging_task or logging_task.done() or run_meta is None:
        return StatusResponse(running=False)
    return StatusResponse(
        running=True,
        db_name=run_meta["db_name"],
        table_name=run_meta["table_name"],
        partition_name=run_meta["partition_name"],
        run_id=run_meta["run_id"],
        started_at=run_meta["started_at"],
        grid_interval_seconds=run_meta["grid_interval_seconds"],
        nodes=public_nodes(run_meta["nodes"]),
    )


@app.get("/logging/runs")
async def list_runs(limit: int = Query(default=100, gt=0, le=1000)):
    pool = await create_raw_pool()
    try:
        async with pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    f"""
                    SELECT run_id, db_name_base, started_at, stopped_at, config
                    FROM {RUNS_TABLE}
                    ORDER BY started_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            except asyncpg.UndefinedTableError:
                rows = []
    finally:
        await pool.close()

    return [
        {
            "run_id": row["run_id"],
            "db_name_base": row["db_name_base"],
            "started_at": row["started_at"].isoformat(),
            "stopped_at": row["stopped_at"].isoformat() if row["stopped_at"] else None,
            "config": row["config"],
        }
        for row in rows
    ]


@app.delete("/logging/runs/{run_id}")
async def delete_run(run_id: str):
    active_run_id = run_meta["run_id"] if run_meta else None
    if active_run_id == run_id and logging_task and not logging_task.done():
        raise HTTPException(status_code=409, detail="Cannot delete the active logging run")

    pool = await create_pool()
    partition_name = partition_name_for_run(run_id)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await lock_schema(conn)
                run_deleted = await conn.fetchval(
                    f"DELETE FROM {RUNS_TABLE} WHERE run_id = $1 RETURNING run_id",
                    run_id,
                )

                partition_dropped = await table_exists(conn, partition_name)
                if partition_dropped:
                    await conn.execute(f"DROP TABLE {partition_name}")

                delete_tag = await conn.execute(f"DELETE FROM {READINGS_TABLE} WHERE run_id = $1", run_id)
                fallback_rows_deleted = affected_rows(delete_tag)

                if not run_deleted and not partition_dropped and fallback_rows_deleted == 0:
                    raise HTTPException(status_code=404, detail="Run session not found")
    finally:
        await pool.close()

    return {
        "status": "deleted",
        "run_id": run_id,
        "partition_name": partition_name,
        "partition_dropped": partition_dropped,
        "fallback_rows_deleted": fallback_rows_deleted,
    }


@app.get("/data")
async def query_data(
    run_id: Optional[str] = None,
    node_id: Optional[str] = None,
    sensor: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
    limit: int = Query(default=500, gt=0, le=10000),
):
    query_run_id = run_id or (run_meta["run_id"] if run_meta else None)
    if query_run_id is None:
        raise HTTPException(status_code=404, detail="No active logging run")

    pool = db_pool or await create_raw_pool()
    should_close_pool = db_pool is None
    filters, values = build_reading_filters(query_run_id, node_id, sensor, from_ts, to_ts)

    try:
        rows = await fetch_readings(pool, filters, values, limit=limit)
    finally:
        if should_close_pool:
            await pool.close()

    return [reading_to_dict(row) for row in rows]


@app.get("/data/counts")
async def query_data_counts(
    run_id: str,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
):
    pool = db_pool or await create_raw_pool()
    should_close_pool = db_pool is None
    filters, values = build_reading_filters(run_id, from_ts=from_ts, to_ts=to_ts)

    try:
        rows = await fetch_reading_counts(pool, filters, values)
    finally:
        if should_close_pool:
            await pool.close()

    return [
        {
            "run_id": row["run_id"],
            "node_id": row["node_id"],
            "sensor": row["sensor"],
            "datapoints": row["datapoints"],
        }
        for row in rows
    ]


@app.get("/export")
async def export_data(
    run_id: str,
    node_id: Optional[str] = None,
    sensor: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
):
    pool = db_pool or await create_raw_pool()
    should_close_pool = db_pool is None
    filters, values = build_reading_filters(run_id, node_id, sensor, from_ts, to_ts)

    try:
        rows = await fetch_readings(pool, filters, values)
    finally:
        if should_close_pool:
            await pool.close()

    data = [reading_to_dict(row) for row in rows]
    filename_base = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)

    if format == "json":
        return JSONResponse(
            data,
            headers={"Content-Disposition": f'attachment; filename="{filename_base}_export.json"'},
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "run_id", "node_id", "sensor", "value", "timestamp_utc"])
    writer.writeheader()
    writer.writerows(data)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename_base}_export.csv"'},
    )

""" 
{
  "db_name_base": "swan_log",
  "interval_seconds": 5,
  "nodes": [
    {
      "node_id": "node-01",
      "sensors": [
        {
          "name": "temperature",
          "interval_seconds": 15
        },
        {
          "name": "humidity",
          "interval_seconds": 20
        }
      ],
      "interval_seconds": 1
    },
    {
      "node_id": "node-03",
      "sensors": [
        {
          "name": "temperature",
          "interval_seconds": 5
        }
      ],
      "interval_seconds": 10
    }
  ]
}
"""
