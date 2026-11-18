from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
import math
from pathlib import Path
import tempfile

from ....converting.engine.geometry.polygons import path_bounds
from ....converting.engine.units import mm_to_pt, pt_to_mm
from .geometry import place_paths, paths_collide, orient_paths, bounds_mm
from .models import OrientedItem, PlacedItem, RowLayout, SheetLayout

ARTBOARD_WIDTH_MM = 400.0
ARTBOARD_HEIGHT_MM = 300.0
PAGE_MARGIN_X_MM = 0.5   # 전역 가로 마진 0.5mm
PAGE_MARGIN_Y_MM = 0.5   # 전역 세로 마진 0.5mm
PAGE_MARGIN_MM = 0.5
ROW_GAP_MM = 0.25
# Minimum required gap between adjacent items' safety outlines.
# Safety outlines already include a +0.125mm offset, so requiring a 0.25mm
# safety gap guarantees the rendered artwork outlines are also >= 0.25mm apart.
#
# Raised to 0.5mm for cut artwork: the blade needs real clearance, and a
# 0.31mm cutline-to-cutline gap measured in practice was too tight to plot
# reliably. With the safety outline contributing +0.125mm on each side this
# holds the rendered cutlines at least 0.5mm apart.
MIN_SAFETY_GAP_MM = 0.5
STEP_MM = 0.5

DEFAULT_BEAM_WIDTH = 4
DEFAULT_ROW_CANDIDATE_LIMIT = 30
MAX_BBOX_OVERLAP_PROBE_MM = 60.0
HYBRID_COARSE_STEP_MM = 5.0
HYBRID_MAX_COARSE_STEP_MM = 25.0
HYBRID_BINARY_THRESHOLD_MM = 0.5

# Preset long side definitions (final outline long side)
# stand: 148mm (current implementation), keyring: 65mm
# mixed: uses stand sheet layout (2 rows) but allows keyring pairs stacked vertically as stand-like items
PRESET_LONG_SIDE_MM: dict[str, float] = {
    "stand": 148.0,
    "keyring": 65.0,
    "mixed": 148.0,
}
DEFAULT_PRESET = "auto"
# Classification thresholds (with tolerance margins):
#   long side < 66mm            -> keyring
#   66mm <= long side < 149mm   -> stand
KEYRING_THRESHOLD_MM = 66.0
STAND_THRESHOLD_MM = 149.0
# In mixed preset, keyrings are stacked vertically into stand-like items;
# the total stack height (incl. row gaps) must not exceed 148mm, and the
# packer keeps stacking as many keyrings as fit.
MIXED_STACK_MAX_MM = 148.0

PairKey = tuple[str, int, str, int]


@dataclass(frozen=True)
class PackingOptions:
    """Controls the bounded row search and pair-offset cache construction."""

    beam_width: int = DEFAULT_BEAM_WIDTH
    row_candidate_limit: int = DEFAULT_ROW_CANDIDATE_LIMIT
    pair_precompute: bool = True
    pair_precompute_workers: int = 1
    use_hybrid_search: bool = True
    max_bbox_overlap_probe_mm: float = MAX_BBOX_OVERLAP_PROBE_MM
    preset: str = DEFAULT_PRESET  # "auto" → detected from item sizes; or keyring/stand/mixed


@dataclass
class PairOffsetCache:
    values: dict[PairKey, float | None] = field(default_factory=dict)


# -------------------------------------------------------------------
# Mixed preset: keyring vertical pairing logic
# -------------------------------------------------------------------
def _is_keyring_oriented_item(item: OrientedItem) -> bool:
    """Heuristic: height ~65mm is keyring, ~148mm is stand."""
    # Use height_mm; composite items already have height > 100 but are treated as stand-like
    return item.height_mm < KEYRING_THRESHOLD_MM and not getattr(item, "is_composite", False)


def _make_rotated_item(item: OrientedItem, rot_deg: int) -> OrientedItem:
    """Return a copy of *item* with its paths rotated by ``rot_deg`` (90/180/270).

    The original orientation options only cover portrait (long side vertical);
    this helper builds landscape (90°) variants and the 180° counterparts of
    composite components that do not exist in the original option list.
    """
    outline, ob = orient_paths(item.outline_paths_pt, rot_deg)
    safety, sb = orient_paths(item.safety_paths_pt, rot_deg)
    sw, sh = bounds_mm(sb)
    return OrientedItem(
        source=item.source,
        instance_id=item.instance_id,
        copy_index=item.copy_index,
        rotation_deg=(item.rotation_deg + rot_deg) % 360,
        outline_paths_pt=outline,
        safety_paths_pt=safety,
        outline_bounds_pt=ob,
        safety_bounds_pt=sb,
        width_mm=sw,
        height_mm=sh,
        safety_sub_bounds_pt=tuple(path_bounds([p]) for p in safety),
    )


def _optimize_stack_width(
    group: list[tuple[str, OrientedItem]],
    portrait_by_iid: dict[str, OrientedItem],
) -> None:
    """Flip wide stack components back to portrait when the item count holds.

    ``group`` is a list of (iid, current_item). Repeatedly take the component
    that defines the stack width and try its portrait variant (smaller width,
    larger height); keep the flip if the stack still fits under
    MIXED_STACK_MAX_MM. The item count never changes.
    """
    changed = True
    while changed:
        changed = False
        cur_w = max(it.width_mm for _, it in group)
        # Flip ALL width-driving components to portrait at once. A single flip
        # cannot shrink the width (another landscape component still drives
        # it), so the whole driver set must switch together.
        new_group = list(group)
        for idx, (iid, it) in enumerate(group):
            if it.width_mm < cur_w - 1e-9:
                continue  # not the width driver
            p = portrait_by_iid.get(iid)
            if p is not None and p is not it:
                new_group[idx] = (iid, p)
        new_h = sum(it.height_mm for _, it in new_group) + ROW_GAP_MM * (len(new_group) - 1)
        if new_h <= MIXED_STACK_MAX_MM + 1e-9:
            new_w = max(it.width_mm for _, it in new_group)
            if new_w < cur_w - 1e-9:
                group[:] = new_group
                changed = True


