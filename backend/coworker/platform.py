"""Platform-aware command/tool policy for Coworker.

Centralizes OS detection and the per-platform command allowlists used by the
agent's ``run_command`` tool and the manual bottom-panel terminal. The backend
behaves identically on macOS / Linux / Windows; only the shell command
vocabulary differs (Unix tools on macOS/Linux, native PowerShell/cmd verbs on
Windows).

This module is a leaf in the import DAG (stdlib only) so it can be imported
from ``workspace``, the agent graph and the terminal handler without cycles.
Tests can force a platform via :func:`force_platform`.
"""

from __future__ import annotations

import os
import shutil
import sys

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

#: Override for the detected platform (set by tests via :func:`force_platform`).
_ACTIVE_PLATFORM: str | None = None


def platform_tag(platform: str | None = None) -> str:
    """Return a stable platform tag: ``darwin`` | ``win32`` | ``linux``."""
    if platform is not None:
        return platform
    if _ACTIVE_PLATFORM is not None:
        return _ACTIVE_PLATFORM
    sys_platform = sys.platform.lower()
    if sys_platform.startswith("win"):
        return "win32"
    if sys_platform == "darwin":
        return "darwin"
    return "linux"


def force_platform(platform: str | None) -> None:
    """Override the detected platform for tests. Pass ``None`` to reset."""
    global _ACTIVE_PLATFORM
    _ACTIVE_PLATFORM = platform


def is_windows(platform: str | None = None) -> bool:
    return platform_tag(platform) == "win32"


def is_macos(platform: str | None = None) -> bool:
    return platform_tag(platform) == "darwin"


def is_linux(platform: str | None = None) -> bool:
    return platform_tag(platform) == "linux"


# ---------------------------------------------------------------------------
# Command allowlists (per platform)
# ---------------------------------------------------------------------------
# Organized by category so the full set stays reviewable. The tests in
# tests/test_platform_commands.py pin the platform boundary: names tested as
# Unix-only (`ls/cat/rg/sed/chmod/find/python3`) or Windows-only
# (`dir/type/findstr/where/tasklist/get-childitem`) MUST stay OUT of COMMON.

# --- JS/TS toolchain ------------------------------------------------------
_JS_TS = frozenset({
    "bun", "deno", "node", "npm", "npx", "pnpm", "yarn", "corepack", "node-gyp",
    "tsc", "tsx", "ts-node", "tsgo", "tsup", "esbuild", "swc", "babel",
    "webpack", "rollup", "vite", "vitest", "jest", "mocha", "ava", "tape",
    "eslint", "prettier", "stylelint", "biome", "oxlint", "gulp", "grunt",
    "turbo", "nx", "lerna", "pm2", "nodemon", "forever", "concurrently",
    "cross-env", "depcheck", "ncu", "commitizen", "cz", "changesets",
    "lint-staged", "next", "nuxt", "svelte", "astro", "prisma", "drizzle-kit",
    "typeorm", "knex", "sequelize", "mongoose", "dotenv-cli", "expo", "metro",
})

# --- Python toolchain -----------------------------------------------------
_PY = frozenset({
    "python", "pip", "pip3", "pipx", "uv", "poetry", "pipenv", "pdm", "hatch",
    "conda", "micromamba", "mamba", "rye", "pytest", "ptw", "coverage", "ruff",
    "black", "mypy", "flake8", "pyright", "isort", "autopep8", "pylint",
    "bandit", "vulture", "radon", "interrogate", "twine", "pre-commit", "tox",
    "nox", "cookiecutter", "django-admin", "gunicorn", "uvicorn", "hypercorn",
    "waitress", "streamlit", "gradio", "jupyter", "jupyterlab", "ipython",
    "nbconvert", "nbformat", "papermill", "mlflow", "dvc", "airflow", "dbt",
    "spark-submit", "pyspark", "dask", "prefect", "celery", "invoke", "fabric",
    "sphinx-build", "mkdocs", "jupytext", "ansible", "ansible-playbook",
    "ansible-galaxy", "ansible-vault", "ansible-lint", "molecule",
})

