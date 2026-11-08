import json
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ChatMessage, Interaction
from app.schemas import ChatTurnRequest, ChatTurnResponse
from app.agent.graph import build_agent_graph
from app.authz import Authz, get_authz
from app.ratelimit import llm_rate_limit
from app.routers.interactions import interactions_out

router = APIRouter(prefix="/api/chat", tags=["Chat"])

# In-memory LangChain message history, keyed by (rep, session) so one rep can never
# continue or read another rep's conversation (a production build would persist +
# rehydrate this from the DB per request).
_SESSION_HISTORY: dict[tuple[str, str], list] = {}


async def _session_owners(db: AsyncSession, session_id: str) -> set[str]:
    rows = await db.execute(select(ChatMessage.owner_id).where(ChatMessage.session_id == session_id).distinct())
    return {r[0] for r in rows.all()}


@router.post("/turn", response_model=ChatTurnResponse)
async def chat_turn(payload: ChatTurnRequest, authz: Authz = Depends(llm_rate_limit), db: AsyncSession = Depends(get_db)):
    owners = await _session_owners(db, payload.session_id)
    if owners and owners != {authz.user.id}:
        # Someone else's session id: never continue it (not even as admin).
        raise HTTPException(404, "Chat session not found")
    history = _SESSION_HISTORY.setdefault((authz.user.id, payload.session_id), [])

    # If the frontend already knows which HCP is selected,
    # explicitly provide that context to the LLM.
    if payload.hcp_id:
        user_content = f"""Selected HCP ID: {payload.hcp_id}

User message:
{payload.message}

IMPORTANT:
- Use the Selected HCP ID for every CRM tool call.
- Never replace the Selected HCP ID with the doctor's name.
- Treat the Selected HCP ID as authoritative.
""".strip()
    else:
        user_content = payload.message

    user_msg = HumanMessage(content=user_content)
    history.append(user_msg)

    # Store the original user message (without injected instructions)
    db.add(
        ChatMessage(
            session_id=payload.session_id,
            owner_id=authz.user.id,
            role="user",
            content=payload.message,
        )
    )
    await db.commit()

    graph = build_agent_graph(db, authz)
    turn_start = len(history)  # history already includes this turn's user message
    result = await graph.ainvoke({"messages": history})

    all_messages = result["messages"]
    _SESSION_HISTORY[(authz.user.id, payload.session_id)] = all_messages
    # Only what the agent produced during *this* turn. The graph returns the
    # whole session; reporting/persisting all of it duplicated earlier turns.
    new_messages = all_messages[turn_start:]

    tool_calls_log = []
    final_reply = ""
    logged_interaction_id = None

    for m in new_messages:
        if isinstance(m, AIMessage):
            if getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    tool_calls_log.append(
                        {
                            "tool": tc["name"],
                            "args": tc["args"],
                        }
                    )
            if m.content:
                final_reply = m.content  # type: ignore[assignment]  # BaseMessage.content is str | list; Groq returns str here

        if isinstance(m, ToolMessage) and m.name == "log_interaction":
            try:
                data = json.loads(m.content)  # type: ignore[arg-type]  # ToolMessage.content is str | list; our tools always return a JSON str
                logged_interaction_id = data.get("interaction_id")
            except Exception:
                pass

    for m in new_messages:
        if isinstance(m, ToolMessage):
            db.add(
                ChatMessage(
                    session_id=payload.session_id,
                    owner_id=authz.user.id,
                    role="tool",
                    content=str(m.content),
                    tool_name=m.name,
                    tool_call_id=m.tool_call_id,
                )
            )

    db.add(
        ChatMessage(
            session_id=payload.session_id,
            owner_id=authz.user.id,
            role="assistant",
            content=final_reply,
        )
    )

    await db.commit()

    interaction_out = None
    if logged_interaction_id:
        interaction = await db.get(Interaction, logged_interaction_id)
        if interaction and authz.can_read(interaction):
            interaction_out = (await interactions_out(db, [interaction]))[0]

    return ChatTurnResponse(
        session_id=payload.session_id,
        reply=final_reply or "Got it.",
        tool_calls=tool_calls_log,
        interaction=interaction_out,
    )


@router.get("/{session_id}/history")
async def get_history(session_id: str, authz: Authz = Depends(get_authz), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        authz.read_filter(select(ChatMessage), ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )

    rows = result.scalars().all()
    if not rows:
        raise HTTPException(404, "Chat session not found")

    return [
        {
            "role": row.role,
            "content": row.content,
            "tool_name": row.tool_name,
        }
        for row in rows
    ]