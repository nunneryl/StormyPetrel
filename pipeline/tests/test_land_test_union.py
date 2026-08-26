"""The land test is the union over EVERY dataset and EVERY wave variable.

THE DEFECT. `_cell_is_water` used to be handed ONE (dataset, variable) pair by
`_find_wave_dataset` — the first match in cfgrib grouping order — and tested only that.

Measured against the real gyx 2026-08-24 18Z CG1 cycle: cfgrib splits that file into two
datasets carrying the SAME six variables ['ws','wdir','swh','shts','dirpw','perpw'].
ds[0] is dims={'latitude':103,'longitude':129} with NO step dimension — a scalar step 0,
valid_time 2026-08-24T18:00, i.e. the ANALYSIS hour f000. ds[1] is dims={'step':144,...}.
`_find_wave_dataset` returned ds[0] with 'swh', so the predicate evaluated an array of
shape (1,) and the land verdict for the whole cycle rested on ONE forecast hour — while
the docstring claimed land was "all-NaN across the step axis", which ds[0] does not have.

Short Sands Beach York's baked node is cell (li=34, lj=38). swh at ds[0] is NaN, so
production called it LAND. Across ds[1]'s 144 steps that same cell holds 69 finite values,
swh 0.3401 to 0.7895 m — a rejected node carrying two thirds of a forecast.

Not a rare edge: 108 of 13287 gyx cells (0.81%) are NaN at step 0 and finite at some later
step, and 95% of those have a wet neighbour at step 0 (mean 3.37, versus 0.19 for
stable-land cells). They are the wet/dry boundary, which is where surf spots sit. Step 0 is
not anomalously sparse either — 6629 finite cells against a forecast-step range of 6605 to
6736. ANY single step is a coin flip for a boundary cell.

AND IT RUNS BOTH WAYS, which is why "read the multi-step dataset instead" is equally wrong:
cell (51,15) on the same grid — 43.5501N, 288.7070E — is finite at step 0 (swh 0.0480) and
all-NaN across every one of the 144 forecast steps. Only the union is correct in both
directions, and both directions are pinned below.

FIXTURES ARE SYNTHETIC xarray Datasets shaped like cfgrib's two groups. No GRIB is needed
to run this file. Every expected value is written literally in the fixture beside the
assertion; nothing is obtained by calling the function under test.

Run: python -m pipeline.tests.test_land_test_union   (or pytest)
"""
from __future__ import annotations

import numpy as np
import xarray as xr

from pipeline.forecast import nwps

NAN = float("nan")

# A 3x3 nest. Ascending south->north / west->east, the cfgrib convention the reader
# assumes. The cell under test throughout is (li=1, lj=1) — the centre.
LATS = np.array([36.30, 36.35, 36.40])
LNGS = np.array([-75.90, -75.85, -75.80])
CENTRE = (1, 1)


def _scalar_step(values, var="swh"):
    """cfgrib's ds[0]: dims (latitude, longitude), `step` a SCALAR coordinate.

    `values` is a 3x3 nested list. isel(latitude=, longitude=) on this yields a 0-d
    DataArray, which the predicate flattens to shape (1,) — the exact shape production
    was evaluating for every land verdict."""
    return xr.Dataset(
        {var: (("latitude", "longitude"), np.array(values, dtype="float64"))},
        coords={"latitude": LATS, "longitude": LNGS,
                "step": np.timedelta64(0, "h")},
    )


def _multi_step(values, var="swh"):
    """cfgrib's ds[1]: dims (step, latitude, longitude).

    `values` is a list of 3x3 nested lists, one per step."""
    arr = np.array(values, dtype="float64")
    return xr.Dataset(
        {var: (("step", "latitude", "longitude"), arr)},
        coords={"step": np.arange(1, arr.shape[0] + 1).astype("timedelta64[h]"),
                "latitude": LATS, "longitude": LNGS},
    )


def _grid(centre):
    """3x3 with `centre` at (1,1) and a fixed 0.5 everywhere else, so only the centre
    cell is ever under test and the surrounding values never change a verdict."""
    return [[0.5, 0.5, 0.5], [0.5, centre, 0.5], [0.5, 0.5, 0.5]]