# --- Ruby / PHP -----------------------------------------------------------
_RUBY_PHP = frozenset({
    "ruby", "gem", "bundle", "bundler", "rake", "irb", "rails", "rspec",
    "rubocop", "pry", "jekyll", "php", "composer", "artisan", "wp", "phpunit",
    "psalm", "phpstan", "pint", "behat", "phive",
})

# --- JVM / .NET / Swift ----------------------------------------------------
_JVM = frozenset({
    "java", "javac", "jar", "jshell", "keytool", "gradle", "mvn", "kotlin",
    "kotlinc", "scala", "scalac", "sbt", "sdk", "dotnet", "nuget", "msbuild",
    "swift", "swiftc", "zig",
})

# --- Go / Rust / C / C++ --------------------------------------------------
_GO_RUST_CC = frozenset({
    "go", "gofmt", "goimports", "golangci-lint", "govulncheck", "delve",
    "cargo", "rustc", "rustfmt", "clippy", "rustup", "sccache", "cargo-audit",
    "cargo-expand", "cargo-watch", "cargo-nextest", "cargo-binstall",
    "gcc", "g++", "clang", "clang++", "cc", "ccache", "gfortran", "zig",
})

# --- Other languages / runtimes --------------------------------------------
_OTHER_LANG = frozenset({
    "Rscript", "julia", "lua", "luajit", "perl", "erl", "elixir", "mix",
    "ghc", "runghc", "cabal", "stack", "ocaml", "ocamlc", "dune", "opam",
    "clojure", "clj", "lein", "boot", "elm", "nim", "v", "tcl", "swipl",
    "octave", "gnuplot", "maxima", "sage", "gap", "matlab", "maple",
})

# --- Build systems ---------------------------------------------------------
_BUILD = frozenset({
    "make", "gmake", "bmake", "cmake", "ninja", "meson", "samurai", "tup",
    "bazel", "buck", "scons", "waf", "autoconf", "automake", "libtool",
    "pkg-config", "aclocal", "autoreconf", "xcodebuild", "xcrun", "ant",
    "ragel", "bison", "flex", "yacc", "lex", "llvm-config", "ld", "ar",
})

# --- Ops: git / containers / k8s / IaC / cloud -----------------------------
_OPS = frozenset({
    "git", "git-lfs", "gh", "hub", "glab", "tig", "lazygit", "gitui", "hg",
    "svn", "jj", "gpg", "age", "sops", "pass", "gopass", "keybase",
    "docker", "docker-compose", "podman", "buildah", "skopeo", "nerdctl",
    "kubectl", "k9s", "helm", "kind", "minikube", "k3d", "k3s", "kustomize",
    "istioctl", "argocd", "flux", "stern", "skaffold", "tilt", "devspace",
    "telepresence", "kubeadm", "kubeval", "kubeconform", "kubescape",
    "terraform", "tofu", "terragrunt", "pulumi", "vagrant", "packer", "vault",
    "consul", "nomad", "ansible", "ansible-playbook", "ansible-galaxy",
    "ansible-vault", "ansible-lint", "molecule",
    "aws", "az", "gcloud", "aliyun", "tccli", "hcloud", "linode-cli",
    "doctl", "scw", "vercel", "now", "netlify", "firebase", "supabase",
    "heroku", "flyctl", "fly", "wrangler", "deployctl", "kubeflow", "kapp",
    "ytt", "kbld", "vendir", "imgpkg", "kctrl",
})

# --- Network / HTTP ---------------------------------------------------------
_NET = frozenset({
    "curl", "wget", "httpie", "http", "openssl", "base64", "jq", "yq",
    "ping", "traceroute", "tracepath", "dig", "nslookup", "host", "whois",
    "netstat", "ss", "ip", "ifconfig", "arp", "route", "tcpdump", "tshark",
    "mtr", "iperf", "iperf3", "nc", "ncat", "socat", "ethtool",
    "ssh", "scp", "sftp", "rsync", "rclone", "unison", "telnet",
    "ftp", "lftp", "ssh-keygen", "ssh-copy-id", "ssh-agent", "ssh-add",
})

