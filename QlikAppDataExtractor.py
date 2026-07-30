import ssl
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3
import websocket

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOGGER_NAME = "data_extractor"
DEFAULT_CHUNK_SIZE = 1000
logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())


def build_ws_url(host, app_guid):
    return f"wss://{host}/app/{app_guid}"


def send_request(ws, method, params, handle=-1, msg_id=1):
    payload = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "handle": handle,
        "params": params,
    }
    ws.send(json.dumps(payload))
    while True:
        response = json.loads(ws.recv())
        if response.get("id") == msg_id:
            return response


def open_engine_connection(host, headers, app_guid):
    logger.info("Connecting to Qlik Sense Engine...")
    ws_url = build_ws_url(host, app_guid)
    try:
        ws = websocket.create_connection(
            ws_url,
            sslopt={"cert_reqs": ssl.CERT_NONE},
            header=headers,
        )
    except Exception as e:
        logger.error("Engine connection failed: %s", e)
        raise
    logger.info("Engine connection established.")
    return ws


def open_app(ws, app_guid):
    logger.info("Opening Qlik application...")
    open_doc_res = send_request(ws, "OpenDoc", {"qDocName": app_guid}, handle=-1, msg_id=1)
    if "error" in open_doc_res:
        raise RuntimeError(f"Failed to open document: {open_doc_res['error']}")
    app_handle = open_doc_res["result"]["qReturn"]["qHandle"]
    logger.info("Application opened successfully. App handle: %s", app_handle)
    return app_handle


def get_table_metadata(ws, app_handle, msg_id=99):
    tables_res = send_request(
        ws,
        "GetTablesAndKeys",
        [{"qcx": 1000, "qcy": 1000}, {"qcx": 0, "qcy": 0}, 30, False, False],
        handle=app_handle,
        msg_id=msg_id,
    )
    if "error" in tables_res:
        raise RuntimeError(f"Failed to list tables: {tables_res['error']}")

    table_metadata = {}
    for table in tables_res["result"]["qtr"]:
        table_metadata[table["qName"]] = {
            "rowCount": table["qNoOfRows"],
            "columns": table["qFields"],
        }
    return table_metadata


def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "unnamed_table"


def generate_json_file(table_name, table_json, output_dir=None):
    output_dir = output_dir or Path("output")
    output_dir.mkdir(exist_ok=True)
    file_path = output_dir / f"{safe_filename(table_name)}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(table_json, f, indent=4, ensure_ascii=False)
    logger.info("Saved %s", file_path)
    return file_path


def cell_to_text(cell):
    """Convert a Qlik cell object to display text."""
    if cell is None:
        return ""
    if isinstance(cell, dict):
        return cell.get("qText", cell.get("qNum", ""))
    return str(cell)


def parse_row_to_dict(column_names, q_value, extra_column_names):
    """
    Map Qlik row cells to column names.

    Vizlib Writeback and some Qlik tables return more (or fewer) cells than
    GetTablesAndKeys reports in qFields. Extra cells are stored under
    _unmapped_col_N keys; missing cells default to empty strings.
    """
    cells = q_value if q_value is not None else []
    row_dict = {}

    for i, col_name in enumerate(column_names):
        row_dict[col_name] = cell_to_text(cells[i]) if i < len(cells) else ""

    for i in range(len(column_names), len(cells)):
        extra_idx = i - len(column_names)
        while len(extra_column_names) <= extra_idx:
            extra_column_names.append(f"_unmapped_col_{len(extra_column_names) + 1}")
        row_dict[extra_column_names[extra_idx]] = cell_to_text(cells[i])

    return row_dict


def build_table_json(table_name, table_meta, all_rows, extra_column_names=None):
    column_names = [c["qName"] for c in table_meta["columns"]]
    all_column_names = column_names + (extra_column_names or [])
    return {
        "tableName": table_name,
        "metadataRowCount": table_meta["rowCount"],
        "rowCount": len(all_rows),
        "columnCount": len(all_column_names),
        "columns": [
            {"name": col["qName"], "type": col.get("qTags", [])}
            for col in table_meta["columns"]
        ]
        + [
            {"name": name, "type": ["$unmapped"]}
            for name in (extra_column_names or [])
        ],
        "rows": all_rows,
    }


