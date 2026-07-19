# AmbiHub — Cloud Hosting Design

Status: approved design, not yet built. Last updated 2026-07-18.

This is the plan to move AmbiHub from "runs on Carlos's Mac" to "a paying customer can use it in the cloud." Nothing here is built yet. Phase A is gated on the RunPod render benchmark.

## Locked decisions
| Area | Decision | Why |
|---|---|---|
| Web host | **Railway** | Simple Flask + worker + volume + custom domain, matches other apps. |
| Render compute | **RunPod Serverless** (pay per render, scale to zero) | Per-customer renders run in parallel; no idle GPU cost at launch volume. |
| Object storage | **Cloudflare R2** | Render runs on RunPod (outside AWS), so S3 cross-cloud egress would bill ~$0.09/GB per video pull. R2 has zero egress and is S3-compatible. |
| Database | JSON on a persistent volume for launch; **Supabase Postgres** later | Postgres migration is a scaling upgrade, not launch-critical (see Phase B). |

If we ever move render onto AWS compute in the same region as the bucket, S3 becomes the better pick (free intra-region transfer). As long as render is on RunPod, R2 wins.

## Why this migration is tractable
The existing code already does the hard part remotely:
- All generation (images, upscale, video loops, music) is remote fal.ai. No local GPU inference.
- The autopilot music path uses fal Stable Audio, so the visible-Chrome Suno automation is an operator-only tool and is **not** part of the hosted customer path. It stays local.
- Multi-tenancy, tier/quota enforcement, ownership guards, and Stripe billing are built and proven end to end.

The only local heavy compute is the FFmpeg render, and that is what moves to RunPod.

## Target architecture
```
 Customer browser
      │  HTTPS (Railway custom domain)
 ┌────▼───────────────┐        ┌──────────────────────┐
 │  Web tier          │  fal   │  fal.ai (all gen)    │
 │  Flask + gunicorn  │───────▶│  images/video/music  │
 │  (Railway service) │        └──────────────────────┘
 │  autopilot loop    │        ┌──────────────────────┐
 │  (single owner)    │  job   │  RunPod Serverless   │
 │  + persistent vol  │───────▶│  GPU FFmpeg (NVENC)  │
 │    for JSON state  │        │  scale to zero       │
 └───┬────────────────┘        └──────────┬───────────┘
     │ media (R2 keys)                     │ pull assets / push final
 ┌───▼─────────────────┐                   │
 │  Cloudflare R2      │◀──────────────────┘
 │  (signed URLs)      │
 └─────────────────────┘
```

Component responsibilities:
- **Web tier (Railway):** the existing Flask app under gunicorn. Serves the UI and API, runs Stripe checkout and YouTube OAuth, orchestrates the pipeline, and runs the autopilot scheduler loop on exactly one instance. A Railway volume holds the JSON state files for launch.
- **Render worker (RunPod Serverless):** a Docker image with FFmpeg + NVENC and the existing `build_video` logic wrapped as a serverless handler. Input is a job describing the asset R2 keys and overlay config; it downloads from R2, encodes, uploads the final to R2, returns the key.
- **Object storage (R2):** all media (generated images, loops, music tracks, final renders). The app stores R2 keys, never absolute paths. The `/files/` route redirects to a short-lived signed URL.

## Data flow for one video
1. Web tier creates the project, calls fal.ai for images, upscale, loop, and Stable Audio music. Assets are written to R2 (keys saved in project state).
2. Web tier dispatches a render job to the RunPod endpoint with the asset keys and overlay config.
3. RunPod worker pulls assets from R2, runs the NVENC FFmpeg render, pushes `{slug}_1hr.mp4` to R2, returns its key.
4. Web tier runs the SEO step, then the YouTube upload (private with `publishAt`, preserving the veto window and all existing guards).
5. Veto-window email fires via existing notifications.

