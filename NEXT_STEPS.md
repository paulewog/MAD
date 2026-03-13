# MAD Next Steps

## Critical

1. **No file locking on feature files** — `state.py` `FeatureFile` reads/writes JSON without locking. Multiple processes (runner, TUI, server) can corrupt data via concurrent access.

2. **API key in plain text** — `.mad/config.json` contains a hardcoded `api_key` tracked by git. Move to environment variables or secrets manager; rotate the key.

3. **File handle leak in runner** — `runner.py:370` opens log file without context manager. Exception before `finally` block leaves handle open.

## High

4. **Fragile JSON parsing of agent output** — `phases.py` uses regex fallback to extract JSON from agent output. Fails on nested structures, no schema validation on parsed results.

5. **Process cleanup race conditions** — `runner.py` SIGTERM→SIGKILL escalation has no timeout on `process.wait()` (can hang on WSL), silently swallows `os.killpg` errors, and mutates shared status object without synchronization.

6. **Silent error swallowing** — Multiple locations catch broad exceptions and continue without logging: `state.py:408` (`except Exception: continue`), `config.py:217` (`except Exception: pass`), `server_client.py` reconnect loop.

7. **`auto` command registration bug** — `pipeline.py:437` uses `@click.command()` instead of `@cli.command()`, so `pipeline auto` is unreachable. Also has duplicate docstrings.

## Medium

8. **Hardcoded paths in schedule module** — `schedule.py` hardcodes `~/MAD/` for `SCHEDULES_PATH` and `RUNS_PATH` instead of using config's `mad_dir`.

9. **No log rotation** — `runner.log` and `pipeline.log` grow unbounded. Already 3+ MB. Will eventually fill disk on long-running systems.

10. **Auth bypass when no keys configured** — `server/api.go` `checkAnyAuth()` returns true if both keys are empty, leaving the server fully open by default.

11. **TUI is a 3,157-line monolith** — `tui.py` mixes UI rendering, data loading, agent management, and server communication. Hard to test or maintain. File-stat polling on every refresh won't scale.
