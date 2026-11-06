#!/usr/bin/env python3
"""crdkit — vendor upstream CRDs into a Helm chart, verifiably.

The chart contract, in one sentence: the templates are the upstream
CustomResourceDefinitions, field for field, plus exactly one documented
annotation per CRD (`helm.sh/resource-policy: keep`, so a `helm uninstall`
cannot take every Gateway/HTTPRoute in the cluster with it). Nothing else is
added, dropped or reordered, and `crdkit verify` proves that on every run by
re-downloading the pinned upstream asset.

Verbs:
  generate [--chart NAME] [--tag TAG] [--bump]   write charts/<name>/ from sources/charts.yaml
  verify   [--chart NAME]                        re-download upstream and prove the templates match
  check-upstreams [--json]                       list charts whose upstream has a newer tag
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources" / "charts.yaml"
KEEP = ("helm.sh/resource-policy", "keep")
UA = {"User-Agent": "crdkit (+https://github.com/ychan-alt/gatekeeper-crds)"}
TIMEOUT = 90


def log(*a) -> None:
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- fetch / parse

def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def fetch_json(url: str):
    return json.loads(fetch(url).decode())


def docs_of(text: str):
    return [d for d in yaml.safe_load_all(text) if d]


def dump(doc) -> str:
    """Deterministic, unwrapped YAML with an explicit document start."""
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False,
                          width=1_000_000, allow_unicode=True, explicit_start=True)


def ref(doc) -> str:
    return f"{doc['kind']}/{doc['metadata']['name']}"


# ---------------------------------------------------------------- sources

def load_sources() -> dict:
    return yaml.safe_load(SOURCES.read_text())["charts"]


def save_sources(charts: dict) -> None:
    SOURCES.write_text(yaml.safe_dump({"charts": charts}, sort_keys=False, width=120))


def release_tags(up: dict) -> list:
    pat = re.compile(up["tagPattern"])
    tags = []
    for rel in fetch_json(up["releasesApi"] + "?per_page=30"):
        tag = rel.get("tag_name", "")
        if pat.match(tag) and not rel.get("draft"):
            tags.append(tag)
    return tags


def newest_tag(up: dict):
    tags = release_tags(up)
    if not tags:
        return None
    return sorted(tags, key=semver_key)[-1]


def semver_key(tag: str):
    nums = re.findall(r"\d+", tag)
    return tuple(int(n) for n in nums[:3]) + (0 if "-" in tag else 1,)


def asset_of(spec: dict):
    return list(spec["assets"].values())[0]


def fetch_asset(spec: dict, tag: str):
    """Download the declared asset: (raw bytes, url, kept docs, dropped refs)."""
    asset = asset_of(spec)
    url = asset["url"].format(tag=tag)
    raw = fetch(url)
    wanted = set(asset["kinds"])
    kept, dropped = [], []
    for doc in docs_of(raw.decode()):
        if doc.get("kind") in wanted:
            kept.append(doc)
        else:
            dropped.append(ref(doc))
    log(f"  fetched {url} ({len(raw)} bytes, sha256 {hashlib.sha256(raw).hexdigest()[:12]})")
    return raw, url, kept, dropped


def upstream_docs(spec: dict, tag: str):
    """Everything the chart should contain, plus what was dropped by design."""
    _, _, kept, dropped = fetch_asset(spec, tag)
    return kept, dropped


# ---------------------------------------------------------------- emit

def emit_crds(docs: list) -> str:
    out = []
    for doc in docs:
        doc = json.loads(json.dumps(doc))          # deep copy, no shared state
        ann = doc["metadata"].setdefault("annotations", {})
        ann[KEEP[0]] = KEEP[1]
        out.append(dump(doc))
    return "".join(out)


def chart_yaml(spec: dict, name: str, tag: str, url: str, sha: str) -> str:
    owner = spec.get("owner", "ychan-alt")
    up = spec["upstream"]
    meta = {
        "apiVersion": "v2",
        "name": name,
        "description": spec["description"],
        "type": "application",
        "version": tag.lstrip("v"),
        "appVersion": tag,
        "kubeVersion": spec["kubeVersion"],
        "home": up["homepage"],
        "icon": up["icon"],
        "sources": [up["repo"], f"https://github.com/{owner}/gatekeeper-crds"],
        "maintainers": [{"name": owner, "url": f"https://github.com/{owner}"}],
        "annotations": {
            "crdkit.io/upstream-repo": up["repo"],
            "crdkit.io/upstream-tag": tag,
            "crdkit.io/upstream-asset": url,
            "crdkit.io/upstream-sha256": sha,
        },
    }
    return yaml.safe_dump(meta, sort_keys=False, width=120)


def chart_readme(spec: dict, name: str, tag: str, url: str, sha: str, dropped: list) -> str:
    up = spec["upstream"]
    return f"""# {name}

