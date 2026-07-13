"""Default dynamic role implementations for SoccerSim.

Each role extends :class:`RoleStrategy` and assembles its subtree through
:meth:`build_subtree`. Methods such as ``target``, ``wants_to_kick``, and
``kick_target`` are implementation helpers for role utility nodes, not
base-class contracts.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import py_trees

from ..soccer_framework import BallState, PlayContext, Pose2D, ReadySlot
from ..tactics.geometry import normalize_angle
from .nodes import AttackSubtreeConfig, MoveToTarget, build_attack_subtree
from .role import RoleStrategy

if TYPE_CHECKING:
    from ..runtime import SoccerKit


# ----------------------------------------------------------------------
# Chaser: approach and kick the ball
# ----------------------------------------------------------------------

# Approach alignment distance behind the ball while chasing, in meters.
_CHASER_APPROACH_OFFSET = 0.22


class ChaserRole(RoleStrategy):
    """Default chaser; kick target is split by ReadySlot into center shot or side clearance."""

    name = "chaser"

    def __init__(self) -> None:
        self._last_decision: str | None = None

    def target(
        self,
        kit: "SoccerKit",
        player_id: int,
        context: PlayContext,
    ) -> Pose2D:
        ball = context.known_ball
        kt = self.kick_target(kit, player_id, context)
        kick_theta = math.atan2(kt.y - ball.y, kt.x - ball.x)
        return kit.motion.approach_target(
            ball,
            kick_theta,
            _CHASER_APPROACH_OFFSET,
        )

    def kick_target(
        self,
        kit: "SoccerKit",
        player_id: int,
        context: PlayContext,
    ) -> Pose2D:
        slot = kit.config.ready_slot_for_player(player_id)
        if slot == ReadySlot.SIDE:
            target, decision = kit.targeting.select_clear_or_pass_target(
                player_id,
                context,
                kit.is_player_allowed,
            )
        else:
            target, decision = kit.targeting.select_kick_target(
                player_id,
                context,
                kit.is_player_allowed,
                was_shooting=self._last_decision == "shoot",
            )

        if decision != self._last_decision:
            self._last_decision = decision
            logger = kit.logger
            if logger is not None:
                extra: dict[str, object] = {}
                msg = (
                    f"chaser kick decision={decision} "
                    f"ball=({context.known_ball.x:.3f},{context.known_ball.y:.3f}) "
                    f"target=({target.x:.3f},{target.y:.3f}) "
                    f"player={player_id} slot={slot.value}"
                )
                if slot == ReadySlot.CENTER and decision in ("shoot", "dribble"):
                    lane_score = kit.targeting.shot_lane_score(context)
                    extra["shot_lane_score"] = round(lane_score, 3)
                    msg += f" lane_score={lane_score:.3f}"
                logger.info(
                    msg,
                    event="chaser_kick_decision",
                    player_id=player_id,
                    slot=slot.value,
                    decision=decision,
                    ball_x=round(context.known_ball.x, 3),
                    ball_y=round(context.known_ball.y, 3),
                    target_x=round(target.x, 3),
                    target_y=round(target.y, 3),
                    **extra,
                )

        return target

    def _approach_reason(self, kit: "SoccerKit", player_id: int) -> str:
        slot = kit.config.ready_slot_for_player(player_id)
        return f"{slot.value} approach ball"

    def _kick_reason(
        self,
        kit: "SoccerKit",
        player_id: int,
        target: Pose2D,
    ) -> str:
        slot = kit.config.ready_slot_for_player(player_id)
        decision = self._last_decision or "unknown"
        default = f"chaser kick decision={decision}"
        return kit.targeting.kick_reason(target, default=default)

    def build_subtree(
        self,
        kit: "SoccerKit",
        player_id: int,
    ) -> py_trees.behaviour.Behaviour:
        return build_attack_subtree(
            kit,
            player_id,
            AttackSubtreeConfig(
                target_fn=lambda context: self.target(kit, player_id, context),
                kick_target_fn=lambda context: self.kick_target(
                    kit,
                    player_id,
                    context,
                ),
                reason_fn=lambda: self._approach_reason(kit, player_id),
                kick_reason_fn=lambda target: self._kick_reason(
                    kit,
                    player_id,
                    target,
                ),
                speed_multiplier=1.5,
            ),
        )


# ----------------------------------------------------------------------
# Supporter: attacking support position
# ----------------------------------------------------------------------


class SupporterRole(RoleStrategy):
    """Attacking support role; uses :meth:`Targeting.support_target` for positioning."""

    name = "supporter"

    def __init__(self) -> None:
        self._was_pushed: bool = False
        self._pushout_logged: bool = False

    def target(
        self,
        kit: "SoccerKit",
        player_id: int,
        context: PlayContext,
    ) -> Pose2D:
        target, was_pushed = kit.targeting.support_target(
            player_id,
            context,
            kit.is_player_allowed,
        )

        if was_pushed != self._was_pushed:
            self._was_pushed = was_pushed
            if was_pushed:
                self._pushout_logged = False
            logger = kit.logger
            if logger is not None and was_pushed and not self._pushout_logged:
                self._pushout_logged = True
                logger.info(
                    f"supporter pushout player={player_id} "
                    f"target=({target.x:.3f},{target.y:.3f}) "
                    f"ball=({context.known_ball.x:.3f},{context.known_ball.y:.3f})",
                    event="supporter_pushout",
                    player_id=player_id,
                    target_x=round(target.x, 3),
                    target_y=round(target.y, 3),
                    ball_x=round(context.known_ball.x, 3),
                    ball_y=round(context.known_ball.y, 3),
                )

        return target

    def build_subtree(
        self,
        kit: "SoccerKit",
        player_id: int,
    ) -> py_trees.behaviour.Behaviour:
        return MoveToTarget(
            kit,
            player_id,
            lambda context: self.target(kit, player_id, context),
            reason_fn=lambda: "supporter hold",
            hold_vyaw=0.12,
            speed_multiplier=1.3,
        )


# ----------------------------------------------------------------------
# Defender: extension defensive position, defaulting to supporter target
# ----------------------------------------------------------------------


class DefenderRole(RoleStrategy):
    """Extension defensive role; default implementation reuses the supporter target."""

    name = "defender"

    def __init__(self) -> None:
        super().__init__()
        self._fallback = SupporterRole()

    def target(
        self,
        kit: "SoccerKit",
        player_id: int,
        context: PlayContext,
    ) -> Pose2D:
        return self._fallback.target(kit, player_id, context)

    def build_subtree(
        self,
        kit: "SoccerKit",
        player_id: int,
    ) -> py_trees.behaviour.Behaviour:
        return MoveToTarget(
            kit,
            player_id,
            lambda context: self.target(kit, player_id, context),
            reason_fn=lambda: "defender hold",
            hold_vyaw=0.12,
        )


# ----------------------------------------------------------------------
# Goalkeeper: guard the goal and clear dangerous balls
# ----------------------------------------------------------------------


class GoalkeeperRole(RoleStrategy):
    """Goalkeeper guarding and defensive-area clearance."""

    name = "goalkeeper"

    # Approach alignment distance for goalkeeper challenges, tighter than the chaser, in meters.
    _APPROACH_OFFSET = 0.18

    def __init__(self):
        super().__init__()
        self._last_ball_x = 0.0
        self._last_ball_y = 0.0
        self._last_ball_time = 0.0
        self._was_in_defensive_area = False
        self._smooth_vx = 0.0
        self._smooth_vy = 0.0
        self._clear_plan_logged = False
        self._guard_logged = False
        self._last_kick_dir_index = -1

    def target(
        self,
        kit: "SoccerKit",
        context: PlayContext,
    ) -> Pose2D:
        # When the ball is dangerous and kickable, target the approach point behind the ball to enter IsInKickRange.
        # Otherwise return to the goal-line guard target.
        ball = context.known_ball

        # Smoothed velocity estimation with low-pass filter
        dt = ball.last_seen_at - self._last_ball_time
        if 0 < dt < 0.5:
            raw_vx = (ball.x - self._last_ball_x) / dt
            raw_vy = (ball.y - self._last_ball_y) / dt
            self._smooth_vx = 0.7 * self._smooth_vx + 0.3 * raw_vx
            self._smooth_vy = 0.7 * self._smooth_vy + 0.3 * raw_vy
        else:
            self._smooth_vx *= 0.9
            self._smooth_vy *= 0.9

        self._last_ball_x = ball.x
        self._last_ball_y = ball.y
        self._last_ball_time = ball.last_seen_at

        # Dynamic prediction horizon based on time-to-intercept
        keeper_id = kit.config.goalkeeper_player_id()
        robot = context.teammates.get(keeper_id)
        if robot is not None and robot.pose is not None:
            dist = math.hypot(ball.x - robot.pose.x, ball.y - robot.pose.y)
            rush_speed = kit.config.strategy.goalkeeper_rush_speed_ratio * kit.config.strategy.goalkeeper_rush_speed_multiplier
            travel_time = max(dist / rush_speed, 0.2)
            travel_time = min(travel_time, kit.config.strategy.goalkeeper_prediction_max_sec)
        else:
            travel_time = 0.3
        pred_x = ball.x + self._smooth_vx * travel_time
        pred_y = ball.y + self._smooth_vy * travel_time

        wants = self.wants_to_kick(kit, context)
        logger = kit.logger
        if wants:
            kt = self.kick_target(kit, context)
            kick_theta = math.atan2(kt.y - pred_y, kt.x - pred_x)
            target = kit.motion.approach_target(
                BallState(x=pred_x, y=pred_y, last_seen_at=ball.last_seen_at),
                kick_theta,
                self._APPROACH_OFFSET,
            )
            target = kit.field.clamp_from_goal_obstructions(target)
            if logger is not None and not self._clear_plan_logged:
                dir_name = (
                    "center" if abs(kt.y) < 1.0
                    else "top" if kt.y > 0
                    else "bottom"
                )
                logger.info(
                    f"GK clear plan: dir={dir_name} "
                    f"ball=({ball.x:.3f},{ball.y:.3f}) "
                    f"pred_t={travel_time:.2f}s "
                    f"ball_v=({self._smooth_vx:.2f},{self._smooth_vy:.2f})",
                    event="goalkeeper_clear_plan",
                    direction=dir_name,
                    ball_x=round(ball.x, 3), ball_y=round(ball.y, 3),
                    pred_horizon=round(travel_time, 2),
                    ball_vx=round(self._smooth_vx, 2),
                    ball_vy=round(self._smooth_vy, 2),
                )
                self._clear_plan_logged = True
        else:
            target = kit.ready_stance.goalkeeper_guard_target(ball)
            if logger is not None and not self._guard_logged:
                logger.info(
                    f"GK guard: target=({target.x:.3f},{target.y:.3f}) "
                    f"ball=({ball.x:.3f},{ball.y:.3f})",
                    event="goalkeeper_guard_active",
                    target_x=round(target.x, 3), target_y=round(target.y, 3),
                    ball_x=round(ball.x, 3), ball_y=round(ball.y, 3),
                )
                self._guard_logged = True

        return target

    def wants_to_kick(
        self,
        kit: "SoccerKit",
        context: PlayContext,
    ) -> bool:
        ball = context.known_ball
        raw = kit.targeting.ball_in_own_defensive_area(ball)
        hyst = kit.config.strategy.goalkeeper_challenge_hysteresis_m

        if self._was_in_defensive_area and hyst > 0.0:
            # Exit hysteresis: ball must leave the area plus the hysteresis band
            area_x, area_y = kit.targeting.goalkeeper_defensive_area()
            in_area = ball.x < area_x + hyst and abs(ball.y) <= area_y + hyst
        else:
            in_area = raw

        if in_area != self._was_in_defensive_area:
            self._was_in_defensive_area = in_area
            if in_area:
                self._clear_plan_logged = False
                self._guard_logged = False
                self._last_kick_dir_index = -1
            logger = kit.logger
            if logger is not None:
                direction = "guard→clear" if in_area else "clear→guard"
                logger.info(
                    f"GK {direction} "
                    f"ball=({ball.x:.3f},{ball.y:.3f})",
                    event="goalkeeper_state_transition",
                    state=direction,
                    ball_x=round(ball.x, 3), ball_y=round(ball.y, 3),
                )
        return in_area

    def kick_target(
        self,
        kit: "SoccerKit",
        context: PlayContext,
    ) -> Pose2D:
        ball = context.known_ball

        # Static ball in goal area → force forward center clearance
        ball_speed = math.hypot(self._smooth_vx, self._smooth_vy)
        own_goal_x = kit.field.own_goal_x()
        if ball.x < own_goal_x + 0.8 and ball_speed < 0.3:
            return Pose2D(6.30, 0.0, kit.field.attack_theta())

        keeper_id = kit.config.goalkeeper_player_id()
        robot = context.teammates.get(keeper_id)
        if robot is None or robot.pose is None:
            return Pose2D(6.30, 0.0, kit.field.attack_theta())

        goal_w = kit.config.goal_width
        candidates = [
            (6.30, 0.0),                         # opponent half center
            (6.30, goal_w * 0.5),                # opponent goal top post
            (6.30, -goal_w * 0.5),               # opponent goal bottom post
        ]

        # Lock direction for the entire clear cycle
        if self._last_kick_dir_index == -1:
            best_index = 0
            best_diff = float("inf")
            for i, (tx, ty) in enumerate(candidates):
                theta = math.atan2(ty - ball.y, tx - ball.x)
                diff = abs(normalize_angle(theta - robot.pose.theta))
                if diff < best_diff:
                    best_diff = diff
                    best_index = i
            self._last_kick_dir_index = best_index
            dir_name = ("center", "top", "bottom")[best_index]
            logger = kit.logger
            if logger is not None:
                logger.info(
                    f"GK kick dir: {dir_name} "
                    f"ball=({ball.x:.3f},{ball.y:.3f}) "
                    f"robot_theta={robot.pose.theta:.2f}",
                    event="goalkeeper_kick_direction",
                    direction=dir_name,
                    ball_x=round(ball.x, 3), ball_y=round(ball.y, 3),
                    robot_theta=round(robot.pose.theta, 2),
                )

        return Pose2D(candidates[self._last_kick_dir_index][0],
                      candidates[self._last_kick_dir_index][1],
                      kit.field.attack_theta())

    def _guard_reason(self) -> str:
        return "goalkeeper guard"

    def _kick_reason(
        self,
        kit: "SoccerKit",
        target: Pose2D,
    ) -> str:
        return kit.targeting.kick_reason(target, default="goalkeeper clear")

    def build_subtree(
        self,
        kit: "SoccerKit",
        player_id: int,
    ) -> py_trees.behaviour.Behaviour:
        return build_attack_subtree(
            kit,
            player_id,
            AttackSubtreeConfig(
                target_fn=lambda context: self.target(kit, context),
                kick_target_fn=lambda context: self.kick_target(kit, context),
                wants_kick_fn=lambda context: self.wants_to_kick(kit, context),
                reason_fn=self._guard_reason,
                kick_reason_fn=lambda target: self._kick_reason(kit, target),
                hold_vyaw=0.12,
                strafe=True,
                speed_multiplier=kit.config.strategy.goalkeeper_rush_speed_multiplier,
                kick_power=kit.config.strategy.goalkeeper_kick_power,
                lateral_speed=kit.config.strategy.goalkeeper_lateral_speed,
            ),
        )
