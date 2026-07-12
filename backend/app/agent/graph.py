"""
LangGraph agent that powers the conversational side of the Log Interaction
Screen.

Role of the agent:
  The field rep chats naturally ("Just met Dr. Rao, discussed CardioMax,
  left 3 samples, she seemed positive but asked about pricing"). The agent
  decides which CRM tool(s) to call -- pulling HCP context, logging the
  interaction, checking compliance on samples, scheduling a follow-up,
  or editing a record the rep corrects mid-conversation -- and then
  replies conversationally confirming what it did. It removes the need
  for the rep to fill out a structured form by hand in the field.

Graph shape:

    START -> agent -> (conditional) -> tools -> agent -> ... -> END
                    -> (no tool calls) -> END

  "agent" node: calls the Groq LLM (bound to the tool schemas) with the
  running message history and decides whether to respond directly or
  invoke one or more tools.

  "tools" node: executes whichever tools the agent requested (via the
  prebuilt ToolNode) and appends their results to the message history as
  ToolMessages, then loops back to "agent" so it can use those results
  to keep working or produce a final natural-language reply.
"""
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import ainvoke_resilient
from app.agent.tools import build_tools

SYSTEM_PROMPT = """You are an AI assistant embedded in a pharmaceutical CRM,
helping a field representative log and manage interactions with Healthcare
Professionals (HCPs) through natural conversation instead of a form.

Guidelines:
- When the rep describes a visit/call, first call get_hcp_context if you
  don't already know the HCP, then call log_interaction with their notes.
- If samples were mentioned, call check_sample_compliance after logging
  and mention any flags to the rep.
- If the rep asks to change something they already logged, use
  edit_interaction.
- If it's a good moment for a follow-up (HCP asked a question you couldn't
  answer, expressed interest, etc.), call schedule_follow_up.
- You can call suggest_next_best_action if the rep asks what to do next
  with this HCP.
- Always confirm actions back to the rep in one or two friendly, concise
  sentences. Never invent HCP IDs -- ask the rep to specify the HCP if
  it's ambiguous or missing from context.
"""


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def build_agent_graph(db: AsyncSession):
    tools = build_tools(db)

    async def agent_node(state: AgentState):
        messages = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
        # Resilient: tries gemma2-9b-it first (assignment-mandated), falls
        # through to a currently-supported Groq model only if Groq reports
        # the requested one as decommissioned. See app/agent/llm.py.
        response, _model_used = await ainvoke_resilient(messages, temperature=0.2, tools=tools)
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()
