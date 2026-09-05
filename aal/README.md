# AAL — a fully aware AI that answers to you

A local web app modelled on the ad-lab setup in the reference video: a **brand
brain** you can see and edit as a graph, an assistant that carries the whole
brain in context on every turn and writes new knowledge back into it, and a
**studio** that turns that knowledge into ad renders.


---

## What "fully aware" actually means here

The brain is ten categories hanging off one brand node — the same set shown in
the video:

| Category | What lives in it |
|---|---|
| Identity | Positioning, audience, tone of voice, visual signature, non-negotiables |
| Your taste | What you always approve, what you always kill |
| Claims | What may legally and factually be said; banned phrasing |
| Assets | Packshots, logos, palette, type |
| Angles | The argument an ad makes |
| Prompt bank | Render prompts that worked, with their settings |
| Intel file | Audience, objections, seasonality, pricing, channel behaviour |
| Ad Spy | Competitor ads and how long each has been running |
| Offers | Live promotions and price points |
| Performance | CTR, hook rate, ROAS, spend, per creative |

On **every** turn the server renders the complete graph into a digest and puts
it in the system prompt (`GET /api/brain/digest` shows you exactly what the
model sees). That is the awareness. On top of it the assistant has tools to go
deeper and to change what it knows:

| Tool | Effect |
|---|---|
| `search_brain`, `list_category` | read past the digest into full entries |
| `remember`, `update_memory`, `forget` | write durable facts back into the graph |
| `link` | connect an angle to the intel it came from |
| `render_ad`, `list_renders` | queue and inspect studio renders |

Tell it *"our 40oz base is 3 inches so it fits a car cupholder"* and it files
that under Intel; the graph in the UI updates the moment the turn ends, and it
is part of the context on every future question.

## Running it

```bash
cp .env.example .env      # put your ANTHROPIC_API_KEY in it
./run.sh                  # creates .venv, installs deps, serves on :8420
```

Then open <http://127.0.0.1:8420> and press **Load demo brand** to get a
populated graph to poke at, or start empty and tell the assistant about your
own brand.

Configuration is all environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** The brain. |
| `FAL_KEY` | — | Optional. Real image/video renders via Fal AI. |
| `AAL_MODEL` | `claude-opus-5` | Swap to `claude-sonnet-5` / `claude-haiku-4-5` for cheaper high-volume work. |
| `AAL_EFFORT` | `medium` | `low` … `max`. Raise it for strategy work, lower it for chat. |
| `AAL_PORT` | `8420` | |

Without `FAL_KEY` the studio still works end to end — it writes a local
placeholder card per render, so you can exercise prompt → studio → prompt bank
without spending anything.

## The three views

- **Brain** — the force-directed graph (drag to pan, scroll to zoom, click any
  node to inspect, edit or delete it) with a **Grid** layout as the alternative
  view. Search dims everything that does not match.
- **Studio** — prompt composer with Image/Video, a Templates drawer fed by your
  prompt bank, a count stepper, and an Image Settings panel (engine, model,
  aspect ratio, resolution, estimated cost). Renders land in the grid above.
- **Ad Spy** — competitor ads with days-live, plus **Recommend what to ship
  next**, which asks the model for ranked, brain-grounded next ads with a
  complete render prompt for each. One click sends a recommendation to the
  studio.

The chat dock is always on the right. 🎙 dictates (browser speech recognition)
and 🔊 speaks replies back, so it answers to you out loud with no extra
hardware.

## Shape of the code

```
server/
  config.py   env-driven settings
  db.py       sqlite: nodes, edges, messages, renders
  brain.py    the ten categories, the graph, the context digest
  agent.py    persona, tool definitions, streaming tool loop
  render.py   Fal AI adapter + offline placeholder renderer
  seed.py     demo brand
  main.py     FastAPI routes + SSE
web/
  index.html, styles.css, app.js, graph.js   (vanilla, no build step)
```

Notes on the model integration:

- Streaming manual tool loop (`agent.converse`) so the UI gets tokens, thinking
  and tool chips live rather than one blob at the end.
- Adaptive thinking with `display: "summarized"`; effort is configurable.
- Server-side refusal fallbacks are on by default (`AAL_FALLBACKS=0` to turn
  them off), so a declined request routes to a fallback model instead of
  returning nothing.
- The brain digest sits behind a cache breakpoint, so a conversation that does
  not change the brain re-reads it from cache.

## API

`GET /api/status` · `GET /api/brain` · `GET /api/brain/digest` ·
`GET /api/brain/category/{c}` · `POST|PATCH|DELETE /api/brain/node` ·
`POST /api/brain/edge` · `POST /api/brain/seed` · `POST /api/brain/brand` ·
`POST /api/chat` (SSE) · `GET /api/chat/{s}/history` ·
`GET /api/studio/config` · `POST /api/studio/render` · `GET /api/studio/renders` ·
`POST /api/studio/estimate` · `POST /api/recommendations`

Interactive docs at `/api/docs`.

## Relationship to the Proxmox scripts in this repo

None — `deploy-jarvis-*.sh` and `jarvis-setup-notes.md` are untouched. This app
runs anywhere Python does. If you later want the voice assistant to share this
brain, point a Home Assistant conversation agent at `POST /api/chat`; the
awareness and the memory tools come along with it.
