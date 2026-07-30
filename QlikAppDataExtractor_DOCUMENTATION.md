# Qlik Sense Data Extractor — End-to-End Documentation

This guide explains how the script `QlikAppDataExtractor.py` works from start to finish. 

---

## Table of Contents

1. [What Does This Script Do?](#1-what-does-this-script-do)
2. [How It Fits Together (Big Picture)](#2-how-it-fits-together-big-picture)
3. [Before You Run It](#3-before-you-run-it)
4. [Configuration File Explained](#4-configuration-file-explained)
5. [How to Run the Script](#5-how-to-run-the-script)
6. [Step-by-Step: What Happens When You Run It](#6-step-by-step-what-happens-when-you-run-it)
7. [Function-by-Function Breakdown](#7-function-by-function-breakdown)
8. [Output Files Explained](#8-output-files-explained)
9. [Choosing Which Tables to Extract](#9-choosing-which-tables-to-extract)
10. [Logs and Troubleshooting](#10-logs-and-troubleshooting)
11. [Glossary](#11-glossary)
12. [Line-by-Line Explanation](#12-line-by-line-explanation)
13. [Vizlib Writeback and Column Mismatch Handling](#13-vizlib-writeback-and-column-mismatch-handling)

---

## 1. What Does This Script Do?

In simple terms, this script:

1. **Logs in** to a Qlik Sense server using certificates and a user identity.
2. **Opens** a specific Qlik application (identified by its GUID).
3. **Discovers** all data tables inside that application.
4. **Downloads** the rows from each table (in batches, not all at once).
5. **Saves** each table as a separate JSON file on your computer.
6. **Handles** column mismatches automatically (common in Vizlib Writeback apps).
7. **Logs out** and closes the connection cleanly.

Think of it like a robot that opens a Qlik app, copies every spreadsheet-like table inside it, saves them as files, and then closes the app.

---

## 2. How It Fits Together (Big Picture)

The script talks to two different parts of Qlik Sense:

| Part | What it is | How the script connects |
|------|------------|-------------------------|
| **QPS (Qlik Proxy Service)** | Handles user sessions (login/logout) | Regular HTTPS requests (`requests` library) |
| **Qlik Engine** | Holds the actual app data | WebSocket connection (live, two-way channel) |

```
┌─────────────────────────────────────────────────────────────────┐
│                        YOUR COMPUTER                            │
│                                                                 │
│   config.json  ──►  QlikAppDataExtractor.py                     │
│                              │                                  │
└──────────────────────────────┼──────────────────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   Qlik Sense Server  │
                    │                      │
                    │  1. Create session   │  ◄── HTTPS + certificates
                    │  2. Open app         │  ◄── WebSocket
                    │  3. List tables      │  ◄── WebSocket
                    │  4. Download rows    │  ◄── WebSocket
                    │  5. Delete session   │  ◄── HTTPS
                    └──────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   output/ folder     │
                    │   - Table1.json      │
                    │   - Table2.json      │
                    │   - _extraction_     │
                    │     summary.json     │
                    └──────────────────────┘
```

---

## 3. Before You Run It

### 3.1 Required Software

- **Python 3.7 or newer** installed on your machine.
- Three Python packages (install with pip):

```bash
pip install requests websocket-client urllib3
```

| Package | Purpose |
|---------|---------|
| `requests` | Sends HTTP requests to create/delete Qlik sessions |
| `websocket-client` | Maintains the live connection to the Qlik Engine |
| `urllib3` | Used internally by `requests`; warnings are silenced in the script |

### 3.2 Required Files

Place these in the **same folder** as the script (or adjust paths in config):

| File | Purpose |
|------|---------|
| `QlikAppDataExtractor.py` | The main script |
| `config.json` | Settings (server URL, app ID, certificates, etc.) |
| Client certificate (`.pem`) | Proves your identity to Qlik |
| Client key (`.pem` or similar) | Private key paired with the certificate |

### 3.3 Folders Created Automatically

When you run the script, it creates:

| Folder | Contents |
|--------|----------|
| `output/` | One JSON file per extracted table, plus a summary file |
| `log/` | Timestamped log files for each run |

---

## 4. Configuration File Explained

The script reads settings from `config.json`. Here is a full example with comments (comments are **not** allowed in real JSON — remove them when saving):

```json
{
  "user_id": "jdoe",
  "user_directory": "COMPANY",
  "proxy_server": "https://qlik-server.company.com:4243",
  "client_cert": "/path/to/client.pem",
  "client_key": "/path/to/client_key.pem",
  "url": "qlik-server.company.com",
  "xrfkey": "1234567890123456",
  "cookie_name": "X-Qlik-Session-company",
  "app_guid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

### Required Settings

| Key | What it means | Example |
|-----|---------------|---------|
| `user_id` | Your Qlik username | `"jdoe"` |
| `user_directory` | The directory/ realm your user belongs to | `"COMPANY"` |
| `proxy_server` | Full URL of the Qlik Proxy (QPS) | `"https://qlik.company.com:4243"` |
| `client_cert` | Path to your client certificate file | `"/certs/client.pem"` |
| `client_key` | Path to your client private key file | `"/certs/client_key.pem"` |
| `url` | Qlik server hostname (no `https://`) | `"qlik.company.com"` |
| `xrfkey` | A 16-character security key used in Qlik API calls | `"1234567890123456"` |
| `cookie_name` | Name of the session cookie Qlik uses | `"X-Qlik-Session-company"` |
| `app_guid` | Unique ID of the Qlik app you want to extract | `"a1b2c3d4-..."` |

### Optional Settings

| Key | Default | What it does |
|-----|---------|--------------|
| `table_name` | *(none)* | Extract only **one** specific table |
| `table_names` | *(none)* | Extract a **list** of specific tables |
| `chunk_size` | `1000` | How many rows to fetch per API call |
| `skip_empty_tables` | `false` | If `true`, skip tables with 0 rows |
| `output_dir` | `"output"` | Folder where JSON files are saved |

> **Tip:** If you omit both `table_name` and `table_names`, the script extracts **every table** in the app automatically.

---

## 5. How to Run the Script

1. Open a terminal (Command Prompt, PowerShell, or Terminal on Mac/Linux).
2. Navigate to the folder containing the script:

```bash
cd /path/to/your/script/folder
```

3. Run the script:

```bash
python QlikAppDataExtractor.py
```

Or on some systems:

```bash
python3 QlikAppDataExtractor.py
```

4. Watch the terminal for progress messages. When finished, check the `output/` folder for results.

**Exit codes:**
- `0` = success (all tables extracted)
- `1` = something failed (check logs)

---

## 6. Step-by-Step: What Happens When You Run It

Here is the exact order of operations when you execute the script:

### Phase 1 — Setup

```
main()
  └── setup_logger()          → Creates log file in log/ folder
  └── load_config()           → Reads config.json
  └── Validates required keys → Stops if anything is missing
```

### Phase 2 — Authentication

```
generate_session()
  └── Creates a unique session ID (UUID)
  └── Sends POST request to Qlik Proxy with your certificate
  └── Qlik returns a valid session
```

The session is like a temporary login ticket. Without it, the Engine will reject your connection.

### Phase 3 — Build Request Headers

```
build_headers()
  └── Combines session ID, user info, and security keys
  └── Produces HTTP headers the WebSocket connection needs
```

### Phase 4 — Extract Data (the main work)

```
extract_qlik_app()
  │
  ├── open_engine_connection()   → Opens WebSocket to Qlik Engine
  ├── open_app()                 → Opens the Qlik app by GUID
  ├── get_table_metadata()       → Lists all tables and their columns
  ├── resolve_tables_to_extract()→ Decides which tables to pull
  │
  └── For each table:
        ├── extract_table_data() → Downloads rows in chunks
        ├── parse_row_to_dict()  → Maps cells to columns (handles mismatches)
        └── generate_json_file() → Saves to output/TableName.json
  │
  └── Writes _extraction_summary.json
  └── Closes WebSocket
```

### Phase 5 — Cleanup

```
delete_session()
  └── Sends DELETE request to Qlik Proxy
  └── Frees the session on the server
```

Even if extraction fails partway through, the script still tries to delete the session in the `finally` block — this prevents orphaned sessions on the server.

---

## 7. Function-by-Function Breakdown

Below is every function in the script, explained in plain language.

---

### `build_ws_url(host, app_guid)`

**Purpose:** Builds the WebSocket address for the Qlik app.

**Input:** Server hostname and app GUID.

**Output:** A URL like `wss://qlik-server.company.com/app/a1b2c3d4-...`

**Why:** WebSockets need a specific URL format to connect to a Qlik app.

---

### `send_request(ws, method, params, handle, msg_id)`

**Purpose:** Sends a command to Qlik Engine and waits for the reply.

**How it works:**
1. Packages your request as JSON (JSON-RPC 2.0 format — a standard way APIs talk).
2. Sends it over the WebSocket.
3. Keeps reading messages until it gets one with a matching `id`.

**Example:** Calling `send_request(ws, "OpenDoc", {"qDocName": "..."})` tells Qlik to open an app.

**Important parameters:**
- `method` — the Qlik API action (e.g. `"GetTableData"`, `"OpenDoc"`)
- `handle` — an internal reference number Qlik assigns to opened objects
- `msg_id` — a unique number so the script knows which response belongs to which request

---

### `open_engine_connection(host, headers, app_guid)`

**Purpose:** Opens the WebSocket connection to Qlik Engine.

**What can go wrong:** Network issues, wrong hostname, invalid session — all raise an error and stop the script.

---

### `open_app(ws, app_guid)`

**Purpose:** Opens the Qlik application inside the Engine.

**Returns:** An `app_handle` — a number Qlik uses internally to refer to this open app. Every subsequent API call (list tables, get data) needs this handle.

---

### `get_table_metadata(ws, app_handle, msg_id)`

**Purpose:** Asks Qlik for a list of all tables in the app.

**Returns:** A dictionary like:

```python
{
  "Sales": {
    "rowCount": 50000,
    "columns": [ {"qName": "Date", ...}, {"qName": "Amount", ...} ]
  },
  "Customers": {
    "rowCount": 1200,
    "columns": [ ... ]
  }
}
```

This is the "table of contents" for the app — the script uses it to know what to download.

---

### `resolve_tables_to_extract(table_metadata, config)`

**Purpose:** Decides which tables to actually extract based on your config.

**Logic:**

| Config present | Result |
|----------------|--------|
| `table_names: ["A", "B"]` | Extract only A and B |
| `table_name: "A"` | Extract only A |
| Neither set | Extract **all** tables |

If you ask for a table that does not exist, the script logs a warning and skips it.

---

### `extract_table_data(ws, app_handle, table_name, table_meta, chunk_size, msg_id)`

**Purpose:** Downloads all rows from one table.

**Why chunks?** Large tables can have millions of rows. Downloading everything in one request would be slow or fail. Instead, the script requests rows in batches (default: 1000 at a time):

```
Request rows 0–999      → append to list
Request rows 1000–1999  → append to list
Request rows 2000–2999  → append to list
... until no more rows
```

**Returns:** The complete table as a JSON structure, plus the next message ID to use.

**Column mismatch handling:** Some apps (especially **Vizlib Writeback**) return a different number of cells per row than the column list from `GetTablesAndKeys`. The script handles this safely — see [Section 13](#13-vizlib-writeback-and-column-mismatch-handling).

---

### `cell_to_text(cell)`

**Purpose:** Converts a single Qlik cell object into plain text for the JSON output.

**How it works:**
- If the cell is `None`, returns `""`
- If the cell is a dictionary, returns `qText` first, or falls back to `qNum`
- Otherwise converts the value to a string

**Why:** Qlik cells can store values as text or numbers. This function picks the best display value.

---

### `parse_row_to_dict(column_names, q_value, extra_column_names)`

**Purpose:** Maps one row of Qlik cell data to a Python dictionary (column name → value).

**How it handles mismatches:**

| Situation | What the script does |
|-----------|----------------------|
| Row has **fewer** cells than columns | Missing columns get `""` |
| Row has **more** cells than columns | Extra cells saved as `_unmapped_col_1`, `_unmapped_col_2`, etc. |
| Row has no `qValue` | Treated as all empty values |

**Why:** Vizlib Writeback and similar extensions often add hidden system columns to row data that are not listed in table metadata. The old approach crashed with `list index out of range`; this function prevents that.

---

### `build_table_json(table_name, table_meta, all_rows, extra_column_names=None)`

**Purpose:** Wraps the raw rows into a structured JSON object with metadata (column names, row counts, etc.).

**Extra columns:** If unmapped columns were detected during extraction, they are included in:
- `columnCount` (total count includes them)
- `columns` (listed with type `"$unmapped"`)
- Each row in `rows` (under `_unmapped_col_N` keys)

### `generate_json_file(table_name, table_json, output_dir)`

**Purpose:** Saves the table data to a file.

**File naming:** Table names are sanitized (unsafe characters like `/` or `:` become `_`) so the filename is valid on all operating systems.

**Example output path:** `output/Sales.json`

---

### `safe_filename(name)`

**Purpose:** Converts a table name into a safe filename by replacing invalid characters with underscores.

---

### `extract_qlik_app(host, headers, app_guid, config)`

**Purpose:** The orchestrator — runs the entire extraction process for one app.

**Key behaviors:**
- Opens **one** WebSocket connection for all tables (efficient).
- If one table fails, others still continue.
- Writes a summary file at the end.
- Always closes the WebSocket, even on error.

---

### `generate_session(...)` and `delete_session(...)`

**Purpose:** Create and destroy a Qlik user session via the Proxy API.

**Why certificates?** Qlik Sense enterprise setups often use mutual TLS — both the server and client prove their identity with certificates.

**Why delete?** Leaving sessions open consumes server resources and license slots.

---

### `setup_logger()`

**Purpose:** Configures logging to both the terminal and a timestamped file in `log/`.

**Example log file:** `log/20260730-060000Z.log`

---

### `load_config(file_path)`

**Purpose:** Reads and parses `config.json`.

**Error handling:** Clear messages if the file is missing, unreadable, or contains invalid JSON.

---

### `build_headers(config, session_id)`

**Purpose:** Builds the HTTP headers required for the WebSocket connection, including the session cookie and user identity.

---

### `main()`

**Purpose:** The entry point — ties everything together in the correct order.

**Flow:** Setup → Load config → Create session → Extract → Delete session → Exit with status code.

---

## 8. Output Files Explained

### Per-Table JSON Files

Each table is saved as `output/{TableName}.json`:

```json
{
  "tableName": "Sales",
  "metadataRowCount": 50000,
  "rowCount": 50000,
  "columnCount": 5,
  "columns": [
    { "name": "Date", "type": ["$date", "$numeric"] },
    { "name": "Amount", "type": ["$numeric"] }
  ],
  "rows": [
    { "Date": "2024-01-01", "Amount": "1500" },
    { "Date": "2024-01-02", "Amount": "2300" }
  ]
}
```

### Tables with Unmapped Columns (Vizlib Writeback)

When Qlik returns more cells than metadata columns, the JSON includes extra fields:

```json
{
  "tableName": "Config_Streams",
  "metadataRowCount": 12,
  "rowCount": 12,
  "columnCount": 9,
  "columns": [
    { "name": "StreamName", "type": ["$text"] },
    { "name": "StreamId", "type": ["$text"] },
    { "name": "_unmapped_col_1", "type": ["$unmapped"] },
    { "name": "_unmapped_col_2", "type": ["$unmapped"] }
  ],
  "rows": [
    {
      "StreamName": "Finance",
      "StreamId": "abc-123",
      "_unmapped_col_1": "system-value-1",
      "_unmapped_col_2": "system-value-2"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `tableName` | Name of the table in Qlik |
| `metadataRowCount` | Row count reported by Qlik metadata |
| `rowCount` | Actual number of rows downloaded |
| `columnCount` | Number of columns (includes unmapped columns if any) |
| `columns` | List of column names and types |
| `rows` | The actual data — each item is one row as a dictionary |
| `_unmapped_col_N` | Extra cell data with no matching column in Qlik metadata (Vizlib Writeback) |
| `"$unmapped"` | Type tag for auto-detected columns not listed in metadata |

> **Note:** Cell values are stored as text (`qText` from Qlik, or `qNum` as fallback). Numbers and dates appear as strings in the JSON.

### Summary File

`output/_extraction_summary.json` gives you a quick overview of the entire run:

```json
{
  "appGuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "extractedAt": "2026-07-30T06:00:00+00:00",
  "tables": {
    "Sales": { "status": "success", "rowCount": 50000, "columnCount": 5 },
    "Customers": { "status": "success", "rowCount": 1200, "columnCount": 8 }
  },
  "failures": {}
}
```

If any table failed, you will see entries under `"failures"` with the error message.

---

## 9. Choosing Which Tables to Extract

### Extract All Tables (default)

Omit `table_name` and `table_names` from config:

```json
{
  "app_guid": "your-app-guid",
  "... other required fields ..."
}
```

### Extract One Table

```json
{
  "table_name": "Sales",
  "... other required fields ..."
}
```

### Extract Specific Tables

```json
{
  "table_names": ["Sales", "Customers", "Products"],
  "... other required fields ..."
}
```

### Skip Empty Tables

```json
{
  "skip_empty_tables": true,
  "... other required fields ..."
}
```

### Adjust Batch Size

If you have very wide tables (many columns) or network timeouts, try a smaller chunk:

```json
{
  "chunk_size": 500
}
```

For fast networks and simple tables, you can increase it:

```json
{
  "chunk_size": 2000
}
```

---

## 10. Logs and Troubleshooting

### Where to Look

| Location | What it tells you |
|----------|-------------------|
| Terminal output | Real-time progress |
| `log/YYYYMMDD-HHMMSSZ.log` | Full history of the run |
| `output/_extraction_summary.json` | Which tables succeeded or failed |

### Common Problems

| Problem | Likely Cause | What to Check |
|---------|--------------|---------------|
| `Configuration file not found` | Missing `config.json` | File is in the same folder as the script |
| `Missing required configuration keys` | A required field is empty or missing | Review Section 4 of this guide |
| `Failed to create session` | Wrong certificates, user, or proxy URL | Verify cert paths, user_id, proxy_server |
| `Engine connection failed` | Session expired, wrong host, network issue | Check `url`, cookie_name, network access |
| `Failed to open document` | Wrong `app_guid` or no access to the app | Confirm GUID and user permissions |
| `Table 'X' not found` | Typo in `table_name` / `table_names` | Check exact table name in Qlik (case-sensitive) |
| `list index out of range` in failures | *(Fixed in current version)* Old script crashed on column count mismatch | Update to latest script; see [Section 13](#13-vizlib-writeback-and-column-mismatch-handling) |
| Column count mismatch warning in logs | Vizlib Writeback or extension tables with extra system columns | Expected — data is still extracted; check `_unmapped_col_N` fields in output |
| Partial extraction (some tables in `failures`) | One table had an issue | Read the error in summary file and logs |

### Tips

- Run the script from the folder where `config.json` lives, or provide the full path.
- Certificate paths can be absolute (`/full/path/to/cert.pem`) or relative to where you run the script.
- Table names in Qlik are **case-sensitive** — `"sales"` and `"Sales"` are different.
- Large apps with many tables can take a long time. Watch the log for progress (`Fetched X rows` messages).

---

## 11. Glossary

| Term | Simple Explanation |
|------|--------------------|
| **App GUID** | A unique ID that identifies one Qlik application (like a serial number) |
| **WebSocket** | A persistent, two-way connection — unlike regular HTTP which is request-response only |
| **JSON-RPC** | A format for sending commands over WebSocket/HTTP: "call this method with these parameters" |
| **Handle** | An internal number Qlik assigns to an open object (like an app) so you can refer to it in later calls |
| **Chunk / Batch** | A group of rows downloaded in one API request |
| **QPS (Qlik Proxy Service)** | The front door to Qlik — handles authentication and routing |
| **Qlik Engine** | The backend that stores and serves app data |
| **Session** | A temporary authenticated connection tied to your user identity |
| **XRF Key** | A 16-character key Qlik requires on API calls for security |
| **Client Certificate** | A digital ID file that proves who you are to the server |
| **Metadata** | Information *about* the data (table names, column names, row counts) — not the data itself |
| **qValue** | The array of cell objects in one row returned by `GetTableData` |
| **qFields** | The list of column definitions returned by `GetTablesAndKeys` |
| **Vizlib Writeback** | A Qlik extension that lets users edit/write data back to tables — often adds extra system columns |
| **Unmapped column** | An extra cell in row data with no matching entry in metadata — saved as `_unmapped_col_N` |

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────────────────┐
│  QUICK START                                                │
├─────────────────────────────────────────────────────────────┤
│  1. pip install requests websocket-client urllib3           │
│  2. Create config.json with your Qlik details               │
│  3. Place client cert + key where config points to them     │
│  4. python QlikAppDataExtractor.py                          │
│  5. Check output/ for JSON files                            │
│  6. Check log/ if something went wrong                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 12. Line-by-Line Explanation

This section walks through **every line** of `QlikAppDataExtractor.py`. Read it top to bottom while looking at the script side-by-side.

**How to read this section:**
- **Line number** — matches the line in the `.py` file
- **Code** — the actual line (shortened if very long)
- **Explanation** — what that line does in plain English

---

### Section A — Imports and Global Setup (Lines 1–18)

| Line | Code | Explanation |
|------|------|-------------|
| 1 | `import ssl` | Loads Python's SSL/TLS library. Used later to configure secure WebSocket connections. |
| 2 | `import json` | Loads the JSON library. Used to read config files, send API messages, and write output files. |
| 3 | `import logging` | Loads Python's built-in logging system. Used to write messages to the terminal and log files. |
| 4 | `import re` | Loads "regular expressions" — a tool for finding/replacing text patterns. Used to clean up table names for filenames. |
| 5 | `import uuid` | Loads the UUID library. Used to generate a unique session ID when logging into Qlik. |
| 6 | `from datetime import datetime, timezone` | Imports tools for working with dates and times. Used for timestamps in logs and summary files. |
| 7 | `from pathlib import Path` | Imports `Path`, a modern way to work with file and folder paths (better than plain strings). |
| 8 | *(blank)* | Blank line for readability — separates import groups. |
| 9 | `import requests` | Loads the `requests` library — the standard way to make HTTP/HTTPS web requests in Python. |
| 10 | `import urllib3` | Loads `urllib3`, which `requests` uses internally. Imported so we can disable its warning messages. |
| 11 | `import websocket` | Loads the WebSocket client library. Used to connect to the Qlik Engine. |
| 12 | *(blank)* | Blank line. |
| 13 | `urllib3.disable_warnings(...)` | Hides SSL warning messages. The script connects with `verify=False`, which normally triggers noisy warnings. |
| 14 | *(blank)* | Blank line. |
| 15 | `LOGGER_NAME = "data_extractor"` | Defines a constant (a fixed value) for the logger's name. Using a constant avoids typos. |
| 16 | `DEFAULT_CHUNK_SIZE = 1000` | Defines the default number of rows to download per API request. 1000 is a good balance of speed and reliability. |
| 17 | `logger = logging.getLogger(LOGGER_NAME)` | Creates a logger object named `"data_extractor"`. All log messages go through this object. |
| 18 | `logger.addHandler(logging.NullHandler())` | Adds a "do nothing" handler initially. This prevents error messages before logging is fully set up in `setup_logger()`. |
| 19 | *(blank)* | Blank line separating setup from functions. |

---

### Section B — `build_ws_url` (Lines 21–22)

| Line | Code | Explanation |
|------|------|-------------|
| 21 | `def build_ws_url(host, app_guid):` | Defines a function that takes the server hostname and app GUID as inputs. |
| 22 | `return f"wss://{host}/app/{app_guid}"` | Builds and returns the WebSocket URL. `wss://` means secure WebSocket. Example: `wss://qlik.company.com/app/abc-123`. |

---

### Section C — `send_request` (Lines 25–37)

This is the core communication function — every Qlik Engine API call goes through here.

| Line | Code | Explanation |
|------|------|-------------|
| 25 | `def send_request(ws, method, params, handle=-1, msg_id=1):` | Defines the function. `ws` = WebSocket connection. `method` = API action name. `params` = arguments. `handle` = Qlik object reference (default -1 = no object). `msg_id` = unique request number. |
| 26 | `payload = {` | Starts building the message to send to Qlik. |
| 27 | `"jsonrpc": "2.0",` | Specifies the JSON-RPC protocol version — a standard format Qlik Engine expects. |
| 28 | `"id": msg_id,` | Sets the request ID so we can match the response later. |
| 29 | `"method": method,` | Sets which Qlik API method to call (e.g. `"OpenDoc"`, `"GetTableData"`). |
| 30 | `"handle": handle,` | Sets which Qlik object to act on (e.g. the opened app). |
| 31 | `"params": params,` | Sets the parameters/arguments for the method. |
| 32 | `}` | Closes the payload dictionary. |
| 33 | `ws.send(json.dumps(payload))` | Converts the payload to a JSON string and sends it over the WebSocket. |
| 34 | `while True:` | Starts an infinite loop — keeps reading until we get the response we asked for. |
| 35 | `response = json.loads(ws.recv())` | Waits for a message from Qlik, then parses it from JSON text into a Python dictionary. |
| 36 | `if response.get("id") == msg_id:` | Checks if this response matches our request ID (ignores unrelated messages). |
| 37 | `return response` | Returns the matching response to whoever called this function. |

---

### Section D — `open_engine_connection` (Lines 40–53)

| Line | Code | Explanation |
|------|------|-------------|
| 40 | `def open_engine_connection(host, headers, app_guid):` | Defines the function to open a WebSocket to Qlik Engine. |
| 41 | `logger.info("Connecting to Qlik Sense Engine...")` | Writes an informational message to the log. |
| 42 | `ws_url = build_ws_url(host, app_guid)` | Calls `build_ws_url` to create the connection URL. |
| 43 | `try:` | Starts a "try" block — if anything below fails, jump to the `except` block. |
| 44 | `ws = websocket.create_connection(` | Starts creating the WebSocket connection. |
| 45 | `ws_url,` | The URL to connect to. |
| 46 | `sslopt={"cert_reqs": ssl.CERT_NONE},` | SSL option: don't verify the server's certificate (common in internal/QA environments). |
| 47 | `header=headers,` | Passes HTTP headers (session cookie, user info, etc.) needed for authentication. |
| 48 | `)` | Closes the `create_connection` call. |
| 49 | `except Exception as e:` | If connection fails for any reason, catch the error and store it in `e`. |
| 50 | `logger.error("Engine connection failed: %s", e)` | Log the error message. `%s` is replaced by the actual error text. |
| 51 | `raise` | Re-raise the error so the script stops — we can't continue without a connection. |
| 52 | `logger.info("Engine connection established.")` | Log success (only reached if connection worked). |
| 53 | `return ws` | Return the open WebSocket object for use by other functions. |

---

### Section E — `open_app` (Lines 56–63)

| Line | Code | Explanation |
|------|------|-------------|
| 56 | `def open_app(ws, app_guid):` | Defines function to open a Qlik app inside the Engine. |
| 57 | `logger.info("Opening Qlik application...")` | Log that we're opening the app. |
| 58 | `open_doc_res = send_request(ws, "OpenDoc", {"qDocName": app_guid}, handle=-1, msg_id=1)` | Sends the `"OpenDoc"` command to Qlik with the app GUID. `handle=-1` because no app is open yet. `msg_id=1` is the first request ID. |
| 59 | `if "error" in open_doc_res:` | Check if Qlik returned an error instead of success. |
| 60 | `raise RuntimeError(f"Failed to open document: {open_doc_res['error']}")` | Stop the script with a clear error message including Qlik's error details. |
| 61 | `app_handle = open_doc_res["result"]["qReturn"]["qHandle"]` | Extract the app handle — an internal number Qlik assigns. All future calls about this app need this number. |
| 62 | `logger.info("Application opened successfully. App handle: %s", app_handle)` | Log the handle so you can see it in logs for debugging. |
| 63 | `return app_handle` | Return the handle to the caller. |

---

### Section F — `get_table_metadata` (Lines 66–83)

| Line | Code | Explanation |
|------|------|-------------|
| 66 | `def get_table_metadata(ws, app_handle, msg_id=99):` | Defines function to list all tables in the app. Default `msg_id=99`. |
| 67–73 | `tables_res = send_request(...)` | Calls Qlik's `"GetTablesAndKeys"` method. The long parameter list tells Qlik how much metadata to return (grid size, key options, etc.). |
| 74 | `if "error" in tables_res:` | Check for errors. |
| 75 | `raise RuntimeError(...)` | Stop if listing tables failed. |
| 76 | *(blank)* | Blank line. |
| 77 | `table_metadata = {}` | Create an empty dictionary to store table info. |
| 78 | `for table in tables_res["result"]["qtr"]:` | Loop through each table in Qlik's response. `"qtr"` = table records. |
| 79 | `table_metadata[table["qName"]] = {` | Use the table name as the dictionary key. |
| 80 | `"rowCount": table["qNoOfRows"],` | Store how many rows the table has. |
| 81 | `"columns": table["qFields"],` | Store the list of columns/fields in the table. |
| 82 | `}` | Close the inner dictionary. |
| 83 | `return table_metadata` | Return the complete metadata dictionary. |

---

### Section G — `safe_filename` (Lines 86–87)

| Line | Code | Explanation |
|------|------|-------------|
| 86 | `def safe_filename(name):` | Defines function to make a table name safe for use as a filename. |
| 87 | `return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "unnamed_table"` | Replace any illegal filename characters with `_`. `.strip()` removes leading/trailing spaces. If the result is empty, use `"unnamed_table"`. |

---

### Section H — `generate_json_file` (Lines 90–97)

| Line | Code | Explanation |
|------|------|-------------|
| 90 | `def generate_json_file(table_name, table_json, output_dir=None):` | Defines function to save table data to disk. `output_dir=None` means "use default if not provided". |
| 91 | `output_dir = output_dir or Path("output")` | If no output folder was passed, use `"output"`. |
| 92 | `output_dir.mkdir(exist_ok=True)` | Create the output folder if it doesn't exist. `exist_ok=True` means don't error if it already exists. |
| 93 | `file_path = output_dir / f"{safe_filename(table_name)}.json"` | Build the full file path, e.g. `output/Sales.json`. |
| 94 | `with open(file_path, "w", encoding="utf-8") as f:` | Open the file for writing. `with` automatically closes it when done. `utf-8` supports all characters. |
| 95 | `json.dump(table_json, f, indent=4, ensure_ascii=False)` | Write the Python dictionary as formatted JSON. `indent=4` makes it readable. `ensure_ascii=False` keeps non-English characters as-is. |
| 96 | `logger.info("Saved %s", file_path)` | Log which file was saved. |
| 97 | `return file_path` | Return the path so callers know where the file went. |

---

### Section I — `cell_to_text` (Lines 100–106)

| Line | Code | Explanation |
|------|------|-------------|
| 100 | `def cell_to_text(cell):` | Defines function to convert one Qlik cell to plain text. |
| 101 | `"""Convert a Qlik cell object to display text."""` | Docstring describing the function. |
| 102 | `if cell is None:` | If the cell is missing/null... |
| 103 | `return ""` | ...return an empty string. |
| 104 | `if isinstance(cell, dict):` | If the cell is a dictionary (normal Qlik format)... |
| 105 | `return cell.get("qText", cell.get("qNum", ""))` | ...return text value, or numeric value as fallback. |
| 106 | `return str(cell)` | For any other type, convert to string. |

---

### Section J — `parse_row_to_dict` (Lines 109–129)

This function safely maps row cells to column names — critical for Vizlib Writeback tables.

| Line | Code | Explanation |
|------|------|-------------|
| 109 | `def parse_row_to_dict(column_names, q_value, extra_column_names):` | Defines the row parser. Takes column list, cell array, and a shared list for tracking extra columns. |
| 110–116 | `"""Map Qlik row cells..."""` | Docstring explaining Vizlib Writeback mismatch handling. |
| 117 | `cells = q_value if q_value is not None else []` | Safely get the cell array; use empty list if missing. |
| 118 | `row_dict = {}` | Start an empty dictionary for this row. |
| 119 | *(blank)* | Blank line. |
| 120 | `for i, col_name in enumerate(column_names):` | Loop through every column name from metadata. |
| 121 | `row_dict[col_name] = cell_to_text(cells[i]) if i < len(cells) else ""` | If a cell exists at this position, convert it; otherwise use `""`. |
| 122 | *(blank)* | Blank line. |
| 123 | `for i in range(len(column_names), len(cells)):` | Loop through any **extra** cells beyond metadata column count. |
| 124 | `extra_idx = i - len(column_names)` | Calculate index within the extra columns (0-based). |
| 125 | `while len(extra_column_names) <= extra_idx:` | Ensure we have enough names in the shared extra-column list. |
| 126 | `extra_column_names.append(f"_unmapped_col_{len(extra_column_names) + 1}")` | Add a new auto-generated column name like `_unmapped_col_1`. |
| 127 | `row_dict[extra_column_names[extra_idx]] = cell_to_text(cells[i])` | Store the extra cell under the unmapped column name. |
| 128 | *(blank)* | Blank line. |
| 129 | `return row_dict` | Return the completed row dictionary. |

---

### Section K — `build_table_json` (Lines 132–149)

| Line | Code | Explanation |
|------|------|-------------|
| 132 | `def build_table_json(table_name, table_meta, all_rows, extra_column_names=None):` | Defines function to assemble final JSON. Optional `extra_column_names` for unmapped columns. |
| 133 | `column_names = [c["qName"] for c in table_meta["columns"]]` | Build list of metadata column names. |
| 134 | `all_column_names = column_names + (extra_column_names or [])` | Combine metadata columns with any unmapped columns. |
| 135 | `return {` | Start building the return dictionary. |
| 136 | `"tableName": table_name,` | Include the table name. |
| 137 | `"metadataRowCount": table_meta["rowCount"],` | Row count from Qlik metadata. |
| 138 | `"rowCount": len(all_rows),` | Actual downloaded row count. |
| 139 | `"columnCount": len(all_column_names),` | Total columns including unmapped ones. |
| 140–143 | `"columns": [...]` | Build column list from metadata fields. |
| 144–147 | `+ [{ "name": name, "type": ["$unmapped"] } ...]` | Append unmapped columns with `$unmapped` type tag. |
| 148 | `"rows": all_rows,` | Include all row data. |
| 149 | `}` | Close and return the dictionary. |

---

### Section L — `extract_table_data` (Lines 152–214)

This is the heaviest function — it downloads all rows from one table in batches.

| Line | Code | Explanation |
|------|------|-------------|
| 152 | `def extract_table_data(ws, app_handle, table_name, table_meta, chunk_size, msg_id):` | Defines the row-download function. |
| 153 | `row_count = table_meta["rowCount"]` | Get total rows expected for this table. |
| 154 | `if row_count == 0:` | If the table is empty... |
| 155 | `logger.info("Skipping empty table: '%s'", table_name)` | ...log that we're skipping it. |
| 156 | `return build_table_json(table_name, table_meta, []), msg_id` | ...return empty table structure (no API calls). |
| 157 | *(blank)* | Blank line. |
| 158 | `column_names = [c["qName"] for c in table_meta["columns"]]` | Build list of column names from metadata. |
| 159 | `extra_column_names = []` | Track unmapped columns discovered during extraction. |
| 160 | `all_rows = []` | Empty list to accumulate downloaded rows. |
| 161 | `offset = 0` | Start at row 0. |
| 162 | `mismatch_logged = False` | Flag to log column mismatch warning only once per table. |
| 163 | *(blank)* | Blank line. |
| 164 | `logger.info("Extracting table '%s' (%s rows, %s columns)", ...)` | Log table name, row count, column count. |
| 165 | *(blank)* | Blank line. |
| 166 | `while offset < row_count:` | Keep downloading until all rows fetched. |
| 167 | `params = {` | Build `GetTableData` parameters. |
| 168 | `"qOffset": offset,` | Start from this row. |
| 169 | `"qRows": chunk_size,` | Fetch up to this many rows. |
| 170 | `"qSyntheticMode": False,` | Exclude synthetic rows. |
| 171 | `"qTableName": table_name,` | Target table name. |
| 172 | `}` | Close params. |
| 173 | `data_res = send_request(ws, "GetTableData", params, ...)` | Request batch from Qlik. |
| 174 | `msg_id += 1` | Increment message ID for next request. |
| 175 | *(blank)* | Blank line. |
| 176 | `if "error" in data_res:` | Check for API errors. |
| 177 | `raise RuntimeError(...)` | Stop this table if Qlik returned an error. |
| 178 | *(blank)* | Blank line. |
| 179 | `data_pages = data_res.get("result", {}).get("qData", [])` | Safely extract row data array. |
| 180 | `if not data_pages:` | If no rows returned... |
| 181 | `break` | ...exit the download loop. |
| 182 | *(blank)* | Blank line. |
| 183 | `for row in data_pages:` | Loop through each row in the batch. |
| 184 | `q_value = row.get("qValue", [])` | Safely get cell array (empty list if missing). |
| 185 | `if not mismatch_logged and len(q_value) != len(column_names):` | If cell count differs from metadata (first time only)... |
| 186–192 | `logger.warning("Table '%s': row cell count...")` | ...log a warning about mismatch (common with Vizlib Writeback). |
| 193 | `mismatch_logged = True` | Don't log the same warning for every row. |
| 194 | *(blank)* | Blank line. |
| 195 | `all_rows.append(parse_row_to_dict(column_names, q_value, extra_column_names))` | Parse row safely and add to results. |
| 196 | *(blank)* | Blank line. |
| 197 | `fetched = len(data_pages)` | Count rows in this batch. |
| 198 | `offset += fetched` | Advance offset. |
| 199 | `logger.info("  '%s': fetched %s rows (%s/%s)", ...)` | Log batch progress. |
| 200 | *(blank)* | Blank line. |
| 201 | `if fetched < chunk_size:` | If partial batch received... |
| 202 | `break` | ...we're at the end of the table. |
| 203 | *(blank)* | Blank line. |
| 204 | `if extra_column_names:` | If unmapped columns were found... |
| 205–210 | `logger.info("Table '%s': captured %s unmapped column(s)...")` | ...log which unmapped columns were captured. |
| 211 | *(blank)* | Blank line. |
| 212 | `table_json = build_table_json(table_name, table_meta, all_rows, extra_column_names)` | Build final JSON including unmapped columns. |
| 213 | `logger.info("Finished '%s': %s rows extracted.", ...)` | Log completion. |
| 214 | `return table_json, msg_id` | Return table data and updated message ID. |

---

### Section M — `resolve_tables_to_extract` (Lines 217–239)

| Line | Code | Explanation |
|------|------|-------------|
| 217 | `def resolve_tables_to_extract(table_metadata, config):` | Defines function to decide which tables to extract. |
| 218 | `"""Return ordered list..."""` | Docstring — brief description of the function. |
| 219 | `all_tables = list(table_metadata.keys())` | Get a list of every table name found in the app. |
| 220 | `if not all_tables:` | If the app has no tables... |
| 221 | `return []` | ...return an empty list (nothing to extract). |
| 222 | *(blank)* | Blank line. |
| 223 | `explicit_list = config.get("table_names")` | Read optional `table_names` from config. |
| 224 | `single_table = config.get("table_name")` | Read optional `table_name` from config. |
| 225 | *(blank)* | Blank line. |
| 226 | `if explicit_list:` | If user specified a list of tables... |
| 227 | `if isinstance(explicit_list, str):` | If they provided a single string instead of a list... |
| 228 | `explicit_list = [explicit_list]` | ...wrap it in a list. |
| 229 | `missing = [t for t in explicit_list if t not in table_metadata]` | Find configured tables that don't exist in the app. |
| 230 | `for name in missing:` | Loop through missing names. |
| 231 | `logger.warning("Configured table not found in app: '%s'", name)` | Warn about each missing table. |
| 232 | `return [t for t in explicit_list if t in table_metadata]` | Return only tables that exist. |
| 233 | *(blank)* | Blank line. |
| 234 | `if single_table:` | If user specified a single table... |
| 235 | `if single_table not in table_metadata:` | If that table doesn't exist... |
| 236 | `raise KeyError(...)` | ...stop with an error. |
| 237 | `return [single_table]` | Return a one-item list. |
| 238 | *(blank)* | Blank line. |
| 239 | `return all_tables` | Default: return every table in the app. |

---

### Section N — `extract_qlik_app` (Lines 242–305)

This is the main orchestrator — it runs the full extraction for one app.

| Line | Code | Explanation |
|------|------|-------------|
| 242 | `def extract_qlik_app(host, headers, app_guid, config):` | Defines the top-level extraction function. |
| 243 | `chunk_size = config.get("chunk_size", DEFAULT_CHUNK_SIZE)` | Read chunk size from config, or use 1000. |
| 244 | `skip_empty = config.get("skip_empty_tables", False)` | Read whether to skip empty tables. |
| 245 | `output_dir = Path(config.get("output_dir", "output"))` | Read output folder from config. |
| 246 | *(blank)* | Blank line. |
| 247 | `ws = open_engine_connection(host, headers, app_guid)` | Open the WebSocket connection. |
| 248–253 | `summary = { ... }` | Build summary dict with appGuid, extractedAt, tables, failures. |
| 254 | *(blank)* | Blank line. |
| 255 | `try:` | Start try block — ensures WebSocket closes on error. |
| 256 | `ws.recv()` | Discard Qlik's initial WebSocket handshake message. |
| 257 | `app_handle = open_app(ws, app_guid)` | Open the app and get its handle. |
| 258 | *(blank)* | Blank line. |
| 259 | `table_metadata = get_table_metadata(ws, app_handle, msg_id=99)` | List all tables and columns. |
| 260 | `tables_to_extract = resolve_tables_to_extract(table_metadata, config)` | Decide which tables to download. |
| 261–268 | Logging loop | Log table counts and each table name with row count. |
| 269 | *(blank)* | Blank line. |
| 270 | `msg_id = 100` | Start message IDs at 100. |
| 271 | `for table_name in tables_to_extract:` | Loop through each table. |
| 272 | `meta = table_metadata[table_name]` | Get table metadata. |
| 273–276 | Skip empty check | Optionally skip empty tables and record in summary. |
| 277 | *(blank)* | Blank line. |
| 278 | `try:` | Try extracting this table (failures don't stop others). |
| 279–281 | `extract_table_data(...)` | Download all rows. |
| 282 | `generate_json_file(...)` | Save to JSON file. |
| 283–287 | Summary success record | Record row/column counts in summary. |
| 288 | `except Exception as e:` | Catch per-table failures. |
| 289 | `logger.error(...)` | Log the error. |
| 290 | `summary["failures"][table_name] = str(e)` | Record failure in summary. |
| 291 | *(blank)* | Blank line. |
| 292–296 | Write summary file | Save `_extraction_summary.json`. |
| 297 | *(blank)* | Blank line. |
| 298–300 | Final logging | Log succeeded/failed counts and return summary. |
| 301 | `return summary` | Return summary to caller. |
| 302 | *(blank)* | Blank line. |
| 303 | `finally:` | Always runs — closes WebSocket. |
| 304 | `ws.close()` | Close connection. |
| 305 | `logger.info("WebSocket connection closed.")` | Log closure. |

---

### Section O — `generate_session` (Lines 308–335)

| Line | Code | Explanation |
|------|------|-------------|
| 308 | `def generate_session(session, xrfkey, user_id, user_directory, proxy_server, client_cert, client_key):` | Defines function to log into Qlik via the Proxy API. |
| 309 | `session_id = str(uuid.uuid4())` | Generate a random unique session ID. |
| 310 | `session_url = f"{proxy_server}/qps/session?xrfkey={xrfkey}"` | Build session creation URL. |
| 311–314 | `session_headers = {...}` | Headers with XRF key and content type. |
| 315–320 | `session_payload = {...}` | Body with user directory, user ID, and session ID. |
| 321–328 | `resp = session.post(...)` | POST request with certificates to create session. |
| 329 | `try:` | Check HTTP response status. |
| 330 | `resp.raise_for_status()` | Raise on HTTP 4xx/5xx errors. |
| 331 | `except requests.RequestException as e:` | Catch HTTP/network errors. |
| 332 | `logger.error(...)` | Log failure. |
| 333 | `raise` | Stop script — can't proceed without session. |
| 334 | `logger.info("Session created: %s", session_id)` | Log success. |
| 335 | `return session_id` | Return session ID. |

---

### Section P — `delete_session` (Lines 338–356)

| Line | Code | Explanation |
|------|------|-------------|
| 338 | `def delete_session(session, xrfkey, proxy_server, client_cert, client_key, session_id):` | Defines function to log out and free the session. |
| 339 | `session_url = f"{proxy_server}/qps/session/{session_id}?xrfkey={xrfkey}"` | Build URL for the session to delete. |
| 340–343 | `session_headers = {...}` | Same headers as session creation. |
| 344–350 | `resp = session.delete(...)` | HTTP DELETE with certificates. |
| 351 | `try:` | Verify success. |
| 352 | `resp.raise_for_status()` | Raise on HTTP failure. |
| 353 | `except requests.RequestException as e:` | Catch errors. |
| 354 | `logger.error(...)` | Log failure. |
| 355 | `raise` | Re-raise to caller. |
| 356 | `logger.info("Deleted session: %s", session_id)` | Log successful cleanup. |

---

### Section Q — `setup_logger` (Lines 359–382)

| Line | Code | Explanation |
|------|------|-------------|
| 359 | `def setup_logger():` | Defines function to configure logging. |
| 360 | `global logger` | Modify the global logger from line 17. |
| 361 | `log_dir = Path(__file__).resolve().parent / "log"` | Path to `log/` folder next to the script. |
| 362 | `log_dir.mkdir(parents=True, exist_ok=True)` | Create log folder if needed. |
| 363 | `timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")` | Timestamp for log filename. |
| 364 | `log_file = log_dir / f"{timestamp}.log"` | Full path to this run's log file. |
| 365 | *(blank)* | Blank line. |
| 366 | `formatter = logging.Formatter(...)` | Define log message format. |
| 367 | *(blank)* | Blank line. |
| 368–369 | File handler setup | Write logs to file. |
| 370 | *(blank)* | Blank line. |
| 371–372 | Stream handler setup | Write logs to terminal. |
| 373 | *(blank)* | Blank line. |
| 374–379 | Logger configuration | Set level, clear old handlers, add new ones. |
| 380 | *(blank)* | Blank line. |
| 381 | `logger.info("Logging initialized. Output file: %s", log_file)` | Log where logs are written. |
| 382 | `return logger` | Return configured logger. |

---

### Section R — `load_config` (Lines 385–397)

| Line | Code | Explanation |
|------|------|-------------|
| 385 | `def load_config(file_path="config.json"):` | Read config file (default: `config.json`). |
| 386 | `try:` | Start try block. |
| 387–388 | `with open(...) / return json.load(f)` | Open and parse JSON config. |
| 389–390 | `except FileNotFoundError:` | Handle missing file. |
| 391–392 | `except PermissionError:` | Handle permission denied. |
| 393–394 | `except json.JSONDecodeError:` | Handle invalid JSON. |

---

### Section S — `build_headers` (Lines 400–425)

| Line | Code | Explanation |
|------|------|-------------|
| 400 | `def build_headers(config, session_id):` | Build HTTP headers for WebSocket connection. |
| 401–405 | Read config values | Extract host, user, xrfkey, cookie name. |
| 406 | *(blank)* | Blank line. |
| 407–425 | `return { ... }` | Return full headers dict including Cookie, X-Qlik-User, X-Qlik-Xrfkey. |

---

### Section T — `main` (Lines 428–485)

This is the entry point — the function that runs when you execute the script.

| Line | Code | Explanation |
|------|------|-------------|
| 428 | `def main():` | Entry point function. |
| 429 | `setup_logger()` | Configure logging. |
| 430 | `logger.info("Starting Qlik application data extraction")` | Log start message. |
| 431–436 | Load config | Read config or exit with code 1. |
| 437–452 | Validate required keys | Check all mandatory config fields present. |
| 453–455 | `http_session` / `session_id = None` | Prepare HTTP session and session ID variable. |
| 456 | `try:` | Main extraction block. |
| 457–466 | `generate_session(...)` | Log into Qlik. |
| 467 | `headers = build_headers(config, session_id)` | Build WebSocket headers. |
| 468 | `summary = extract_qlik_app(...)` | Run full extraction. |
| 469 | `return 1 if summary.get("failures") else 0` | Exit 1 if any table failed, else 0. |
| 470–472 | `except Exception` | Handle catastrophic failures. |
| 473 | `finally:` | Always attempt session cleanup. |
| 474–485 | `delete_session(...)` | Log out (with warning if deletion fails). |

---

### Section U — Script Entry Point (Lines 488–489)

| Line | Code | Explanation |
|------|------|-------------|
| 488 | `if __name__ == "__main__":` | True only when running the script directly. |
| 489 | `raise SystemExit(main())` | Run `main()` and exit with its return code (0 or 1). |

---

### Visual Flow Summary

Reading the line-by-line sections in order follows this path:

```
Lines 488–489  →  Script starts
Lines 428–452  →  Setup logging, load config, validate
Lines 457–466  →  Log into Qlik (generate_session)
Line 467       →  Build headers
Lines 242–305  →  Extract all tables (extract_qlik_app)
  Lines 247–257  →  Connect WebSocket, open app
  Lines 259–260  →  Discover tables
  Lines 271–290  →  Download each table (uses parse_row_to_dict)
  Lines 292–300  →  Write summary
Lines 474–485  →  Log out (delete_session)
Line 489       →  Exit
```

---

## 13. Vizlib Writeback and Column Mismatch Handling

This section explains a common issue when extracting apps that use **Vizlib Writeback** or similar Qlik extensions, and how the script handles it.

### The Problem

Qlik provides table structure through two API calls:

| API Call | What it returns |
|----------|-----------------|
| `GetTablesAndKeys` | Column list (`qFields`) — what columns the table *should* have |
| `GetTableData` | Row data (`qValue`) — the actual cell values per row |

In standard Qlik tables, these always match: 7 columns in metadata = 7 cells per row.

In **Vizlib Writeback** apps, extension tables often return **more cells** than metadata reports. These extra cells are typically system fields (row IDs, writeback keys, timestamps) that Vizlib adds internally.

### What Used to Happen (Old Script)

The old row parsing code assumed perfect alignment:

```python
column_names[i]: cell.get("qText", "")
for i, cell in enumerate(row["qValue"])
```

When row 1 had 9 cells but metadata listed 7 columns, accessing `column_names[7]` or `column_names[8]` caused:

```
list index out of range
```

This is exactly what you saw in your extraction summary — `Admin` worked (7 columns = 7 cells), but 22 Vizlib tables failed.

### How the Current Script Fixes It

The script now uses two dedicated functions:

**1. `cell_to_text(cell)`** — Safely converts any Qlik cell to text (handles `qText`, `qNum`, or missing values).

**2. `parse_row_to_dict(column_names, q_value, extra_column_names)`** — Maps cells to columns with three rules:

```
Metadata columns:  [Col_A, Col_B, Col_C]     (3 columns)
Row cells:         [val_A, val_B, val_C, val_X, val_Y]  (5 cells)

Result:
{
  "Col_A": "val_A",
  "Col_B": "val_B",
  "Col_C": "val_C",
  "_unmapped_col_1": "val_X",
  "_unmapped_col_2": "val_Y"
}
```

### What You'll See in Logs

When a mismatch is detected, you'll see a **warning** (not an error):

```
WARNING - Table 'Config_Streams': row cell count (9) differs from metadata column count (7).
Extra/missing cells will be mapped safely (common with Vizlib Writeback).
```

After extraction completes, if unmapped columns were found:

```
INFO - Table 'Config_Streams': captured 2 unmapped column(s): _unmapped_col_1, _unmapped_col_2
```

### What to Do With Unmapped Columns

| Option | Action |
|--------|--------|
| **Use the data as-is** | The values are preserved in `_unmapped_col_N` fields — often sufficient for backup/migration |
| **Identify real column names** | Open the app in Qlik and compare with the JSON to figure out what each unmapped field represents |
| **Ignore them** | If you only need the named metadata columns, filter out keys starting with `_unmapped_col_` |

### Re-Running Your Failed Extraction

After updating to the current script version, re-run:

```bash
python QlikAppDataExtractor.py
```

Your `_extraction_summary.json` should show all tables under `"tables"` with empty `"failures": {}`.

---

*Documentation version: matches `QlikAppDataExtractor.py` as of July 2026 (includes Vizlib Writeback column mismatch handling).*
