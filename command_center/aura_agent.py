"""AURA — SuffAI brand operating agent for Geo + Ads Lab.

Tool-calling loop over Vertex Gemini. UI mutations return as ui_commands for the shell;
server tools read metrics / prepare geo-brief context. Writes (campaign) require confirm.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import chatbot

log = logging.getLogger("command_center.aura_agent")

AURA_SYSTEM = chatbot.CHAT_SYSTEM + """

========================
AURA — BRAND OPERATING AGENT
========================
You are AURA (Audience · Universe · Retail Actions), SuffAI's brand operating agent.
You do not only answer — you act on the Geo Intelligence and Ads Lab dashboards for the
logged-in brand via tools.

Rules:
- Always respect the session brand. Never switch to another brand. Competitors appear only as "Competitors".
- Prefer tools for navigation and filters before explaining.
- Use get_session_context or get_metrics_summary when you need fresh facts.
- set_filters / switch_tab / focus_pin / reset_filters / export_ads_csv / run_geo_brief change the UI.
- run_ads_campaign requires confirm=true after the user explicitly agrees. If confirm is false or missing, ask for confirmation first.
- Do not invent metrics. If a tool returns an error or empty data, say what is missing.
- Brand users cannot access Style Assistant or Admin — never suggest those tools.
- After acting, give a short, business-focused summary of what you did and what the data shows.
"""

MAX_TOOL_ROUNDS = int(os.getenv("AURA_MAX_TOOL_ROUNDS", "5"))
AURA_MAX_MESSAGE_CHARS = int(os.getenv("AURA_MAX_MESSAGE_CHARS", "2000"))


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    filter_context: Optional[dict[str, Any]] = None
    confirm_action: Optional[dict[str, Any]] = None  # { "tool": "...", "args": {...} }


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _as_list(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()][:50]
    return [str(raw).strip()] if str(raw).strip() else []


def _tool_declarations() -> list[Any]:
    from vertexai.generative_models import FunctionDeclaration  # type: ignore

    return [
        FunctionDeclaration(
            name="get_session_context",
            description="Return role, brand_name, active tab, and current Geo/Ads filters from the UI context.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        FunctionDeclaration(
            name="get_metrics_summary",
            description="Load a slim brand-masked summary of dashboard metrics.json (totals, top brands, pins).",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        FunctionDeclaration(
            name="switch_tab",
            description="Switch Command Center shell to Geo or Ads Lab.",
            parameters={
                "type": "object",
                "properties": {
                    "tab": {
                        "type": "string",
                        "enum": ["geo", "ads"],
                        "description": "Target tab id",
                    }
                },
                "required": ["tab"],
            },
        ),
        FunctionDeclaration(
            name="set_filters",
            description=(
                "Apply audience filters on the active (or specified) tab. "
                "Geo uses multi-select lists; Ads uses pin_codes plus single age/gender/RFM."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tab": {"type": "string", "enum": ["geo", "ads"]},
                    "pin_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "PIN codes to select",
                    },
                    "age_groups": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Geo: age groups list. Ads: first value used as age_group.",
                    },
                    "age_group": {"type": "string"},
                    "genders": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "gender": {"type": "string"},
                    "rfm_segments": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "rfm_segment": {"type": "string"},
                    "brands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Geo only: own brand and/or Competitors",
                    },
                    "reset": {
                        "type": "boolean",
                        "description": "If true, clear filters first",
                    },
                },
                "required": [],
            },
        ),
        FunctionDeclaration(
            name="reset_filters",
            description="Clear all filters on Geo or Ads Lab.",
            parameters={
                "type": "object",
                "properties": {
                    "tab": {"type": "string", "enum": ["geo", "ads"]},
                },
                "required": [],
            },
        ),
        FunctionDeclaration(
            name="focus_pin",
            description="Focus a PIN hub on the Geo map (triggers store scoring / brief refresh).",
            parameters={
                "type": "object",
                "properties": {
                    "pin": {"type": "string", "description": "PIN code to focus"},
                },
                "required": ["pin"],
            },
        ),
        FunctionDeclaration(
            name="run_geo_brief",
            description=(
                "Ask the Geo tab to refresh the store market brief for the current focus PIN "
                "(Yes/Consider/No + explanation)."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        FunctionDeclaration(
            name="get_ads_ranking",
            description="Return the current Ads Lab ranked creatives from UI context (if any).",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        FunctionDeclaration(
            name="run_ads_campaign",
            description=(
                "Run Ads Lab extract+rank for uploaded SKUs against current audience filters. "
                "MUST pass confirm=true only after the user explicitly agrees. "
                "If no images are loaded in Ads Lab, the UI will open a file picker."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "User confirmed the campaign run",
                    },
                    "pin_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: set these PINs before running",
                    },
                    "age_group": {"type": "string"},
                    "gender": {"type": "string"},
                    "rfm_segment": {"type": "string"},
                },
                "required": [],
            },
        ),
        FunctionDeclaration(
            name="export_ads_csv",
            description="Download the current Ads Lab ranking as CSV.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
    ]


def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    sess: dict[str, Any],
    ui_context: dict[str, Any],
    metrics_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], Optional[dict[str, Any]]]:
    """
    Returns (tool_result, ui_commands, pending_action|None).
    pending_action is set when a write needs user confirmation.
    """
    args = args if isinstance(args, dict) else {}
    role = str(sess.get("role") or "")
    brand_name = str(sess.get("brand_name") or "").strip()
    ui_commands: list[dict[str, Any]] = []
    pending: Optional[dict[str, Any]] = None

    if name == "get_session_context":
        geo = ui_context.get("geo") if isinstance(ui_context.get("geo"), dict) else {}
        ads = ui_context.get("ads") if isinstance(ui_context.get("ads"), dict) else {}
        return (
            {
                "role": role,
                "brand_name": brand_name or None,
                "display_name": sess.get("display_name"),
                "active_tab": ui_context.get("active_tab"),
                "geo_filters": geo.get("filters") or {},
                "geo_summary": geo.get("summary") or {},
                "ads_filters": ads.get("filters") or {},
                "ads_filters_label": ads.get("filters_label") or "",
                "ads_ranked_count": len(ads.get("ranked") or [])
                if isinstance(ads.get("ranked"), list)
                else 0,
            },
            ui_commands,
            None,
        )

    if name == "get_metrics_summary":
        summary = chatbot.slim_metrics_json(metrics_path)
        if role == "brand" and brand_name and "error" not in summary:
            try:
                import auth_acl

                # slim summary is not full metrics — expose brand lock note
                summary = {
                    **summary,
                    "brand_scope": brand_name,
                    "competitor_label": "Competitors",
                    "note": "Full competitor brand names are masked for brand sessions.",
                }
            except Exception:
                summary["brand_scope"] = brand_name
        return (summary, ui_commands, None)

    if name == "switch_tab":
        tab = str(args.get("tab") or "").strip().lower()
        if tab not in {"geo", "ads"}:
            return ({"ok": False, "error": "tab must be geo or ads"}, ui_commands, None)
        allowed = sess.get("allowed_tabs") or []
        if allowed and tab not in allowed:
            return ({"ok": False, "error": f"tab '{tab}' not allowed for this role"}, ui_commands, None)
        ui_commands.append({"type": "switch_tab", "tab": tab})
        return ({"ok": True, "tab": tab}, ui_commands, None)

    if name == "reset_filters":
        tab = str(args.get("tab") or ui_context.get("active_tab") or "geo").strip().lower()
        if tab not in {"geo", "ads"}:
            tab = "geo"
        ui_commands.append({"type": "reset_filters", "tab": tab})
        return ({"ok": True, "tab": tab}, ui_commands, None)

    if name == "set_filters":
        tab = str(args.get("tab") or ui_context.get("active_tab") or "geo").strip().lower()
        if tab not in {"geo", "ads"}:
            tab = "geo"
        if args.get("reset"):
            ui_commands.append({"type": "reset_filters", "tab": tab})

        pins = _as_list(args.get("pin_codes") or args.get("pins") or args.get("pin"))
        ages = _as_list(args.get("age_groups") or args.get("age_group") or args.get("age"))
        genders = _as_list(args.get("genders") or args.get("gender"))
        rfms = _as_list(args.get("rfm_segments") or args.get("rfm_segment") or args.get("rfm"))
        brands = _as_list(args.get("brands") or args.get("brand"))

        if role == "brand" and brand_name:
            # Brand may only select own brand or Competitors
            brands = [b for b in brands if b.lower() in {brand_name.lower(), "competitors"}]
            if not brands and tab == "geo":
                brands = []  # leave empty = all (own+competitors in options)

        if tab == "geo":
            ui_commands.append(
                {
                    "type": "set_filters",
                    "tab": "geo",
                    "filters": {
                        "pin": pins,
                        "age": ages,
                        "gender": genders,
                        "rfm": rfms,
                        "brand": brands,
                    },
                }
            )
        else:
            ui_commands.append(
                {
                    "type": "set_filters",
                    "tab": "ads",
                    "filters": {
                        "pin_codes": pins,
                        "age_group": ages[0] if ages else (str(args.get("age_group") or "")),
                        "gender": genders[0] if genders else (str(args.get("gender") or "")),
                        "rfm_segment": rfms[0] if rfms else (str(args.get("rfm_segment") or "")),
                    },
                }
            )
        return ({"ok": True, "tab": tab, "applied": True}, ui_commands, None)

    if name == "focus_pin":
        pin = str(args.get("pin") or args.get("pin_code") or "").strip()
        if not pin:
            return ({"ok": False, "error": "pin is required"}, ui_commands, None)
        ui_commands.append({"type": "switch_tab", "tab": "geo"})
        ui_commands.append({"type": "focus_pin", "pin": pin})
        return ({"ok": True, "pin": pin}, ui_commands, None)

    if name == "run_geo_brief":
        ui_commands.append({"type": "switch_tab", "tab": "geo"})
        ui_commands.append({"type": "run_brief"})
        geo = ui_context.get("geo") if isinstance(ui_context.get("geo"), dict) else {}
        summary = geo.get("summary") if isinstance(geo.get("summary"), dict) else {}
        return (
            {
                "ok": True,
                "queued": True,
                "current_summary_hint": {
                    "store_focus_pin": summary.get("store_focus_pin"),
                    "store_decision": summary.get("store_decision"),
                    "store_score": summary.get("store_score"),
                },
            },
            ui_commands,
            None,
        )

    if name == "get_ads_ranking":
        ads = ui_context.get("ads") if isinstance(ui_context.get("ads"), dict) else {}
        ranked = ads.get("ranked") if isinstance(ads.get("ranked"), list) else []
        return (
            {
                "ok": True,
                "filters_label": ads.get("filters_label") or "",
                "filters": ads.get("filters") or {},
                "ranked": ranked[:20],
                "count": len(ranked),
            },
            ui_commands,
            None,
        )

    if name == "run_ads_campaign":
        confirm = bool(args.get("confirm"))
        pins = _as_list(args.get("pin_codes"))
        age = str(args.get("age_group") or "").strip()
        gender = str(args.get("gender") or "").strip()
        rfm = str(args.get("rfm_segment") or "").strip()
        if pins or age or gender or rfm:
            ui_commands.append(
                {
                    "type": "set_filters",
                    "tab": "ads",
                    "filters": {
                        "pin_codes": pins,
                        "age_group": age,
                        "gender": gender,
                        "rfm_segment": rfm,
                    },
                }
            )
        if not confirm:
            pending = {
                "tool": "run_ads_campaign",
                "args": {
                    "confirm": True,
                    "pin_codes": pins,
                    "age_group": age,
                    "gender": gender,
                    "rfm_segment": rfm,
                },
            }
            return (
                {
                    "ok": False,
                    "needs_confirmation": True,
                    "message": (
                        "Confirm to upload/extract/rank SKUs in Ads Lab for the selected audience. "
                        "If no images are loaded yet, the file picker will open."
                    ),
                },
                ui_commands,
                pending,
            )
        ui_commands.append({"type": "switch_tab", "tab": "ads"})
        ui_commands.append({"type": "run_campaign"})
        return (
            {
                "ok": True,
                "queued": True,
                "note": "Ads Lab will run campaign with current images, or open the file picker if empty.",
            },
            ui_commands,
            None,
        )

    if name == "export_ads_csv":
        ui_commands.append({"type": "switch_tab", "tab": "ads"})
        ui_commands.append({"type": "export_csv"})
        return ({"ok": True, "queued": True}, ui_commands, None)

    return ({"ok": False, "error": f"Unknown tool: {name}"}, ui_commands, None)


def _extract_function_calls(response: Any) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            fc = getattr(part, "function_call", None)
            if not fc:
                continue
            fname = str(getattr(fc, "name", "") or "")
            raw_args = getattr(fc, "args", None) or {}
            if hasattr(raw_args, "items"):
                args = {str(k): v for k, v in dict(raw_args).items()}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(str(raw_args))
                except Exception:
                    args = {}
            if fname:
                calls.append((fname, args))
    return calls


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None) or ""
    if text.strip():
        return text.strip()
    parts_out: list[str] = []
    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for p in getattr(content, "parts", None) or []:
            t = getattr(p, "text", None)
            if t:
                parts_out.append(t)
    return "\n".join(parts_out).strip()


def run_agent(
    body: AgentChatRequest,
    *,
    sess: dict[str, Any],
    metrics_path: Path,
) -> Union[dict[str, Any], JSONResponse]:
    request_id = uuid.uuid4().hex[:12]
    message = (body.message or "").strip()
    if not message:
        return JSONResponse(
            status_code=400,
            content={"detail": "message is required", "request_id": request_id},
        )
    if len(message) > AURA_MAX_MESSAGE_CHARS:
        return JSONResponse(
            status_code=400,
            content={
                "detail": f"message too long (max {AURA_MAX_MESSAGE_CHARS} chars)",
                "request_id": request_id,
            },
        )
    if not _env_bool("ENABLE_VERTEX", False):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "AURA disabled: set ENABLE_VERTEX=true on command-center",
                "request_id": request_id,
            },
        )

    role = str(sess.get("role") or "")
    if role not in {"brand", "admin"}:
        return JSONResponse(
            status_code=403,
            content={
                "detail": "AURA is available for brand and admin sessions only",
                "request_id": request_id,
            },
        )

    ui_ctx = chatbot.sanitize_ui_context(body.context)
    filter_ctx = chatbot.sanitize_filter_context(body.filter_context)
    if not filter_ctx and isinstance(body.context, dict):
        filter_ctx = chatbot.sanitize_filter_context(body.context.get("filter_context"))

    # User clicked Confirm in UI — inject as an explicit tool run instruction
    if isinstance(body.confirm_action, dict) and body.confirm_action.get("tool"):
        conf_tool = str(body.confirm_action.get("tool"))
        conf_args = body.confirm_action.get("args") if isinstance(body.confirm_action.get("args"), dict) else {}
        conf_args = {**conf_args, "confirm": True}
        message = (
            f"{message}\n\n[SYSTEM: User confirmed action {conf_tool} with args "
            f"{json.dumps(conf_args, default=str)}. Call {conf_tool} with confirm=true now.]"
        )

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
    location = os.getenv("VERTEX_LOCATION", "asia-south1")
    model_name = os.getenv("VERTEX_CHAT_MODEL", "gemini-2.0-flash")
    if not project:
        return JSONResponse(
            status_code=503,
            content={"detail": "GOOGLE_CLOUD_PROJECT is not set", "request_id": request_id},
        )

    try:
        import vertexai  # type: ignore
        from vertexai.generative_models import (  # type: ignore
            Content,
            GenerativeModel,
            Part,
            Tool,
        )
    except ImportError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": f"Missing Vertex libs: {exc}", "request_id": request_id},
        )

    dashboard_ctx = {
        "session": {
            "role": role,
            "brand_name": sess.get("brand_name"),
            "display_name": sess.get("display_name"),
            "allowed_tabs": sess.get("allowed_tabs") or [],
        },
        "ui": ui_ctx,
        "filter_context": filter_ctx,
    }
    context_json = json.dumps(dashboard_ctx, ensure_ascii=False, default=str)
    if len(context_json) > chatbot.CHAT_MAX_CONTEXT_CHARS:
        context_json = context_json[: chatbot.CHAT_MAX_CONTEXT_CHARS] + "…[truncated]"

    user_blob = (
        f"Dashboard context (JSON):\n{context_json}\n\n"
        f"User request:\n{message}"
    )

    all_ui_commands: list[dict[str, Any]] = []
    pending_action: Optional[dict[str, Any]] = None
    tool_trace: list[dict[str, Any]] = []

    try:
        vertexai.init(project=project, location=location)
        tool = Tool(function_declarations=_tool_declarations())
        model = GenerativeModel(
            model_name,
            system_instruction=[AURA_SYSTEM],
            tools=[tool],
        )
        contents: list[Any] = [Content(role="user", parts=[Part.from_text(user_blob)])]

        final_answer = ""
        for round_i in range(MAX_TOOL_ROUNDS):
            response = model.generate_content(contents)
            calls = _extract_function_calls(response)
            text = _response_text(response)

            if not calls:
                final_answer = text or final_answer
                break

            # Append model turn
            model_content = getattr(getattr(response, "candidates", [None])[0], "content", None)
            if model_content is not None:
                contents.append(model_content)
            else:
                contents.append(response.candidates[0].content)

            fn_response_parts: list[Any] = []
            for fname, fargs in calls:
                result, cmds, pending = execute_tool(
                    fname,
                    fargs,
                    sess=sess,
                    ui_context=ui_ctx,
                    metrics_path=metrics_path,
                )
                all_ui_commands.extend(cmds)
                if pending and not pending_action:
                    pending_action = pending
                tool_trace.append({"tool": fname, "args": fargs, "result": result})
                log.info(
                    "request_id=%s aura tool=%s ok=%s cmds=%s",
                    request_id,
                    fname,
                    result.get("ok", True),
                    len(cmds),
                )
                fn_response_parts.append(
                    Part.from_function_response(
                        name=fname,
                        response={"content": result},
                    )
                )
            contents.append(Content(role="user", parts=fn_response_parts))

            # If confirmation needed, stop early so UI can prompt
            if pending_action:
                final_answer = text or (
                    pending_action.get("args")
                    and "I can run the Ads Lab campaign next — please confirm."
                ) or "Please confirm to continue."
                # One more short generation without tools for a clean ask
                break
        else:
            # exhausted rounds — ask model for final summary without new tools
            try:
                wrap = model.generate_content(
                    contents
                    + [
                        Content(
                            role="user",
                            parts=[
                                Part.from_text(
                                    "Stop calling tools. Summarize what you did and the key findings."
                                )
                            ],
                        )
                    ]
                )
                final_answer = _response_text(wrap) or final_answer
            except Exception:
                pass

        if not final_answer.strip():
            # Fallback: one shot without requiring tool text
            if tool_trace:
                names = ", ".join(t["tool"] for t in tool_trace)
                final_answer = f"Done. Actions: {names}."
            else:
                try:
                    simple = chatbot.call_vertex_chat(
                        message,
                        {
                            "metrics_json": chatbot.slim_metrics_json(metrics_path),
                            "ui": ui_ctx,
                            "filter_context": filter_ctx,
                            "session": {"role": role, "brand_name": sess.get("brand_name")},
                        },
                        request_id,
                        system=AURA_SYSTEM,
                    )
                    final_answer = simple
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": detail, "request_id": request_id},
                    )

    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail, "request_id": request_id},
        )
    except Exception as exc:
        log.exception("request_id=%s aura agent failed", request_id)
        return JSONResponse(
            status_code=503,
            content={"detail": f"AURA agent failed: {exc}", "request_id": request_id},
        )

    # Dedupe ui_commands while preserving order
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for cmd in all_ui_commands:
        key = json.dumps(cmd, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cmd)

    log.info(
        "request_id=%s aura ok tools=%s ui_cmds=%s pending=%s answer_len=%s",
        request_id,
        len(tool_trace),
        len(deduped),
        bool(pending_action),
        len(final_answer),
    )
    return {
        "ok": True,
        "agent": "AURA",
        "answer": final_answer,
        "ui_commands": deduped,
        "pending_action": pending_action,
        "tool_trace": tool_trace[:20],
        "request_id": request_id,
        "model": model_name,
    }
