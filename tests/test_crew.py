"""Tests for the Phase-0 multi-agent collaboration layer (core/crew.py).

These use injected responders, so no LLM, provider or credentials are needed —
they exercise the orchestration and the hand-off log, not the model.
"""

import pytest

from core.crew import Agent, Crew, Handoff, CrewRun, run_pair


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