# --------------------------------------------------------------------------- #
# 1 — each dataset shape, alone                                                #
# --------------------------------------------------------------------------- #
def test_a_scalar_step_dataset_alone_with_a_nan_cell_is_land():
    """One dataset, no step dimension, centre NaN. Nothing else can vouch for the cell,
    so the verdict is LAND. Expected: False."""
    ds = _scalar_step(_grid(NAN))
    assert nwps._cell_is_water([ds], *CENTRE) is False


def test_a_scalar_step_dataset_alone_with_a_finite_cell_is_water():
    """Same shape, centre 0.0480 — the value at the real reverse cell (51,15). A single
    finite value at a single step is enough. Expected: True."""
    ds = _scalar_step(_grid(0.0480))
    assert nwps._cell_is_water([ds], *CENTRE) is True


def test_a_multi_step_dataset_alone_finite_at_some_steps_is_water():
    """One dataset with a step axis; the centre is NaN at steps 1 and 3, finite at step 2.
    Land is ALL-NaN, not ANY-NaN, so a partly-masked cell is still water. Expected: True.

    The 0.3401 is the low end of Short Sands Beach York's real range across ds[1]."""
    ds = _multi_step([_grid(NAN), _grid(0.3401), _grid(NAN)])
    assert nwps._cell_is_water([ds], *CENTRE) is True


def test_a_multi_step_dataset_alone_nan_at_every_step_is_land():
    """Same shape, centre NaN at all three steps. Expected: False."""
    ds = _multi_step([_grid(NAN), _grid(NAN), _grid(NAN)])
    assert nwps._cell_is_water([ds], *CENTRE) is False


# --------------------------------------------------------------------------- #
# 2 — both datasets present. These two are the regression.                     #
# --------------------------------------------------------------------------- #
def test_scalar_step_nan_but_multi_step_finite_is_water():
    """THE SHORT SANDS BEACH YORK CASE, and the one that fails against the old code.

    ds[0] (analysis hour) is NaN at the cell; ds[1] carries finite values. Old behaviour:
    _find_wave_dataset returns ds[0] first, the array is shape (1,) and all-NaN, verdict
    LAND — the node rejected, the warning raised, the ring walk taken, all while 69 of
    144 forecast hours held real data. Union behaviour: WATER.

    Expected: True. Values are the real observed endpoints, 0.3401 and 0.7895 m.
    """
    ds0 = _scalar_step(_grid(NAN))
    ds1 = _multi_step([_grid(0.3401), _grid(NAN), _grid(0.7895)])
    assert nwps._cell_is_water([ds0, ds1], *CENTRE) is True
    # order must not matter — the union is symmetric
    assert nwps._cell_is_water([ds1, ds0], *CENTRE) is True


def test_scalar_step_finite_but_multi_step_all_nan_is_water():
    """THE REVERSE CELL — real cell (51,15), 43.5501N 288.7070E, swh 0.0480 at step 0 and
    all-NaN across all 144 forecast steps.

    This is why the fix is not "read ds[1] instead of ds[0]": reading all steps is NOT a
    strict superset of reading step 0. A predicate that consulted only the multi-step
    dataset would call this cell LAND and lose the one hour of data it does have.

    Expected: True.
    """
    ds0 = _scalar_step(_grid(0.0480))
    ds1 = _multi_step([_grid(NAN), _grid(NAN), _grid(NAN)])
    assert nwps._cell_is_water([ds0, ds1], *CENTRE) is True
    assert nwps._cell_is_water([ds1, ds0], *CENTRE) is True


def test_nan_in_both_datasets_is_land():
    """Genuine land: nothing finite anywhere, in either group. Expected: False.

    This is the case that must NOT become water — a union that returned True whenever it
    saw any dataset at all would make the land test useless, and this pins against it."""
    ds0 = _scalar_step(_grid(NAN))
    ds1 = _multi_step([_grid(NAN), _grid(NAN), _grid(NAN)])
    assert nwps._cell_is_water([ds0, ds1], *CENTRE) is False


# --------------------------------------------------------------------------- #
# 3 — variable coverage                                                        #
# --------------------------------------------------------------------------- #
def test_a_dataset_whose_only_wave_variable_is_shts_is_read():
    """The union is over every name in _WAVE_VARS_FOR_LAND_CHECK, not just 'swh'. A group
    carrying only 'shts' (significant height of total swell) must still be consulted.
    Expected: True."""
    ds = _scalar_step(_grid(1.25), var="shts")
    assert nwps._cell_is_water([ds], *CENTRE) is True
    assert "shts" in nwps._WAVE_VARS_FOR_LAND_CHECK


