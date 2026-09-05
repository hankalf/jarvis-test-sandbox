# jarvis-test-sandbox

Two unrelated things live here.

## `aal/` — a fully aware AI that answers to you

A local web app: a brand **brain** you can see and edit as a graph, an
assistant that carries that whole brain in context on every turn and writes new
knowledge back into it, and a **studio** that turns it into ad renders.

```bash
cd aal && cp .env.example .env   # add your ANTHROPIC_API_KEY
./run.sh                         # http://127.0.0.1:8420
```

See [`aal/README.md`](aal/README.md).

## Proxmox "Hey Jarvis" voice stack

`deploy-jarvis-hassos-sandbox.sh`, `deploy-jarvis-voice.sh` and
`jarvis-setup-notes.md` build a self-hosted voice assistant on a Proxmox host.
Read [`CLAUDE.md`](CLAUDE.md) before running either script — there are hard
constraints about the existing production VM on that host.
