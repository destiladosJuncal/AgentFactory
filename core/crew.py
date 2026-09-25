"""
Multi-agent collaboration on top of the existing single-agent conversation.

An ``Agent`` here is a role wrapped around a ``ConversacionChat``: it keeps the
whole existing capability set (tools, the shared library, workflows and the
security guards) and only adds a persona and a job. A ``Crew`` runs a few of
these agents and passes work between them, recording every hand-off so we can
later show *how* they collaborated.

This is Phase 0 on purpose — two (or a few) agents, a sequential hand-off, and a
transcript of who said what to whom. The next phase instruments what each
agent's code actually touches (file/network/subprocess events via Python audit
hooks); that monitor is the safety net that makes handing more autonomy to a
team of agents acceptable, which matters more here than in the single-agent
case because there is less human review per action.

New code, written in English from the start so it never needs the translation
pass the rest of ``core`` is going through. It only *calls* the existing
(Spanish-named) ``ConversacionChat`` / ``GestorConversaciones`` API; it doesn't
depend on their internals.

Testing: an ``Agent`` takes an optional ``responder`` callable ``(str) -> str``.
Inject a fake one to exercise the orchestration without an LLM or credentials;
leave it out and the agent lazily builds a real ``ConversacionChat`` (which, with
no provider configured, simply answers in simulation mode).
"""

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# Prompt scaffolding sent to each agent. Kept as module constants so the wording
# is easy to tune and, later, to route through the i18n layer.
_ROLE_TEMPLATE = (
    "You are «{name}», acting as: {role}.\n"
    "Your goal: {goal}\n\n"
    "{body}"
)
_HANDOFF_TEMPLATE = (
    "Original task:\n{task}\n\n"
    "A teammate («{prev_name}», {prev_role}) handed you their result:\n"
    "-----\n{prev_output}\n-----\n\n"
    "Do your part and produce your output."
)

Responder = Callable[[str], str]


@dataclass
class Handoff:
    """One message passing from one agent to the next (or the task coming in)."""
    from_agent: Optional[str]      # None means "the human / the original task"
    to_agent: str
    content: str
    ts: float = field(default_factory=time.time)


@dataclass
class CrewRun:
    """The result of running a crew: the final output plus the full hand-off log,
    which is exactly what a collaboration view will render."""
    task: str
    final_output: str
    handoffs: List[Handoff]
    agents: List[str]


class Agent:
    """A role-playing agent backed by a ConversacionChat.

    ``responder`` is the seam for testing: a callable that maps a prompt to a
    reply. When omitted, the agent builds a real conversation on first use.
    """

    def __init__(self, name: str, role: str, goal: str = "",
                 responder: Optional[Responder] = None,
                 tools_enabled: bool = True):
        self.name = name
        self.role = role
        self.goal = goal or role
        self.tools_enabled = tools_enabled
        self._responder = responder
        self._chat = None  # lazily built ConversacionChat, only if no responder

    # -- the LLM seam --------------------------------------------------------

    def _reply(self, prompt: str) -> str:
        if self._responder is not None:
            return self._responder(prompt)
        return self._real_chat().enviar(prompt)

    def _real_chat(self):
        """Build (once) a real ConversacionChat for this agent. Imported lazily so
        the module stays cheap to import and testable without the chat stack."""
        if self._chat is None:
            from core.conversaciones import GestorConversaciones
            from core.chat import ConversacionChat
            conv_dir = GestorConversaciones().crear_conversacion(f"crew · {self.name}")
            chat = ConversacionChat(conv_dir)
            # Respect the agent's tool policy when the attribute exists.
            if hasattr(chat, "tools_habilitadas"):
                chat.tools_habilitadas = self.tools_enabled
            self._chat = chat
        return self._chat

    # -- running -------------------------------------------------------------

    def run(self, body: str) -> str:
        """Frame ``body`` with this agent's role/goal, ask, and return the reply."""
        prompt = _ROLE_TEMPLATE.format(
            name=self.name, role=self.role, goal=self.goal, body=body)
        return self._reply(prompt)


class Crew:
    """A team of agents that hands work down a line (sequential process).

    ``run`` feeds the task to the first agent, then hands each agent the original
    task plus the previous agent's output, and returns the last output together
    with the full hand-off log.
    """

    def __init__(self, agents: List[Agent], name: str = "crew"):
        if not agents:
            raise ValueError("a crew needs at least one agent")
        self.agents = agents
        self.name = name

    def run(self, task: str) -> CrewRun:
        handoffs: List[Handoff] = []
        prev: Optional[Agent] = None
        prev_output = ""

        for agent in self.agents:
            if prev is None:
                body = task
                handoffs.append(Handoff(None, agent.name, task))
            else:
                body = _HANDOFF_TEMPLATE.format(
                    task=task, prev_name=prev.name, prev_role=prev.role,
                    prev_output=prev_output)
                handoffs.append(Handoff(prev.name, agent.name, prev_output))

            prev_output = agent.run(body)
            prev = agent

        return CrewRun(task=task, final_output=prev_output,
                       handoffs=handoffs, agents=[a.name for a in self.agents])


def run_pair(task: str, first: Agent, second: Agent) -> CrewRun:
    """Convenience for the Phase-0 two-agent case: ``first`` does the work,
    ``second`` reviews/refines it."""
    return Crew([first, second]).run(task)
