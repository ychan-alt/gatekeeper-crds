# gatekeeper-crds

Gateway API CRDs, vendored from upstream into a Helm chart — so the cluster can
say *which* CRD bundle it runs (`helm get metadata`), roll back to a pinned one,
and get the same command on staging and production.

Upstream publishes **no chart** (four release assets: two `*-install.yaml` and two
`*-swagger.json`), so this is not an official distribution — it is a packaging
layer over the official asset, and it is built to be *provable* rather than
trusted.

```sh
helm upgrade --install gateway-api oci://ghcr.io/ychan-alt/charts/gateway-api-crds \
  --version 1.7.0 -n gateway-api --create-namespace --take-ownership --force-conflicts
```

## The contract

Every `CustomResourceDefinition` in `charts/*/templates/` is the upstream one,
field for field, with exactly **one** addition:

- `helm.sh/resource-policy: keep` on each CRD — `helm uninstall` leaves the CRDs
  (and therefore every `Gateway`, `HTTPRoute` and `TCPRoute` in the cluster) in
  place.

Nothing else: no RBAC, no webhook, no admission policy, no hooks, no images, no
values, no templating. Dropped by design from the upstream asset: the
`safe-upgrades` `ValidatingAdmissionPolicy` and its binding — an admission policy
that can refuse a rollback is a decision for the operator, not a side effect of a
CRD chart (upstream ships it, Envoy Gateway's chart ships it, spnngl's drops it;
this chart drops it too and says so).

## Verify instead of trust

`crdkit verify` re-downloads the pinned upstream asset and fails on any
difference — the CRDs, their annotations, the chart version and `appVersion`. It
runs on every pull request and before every publish.

```sh
pip install pyyaml
python3 tools/crdkit.py verify
# gateway-api-crds: verifying against v1.7.0
#   OK 10 CRDs identical to upstream (modulo helm.sh/resource-policy)
# verify: PASS
```

The asset's SHA-256 is recorded in the chart's own metadata
(`crdkit.io/upstream-sha256`) and in `NOTICE`, so a published chart always names
the bytes it was built from.

## Layout

| Path | What it is |
|---|---|
| <a href="sources/charts.yaml">sources/charts.yaml</a> | for each chart: upstream repo, tag pattern, pinned tag, asset URL, and which kinds are kept |
| <a href="tools/crdkit.py">tools/crdkit.py</a> | the whole toolchain: `generate`, `verify`, `check-upstreams` |
| `charts/<name>/` | generated — never edit by hand, `verify` would fail |
| `.github/workflows/` | `verify` (PRs), `sync` (upstream tag watcher), `release` (package, push, sign) |

Adding a chart is one block in `sources/charts.yaml` (e.g. `traefik-crds` from
`raw.githubusercontent.com/traefik/traefik/<tag>/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml`,
once Traefik 3.8.0 GA is tagged) and one `crdkit generate`.

## Minting an older version (a rollback target)

The tree always describes **the pinned version**. Never downgrade the pin to
manufacture an older chart — that makes the repo stop describing what your
clusters actually run, and it does not create a second version: the generator has
one output directory per chart. Mint outside the tree instead:

```sh
python3 tools/crdkit.py generate --chart gateway-api-crds --tag v1.6.3 --out /tmp/mint
python3 tools/crdkit.py verify   --chart gateway-api-crds --out /tmp/mint
helm package /tmp/mint/charts/gateway-api-crds -d dist
helm push dist/gateway-api-crds-1.6.3.tgz oci://ghcr.io/ychan-alt/charts
```

`verify --out` reads the tag from the minted `Chart.yaml`, so the older chart is
checked against *its own* upstream asset and the repo pin is untouched. Tag the
commit that produced a published version (`git tag gateway-api-crds-1.7.0`) so its
source stays reachable — the registry holds every version, git holds how each was
built.

## Updating

The `sync` workflow wakes every 6 h, lists upstream tags matching the chart's
pattern and, when one is newer than the pin, regenerates and verifies the chart
and opens a PR whose body carries the CRD diff. **Merging is manual on purpose** —
a human eye on a schema change is the point — and the merge triggers `release`,
which packages, pushes to GHCR and signs with cosign (keyless).

```sh
python3 tools/crdkit.py check-upstreams
python3 tools/crdkit.py generate --bump && python3 tools/crdkit.py verify
```

## Adopting CRDs that already exist

CRDs applied earlier with `kubectl apply --server-side` belong to another field
manager, so Helm 4 (which applies server-side by default) refuses:

```
Apply failed with 4 conflicts: conflicts with "kubectl"
```

`--take-ownership` clears Helm's *release-metadata* check; `--force-conflicts`
clears the *field ownership* one. Both are needed once. After that, upgrades go
through `helm upgrade --version <new> --force-conflicts` — and no bundle should
ever be applied by hand again, or the conflict comes back.

## Publishing notes

- GHCR packages are **private by default**; the first publish needs the package
  flipped to public once, at
  `https://github.com/users/ychan-alt/packages/container/charts%2Fgateway-api-crds/settings`.
- `cosign verify` on this repo's charts uses the workflow identity
  `^https://github\.com/ychan-alt/gatekeeper-crds/\.github/workflows/release\.yml@`.

## Licence

Chart scaffolding Apache-2.0 (`LICENSE`). The vendored CRDs remain the work of
`kubernetes-sigs/gateway-api` (Apache-2.0) — see `charts/*/NOTICE`.
