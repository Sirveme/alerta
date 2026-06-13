"""Servicio de alto nivel para consultar el buzón SUNAT."""
import json
from typing import Any

from rich.console import Console
from rich.table import Table

from app.services.sunat.client import SUNATClient

console = Console()


class BuzonService:
    """Operaciones sobre el buzón SUNAT."""

    def __init__(self, client: SUNATClient):
        self.client = client

    def imprimir_carpetas(self, carpetas: list[dict[str, Any]]) -> None:
        """Imprime las carpetas en una tabla."""
        tabla = Table(title="📁 Carpetas del Buzón SUNAT")
        tabla.add_column("Código", style="cyan")
        tabla.add_column("Nombre", style="white")

        for carpeta in carpetas:
            tabla.add_row(
                str(carpeta.get("codCarpeta", "?")),
                str(carpeta.get("nomCarpeta", "?")),
            )
        console.print(tabla)

    def imprimir_mensajes(
        self,
        data_mensajes: dict[str, Any],
        max_mostrar: int = 5,
    ) -> list[dict[str, Any]]:
        """Imprime los mensajes en una tabla y retorna los IDs para detalle."""
        rows = data_mensajes.get("rows", [])
        total = data_mensajes.get("records", 0)

        tabla = Table(title=f"📬 Mensajes ({total} totales, mostrando {min(len(rows), max_mostrar)})")
        tabla.add_column("Cód.", style="cyan", no_wrap=True)
        tabla.add_column("Fecha", style="yellow", no_wrap=True)
        tabla.add_column("Asunto", style="white")
        tabla.add_column("Adj.", style="green", justify="center")

        for msg in rows[:max_mostrar]:
            tabla.add_row(
                str(msg.get("codMensaje", "?")),
                str(msg.get("fecEnvio", "?")),
                (msg.get("desAsunto") or "")[:80],
                "📎" if msg.get("cantidadArchAdj", 0) > 0 else "",
            )
        console.print(tabla)
        return rows[:max_mostrar]

    def imprimir_detalle(self, detalle: dict[str, Any]) -> None:
        """Imprime el detalle de un mensaje."""
        console.print("\n" + "─" * 80)
        console.print(f"[bold cyan]Detalle del mensaje[/bold cyan]")
        console.print("─" * 80)
        console.print(f"[yellow]Asunto:[/yellow] {detalle.get('desAsunto') or 'N/A'}")
        console.print(f"[yellow]Fecha envío:[/yellow] {detalle.get('fecEnvio') or 'N/A'}")
        console.print(f"[yellow]Adjuntos:[/yellow] {len(detalle.get('listAttach') or [])}")

        # El campo msjMensaje suele ser JSON anidado como string
        msj = detalle.get("msjMensaje", "")
        if msj:
            try:
                msj_parsed = json.loads(msj) if isinstance(msj, str) else msj
                console.print(f"\n[yellow]Contenido (parseado JSON):[/yellow]")
                console.print_json(data=msj_parsed)
            except (json.JSONDecodeError, TypeError):
                preview = msj[:300] if isinstance(msj, str) else str(msj)[:300]
                console.print(f"\n[yellow]Contenido (texto plano):[/yellow]")
                console.print(preview + ("..." if len(str(msj)) > 300 else ""))

        # URL para renderizar HTML completo (si existe)
        url_render = detalle.get("url")
        if url_render:
            console.print(f"\n[yellow]URL renderizado:[/yellow] {url_render[:120]}...")
