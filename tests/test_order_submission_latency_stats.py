import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "osl"
    / "order_submission_latency_stats.py"
)


def _make_orders_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (date_added TEXT, client_order_id TEXT)")
    conn.execute(
        "INSERT INTO orders (date_added, client_order_id) VALUES (?, ?)",
        ("2026-01-01T00:00:00Z", "s_f:ethusd:1774361033::9_1774361034906"),
    )
    conn.commit()
    conn.close()


def test_script_accepts_db_flag(tmp_path):
    db_path = tmp_path / "custom_orders.db"
    _make_orders_db(db_path)

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--db", str(db_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "rows_read: 1" in result.stdout


def test_script_handles_null_date_added_without_crashing(tmp_path):
    db_path = tmp_path / "null_date_added.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE orders (date_added TEXT, client_order_id TEXT)")
    conn.execute(
        "INSERT INTO orders (date_added, client_order_id) VALUES (?, ?)",
        (None, "s_f:ethusd:1774361033::9_1774361034906"),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--db", str(db_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "rows_read: 1" in result.stdout
    assert "date_added_parse_failed: 1" in result.stdout


def test_script_csv_output(tmp_path):
    db_path = tmp_path / "csv_output.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE orders (date_added TEXT, client_order_id TEXT)")

    sub_ms = 1_700_000_000_000
    added_ms = sub_ms + 100
    date_added = datetime.fromtimestamp(
        added_ms / 1000.0,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    cid = f"s_f:ethusd:1774361033::9_{sub_ms}"
    conn.execute(
        "INSERT INTO orders (date_added, client_order_id) VALUES (?, ?)",
        (date_added, cid),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--db", str(db_path), "--tables", "orders", "--csv"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "rows_read,date_added_parse_failed,mean,min,max,p90,p99"
    assert lines[1] == "1,0,100.000000,100.000000,100.000000,100.000000,100.000000"


def test_script_tables_output_includes_per_table_and_total(tmp_path):
    db_path = tmp_path / "multi_table.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t1 (date_added TEXT, client_order_id TEXT)")
    conn.execute("CREATE TABLE t2 (date_added TEXT, client_order_id TEXT)")

    sub_ms_1 = 1_700_000_000_000
    added_ms_1 = sub_ms_1 + 100
    date_added_1 = datetime.fromtimestamp(
        added_ms_1 / 1000.0,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO t1 (date_added, client_order_id) VALUES (?, ?)",
        (date_added_1, f"s_f:ethusd:1774361033::9_{sub_ms_1}"),
    )

    sub_ms_2 = 1_700_000_000_500
    added_ms_2 = sub_ms_2 + 200
    date_added_2 = datetime.fromtimestamp(
        added_ms_2 / 1000.0,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO t2 (date_added, client_order_id) VALUES (?, ?)",
        (date_added_2, f"s_f:ethusd:1774361033::9_{sub_ms_2}"),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--db", str(db_path), "--tables", "t1,t2"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "table: t1" in result.stdout
    assert "table: t2" in result.stdout
    assert "table: TOTAL" in result.stdout
    assert "rows_read: 2" in result.stdout
    assert "mean: 150.000000" in result.stdout


def test_script_tables_csv_output_includes_total_row(tmp_path):
    db_path = tmp_path / "multi_table_csv.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t1 (date_added TEXT, client_order_id TEXT)")
    conn.execute("CREATE TABLE t2 (date_added TEXT, client_order_id TEXT)")

    sub_ms_1 = 1_700_000_000_000
    added_ms_1 = sub_ms_1 + 100
    date_added_1 = datetime.fromtimestamp(
        added_ms_1 / 1000.0,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO t1 (date_added, client_order_id) VALUES (?, ?)",
        (date_added_1, f"s_f:ethusd:1774361033::9_{sub_ms_1}"),
    )

    sub_ms_2 = 1_700_000_000_500
    added_ms_2 = sub_ms_2 + 200
    date_added_2 = datetime.fromtimestamp(
        added_ms_2 / 1000.0,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO t2 (date_added, client_order_id) VALUES (?, ?)",
        (date_added_2, f"s_f:ethusd:1774361033::9_{sub_ms_2}"),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db",
            str(db_path),
            "--tables",
            "t1,t2",
            "--csv",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "table,rows_read,date_added_parse_failed,mean,min,max,p90,p99"
    assert "t1,1,0,100.000000,100.000000,100.000000,100.000000,100.000000" in lines
    assert "t2,1,0,200.000000,200.000000,200.000000,200.000000,200.000000" in lines
    assert "TOTAL,2,0,150.000000,100.000000,200.000000,200.000000,200.000000" in lines


def test_script_custom_quantiles_in_csv_output(tmp_path):
    db_path = tmp_path / "custom_quantiles.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE orders (date_added TEXT, client_order_id TEXT)")
    for i, latency in enumerate([100, 200, 300, 400], start=1):
        sub_ms = 1_700_000_000_000 + i
        added_ms = sub_ms + latency
        date_added = datetime.fromtimestamp(
            added_ms / 1000.0,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        conn.execute(
            "INSERT INTO orders (date_added, client_order_id) VALUES (?, ?)",
            (date_added, f"s_f:ethusd:1774361033::9_{sub_ms}"),
        )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--db",
            str(db_path),
            "--tables",
            "orders",
            "--csv",
            "--quantiles",
            "75,90,95,99.99",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "rows_read,date_added_parse_failed,mean,min,max,p75,p90,p95,p99.99"
    assert lines[1] == "4,0,250.000000,100.000000,400.000000,300.000000,400.000000,400.000000,400.000000"


def test_script_short_flags_work(tmp_path):
    db_path = tmp_path / "short_flags.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE orders (date_added TEXT, client_order_id TEXT)")
    sub_ms = 1_700_000_000_000
    added_ms = sub_ms + 123
    date_added = datetime.fromtimestamp(
        added_ms / 1000.0,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO orders (date_added, client_order_id) VALUES (?, ?)",
        (date_added, f"s_f:ethusd:1774361033::9_{sub_ms}"),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "-d",
            str(db_path),
            "-t",
            "orders",
            "--csv",
            "-q",
            "75,90,95",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "rows_read,date_added_parse_failed,mean,min,max,p75,p90,p95"
    assert lines[1] == "1,0,123.000000,123.000000,123.000000,123.000000,123.000000,123.000000"