def extract_table_data(ws, app_handle, table_name, table_meta, chunk_size, msg_id):
    row_count = table_meta["rowCount"]
    if row_count == 0:
        logger.info("Skipping empty table: '%s'", table_name)
        return build_table_json(table_name, table_meta, []), msg_id

    column_names = [c["qName"] for c in table_meta["columns"]]
    extra_column_names = []
    all_rows = []
    offset = 0
    mismatch_logged = False

    logger.info("Extracting table '%s' (%s rows, %s columns)", table_name, row_count, len(column_names))

    while offset < row_count:
        params = {
            "qOffset": offset,
            "qRows": chunk_size,
            "qSyntheticMode": False,
            "qTableName": table_name,
        }
        data_res = send_request(ws, "GetTableData", params, handle=app_handle, msg_id=msg_id)
        msg_id += 1

        if "error" in data_res:
            raise RuntimeError(f"API error fetching '{table_name}': {data_res['error']}")

        data_pages = data_res.get("result", {}).get("qData", [])
        if not data_pages:
            break

        for row in data_pages:
            q_value = row.get("qValue", [])
            if not mismatch_logged and len(q_value) != len(column_names):
                logger.warning(
                    "Table '%s': row cell count (%s) differs from metadata column count (%s). "
                    "Extra/missing cells will be mapped safely (common with Vizlib Writeback).",
                    table_name,
                    len(q_value),
                    len(column_names),
                )
                mismatch_logged = True

            all_rows.append(parse_row_to_dict(column_names, q_value, extra_column_names))

        fetched = len(data_pages)
        offset += fetched
        logger.info("  '%s': fetched %s rows (%s/%s)", table_name, fetched, offset, row_count)

        if fetched < chunk_size:
            break

    if extra_column_names:
        logger.info(
            "Table '%s': captured %s unmapped column(s): %s",
            table_name,
            len(extra_column_names),
            ", ".join(extra_column_names),
        )

    table_json = build_table_json(table_name, table_meta, all_rows, extra_column_names)
    logger.info("Finished '%s': %s rows extracted.", table_name, len(all_rows))
    return table_json, msg_id


def resolve_tables_to_extract(table_metadata, config):
    """Return ordered list of table names based on config (all, single, or explicit list)."""
    all_tables = list(table_metadata.keys())
    if not all_tables:
        return []

    explicit_list = config.get("table_names")
    single_table = config.get("table_name")

    if explicit_list:
        if isinstance(explicit_list, str):
            explicit_list = [explicit_list]
        missing = [t for t in explicit_list if t not in table_metadata]
        for name in missing:
            logger.warning("Configured table not found in app: '%s'", name)
        return [t for t in explicit_list if t in table_metadata]

    if single_table:
        if single_table not in table_metadata:
            raise KeyError(f"Table '{single_table}' not found in application.")
        return [single_table]

    return all_tables


def extract_qlik_app(host, headers, app_guid, config):
    chunk_size = config.get("chunk_size", DEFAULT_CHUNK_SIZE)
    skip_empty = config.get("skip_empty_tables", False)
    output_dir = Path(config.get("output_dir", "output"))

    ws = open_engine_connection(host, headers, app_guid)
    summary = {
        "appGuid": app_guid,
        "extractedAt": datetime.now(timezone.utc).isoformat(),
        "tables": {},
        "failures": {},
    }

    try:
        ws.recv()
        app_handle = open_app(ws, app_guid)

        table_metadata = get_table_metadata(ws, app_handle, msg_id=99)
        tables_to_extract = resolve_tables_to_extract(table_metadata, config)

        logger.info(
            "Found %s table(s) in app; extracting %s.",
            len(table_metadata),
            len(tables_to_extract),
        )
        for name in tables_to_extract:
            logger.info("  - %s (%s rows)", name, table_metadata[name]["rowCount"])

        msg_id = 100
        for table_name in tables_to_extract:
            meta = table_metadata[table_name]
            if skip_empty and meta["rowCount"] == 0:
                logger.info("Skipping empty table (skip_empty_tables=true): '%s'", table_name)
                summary["tables"][table_name] = {"status": "skipped", "rowCount": 0}
                continue

            try:
                table_json, msg_id = extract_table_data(
                    ws, app_handle, table_name, meta, chunk_size, msg_id
                )
                generate_json_file(table_name, table_json, output_dir)
                summary["tables"][table_name] = {
                    "status": "success",
                    "rowCount": table_json["rowCount"],
                    "columnCount": table_json["columnCount"],
                }
            except Exception as e:
                logger.error("Failed to extract table '%s': %s", table_name, e)
                summary["failures"][table_name] = str(e)

        summary_path = output_dir / "_extraction_summary.json"
        output_dir.mkdir(exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=4, ensure_ascii=False)
        logger.info("Extraction summary saved to %s", summary_path)

        succeeded = len(summary["tables"])
        failed = len(summary["failures"])
        logger.info("Extraction complete: %s succeeded, %s failed.", succeeded, failed)
        return summary

    finally:
        ws.close()
        logger.info("WebSocket connection closed.")


