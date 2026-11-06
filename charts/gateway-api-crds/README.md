# gateway-api-crds

CustomResourceDefinitions vendored from [https://github.com/kubernetes-sigs/gateway-api](https://github.com/kubernetes-sigs/gateway-api) — tag
[`v1.7.0`](https://github.com/kubernetes-sigs/gateway-api/releases/tag/v1.7.0), asset `https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.7.0/standard-install.yaml` (sha256 `4e2602c4cbe1afbc394d86f4ac6522e1a5263b01133bba1e40a6d1a549e753a1`),
licence Apache-2.0.

```sh
helm upgrade --install gateway-api-crds oci://ghcr.io/ychan-alt/charts/gateway-api-crds --version 1.7.0 \
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
