"""Chase/kick decisions and pass scoring.

Callers first use :mod:`predicates` to rule out sideline/recovery cases, then use
this module to choose this tick's kick target:

center view: pass, then shoot, then dribble
side view: pass if possible, otherwise clear, without shooting
choose best passing teammate by score
generic lane-obstacle score
"""

from __future__ import annotations

import math
from collections.abc import Callable

from ...soccer_framework import (
    BallState,
    GameControlState,
    Pose2D,
    SetPlay,
    SoccerConfig,
    PlayContext,
)
from ..geometry import clamp
from ..navigation import Obstacle, ObstacleCollector
from ..geometry import TeamFieldFrame
from . import recovery
from .predicates import ball_near_sideline


__all__ = [
    "PlayerAllowed",
    "best_pass_target",
    "dribble_target",
    "kick_reason",
    "lane_clear_score",
    "select_clear_or_pass_target",
    "select_kick_target",
    "shot_lane_is_clear",
    "should_make_restart_touch",
]


# Shared with upper layers: test whether a teammate can legally join tactics, excluding penalized/substitute players.
PlayerAllowed = Callable[[GameControlState, int], bool]


# Top-level selection


def select_kick_target(
    config: SoccerConfig,
    field: TeamFieldFrame,
    obstacles: ObstacleCollector,
    player_id: int,
    context: PlayContext,
    is_player_allowed: PlayerAllowed,
    *,
    was_shooting: bool = False,
) -> tuple[Pose2D, str]:
    """Decide this tick's aim target for a center chaser.

    Decision order: sideline recovery, restart touch, clear shot lane, best pass,
    and finally dribble forward.  Shot lane uses hysteresis via ``was_shooting``
    to prevent rapid shoot/dribble oscillation.

    Returns (target, decision) where decision is one of:
    ``"sideline"``, ``"restart"``, ``"shoot"``, ``"pass"``, ``"dribble"``.
    """

    ball = context.known_ball
    if ball_near_sideline(config, ball):
        return recovery.sideline_recovery_target(config, field, ball), "sideline"

    game = context.known_game
    if should_make_restart_touch(config, game):
        teammate = best_pass_target(
            config, obstacles,
            player_id, context, is_player_allowed,
        )
        if teammate is not None:
            return Pose2D(teammate.x, teammate.y, 0.0), "restart"
        return dribble_target(config, field, ball), "dribble"

    if shot_lane_is_clear(config, field, obstacles, context, was_shooting=was_shooting):
        return Pose2D(field.opponent_goal_x(), 0.0, 0.0), "shoot"

    teammate = best_pass_target(
        config, obstacles,
        player_id, context, is_player_allowed,
    )
    if teammate is not None:
        return Pose2D(teammate.x, teammate.y, 0.0), "pass"
    return dribble_target(config, field, ball), "dribble"


def select_clear_or_pass_target(
    config: SoccerConfig,
    field: TeamFieldFrame,
    obstacles: ObstacleCollector,
    player_id: int,
    context: PlayContext,
    is_player_allowed: PlayerAllowed,
) -> tuple[Pose2D, str]:
    """Side-lane view: pass if possible, otherwise clear toward the opponent goal without shooting or dribbling.

    Side chasers prefer clearing toward the middle rather than dribbling along the sideline into pressure.

    Returns (target, decision) where decision is one of:
    ``"sideline"``, ``"pass"``, ``"clear"``.
    """

    ball = context.known_ball
    if ball_near_sideline(config, ball):
        return recovery.sideline_recovery_target(config, field, ball), "sideline"

    teammate = best_pass_target(
        config, obstacles,
        player_id, context, is_player_allowed,
    )
    if teammate is not None:
        return Pose2D(teammate.x, teammate.y, 0.0), "pass"
    return Pose2D(field.opponent_goal_x(), 0.0, 0.0), "clear"


# Restart touch


def should_make_restart_touch(
    config: SoccerConfig,
    game: GameControlState,
) -> bool:
    """Whether our team should actively touch the ball on restart: kickoff, throw-in, or indirect free kick."""

    return game.is_kickoff_for_team(config.team_id) or (
        game.is_restart_for_team(config.team_id)
        and game.set_play in {SetPlay.THROW_IN, SetPlay.INDIRECT_FREE_KICK}
    )


# Pass scoring


