# G3 geometry fixtures

These non-proprietary fixtures are generated from the adjacent build123d Python sources solely for
official Geometry and Prime live capability tests.

| Fixture | Exact dimensions | Coordinate convention | Expected topology |
| --- | --- | --- | --- |
| `g3_box.step` | 2000 x 3000 x 4000 mm | XY-centered, base Z=0 | 1 solid, 6 faces |
| `g3_cylinder.step` | radius 500 mm, height 2000 mm | XY-centered, base Z=0 | 1 solid, 3 faces |

The checked STEP geometry was validated with independent measurement and snapshot workflows. The
adjacent `manifest.json` binds each asset to its project-owned generator by SHA-256. These files are
test inputs, not a fallback source of Geometry Graph values.
