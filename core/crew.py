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

import json
import re
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
    stopped: bool = False          # True if a stop was requested mid-run


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

    def respond(self, text: str) -> str:
        """Raw reply to an already-framed prompt (used by the interactive Room,
        which builds its own room-aware prompt instead of the role template)."""
        return self._reply(text)

    def cancel(self):
        """Best-effort stop: ask the agent's underlying chat to abort its tool
        loop. A single LLM call already in flight can't be interrupted cleanly,
        but this cuts any further tool iterations as soon as it checks."""
        chat = self._chat
        if chat is not None and hasattr(chat, "cancelado"):
            try:
                chat.cancelado = True
            except Exception:
                pass

    @property
    def conversation_dir(self):
        """The agent's conversation folder once its real chat exists, else None.
        Useful for the collaboration view and for demo cleanup."""
        return getattr(self._chat, "conversacion_dir", None) if self._chat else None

    @property
    def last_usage(self):
        """Token/cost usage of the agent's last turn (best-effort), or None."""
        return getattr(self._chat, "uso_turno", None) if self._chat else None

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

    def run(self, task: str,
            progress: Optional[Callable[[str, object], None]] = None,
            should_stop: Optional[Callable[[], bool]] = None) -> CrewRun:
        """Run the crew. If ``progress`` is given, it's called as the run unfolds
        — ``progress("handoff", Handoff)`` when a message passes between agents,
        and ``progress("result", (agent_name, output))`` when an agent finishes —
        so a UI can show the conversation live instead of only at the end.

        If ``should_stop`` is given, it's checked before each agent; when it
        returns True the run stops there (the agent already in flight finishes,
        but no further agent starts) and the result is flagged ``stopped``."""
        def emit(kind, data):
            if progress is not None:
                progress(kind, data)

        def stop_requested() -> bool:
            return bool(should_stop and should_stop())

        handoffs: List[Handoff] = []
        prev: Optional[Agent] = None
        prev_output = ""
        stopped = False

        for agent in self.agents:
            if stop_requested():
                stopped = True
                break

            if prev is None:
                body = task
                h = Handoff(None, agent.name, task)
            else:
                body = _HANDOFF_TEMPLATE.format(
                    task=task, prev_name=prev.name, prev_role=prev.role,
                    prev_output=prev_output)
                h = Handoff(prev.name, agent.name, prev_output)
            handoffs.append(h)
            emit("handoff", h)

            prev_output = agent.run(body)
            emit("result", (agent.name, prev_output))
            prev = agent

        return CrewRun(task=task, final_output=prev_output, handoffs=handoffs,
                       agents=[a.name for a in self.agents], stopped=stopped)


def run_pair(task: str, first: Agent, second: Agent) -> CrewRun:
    """Convenience for the Phase-0 two-agent case: ``first`` does the work,
    ``second`` reviews/refines it."""
    return Crew([first, second]).run(task)


# --- Dynamic team planning --------------------------------------------------
#
# Instead of the human spelling out the roster, a planner agent reads the task
# and proposes the *smallest* team that can solve it by collaborating. The user
# can still review/edit the proposal before it runs — which doubles as oversight,
# the same spirit as the rest of the app's security model.

@dataclass
class AgentSpec:
    """A planned agent before it's instantiated: just a role on paper."""
    name: str
    role: str
    goal: str = ""


_PLAN_PROMPT = (
    "You are a planner. Break the task below into the SMALLEST team of "
    "specialized agents that can solve it well by collaborating in sequence — "
    "each agent builds on the previous one's output. Use between 1 and {max} "
    "agents: only as many as genuinely help; do not pad the team.\n\n"
    "Reply with ONLY a JSON array, no prose before or after. Each item:\n"
    '  {{"name": "<ONE short word, no spaces, easy to @mention>", '
    '"role": "<what they are>", "goal": "<their concrete job>"}}\n\n'
    "Task:\n{task}"
)


