"""Support-position targets plus teammate-spacing pushout.

SafetyGuards ensure PLAY support targets only run with fresh ball and
GameController data; the supporter positions on the ball-to-own-goal-center
line (max 3 m behind the ball) and pushes away from teammates to avoid stacking.
"""

from __future__ import annotations

import math

from ...soccer_framework import (
    BallState,
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

    Positions the supporter behind the **chaser** (not the ball) at a dynamic
    distance of 1–4 m, offset laterally by 10°–45° to form a triangle.
    Always faces the ball via ``theta = face_ball_theta`` so the supporter can
    react instantly when the ball passes the chaser.

    Distance rule (relative to chaser):
      current < 1 m → target at 1 m  (back up, strafe mode keeps facing ball)
      current > 4 m → target at 4 m  (close in)
      otherwise     → keep distance, only lateral strafe to adjust triangle angle

    Falls back to ball→own-goal line positioning when no chaser or own pose is
    available.

    Returns (target, was_pushed).
    """

    ball = context.known_ball
    game = context.known_game

    # Find chaser: non-GK teammate closest to the ball
    gk_id = config.goalkeeper_player_id()
    chaser_pose = None
    chaser_dist = float("inf")
    for tid, trobot in context.teammates.items():
        if (
            tid == player_id
            or trobot.pose is None
            or not is_player_allowed(game, tid)
        ):
            continue
        if tid == gk_id:
            continue
        dist = math.hypot(ball.x - trobot.pose.x, ball.y - trobot.pose.y)
        if dist < chaser_dist:
            chaser_dist = dist
            chaser_pose = trobot.pose

    own_robot = context.teammates.get(player_id)

    # Need both chaser pose and own pose for chaser-relative positioning.
    if chaser_pose is not None and own_robot is not None and own_robot.pose is not None:
        tx, ty = _chaser_relative_target(
            config, ball, chaser_pose, own_robot.pose, player_id,
        )
    else:
        # Fallback: ball → own-goal line at 2.5 m behind ball
        tx, ty = _goal_line_fallback(config, field, ball)

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


def _chaser_relative_target(
    config: SoccerConfig,
    ball: BallState,
    chaser_pose: Pose2D,
    own_pose: Pose2D,
    player_id: int,
) -> tuple[float, float]:
    """Compute supporter position behind the chaser with distance clamping.

    Direction: ball → chaser extended (the "behind" direction).
    Distance: clamped to [1, 4] m from the chaser based on current separation.
    Angle:    10°–45° lateral offset (dynamic by ball field position).
    """

    # Behind-direction: from ball through chaser
    bc_dx = chaser_pose.x - ball.x
    bc_dy = chaser_pose.y - ball.y
    bc_len = math.hypot(bc_dx, bc_dy)
    if bc_len < 1e-6:
        bc_dx, bc_dy = -1.0, 0.0
        bc_len = 1.0
    bx = bc_dx / bc_len
    by = bc_dy / bc_len

    # Current supporter → chaser distance
    sc_dx = own_pose.x - chaser_pose.x
    sc_dy = own_pose.y - chaser_pose.y
    sc_dist = math.hypot(sc_dx, sc_dy)

    # Clamp desired distance to [1, 4]
    if sc_dist < 1.0:
        desired_dist = 1.0
    elif sc_dist > 4.0:
        desired_dist = 4.0
    else:
        desired_dist = sc_dist

    # Triangle angle: 10° in defence → 45° in attack
    t = max(0.0, min(1.0, (ball.x + config.field_length / 2.0) / config.field_length))
    angle_rad = math.radians(10.0 + t * 35.0)

    # Rotate behind-direction by angle_rad; side alternates by player_id parity
    side = 1.0 if player_id % 2 == 0 else -1.0
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    dir_x = bx * cos_a - by * sin_a * side
    dir_y = bx * sin_a * side + by * cos_a

    tx = chaser_pose.x + desired_dist * dir_x
    ty = chaser_pose.y + desired_dist * dir_y
    return tx, ty


def _goal_line_fallback(
    config: SoccerConfig,
    field: TeamFieldFrame,
    ball: BallState,
) -> tuple[float, float]:
    """Ball → own-goal line at 2.5 m behind the ball (used when no chaser)."""

    GK = field.own_goal_x()
    dx = ball.x - GK
    dy = ball.y
    dist = math.hypot(dx, dy)
    max_dist = 2.5
    ratio = max(dist - max_dist, 0.0) / max(dist, 0.01)
    return GK + dx * ratio, dy * ratio


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