## What changes, mapped to the code
| Change | Files | Note |
|---|---|---|
| Secrets into env | `UltraFocusZone_Automation/config.json`, `generate_assets.py:71`, `youtube_upload.py:44` | Keys live only in the gitignored local `config.json` (not in git; the committed `config.default.json` is empty). For a shared host, read them from env. `FAL_KEY` fallback already exists; add an env fallback for YouTube OAuth. |
| Production server | `dashboard/app.py:2091` | Add gunicorn + a `wsgi:app` entry and a Railway start command. Keep `app.run` only under `__main__` for local dev. |
| Parametrize URLs | `youtube_upload.py:37`, `notifications.py:55` | Build the OAuth redirect and base URL from `APP_BASE_URL`. |
| Storage abstraction | new `dashboard/storage.py`; writers in `generate_assets.py`, `tasks.py`; `build_video.py` | boto3 client pointed at the R2 endpoint. Replace local read/write; stop saving absolute paths (save keys). |
| Serve via signed URL | `app.py:1763` `/files/` | Redirect to a signed R2 URL instead of streaming from disk. |
| Render on RunPod | new handler image; `tasks._run_step3` (`tasks.py:771`) | Dispatch a job to the RunPod endpoint and wait, instead of local `bv.assemble_video`. |
| Single scheduler owner | `autopilot.py:452`, `app.py:2094` | Keep the loop in the one web instance, gated by the existing `AUTOPILOT_SCHEDULER` flag. |

## Phase A — launch-critical (the only thing between "works for you" and "a customer can use it")
Gated by the RunPod benchmark. Build order:
1. **Secrets into env.** Move the fal and YouTube keys from the local `config.json` into Railway env vars. The keys are not exposed in git today (local config is gitignored, the committed template is empty), so this is a hosting step, not an emergency. Rotating the keys when moving to a shared host is good hygiene but optional.
2. **Production server + URLs.** gunicorn, `wsgi:app`, parametrized OAuth redirect and base URL.
3. **R2 storage layer.** New `storage.py`, migrate all media read/write, switch `/files/` to signed URLs, stop persisting absolute paths.
4. **RunPod render worker.** Package `build_video` as a serverless handler image, dispatch from `_run_step3`, handle result and failure.
5. **Deploy on Railway.** One web service, a mounted volume for the JSON state, `AUTOPILOT_SCHEDULER=1` on that single instance, custom domain + HTTPS.

Phase A keeps state as JSON on a single instance on purpose. With render offloaded to RunPod, the web instance stays light, so this avoids the large Postgres migration while still delivering a fully working hosted product.

## Phase B — scale (only when one web instance is not enough)
1. Migrate `users.json`, `projects.json`, `channels.json`, `image_library_*.json` to Supabase Postgres.
2. Replace in-process `threading.Lock` guards with the database.
3. Split the autopilot scheduler into a dedicated worker so the web tier is stateless.
4. Scale the web tier horizontally.

This is a scaling upgrade, not a launch requirement.

## RunPod benchmark (Phase A gate)
Purpose: measure the real cost of one render so the pricing floor is validated.
- Measures: wall-clock time and GPU-seconds to NVENC-encode one 1-hour 1080p render on a chosen RunPod tier, plus the output file size (feeds the R2 storage and transfer estimate).
- Validates: the render-cost side of the locked margin math (Pro cap 25, the $4.25 gross-credit floor). If a render costs more GPU time than assumed, the phasing is unchanged but the pricing floor is re-checked before launch.
- Cost: roughly one dollar.
- Needs: Carlos's RunPod signup.

## Open items on Carlos's side
- **RunPod signup** so the benchmark can run. This is the Phase A gate.
- **Buy ambihub.ai.** Starts the multi-week YouTube API audit clock, which blocks customer uploads regardless of hosting progress. Do this first, in parallel.
- **Railway account** for the web tier deploy.

## Safety note
Secrets are not exposed in git today: the real keys sit only in the gitignored local `config.json`, and the committed `config.default.json` is empty. Phase A moves them into Railway env vars as a normal hosting step. Keep the standing rule in force through the migration: never expose live credentials in the repo, and never test outward actions (YouTube publish, veto) against real customer data.