CustomResourceDefinitions vendored from [{up['repo']}]({up['repo']}) — tag
[`{tag}`]({up['repo']}/releases/tag/{tag}), asset `{url}` (sha256 `{sha}`),
licence {up['license']}.

```sh
helm upgrade --install {name} oci://ghcr.io/ychan-alt/charts/{name} --version {tag.lstrip('v')} \\
  -n {name} --create-namespace --take-ownership --force-conflicts
```

`--take-ownership` and `--force-conflicts` are needed only when the CRDs already
existed under another field manager (a previous `kubectl apply`); see
[the migration note](../README.md#adopting-crds-that-already-exist).

## What this chart guarantees

- every `CustomResourceDefinition` is the upstream one, field for field;
- **one** addition: `helm.sh/resource-policy: keep` on each CRD, so `helm uninstall`
  leaves the CRDs — and every Gateway/HTTPRoute that uses them — in place;
- nothing else: no RBAC, no webhook, no admission policy, no hooks, no images.

`python3 tools/crdkit.py verify` re-downloads the pinned asset and fails on any
difference. Dropped by design (not CustomResourceDefinition):
{chr(10).join('- `' + d + '`' for d in dropped) or '- nothing'}

## Updating

`python3 tools/crdkit.py generate --bump && python3 tools/crdkit.py verify`, then
merge — the scheduled workflow opens that PR on its own.
"""


def notice(spec: dict, name: str, tag: str, url: str, sha: str) -> str:
    up = spec["upstream"]
    return f"""{name}
{'-' * len(name)}

The CustomResourceDefinitions in templates/ are the work of the upstream project
{up['repo']}, licensed under {up['license']}. They are redistributed here
unmodified except for the `helm.sh/resource-policy: keep` annotation.

  upstream tag   : {tag}
  source asset   : {url}
  asset sha256   : {sha}
  upstream licence: {up['license']} (see {up['repo']}/blob/{tag}/LICENSE)

The chart scaffolding around them (Chart.yaml, values.yaml, sources/, tools/,
workflows) is Apache-2.0, see ../LICENSE.
"""


# ---------------------------------------------------------------- verbs

def generate(charts: dict, only: str | None, tag_override: str | None, bump: bool,
             out: str | None = None) -> int:
    """Write charts/<name>/ from the pinned asset.

    `out` writes the chart somewhere other than the repo — that is how an older
    version is minted as a rollback target without moving the pin, and without
    the repo tree ever describing a version nobody runs.
    """
    changed = []
    moved_pin = False
    for name, spec in charts.items():
        if only and name != only:
            continue
        tag = tag_override or spec["upstream"]["current"]
        if bump:
            newest = newest_tag(spec["upstream"])
            if newest and semver_key(newest) > semver_key(tag):
                log(f"{name}: bumping {tag} -> {newest}")
                tag = newest
            else:
                log(f"{name}: already at the newest upstream tag ({tag})")
        url = asset_of(spec)["url"].format(tag=tag)
        raw = fetch(url)
        sha = hashlib.sha256(raw).hexdigest()
        wanted = set(asset_of(spec)["kinds"])
        kept, dropped = [], []
        for doc in docs_of(raw.decode()):
            if doc.get("kind") in wanted:
                kept.append(doc)
            else:
                dropped.append(ref(doc))
        if not kept:
            log(f"{name}: upstream yielded no CustomResourceDefinition — aborting")
            return 2

        dest = Path(out).resolve() / "charts" / name if out else ROOT / "charts" / name
        (dest / "templates").mkdir(parents=True, exist_ok=True)
        (dest / "Chart.yaml").write_text(chart_yaml(spec, name, tag, url, sha))
        (dest / "values.yaml").write_text(
            "# This chart deliberately has no values: the CRDs are vendored verbatim\n"
            "# and `helm.sh/resource-policy: keep` is baked in. See README.md.\n")
        (dest / "templates" / "crds.yaml").write_text(emit_crds(kept))
        (dest / "README.md").write_text(chart_readme(spec, name, tag, url, sha, sorted(dropped)))
        (dest / "NOTICE").write_text(notice(spec, name, tag, url, sha))

        if not out and spec["upstream"]["current"] != tag:
            spec["upstream"]["current"] = tag
            moved_pin = True
        changed.append(f"{name} {tag} ({len(kept)} CRDs, {len(raw)} bytes)")
        log(f"{name}: wrote {len(kept)} CRDs at {tag} into {dest}")
    if changed:
        if moved_pin:
            save_sources(charts)
        print("\n".join(changed))
    return 0


def strip_keep(doc):
    doc = json.loads(json.dumps(doc))
    ann = doc.get("metadata", {}).get("annotations", {})
    if ann.get(KEEP[0]) == KEEP[1]:
        del ann[KEEP[0]]
    return doc


def compare(up: dict, got: dict, label: str) -> int:
    """Field-for-field comparison of upstream vs chart, modulo the keep annotation."""
    failures = 0
    for r in sorted(set(up) - set(got)):
        log(f"  FAIL {label}: missing {r}")
        failures += 1
    for r in sorted(set(got) - set(up)):
        log(f"  FAIL {label}: unexpected {r}")
        failures += 1
    for r in sorted(set(up) & set(got)):
        want, got_doc = strip_keep(up[r]), strip_keep(got[r])
        if want != got_doc:
            log(f"  FAIL {label}: {r} differs from upstream")
            failures += 1
            for path, a, b in diff_paths(want, got_doc):
                log(f"       {path}: upstream={a!r} chart={b!r}")
    return failures


def render_check(charts: dict, only: str | None, out: str | None = None) -> int:
    """End-to-end: what `helm template` actually emits must equal upstream too."""
    helm = shutil.which("helm")
    if not helm:
        log("helm not on PATH — render check skipped")
        return 0
    root = Path(out).resolve() if out else ROOT
    failures = 0
    for name, spec in charts.items():
        if only and name != only:
            continue
        chart_path = root / "charts" / name / "Chart.yaml"
        if not chart_path.exists():
            log(f"  FAIL no Chart.yaml at {chart_path}")
            failures += 1
            continue
        # Same rule as verify: the chart names its own upstream tag, so a minted
        # (older) chart is rendered against its own asset, not against the pin.
        tag = str(yaml.safe_load(chart_path.read_text()).get("appVersion")
                  or spec["upstream"]["current"])
        log(f"{name}: rendering with {helm} (against {tag})")
        proc = subprocess.run([helm, "template", "render-check", str(root / "charts" / name)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            log(f"  FAIL helm template failed:\n{proc.stderr}")
            failures += 1
            continue
        up = {ref(d): d for d in upstream_docs(spec, tag)[0]}
        got = {ref(d): d for d in docs_of(proc.stdout)}
        failures += compare(up, got, "rendered")
        if not failures:
            log(f"  OK rendered output equals upstream ({len(up)} CRDs)")
    print("render: PASS" if not failures else f"render: {failures} FAILURE(S)")
    return 1 if failures else 0


def verify(charts: dict, only: str | None, out: str | None = None) -> int:
    root = Path(out).resolve() if out else ROOT
    failures = 0
    for name, spec in charts.items():
        if only and name != only:
            continue
        chart_path = root / "charts" / name / "Chart.yaml"
        if not chart_path.exists():
            log(f"  FAIL no Chart.yaml at {chart_path}")
            failures += 1
            continue
        chart = yaml.safe_load(chart_path.read_text())
        # The chart's own appVersion names the upstream tag it claims to be built
        # from — that is what we verify against, so a minted (older) chart is
        # checked against its own asset rather than against the repo's pin.
        tag = str(chart.get("appVersion") or spec["upstream"]["current"])
        if not out and tag != spec["upstream"]["current"]:
            log(f"  FAIL Chart.yaml appVersion {tag} != pinned {spec['upstream']['current']} "
                f"(the tree must describe the pinned version)")
            failures += 1
        log(f"{name}: verifying against {tag}")
        up_docs, up_dropped = upstream_docs(spec, tag)
        up = {ref(d): d for d in up_docs}
        tpl = root / "charts" / name / "templates" / "crds.yaml"
        if not tpl.exists():
            log(f"  FAIL no template at {tpl}")
            failures += 1
            continue
        got = {ref(d): d for d in docs_of(tpl.read_text())}

        failures += compare(up, got, "template")

        if str(chart.get("version")) != tag.lstrip("v"):
            log(f"  FAIL Chart.yaml version {chart.get('version')} != {tag.lstrip('v')}")
            failures += 1
        dropped_note = sorted(up_dropped)
        if dropped_note:
            log(f"  note: dropped by design: {', '.join(dropped_note)}")
        if not failures:
            log(f"  OK {len(up)} CRDs identical to upstream (modulo {KEEP[0]})")
    print("verify: PASS" if not failures else f"verify: {failures} FAILURE(S)")
    return 1 if failures else 0


def diff_paths(a, b, path="$"):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append((f"{path}.{k}", "<absent>", b[k]))
            elif k not in b:
                out.append((f"{path}.{k}", a[k], "<absent>"))
            else:
                out += diff_paths(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path, f"len {len(a)}", f"len {len(b)}"))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff_paths(x, y, f"{path}[{i}]")
    elif a != b:
        out.append((path, a, b))
    return out[:10]


def check_upstreams(charts: dict, as_json: bool) -> int:
    rows = []
    for name, spec in charts.items():
        cur = spec["upstream"]["current"]
        newest = newest_tag(spec["upstream"])
        rows.append({"chart": name, "current": cur, "newest": newest,
                     "update": bool(newest and semver_key(newest) > semver_key(cur))})
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['chart']:<22} current={r['current']:<12} newest={r['newest']} update={r['update']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="crdkit")
    sub = ap.add_subparsers(dest="verb", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--chart")
    g.add_argument("--tag")
    g.add_argument("--bump", action="store_true")
    g.add_argument("--out", help="write outside the repo (minting an older version)")
    v = sub.add_parser("verify")
    v.add_argument("--chart")
    v.add_argument("--out")
    r = sub.add_parser("render-check")
    r.add_argument("--chart")
    r.add_argument("--out")
    c = sub.add_parser("check-upstreams")
    c.add_argument("--json", action="store_true")
    args = ap.parse_args()
    charts = load_sources()
    if args.verb == "generate":
        return generate(charts, args.chart, args.tag, args.bump, args.out)
    if args.verb == "verify":
        return verify(charts, args.chart, args.out)
    if args.verb == "render-check":
        return render_check(charts, args.chart, args.out)
    return check_upstreams(charts, args.json)


if __name__ == "__main__":
    sys.exit(main())