# --- Data / databases --------------------------------------------------------
_DATA = frozenset({
    "sqlite3", "psql", "pg_dump", "pg_restore", "mycli", "pgcli", "litecli",
    "mysql", "mysqldump", "mariadb", "mariadb-dump", "redis-cli",
    "redis-benchmark", "mongosh", "mongoimport", "mongoexport", "mongodump",
    "mongorestore", "mongostat", "mongotop", "sqlcmd", "duckdb",
    "clickhouse-client", "clickhouse-local", "cqlsh", "cockroach", "etcdctl",
    "neo4j", "cypher-shell", "elasticsearch", "opensearch", "logstash",
    "filebeat", "metricbeat", "kcat", "kafka-topics", "kafka-console-consumer",
    "kafka-console-producer", "zookeeper-server-start", "memcached",
    "memcached-tool", "mongo", "influx", "timescaledb-tune",
})

# --- Media / docs ------------------------------------------------------------
_MEDIA = frozenset({
    "ffmpeg", "ffprobe", "convert", "magick", "mogrify", "sox", "gifsicle",
    "pngquant", "optipng", "jpegoptim", "exiftool", "pandoc", "wkhtmltopdf",
    "pdflatex", "xelatex", "latexmk", "bibtex", "dvisvgm", "typst", "tectonic",
    "mdbook", "markdown", "docx", "sass", "scss", "lessc", "postcss",
})

# --- Security / audit ---------------------------------------------------------
_SEC = frozenset({
    "nmap", "nikto", "sqlmap", "hydra", "john", "hashcat", "gitleaks",
    "trufflehog", "semgrep", "bandit", "gosec", "staticcheck", "cppcheck",
    "clang-tidy", "clang-format", "valgrind", "gdb", "lldb", "strace",
    "ltrace", "perf", "oprofile", "bpftrace", "trivy", "grype", "syft",
    "cosign", "dockle", "hadolint", "snyk", "osv-scanner", "checkov",
    "tfsec", "tflint", "terrascan", "souffle",
})

# --- Archive / compression -----------------------------------------------------
_ARCHIVE = frozenset({
    "zip", "unzip", "gzip", "gunzip", "tar", "xz", "bzip2", "bunzip2", "zstd",
    "7z", "brotli", "br", "pigz", "pbzip2", "rar", "unrar", "cpio", "arj",
    "p7zip", "unzstd", "unxz", "bzcat", "zcat", "unlzma", "lzma",
})

# --- Monitor / process ----------------------------------------------------------
_MONITOR = frozenset({
    "ps", "top", "htop", "btop", "glances", "atop", "iotop", "iftop",
    "nethogs", "iostat", "vmstat", "sar", "mpstat", "pidstat", "dstat",
    "dmesg", "dmidecode", "lscpu", "lsblk", "lsusb", "lspci", "lsmod",
    "systemctl", "journalctl", "service", "sysctl", "ulimit", "lsof", "fuser",
    "uptime", "free", "df", "du", "ncdu", "duf", "dust", "gdu",
})

# --- CLI enhancement / files / editors -------------------------------------------
_CLI = frozenset({
    "tree", "fd", "fzf", "fzy", "bat", "eza", "exa", "lsd", "micro", "helix",
    "kak", "vis", "code", "codium", "subl", "vim", "vi", "nano", "emacs", "ed",
    "less", "more", "mc", "ranger", "lf", "broot", "yazi", "nnn", "vifm",
    "zoxide", "direnv", "tmux", "screen", "zellij", "dtach", "abduco",
    "fish", "zsh", "bash", "sh", "dash", "tcsh", "csh", "ksh", "pwsh",
    "nushell", "xonsh", "elvish", "git-crypt", "git-secret", "gix", "tokei",
})

