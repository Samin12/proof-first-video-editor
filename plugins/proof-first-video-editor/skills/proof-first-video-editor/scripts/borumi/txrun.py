#!/usr/bin/env python3
"""txrun.py - the verified-transaction runner every timeline writer uses (library + a small status CLI).

  lock WORK/borumi.lock -> read the COMMITTED hash h0 + a project-wide snapshot -> begin_project_edit ->
  check the tx starts from the committed state -> body(tx, k, pre, checks) stages + verifies ->
    --dry:            abort (nothing changes), return info
    a check failed:   abort, exit 2 ("NOT COMMITTED")
    hash moved:       the committed hash is no longer h0 (the user committed from the Borumi app meanwhile; app
                      commits do NOT take the lock) -> abort, exit 4 ("COLLISION - re-read and redo")
    else:             commit -> project-wide snapshot of the committed state -> post_check(pre_proj, post) re-diff
                      (problems are reported, not undone: never undo the user's own edits) -> append commits.log,
                      save snaps/after_<name>.json
  any exception:      traceback, abort, exit 3

Library:
    import txrun
    def body(tx, k, pre, checks):           # k = {"tx_id": ...}; pre = snapshot.snapshot(k)
        ...stage edits with B.call(..., {"tx_id": tx.id, "timeline_hash": tx.hash(), ...})...
        checks.add("what must hold", ok, "detail")
        return post_check, info              # post_check(pre_proj, post_snapshot) -> (problems, notes) or None
    info = txrun.run(ctx, "step-name", body, args.dry, lambda info: "one-sentence change summary")

  preflight(ctx, name, body): run(..., dry=True) - try a strategy in an ABORTED tx (the insert-quirk preflight).

CLI:
  txrun.py status [common args]     lock owner, committed hash + duration, last commits of this writer
  txrun.py log [-n 20]              the last N commits.log entries

Exit codes of every writer script: 0 ok / dry ok, 2 checks failed (aborted), 3 error (aborted), 4 collision (aborted).
"""
import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwlib as B  # noqa: E402
import snapshot as S  # noqa: E402

al = B.al


def committed_hash(pid):
    return al.summary({"project_id": pid})["timeline_hash"]


def run(ctx, name, body, dry, summary_fn, lock_timeout=None):
    """lock -> committed hash -> tx -> body -> checks -> (dry: abort | collision check, commit) -> post-commit
    project-wide re-diff with the body's callable check -> commits.log. Returns the body's info dict (+ checks_ok,
    commit_id, post_problems)."""
    timeout = lock_timeout if lock_timeout is not None else 900
    with al.Lock(ctx.work, timeout=timeout, label="pfve-writer/" + name):
        h0 = committed_hash(ctx.pid)
        pre_proj = S.snapshot({"project_id": ctx.pid})
        tx = al.Tx(ctx.pid)
        k = tx.key
        checks = al.Checks()
        try:
            pre = S.snapshot(k)
            checks.add("tx starts from the committed state", pre["hash"] == h0 == pre_proj["hash"],
                       "%s %s %s" % (pre["hash"], h0, pre_proj["hash"]))
            post_check, info = body(tx, k, pre, checks)
            info = dict(info or {})
            checks.show()
            info["checks_ok"] = checks.ok
            if dry:
                tx.abort("--dry")
                B.log("DRY (%s): %s" % ("checks pass" if checks.ok else "CHECKS FAILED", summary_fn(info)))
                return info
            if not checks.ok:
                tx.abort("checks failed")
                B.log("NOT COMMITTED")
                sys.exit(2)
            h1 = committed_hash(ctx.pid)
            if h1 != h0:
                tx.abort("collision: project changed during the tx (%s -> %s)" % (h0, h1))
                B.log("COLLISION - the project was edited meanwhile (the Borumi app does not take the lock): re-read and redo")
                sys.exit(4)
            r = tx.commit(summary_fn(info))
            post = S.snapshot({"project_id": ctx.pid})
            bad, notes = post_check(pre_proj, post) if post_check else ([], [])
            for n in notes:
                B.log("  note: " + n)
            with open(ctx.commits_log, "a") as fh:
                fh.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "project_id": ctx.pid, "step": name,
                                     "commit": r.get("commit_id"), "info": info, "post_diff_problems": bad}, default=str) + "\n")
            if bad:
                B.log("POST-COMMIT DIFF PROBLEMS (the user may have edited meanwhile, or the commit merged differently; "
                      "report them, never undo the user's edits):")
                for b_ in bad[:30]:
                    B.log("  " + b_)
            else:
                B.log("post-commit project-wide diff: as predicted")
            info["commit_id"] = r.get("commit_id")
            info["post_problems"] = bad
            S.save(post, ctx.path("snaps", "after_%s.json" % name))
            return info
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            tx.abort("error")
            B.die("aborted: %s" % e, 3)


def preflight(ctx, name, body, lock_timeout=None):
    """run body in an ABORTED tx (dry); returns the body's info."""
    return run(ctx, "preflight_" + name, body, True, lambda i: "preflight", lock_timeout)


def commits(ctx, step_prefix=None):
    """parsed commits.log entries of this writer (oldest first), optionally only steps starting with step_prefix."""
    out = []
    if not os.path.exists(ctx.commits_log):
        return out
    for line in open(ctx.commits_log):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("project_id") not in (None, ctx.pid):
            continue
        if step_prefix and not str(d.get("step", "")).startswith(step_prefix):
            continue
        out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    B.add_common_args(ap, dry=False)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status", help="lock owner, committed hash, last commits")
    lg = sp.add_parser("log", help="last commits.log entries")
    lg.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    ctx = B.context(a)
    if a.cmd == "status":
        lock = os.path.join(ctx.work, "borumi.lock")
        owner = lock + ".owner.json"
        if os.path.isdir(lock):
            B.log("lock HELD: %s" % (open(owner).read().strip() if os.path.exists(owner) else "(no owner file)"))
        else:
            B.log("lock free")
        s = al.summary(ctx.key)
        B.log("project %s: committed hash %s, duration %d ms (%s)" % (ctx.pid, s["timeline_hash"], s["duration_ms"],
                                                                     B.fmt_ts(s["duration_ms"])))
        for d in commits(ctx)[-5:]:
            B.log("  %s %-28s commit %s%s" % (d["t"], d["step"], d.get("commit"),
                                              "  POST-DIFF PROBLEMS %d" % len(d["post_diff_problems"]) if d.get("post_diff_problems") else ""))
        return 0
    for d in commits(ctx)[-a.n:]:
        print(json.dumps({k_: d.get(k_) for k_ in ("t", "step", "commit", "post_diff_problems")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