def _parse_team(text: str, max_agents: int) -> List[AgentSpec]:
    """Pull a team out of the planner's reply. Tolerant: finds the JSON array
    even if the model wrapped it in prose, and drops malformed items. Returns an
    empty list if nothing usable is found (the caller decides the fallback)."""
    if not text:
        return []
    # Grab from the first '[' to the last ']' — survives stray prose/code fences.
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        raw = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []

    specs: List[AgentSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if not role:
            continue
        name = str(item.get("name") or f"Agent {len(specs) + 1}").strip()
        goal = str(item.get("goal") or "").strip()
        specs.append(AgentSpec(name=name, role=role, goal=goal))
        if len(specs) >= max_agents:
            break
    return specs


def plan_team(task: str, planner: Optional[Responder] = None,
              max_agents: int = 4) -> List[AgentSpec]:
    """Ask the planner how many agents (and which roles) this task needs.

    ``planner`` is the LLM seam for testing. Without it, a tool-less
    ConversacionChat does the planning. Always returns at least one agent: if the
    model is unavailable or its answer can't be parsed, it falls back to a single
    generalist, so the crew can still run."""
    max_agents = max(1, int(max_agents))
    prompt = _PLAN_PROMPT.format(max=max_agents, task=task)

    if planner is not None:
        reply = planner(prompt)
    else:
        from core.conversaciones import GestorConversaciones
        from core.chat import ConversacionChat
        conv_dir = GestorConversaciones().crear_conversacion("crew · planner")
        chat = ConversacionChat(conv_dir)
        if hasattr(chat, "tools_habilitadas"):
            chat.tools_habilitadas = False
        reply = chat.enviar(prompt)

    specs = _parse_team(reply, max_agents)
    if not specs:
        specs = [AgentSpec("Agent", "generalist",
                           "solve the whole task on your own")]
    return specs


def build_crew(specs: List[AgentSpec], tools_enabled: bool = True,
               name: str = "crew") -> Crew:
    """Turn a planned roster into a runnable Crew."""
    agents = [Agent(s.name, s.role, s.goal, tools_enabled=tools_enabled)
              for s in specs]
    return Crew(agents, name=name)


# --- Interactive room (swarm + human-in-the-loop) ---------------------------
#
# A Room turns the one-shot relay into a live group chat the human takes part in.
# Everyone reads everyone by default, EXCEPT a message the human directs with
# "@Name ...", which is private to that agent — the others never receive it. A
# plain message goes to the whole room and every agent answers in turn.

@dataclass
class Message:
    speaker: str                   # "user", "system", or an agent's name
    text: str
    audience: str = "all"          # "all" (public) or an agent name (private)
    ts: float = field(default_factory=time.time)


_ROOM_PROMPT = (
    "You are «{name}» in a group chat, acting as: {role}. Your goal: {goal}.\n"
    "Teammates and the human share this room and read what you write. New "
    "messages since your last turn:\n-----\n{context}\n-----\n"
    "Reply briefly and in character as «{name}», in the same language as the "
    "conversation. Don't prefix your reply with your own name."
)


def parse_mention(text: str):
    """('Name', 'rest') if the message starts with @Name, else (None, text)."""
    m = re.match(r"\s*@([^\s:,]+)[\s:,]*(.*)", text, re.DOTALL)
    if m:
        return m.group(1), m.group(2).strip()
    return None, text


class Room:
    """A live group chat over a set of agents, with the human as a participant.

    ``progress`` (if given) is called with each new ``Message`` as it appears, so
    a UI can render the room live. ``should_stop`` is checked before each agent
    turn. Each agent keeps its own memory (its ConversacionChat); the room only
    feeds each agent the messages it is allowed to see since its last turn."""

    def __init__(self, agents: List[Agent]):
        if not agents:
            raise ValueError("a room needs at least one agent")
        self.agents = list(agents)
        self._by_name = {a.name: a for a in self.agents}
        self.transcript: List[Message] = []
        self._seen = {a.name: 0 for a in self.agents}   # index consumed per agent

    def has_agent(self, name: str) -> bool:
        return name in self._by_name

    def _resolve_mention(self, text: str):
        """('AgentName', 'body') if the message starts with @ and names a real
        agent (matched case-insensitively, longest name wins so multi-word names
        work), else (None, text) → broadcast. Resolving against the actual roster
        is what makes '@Naming Specialist' target the right agent."""
        t = text.lstrip()
        if not t.startswith("@"):
            return None, text
        rest = t[1:]
        low = rest.lower()
        best = None
        for name in self._by_name:
            if low.startswith(name.lower()) and (best is None or len(name) > len(best)):
                best = name
        if best is None:
            return None, text
        return best, rest[len(best):].lstrip(" :,").strip()

    def _visible_for(self, name: str, upto: int) -> List[Message]:
        """New messages an agent may read: public ones, its own, and private
        ones addressed to it. A private @X message is invisible to everyone but X."""
        out = []
        for msg in self.transcript[self._seen[name]:upto]:
            if msg.audience == "all" or msg.audience == name or msg.speaker == name:
                out.append(msg)
        return out

    def _agent_turn(self, agent: Agent, emit) -> str:
        upto = len(self.transcript)
        nuevos = self._visible_for(agent.name, upto)
        context = "\n".join(f"{m.speaker}: {m.text}" for m in nuevos) or "(nothing new)"
        prompt = _ROOM_PROMPT.format(name=agent.name, role=agent.role,
                                     goal=agent.goal, context=context)
        reply = agent.respond(prompt)
        self.transcript.append(Message(agent.name, reply, "all"))
        self._seen[agent.name] = len(self.transcript)   # it has now seen its own
        emit(self.transcript[-1])
        return reply

    def _emitter(self, progress):
        return (lambda m: progress(m)) if progress else (lambda m: None)

    def kickoff(self, task: str, progress=None, should_stop=None) -> None:
        """Open the room with the initial task; every agent speaks once, in order,
        each seeing the task and the teammates who went before it."""
        emit = self._emitter(progress)
        stop = lambda: bool(should_stop and should_stop())
        self.transcript.append(Message("user", task, "all"))
        emit(self.transcript[-1])
        for agent in self.agents:
            if stop():
                break
            self._agent_turn(agent, emit)

    def user_message(self, text: str, progress=None, should_stop=None) -> None:
        """The human speaks. '@Name ...' is private to Name (only Name answers);
        anything else is public and every agent answers in turn."""
        emit = self._emitter(progress)
        stop = lambda: bool(should_stop and should_stop())

        name, body = self._resolve_mention(text)
        if name:
            self.transcript.append(Message("user", body, name))   # private to Name
            emit(self.transcript[-1])
            if not stop():
                self._agent_turn(self._by_name[name], emit)
        else:
            self.transcript.append(Message("user", text, "all"))  # public
            emit(self.transcript[-1])
            for agent in self.agents:
                if stop():
                    break
                self._agent_turn(agent, emit)
