"""
Scenario engine for Polaris Harness.

Implements deterministic state machine for scenario execution.
"""

import uuid
import re
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from enum import Enum

from .clock import ClockManager, ClockMode, ClockEvent
from .framing import Frame
from .matcher import MatchRule, MatchResult, MatchType, Matcher


class EngineEventType(Enum):
    """Engine event type enumeration."""
    CONNECTION_OPENED = "connection_opened"
    CONNECTION_CLOSED = "connection_closed"
    REQUEST_RECEIVED = "request_received"
    TIMER_TICK = "timer_tick"
    ACTION_EXECUTED = "action_executed"
    ASSERTION_FAILED = "assertion_failed"
    SCENARIO_COMPLETE = "scenario_complete"


@dataclass
class EngineEvent:
    """Engine event data structure."""
    event_type: EngineEventType
    timestamp_ms: int
    data: Dict[str, Any] = None
    source: str = None


@dataclass
class EngineAction:
    """Engine action data structure."""
    action_type: str
    endpoint: str
    after_ms: int
    parameters: Dict[str, Any] = None
    data: Dict[str, Any] = None


@dataclass
class EngineState:
    """Engine state data structure."""
    name: str
    data: Dict[str, Any] = None


class ScenarioState:
    """Scenario state machine."""

    def __init__(self, name: str, initial_state: str = "idle"):
        self.name = name
        self.states: Dict[str, Dict[str, Any]] = {}
        self.current_state = initial_state
        self.state_history = []

    def add_state(self, state_name: str, state_data: Dict[str, Any] = None) -> None:
        """Add a state to the scenario.

        Args:
            state_name: Name of the state
            state_data: State data
        """
        self.states[state_name] = state_data or {}

    def set_initial_state(self, state_name: str) -> None:
        """Set the initial state.

        Args:
            state_name: Name of the initial state
        """
        if state_name not in self.states:
            raise ValueError(f"State not found: {state_name}")
        self.current_state = state_name

    def get_current_state(self) -> str:
        """Get current state.

        Returns:
            Current state name
        """
        return self.current_state

    def transition_to(self, state_name: str) -> bool:
        """Transition to a new state.

        Args:
            state_name: Name of the state to transition to

        Returns:
            True if transition successful, False otherwise
        """
        if state_name not in self.states:
            return False

        self.state_history.append(self.current_state)
        self.current_state = state_name
        return True

    def get_state_data(self, state_name: str = None) -> Dict[str, Any]:
        """Get state data.

        Args:
            state_name: Name of the state (current if None)

        Returns:
            State data
        """
        if state_name is None:
            state_name = self.current_state
        return self.states.get(state_name, {}).copy()

    def get_all_states(self) -> Dict[str, Dict[str, Any]]:
        """Get all states.

        Returns:
            Dictionary of all states
        """
        return self.states.copy()