def best_pass_target(
    config: SoccerConfig,
    obstacles: ObstacleCollector,
    player_id: int,
    context: PlayContext,
    is_player_allowed: PlayerAllowed,
) -> Pose2D | None:
    """Select this tick's best legal passing target; return ``None`` when no candidate qualifies.

    Score weights are lane clearance 0.55, forward gain 0.30, center pull 0.15,
    minus distance penalty. Candidates below ``pass_min_score`` are discarded.
    """

    if not config.strategy.pass_enabled:
        return None
    ball = context.known_ball
    game = context.known_game
    candidates: list[tuple[float, Pose2D]] = []
    for teammate_id, robot in context.teammates.items():
        if teammate_id == player_id or robot.pose is None:
            continue
        if not is_player_allowed(game, teammate_id):
            continue
        forward_gain = robot.pose.x - ball.x
        if forward_gain < config.strategy.pass_min_forward_m:
            continue
        lane_score = lane_clear_score(
            config,
            ball.x,
            ball.y,
            robot.pose.x,
            robot.pose.y,
            obstacles.opponent_obstacles(context),
        )
        distance = math.hypot(robot.pose.x - ball.x, robot.pose.y - ball.y)
        field_score = clamp(
            forward_gain / max(1.0, config.field_length),
            0.0,
            1.0,
        )
        center_score = 1.0 - clamp(
            abs(robot.pose.y) / (config.field_width / 2.0),
            0.0,
            1.0,
        )
        score = 0.55 * lane_score + 0.30 * field_score + 0.15 * center_score
        score -= clamp(distance / 12.0, 0.0, 0.25)
        if score >= config.strategy.pass_min_score:
            candidates.append((score, robot.pose))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def shot_lane_is_clear(
    config: SoccerConfig,
    field: TeamFieldFrame,
    obstacles: ObstacleCollector,
    context: PlayContext,
    *,
    was_shooting: bool = False,
) -> bool:
    """Treat a shot lane as shootable with hysteresis to prevent oscillation.

    When not already shooting, lane_clear_score >= 0.55 is required to enter.
    When already shooting, lane_clear_score >= 0.35 is sufficient to stay.
    """

    ball = context.known_ball
    score = lane_clear_score(
        config,
        ball.x,
        ball.y,
        field.opponent_goal_x(),
        0.0,
        obstacles.opponent_obstacles(context),
    )
    threshold = 0.35 if was_shooting else 0.55
    return score >= threshold


def lane_clear_score(
    config: SoccerConfig,
    start_x: float,
    start_y: float,
    target_x: float,
    target_y: float,
    obstacles: tuple[Obstacle, ...],
) -> float:
    """Generic lane-obstacle score in [0, 1], where 1 is clear and 0 is blocked.

    Project each obstacle onto the lane; if it lies within the segment and inside
    lateral clearance, reduce score proportionally. Multiple obstacles take the worst score.
    """

    if not obstacles:
        return 1.0
    seg_dx = target_x - start_x
    seg_dy = target_y - start_y
    seg_len = math.hypot(seg_dx, seg_dy)
    if seg_len < 1e-6:
        return 0.0
    dir_x = seg_dx / seg_len
    dir_y = seg_dy / seg_len
    left_x = -dir_y
    left_y = dir_x
    score = 1.0
    for obstacle in obstacles:
        rel_x = obstacle.x - start_x
        rel_y = obstacle.y - start_y
        along = rel_x * dir_x + rel_y * dir_y
        if along <= 0.0 or along >= seg_len:
            continue
        lateral = abs(rel_x * left_x + rel_y * left_y)
        clearance = max(config.strategy.pass_lane_clearance, obstacle.radius)
        if lateral < clearance:
            score = min(score, clamp(lateral / clearance, 0.0, 1.0))
    return score


# Dribble and reason text


def dribble_target(
    config: SoccerConfig,
    field: TeamFieldFrame,
    ball: BallState,
) -> Pose2D:
    """Simple dribble target: advance by ``dribble_advance_m`` along ``+x`` and pull ``y`` toward center."""

    target_x = ball.x + config.strategy.dribble_advance_m
    target_y = ball.y * config.strategy.dribble_center_pull
    return field.clamp_inside_field(
        Pose2D(target_x, target_y, field.attack_theta())
    )


def kick_reason(
    config: SoccerConfig,
    target: Pose2D,
    default: str = "chaser kick",
) -> str:
    """Choose the reason suffix from whether target points at the opponent goal center.

    When target is near the goal mouth, keep ``default``; otherwise append
    ``" to target"`` so logs distinguish shooting from pass/clear targets.
    """

    if abs(target.x) >= config.field_length / 2.0 - 0.2 and abs(target.y) < 0.2:
        return default
    return f"{default} to target"
