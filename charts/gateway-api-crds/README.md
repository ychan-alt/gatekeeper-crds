# gateway-api-crds

CustomResourceDefinitions vendored from [https://github.com/kubernetes-sigs/gateway-api](https://github.com/kubernetes-sigs/gateway-api) — tag
[`v1.6.3`](https://github.com/kubernetes-sigs/gateway-api/releases/tag/v1.6.3), asset `https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.3/standard-install.yaml` (sha256 `356c2b8e286a964dc20995435659ad1095386329974793af10bbfe3eff977fe5`),
licence Apache-2.0.

```sh
helm upgrade --install gateway-api-crds oci://ghcr.io/ychan-alt/charts/gateway-api-crds --version 1.6.3 \
  -n gateway-api-crds --create-namespace --take-ownership --force-conflicts
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
- `ValidatingAdmissionPolicy/safe-upgrades.gateway.networking.k8s.io`
- `ValidatingAdmissionPolicyBinding/safe-upgrades.gateway.networking.k8s.io`

## Updating

`python3 tools/crdkit.py generate --bump && python3 tools/crdkit.py verify`, then
merge — the scheduled workflow opens that PR on its own.
