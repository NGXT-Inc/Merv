"""Shared tool baselines for SSH-accessible compute environments."""

from __future__ import annotations


BASELINE_APT_PACKAGES: tuple[str, ...] = (
    "bash",
    "sudo",
    "git",
    "rsync",
    "ripgrep",
    "fd-find",
    "findutils",
    "grep",
    "sed",
    "gawk",
    "jq",
    "tree",
    "less",
    "file",
    "curl",
    "wget",
    "tar",
    "gzip",
    "zip",
    "unzip",
    "xz-utils",
    "python3",
    "python3-venv",
    "python3-pip",
    "build-essential",
    "pkg-config",
    "cmake",
    "ninja-build",
    "git-lfs",
    "procps",
    "util-linux",
    "iproute2",
    "dnsutils",
    "lsof",
)


MODAL_APT_PACKAGES: tuple[str, ...] = (
    "openssh-server",
    "ca-certificates",
    *BASELINE_APT_PACKAGES,
)


LAMBDA_APT_PACKAGES: tuple[str, ...] = (
    "openssh-server",
    "ca-certificates",
    *BASELINE_APT_PACKAGES,
)


ML_PYTHON_PACKAGES: tuple[str, ...] = (
    "transformers",
    "numpy",
    "matplotlib",
    "pandas",
    "scikit-learn",
)
