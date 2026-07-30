# Qlik App Data Extractor 

**Component:** `QlikAppDataExtractor.py`
**Audience:** Engineers, integrators, and reviewers with working knowledge of Python, HTTP/WebSocket, and RPC APIs
---

## 1. Purpose and Scope

A single-process batch extractor that pulls the **materialised row data** of every table in one Qlik Sense application and persists each table as a JSON document.

### In scope

- Certificate-authenticated Qlik session lifecycle (create/delete)
- Qlik Engine WebSocket JSON-RPC interaction
- Table discovery, paginated row retrieval, per-table serialisation
- Tolerance for schema/payload mismatches introduced by Qlik extensions

### Explicitly out of scope

- Incremental or delta extraction (every run is a full snapshot)
- Cross-app orchestration (one app per invocation)
- Transformation, typing, or loading into a downstream store
- Scheduling and retry-across-runs (delegate to cron, Airflow, or similar)

---

## 2. Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                        Process boundary                       │
│                                                               │
│  config.json ──► main()                                       │
│                       │                                       │
│                       ├─► generate_session()  ─── HTTPS ──┐   │
│                       ├─► build_headers()                 │   │
│                       ├─► extract_qlik_app()              │   │
│                       │     ├─ open_engine_connection() ──┼─┐ │
│                       │     ├─ open_app()                 │ │ │
│                       │     ├─ get_table_metadata()       │ │ │
│                       │     ├─ resolve_tables_to_extract()│ │ │
│                       │     └─ per table:                 │ │ │
│                       │          extract_table_data()     │ │ │
│                       │          parse_row_to_dict()      │ │ │
│                       │          generate_json_file()     │ │ │
│                       └─► delete_session()  ──────────────┘ │ │
└─────────────────────────────────────────────────────────────┼─┘
                                                              │
                       ┌──────────────────────────────────────┴──┐
                       │  Qlik Sense                             │
                       │   • QPS   /qps/session       (HTTPS)    │
                       │   • Engine wss://host/app/{guid} (WS)   │
                       └─────────────────────────────────────────┘
```

Two transports, two trust mechanisms:

| Transport | Endpoint | Auth | Used for |
|-----------|----------|------|----------|
| HTTPS | `{proxy_server}/qps/session` | Client certificate (mTLS) | Session create / delete |
| WSS | `wss://{host}/app/{app_guid}` | Session cookie + `X-Qlik-User` header | All data operations |

The session ID produced over HTTPS is injected as a `Cookie` header on the WebSocket upgrade request. This coupling is the reason session creation must succeed before the Engine connection is attempted.

---

## 3. Authentication Model

```
uuid4() ──► POST /qps/session (mTLS, cert + key)
              │
              └─► session_id
                    │
                    ├─► Cookie: {cookie_name}={session_id}
                    └─► X-Qlik-User: UserDirectory=…;UserId=…
                          │
                          └─► WebSocket upgrade
```

Notable properties:

- The client **proposes** the session ID rather than receiving one; QPS registers the supplied UUID.
- `X-Qlik-User` asserts the identity the Engine applies, so **section access and data reductions are enforced against that user**. Extracted output is therefore identity-scoped, not necessarily the full dataset.
- `verify=False` is set on all HTTPS calls and `ssl.CERT_NONE` on the WebSocket. See [§10 Security posture](#10-security-posture).

---

## 4. Engine Protocol Contract

All Engine traffic is JSON-RPC 2.0 over a single WebSocket.

### Correlation scheme

`send_request()` writes a request and then reads frames in a loop, discarding anything whose `id` does not match. This tolerates the Engine's unsolicited notifications (`OnConnected`, change notifications) interleaved with responses.

Message IDs are allocated deterministically rather than from a counter shared across concerns:

| ID | Call | Handle |
|----|------|--------|
| `1` | `OpenDoc` | `-1` (no object yet) |
| `99` | `GetTablesAndKeys` | app handle |
| `100+` | `GetTableData` (monotonic, one per chunk) | app handle |

The first frame after connect is consumed and discarded by `ws.recv()` in `extract_qlik_app()` — this is the Engine's `OnConnected` greeting, which carries no `id` and would otherwise stall the correlation loop.

### Calls used

| Method | Params | Returns |
|--------|--------|---------|
| `OpenDoc` | `{"qDocName": app_guid}` | `result.qReturn.qHandle` |
| `GetTablesAndKeys` | positional array (window sizes, cell limit, flags) | `result.qtr[]` — per-table `qName`, `qNoOfRows`, `qFields[]` |
| `GetTableData` | `{qOffset, qRows, qSyntheticMode, qTableName}` | `result.qData[]` — rows of `qValue[]` cells |

`qSyntheticMode` is `False`, so Qlik-generated synthetic keys are excluded from output.

---

## 5. Control Flow and Pagination

```
open WS ─► discard greeting ─► OpenDoc ─► GetTablesAndKeys
                                              │
                                   resolve_tables_to_extract()
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        │  for each selected table (sequential)     │
                        │    offset = 0                             │
                        │    while offset < metadata_row_count:     │
                        │      GetTableData(offset, chunk_size)     │
                        │      accumulate rows                      │
                        │      offset += len(page)                  │
                        │      break if page empty or partial       │
                        │    build document ─► write file           │
                        └───────────────────────────────────────────┘
                                              │
                              write _extraction_summary.json ─► close WS
```

The loop has three independent termination conditions, which is deliberate defensiveness against metadata that disagrees with reality:

| Condition | Rationale |
|-----------|-----------|
| `offset >= row_count` | Normal completion against declared row count |
| Empty page | Engine has no more data despite metadata claiming otherwise |
| `len(page) < chunk_size` | Short page signals the tail; avoids one wasted round trip |

`chunk_size` defaults to **1000**. It trades round-trip count against per-response memory and Engine-side cost. Wide tables (many columns) or constrained networks warrant lowering it.

### Table selection precedence

`table_names` (list) → `table_name` (single) → all discovered tables.

Selection semantics differ intentionally between the two explicit modes:

- `table_names`: missing entries produce a **warning** and are skipped — partial success is acceptable for a batch list.
- `table_name`: a missing entry **raises** — a single-target run that cannot hit its target is a hard failure.

---

## 6. Payload Mapping and the Mismatch Problem

The central correctness concern is that Qlik exposes schema and data through two independent calls, and they are not guaranteed to agree:

- `GetTablesAndKeys` → `qFields[]` (the declared columns)
- `GetTableData` → `qValue[]` per row (the actual cells)

Standard tables align. Tables backed by extensions — **Vizlib Writeback** being the observed case — return additional cells (row identifiers, writeback keys, internal state) that never appear in `qFields`.

A naive positional zip indexes past the end of the column list and raises `IndexError: list index out of range` for every row of every affected table.

`parse_row_to_dict()` makes the mapping total rather than partial:

| Cardinality | Behaviour |
|-------------|-----------|
| `len(cells) == len(columns)` | Direct positional mapping |
| `len(cells) < len(columns)` | Missing trailing columns emit `""` |
| `len(cells) > len(columns)` | Surplus cells bound to synthesised `_unmapped_col_N` keys |

The `extra_column_names` list is passed by reference and accumulates across the table's chunks, so names remain stable for every row and can be reflected in the document's `columns` array with a `$unmapped` type tag.

Cell coercion is handled by `cell_to_text()`: `qText`, falling back to `qNum`, falling back to `str()`, with `None` mapping to `""`. **All values are emitted as strings** — no type inference is attempted, which keeps the output lossless with respect to Qlik's own display formatting but pushes typing downstream.

A mismatch logs one `WARNING` per table (guarded by `mismatch_logged`), not per row.

---

## 7. Output Contract

```
output/
├── {TableName}.json          # one per extracted table
└── _extraction_summary.json  # run manifest
```

Table document:

```json
{
  "tableName": "Config_Streams",
  "metadataRowCount": 12,
  "rowCount": 12,
  "columnCount": 9,
  "columns": [
    { "name": "StreamName",      "type": ["$text"] },
    { "name": "_unmapped_col_1", "type": ["$unmapped"] }
  ],
  "rows": [ { "StreamName": "Finance", "_unmapped_col_1": "…" } ]
}
```

`metadataRowCount` and `rowCount` are reported separately by design — divergence is a signal worth alerting on (section access reduction, concurrent reload, or a truncated extraction).

Filenames pass through `safe_filename()`, which strips characters illegal on Windows/NTFS as well as POSIX path separators. **Note the collision risk:** two Qlik tables differing only by an illegal character (`A/B` and `A:B`) sanitise to the same filename and the second silently overwrites the first. Rare, but not detected.

Summary document records per-table `status`/`rowCount`/`columnCount` under `tables`, and error strings under `failures`.

---

## 8. Failure Model

Failure handling is deliberately tiered by blast radius:

| Scope | Handling | Rationale |
|-------|----------|-----------|
| Session creation | Raise, abort run | Nothing downstream can proceed |
| WebSocket connect / `OpenDoc` | Raise, abort run | Same |
| `GetTablesAndKeys` | Raise, abort run | Without the manifest there is no work to do |
| Per-table extraction | Catch, record in `failures`, continue | One bad table should not forfeit 30 good ones |
| Session deletion | Catch, log `WARNING` | Extraction already succeeded; a leaked session is an operational nuisance, not a data failure |

Resource release uses `try/finally` at two levels: the WebSocket in `extract_qlik_app()`, the QPS session in `main()`. Both run regardless of outcome.

Exit codes: `0` when `failures` is empty, `1` otherwise (including config and connection failures). Suitable for direct use as a scheduler success signal.

**Partial-write caveat:** table files are written incrementally as each table completes. A mid-run abort leaves a populated `output/` directory with no summary file. Consumers should treat the presence of `_extraction_summary.json` as the completion marker, not the presence of table files.

---

## 9. Performance Characteristics

| Dimension | Behaviour |
|-----------|-----------|
| Concurrency | Fully sequential — one connection, one table at a time |
| Round trips | `1 + 1 + Σ ceil(rows_t / chunk_size)` over selected tables |
| Connection cost | One TLS handshake and one app open for the entire run |
| Memory | **O(total rows of the largest table)** — all rows buffered before serialisation |
| Dominant cost | Engine-side data assembly and network transfer |

The memory profile is the primary scaling limit. A table of several million wide rows materialises fully as Python dicts (one dict per row, with repeated key strings) before `json.dump` runs. Peak RSS can exceed the eventual file size by a large multiple.

Mitigations if this becomes binding:

1. Stream chunks directly to the file handle as newline-delimited JSON, removing the accumulator.
2. Restrict scope via `table_names` and drive multiple targeted runs.
3. Reduce `chunk_size` — this bounds per-response memory but **not** the accumulator, so it only partially helps.

Parallelism is intentionally absent. The Engine multiplexes concurrent requests on one socket, so it is achievable, but it complicates message-ID allocation and can trip proxy-level throttling. Sequential execution keeps failure attribution unambiguous.

---

## 10. Security Posture

Three items warrant explicit acknowledgement in any review:

1. **TLS verification is disabled** — `verify=False` on HTTPS and `ssl.CERT_NONE` on the WebSocket. Acceptable for internal hosts with self-signed certificates; it does eliminate MITM protection. Prefer pinning a CA bundle where the environment allows.
2. **Credentials sit in plaintext config** — `client_cert` and `client_key` are filesystem paths in `config.json`-style files. Key file permissions are the only control. Do not commit these files.
3. **Output may contain sensitive data** — extracted rows are business data written unencrypted to local disk. Retention and access control on `output/` are the operator's responsibility.

The browser-mimicking `User-Agent` and `Sec-Fetch-*` headers are present because some Qlik proxy configurations reject non-browser clients. They are compatibility shims, not security measures.

---

## 11. Configuration Surface

| Key | Required | Default | Notes |
|-----|----------|---------|-------|
| `user_id`, `user_directory` | yes | — | Asserted identity; governs section access |
| `proxy_server` | yes | — | Full URL including scheme |
| `client_cert`, `client_key` | yes | — | mTLS pair for QPS |
| `url` | yes | — | Hostname only, no scheme |
| `xrfkey` | yes | — | Must be exactly 16 characters |
| `cookie_name` | yes | — | Virtual-proxy specific |
| `app_guid` | yes | — | Extraction target |
| `table_names` | no | — | List; takes precedence over `table_name` |
| `table_name` | no | — | Single table; strict (raises if absent) |
| `chunk_size` | no | `1000` | Rows per `GetTableData` call |
| `skip_empty_tables` | no | `false` | Records `status: skipped` instead of writing a file |
| `output_dir` | no | `output` | Relative to the working directory |

Validation reports **all** missing required keys in one pass rather than failing on the first.

---

## 12. Known Limitations

| Limitation | Impact | Possible direction |
|------------|--------|--------------------|
| Whole-table buffering in memory | Caps practical table size | Stream to NDJSON |
| Single app per invocation | External loop needed for portfolios | Accept `app_guids`, mirroring the metadata extractor |
| No in-run retry on Engine errors | A transient blip fails that table | Wrap `send_request` with bounded backoff |
| Unmapped columns are unnamed | Requires manual correlation | Cross-reference `GetFieldList` or layout metadata |
| All values stringified | Downstream must re-type | Emit `qNum` alongside `qText` when present |
| Filename sanitisation may collide | Silent overwrite (rare) | Suffix with a hash of the original name |
| No integrity check on output | Truncated files not detected | Record row-count checksum in the summary |

---

## 13. Extension Points

The module is import-safe (`if __name__ == "__main__"` guard) and its functions are side-effect-free apart from I/O, so the pieces compose:

| Goal | Approach |
|------|----------|
| Alternative sink (Parquet, S3, database) | Replace `generate_json_file()`; `build_table_json()` output is the stable interface |
| Multi-app runs | Wrap `extract_qlik_app()` in a loop over GUIDs, reusing one QPS session |
| Different row shape | Substitute `parse_row_to_dict()`; the mismatch contract is self-contained |
| Additional Engine calls (layout, script, lineage) | Add methods via `send_request()`, allocating IDs above the data range |
| Metrics/observability | Attach a handler to the `data_extractor` logger, or emit from the summary |

### Recommended pairing

Run `QlikAppMetadataExtractor.py` first as a cheap survey — one REST call returns table names, row counts, and byte sizes. Use its `tableSummary` to populate `table_names` here, so large or irrelevant tables are never fetched. See `QlikAppMetadataExtractor_HLD.md` §11.

---