def _create_vertical_composite(
    items: list[OrientedItem], instance_id: str
) -> OrientedItem | None:
    """Stack N keyring items vertically into one stand-like composite.

    Items are centered horizontally; components are laid out top-to-bottom
    with ROW_GAP between them. The total height (incl. gaps) must not exceed
    MIXED_STACK_MAX_MM (148mm), otherwise None is returned.
    """
    total_h = sum(it.height_mm for it in items) + ROW_GAP_MM * (len(items) - 1)
    if total_h > MIXED_STACK_MAX_MM + 1e-9:
        return None

    comp_width = max(it.width_mm for it in items)
    comp_height = total_h
    combined_safety: list[PathPoints] = []
    combined_outline: list[PathPoints] = []
    components: list[tuple[OrientedItem, float, float]] = []
    y = 0.0
    for it in items:
        x = (comp_width - it.width_mm) / 2.0
        combined_safety.extend(place_paths(it.safety_paths_pt, x, y))
        combined_outline.extend(place_paths(it.outline_paths_pt, x, y))
        components.append((it, x, y))
        y += it.height_mm + ROW_GAP_MM

    try:
        safety_bounds = path_bounds(combined_safety)
    except Exception:
        safety_bounds = (0.0, 0.0, comp_width, comp_height)
    try:
        outline_bounds = path_bounds(combined_outline)
    except Exception:
        outline_bounds = safety_bounds

    return OrientedItem(
        source=items[0].source,  # dummy; renderer uses composite_components
        instance_id=instance_id,
        copy_index=1,
        rotation_deg=0,
        outline_paths_pt=combined_outline,
        safety_paths_pt=combined_safety,
        outline_bounds_pt=outline_bounds,
        safety_bounds_pt=safety_bounds,
        width_mm=comp_width,
        height_mm=comp_height,
        safety_sub_bounds_pt=tuple(path_bounds([p]) for p in combined_safety),
        composite_components=tuple(components),
        is_composite=True,
    )


def _create_composite_180(
    comp: OrientedItem, options_by_instance: dict[str, list[OrientedItem]]
) -> OrientedItem:
    """Build the 180°-rotated version of a composite.

    Components are taken in reverse order and each is replaced by its 180°-
    rotated counterpart, then re-stacked so the rendered safety paths match
    the component positions (mirrors the original 2-item logic for N items).
    """
    new_items: list[OrientedItem] = []
    for comp_item, _x, _y in reversed(comp.composite_components):
        rot180 = (comp_item.rotation_deg + 180) % 360
        found = None
        for opt in options_by_instance.get(comp_item.instance_id, []):
            if opt.rotation_deg == rot180:
                found = opt
                break
        if found is None:
            # Landscape/rotated variants are not part of the original options;
            # build the 180° counterpart directly.
            found = _make_rotated_item(comp_item, 180)
        new_items.append(found)

    total_h = sum(it.height_mm for it in new_items) + ROW_GAP_MM * (len(new_items) - 1)
    comp_width = max(it.width_mm for it in new_items)
    combined_safety: list[PathPoints] = []
    combined_outline: list[PathPoints] = []
    components: list[tuple[OrientedItem, float, float]] = []
    y = 0.0
    for it in new_items:
        x = (comp_width - it.width_mm) / 2.0
        combined_safety.extend(place_paths(it.safety_paths_pt, x, y))
        combined_outline.extend(place_paths(it.outline_paths_pt, x, y))
        components.append((it, x, y))
        y += it.height_mm + ROW_GAP_MM

    try:
        sb = path_bounds(combined_safety)
    except Exception:
        sb = (0.0, 0.0, comp_width, total_h)
    try:
        ob = path_bounds(combined_outline)
    except Exception:
        ob = sb

    return OrientedItem(
        source=new_items[0].source,
        instance_id=comp.instance_id,
        copy_index=1,
        rotation_deg=180,
        outline_paths_pt=combined_outline,
        safety_paths_pt=combined_safety,
        outline_bounds_pt=ob,
        safety_bounds_pt=sb,
        width_mm=comp_width,
        height_mm=total_h,
        safety_sub_bounds_pt=tuple(path_bounds([p]) for p in combined_safety),
        composite_components=tuple(components),
        is_composite=True,
    )


def _build_mixed_composites(
    options_by_instance: dict[str, list[OrientedItem]],
) -> tuple[dict[str, list[OrientedItem]], dict[str, str]]:
    """For mixed preset: group keyring instances into vertical stacks.

    - Keyrings (long side < 66mm) are sorted by height ascending and greedily
      grouped so each stack's total height (incl. row gaps) stays within
      MIXED_STACK_MAX_MM (148mm) — i.e. as many keyrings as fit per stack.
    - Each multi-item group becomes a stand-like composite (0° and 180°).
    - Stand items are kept as-is; leftover single keyrings stay singles.
    - Returns (new_options, pair_map: original iid -> composite iid).
    """
    keyring_iids: list[str] = []
    stand_iids: list[str] = []
    for iid, opts in options_by_instance.items():
        if not opts:
            continue
        if _is_keyring_oriented_item(opts[0]):
            keyring_iids.append(iid)
        else:
            stand_iids.append(iid)

    # Each keyring gets two orientation variants: portrait (long side vertical)
    # and landscape (short side vertical) so stacks can pack more items per 148mm.
    entries: list[tuple[str, OrientedItem, OrientedItem]] = []  # (iid, portrait, landscape)
    for iid in keyring_iids:
        p_item = options_by_instance[iid][0]
        l_item = _make_rotated_item(p_item, 90)
        entries.append((iid, p_item, l_item))

    # Sort by landscape height (= short side) ascending so each stack fills up
    # to 148mm with the maximum number of keyrings.
    entries.sort(key=lambda e: e[2].height_mm)

    new_options: dict[str, list[OrientedItem]] = {}
    for iid in stand_iids:
        new_options[iid] = options_by_instance[iid]

    pair_map: dict[str, str] = {}
    composite_count = 0
    i = 0
    n = len(entries)
    while i < n:
        # Greedy: fill a stack with as many landscape keyrings as fit.
        group: list[tuple[str, OrientedItem]] = []  # (iid, chosen_item)
        total_h = 0.0
        while i < n:
            item = entries[i][2]  # landscape variant (short side vertical)
            next_h = total_h + (ROW_GAP_MM if group else 0.0) + item.height_mm
            if next_h > MIXED_STACK_MAX_MM + 1e-9:
                break
            group.append((entries[i][0], item))
            total_h = next_h
            i += 1

        if len(group) < 2:
            # Single keyring (or no fit): keep it as a plain item.
            for iid, _item in group:
                new_options[iid] = options_by_instance[iid]
                pair_map[iid] = iid
            continue

        # Width optimization: while keeping the item count, flip the widest
        # component(s) back to portrait when the stack still fits under 148mm.
        portrait_by_iid = {iid: p for iid, p, _l in entries}
        _optimize_stack_width(group, portrait_by_iid)

        composite_iid = f"stack_{composite_count}"
        items = [it for _iid, it in group]
        comp = _create_vertical_composite(items, composite_iid)
        if comp is None:
            for iid, _item in group:
                new_options[iid] = options_by_instance[iid]
                pair_map[iid] = iid
            continue
        comp_180 = _create_composite_180(comp, options_by_instance)
        new_options[composite_iid] = [comp, comp_180]
        for iid, _item in group:
            pair_map[iid] = composite_iid
        composite_count += 1

    return new_options, pair_map