class ScenarioEngine:
    """Main scenario engine."""

    def __init__(self, scenario_id: str, scenario_data: Dict[str, Any]):
        self.scenario_id = scenario_id
        self.scenario_data = scenario_data
        self.state_machine = ScenarioState(scenario_id)
        self.clock = ClockManager(ClockMode.VIRTUAL)
        self.events: List[EngineEvent] = []
        self.actions: List[EngineAction] = []
        self.assertions: List[str] = scenario_data.get("assertions", [])
        self.endpoint_available = {"command": True, "preview": True}
        self.connection_id = str(uuid.uuid4())

        # Initialize state machine
        self._initialize_state_machine()

    def _initialize_state_machine(self) -> None:
        """Initialize state machine from scenario data."""
        states = self.scenario_data.get("states", {})
        for state_name, state_data in states.items():
            self.state_machine.add_state(state_name, state_data)

        initial_state = self.scenario_data.get("initial_state", "idle")
        self.state_machine.set_initial_state(initial_state)

    def process_connection_opened(self, connection_id: str) -> None:
        """Process connection opened event.

        Args:
            connection_id: Connection identifier
        """
        event = EngineEvent(
            event_type=EngineEventType.CONNECTION_OPENED,
            timestamp_ms=self.clock.now_ms(),
            data={"connection_id": connection_id},
            source="transport"
        )
        self.events.append(event)

    def process_connection_closed(self, connection_id: str) -> None:
        """Process connection closed event.

        Args:
            connection_id: Connection identifier
        """
        event = EngineEvent(
            event_type=EngineEventType.CONNECTION_CLOSED,
            timestamp_ms=self.clock.now_ms(),
            data={"connection_id": connection_id},
            source="transport"
        )
        self.events.append(event)

    def process_request_received(self, frame: Frame, connection_id: str) -> None:
        """Process request received event.

        Args:
            frame: Received frame
            connection_id: Connection identifier
        """
        event = EngineEvent(
            event_type=EngineEventType.REQUEST_RECEIVED,
            timestamp_ms=self.clock.now_ms(),
            data={
                "frame": frame,
                "connection_id": connection_id,
                "endpoint": "command"
            },
            source="transport"
        )
        self.events.append(event)

    def process_timer_tick(self) -> None:
        """Process timer tick event."""
        event = EngineEvent(
            event_type=EngineEventType.TIMER_TICK,
            timestamp_ms=self.clock.now_ms(),
            data={},
            source="clock"
        )
        self.events.append(event)

    def execute_scenario(self, frame: Frame, connection_id: str) -> List[EngineAction]:
        """Execute scenario based on received frame.

        Args:
            frame: Received frame
            connection_id: Connection identifier

        Returns:
            List of actions to execute
        """
        current_state = self.state_machine.get_current_state()
        state_data = self.state_machine.get_state_data()

        # Get on_request rules for current state
        on_request_rules = state_data.get("on_request", [])

        # Find matching rule
        matched_rule = None
        for rule in on_request_rules:
            if self._match_rule(frame, rule):
                matched_rule = rule
                break

        if not matched_rule:
            # No matching rule found
            return [EngineAction(
                action_type="send",
                endpoint="command",
                after_ms=0,
                parameters={"text": "ERROR: No matching rule found"}
            )]

        # Execute actions from matched rule
        actions = []
        for action in matched_rule.get("actions", []):
            action_obj = self._create_action(action)
            if action_obj:
                actions.append(action_obj)

        return actions

    def _match_rule(self, frame: Frame, rule: Dict[str, Any]) -> bool:
        """Match frame against rule.

        Args:
            frame: Frame to match
            rule: Rule to match against

        Returns:
            True if rule matches, False otherwise
        """
        # Create match rule from rule data
        rule_match = rule.get("match", {})
        rule_type = rule_match.get("type", "exact_text")
        rule_value = rule_match.get("value", "")
        rule_hex = rule_match.get("hex", "")
        rule_pattern = rule_match.get("pattern", "")
        rule_rationale = rule_match.get("rationale", "")
        rule_encoding = rule_match.get("encoding", "utf-8")

        # Create match rule
        match_rule = MatchRule(
            id=rule.get("id", ""),
            match_type=MatchType(rule_type),
            value=rule_value,
            encoding=rule_encoding,
            hex=rule_hex if rule_type == "exact_bytes" else None,
            pattern=rule_pattern if rule_type == "regex_text" else None,
            rationale=rule_rationale if rule_type == "regex_text" else None,
            required=rule.get("required", True)
        )

        # Use matcher to match frame
        matcher = Matcher()
        matcher.add_match_rule(match_rule)

        return matcher.match_frame(frame, match_rule).matched

    def _create_action(self, action_data: Dict[str, Any]) -> Optional[EngineAction]:
        """Create action from action data.

        Args:
            action_data: Action data

        Returns:
            Engine action or None
        """
        action_type = action_data.get("type", "")
        endpoint = action_data.get("endpoint", "")
        after_ms = action_data.get("after_ms", 0)

        if action_type == "send":
            return EngineAction(
                action_type="send",
                endpoint=endpoint,
                after_ms=after_ms,
                parameters={"text": action_data.get("text", "")}
            )
        elif action_type == "transition":
            next_state = action_data.get("next_state", "")
            if next_state:
                return EngineAction(
                    action_type="transition",
                    endpoint=endpoint,
                    after_ms=after_ms,
                    parameters={"next_state": next_state}
                )
        elif action_type == "set_endpoint_available":
            available = action_data.get("available", True)
            return EngineAction(
                action_type="set_endpoint_available",
                endpoint=endpoint,
                after_ms=after_ms,
                parameters={"available": available}
            )
        elif action_type == "emit_marker":
            marker = action_data.get("marker", "")
            return EngineAction(
                action_type="emit_marker",
                endpoint=endpoint,
                after_ms=after_ms,
                parameters={"marker": marker}
            )

        return None

    def execute_actions(self, actions: List[EngineAction]) -> List[EngineEvent]:
        """Execute actions and return resulting events.

        Args:
            actions: Actions to execute

        Returns:
            List of resulting events
        """
        events = []

        for action in actions:
            # Execute action
            if action.action_type == "send":
                event = EngineEvent(
                    event_type=EngineEventType.ACTION_EXECUTED,
                    timestamp_ms=self.clock.now_ms() + action.after_ms,
                    data={
                        "action": "send",
                        "endpoint": action.endpoint,
                        "text": action.parameters.get("text", "")
                    },
                    source="engine"
                )
                events.append(event)

            elif action.action_type == "transition":
                next_state = action.parameters.get("next_state", "")
                if self.state_machine.transition_to(next_state):
                    event = EngineEvent(
                        event_type=EngineEventType.ACTION_EXECUTED,
                        timestamp_ms=self.clock.now_ms() + action.after_ms,
                        data={
                            "action": "transition",
                            "from_state": self.state_machine.get_current_state(),
                            "to_state": next_state
                        },
                        source="engine"
                    )
                    events.append(event)

            elif action.action_type == "set_endpoint_available":
                available = action.parameters.get("available", True)
                self.endpoint_available[action.endpoint] = available

                event = EngineEvent(
                    event_type=EngineEventType.ACTION_EXECUTED,
                    timestamp_ms=self.clock.now_ms() + action.after_ms,
                    data={
                        "action": "set_endpoint_available",
                        "endpoint": action.endpoint,
                        "available": available
                    },
                    source="engine"
                )
                events.append(event)

            elif action.action_type == "emit_marker":
                marker = action.parameters.get("marker", "")
                event = EngineEvent(
                    event_type=EngineEventType.ACTION_EXECUTED,
                    timestamp_ms=self.clock.now_ms() + action.after_ms,
                    data={
                        "action": "emit_marker",
                        "marker": marker
                    },
                    source="engine"
                )
                events.append(event)

        self.events.extend(events)
        return events

    def check_assertions(self) -> List[str]:
        """Check scenario assertions.

        Returns:
            List of failed assertions
        """
        failed_assertions = []

        # TODO: Implement assertion checking
        # For now, just return empty list
        return failed_assertions

    def get_scenario_state(self) -> Dict[str, Any]:
        """Get current scenario state.

        Returns:
            Scenario state
        """
        return {
            "scenario_id": self.scenario_id,
            "current_state": self.state_machine.get_current_state(),
            "clock_time_ms": self.clock.now_ms(),
            "endpoint_available": self.endpoint_available,
            "event_count": len(self.events),
            "action_count": len(self.actions)
        }

    def get_events(self) -> List[EngineEvent]:
        """Get engine events.

        Returns:
            List of engine events
        """
        return self.events.copy()

    def get_actions(self) -> List[EngineAction]:
        """Get engine actions.

        Returns:
            List of engine actions
        """
        return self.actions.copy()

    def reset(self) -> None:
        """Reset scenario engine."""
        self.state_machine = ScenarioState(self.scenario_id)
        self._initialize_state_machine()
        self.events.clear()
        self.actions.clear()
        self.clock.reset()
        self.endpoint_available = {"command": True, "preview": True}
        self.connection_id = str(uuid.uuid4())