def test_a_nan_shts_cell_is_land_not_trusted_as_unreadable():
    """The half of the shts case that has teeth. The test above cannot tell "recognised
    shts and found it finite" from "did not recognise shts, so fell through to trust the
    input" — both answer True. This one separates them: shts is the only wave variable and
    the cell is NaN, so a predicate that reads shts says LAND, while one that does not
    recognise the name sees no wave data at all and says water.

    Expected: False."""
    ds = _scalar_step(_grid(NAN), var="shts")
    assert nwps._cell_is_water([ds], *CENTRE) is False


def test_every_name_in_the_check_list_is_actually_consulted():
    """Each of the five recognised names, alone in a dataset, with the cell NaN. Every one
    must produce LAND — i.e. each name is genuinely read, not silently ignored and rescued
    by the trust-the-input fallback. Expected: False for all five."""
    for name in ("swh", "htsgw", "shts", "shww", "swell"):
        ds = _scalar_step(_grid(NAN), var=name)
        assert nwps._cell_is_water([ds], *CENTRE) is False, name
    # and a name that is NOT a wave variable falls through to trust-the-input
    assert nwps._cell_is_water([_scalar_step(_grid(NAN), var="ws")], *CENTRE) is True


def test_a_wave_variable_in_the_second_dataset_is_reached():
    """First dataset has NO wave variable at all; the second does, and it is finite.
    First-match on datasets would stop at neither — but a loop that returned after the
    first dataset carrying anything would miss it. Expected: True."""
    ds_wind = _scalar_step(_grid(3.0), var="ws")     # not a wave variable
    ds_wave = _scalar_step(_grid(0.75), var="swh")
    assert nwps._cell_is_water([ds_wind, ds_wave], *CENTRE) is True


def test_no_wave_variable_in_any_dataset_is_water():
    """TRUST THE INPUT. With nothing to read we cannot tell land from water, and calling
    it land would discard the spot entirely. Preserved from the old behaviour, where
    _find_wave_dataset returning (None, None) made both callers treat the point as usable.

    Two wind-only datasets, and the cell is finite in neither sense that matters.
    Expected: True."""
    ds0 = _scalar_step(_grid(3.0), var="ws")
    ds1 = _multi_step([_grid(4.0), _grid(4.5), _grid(5.0)], var="wdir")
    assert nwps._cell_is_water([ds0, ds1], *CENTRE) is True


def test_an_empty_dataset_list_is_water():
    """Degenerate case of the same rule: nothing to read at all. Expected: True."""
    assert nwps._cell_is_water([], *CENTRE) is True


# --------------------------------------------------------------------------- #
# 3b — a variable that cannot be READ is absence of evidence, not evidence     #
#      of land                                                                  #
# --------------------------------------------------------------------------- #
def _unreadable(var="swh"):
    """A dataset whose wave variable has no latitude/longitude dims at all, so
    .isel(latitude=, longitude=) raises rather than returning a value."""
    return xr.Dataset({var: (("x", "y"), np.full((3, 3), 0.9))},
                      coords={"x": [0, 1, 2], "y": [0, 1, 2]})


def test_a_variable_that_cannot_be_read_does_not_veto_a_later_finite_one():
    """The first dataset's wave variable raises on isel; the second is finite. A raise is
    the ABSENCE of evidence, so the loop must carry on and find the finite value rather
    than short-circuit to land. Expected: True."""
    ds_bad = _unreadable()
    ds_good = _scalar_step(_grid(0.88))
    assert nwps._cell_is_water([ds_bad, ds_good], *CENTRE) is True


def test_a_single_unreadable_wave_variable_is_land_not_water():
    """The other half of the same rule, and the one that stops "absence of evidence" from
    becoming "trust the input". The dataset ADVERTISES a wave variable, so this is not the
    no-wave-data case — it is a wave variable we failed to read, and with nothing else to
    consult the verdict is land, exactly as the old bare `except: return False` gave.
    Expected: False."""
    assert nwps._cell_is_water([_unreadable()], *CENTRE) is False