def pair_key(left: OrientedItem, right: OrientedItem) -> PairKey:
    return (left.instance_id, left.rotation_deg, right.instance_id, right.rotation_deg)


def _bounds_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def paths_bounds_overlap(paths_a, paths_b) -> bool:
    return _bounds_overlap(path_bounds(paths_a), path_bounds(paths_b))


def get_oriented_item_polys(item: OrientedItem, clearance_mm: float = 0.0):
    """Outline-path polygons (incl. dome boss), optionally buffered.

    Using the artwork outlines (valid) instead of the offset safety paths
    avoids invalid-polygon issues: the safety offset can self-intersect on
    thin artwork parts, which makes shapely miss intersections. Buffering the
    outline by ``clearance_mm`` enforces that exact minimum gap between items.
    """
    key = round(clearance_mm, 4)
    cache_attr = f"_prep_out_c{key}"
    polys_attr = f"_polys_out_c{key}"
    if not hasattr(item, cache_attr) or getattr(item, cache_attr) is None:
        from shapely.geometry import Polygon
        from shapely.prepared import prep
        from shapely.validation import make_valid
        buf_pt = mm_to_pt(clearance_mm) if clearance_mm > 0 else 0.0
        polys = []
        for p in item.outline_paths_pt:
            if len(p) < 3:
                continue
            poly = Polygon(p)
            if not poly.is_valid:
                try:
                    poly = make_valid(poly)
                except Exception:
                    poly = poly.buffer(0)
            if buf_pt > 0:
                poly = poly.buffer(buf_pt)
            polys.append(poly)
        preps = [prep(p) for p in polys]
        object.__setattr__(item, polys_attr, polys)
        object.__setattr__(item, cache_attr, preps)
    return getattr(item, polys_attr), getattr(item, cache_attr)


def item_pair_collides_at(left: OrientedItem, right: OrientedItem, dx_mm: float) -> bool:
    """Return whether safety outlines overlap with left at zero and right at ``dx``.

    A MIN_SAFETY_GAP_MM clearance is applied so adjacent items keep at least
    that gap between their safety outlines (=> >= 0.25mm on the artwork).
    """
    dx_pt = mm_to_pt(dx_mm)
    buf_pt = mm_to_pt(MIN_SAFETY_GAP_MM / 2.0)
    r0, r1, r2, r3 = right.outline_bounds_pt
    right_bounds_pt = (r0 + dx_pt - buf_pt, r1 - buf_pt, r2 + dx_pt + buf_pt, r3 + buf_pt)
    l0, l1, l2, l3 = left.outline_bounds_pt
    left_bounds_pt = (l0 - buf_pt, l1 - buf_pt, l2 + buf_pt, l3 + buf_pt)
    if not _bounds_overlap(left_bounds_pt, right_bounds_pt):
        return False

    from shapely.affinity import translate
    left_polys, left_preps = get_oriented_item_polys(left, clearance_mm=MIN_SAFETY_GAP_MM / 2.0)
    right_polys, _ = get_oriented_item_polys(right, clearance_mm=MIN_SAFETY_GAP_MM / 2.0)

    # get_oriented_item_polys skips paths with < 3 points; keep the sub-bounds
    # lists index-aligned with the polygon lists.
    left_sub = tuple(path_bounds([p]) for p in left.outline_paths_pt if len(p) >= 3)
    right_sub = tuple(path_bounds([p]) for p in right.outline_paths_pt if len(p) >= 3)

    for i, prep_pa in enumerate(left_preps):
        ba = left_sub[i]
        for j, pb in enumerate(right_polys):
            rb = right_sub[j]
            bb = (rb[0] + dx_pt - buf_pt, rb[1] - buf_pt, rb[2] + dx_pt + buf_pt, rb[3] + buf_pt)
            if not _bounds_overlap(ba, bb):
                continue
            pb_trans = translate(pb, xoff=dx_pt, yoff=0.0)
            if prep_pa.intersects(pb_trans):
                return True
    return False


def candidate_collides_with_row(
    candidate: OrientedItem, x_mm: float, y_mm: float, placed: list[PlacedItem]
) -> bool:
    """Validate a proposed position against every safety outline in the row."""
    cx_pt = mm_to_pt(x_mm)
    cy_pt = mm_to_pt(y_mm)
    buf_pt = mm_to_pt(MIN_SAFETY_GAP_MM / 2.0)
    c0, c1, c2, c3 = candidate.outline_bounds_pt
    cand_bounds_pt = (c0 + cx_pt - buf_pt, c1 + cy_pt - buf_pt, c2 + cx_pt + buf_pt, c3 + cy_pt + buf_pt)
    candidate_paths = None

    for placed_item in placed:
        px_pt = mm_to_pt(placed_item.x_mm)
        py_pt = mm_to_pt(placed_item.y_mm)
        p0, p1, p2, p3 = placed_item.item.outline_bounds_pt
        other_bounds_pt = (p0 + px_pt - buf_pt, p1 + py_pt - buf_pt, p2 + px_pt + buf_pt, p3 + py_pt + buf_pt)
        if not _bounds_overlap(cand_bounds_pt, other_bounds_pt):
            continue
        if candidate_paths is None:
            candidate_paths = place_paths(candidate.outline_paths_pt, x_mm, y_mm)
        other_paths = place_paths(placed_item.item.outline_paths_pt, placed_item.x_mm, placed_item.y_mm)
        if paths_collide(candidate_paths, other_paths, clearance_mm=MIN_SAFETY_GAP_MM / 2.0):
            return True
    return False


def _placed_row_collides(placed: list[PlacedItem]) -> bool:
    """Full-pair collision check on a row's placed items (outline + safety gap).

    Used to re-validate a row after mixed-preset vertical alignment, where the
    original y=0 pair-offset validation no longer applies.
    """
    for i in range(len(placed)):
        pi = placed[i]
        if pi.item.instance_id == "base_pedestal":
            continue
        pa = None
        for j in range(i + 1, len(placed)):
            pj = placed[j]
            if pj.item.instance_id == "base_pedestal":
                continue
            if pa is None:
                pa = place_paths(pi.item.outline_paths_pt, pi.x_mm, pi.y_mm)
            pb = place_paths(pj.item.outline_paths_pt, pj.x_mm, pj.y_mm)
            if paths_collide(pa, pb, clearance_mm=MIN_SAFETY_GAP_MM / 2.0):
                return True
    return False