def generate_session(session, xrfkey, user_id, user_directory, proxy_server, client_cert, client_key):
    session_id = str(uuid.uuid4())
    session_url = f"{proxy_server}/qps/session?xrfkey={xrfkey}"
    session_headers = {
        "X-Qlik-Xrfkey": xrfkey,
        "Content-Type": "application/json",
    }
    session_payload = {
        "UserDirectory": user_directory,
        "UserId": user_id,
        "Attributes": [],
        "SessionId": session_id,
    }
    resp = session.post(
        session_url,
        json=session_payload,
        headers=session_headers,
        cert=(client_cert, client_key),
        verify=False,
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to create session at %s: %s", session_url, e)
        raise
    logger.info("Session created: %s", session_id)
    return session_id


def delete_session(session, xrfkey, proxy_server, client_cert, client_key, session_id):
    session_url = f"{proxy_server}/qps/session/{session_id}?xrfkey={xrfkey}"
    session_headers = {
        "X-Qlik-Xrfkey": xrfkey,
        "Content-Type": "application/json",
    }
    resp = session.delete(
        session_url,
        headers=session_headers,
        cert=(client_cert, client_key),
        verify=False,
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to delete session %s: %s", session_id, e)
        raise
    logger.info("Deleted session: %s", session_id)


def setup_logger():
    global logger
    log_dir = Path(__file__).resolve().parent / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    log_file = log_dir / f"{timestamp}.log"

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False

    logger.info("Logging initialized. Output file: %s", log_file)
    return logger


def load_config(file_path="config.json"):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("Configuration file '%s' not found.", file_path)
        raise
    except PermissionError:
        logger.error("Permission denied reading '%s'.", file_path)
        raise
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in '%s': %s", file_path, e)
        raise


def build_headers(config, session_id):
    host = config["url"]
    user_directory = config["user_directory"]
    user_id = config["user_id"]
    xrfkey = config["xrfkey"]
    cookie_name = config["cookie_name"]

    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US",
        "Cookie": f"{cookie_name}={session_id}",
        "Host": host,
        "Origin": f"https://{host}",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        ),
        "X-Qlik-Xrfkey": xrfkey,
        "Content-Type": "application/json;charset=UTF-8",
        "X-Qlik-User": f"UserDirectory={user_directory};UserId={user_id}",
    }


def main():
    setup_logger()
    logger.info("Starting Qlik application data extraction")

    try:
        config = load_config()
    except Exception:
        logger.error("Cannot proceed without valid configuration. Exiting.")
        return 1

    required_keys = [
        "user_id",
        "user_directory",
        "proxy_server",
        "client_cert",
        "client_key",
        "url",
        "xrfkey",
        "cookie_name",
        "app_guid",
    ]
    missing = [k for k in required_keys if not config.get(k)]
    if missing:
        logger.error("Missing required configuration keys: %s", ", ".join(missing))
        return 1

    http_session = requests.Session()
    session_id = None

    try:
        session_id = generate_session(
            http_session,
            config["xrfkey"],
            config["user_id"],
            config["user_directory"],
            config["proxy_server"],
            config["client_cert"],
            config["client_key"],
        )
        headers = build_headers(config, session_id)
        summary = extract_qlik_app(config["url"], headers, config["app_guid"], config)
        return 1 if summary.get("failures") else 0
    except Exception as e:
        logger.error("Extraction failed: %s", e)
        return 1
    finally:
        if session_id:
            try:
                delete_session(
                    http_session,
                    config["xrfkey"],
                    config["proxy_server"],
                    config["client_cert"],
                    config["client_key"],
                    session_id,
                )
            except Exception:
                logger.warning("Failed to delete session %s", session_id)


if __name__ == "__main__":
    raise SystemExit(main())
