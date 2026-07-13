"""Support-position targets plus teammate-spacing pushout.

SafetyGuards ensure PLAY support targets only run with fresh ball and
GameController data; the supporter positions on the ball-to-own-goal-center
line (max 3 m behind the ball) and pushes away from teammates to avoid stacking.
"""

from __future__ import annotations

import math

from ...soccer_framework import (
    Pose2D,
    SoccerConfig,
    PlayContext,
)
from ..geometry import TeamFieldFrame
from .attack import PlayerAllowed


__all__ = ["support_target"]


def support_target(
    config: SoccerConfig,
    field: TeamFieldFrame,
    player_id: int,
    context: PlayContext,
    is_player_allowed: PlayerAllowed,
) -> tuple[Pose2D, bool]:
    """Compute this tick's supporter target Pose2D.

    Position on the ball-to-own-goal-center line, at most 3 m behind the ball.
    This keeps the supporter in the chaser's rear support zone without
    crossing the ball, ready for a natural role switch.

    Pushout: use :func:`_spaced_support_target` to avoid overlapping teammates.

    Returns (target, was_pushed).
    """

    ball = context.known_ball
    max_dist = 1.0 if ball.x <= 0 else 3.0
    GK = field.own_goal_x()
    dx = ball.x - GK
    dy = ball.y
    dist = math.hypot(dx, dy)
    ratio = max(dist - max_dist, 0.0) / max(dist, 0.01)
    tx = GK + dx * ratio
    ty = dy * ratio
    target = field.clamp_inside_field(
        Pose2D(tx, ty, field.face_ball_theta(tx, ty, ball))
    )
    return _spaced_support_target(
        config,
        field,
        player_id,
        context,
        target,
        is_player_allowed,
    )


# Teammate spacing pushout


def _spaced_support_target(
    config: SoccerConfig,
    field: TeamFieldFrame,
    player_id: int,
    context: PlayContext,
    target: Pose2D,
    is_player_allowed: PlayerAllowed,
) -> tuple[Pose2D, bool]:
    """If target is closer than min_spacing to the nearest teammate, push it along "teammate -> target" out to ``min_spacing``.

    Steps:
    1. Find the nearest legal teammate.
    2. If distance is large enough, do nothing.
    3. Otherwise scale the "teammate -> target" unit vector to min_spacing.
    4. Clamp inside the field and finally face the ball.

    Degenerate case: when target almost overlaps the teammate, no direction can
    be scaled, so fall back to ``lane_sign`` based on which side of the ball target
    is on; if target is exactly on the ball, split by player_id parity.

    In extreme corners with teammate pressure, clamping can make the final target
    slightly closer than min_spacing. With at most three teammates this is rare; if
    strict final distance is needed, iterate once more after clamping.

    Returns (adjusted_target, was_pushed).
    """

    min_spacing = config.strategy.support_min_spacing_m
    if min_spacing <= 0.0:
        return target, False

    ball = context.known_ball
    game = context.known_game
    teammate_poses = tuple(
        robot.pose
        for teammate_id, robot in context.teammates.items()
        if teammate_id != player_id
        and robot.pose is not None
        and is_player_allowed(game, teammate_id)
    )
    if not teammate_poses:
        return target, False

    closest = min(
        teammate_poses,
        key=lambda pose: math.hypot(pose.x - target.x, pose.y - target.y),
    )
    dx = target.x - closest.x
    dy = target.y - closest.y
    distance = math.hypot(dx, dy)
    if distance >= min_spacing:
        return target, False

    if distance <= 1e-6:
        lane_sign = 1.0 if target.y >= ball.y else -1.0
        if abs(target.y - ball.y) < 1e-6:
            lane_sign = 1.0 if player_id % 2 == 0 else -1.0
        dx, dy = 0.0, lane_sign
        distance = 1.0

    scale = min_spacing / distance
    pushed = field.clamp_inside_field(
        Pose2D(
            closest.x + dx * scale,
            closest.y + dy * scale,
            target.theta,
        )
    )
    return Pose2D(
        pushed.x,
        pushed.y,
        field.face_ball_theta(pushed.x, pushed.y, ball),
    ), True