def linear_pair_offset_search(
    left: OrientedItem, right: OrientedItem, start_x: float, max_x: float
) -> float | None:
    x = start_x
    while x <= max_x + 1e-9:
        if not item_pair_collides_at(left, right, x):
            return round(x, 3)
        x += STEP_MM
    return None


def hybrid_pair_offset_search(
    left: OrientedItem, right: OrientedItem, start_x: float, max_x: float
) -> float | None:
    """Find the first non-colliding pair offset using adaptive binary search on tight bounding range."""
    # 1. Compute exact theoretical maximum overlap limit from bounding box right edge.
    # Beyond left_max_x_pt - right_min_x_pt, bounding boxes cannot overlap.
    left_max_x_pt = left.safety_bounds_pt[2]
    right_min_x_pt = right.safety_bounds_pt[0]
    bbox_no_overlap_mm = pt_to_mm(left_max_x_pt - right_min_x_pt)
    tight_max_x = min(max_x, round(bbox_no_overlap_mm, 3))

    if not item_pair_collides_at(left, right, start_x):
        return round(start_x, 3)

    if item_pair_collides_at(left, right, tight_max_x):
        if tight_max_x < max_x and item_pair_collides_at(left, right, max_x):
            return None
        tight_max_x = max_x

    lo = start_x
    hi = tight_max_x

    while hi - lo > STEP_MM:
        mid = (lo + hi) / 2.0
        if item_pair_collides_at(left, right, mid):
            lo = mid
        else:
            hi = mid

    return round(hi, 3)


def compute_pair_offset(
    left: OrientedItem, right: OrientedItem, options: PackingOptions
) -> tuple[PairKey, float | None]:
    """Compute the minimum rightward offset for this ordered, rotated pair."""
    usable_width = ARTBOARD_WIDTH_MM - PAGE_MARGIN_X_MM * 2
    if left.width_mm + right.width_mm > usable_width:
        return pair_key(left, right), None
    start_x = max(0.0, left.width_mm - min(right.width_mm, options.max_bbox_overlap_probe_mm))
    max_x = left.width_mm + 1.0
    search = hybrid_pair_offset_search if options.use_hybrid_search else linear_pair_offset_search
    return pair_key(left, right), search(left, right, start_x, max_x)


def _compute_pair_offset_worker(args):
    """Top-level worker for ProcessPoolExecutor (must be picklable)."""
    left, right, options = args
    return compute_pair_offset(left, right, options)


def precompute_pair_offsets(
    oriented_items: list[OrientedItem], cache: PairOffsetCache, options: PackingOptions
) -> None:
    """Precompute ordered offsets only for the current bounded row candidate pool.

    Runs on a process pool when there is enough work and the caller requested
    more than one worker; otherwise falls back to serial computation to avoid
    process-spawn overhead on tiny pools.
    """
    if not options.pair_precompute:
        return
    jobs = [
        (left, right)
        for left in oriented_items
        for right in oriented_items
        if left.instance_id != right.instance_id and pair_key(left, right) not in cache.values
    ]
    if not jobs:
        return

    workers = getattr(options, "pair_precompute_workers", 1)
    if workers > 1 and len(jobs) >= 16:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(
                _compute_pair_offset_worker,
                [(left, right, options) for left, right in jobs],
            ))
        for (left, right), (key, value) in zip(jobs, results):
            cache.values[key] = value
        return

    for left, right in jobs:
        key, value = compute_pair_offset(left, right, options)
        cache.values[key] = value


def get_pair_offset(
    left: OrientedItem, right: OrientedItem, cache: PairOffsetCache, options: PackingOptions
) -> float | None:
    key = pair_key(left, right)
    if key not in cache.values:
        _, cache.values[key] = compute_pair_offset(left, right, options)
    return cache.values[key]


def candidate_x_from_pair_constraints(
    candidate: OrientedItem, placed: list[PlacedItem], cache: PairOffsetCache, options: PackingOptions
) -> float | None:
    """Use the tightest ordered pair constraint from every item already in the row."""
    x = 0.0
    for placed_item in placed:
        dx = get_pair_offset(placed_item.item, candidate, cache, options)
        if dx is None:
            return None
        x = max(x, placed_item.x_mm + dx)
    return x


def place_candidate_by_pair_offsets(
    candidate: OrientedItem, row: RowLayout, usable_width: float,
    cache: PairOffsetCache, options: PackingOptions,
) -> PlacedItem | None:
    x = candidate_x_from_pair_constraints(candidate, row.placed, cache, options)
    if x is None:
        return None
    max_x = usable_width - candidate.width_mm
    if max_x < 0 or x > max_x + 1e-9:
        return None

    # Pair offsets are directional pair estimates. Validate the complete row and
    # move right only when a non-adjacent outline exposes a collision.
    while x <= max_x + 1e-9:
        if not candidate_collides_with_row(candidate, x, 0.0, row.placed):
            return PlacedItem(candidate, x, 0.0)
        x += STEP_MM
    return None


def choose_orientation(
    item_options: list[OrientedItem], row: RowLayout, usable_width: float,
    cache: PairOffsetCache, options: PackingOptions,
) -> PlacedItem | None:
    best: PlacedItem | None = None
    for opt in item_options:
        placed = place_candidate_by_pair_offsets(opt, row, usable_width, cache, options)
        if placed is None:
            continue
        if best is None:
            best = placed
            continue
        candidate_width = max(row.width_mm, placed.x_mm + placed.item.width_mm)
        best_width = max(row.width_mm, best.x_mm + best.item.width_mm)
        if (candidate_width, placed.item.height_mm, placed.x_mm) < (best_width, best.item.height_mm, best.x_mm):
            best = placed
    return best


