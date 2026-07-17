# YouTube API Verification & Audit Package

Everything needed to take AmbiHub from "uploads locked private, 100 users,
~6 uploads/day" to a compliant multi-tenant publisher. Two separate processes,
both required; the audit is the longest external clock in the launch plan.
File both as early as possible.

| Process | Unlocks | Timeline |
|---|---|---|
| 1. Google OAuth sensitive-scope verification | Removes 100-user cap + "unverified app" warning screen | ~10 days published, longer in practice |
| 2. YouTube API Services compliance audit + quota extension | Lifts the private-video lock on API uploads; raises the 10,000 units/day quota (~6 uploads/day default) | No SLA; weeks to months |

---

## Prerequisites (blockers for filing, need CARLOS input)

- [x] **Public domain** for the product: `ambihub.ai`, DECIDED 2026-07-17
      (AmbiHub; ambihub.io optional defensive grab). Can be parked on existing
      hosting from the other apps, only two static pages are required to file.
- [ ] **Domain verified in Google Search Console** under the same Google
      account that owns the Cloud project.
- [ ] **Privacy policy live** at `https://ambihub.ai/privacy`; content already
      exists in `dashboard/templates/privacy.html` (includes the YouTube API
      Services section); needs static export to the public domain.
- [ ] **Terms live** at `https://ambihub.ai/terms`, same, from `terms.html`.
- [ ] **OAuth consent screen** (Cloud Console → APIs & Services): app name
      AmbiHub, support email, logo, homepage `https://ambihub.ai`,
      privacy + terms URLs above, authorized domain `ambihub.ai`.
- [ ] **Unlisted demo video** (script below) uploaded to any YouTube channel.

## Scopes requested

- `https://www.googleapis.com/auth/youtube.upload` (sensitive)
- `https://www.googleapis.com/auth/youtube` (sensitive)

### Draft justification, youtube.upload

> AmbiHub is a video-production tool for YouTube creators. After a user
> connects their own channel via OAuth, the app renders long-form ambient
> music videos the user has configured and uploads them to that user's own
> channel on the user's schedule. The youtube.upload scope is used solely to
> upload the finished video file, set its title/description/tags, and set a
> scheduled publish time chosen by the user. Videos are uploaded as private
> and become public only at the user's scheduled time, preserving user
> control. No content is uploaded to any channel other than the
> authenticated user's own.

### Draft justification, youtube

> Used for three narrow functions on the authenticated user's own channel:
> (1) setting the custom thumbnail for a video the app just uploaded
> (thumbnails.set), (2) reading back the scheduled publish time of the
> app's own uploads to keep the in-app calendar accurate (videos.list),
> and (3) resolving the user's channel identity at connection time so the
> app can label their workspace. The app does not read, modify, or manage
> any content the app did not create.

## Demo video script (3–4 minutes, unlisted, screen recording)

1. Show `https://ambihub.ai` homepage with the app name visible.
2. Log into AmbiHub → click "Connect YouTube."
3. Show the FULL Google consent screen with the URL bar visible, app name and both
   scopes readable. Approve.
4. Create a video project; show the user choosing the publish schedule.
5. Show the finished upload appearing in YouTube Studio as **Private
   (Scheduled)**, demonstrating uploads target only the connected channel
   and stay private until the user's chosen time.
6. Show the disconnect/revoke path (Google account permissions page).

## Compliance audit (YouTube API Services, Audit & Quota Extension Form)

Draft answers to the core questions:

- **What does your application do?** AmbiHub automates production of
  long-form ambient/focus-music videos for YouTube creators: AI-generated
  imagery and music are assembled into videos on the creator's own machine
  or our hosted service, then uploaded to the creator's own connected
  channel on their configured schedule.
- **API Services used and why:** videos.insert (upload the user's finished
  video, private with user-chosen publishAt), thumbnails.set (the video's
  thumbnail), videos.list (read back our own uploads' status/publish time),
  channels.list (identify the connected channel at setup).
- **User data handling:** OAuth tokens are stored encrypted per user and
  used only to act on that user's channel at their direction. We store no
  YouTube data beyond the IDs/status of videos our app uploaded. No data is
  shared with third parties; no analytics are derived from YouTube data.
  Users can disconnect at any time, which deletes stored tokens.
- **Compliance with Developer Policies:** one Google Cloud project per
  service; quota used only for user-initiated production; no artificial
  inflation of metrics (uploads are private until the user's publish time);
  required attribution and API ToS links present in the app's privacy page.
- **Quota extension request:** default 10,000 units/day supports ~6 uploads
  (≈1,600 units each). Request **100,000 units/day** to support the first
  ~60 daily customer uploads, with per-customer scheduling spreading load
  across the day. (Recalculate before filing if customer projections
  change: target = expected daily uploads × 1,600 × 1.5 headroom.)

## Filing order

1. Carlos picks `ambihub.ai` → static privacy/terms pages go live → Search
   Console verification.
2. Complete OAuth consent screen → submit sensitive-scope verification with
   demo video + justifications above.
3. Submit the YouTube Audit & Quota Extension Form immediately after (do
   not wait for #2 to conclude, the audit is the longer clock).
4. Until both clear: keep customer count ≤ test users, and treat the
   private-lock as a hard launch gate for any "publishes for you" tier.
