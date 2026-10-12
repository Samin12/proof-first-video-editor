#!/usr/bin/env python3
"""Read-only Borumi timeline reader (chunked get_timeline / get_transcript, merged by segment id).

usage:
  borumi_read.py [OUT.json] [--bundle PATH | --project ALIAS] [--config PATH] [--project-id ID]
      prints every layer (id, kind, segment count), the duration and the timeline hash; writes the merged
      timeline to OUT.json when given.

Talks to Borumi through the persistent MCP session of opencutagent-borumi's borumi_mcp.py (FIFO daemon; `start`
never returns, so background it: `nohup python3 <borumi_mcp_dir>/borumi_mcp.py start >/dev/null 2>&1 &`, then wait
for ~/.borumi-agent/sess/ready). The folder comes from the config key borumi_mcp_dir / $BORUMI_MCP_DIR (default
~/.borumi-agent/plugin/scripts).

Only READ tools are ever called (enforced): list_open_projects, get_project_overview, get_scenes,
get_timeline, get_transcript. The project database is never opened in place: db_copy() copies
project.bmproj to a temp dir and checks its integrity first.

Quirks handled here (see references/audio.md):
  * get_timeline is read in 60 s chunks; a segment spanning chunks appears in several replies and is merged
    by id. If the timeline hash changes during the read (someone is editing), the read restarts (12 tries).
  * Borumi shows SHORT ids (shortest unique prefix). resolver() maps them to full ids; a short id that is
    itself a prefix of another short id in use maps to the candidate NOT claimed by the longer one.
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_config  # noqa: E402

READ_TOOLS = {"list_open_projects", "get_project_overview", "get_scenes", "get_timeline", "get_transcript"}
CHUNK_MS = 60000
_P = {}
BUNDLE = None
_mcp = None


def set_project(P):
    """Select the project (a dict from audio_config.resolve) for project_id(), db_copy() and media_file()."""
    global _P, BUNDLE
    _P = dict(P)
    BUNDLE = _P.get("bundle")
    return _P


def _borumi_mcp():
    global _mcp
    if _mcp is None:
        d = _P.get("mcp_dir") or os.path.expanduser(audio_config.DEFAULTS["borumi_mcp_dir"])
        if not os.path.exists(os.path.join(d, "borumi_mcp.py")):
            raise SystemExit(f"borumi_mcp.py not found in {d}: install Samin12/opencutagent-borumi or set "
                             "borumi_mcp_dir / $BORUMI_MCP_DIR")
        sys.path.insert(0, d)
        import borumi_mcp  # noqa: E402
        _mcp = borumi_mcp
    return _mcp


def call(tool, args, timeout=300):
    assert tool in READ_TOOLS, f"refusing non-read tool {tool}"
    r = _borumi_mcp().call(tool, args, timeout)
    if "error" in r and "response" not in r:
        raise RuntimeError(f"{tool}: {r['error']}")
    resp = r.get("response", {})
    if "error" in resp:
        raise RuntimeError(f"{tool}: {resp['error']}")
    res = resp.get("result") or {}
    sc = res.get("structuredContent")
    if sc is None:
        txt = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        try:
            sc = json.loads(txt)
        except Exception:
            raise RuntimeError(f"{tool}: unparsable reply {txt[:400]}")
    if res.get("isError"):
        raise RuntimeError(f"{tool}: {json.dumps(sc)[:600]}")
    return sc


def project_id(name=None):
    """Id of the selected project, verified to be open in Borumi from the SAME bundle path (or, only when
    `name` is passed explicitly, the same project name). If the config/CLI gave a project_id, it must match.
    Matching by path matters: db_copy()/media_file() read the bundle on disk, so a same-named copy that is
    open from another folder must not be accepted silently."""
    d = call("list_open_projects", {})
    for p in d["projects"]:
        same_path = BUNDLE and os.path.realpath(p.get("path", "")) == os.path.realpath(BUNDLE)
        if same_path or (name and p["name"] == name):
            if _P.get("project_id") and p["id"] != _P["project_id"]:
                raise RuntimeError(f"bundle {BUNDLE} is open as project {p['id']}, config says {_P['project_id']}")
            return p["id"]
    raise RuntimeError(f"project {_P.get('name')!r} ({BUNDLE}) is not open in Borumi (open it, enable MCP)")


def overview(pid):
    return call("get_project_overview", {"project_id": pid})


def _chunks(dur, step=CHUNK_MS):
    t = 0
    while t < dur:
        yield t, min(dur, t + step)
        t += step


def read_timeline(pid, fields=("all",)):
    """Returns {duration_ms, timeline_hash, layers:{id:{kind,category,segments:{seg_id:seg}}}}.
    Re-reads if the timeline hash changes during the read (another agent editing)."""
    for attempt in range(12):
        if attempt:
            time.sleep(min(30, 3 * attempt))
        ov = overview(pid)
        dur = ov["project"]["duration_ms"]
        h0 = ov["timeline_hash"]
        layers, hashes, moved = {}, set(), False
        for a, b in _chunks(dur):
            d = call("get_timeline", {"project_id": pid, "range": {"start_ms": a, "end_ms": b},
                                      "detail": "segments", "segment_fields": list(fields)})
            hashes.add(d["timeline_hash"])
            for L in d["layers"]:
                e = layers.setdefault(L["id"], {"kind": L["kind"], "category": L.get("category"), "segments": {}})
                for s in L.get("segments", []):
                    prev = e["segments"].get(s["id"])
                    if prev and (prev["start_ms"], prev["end_ms"]) != (s["start_ms"], s["end_ms"]):
                        moved = True  # timeline edited between chunk reads
                    e["segments"][s["id"]] = s
        ov2 = overview(pid)
        if not moved and len(hashes) == 1 and h0 in hashes and ov2["timeline_hash"] == h0:
            return {"project_id": pid, "duration_ms": dur, "timeline_hash": h0, "layers": layers, "overview": ov}
        print(f"timeline changed during read (attempt {attempt + 1}); re-reading", file=sys.stderr)
    raise RuntimeError("timeline kept changing while reading")


def read_transcript(pid, dur):
    """Sorted unique (start_ms, end_ms, word, layer_id) tuples over the whole project."""
    words = []
    for a, b in _chunks(dur):
        d = call("get_transcript", {"project_id": pid, "range": {"start_ms": a, "end_ms": b}, "mode": "words"})
        for L in d.get("layers") or []:
            for s in L["segments"]:
                for it in s["items"]:
                    words.append((it[0], it[1], it[2], L["id"]))
    return sorted(set(words))


def db_copy(dest_dir=None):
    """Copy the project DB to a temp dir (never opens the live file) and return a sqlite3 connection."""
    import atexit, shutil, sqlite3, tempfile
    d = dest_dir or tempfile.mkdtemp(prefix="borumi-audio-db-")
    if not dest_dir:
        atexit.register(shutil.rmtree, d, True)
    dst = os.path.join(d, "project.bmproj")
    for attempt in range(5):
        shutil.copy2(os.path.join(BUNDLE, "project.bmproj"), dst)
        con = sqlite3.connect(dst)
        if con.execute("pragma integrity_check").fetchone()[0] == "ok":
            return con
        con.close()
        time.sleep(1)
    raise RuntimeError("could not get a consistent copy of project.bmproj")


def resolver(full_ids, short_ids):
    """Map Borumi short ids (shortest unique prefixes) to full ids. A short id that is a prefix of
    another short id in use maps to the candidate NOT claimed by the longer one."""
    full_ids = list(full_ids)
    short_ids = set(short_ids)

    def res(short):
        c = [f for f in full_ids if f.startswith(short)]
        c = [f for f in c if not any(o != short and o.startswith(short) and f.startswith(o) for o in short_ids)]
        if len(c) != 1:
            raise KeyError(f"cannot resolve id {short!r}: {c[:4]}")
        return c[0]
    return res


def media_file(full_id):
    d = os.path.join(BUNDLE, "medias")
    c = [f for f in os.listdir(d) if f.startswith(full_id + ".") and not f.endswith(".cursor")]
    if len(c) != 1:
        raise FileNotFoundError(f"media file for {full_id}: {c}")
    return os.path.join(d, c[0])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", help="write the merged timeline JSON here")
    audio_config.add_args(ap)
    a = ap.parse_args()
    set_project(audio_config.resolve(a))
    pid = project_id()
    tl = read_timeline(pid)
    if a.out:
        json.dump(tl, open(a.out, "w"), indent=1)
    for lid, L in tl["layers"].items():
        print(lid, L["kind"], len(L["segments"]))
    print("duration", tl["duration_ms"], "hash", tl["timeline_hash"])
