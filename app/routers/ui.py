"""Cambio de tema/modo UI."""
from fastapi import APIRouter, Form, Response

router = APIRouter(prefix="/ui", tags=["ui"])

TEMAS_VALIDOS = {"vino-ambar", "oceano-arena", "bosque-cobre"}
MODOS_VALIDOS = {"oscuro", "claro"}


@router.post("/tema")
async def cambiar_tema(tema: str = Form(...), modo: str = Form(...)):
    if tema not in TEMAS_VALIDOS or modo not in MODOS_VALIDOS:
        return {"ok": False, "error": "tema o modo inválido"}
    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie("ui_tema", tema, max_age=365 * 24 * 60 * 60, samesite="lax")
    response.set_cookie("ui_modo", modo, max_age=365 * 24 * 60 * 60, samesite="lax")
    return response
