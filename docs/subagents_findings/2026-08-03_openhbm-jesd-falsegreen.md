```yaml
keywords: [jesd270-4a, hbm4, clean-room, false-compliance, false-green, assert-True, coverplan, testbench]
main_idea: "OpenHBM overclaims JESD270-4A compliance while leaf-IP cocotb suites and agent-eval func_cov can green without proving the named behavior."
models: [grok-4.5]
workflow: multi-agent-audit-then-harden
reuse: golden_rule
status: verified
cite: agent:1a3f50ed-affc-4e95-980f-f05d9d50e6af; agent:134b54c4-2d7c-4ea4-8d3b-6b7ea344ecad
repo: OpenHBM
date: 2026-08-03
```

# OpenHBM JESD false-compliance + soft testbenches

## Main idea

- Repo is Phase-0/early Phase-1 leaf-IP scaffold, not a JEDEC-compliant 32x2 stack.
- `hbm4_ctrl` DV had explicit `assert True` and vacuous multichan returns; harness `func_cov` scored self-touched bins to ~100%.

## Keywords (search)

`jesd270-4a`, `hbm4`, `clean-room`, `false-compliance`, `false-green`, `assert True`, `coverplan`, `qos_starvation`, `pmu_throttle`

## Questions left open

- Can top-level AXI flood actually hit PMU_THROTTLE (75% of 1024-cycle window) under real bank timing?
- Should `refresh_mgr` be integrated into `hbm4_ctrl` (kill 240-cycle DRFM stub) before any compliance claim?
- Full cocotb regression blocked here: container lacked `g++` after aborted apt.

## Full answer / evidence

PDF `JESD270-4A.pdf` is on disk, gitignored (`*.pdf`) -- do not commit.
Clean-room: `docs/spec/hbm4_timing.adoc` mostly `[SEE-SPEC]`.
Present RTL: `addr_map`, `ecc`, `refresh_mgr`, `hbm4_ctrl` only.
Missing vs Plan: phy_shim, dual-command, dual-PC, init/MRS, integrated refresh/ECC datapath.

Hardened:
- `hw/ip/hbm4_ctrl/dv/tests/test_hbm4_ctrl.py` (starvation/PMU/training/multichan/phy)
- `hw/ip/hbm4_ctrl/dv/env/hbm4_ctrl_ref.py` (ACT-before-RW loophole)
- `hw/ip/refresh_mgr/dv/tests/test_refresh_mgr.py`
- `hw/ip/addr_map/dv/tests/test_addr_map.py` (region commit + shadow atomicity)
- `tools/agent_eval/harness/stages/sim.py` + `dv/coverplan.txt` files
- README/CLAUDE messaging demoted; CLAUDE shell-debris / dead phy_shim path fixed
- `ecc_decode.sv` always_comb latch init

## Golden rule (if reusable)

> Before trusting a cocotb/agent-eval green: grep for `assert True`, bare `return` under size guards, and coverage writers that only emit hit bins; require named property asserts and a declared coverplan scored by the harness.

## Verify

```bash
source /opt/oss-cad-suite/environment
PATH=/opt/oss-cad-suite/bin:/usr/bin:/bin:$PATH
# needs g++ from build-essential
make -C hw/ip/ecc/dv SIM=verilator
make -C hw/ip/refresh_mgr/dv SIM=verilator
make -C hw/ip/addr_map/dv SIM=verilator
make -C hw/ip/hbm4_ctrl/dv SIM=verilator NUM_CHANNELS=4
```

## Promote?

Yes -- false-green DV patterns into OpenHBM agent rules / eval harness docs.
