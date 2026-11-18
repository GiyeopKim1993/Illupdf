from __future__ import annotations
import math
from .hole import resolve_keyring_hole_center, INTERNAL_ITERATION_GUARD
from .reinforcement import build_reinforced_keyring_outer_path
from ..geometry.polygons import polygon_area_and_centroid, GEOMETRY_EPSILON_PT, approximate_circle_path
from ..models import KeyringCandidate, KeyringBalanceResult, PathPoints, TraceObserver
from ..units import pt_to_mm

THETA_TOLERANCE_RAD = 0.01
THETA_IMPROVEMENT_TOLERANCE_RAD = 1e-5
BRACKET_WIDTH_TOLERANCE_MM = 0.01
RX_RECURRING_STEP_MM = 0.001


def build_keyring_balance(
    base_outer_path_pt: PathPoints,
    observer: TraceObserver | None = None,
) -> KeyringBalanceResult:
    from ..errors import PdfGenerationError
    from ..keyring.hole import KEYRING_HOLE_RADIUS_MM
    from ..keyring.reinforcement import KEYRING_BOSS_RADIUS_MM
    from ..units import mm_to_pt
    from ..pipeline import emit_trace, make_keyring_trace_frame

    hole_radius_pt = mm_to_pt(KEYRING_HOLE_RADIUS_MM)
    boss_radius_pt = mm_to_pt(KEYRING_BOSS_RADIUS_MM)
    _, base_centroid_pt = polygon_area_and_centroid(base_outer_path_pt)
    current_rx_pt = base_centroid_pt[0]
    previous_candidate: KeyringCandidate | None = None
    bracket_low: KeyringCandidate | None = None
    bracket_high: KeyringCandidate | None = None
    best_candidate: KeyringCandidate | None = None
    seen_states: dict[float, float] = {}
    iteration = 0

    while True:
        iteration += 1
        if iteration > INTERNAL_ITERATION_GUARD:
            raise PdfGenerationError("keyring balancing did not converge before the internal safety guard")

        hole_center_pt = resolve_keyring_hole_center(
            base_outer_path_pt,
            current_rx_pt,
            hole_radius_pt,
            boss_radius_pt,
        )
        hole_path_pt = approximate_circle_path(hole_center_pt, hole_radius_pt)
        outer_path_pt, boss_path_pt = build_reinforced_keyring_outer_path(
            base_outer_path_pt,
            hole_center_pt,
            boss_radius_pt,
        )

        outer_area_pt2, outer_centroid_pt = polygon_area_and_centroid(outer_path_pt)
        hole_area_pt2 = math.pi * hole_radius_pt * hole_radius_pt
        final_area_pt2 = outer_area_pt2 - hole_area_pt2
        if final_area_pt2 <= GEOMETRY_EPSILON_PT:
            raise PdfGenerationError("keyring balancing produced a non-positive final area")

        cx_final = (outer_area_pt2 * outer_centroid_pt[0] - hole_area_pt2 * hole_center_pt[0]) / final_area_pt2
        cy_final = (outer_area_pt2 * outer_centroid_pt[1] - hole_area_pt2 * hole_center_pt[1]) / final_area_pt2
        centroid_pt = (cx_final, cy_final)
        error_x_pt = cx_final - current_rx_pt
        theta_rad = math.atan2(error_x_pt, max(GEOMETRY_EPSILON_PT, hole_center_pt[1] - cy_final))
        current_candidate = KeyringCandidate(
            rx_pt=current_rx_pt,
            error_x_pt=error_x_pt,
            theta_rad=theta_rad,
            outer_path_pt=outer_path_pt,
            hole_path_pt=hole_path_pt,
            boss_path_pt=boss_path_pt,
            hole_center_pt=hole_center_pt,
            centroid_pt=centroid_pt,
            outer_centroid_pt=outer_centroid_pt,
        )

        if best_candidate is None or abs(current_candidate.theta_rad) < abs(best_candidate.theta_rad):
            best_candidate = current_candidate

        status = "running"
        if previous_candidate is not None and previous_candidate.error_x_pt * current_candidate.error_x_pt < 0:
            low_candidate, high_candidate = sorted(
                [previous_candidate, current_candidate],
                key=lambda candidate: candidate.rx_pt,
            )
            bracket_low = low_candidate
            bracket_high = high_candidate
            status = "oscillating"
        elif bracket_low is not None and bracket_high is not None:
            if current_candidate.error_x_pt * bracket_low.error_x_pt > 0:
                bracket_low = current_candidate
            elif current_candidate.error_x_pt * bracket_high.error_x_pt > 0:
                bracket_high = current_candidate
            status = "oscillating"

        if iteration % 10 == 0:
            print(f"    - Balancing iteration {iteration}: theta={current_candidate.theta_rad:.4f}, error_x={current_candidate.error_x_pt:.4f}")

        emit_trace(
            observer,
            make_keyring_trace_frame(
                preset_name="keyring",
                stage="keyring_balance",
                message="Keyring hole placement, reinforcement, and balance iteration.",
                candidate=current_candidate,
                iteration=iteration,
                status=status,
            ),
        )

        if abs(current_candidate.theta_rad) <= THETA_TOLERANCE_RAD:
            return make_keyring_balance_result(best_candidate, iteration)

        quantized_rx_mm = round(pt_to_mm(current_candidate.rx_pt), 3)
        previous_theta_for_state = seen_states.get(quantized_rx_mm)
        current_abs_theta = abs(current_candidate.theta_rad)
        if (
            previous_theta_for_state is not None
            and previous_theta_for_state - current_abs_theta <= THETA_IMPROVEMENT_TOLERANCE_RAD
        ):
            return make_keyring_balance_result(best_candidate, iteration)
        seen_states[quantized_rx_mm] = min(previous_theta_for_state or current_abs_theta, current_abs_theta)

        if bracket_low is not None and bracket_high is not None:
            bracket_width_mm = abs(pt_to_mm(bracket_high.rx_pt - bracket_low.rx_pt))
            if bracket_width_mm <= BRACKET_WIDTH_TOLERANCE_MM:
                return make_keyring_balance_result(best_candidate, iteration)
            next_rx_pt = (bracket_low.rx_pt + bracket_high.rx_pt) / 2.0
        else:
            next_rx_pt = (current_candidate.rx_pt + current_candidate.centroid_pt[0]) / 2.0

        if abs(pt_to_mm(next_rx_pt - current_candidate.rx_pt)) <= RX_RECURRING_STEP_MM:
            return make_keyring_balance_result(best_candidate, iteration)

        previous_candidate = current_candidate
        current_rx_pt = next_rx_pt


def make_keyring_balance_result(candidate: KeyringCandidate, iterations: int) -> KeyringBalanceResult:
    return KeyringBalanceResult(
        outer_path_pt=list(candidate.outer_path_pt),
        hole_path_pt=list(candidate.hole_path_pt),
        boss_path_pt=list(candidate.boss_path_pt),
        hole_center_pt=candidate.hole_center_pt,
        centroid_pt=candidate.centroid_pt,
        theta_rad=candidate.theta_rad,
        iterations=iterations,
    )
