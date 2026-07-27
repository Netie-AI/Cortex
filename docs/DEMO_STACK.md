# Full demo stack (Cortex + OpenVault + AirGPT)

Ports: **Cortex `8000`** · **OpenVault `5000`** · **AirGPT/OpenIDE `8765`**

## One command (Linux / cloud agent)

```bash
export OPENVAULT_ROOT=/path/to/openvault   # sibling ../openvault in multi-repo envs
bash scripts/start_demo_stack.sh
```

This starts Cortex (if needed), OpenVault console with `--mock-health`, approves mesh peers, and starts `OpenVault/scripts/airgpt_demo_shell.py` when real AirGPT is not listening on `:8765`.

## Windows (existing)

1. Start Cortex from this repo (`demo/run_demo.ps1` or uvicorn on `:8000`).
2. From OpenVault: `scripts\\windows\\Start-LocalMesh.ps1`
3. Start real AirGPT from `D:\\AirGPT` on `:8765` (preferred).

## Verify

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:5000/api/healthz
curl -s http://127.0.0.1:8765/api/healthz
curl -s http://127.0.0.1:5000/api/local/connect-pack | python3 -m json.tool | grep -A3 perfect_local
```

`perfect_local.ready` should be `true`.

## Demo film

See `docs/VIDEO_YC_MULTIPLAYER.md`, `docs/VIDEO_YC_PROMPTS.md`, and `video-assets/`.
