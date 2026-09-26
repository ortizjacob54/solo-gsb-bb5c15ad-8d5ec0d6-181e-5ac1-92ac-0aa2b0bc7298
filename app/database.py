"""SQLite 连接与建表初始化。

数据库路径通过环境变量 DATABASE_PATH 配置（默认 tide.db）。
建表在服务启动时执行（CREATE TABLE IF NOT EXISTS，幂等），
因此 `docker compose up --build` 一条命令即可完成依赖安装、
数据库初始化和服务启动。
"""
import os
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DATABASE_PATH = os.getenv("DATABASE_PATH", "tide.db")

# 同港预约并发创建/改期时，进程内先按港口串行化，再由 BEGIN IMMEDIATE
# 保证跨进程/跨连接的原子性，避免双重占用。
_port_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def port_lock(port_id: int) -> threading.Lock:
    """返回某港口对应的进程内互斥锁。"""
    return _port_locks[port_id]


SCHEMA = """
CREATE TABLE IF NOT EXISTS ports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    reference_depth REAL    NOT NULL          -- 航道基准水深（米）
);

CREATE TABLE IF NOT EXISTS tide_forecasts (
    port_id       INTEGER NOT NULL
        REFERENCES ports(id) ON DELETE CASCADE,
    forecast_time TEXT    NOT NULL,           -- ISO 8601（统一存为 UTC）
    tide_height   REAL    NOT NULL,           -- 潮高（米，相对海图基准面）
    PRIMARY KEY (port_id, forecast_time)      -- 同一时刻重复录入即“更正”
);

CREATE TABLE IF NOT EXISTS tide_observations (
    port_id  INTEGER NOT NULL
        REFERENCES ports(id) ON DELETE CASCADE,
    obs_time TEXT    NOT NULL,                -- 实测时刻（ISO 8601，统一存为 UTC）
    tide_height REAL NOT NULL,                -- 实测潮高（米，相对海图基准面）
    PRIMARY KEY (port_id, obs_time)           -- 同港同一 UTC 时刻重复写入即覆盖
);

CREATE TABLE IF NOT EXISTS bridge_configs (
    port_id                 INTEGER PRIMARY KEY  -- 每港至多一处桥梁
        REFERENCES ports(id) ON DELETE CASCADE,
    bridge_bottom_elevation REAL NOT NULL        -- 梁底高程（米，相对潮高基准面）
);

CREATE TABLE IF NOT EXISTS tide_alert_rules (
    port_id              INTEGER PRIMARY KEY  -- 每港一条潮位预警规则
        REFERENCES ports(id) ON DELETE CASCADE,
    direction            TEXT    NOT NULL,    -- high（高潮）/ low（低潮）
    trigger_height       REAL    NOT NULL,    -- 触发潮高（米，相对海图基准面）
    clear_height         REAL    NOT NULL,    -- 解除潮高（米，相对海图基准面）
    min_duration_minutes REAL    NOT NULL     -- 最短持续分钟（非负）
);

CREATE INDEX IF NOT EXISTS idx_tide_observations_port
    ON tide_observations (port_id, obs_time);

CREATE TABLE IF NOT EXISTS reservations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    port_id                INTEGER NOT NULL
        REFERENCES ports(id) ON DELETE CASCADE,
    ship_name              TEXT    NOT NULL,
    draft                  REAL    NOT NULL,
    safety_margin          REAL    NOT NULL,
    duration_minutes       REAL    NOT NULL,   -- 通航时长（分钟，正数）
    allowed_departure_from TEXT   NOT NULL,   -- 允许出发范围起点（ISO 8601 UTC）
    allowed_departure_to   TEXT   NOT NULL,   -- 允许出发范围终点（ISO 8601 UTC）
    departure_start        TEXT   NOT NULL,   -- 排定的出发时刻（ISO 8601 UTC）
    departure_end          TEXT   NOT NULL,   -- 出发时刻 + 通航时长（ISO 8601 UTC）
    status                 TEXT   NOT NULL DEFAULT 'active',
                                                   -- active / cancelled
    created_at             TEXT   NOT NULL,
    canceled_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_reservations_port_status
    ON reservations (port_id, status, departure_start);
"""


def init_db() -> None:
    """创建数据目录与表结构（幂等）。"""
    db_path = Path(DATABASE_PATH)
    if db_path.parent and str(db_path.parent) != ".":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.executescript(SCHEMA)


def get_db():
    """FastAPI 依赖：每个请求一个 SQLite 连接，正常返回时提交。"""
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def locked_reservation_tx() -> Iterator[sqlite3.Connection]:
    """预约创建/改期专用：以 BEGIN IMMEDIATE 立即获取写锁的事务连接。

    检查（最新预报 + 有效预约）与写入在同一写事务内完成，
    提交前其他连接无法开始写事务，从而原子地避免双重占用；
    busy_timeout 使并发请求排队等待而非立即报 SQLITE_BUSY。
    """
    conn = sqlite3.connect(DATABASE_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