def build_best_row(
    options_by_instance: dict[str, list[OrientedItem]], remaining_ids: list[str], usable_width: float,
    *, cache: PairOffsetCache, options: PackingOptions,
) -> RowLayout:
    beam = [RowLayout()]
    row_pool_ids = remaining_ids[: max(1, options.row_candidate_limit)]
    pool_items = [item for iid in row_pool_ids for item in options_by_instance[iid]]
    precompute_pair_offsets(pool_items, cache, options)

    for step_idx in range(len(row_pool_ids)):
        expanded: list[RowLayout] = []
        for state in beam:
            used = {p.item.instance_id for p in state.placed}
            for iid in row_pool_ids:
                if iid in used:
                    continue
                placed = choose_orientation(options_by_instance[iid], state, usable_width, cache, options)
                if placed is None:
                    continue
                expanded.append(RowLayout(
                    state.placed + [placed],
                    max(state.width_mm, placed.x_mm + placed.item.width_mm),
                    max(state.height_mm, placed.item.height_mm),
                ))
        if not expanded:
            break
        expanded.sort(key=lambda row: (-len(row.placed), row.width_mm, row.height_mm))
        beam = expanded[: max(1, options.beam_width)]
    return min(beam, key=lambda row: (-len(row.placed), row.width_mm, row.height_mm))


def _center_rows_and_group(rows: list[RowLayout], usable_width: float, usable_height: float, margin: float) -> list[PlacedItem]:
    """Stand preset: centered vertically and horizontally."""
    y = total_h = 0.0
    raw: list[PlacedItem] = []
    for row in rows:
        raw.extend(PlacedItem(p.item, p.x_mm, y + p.y_mm) for p in row.placed)
        y += row.height_mm + ROW_GAP_MM
        total_h = y - ROW_GAP_MM
    group_dy = margin + (usable_height - total_h) / 2.0
    return [PlacedItem(p.item, p.x_mm + PAGE_MARGIN_X_MM, p.y_mm + group_dy) for p in raw]


def _place_rows_keyring_fixed(rows: list[RowLayout], usable_width: float, margin: float) -> list[PlacedItem]:
    """
    Keyring preset: fixed Y grid.
    - Row 0 Y = 0.5 (margin)
    - Row 1 Y = 0.5 + row_pitch
    - Row 2 Y = 0.5 + 2*row_pitch, etc.
    - row_pitch = actual safe height of the row items + ROW_GAP_MM,
      so adjacent rows never overlap even when the artwork's long side
      exceeds the nominal 65mm preset (e.g. 65.99mm safe height).
    - No vertical centering, only horizontal centering per row.
    """
    placed: list[PlacedItem] = []
    y = margin  # 0.5
    for row in rows:
        # Row pitch from the actual (safety-inclusive) item heights in this row.
        row_h = max((p.item.height_mm for p in row.placed), default=0.0)
        pitch = max(row_h, 0.0) + ROW_GAP_MM
        # Place items in this row at current y
        for p in row.placed:
            placed.append(PlacedItem(p.item, p.x_mm + PAGE_MARGIN_X_MM, y + p.y_mm))
        y += pitch
    return placed


def _place_rows_by_preset(
    rows: list[RowLayout], usable_width: float, usable_height: float, margin: float, preset: str
) -> list[PlacedItem]:
    preset = (preset or DEFAULT_PRESET).lower()
    if preset == "keyring":
        return _place_rows_keyring_fixed(rows, usable_width, margin)
    # Default: stand (centered)
    return _center_rows_and_group(rows, usable_width, usable_height, margin)


