import asyncio
import json
from uuid import uuid4

import httpx

from a2a.client import A2AClient, A2ACardResolver
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    FilePart,
    FileWithBytes,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendStreamingMessageRequest,
    SendStreamingMessageSuccessResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TextPart,
    TaskStatusUpdateEvent,
)
from a2a.utils import get_message_text, new_agent_text_message

from .asin_env import ASINEnv


class ASINGreenA2AAgent:
    """
    ASIN Green Agent orchestrator (A2A).

    Receives an assessment request message (JSON) with:
      - participants: { role: endpoint }
      - config: { num_tasks: int, ... }

    Then runs num_tasks tasks, repeatedly prompting the participant agent(s)
    for an action, stepping the ASIN environment, and emitting per-task result artifacts.
    """

    def __init__(self):
        self.env = ASINEnv()
        self._client_cache: dict[str, tuple[A2AClient, httpx.AsyncClient]] = {}

    async def _get_client(self, endpoint: str) -> A2AClient:
        cached = self._client_cache.get(endpoint)
        if cached:
            return cached[0]

        httpx_client = httpx.AsyncClient(timeout=60.0)
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=endpoint)
        card = await resolver.get_agent_card(relative_card_path="/.well-known/agent-card.json")
        if card is None:
            # Fallback path used by some A2A servers
            card = await resolver.get_agent_card(relative_card_path="/.well-known/agent.json")
        if card is None:
            await httpx_client.aclose()
            raise RuntimeError(f"Failed to resolve agent card from {endpoint}")

        client = A2AClient(httpx_client=httpx_client, agent_card=card)
        self._client_cache[endpoint] = (client, httpx_client)
        return client

    def _parse_eval_request(self, message: Message) -> dict:
        # Prefer structured DataParts
        for part in message.parts:
            if isinstance(part.root, DataPart) and isinstance(part.root.data, dict):
                return part.root.data

        # Fallback: message text is JSON
        text = get_message_text(message) or ""
        try:
            return json.loads(text)
        except Exception as e:
            raise ValueError(f"Invalid assessment request payload (expected JSON). Got: {text[:200]!r}") from e

    async def _ask_navigator(
        self,
        navigator_endpoint: str,
        prompt_text: str,
        map_b64: str,
        view_b64: str,
        *,
        include_map: bool = True,
    ) -> str:
        client = await self._get_client(navigator_endpoint)

        parts: list[Part] = [Part(root=TextPart(text=prompt_text))]

        # Send the route map only once per level/task (first step).
        if include_map:
            parts.append(
                Part(
                    root=FilePart(
                        file=FileWithBytes(
                            bytes=map_b64, mime_type="image/png", name="map.png"
                        )
                    )
                )
            )

        # Always send the current Street View.
        parts.append(
            Part(
                root=FilePart(
                    file=FileWithBytes(
                        bytes=view_b64, mime_type="image/jpeg", name="street_view.jpg"
                    )
                )
            )
        )

        params = MessageSendParams(
            message=Message(
                role=Role.user,
                parts=parts,
                message_id=uuid4().hex,
                task_id=None,
            )
        )
        req = SendStreamingMessageRequest(id=str(uuid4()), params=params)

        # Collect DataParts from artifact updates (preferred)
        data_payloads: list[dict] = []
        async for chunk in client.send_message_streaming(req):
            if not isinstance(chunk.root, SendStreamingMessageSuccessResponse):
                continue
            event = chunk.root.result
            if isinstance(event, TaskArtifactUpdateEvent):
                for p in event.artifact.parts:
                    if isinstance(p.root, DataPart) and isinstance(p.root.data, dict):
                        data_payloads.append(p.root.data)
            elif isinstance(event, TaskStatusUpdateEvent):
                # Ignore status text; the purple agent should emit an Action artifact
                pass

        # Use first action we received
        for payload in data_payloads:
            action = payload.get("action")
            if isinstance(action, str) and action.strip():
                return action.strip()

        # Deterministic fallback
        return "f"

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        await updater.update_status(TaskState.working, new_agent_text_message("Starting assessment..."))

        req = self._parse_eval_request(message)
        participants = req.get("participants", {}) or {}
        config = req.get("config", {}) or {}

        # Single-player benchmark: expect one navigator role.
        if not isinstance(participants, dict) or not participants:
            raise ValueError("Assessment request missing participants mapping")

        # Prefer role named "navigator", otherwise take the first participant
        navigator_endpoint = participants.get("navigator") or next(iter(participants.values()))

        num_tasks = int(config.get("num_tasks", 10))
        num_tasks = max(1, min(50, num_tasks))

        results = []
        for task_index in range(num_tasks):
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(f"Running task {task_index + 1}/{num_tasks}..."),
            )

            session_id = f"{message.context_id or 'ctx'}-{uuid4().hex}-t{task_index}"
            state = self.env.start(session_id, task_config={"task_index": task_index})
            if state.get("done") and state.get("error"):
                # Record failure deterministically
                results.append({"score": 0, "error": state.get("error"), "task_index": task_index})
                await updater.add_artifact(
                    parts=[Part(root=DataPart(data=results[-1]))],
                    name=f"task_{task_index}_result",
                )
                continue

            step_guard = 0
            while not state.get("done", False):
                step_guard += 1
                if step_guard > 2000:
                    break

                prompt_text = state.get("prompt", "")
                imgs = state.get("images") or []
                map_b64 = imgs[0] if len(imgs) > 0 else self.env.logic._placeholder_png_b64(640, 640, "Map missing")
                view_b64 = imgs[1] if len(imgs) > 1 else self.env.logic._placeholder_png_b64(640, 400, "View missing")

                action = await self._ask_navigator(
                    navigator_endpoint,
                    prompt_text,
                    map_b64,
                    view_b64,
                    include_map=(step_guard == 1),
                )
                state = self.env.act(session_id, action)

                # Avoid tight loops; deterministic small delay for network stability
                await asyncio.sleep(0)

            res = self.env.result(session_id)
            # Make sure result is JSON-serializable and has a top-level score field
            if not isinstance(res, dict):
                res = {"score": 0, "error": "Invalid result type"}
            res["task_index"] = task_index
            results.append(res)

            await updater.add_artifact(
                parts=[Part(root=DataPart(data=res))],
                name=f"task_{task_index}_result",
            )

        await updater.update_status(
            TaskState.completed,
            new_agent_text_message(f"Completed {len(results)}/{num_tasks} tasks."),
        )

