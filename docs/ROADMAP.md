# Roadmap

Each phase has an exit criterion so you know when to move on. Don't build phase N+1 before
phase N's criterion is met.

## Phase 0: scaffold (this repo)
Upload, taxonomy, filtering, clips, review, UI, with simulated events.
**Exit:** tests pass, UI clicked through end to end.

## Phase 1: validate demand (parallel with everything, start now)
Show the scaffold with demo data to 3-5 clubs/county coaches. Ask what they tag by hand today,
how long it takes, and what they'd pay. Get real Veo match files from 2-3 of them.
**Exit:** at least 2 clubs commit to trialling it; a ranked list of the 5 events they care most about.

## Phase 2: footage + calibration
Confirm Veo file formats and sizes; add a transcoded proxy for smooth playback; calibrate the
pitch from Veo footage (fixed camera).
**Exit:** pixel-to-pitch mapping accurate enough to place events in thirds / inside-45 zones.

## Phase 3: tracking and possession
Detect and track players and ball, assign teams, derive possession.
**Exit:** possession timeline agrees with a hand-tagged match above an agreed threshold (e.g. 90% of time).

## Phase 4: first real events
Turnovers, kickouts, shots from tracking rules, with confidence scores.
**Exit:** on 3 held-out matches, recall high enough that coaches would rather correct than tag from scratch.

## Phase 5: classification and review
Outcome classifier (goal/point/wide/saved...) trained on your labelled data; review queue UI
(step through low-confidence events with the keyboard).
**Exit:** reviewing a full match takes minutes, not hours.

## Phase 6: production
Auth and per-club isolation, Postgres, object storage, queue + GPU workers, billing, reel export,
pattern reports (kickout destinations, shot maps, turnover heatmaps, scores from turnovers).

## Risks and open questions

- **Veo access:** no automated import is built; users download and upload by hand. Check whether
  Veo offers an API or partner integration, and read their terms before building anything automated.
  Clubs should own or be licensed to use the footage they upload.
- **Accuracy trust:** a tool that mislabels loses coaches fast. Keep the review loop first-class and
  never present simulated or low-confidence output as fact.
- **Training data:** public GAA datasets are scarce; budget real time for labelling.
- **Model/dataset licences:** verify before commercial use.
- **Unit economics:** GPU minutes per match vs price per club per season.
- **Players' data:** the footage identifies people (often minors at underage level); plan privacy,
  consent and GDPR handling before onboarding clubs.
