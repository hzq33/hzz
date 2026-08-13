"""Unit tests for TaskPlanner JSON parsing."""

import pytest

from src.core.planner import TaskPlanner
from src.utils.errors import PlanningError


def _planner() -> TaskPlanner:
    # _parse_plan 是纯解析逻辑，不依赖 LLM client
    return TaskPlanner(llm_client=None, model="deepseek-v4-flash")


class TestParsePlan:
    def test_direct_json(self):
        raw = (
            '{"goal": "g", "steps": [{"id": 1, "description": "d", '
            '"tool_name": null, "tool_args": null, "depends_on": []}]}'
        )
        plan = _planner()._parse_plan(raw, "user input")
        assert plan.goal == "g"
        assert len(plan.steps) == 1
        assert plan.steps[0].tool_name is None

    def test_code_fence(self):
        raw = '```json\n{"goal": "g", "steps": [{"id": 1, "description": "d", "depends_on": []}]}\n```'
        plan = _planner()._parse_plan(raw, "u")
        assert plan.goal == "g"

    def test_trailing_comma(self):
        raw = '{"goal": "g", "steps": [{"id": 1, "description": "d", "depends_on": [],},],}'
        plan = _planner()._parse_plan(raw, "u")
        assert plan.goal == "g"

    def test_extra_text_around_json(self):
        raw = (
            'Here is the plan: {"goal": "g", "steps": '
            '[{"id": 1, "description": "d", "depends_on": []}]} Thanks!'
        )
        plan = _planner()._parse_plan(raw, "u")
        assert plan.goal == "g"

    def test_missing_goal_falls_back_to_input(self):
        raw = '{"steps": [{"id": 1, "description": "d", "depends_on": []}]}'
        plan = _planner()._parse_plan(raw, "fallback goal")
        assert plan.goal == "fallback goal"

    def test_invalid_json_raises(self):
        with pytest.raises(PlanningError):
            _planner()._parse_plan("not json at all", "u")

    def test_missing_steps_raises(self):
        with pytest.raises(PlanningError):
            _planner()._parse_plan('{"goal": "g"}', "u")

    def test_empty_steps_raises(self):
        with pytest.raises(PlanningError):
            _planner()._parse_plan('{"goal": "g", "steps": []}', "u")

    def test_dependency_on_missing_step_raises(self):
        raw = '{"goal": "g", "steps": [{"id": 1, "description": "d", "depends_on": [2]}]}'
        with pytest.raises(PlanningError):
            _planner()._parse_plan(raw, "u")
