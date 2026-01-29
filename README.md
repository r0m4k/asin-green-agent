# ASIN Green Agent (AgentBeats Benchmark)

ASIN (Assessment of Spatial Intelligence for Navigation) is a **Green Agent (evaluator)** for AgentBeats. It evaluates a Purple agent’s ability to navigate in **Manhattan, NYC** using:

- a **2D map** (static map image with the target route + waypoint markers)
- a **Street View image** (first-person view at the agent’s current location/heading)

The Purple agent must issue navigation actions (`f`, `l <deg>`, `r <deg>`, `q`) and is scored on how well it follows the route and how close it finishes to the destination.

---

## What this benchmark tests

- **Visual grounding**: align Street View with the 2D map geometry
- **Spatial reasoning**: turn/move decisions from partial observations
- **Planning / multi-step control**: recover from wrong turns under a step budget

---

## Levels, determinism, and task selection

This benchmark has **10 levels** configured in `src/utils.py` (`LEVEL_CONFIG`).

- Each level has a target route length (meters) and a waypoint count.
- The evaluator uses a deterministic seed per level:
  - `seed = 1000 + level`
- The **evaluation route polyline is truncated** to match `LEVEL_CONFIG[level]["dist"]` **exactly** (so Level distances align by construction).

### How to select a level (critical for reproducibility)

The server chooses the level from `task_config`:

- If `task_config` is a dict, it supports:
  - `{"level": 1..10}` (preferred)
  - `{"task_index": 0..9}` / `{"index": 0..9}`
- If `task_config` is a string (common in AgentBeats), it supports:
  - `"0"` .. `"9"` (interpreted as task index → level = index + 1)
  - `"Task description: 0"` .. `"Task description: 9"` (same behavior)
  - strings containing the word `level` like `"level 3"` (interpreted as level)

If `task_config` is omitted, the server falls back to an internal counter (fine for ad-hoc local testing, **not recommended** for fairness across agents).

---

## Scoring (high-level)

Final score is computed from the walked path vs the reference route:

- **Destination bonus**: +30 if within 50m of the destination
- **Route similarity**: up to 70 based on average deviation to the route polyline
- **Progress multiplier**: similarity is multiplied by estimated progress along the route
- **Level weighting**: the per-task score is multiplied by `LEVEL_CONFIG[level]["weight"]`

The response of `/result` includes both `raw_score` and `score` (weighted).

---

## Safety limits (prevents infinite runs)

The evaluator enforces a **per-level step limit** derived from the level distance and step size:

- `STEP_SIZE_METERS = 15`
- `max_steps = max(120, ceil(target_dist_m / STEP_SIZE_METERS) * 3)`

This keeps runs bounded while allowing recovery from mistakes on longer routes.

---

## Requirements

### Environment variables

This benchmark requires a **Google Maps Platform API key**:

- `GOOGLE_MAPS_API_KEY` (**required**)

Do **not** hardcode keys into this repository.

### Google APIs to enable (Google Cloud Console)

Enable billing and enable these APIs for your project:

- **Directions API** (used to generate routes)
- **Maps Static API** (used to render the 2D map)
- **Street View Static API** (used to render Street View images)

If Directions API is not enabled (or your project only allows newer APIs), route generation will fail.

---

## Run locally (Python)

From `ASIN-Green-Agent/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export GOOGLE_MAPS_API_KEY="YOUR_GOOGLE_MAPS_KEY"
python -m src.server --host 0.0.0.0 --port 9009
```

Server will run at `http://localhost:9009`.

---

## Run locally (Docker)

From `ASIN-Green-Agent/`:

```bash
docker build -t asin-green-agent:local .
docker run --rm \
  -p 9009:9009 \
  -e GOOGLE_MAPS_API_KEY="YOUR_GOOGLE_MAPS_KEY" \
  asin-green-agent:local --host 0.0.0.0 --port 9009
```

---

## API contract (local compatibility endpoints)

The server exposes these HTTP endpoints (POST JSON):

### `POST /start`

Request:

```json
{
  "session_id": "any-unique-string",
  "task_config": {"level": 1}
}
```

Response:
- `done`: boolean
- `prompt`: instruction text
- `images`: `[map_b64, streetview_b64]` (base64 PNG/JPEG bytes)
- `info.level`: selected level (when available)

### `POST /act`

Request:

```json
{
  "session_id": "same-as-start",
  "action": "f"
}
```

Valid actions:
- `f` (move forward 15m)
- `l <deg>` (turn left)
- `r <deg>` (turn right)
- `q` (finish)

Response includes updated `images` and `done`.

### `POST /result`

Request:

```json
{
  "session_id": "same-as-start"
}
```

Response includes:
- `score` (weighted)
- `raw_score`
- `level`, `weight`
- `destination_reached`
- `distance_to_target`
- `avg_deviation`
- `final_map_b64`

---

## Troubleshooting

### `REQUEST_DENIED` / “API not enabled”
- Verify billing is enabled
- Verify the APIs are enabled (Directions, Maps Static, Street View Static)
- Verify your API key restrictions allow server-side usage

### Not reproducible across runs
- Always pass `task_config` (e.g., `{"level": 1}` or `"0".."9"`) to avoid relying on internal counters.

---

## Submission notes (AgentBeats / AgentX)

For competition submission, you generally need:

- a public GitHub repo (this code)
- a Docker image built from this directory and pushed to a registry
- AgentBeats registration for the green agent
- baseline purple agent(s) + a short demo video (per competition requirements)

See the competition page: `https://rdi.berkeley.edu/agentx-agentbeats`

