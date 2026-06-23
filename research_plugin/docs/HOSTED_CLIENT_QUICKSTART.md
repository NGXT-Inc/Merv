# Hosted client quickstart

Use this when the control plane is already hosted and this machine/VM is only
running agents against local checkouts.

## Shape

One hosted control plane serves project records and gates. Each client machine
runs one loopback-only data-plane daemon, and each local checkout is linked to
the hosted project it should work on. The machine config does not contain one
repo path or one project id; folder links live in the daemon-local registry.

## Install on the client VM

```bash
git clone <research-suite-repo-url> ~/research-suite
cd ~/research-suite/research_plugin

python3 -m venv .venv
.venv/bin/pip install -e '.[daemon]'
```

## Fast path: configure, start, and link

```bash
cd ~/research-suite/research_plugin

bin/research-plugin-client configure \
  --control-url https://your-control-plane.example.com

bin/research-plugin-client start

cd ~/work/project-a
~/research-suite/research_plugin/bin/research-plugin-client link --project-id proj_123
```

That configures the machine, starts the machine daemon, and links one local
checkout to one hosted project. The current operator-run setup uses a private
control plane, so the client does not need a control-plane token.

## What gets saved

Machine-local config, daemon logs, pid files, the daemon loopback secret, and
folder links are written under `~/.research_plugin/`; they are not part of any
research repo.

## Manage the daemon

```bash
cd ~/research-suite/research_plugin
bin/research-plugin-client start
bin/research-plugin-client health
```

The daemon is loopback-only. `start` and `health` fail if the hosted control
plane cannot be reached.

## Link more local folders

```bash
cd ~/work/project-b
~/research-suite/research_plugin/bin/research-plugin-client link --project-id proj_456

cd ~/other/repo
~/research-suite/research_plugin/bin/research-plugin-client link --project-id proj_789
```

Inspect links:

```bash
~/research-suite/research_plugin/bin/research-plugin-client links
~/research-suite/research_plugin/bin/research-plugin-client route --repo ~/work/project-a
```

Remove a link:

```bash
~/research-suite/research_plugin/bin/research-plugin-client unlink --repo ~/work/project-a
```

## Agent/MCP environment

The packaged MCP proxy auto-discovers `~/.research_plugin/client.json`. For a
manual MCP config, print the exact values:

```bash
~/research-suite/research_plugin/bin/research-plugin-client mcp-env --repo "$PWD"
```

The repo folder is temporary local context. The hosted project remains the
source of truth for project records, gates, reviews, and sandbox lifecycle.
