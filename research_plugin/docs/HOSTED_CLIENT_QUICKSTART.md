# Hosted client quickstart

Use this when the control plane is already hosted and this machine/VM is only
running agents against local checkouts.

## Shape

One hosted control plane serves project records and gates. Each client machine
uses the stdio MCP proxy as its local data plane, and each local checkout is
linked to the hosted project it should work on. The machine config does not
contain one repo path or one project id; folder links live in a machine-local
SQLite link file under `~/.research_plugin/`.

## Install on the client VM

```bash
git clone <research-suite-repo-url> ~/research-suite
cd ~/research-suite/research_plugin

python3 -m venv .venv
.venv/bin/pip install -e .
```

## Fast path: configure and link

```bash
cd ~/research-suite/research_plugin

bin/research-plugin-client configure \
  --control-url https://your-control-plane.example.com

cd ~/work/project-a
~/research-suite/research_plugin/bin/research-plugin-client link --project-id proj_123
```

That configures the machine and links one local checkout to one hosted project.
The current operator-run setup uses a private control plane, so the client does
not need a control-plane token.

## What gets saved

Machine-local config and folder links are written under `~/.research_plugin/`;
they are not part of any research repo.

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
