"""Tests for the Phase-0 multi-agent collaboration layer (core/crew.py).

These use injected responders, so no LLM, provider or credentials are needed —
they exercise the orchestration and the hand-off log, not the model.
"""

import pytest

from core.crew import (Agent, Crew, Handoff, CrewRun, run_pair,
                       AgentSpec, plan_team, build_crew, _parse_team)


def test_agent_frames_role_and_goal():
    seen = {}

    def responder(prompt):
        seen["prompt"] = prompt
        return "ok"

    a = Agent("Ana", role="researcher", goal="find sources", responder=responder)
    out = a.run("investigate X")

    assert out == "ok"
    assert "Ana" in seen["prompt"]
    assert "researcher" in seen["prompt"]
    assert "find sources" in seen["prompt"]
    assert "investigate X" in seen["prompt"]


def test_goal_defaults_to_role():
    a = Agent("Bob", role="writer", responder=lambda p: p)
    assert a.goal == "writer"


def test_two_agents_hand_off_in_order():
    calls = []

    def make(name, output):
        def responder(prompt):
            calls.append((name, prompt))
            return output
        return responder

    a = Agent("A", "researcher", responder=make("A", "A-RESULT"))
    b = Agent("B", "writer", responder=make("B", "B-RESULT"))

    run = Crew([a, b]).run("do the thing")

    # Final output is the last agent's.
    assert run.final_output == "B-RESULT"
    assert run.agents == ["A", "B"]

    # A saw the original task; B saw A's output plus the task as context.
    a_prompt = calls[0][1]
    b_prompt = calls[1][1]
    assert "do the thing" in a_prompt
    assert "A-RESULT" in b_prompt          # A's output was handed to B
    assert "do the thing" in b_prompt      # original task kept as context


def test_handoff_log_is_recorded():
    a = Agent("A", "researcher", responder=lambda p: "A-RESULT")
    b = Agent("B", "writer", responder=lambda p: "B-RESULT")

    run = Crew([a, b]).run("task")

    assert isinstance(run, CrewRun)
    assert len(run.handoffs) == 2
    # First hand-off is the human task coming in.
    assert run.handoffs[0].from_agent is None
    assert run.handoffs[0].to_agent == "A"
    assert run.handoffs[0].content == "task"
    # Second is A -> B, carrying A's output.
    assert run.handoffs[1].from_agent == "A"
    assert run.handoffs[1].to_agent == "B"
    assert run.handoffs[1].content == "A-RESULT"
    assert all(isinstance(h, Handoff) for h in run.handoffs)


def test_single_agent_crew():
    a = Agent("solo", "generalist", responder=lambda p: "done")
    run = Crew([a]).run("t")
    assert run.final_output == "done"
    assert len(run.handoffs) == 1
    assert run.handoffs[0].from_agent is None


def test_empty_crew_rejected():
    with pytest.raises(ValueError):
        Crew([])


def test_run_pair_helper():
    a = Agent("A", "maker", responder=lambda p: "draft")
    b = Agent("B", "reviewer", responder=lambda p: "final")
    run = run_pair("build it", a, b)
    assert run.final_output == "final"
    assert run.agents == ["A", "B"]


# --- Dynamic team planning -------------------------------------------------

VALID_JSON = (
    '[{"name":"Rae","role":"researcher","goal":"gather"},'
    ' {"name":"Val","role":"writer","goal":"write"}]'
)


def test_parse_team_valid():
    specs = _parse_team(VALID_JSON, max_agents=4)
    assert [s.role for s in specs] == ["researcher", "writer"]
    assert specs[0].name == "Rae"
    assert all(isinstance(s, AgentSpec) for s in specs)


def test_parse_team_extracts_json_from_prose():
    text = "Sure! Here is the team:\n" + VALID_JSON + "\nHope that helps."
    specs = _parse_team(text, max_agents=4)
    assert len(specs) == 2


def test_parse_team_clamps_to_max():
    big = "[" + ",".join(
        '{"name":"A%d","role":"r%d"}' % (i, i) for i in range(10)) + "]"
    specs = _parse_team(big, max_agents=3)
    assert len(specs) == 3


def test_parse_team_drops_items_without_role():
    text = '[{"name":"X"}, {"role":"writer"}]'
    specs = _parse_team(text, max_agents=4)
    assert len(specs) == 1
    assert specs[0].role == "writer"


def test_parse_team_garbage_returns_empty():
    assert _parse_team("no json here", max_agents=4) == []
    assert _parse_team("", max_agents=4) == []


def test_plan_team_uses_planner_reply():
    specs = plan_team("do X", planner=lambda p: VALID_JSON, max_agents=4)
    assert [s.role for s in specs] == ["researcher", "writer"]


def test_plan_team_falls_back_to_generalist_on_garbage():
    specs = plan_team("do X", planner=lambda p: "nonsense", max_agents=4)
    assert len(specs) == 1
    assert specs[0].role == "generalist"


def test_plan_team_passes_max_into_prompt():
    seen = {}

    def planner(prompt):
        seen["p"] = prompt
        return VALID_JSON

    plan_team("the task", planner=planner, max_agents=5)
    assert "the task" in seen["p"]
    assert "5" in seen["p"]


def test_build_crew_from_specs_runs():
    specs = [AgentSpec("A", "researcher", "g1"), AgentSpec("B", "writer", "g2")]
    crew = build_crew(specs, tools_enabled=False)
    # Swap in fake responders so no LLM is needed.
    for agent, out in zip(crew.agents, ["one", "two"]):
        agent._responder = (lambda o: (lambda p: o))(out)
    run = crew.run("task")
    assert run.final_output == "two"
    assert run.agents == ["A", "B"]
