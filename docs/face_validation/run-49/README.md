# Face-height follow-up, run 49

- Run: sw1-raycast run 49, mode `face-followup`, commit 538be6d94aea4ee57126a8a33d7a8ddae673b247,
  2026-10-26 18:12Z to 18:54Z. Suspects window (run 48's): 2026-10-12 17:02Z to 2026-10-26 17:02Z.
- Artifact `face-followup-49`, sha256 a81506adcbbb50c20dfc781ce31ddc6791b82ca5d2870ff3eb1910f24ed70572
  (expires 2027-01-24). No section failed.

## Files

- `face_followup.json`, `suspects_vs_buoys.csv`, `coastline.csv`: the artifact's files, byte for
  byte.
- `depths.json`, made by `depths.py` on 2026-10-26 in the analysis session, not on the runner.
  On the runner every ETOPO request came back empty, and `etopo_depth_m` turned each failure
  into a depth of None without saying why; the same requests answer from the analysis session.
  It gives the depth at each NWPS sample point and at the spot's own cell, the nearest ETOPO cell
  within 30 deg of the facing that is at least 10 m deep, and the depth at section 4's two
  seaward cells.

## How to read it

- Section 1's buoys are 20 to 200 km from the spots. The heights are a rough guide; the periods
  are not (a period does not shorten between a buoy and the beach).
- Section 2 labels every sample point open ocean. Its "nearest open ocean" is 0.25 km from the
  spot in every case, the first ring searched, so it says the water off each spot is open, not
  where a better sample point is. `depths.json` answers that question by depth instead.
- Section 4 rests on 66 hours (Duck Pier) and 67 hours (Jetty Park): the cycles NOMADS held on
  2026-10-26, nearly all on days over 1.5 m at Duck Pier.
- Doheny's MOP point, OC131 (33.45168, -117.691, 10.0 m), faces 170 deg and sits 1.26 km
  west-northwest of CDIP 284. Read from its MOP nowcast file's metadata in the analysis session.
