# Windows installer architecture

The supported Windows deployment path is Windows + WSL2. The bootstrap script
`scripts/install-wsl2.ps1` runs on the Windows host, verifies WSL2, ensures the
distribution, maps the repository path, then delegates to the SAME
`scripts/install-linux.sh` used on a native Linux host. Delegating is the point:
the two platforms cannot drift because they run one installer.

A Windows-native backend is not a supported production profile. The supported
runtime is the backend running inside the WSL2 distribution.

## Interface

```
powershell -ExecutionPolicy Bypass -File .\scripts\install-wsl2.ps1 `
  [-Distro Ubuntu] [-RepoPath <path>] [-SkipInstall] [-AllowDistroInstall] [-DetectOnly]
```

| Parameter | Default | Effect |
| --- | --- | --- |
| `-Distro` | `Ubuntu` | the WSL distribution to target |
| `-RepoPath` | the script's parent directory | the Windows repository path to map |
| `-Profile` | `development` | explicit `development` or `production` profile passed through to the Linux installer |
| `-SkipFrontend` | off | pass `--skip-frontend` to the Linux installer |
| `-SkipInstall` | off | verify prerequisites then stop without installing |
| `-AllowDistroInstall` | off | permit installing the target distribution explicitly |
| `-DetectOnly` | off | run detection and exit, with no install or delegation |

## Stages

1. **Confirm WSL2.** `wsl --status` must succeed. The script reads the default
   version and warns when it is 1 (and how to set it to 2).
2. **Detect the distribution (read-only by design).** `wsl --list --quiet`
   output is UTF-16LE on Windows, so the script strips NUL bytes and a leading
   BOM, trims, and compares case-insensitively. Detection does not install
   anything: if the distribution is missing and `-AllowDistroInstall` is not
   given, the script fails with an instruction rather than registering a
   different distribution automatically. With `-AllowDistroInstall` it runs
   `wsl --install -d <Distro>` and asks the operator to complete first-run setup
   and re-run.
3. **Map the Windows path to a WSL path.** `C:\path\to\repo` becomes
   `/mnt/c/path/to/repo`.
4. **Verify prerequisites inside WSL.** The script checks that the mapped
   directory exists and `python3.11` is present; if not, it installs
   `python3.11`, `python3.11-venv`, `python3-pip`, `git` and `curl` inside the
   distribution, and reminds the operator to install Node 22.
5. **Delegate.** The bootstrap passes the explicit profile (and optional frontend skip) to the same Linux installer, e.g. `bash scripts/install-linux.sh --profile production`.
   A non-zero exit fails the bootstrap.
6. **Print the start command** for launching the backend inside WSL.

## Operating constraints

- **Run it from the Windows host, not from inside WSL.** The script parses
  `wsl --list --quiet`; through the nested `wsl` invocation that parse does not
  behave, and the script can take the wrong branch. Treat the Windows host as
  the correct place to run it. The Linux installer executed inside the WSL2
  distribution is the path that proves the supported case.
- **Node must be installed separately.** The prerequisite step installs the
  Python toolchain but only reminds the operator about Node; a `--skip-frontend`
  install or a separately installed Node is required otherwise.
- Development inside WSL2 is the same developer profile as a Linux host; see
  [PROFILES.md](PROFILES.md).

## Legacy Windows-native helpers (not the supported path)

The repository also contains `scripts/setup-windows.ps1`,
`scripts/start-windows.ps1` and `scripts/verify-windows.ps1`. These are
developer conveniences for a Windows-native virtual environment
(`backend\.venv\Scripts\python.exe`), running `uvicorn` and `npm run dev`
directly on Windows. They predate the WSL2 path and are not a supported
production configuration: the supported Windows production deployment runs the
backend inside WSL2 via `install-wsl2.ps1`. No production TLS boundary, systemd
unit or container topology is provided for a Windows-native backend.