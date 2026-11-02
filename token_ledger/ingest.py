"""Incremental import: sources -> classify -> sqlite."""
import time

from . import classify, prices, sources, store


def run(cfg, quiet=False, force=False, reclassify=False):
    t0 = time.time()
    conn = store.connect(cfg)
    store.refresh_days(conn, cfg)
    price_table = prices.load(cfg.price_file)
    tags = classify.load_tags(cfg.tags_path)
    finder = classify.RulesFinder()
    before = conn.execute("SELECT count(*) FROM calls").fetchone()[0]
    seen = {} if (force or reclassify) else {r[0]: (r[1], r[2]) for r in conn.execute("SELECT path, size, mtime FROM ingested_files")}
    units = bad = 0
    for src in sources.enabled(cfg):
        for u in src.scan(cfg, seen):
            units += 1
            bad += u.get("bad_lines", 0)
            classify.classify_unit(u["calls"], u["project"], u["title"], tags, finder)
            for c in u["calls"]:
                store.upsert_call(conn, cfg, price_table, c, reclassify)
            tss = [c["ts"] for c in u["calls"] if c["ts"]]
            if u["calls"]:
                conn.execute("INSERT OR REPLACE INTO sessions(source, machine, config_dir, session_id, project, title,"
                             " first_ts, last_ts) VALUES(?,?,?,?,?,?,?,?)",
                             (src.NAME, cfg.machine, u["config_dir"], u["session_id"], u["project"],
                              u.get("title_any"), min(tss, default=None), max(tss, default=None)))
            for p, size, mtime in u["files"]:
                conn.execute("INSERT OR REPLACE INTO ingested_files(path, size, mtime, lines_read) VALUES(?,?,?,0)",
                             (p, size, mtime))
            conn.commit()
    store.resolve_accounts(conn, cfg, force=True)
    after = conn.execute("SELECT count(*) FROM calls").fetchone()[0]
    conn.close()
    msg = (f"ingest: {after - before} new calls ({after} total), {units} sessions read, "
           f"{bad} unparsable lines, {time.time() - t0:.1f}s")
    store.log(cfg, msg)
    if not quiet:
        print(msg)
    return after - before