# --- Perf / load testing ---------------------------------------------------------
_PERF = frozenset({
    "wrk", "ab", "hey", "vegeta", "siege", "k6", "locust", "artillery",
    "autocannon", "boom", "bombardier", "wrk2", "ghz", "grpcurl", "protoc",
    "buf", "swagger", "redocly", "openapi-generator", "spectral", "asyncapi",
    "prof", "pprof", "trace", "go-torch",
})

# --- AI / ML ---------------------------------------------------------------------
_AI = frozenset({
    "ollama", "huggingface-cli", "hf", "llama-cli", "llama-server",
    "llama-quantize", "whisper", "aichat", "aider", "claude", "codex",
    "nvidia-smi", "nvtop", "rocm-smi", "mlx", "tensorboard", "optuna",
})

# --- Core Unix utilities (not in COMMON; platform-specific) --------------------
_UNIX_CORE = frozenset({
    "cat", "chmod", "chown", "chgrp", "cp", "mv", "rm", "ln", "mkdir", "rmdir",
    "touch", "ls", "pwd", "echo", "printf", "head", "tail", "wc", "sort",
    "uniq", "cut", "tr", "tee", "xargs", "awk", "grep", "rg", "egrep", "fgrep",
    "find", "locate", "which", "whereis", "man", "whatis", "apropos",
    "basename", "dirname", "realpath", "readlink", "file", "stat", "sed",
    "diff", "patch", "cmp", "comm", "sdiff", "fold", "fmt", "pr", "expand",
    "unexpand", "seq", "factor", "expr", "bc", "dc", "units", "sponge",
    "parallel", "timeout", "stdbuf", "flock", "watch", "script", "envsubst",
    "paste", "join", "tailf", "entr", "nohup", "setsid", "sleep", "nice",
    "renice", "ionice", "chrt", "taskset", "tty", "stty", "tput", "clear",
    "reset", "yes", "true", "false", "wait", "jobs", "fg", "bg", "disown",
    "arch", "machine", "logname", "users", "groups", "who", "whoami", "w",
    "last", "lastlog", "ac", "env", "printenv", "export", "unset", "set",
    "md5", "cksum", "sum", "strings", "hexdump", "od", "xxd", "crc32",
    "md5sum", "sha256sum", "shasum",
    "nm", "objdump", "readelf", "ldd", "gdb", "strace", "ltrace", "perf",
    "kill", "killall", "pkill", "pgrep", "pidof", "free", "date", "time",
    "hostname", "id", "uname", "which", "uptime", "df", "du", "ps",
    "python3",
})

#: Commands allowed on every platform (cross-platform dev toolchains & runtimes).
COMMON_COMMANDS = (
    _JS_TS | _PY | _RUBY_PHP | _JVM | _GO_RUST_CC | _OTHER_LANG | _BUILD
    | _OPS | _NET | _DATA | _MEDIA | _SEC | _ARCHIVE | _MONITOR | _CLI
    | _PERF | _AI
)

#: Commands available on Unix-like systems (macOS + Linux).
UNIX_COMMANDS = _UNIX_CORE

#: Commands available on native Windows (cmd / PowerShell vocabulary).
WINDOWS_COMMANDS = frozenset({
    # cmd builtins
    "cd", "dir", "cls", "copy", "move", "del", "ren", "mkdir", "rmdir", "type",
    "echo", "set", "date", "time", "ver", "where", "whoami", "hostname",
    "findstr", "more", "pause", "pushd", "popd", "setlocal", "endlocal", "call",
    "assoc", "ftype", "path", "tree", "robocopy", "xcopy", "attrib", "fc",
    "icacls", "mklink", "reg", "replace", "start", "subst", "timeout", "title",
    "sort", "systeminfo", "taskkill", "tasklist", "tracert", "typeperf",
    "vol", "sc", "net", "netstat", "nslookup", "pathping", "ping", "route",
    "arp", "ipconfig", "getmac", "nbtstat", "telnet", "ftp", "wmic",
    "fsutil", "driverquery", "chkdsk", "forfiles",
    # PowerShell cmdlets
    "powershell", "get-childitem", "get-content", "select-string",
    "get-command", "get-process", "get-service", "get-item", "get-itemproperty",
    "test-path", "set-location", "clear-host", "write-output", "get-date",
    "sort-object", "where-object", "select-object", "convertto-json",
    "convertfrom-json", "out-string", "write-host", "start-process",
    "stop-process", "get-help", "resolve-path", "split-path", "join-path",
    "get-location", "push-location", "pop-location", "get-member", "get-history",
    "measure-command", "get-alias", "test-netconnection", "resolve-dnsname",
    # Windows dev shells / tools (also in COMMON — kept here for clarity)
    "cmd", "pwsh",
})

