# Host terminal (run servers and commands on your machine)

By default Harnix runs agent commands in a sandbox: a Docker container with **no network**, one command at a time, removed when the command ends, in a copy of your project. That is safe, and it is why "start my FastAPI server and give me a link" cannot work there: the server dies with its container and nothing can reach it.

`HARNIX_TERMINAL=host` (off by default) adds four tools that work on your real machine, like a terminal in Claude Code:

| Tool | What it does | Approval |
|---|---|---|
| `terminal_run` | one command that finishes, on the host, network available | same rules as `run_command`: test/lint tools run, everything else asks, `sudo`/`format`/... refused |
| `terminal_start` | a process that keeps running (dev server), returns an id; `wait_for_url` polls a localhost URL so the result says whether it **really answers** | always asks |
| `terminal_output` | recent output and status of a process from this conversation | no |
| `terminal_stop` | stop one of them | no |

Background processes outlive the task (so you can open the link afterwards) and die when the backend stops. See them with `forge ps` or `GET /api/processes`; stop with `forge stop p1` or `POST /api/processes/p1/stop`. At most `HARNIX_TERMINAL_MAX_PROCESSES` run at once.

## How it is bounded

* **No shell**: argv only, as everywhere in Harnix. Pipes, `&&`, redirects and `cd` do not work; use `workdir`.
* **Working directory = the task's workspace copy**, never your repository, so the server shows what the agent edited, before you Apply. Consequence: `.venv` and `node_modules` are not copied, so install dependencies there (approval) or use a global interpreter.
* **Environment**: your environment minus secret-looking names (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSW*`, ...), `HARNIX_*`, and Harnix's own secret values. Your real `HOME`, `PATH` and caches are kept so `pip`/`npm` work.
* The listening port is reachable by anything that can reach your machine; bind to `127.0.0.1`.
* `wait_for_url` only contacts localhost.
* A process can touch anything your user can touch. The approval card is the control: read the command before approving.

## Limits

Processes are in memory: they are not shown after a backend restart (they are killed on a clean stop; after a crash they may linger, `forge ps` will not show them). The workspace copy is a snapshot from the start of the conversation. Windows and Linux/macOS use the same code path; tested on Windows only. Not run with a real model or a real FastAPI project.
