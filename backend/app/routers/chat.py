import json
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ChatMessage, Interaction
from app.schemas import ChatTurnRequest, ChatTurnResponse, InteractionOut
from app.agent.graph import build_agent_graph

router = APIRouter(prefix="/api/chat", tags=["Chat"])

# In-memory per-session LangChain message history (fine for a demo/assignment;
# a production build would persist + rehydrate this from the DB per request).
_SESSION_HISTORY: dict[str, list] = {}


@router.post("/turn", response_model=ChatTurnResponse)
async def chat_turn(payload: ChatTurnRequest, db: AsyncSession = Depends(get_db)):
    history = _SESSION_HISTORY.setdefault(payload.session_id, [])

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
            role="user",
            content=payload.message,
        )
    )
    await db.commit()

    graph = build_agent_graph(db)
    result = await graph.ainvoke({"messages": history})

    new_messages = result["messages"]
    _SESSION_HISTORY[payload.session_id] = new_messages

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
                final_reply = m.content

        if isinstance(m, ToolMessage) and m.name == "log_interaction":
            try:
                data = json.loads(m.content)
                logged_interaction_id = data.get("interaction_id")
            except Exception:
                pass

    for m in new_messages:
        if isinstance(m, ToolMessage):
            db.add(
                ChatMessage(
                    session_id=payload.session_id,
                    role="tool",
                    content=str(m.content),
                    tool_name=m.name,
                )
            )

    db.add(
        ChatMessage(
            session_id=payload.session_id,
            role="assistant",
            content=final_reply,
        )
    )

    await db.commit()

    interaction_out = None
    if logged_interaction_id:
        interaction = await db.get(Interaction, logged_interaction_id)
        if interaction:
            interaction_out = InteractionOut.model_validate(interaction)

    return ChatTurnResponse(
        session_id=payload.session_id,
        reply=final_reply or "Got it.",
        tool_calls=tool_calls_log,
        interaction=interaction_out,
    )


@router.get("/{session_id}/history")
async def get_history(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )

    rows = result.scalars().all()

    return [
        {
            "role": row.role,
            "content": row.content,
            "tool_name": row.tool_name,
        }
        for row in rows
    ]