#: Read-only commands auto-approved in supervised mode (Unix).
READ_ONLY_UNIX_COMMANDS = frozenset({
    "cat", "date", "df", "du", "echo", "file", "find", "grep", "head", "id",
    "less", "ls", "more", "pwd", "rg", "stat", "tail", "uname", "wc", "whoami",
    "sort", "uniq", "cut", "tr", "awk", "egrep", "fgrep", "diff", "which",
    "whereis", "man", "whatis", "apropos", "basename", "dirname", "realpath",
    "readlink", "ps", "uptime", "free", "hostname", "groups", "who", "w",
    "last", "env", "printenv", "md5", "cksum", "sum", "strings", "hexdump",
    "od", "xxd", "base64", "nm", "objdump", "readelf", "ldd", "md5sum",
    "sha256sum", "shasum", "ping", "dig", "nslookup", "host", "netstat", "ss",
    "ip", "ifconfig", "arp", "python3", "lsblk", "lscpu", "lspci", "lsusb",
    "lsof", "sysctl", "journalctl", "dmesg", "ss", "sar", "iostat", "vmstat",
    "ps", "df", "du", "tree", "git", "git-lfs", "jq", "yq", "sqlite3",
})

#: Read-only commands auto-approved in supervised mode (Windows).
READ_ONLY_WINDOWS_COMMANDS = frozenset({
    "date", "dir", "echo", "findstr", "get-childitem", "get-content", "hostname",
    "select-string", "systeminfo", "tasklist", "time", "type", "ver", "where",
    "get-command", "get-process", "get-service", "get-item", "get-itemproperty",
    "test-path", "get-date", "resolve-path", "split-path", "join-path",
    "get-location", "get-member", "get-history", "get-alias", "ping", "ipconfig",
    "netstat", "tracert", "nslookup", "test-netconnection", "resolve-dnsname",
    "driverquery", "fc", "tree", "nbtstat", "getmac", "arp", "route",
})


def allowed_commands(platform: str | None = None) -> frozenset[str]:
    """Command names the ``run_command`` tool may execute on this platform."""
    tag = platform_tag(platform)
    if tag == "win32":
        return COMMON_COMMANDS | WINDOWS_COMMANDS
    return COMMON_COMMANDS | UNIX_COMMANDS


def read_only_commands(platform: str | None = None) -> frozenset[str]:
    """Read-only commands auto-approved in supervised mode on this platform."""
    tag = platform_tag(platform)
    if tag == "win32":
        return READ_ONLY_WINDOWS_COMMANDS
    return READ_ONLY_UNIX_COMMANDS


# ---------------------------------------------------------------------------
# Executable resolution (Windows PATHEXT)
# ---------------------------------------------------------------------------

#: PATHEXT fallback used when the env var is unset.
_WINDOWS_PATHEXT = (".COM", ".EXE", ".BAT", ".CMD")


def _pathext() -> tuple[str, ...]:
    raw = os.environ.get("PATHEXT", "")
    exts = tuple(ext.strip().upper() for ext in raw.split(";") if ext.strip())
    return exts or _WINDOWS_PATHEXT


