# AmbiHub — Launch Checklist

The product is built and proven. What remains to be "on the market" is almost
entirely hosting plus the YouTube verification clock, and most of the remaining
blockers are account signups only Carlos can do, not code.

Two reference docs:
- [docs/cloud-hosting-design.md](docs/cloud-hosting-design.md) — architecture, phasing, and the Phase A deploy checklist.
- [docs/youtube-api-verification.md](docs/youtube-api-verification.md) — the YouTube filing package and demo-video script.

---

## Done and proven (pushed)
- [x] Multi-tenancy with a verified security boundary (tested with a second account)
- [x] Stripe billing proven end to end (checkout + webhook both directions, real test card)
- [x] Tier / quota enforcement live
- [x] Autopilot pipeline (generate, render, schedule, upload with veto window)
- [x] Email notifications (veto-window + failure), inert until SMTP is set
- [x] Phase A hosting prep: env-based secrets, gunicorn/wsgi entry, scheduler file-lock, stripe dependency, deploy checklist

---

## 1. Carlos — account signups (these gate everything; do this week)
- [ ] **Buy ambihub.ai** — the single most important action. It is the app domain and the blocker on the YouTube filing (section 4), which is the longest pole. Every day unbought is a day added to launch.
- [ ] **RunPod signup** — gates the render benchmark and the render worker.
- [ ] **Railway account** — the web host.
- [ ] **Cloudflare R2 account + bucket + API token** — media storage.

## 2. Claude — Phase A hosting build (once the accounts exist)
Built and verified against the real account, not shipped unproven.
- [ ] **RunPod benchmark (~$1)** — validates the render-cost math the pricing is locked against. This is the Phase A gate.
- [ ] **R2 storage layer** — `storage.py`, migrate media I/O to R2, switch `/files/` to signed URLs while preserving the `serve_file` ownership check.
- [ ] **RunPod render worker** — package `build_video` as a serverless handler image.
- [ ] **Railway deploy** — one web service, volume for the JSON state, env vars, `--workers 1`, custom domain + HTTPS.

## 3. Launch-day switches (small, but silent failures if missed)
These are in the Phase A deploy checklist in the design doc.
- [ ] Create live Stripe products/prices, switch to live keys (test is proven; this is the flip), then one `stripe listen` sanity check against live.
- [ ] Register the production OAuth redirect URI (`APP_BASE_URL` + `/oauth/callback`) in the Google Cloud console.
- [ ] Move real keys into Railway env vars.

## 4. YouTube verification + audit (longest external clock, weeks; gated on the domain)
- [ ] Domain live with the (already-written) privacy/terms pages hosted.
- [ ] Record the demo video (script in [docs/youtube-api-verification.md](docs/youtube-api-verification.md)).
- [ ] Submit the compliance audit. It unlocks public customer uploads and lifts the default ~6-uploads/day quota, so it is a capacity gate, not just a formality. Start it the moment the domain resolves.

## 5. Worth doing, not strictly blocking
- [ ] Public landing page with pricing (the in-app `/billing` page exists; confirm/build a marketing front door).
- [ ] Visual pass (Inter fonts, restrained accent) for a premium first impression.

---

## Critical path
Buy the domain + sign up for the four accounts now. Then Claude builds and deploys
hosting over a few sessions, while the YouTube audit runs in parallel (the
multi-week wait). Then flip the live Stripe keys and onboard the first customer.
The audit is almost certainly the critical path, which is exactly why buying the
domain is the thing to do today.