def test_a_dataset_on_a_different_grid_is_not_consulted():
    """Indices are grid-relative. The reference grid is the first wave-bearing dataset
    (3x3, centre NaN). A second dataset on a 5x5 grid has a finite value at ITS (1,1) —
    but that is a different place on the earth, so it must not vouch for this cell.

    Expected: False. Without the shape guard this would read the 5x5's (1,1) and answer
    True for a cell it has never seen."""
    ds_ref = _scalar_step(_grid(NAN))
    other = xr.Dataset(
        {"swh": (("latitude", "longitude"), np.full((5, 5), 0.77))},
        coords={"latitude": np.linspace(36.0, 37.0, 5),
                "longitude": np.linspace(-76.0, -75.0, 5),
                "step": np.timedelta64(0, "h")},
    )
    assert nwps._cell_is_water([ds_ref, other], *CENTRE) is False


# --------------------------------------------------------------------------- #
# 4 — the surrounding cells are unaffected                                     #
# --------------------------------------------------------------------------- #
def test_the_union_does_not_turn_every_cell_into_water():
    """A whole-grid check against a fixture where only the centre is masked in ds[0] and
    only the corner is masked in both. Expected, written out cell by cell:

        (0,0) 0.5 / finite in ds[1]  -> True
        (1,1) NaN / finite in ds[1]  -> True   (rescued by the union)
        (2,2) NaN / NaN in ds[1]     -> False  (genuine land)
    """
    g0 = [[0.5, 0.5, 0.5], [0.5, NAN, 0.5], [0.5, 0.5, NAN]]
    g1 = [[0.6, 0.6, 0.6], [0.6, 0.61, 0.6], [0.6, 0.6, NAN]]
    ds0 = _scalar_step(g0)
    ds1 = _multi_step([g1, g1])
    assert nwps._cell_is_water([ds0, ds1], 0, 0) is True
    assert nwps._cell_is_water([ds0, ds1], 1, 1) is True
    assert nwps._cell_is_water([ds0, ds1], 2, 2) is False


# --------------------------------------------------------------------------- #
# 5 — the wrapper routes through the same predicate                            #
# --------------------------------------------------------------------------- #
def test_the_baked_node_wrapper_gets_the_union_verdict():
    """_baked_node_is_water must inherit the union, not keep a private copy. The baked
    node at (36.35, -75.85) resolves to the centre cell (1,1): NaN in ds[0], finite in
    ds[1]. Expected: True — the exact rejection this change removes.

    And a cell that is genuinely land in both must still come back False, so the wrapper
    is not simply returning True. (36.40, -75.80) is index (2,2), NaN in both.
    """
    g0 = [[0.5, 0.5, 0.5], [0.5, NAN, 0.5], [0.5, 0.5, NAN]]
    g1 = [[0.6, 0.6, 0.6], [0.6, 0.4210, 0.6], [0.6, 0.6, NAN]]
    dss = [_scalar_step(g0), _multi_step([g1, g1])]
    assert nwps._baked_node_is_water(dss, 36.35, -75.85) is True
    assert nwps._baked_node_is_water(dss, 36.40, -75.80) is False


def test_the_ring_walk_gets_the_union_verdict():
    """_find_offshore_point's closure must route through the same predicate. The nominal
    nearest cell to (36.35, -75.85) is the centre (1,1) — NaN in ds[0], finite in ds[1].
    Under the union it is already water, so the walk accepts it at radius 0 and returns
    that cell's own coordinates.

    Expected literally: (36.35, -75.85, 0) — lat, lng, fallback_cells=0.
    """
    g0 = [[0.5, 0.5, 0.5], [0.5, NAN, 0.5], [0.5, 0.5, 0.5]]
    g1 = [[0.6, 0.6, 0.6], [0.6, 0.55, 0.6], [0.6, 0.6, 0.6]]
    dss = [_scalar_step(g0), _multi_step([g1, g1])]
    got = nwps._find_offshore_point(dss, 36.35, -75.85)
    assert got is not None
    lat, lng, cells = got
    assert round(lat, 2) == 36.35 and round(lng, 2) == -75.85, got
    assert cells == 0, got


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} land-test-union checks passed")


if __name__ == "__main__":
    _run_all()