def resolve_command_name(name: str, platform: str | None = None) -> str:
    """Resolve a bare command name to an executable on this platform.

    On Windows this appends each PATHEXT extension (``.exe`` / ``.cmd`` /
    ``.bat``) and returns the first candidate found on PATH, so ``python``
    becomes ``python.exe`` and ``npm`` becomes ``npm.cmd``. On Unix the bare
    name is returned unchanged (subprocess resolves it via PATH).
    """
    name = name.strip()
    if not name:
        return name
    if not is_windows(platform):
        return name
    if os.path.splitext(name)[1].upper() in _pathext():
        return name
    for ext in _pathext():
        candidate = name + ext
        if shutil.which(candidate) is not None:
            return candidate
    return name


# ---------------------------------------------------------------------------
# Shell selection
# ---------------------------------------------------------------------------

def default_shell(platform: str | None = None) -> str:
    """Pick an interactive shell for the current platform.

    Windows prefers PowerShell (falls back to ``%COMSPEC%`` / cmd.exe). Unix
    honours the ``SHELL`` env var and falls back to bash on Linux and zsh on
    macOS.
    """
    tag = platform_tag(platform)
    if tag == "win32":
        if shutil.which("powershell.exe"):
            return "powershell.exe"
        comspec = os.environ.get("COMSPEC")
        if comspec:
            return comspec
        return "cmd.exe"
    shell = os.environ.get("SHELL")
    if shell:
        return shell
    return "/bin/bash" if tag == "linux" else "/bin/zsh"


# ---------------------------------------------------------------------------
# LLM-facing hints
# ---------------------------------------------------------------------------

_CURATED_COMMON = (
    "git, gh, node, npm, npx, yarn, pnpm, bun, deno, python, pip, pytest, "
    "java, go, cargo, rustc, gcc, make, cmake, docker, kubectl, helm, terraform, "
    "ansible, aws, curl, wget, jq, yq, tar, zip, unzip, gzip, sqlite3, psql, "
    "redis-cli, mongosh, ffmpeg, pandoc, tmux"
)
_CURATED_UNIX = _CURATED_COMMON + (
    ", ls, cat, grep, rg, find, sed, awk, head, tail, wc, sort, uniq, diff, "
    "rsync, ssh, ping, dig, vim, nano, python3, sh, bash, ps, kill"
)
_CURATED_WIN = _CURATED_COMMON + (
    ", dir, type, findstr, where, powershell, get-childitem, ipconfig, netstat, "
    "tasklist, sc, net, robocopy, curl, where, whoami"
)


def command_hint(platform: str | None = None) -> str:
    """Curated summary of the allowlisted commands, for the ``run_command`` tool
    description. The allowlist itself covers all mainstream dev/ops toolchains
    plus niche utilities (hundreds of commands); listing every name would bloat
    every model call, so we surface the most common and tell the model how to
    verify availability of anything else.
    """
    tag = platform_tag(platform)
    curated = _CURATED_WIN if tag == "win32" else _CURATED_UNIX
    total = len(allowed_commands(tag))
    more = max(0, total - len(curated.split(", ")))
    return (
        f"Allowed commands: {curated}, and {more} more (all mainstream dev/ops "
        "toolchains plus niche utilities). If a command is rejected, the error "
        "names it — confirm availability with `which <cmd>` / `command -v <cmd>` "
        "before retrying."
    )


def platform_hint(platform: str | None = None) -> str:
    """One line describing the OS for the model's system prompt."""
    tag = platform_tag(platform)
    labels = {"darwin": "macOS", "win32": "Windows", "linux": "Linux"}
    label = labels.get(tag, tag)
    return (
        f"Current platform: {label} ({tag}). "
        "The run_command tool only executes an allowlisted command set — see its tool description."
    )


__all__ = [
    "COMMON_COMMANDS",
    "UNIX_COMMANDS",
    "WINDOWS_COMMANDS",
    "READ_ONLY_UNIX_COMMANDS",
    "READ_ONLY_WINDOWS_COMMANDS",
    "allowed_commands",
    "command_hint",
    "default_shell",
    "force_platform",
    "is_linux",
    "is_macos",
    "is_windows",
    "platform_hint",
    "platform_tag",
    "read_only_commands",
    "resolve_command_name",
]