def generate_base_pdf(output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    from shapely.geometry import box
    from shapely.affinity import translate
    import pikepdf
    from ....converting.engine.pdf.renderer import make_ocg, assign_oc_properties, ensure_page_resources
    from ....converting.engine.units import mm_to_pt

    w_pt = mm_to_pt(35.0)
    h_pt = mm_to_pt(35.0)
    r_pt = mm_to_pt(3.0)
    
    inner_w = w_pt - 2 * r_pt
    inner_h = h_pt - 2 * r_pt
    poly = box(-inner_w/2, -inner_h/2, inner_w/2, inner_h/2).buffer(r_pt, quad_segs=8)
    
    min_x, min_y, max_x, max_y = poly.bounds
    poly = translate(poly, -min_x, -min_y)
    
    coords = list(poly.exterior.coords)
    
    path_cmds = [f"{coords[0][0]:.4f} {coords[0][1]:.4f} m"]
    for x, y in coords[1:]:
        path_cmds.append(f"{x:.4f} {y:.4f} l")
    path_cmds.append("h")
    outline_path_str = " ".join(path_cmds)
    
    pdf = pikepdf.Pdf.new()
    
    outline_ocg = make_ocg(pdf, "Outline")
    assign_oc_properties(pdf, [outline_ocg])
    
    outline_content = (
        f"q\n"
        f"1 0 0 RG\n"
        f"0.01 w\n"
        f"1 J\n"
        f"1 j\n"
        f"{outline_path_str} S\n"
        f"Q\n"
    )
    outline_form = pdf.make_stream(
        outline_content.encode("latin-1"),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        BBox=pikepdf.Array([0.0, 0.0, w_pt, h_pt]),
        Resources=pikepdf.Dictionary(),
        OC=outline_ocg
    )
    
    page = pdf.add_blank_page(page_size=(w_pt, h_pt))
    page_resources = ensure_page_resources(page)
    page_resources.XObject = pikepdf.Dictionary()
    page_resources.XObject["/OutlineForm"] = outline_form
    
    page_resources.Properties = pikepdf.Dictionary()
    page_resources.Properties["/MC2"] = outline_ocg
    
    content = (
        "/OC /MC2 BDC\n"
        "q\n"
        "/OutlineForm Do\n"
        "Q\n"
        "EMC\n"
    )
    page.Contents = pdf.make_stream(content.encode("latin-1"))
    pdf.Root.PageMode = pikepdf.Name.UseOC
    pdf.save(output_path)


_base_cache: dict[str, tuple[SourcePdf, dict[int, OrientedItem]]] = {}

def get_base_oriented_items() -> tuple[SourcePdf, dict[int, OrientedItem]]:
    key = "base"
    if key in _base_cache:
        return _base_cache[key]
    
    # Write the generated pedestal-base PDF to a temp location instead of CWD,
    # so nesting runs never pollute the working directory.
    base_path = Path(tempfile.gettempdir()) / "illupdf_base_35x35.pdf"
    if not base_path.exists():
        generate_base_pdf(base_path)
    
    from .pdf_parser import parse_source_pdf
    from .geometry import orient_paths, safety_paths, bounds_mm
    
    base_source = parse_source_pdf(base_path)
    base_items = {}
    for rot in [0, 45]:
        outline, ob = orient_paths(base_source.outline_paths_pt, rot)
        safety = safety_paths(outline)
        sb = path_bounds(safety)
        sw, sh = bounds_mm(sb)
        base_items[rot] = OrientedItem(
            source=base_source,
            instance_id="base_pedestal",
            copy_index=0,
            rotation_deg=rot,
            outline_paths_pt=outline,
            safety_paths_pt=safety,
            outline_bounds_pt=ob,
            safety_bounds_pt=sb,
            width_mm=sw,
            height_mm=sh,
        )
    _base_cache[key] = (base_source, base_items)
    return _base_cache[key]


def fill_row_with_bases_shapely(row, usable_width, base_items, row_idx=0, estimated_y_base=0.0, sheet_h=300.0, clearance_mm=0.25, step_mm=5.0):
    from shapely.geometry import Polygon, LineString
    from shapely.ops import unary_union
    from shapely.affinity import translate
    from shapely.validation import make_valid
    from shapely.prepared import prep
    from ....converting.engine.units import pt_to_mm

    occupied_polygons = []
    for p in row.placed:
        item_paths = place_paths(p.item.outline_paths_pt, p.x_mm, p.y_mm)
        for path in item_paths:
            if len(path) >= 3:
                poly = Polygon([(pt_to_mm(x), pt_to_mm(y)) for x, y in path])
                if not poly.is_valid:
                    poly = make_valid(poly)
                if clearance_mm > 0:
                    poly = poly.buffer(clearance_mm)
                occupied_polygons.append(poly)
    
    # Exclude 4 corner crop mark vector lines relative to row-local coordinates
    w = usable_width + PAGE_MARGIN_X_MM * 2
    h = sheet_h
    corners_mm = [(5.0, 5.0), (w - 5.0, 5.0), (5.0, h - 5.0), (w - 5.0, h - 5.0)]
    for cx_mm, cy_mm in corners_mm:
        cy_local = cy_mm - estimated_y_base
        h_line = LineString([(cx_mm - 5.0, cy_local), (cx_mm + 5.0, cy_local)]).buffer(clearance_mm)
        v_line = LineString([(cx_mm, cy_local - 5.0), (cx_mm, cy_local + 5.0)]).buffer(clearance_mm)
        occupied_polygons.append(h_line)
        occupied_polygons.append(v_line)

    try:
        occupied_union = unary_union(occupied_polygons) if occupied_polygons else Polygon()
    except Exception:
        cleaned = [poly.buffer(0) for poly in occupied_polygons if poly.is_valid]
        occupied_union = unary_union(cleaned) if cleaned else Polygon()

    if not occupied_union.is_valid:
        occupied_union = make_valid(occupied_union)
    
    prep_occupied = prep(occupied_union) if not occupied_union.is_empty else None
    occ_bounds = occupied_union.bounds if prep_occupied is not None else None
    placed_base_polys = []
    placed_base_bounds = []

    base_polys_mm = {}
    for rot in [0, 45]:
        base_item = base_items[rot]
        polys = []
        for path in base_item.safety_paths_pt:
            if len(path) >= 3:
                poly = Polygon([(pt_to_mm(x), pt_to_mm(y)) for x, y in path])
                if not poly.is_valid:
                    poly = make_valid(poly)
                polys.append(poly)
        poly_merged = unary_union(polys)
        if not poly_merged.is_valid:
            poly_merged = make_valid(poly_merged)
        min_x, min_y, _, _ = poly_merged.bounds
        poly_merged = translate(poly_merged, -min_x, -min_y)
        poly_buf = poly_merged.buffer(clearance_mm) if clearance_mm > 0 else poly_merged
        base_polys_mm[rot] = (poly_merged, poly_buf)

    row_y0_mm = 0.0
    row_y1_mm = row.height_mm
    
    bases = []
    
    y_mm = row_y0_mm
    min_bh_mm = min(base_items[r].height_mm for r in [0, 45])
    
    while y_mm + min_bh_mm <= row_y1_mm + 1e-5:
        x_mm = 0.5
        while x_mm + min_bh_mm <= usable_width + 1e-5:
            placed = False
            for rot in [0, 45]:
                base_item = base_items[rot]
                bw_mm = base_item.width_mm
                bh_mm = base_item.height_mm
                
                if x_mm + bw_mm > usable_width + 1e-5 or y_mm + bh_mm > row_y1_mm + 1e-5:
                    continue
                
                poly_raw, poly_buf = base_polys_mm[rot]
                base_poly_check = translate(poly_buf, x_mm, y_mm)
                bx0, by0, bx1, by1 = base_poly_check.bounds
                
                intersects = False
                if prep_occupied is not None and occ_bounds is not None:
                    ox0, oy0, ox1, oy1 = occ_bounds
                    if not (bx1 <= ox0 or ox1 <= bx0 or by1 <= oy0 or oy1 <= by0):
                        if prep_occupied.intersects(base_poly_check):
                            intersects = True

                if not intersects:
                    for p_poly, (px0, py0, px1, py1) in zip(placed_base_polys, placed_base_bounds):
                        if not (bx1 <= px0 or px1 <= bx0 or by1 <= py0 or py1 <= by0):
                            if p_poly.intersects(base_poly_check):
                                intersects = True
                                break

                if not intersects:
                    bases.append({
                        'rot': rot,
                        'x': x_mm,
                        'y': y_mm,
                        'w': bw_mm,
                        'h': bh_mm,
                        'right_edge': x_mm + bw_mm,
                        'top_edge': y_mm + bh_mm,
                    })
                    placed_base_polys.append(base_poly_check)
                    placed_base_bounds.append((bx0, by0, bx1, by1))
                    placed = True
                    x_mm += bw_mm + clearance_mm
                    break
            if not placed:
                x_mm += step_mm
        y_mm += step_mm
        
    return bases


def fill_sheet_with_bases_shapely(sheet, usable_width, usable_height, base_items, clearance_mm=0.25, step_mm=5.0):
    from shapely.geometry import Polygon, LineString, box
    from shapely.ops import unary_union
    from shapely.affinity import translate
    from shapely.validation import make_valid
    from shapely.prepared import prep
    from ....converting.engine.units import pt_to_mm

    occupied_polygons = []
    # Existing placed items on sheet (outline + clearance, so bases keep 0.25mm gap)
    for p in sheet.placed:
        item_paths = place_paths(p.item.outline_paths_pt, p.x_mm, p.y_mm)
        for path in item_paths:
            if len(path) >= 3:
                poly = Polygon([(pt_to_mm(x), pt_to_mm(y)) for x, y in path])
                if not poly.is_valid:
                    poly = make_valid(poly)
                if clearance_mm > 0:
                    poly = poly.buffer(clearance_mm)
                occupied_polygons.append(poly)
    
    # Exclude 4 corner crop mark vector lines (5mm crosshair arms at 4 corners + clearance_mm)
    w = sheet.width_mm
    h = sheet.height_mm
    corners_mm = [(5.0, 5.0), (w - 5.0, 5.0), (5.0, h - 5.0), (w - 5.0, h - 5.0)]
    for cx_mm, cy_mm in corners_mm:
        h_line = LineString([(cx_mm - 5.0, cy_mm), (cx_mm + 5.0, cy_mm)]).buffer(clearance_mm)
        v_line = LineString([(cx_mm, cy_mm - 5.0), (cx_mm, cy_mm + 5.0)]).buffer(clearance_mm)
        occupied_polygons.append(h_line)
        occupied_polygons.append(v_line)

    try:
        occupied_union = unary_union(occupied_polygons) if occupied_polygons else Polygon()
    except Exception:
        cleaned = [poly.buffer(0) for poly in occupied_polygons if poly.is_valid]
        occupied_union = unary_union(cleaned) if cleaned else Polygon()

    if not occupied_union.is_valid:
        occupied_union = make_valid(occupied_union)
    
    prep_occupied = prep(occupied_union) if not occupied_union.is_empty else None
    occ_bounds = occupied_union.bounds if prep_occupied is not None else None
    placed_base_polys = []
    placed_base_bounds = []

    base_polys_mm = {}
    for rot in [0, 45]:
        base_item = base_items[rot]
        polys = []
        for path in base_item.safety_paths_pt:
            if len(path) >= 3:
                poly = Polygon([(pt_to_mm(x), pt_to_mm(y)) for x, y in path])
                if not poly.is_valid:
                    poly = make_valid(poly)
                polys.append(poly)
        poly_merged = unary_union(polys)
        if not poly_merged.is_valid:
            poly_merged = make_valid(poly_merged)
        min_x, min_y, _, _ = poly_merged.bounds
        poly_merged = translate(poly_merged, -min_x, -min_y)
        poly_buf = poly_merged.buffer(clearance_mm) if clearance_mm > 0 else poly_merged
        base_polys_mm[rot] = (poly_merged, poly_buf)

    sheet_bases = []
    y_mm = 0.5
    min_bh_mm = min(base_items[r].height_mm for r in [0, 45])

    while y_mm + min_bh_mm <= usable_height + 1e-5:
        x_mm = 0.5
        while x_mm + min_bh_mm <= usable_width + 1e-5:
            placed = False
            for rot in [0, 45]:
                base_item = base_items[rot]
                bw_mm = base_item.width_mm
                bh_mm = base_item.height_mm

                if x_mm + bw_mm > usable_width + 1e-5 or y_mm + bh_mm > usable_height + 1e-5:
                    continue

                poly_raw, poly_buf = base_polys_mm[rot]
                base_poly_check = translate(poly_buf, x_mm, y_mm)
                bx0, by0, bx1, by1 = base_poly_check.bounds

                intersects = False
                if prep_occupied is not None and occ_bounds is not None:
                    ox0, oy0, ox1, oy1 = occ_bounds
                    if not (bx1 <= ox0 or ox1 <= bx0 or by1 <= oy0 or oy1 <= by0):
                        if prep_occupied.intersects(base_poly_check):
                            intersects = True

                if not intersects:
                    for p_poly, (px0, py0, px1, py1) in zip(placed_base_polys, placed_base_bounds):
                        if not (bx1 <= px0 or px1 <= bx0 or by1 <= py0 or py1 <= by0):
                            if p_poly.intersects(base_poly_check):
                                intersects = True
                                break

                if not intersects:
                    base_placed = PlacedItem(item=base_items[rot], x_mm=x_mm, y_mm=y_mm)
                    sheet.placed.append(base_placed)
                    placed_base_polys.append(base_poly_check)
                    placed_base_bounds.append((bx0, by0, bx1, by1))
                    sheet_bases.append(base_placed)
                    placed = True
                    x_mm += bw_mm + clearance_mm
                    break
            if not placed:
                x_mm += step_mm
        y_mm += step_mm

    return sheet_bases


def _item_long_side_mm(item: OrientedItem) -> float:
    """Long side of an oriented item in mm (long side is vertical by design)."""
    return max(item.height_mm, item.width_mm)


def _classify_item(long_side_mm: float) -> str:
    """Classify one item as 'keyring' or 'stand' by threshold margins.

    - long side < KEYRING_THRESHOLD_MM (66mm)  -> keyring
    - otherwise (>= 66mm)                      -> stand
      (items >= 149mm are out-of-spec but still treated as stand)
    """
    return "keyring" if long_side_mm < KEYRING_THRESHOLD_MM else "stand"


def detect_preset(options_by_instance: dict[str, list[OrientedItem]]) -> str:
    """Auto-select the nesting preset from the input item sizes.

    - every item keyring-sized  → "keyring" (fixed 4-row Y grid)
    - every item stand-sized    → "stand"   (2-row centered)
    - a mix of both             → "mixed"   (keyrings paired into stand-like items)
    """
    categories: set[str] = set()
    for iid, opts in options_by_instance.items():
        if not opts:
            continue
        long_side = _item_long_side_mm(opts[0])
        categories.add(_classify_item(long_side))
    if categories == {"keyring"}:
        return "keyring"
    if categories == {"stand"}:
        return "stand"
    return "mixed"


def build_sheets(
    options_by_instance: dict[str, list[OrientedItem]], source_dir: Path, *,
    packing_options: PackingOptions | None = None,
) -> list[SheetLayout]:
    options = packing_options or PackingOptions()
    usable_width = ARTBOARD_WIDTH_MM - PAGE_MARGIN_X_MM * 2  # 399.0mm (400x300 sheet fixed)
    usable_height_2rows = ARTBOARD_HEIGHT_MM - PAGE_MARGIN_Y_MM * 2  # 299.0mm (400x300 sheet fixed)
    usable_height_1row = ARTBOARD_HEIGHT_MM / 2.0 - PAGE_MARGIN_Y_MM * 2  # 149.0mm (single row)
    preset = (getattr(options, "preset", DEFAULT_PRESET) or DEFAULT_PRESET).lower()
    if preset == "auto":
        preset = detect_preset(options_by_instance)
    if preset not in PRESET_LONG_SIDE_MM:
        preset = DEFAULT_PRESET

    # For mixed preset, preprocess keyring items into vertical pairs (stand-like composites)
    if preset == "mixed":
        options_by_instance, _pair_map = _build_mixed_composites(options_by_instance)

    remaining = sorted(options_by_instance, key=lambda iid: (-max(o.height_mm for o in options_by_instance[iid]), -max(o.width_mm for o in options_by_instance[iid])))
    cache = PairOffsetCache()
    sheets: list[SheetLayout] = []
    sheet_index = 1

    while remaining:
        raw_rows: list[RowLayout] = []
        used_in_sheet: set[str] = set()
        used_height = 0.0
        while True:
            candidates = [iid for iid in remaining if iid not in used_in_sheet]
            if not candidates:
                break
            row = build_best_row(options_by_instance, candidates, usable_width, cache=cache, options=options)
            if not row.placed:
                break

            # Requirement 5: In mixed preset, align KEYRING items vertically within row:
            # Row 0 (Bottom row on page): align towards BOTTOM relative to stand height (y = 0.0)
            # Row 1 (Top row on page): align towards TOP relative to stand height (y = row.height_mm - item.height_mm)
            # Stands and composites keep y = 0 (bottom-aligned, matching the collision
            # checks that validated the row), and the aligned row is re-validated so a
            # top-aligned keyring can never end up closer than MIN_SAFETY_GAP_MM to a
            # neighbour (the y=0 validation does not cover the shifted position).
            if preset == "mixed":
                row_idx = len(raw_rows)
                aligned_placed = []
                for p in row.placed:
                    if _is_keyring_oriented_item(p.item) and p.item.height_mm < row.height_mm:
                        y_aligned = 0.0 if row_idx == 0 else (row.height_mm - p.item.height_mm)
                        aligned_placed.append(PlacedItem(p.item, p.x_mm, y_aligned))
                    else:
                        aligned_placed.append(p)
                if _placed_row_collides(aligned_placed):
                    # Alignment broke the safety gap: fall back to the validated y=0 row.
                    aligned_placed = [PlacedItem(p.item, p.x_mm, 0.0) for p in row.placed]
                row.placed = aligned_placed

            # Height reserved per row. For keyring we use the actual safe item
            # height of the row (never smaller than the nominal 65mm preset),
            # so rows never collide when artwork exceeds 65mm (e.g. 65.99mm).
            if preset == "keyring":
                row_h_for_sheet = max(row.height_mm, PRESET_LONG_SIDE_MM["keyring"])
            else:
                row_h_for_sheet = row.height_mm
            next_height = used_height + (ROW_GAP_MM if raw_rows else 0.0) + row_h_for_sheet
            if next_height > usable_height_2rows + 1e-6:
                break
            raw_rows.append(row)
            used_height = next_height
            used_in_sheet.update(p.item.instance_id for p in row.placed)

        if not raw_rows:
            # Keep the historic progress guarantee for an oversized source.
            iid = remaining[0]
            opt = min(options_by_instance[iid], key=lambda o: o.width_mm * o.height_mm)
            raw_rows = [RowLayout([PlacedItem(opt, 0.0, 0.0)], opt.width_mm, opt.height_mm)]
            used_in_sheet.add(iid)

        # Always emit the full artboard. A single-row sheet used to collapse to
        # half height (400x150), which silently changes the output page size
        # between runs and breaks print setups that expect a fixed 400x300.
        sheet_height = ARTBOARD_HEIGHT_MM
        usable_sheet_height = sheet_height - PAGE_MARGIN_Y_MM * 2

        # Evaluate alignment combinations (left, center, right per row: 3^N)
        # and select the layout that places the MAXIMUM number of pedestal bases.
        if preset in ["stand", "mixed"]:
            align_choices = ["left", "center", "right"]
            if len(raw_rows) == 1:
                combos = [(c,) for c in align_choices]
            else:
                combos = [(c1, c2) for c1 in align_choices for c2 in align_choices]

            best_sheet_obj = None
            best_base_count = -1

            base_source, base_items = get_base_oriented_items()

            for combo in combos:
                candidate_rows = []
                for r_idx, r in enumerate(raw_rows):
                    r_width = max(p.x_mm + p.item.width_mm for p in r.placed) if r.placed else 0.0
                    align_type = combo[r_idx]
                    if align_type == "left":
                        char_dx = 0.0
                    elif align_type == "center":
                        char_dx = (usable_width - r_width) / 2.0
                    else:  # right
                        char_dx = usable_width - r_width

                    cand_row = RowLayout(
                        placed=[PlacedItem(p.item, p.x_mm + char_dx, p.y_mm) for p in r.placed],
                        width_mm=usable_width,
                        height_mm=r.height_mm,
                    )

                    # Row level base filling
                    estimated_y_base = 0.5 if r_idx == 0 else (148.0 + ROW_GAP_MM + 0.5)
                    bases = fill_row_with_bases_shapely(cand_row, usable_width, base_items, row_idx=r_idx, estimated_y_base=estimated_y_base, sheet_h=sheet_height, clearance_mm=0.25)
                    for b_info in bases:
                        cand_row.placed.append(PlacedItem(item=base_items[b_info['rot']], x_mm=b_info['x'], y_mm=b_info['y']))
                    if bases:
                        cand_row.width_mm = max(cand_row.width_mm, max(b_info['right_edge'] for b_info in bases))
                        cand_row.height_mm = max(cand_row.height_mm, max(b_info['top_edge'] for b_info in bases))

                    candidate_rows.append(cand_row)

                placed = _place_rows_by_preset(candidate_rows, usable_width, usable_sheet_height, PAGE_MARGIN_Y_MM, preset)
                sheet_obj = SheetLayout(rows=candidate_rows, placed=placed, width_mm=ARTBOARD_WIDTH_MM, height_mm=sheet_height, source_dir=source_dir, index=sheet_index)

                # Sheet level base filling
                fill_sheet_with_bases_shapely(sheet_obj, usable_width, usable_sheet_height, base_items, clearance_mm=0.25)

                base_count = sum(1 for p in sheet_obj.placed if p.item.instance_id == "base_pedestal")
                if base_count > best_base_count:
                    best_base_count = base_count
                    best_sheet_obj = sheet_obj

            sheet_obj = best_sheet_obj
        else:
            placed = _place_rows_by_preset(raw_rows, usable_width, usable_sheet_height, PAGE_MARGIN_Y_MM, preset)
            sheet_obj = SheetLayout(rows=raw_rows, placed=placed, width_mm=ARTBOARD_WIDTH_MM, height_mm=sheet_height, source_dir=source_dir, index=sheet_index)

        sheets.append(sheet_obj)
        remaining = [iid for iid in remaining if iid not in used_in_sheet]
        sheet_index += 1
    return sheets
