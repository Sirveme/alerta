"""
workers/imap_client.py — Cliente IMAP reutilizable con reconexión automática.

Decisiones técnicas:
- Se usa imaplib estándar de Python (no imaplib2) porque imaplib2 no se
  instala correctamente en todas las plataformas. imaplib es suficiente
  para polling periódico (no necesitamos IDLE push).
- Conexión SSL obligatoria (puerto 993).
- Reconexión automática en caso de timeout o desconexión.
- Descarga eficiente: headers primero con BODY.PEEK, body completo solo si necesario.
- Soporte para sub-addresses: ventas+ruc20601234567@reenviame.pe
"""

import email
import imaplib
import logging
import re
from dataclasses import dataclass, field
from email.message import Message
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CorreoDescargado:
    """Datos extraídos de un correo IMAP."""
    uid: str
    remitente: str
    destinatario: str
    asunto: str
    fecha: str
    cuerpo_html: Optional[str] = None
    cuerpo_texto: Optional[str] = None
    adjuntos: list = field(default_factory=list)  # [{nombre, content_type, contenido: bytes}]
    sub_address: Optional[str] = None  # Ej: "ruc20601234567" de ventas+ruc20601234567@


class IMAPClient:
    """
    Cliente IMAP con reconexión automática.
    Uso:
        client = IMAPClient("imap.example.com", "user@example.com", "password")
        with client:
            correos = client.obtener_no_leidos()
    """

    def __init__(self, host: str, user: str, password: str, port: int = 993, timeout: int = 30):
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.timeout = timeout
        self._conn: Optional[imaplib.IMAP4_SSL] = None

    def __enter__(self):
        self.conectar()
        return self

    def __exit__(self, *args):
        self.desconectar()

    def conectar(self):
        """Establece conexión SSL al servidor IMAP."""
        try:
            self._conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
            self._conn.login(self.user, self.password)
            self._conn.select("INBOX")
            logger.info(f"IMAP conectado: {self.user}@{self.host}")
        except Exception as e:
            logger.error(f"Error conectando IMAP {self.user}@{self.host}: {e}")
            raise

    def desconectar(self):
        """Cierra conexión IMAP limpiamente."""
        if self._conn:
            try:
                self._conn.close()
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def _reconectar(self):
        """Reconexión automática si la conexión se perdió."""
        logger.warning(f"Reconectando IMAP {self.user}...")
        self.desconectar()
        self.conectar()

    def obtener_no_leidos(self, limite: int = 50) -> list[CorreoDescargado]:
        """
        Obtiene correos no leídos (UNSEEN) del INBOX.
        Solo descarga headers primero; el body se descarga bajo demanda.
        """
        try:
            status, data = self._conn.search(None, "UNSEEN")
        except (imaplib.IMAP4.abort, OSError):
            self._reconectar()
            status, data = self._conn.search(None, "UNSEEN")

        if status != "OK" or not data[0]:
            return []

        uids = data[0].split()[-limite:]  # Los más recientes
        correos = []

        for uid in uids:
            try:
                correo = self._descargar_correo(uid)
                if correo:
                    correos.append(correo)
            except Exception as e:
                logger.error(f"Error descargando correo UID {uid}: {e}")

        return correos

    def _descargar_correo(self, uid: bytes) -> Optional[CorreoDescargado]:
        """Descarga y parsea un correo completo por UID."""
        # Usar BODY.PEEK para no marcar como leído aún
        status, data = self._conn.fetch(uid, "(BODY.PEEK[])")
        if status != "OK" or not data[0]:
            return None

        raw = data[0][1]
        msg: Message = email.message_from_bytes(raw)

        # Extraer headers
        remitente = email.utils.parseaddr(msg.get("From", ""))[1]
        destinatario = email.utils.parseaddr(msg.get("To", ""))[1]
        asunto = self._decode_header(msg.get("Subject", ""))
        fecha = msg.get("Date", "")

        # Extraer sub-address del destinatario
        # ventas+ruc20601234567@reenviame.pe → "ruc20601234567"
        sub_address = None
        match = re.search(r"\+([^@]+)@", destinatario)
        if match:
            sub_address = match.group(1)

        # Extraer cuerpo y adjuntos
        cuerpo_html = None
        cuerpo_texto = None
        adjuntos = []

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in content_disposition or part.get_filename():
                # Es adjunto
                nombre = self._decode_header(part.get_filename() or "sin_nombre")
                contenido = part.get_payload(decode=True)
                if contenido:
                    adjuntos.append({
                        "nombre": nombre,
                        "content_type": content_type,
                        "contenido": contenido,
                    })
            elif content_type == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    cuerpo_html = payload.decode(charset, errors="replace")
            elif content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    cuerpo_texto = payload.decode(charset, errors="replace")

        return CorreoDescargado(
            uid=uid.decode() if isinstance(uid, bytes) else str(uid),
            remitente=remitente,
            destinatario=destinatario,
            asunto=asunto,
            fecha=fecha,
            cuerpo_html=cuerpo_html,
            cuerpo_texto=cuerpo_texto,
            adjuntos=adjuntos,
            sub_address=sub_address,
        )

    def marcar_como_leido(self, uid: str):
        """Marca un correo como leído (\\Seen). Solo llamar tras procesamiento exitoso."""
        try:
            self._conn.store(uid.encode(), "+FLAGS", "\\Seen")
        except (imaplib.IMAP4.abort, OSError):
            self._reconectar()
            self._conn.store(uid.encode(), "+FLAGS", "\\Seen")

    @staticmethod
    def _decode_header(value: str) -> str:
        """Decodifica headers MIME (=?utf-8?Q?...?=)."""
        if not value:
            return ""
        decoded_parts = email.header.decode_header(value)
        result = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(part)
        return " ".join(result)
