#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZAPiXDeskViewerQt — Qt (PySide6) version of ZAPiXDeskViewer.

Why another version?
    The original viewer (tkinter) freezes when switching conversations and
    leaves "ghosting" (broken pixel artifacts) when resizing with large
    chats. Tkinter redraws the whole chat manually (Canvas + thousands of
    widgets + batched wraplength reflow). Qt uses:

    * QListWidget as the conversation list (native widget, smooth scroll);
    * QGraphicsView + QGraphicsScene for the chat (the X11 compositor
      clips/repaints the scene — no ghosting when resizing);
    * QThread for MD5 / extraction / message loading — the UI never
      touches the data; everything comes back through the signal queue
      (thread-safe);
    * QSplitter as the native separator (drag with no hacks).

Same data core: imports Nucleo, ZipIndex, md5_arquivo and
ler_md5_esperado from ZAPiXDeskViewer.py (the file next to this one),
duplicating nothing.

Usage:  python ZAPiXDeskViewerQt.py

Author:    Alberto Magno (kraftdenker) <alberto.magno@gmail.com>
Co-author: DeepSeek Harness (AI assistant)
"""

import base64
import datetime
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse

# --- imports the core from the tkinter viewer (same folder) -----------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import (QBuffer, QIODevice, QSize, QRectF, QThread,
                            QTimer, QUrl, Signal, Qt)
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont,
                           QFontMetrics, QIcon, QPainter, QPixmap, QImage)
from PySide6.QtWidgets import QStyle
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QFrame, QGraphicsView,
    QGraphicsScene, QGraphicsProxyWidget, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStatusBar, QStyledItemDelegate, QTabWidget, QVBoxLayout,
    QWidget)

from ZAPiXDeskViewer import (APP_TITLE, HAS_PIL, Nucleo,
                             _caminho_recurso, hora, log, log_excecao,
                             ler_md5_esperado, md5_arquivo,
                             tamanho_amigavel)

if HAS_PIL:
    from PIL import Image
    import PIL.ImageTk  # noqa: F401  (only to ensure availability)


def _recurso(nome):
    """Resolve a bundled resource (splash, logo, icon).

    The core resolves resources against ITS OWN directory (BASE_DIR), but the
    branding assets live next to THIS Qt script. So look here first, then fall
    back to the core's location (covers the frozen/PyInstaller layout where
    both scripts share one folder)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), nome)
    if os.path.isfile(p):
        return p
    return _caminho_recurso(nome)


class DebugProxy(QGraphicsProxyWidget):
    """QGraphicsProxyWidget that logs mouse presses (for debugging)."""
    def mousePressEvent(self, event):
        try:
            pos = event.scenePos()
            print(f"[DebugProxy] mousePressEvent: scenePos={pos}, "
                  f"widget={self.widget()}")
        except Exception:
            print(f"[DebugProxy] mousePressEvent: widget={self.widget()}")
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Style constants (WhatsApp palette, same as the original viewer)
# ---------------------------------------------------------------------------

CHAT_BG_COLOR = "#efeae2"
MY_COLOR = "#d9fdd3"
OTHER_COLOR = "#ffffff"
LIST_COLOR = "#ffffff"
LIST_COLOR_SELECTED = "#d9fdd3"
TEXT_COLOR = "#111111"
SOFT_COLOR = "#667781"
SENDER_COLOR = "#5268f5"
SENDER_COLOR_MINE = "#0b843b"
URL_COLOR = "#1a6fb5"
HEADER_BG_COLOR = "#008069"
DATE_BADGE_COLOR = "#ffffff"
HIGHLIGHT_COLOR = "FFF3BF"     # search-result bubble background (yellow)
HIGHLIGHT_BORDER_COLOR = "#E8A33D"

# Global QSS — keeps the app look on any native widget.
ESTILO = f"""
QMainWindow, QWidget {{ background: {LIST_COLOR}; color: {TEXT_COLOR};
                        font-family: 'DejaVu Sans'; }}
QListWidget {{ background: {LIST_COLOR}; border: none;
               outline: none; font-size: 12px; }}
QListWidget::item {{ padding: 7px 10px; border: none; }}
QListWidget::item:selected {{ background: {LIST_COLOR_SELECTED};
                              color: {TEXT_COLOR}; }}
QListWidget::item:hover {{ background: #f2f2f2; }}
QLineEdit {{ background: #f0f2f5; border: none; border-bottom: 1px solid #d5d5d5;
             padding: 6px 10px; font-size: 12px; }}
QPushButton {{ background: {HEADER_BG_COLOR}; color: white; border: none;
               border-radius: 5px; padding: 6px 14px; font-size: 11px; }}
QPushButton:hover {{ background: #128c7e; }}
QPushButton:pressed {{ background: #0b6e63; }}
QPushButton#secundario {{ background: #888888; }}
QSplitter::handle {{ background: #d5d5d5; }}
QProgressBar {{ border: none; background: #d5d5d5; border-radius: 3px;
                text-align: center; font-size: 9px; color: white; }}
QProgressBar::chunk {{ background: {HEADER_BG_COLOR}; border-radius: 3px; }}
QStatusBar {{ background: #f0f2f5; font-size: 10px; color: {SOFT_COLOR}; }}
QTabBar::tab {{ background: white; padding: 5px 14px; margin-right: 2px;
                border-top-left-radius: 5px; border-top-right-radius: 5px;
                font-size: 11px; }}
QTabBar::tab:selected {{ background: {HEADER_BG_COLOR}; color: white; }}
QTextEdit, QPlainTextEdit {{ border: none; background: {CHAT_BG_COLOR};
                             font-size: 12px; }}
QLabel#titulo {{ font-size: 14px; font-weight: bold; }}
QLabel#iconBubble {{ font-size: 18px; }}
QGraphicsView {{ border: none; background: {CHAT_BG_COLOR}; }}
"""

# CSS embedded in the QGraphicsView "bubbles" (rich text of the QLabel item).
CSS_BALAO = """
body {{ margin:0; padding:8px 12px; border-radius:8px;
        font-size:13px; color:{cor_texto}; background:{cor_fundo}; }}
.sender {{ font-weight:bold; font-size:12px; color:{cor_rem}; }}
.footer {{ text-align:right; font-size:10px; color:#667781;
           margin-top:2px; white-space:nowrap; }}
.media {{ color:#667781; font-size:12px; margin-top:3px; }}
.link {{ color:#1a6fb5; word-break:break-all; }}
.caption {{ font-size:12px; margin-top:3px; color:#333; }}
.vcard {{ font-size:11px; color:#667781; }}
img {{ border-radius:6px; margin-top:3px; }}
a {{ color:#0b843b; text-decoration:none; font-size:12px; }}
a:hover {{ text-decoration:underline; }}
"""


def _short_date(data_ms):
    """The 'dd/mm/yyyy' part of the timestamp (for the bubble footer).

    Uses the same unit detection as the core (s/ms/us/ns) — ZAPiXDESK
    exports vary between seconds and milliseconds."""
    from ZAPiXDeskViewer import _ts_para_segundos
    seg = _ts_para_segundos(data_ms)
    if seg is None:
        return ""
    try:
        return datetime.datetime.fromtimestamp(
            seg).strftime("%d/%m/%Y")
    except Exception:
        return ""


def separator_label(day):
    """Formats the day separator: 'Today', 'Yesterday' or full date."""
    try:
        d = datetime.datetime.strptime(
            day[:10], "%d/%m/%Y").date()
    except Exception:
        return day
    hoje = datetime.date.today()
    if d == hoje:
        return "Today · " + day
    if d == hoje - datetime.timedelta(days=1):
        return "Yesterday · " + day
    return day


def _esc(t):
    """Escapes text for HTML (Qt bubbles)."""
    s = str(t or "")
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;"))


def _resumo_tipo(t):
    """[thin fallback] same map as Nucleo._resumo_tipo."""
    m = {1: "[Image]", 2: "[Audio]", 3: "[Video]", 4: "[Contact]",
         8: "[Document]", 9: "[Link]", 10: "[Sticker]"}
    return m.get(t, "[Message]") if t else "[Message]"


def _duration(s):
    try:
        s = int(s)
    except Exception:
        return ""
    m, ss = divmod(s, 60)
    return f"{m}:{ss:02d}"


# ---------------------------------------------------------------------------
# Worker threads (Qt QThread — the UI never touches the data)
# ---------------------------------------------------------------------------

class WorkerOpen(QThread):
    """MD5 + database extraction (phase 1 of opening)."""
    md5_progress = Signal(int, int)          # (read, total) bytes
    status = Signal(str)
    ready = Signal(object, str)              # (Nucleo, zip_path)
    error = Signal(str)

    def __init__(self, zip_path, txt_path, parent=None):
        super().__init__(parent)
        self.zip_path = zip_path
        self.txt_path = txt_path

    def run(self):
        try:
            esperado = ler_md5_esperado(self.txt_path)
            if not esperado:
                raise ValueError("MD5 not found in the .txt file.")
            self.status.emit("Verifying MD5…")
            t0 = time.time()
            real = md5_arquivo(
                self.zip_path,
                progresso=lambda l, t: self.md5_progress.emit(l, t),
                parar=lambda: False)
            if real is None:
                return
            if real != esperado:
                self.status.emit("⚠ MD5 mismatch")
                self.error.emit(
                    "The file's MD5 does not match.\n\n"
                    f"Expected: {esperado}\nFound: {real}\n\n"
                    "The file may be corrupted or incomplete.")
                return
            self.status.emit(
                f"MD5 OK in {time.time() - t0:.1f}s — extracting databases…")
            nucleo = Nucleo(self.zip_path)
            self.ready.emit(nucleo, self.zip_path)
        except Exception as exc:
            log_excecao("WorkerOpen", exc, traceback.format_exc())
            self.error.emit(str(exc))


class WorkerAttachments(QThread):
    """Attachment extraction to the persistent cache (phase 2, in the background)."""
    progress = Signal(int, int)
    ready = Signal(str)

    def __init__(self, nucleo, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo

    def run(self):
        try:
            destination = self.nucleo._extraer_anexos(
                progresso=lambda o, t: self.progress.emit(o, t),
                parar=lambda: False)
            self.ready.emit(destination)
        except Exception as exc:
            log_excecao("WorkerAttachments", exc, traceback.format_exc())


# ---------------------------------------------------------------------------
# Group-sender resolution (export-specific)
# ---------------------------------------------------------------------------
# In some exports the per-message PushName column is EMPTY, so the bubble
# cannot show who sent it. The Messages.RemoteResource column, however,
# holds the SENDER's real jid for group messages. These helpers batch-resolve
# those jids to display names (UserStatuses -> conversation name -> jid
# number) and patch the tuples so the renderer can show the sender.

def _group_sender_map(nucleo, jid, msg_ids):
    """Map {MessageID: display_name} for the given messages of a GROUP.

    Reads RemoteResource (the sender's real jid, present even when PushName
    is empty) in one query and resolves each distinct jid to a name in one
    query. Returns {} for a non-group, no nucleo, or any DB problem."""
    if not nucleo or not msg_ids:
        return {}
    if not str(jid).lower().endswith("@g.us"):
        return {}
    try:
        marks = ",".join("?" * len(msg_ids))
        rows = nucleo.msg_db.execute(
            f"SELECT MessageID, RemoteResource FROM Messages "
            f"WHERE MessageID IN ({marks}) "
            f"AND RemoteResource IS NOT NULL AND trim(RemoteResource) != ''",
            list(msg_ids)).fetchall()
    except Exception:
        return {}
    if not rows:
        return {}
    jids = sorted({str(rr) for (_m, rr) in rows if rr})
    # resolve each distinct jid to a display name in ONE query
    names = {}
    try:
        marks = ",".join("?" * len(jids))
        for (j, cn, pn, df, un) in nucleo.con_db.execute(
                f"SELECT Jid, ContactName, PushName, "
                f"DisplayNameFromServer, UserName FROM UserStatuses "
                f"WHERE Jid IN ({marks})", jids):
            names[str(j)] = str(cn or pn or df or un or "").strip()
    except Exception:
        pass
    # fallbacks: conversation name (the nucleo.nomes map), then the jid number
    for j in jids:
        if not names.get(j):
            names[j] = nucleo.nomes.get(str(j).lower(), "") or \
                str(j).split("@")[0]
    out = {}
    for (m, rr) in rows:
        out[m] = names.get(str(rr), "")
    return out


def _patch_group_senders(nucleo, jid, msgs):
    """Returns `msgs` with the PushName field (index 6) filled, for a GROUP,
    when the export leaves it empty (using RemoteResource -> name). Other
    conversations and already-named messages are returned unchanged."""
    if not nucleo or not msgs or not str(jid).lower().endswith("@g.us"):
        return msgs
    if all(m[6] for m in msgs):
        return msgs  # already have sender names
    mids = [m[0] for m in msgs]
    mapa = _group_sender_map(nucleo, jid, mids)
    if not mapa:
        return msgs
    patched = []
    for m in msgs:
        m = list(m)
        if not m[6] and m[0] in mapa:
            m[6] = mapa[m[0]]
        patched.append(tuple(m))
    return patched


class WorkerConversation(QThread):
    """Recent-first loader: counts the total and loads the `limit` MOST
    RECENT messages (one query, `mensagens(jid, limit, 0)`). The older
    messages are loaded afterwards by WorkerOlder.

    `limit=0` (or None) keeps the legacy behavior: load everything,
    paginated (still used by the "load all" paths / tests)."""
    ready = Signal(dict)                     # dict of results
    progress = Signal(int, int)              # (read, total) messages
    error = Signal(str)

    def __init__(self, nucleo, jid, limit=0, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.jid = jid
        self.limit = limit or 0
        self._stop_flag = False

    def stop(self):
        """Asked by the UI when the user switches conversation."""
        self._stop_flag = True

    def run(self):
        try:
            t0 = time.time()
            total = self.nucleo.total_mensagens(self.jid)
            if self.limit and self.limit > 0:
                # recent-first: only the newest `limit` rows (one query).
                # mensagens(jid, n, 0) returns exactly the most recent n,
                # already in chronological (oldest-first) order.
                msgs = self.nucleo.mensagens(self.jid, self.limit, 0)
                if self._stop_flag:
                    return
                self.progress.emit(len(msgs), total)
                fim = len(msgs) >= total
            else:
                # legacy: load everything, paginated with a cursor (avoids
                # the SQLite 3.45 bug with BLOBs in large queries)
                msgs = []
                offset = 0
                while offset < total and not self._stop_flag:
                    batch = self.nucleo.mensagens(self.jid, 2000, offset)
                    if not batch:
                        break
                    msgs = batch + msgs  # the older page goes to the beginning
                    offset += len(batch)
                    self.progress.emit(offset, total)
                    if offset > 200000:  # safety cap
                        break
                if self._stop_flag:
                    return  # the user already chose another conversation
                # defensive deduplication (the composite cursor can repeat
                # the last row if (TS, MID) is not unique in the database)
                readers = set()
                unicas = []
                for m in msgs:
                    if m[0] not in readers:
                        readers.add(m[0])
                        unicas.append(m)
                msgs = unicas
                fim = offset >= total
            log("Qt: %d msgs (de %d) em %.2fs",
                len(msgs), total, time.time() - t0)
            # fill the group sender name (PushName is empty in some exports)
            msgs = _patch_group_senders(self.nucleo, self.jid, msgs)
            self.ready.emit({
                "jid": self.jid, "total": total, "msgs": msgs,
                "fim": fim,
            })
        except Exception as exc:
            log_excecao("WorkerConversation", exc, traceback.format_exc())
            self.error.emit(str(exc))


class WorkerOlder(QThread):
    """Loads the OLDER messages (the ones already loaded are the most
    recent `_PRIMEIRAS` rows). Pages backwards with a TRUE cursor
    ((TimestampLong, MessageID) of the last row seen) and emits each page
    ALREADY in chronological (oldest-first) order, so the UI can insert it
    at the top of the chat. The first emitted page is the one right after
    the already-loaded recent block.

    Cursor pagination (mensagens_apos_cursor) loses no row even when a
    (TS, MID) pair is duplicated — unlike the OFFSET path.

    Signals:
      batch(list)  -> one older page, chronological (oldest first)
      fim()       -> all remaining older messages have been loaded
      error(str)
    """
    batch = Signal(list)
    fim = Signal()
    error = Signal(str)

    PAGO = 400  # rows per page (same as the core's BLOB-safe threshold)

    def __init__(self, nucleo, jid, total, start_cursor, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.jid = jid
        self.total = total
        # (TimestampLong, MessageID) of the OLDEST already-loaded row; the
        # first page is everything strictly older than this
        self.start_cursor = start_cursor
        self._stop_flag = False
        self._seen = set()      # defensive dedup across pages

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            t0 = time.time()
            read_count = 0
            cur = self.start_cursor
            while cur is not None and not self._stop_flag:
                batch = self.nucleo.mensagens_apos_cursor(
                    self.jid, self.PAGO, cur[0], cur[1])
                if not batch:
                    break
                # defensive dedup: drop rows already seen
                novas = [m for m in batch if m[0] not in self._seen]
                for m in novas:
                    self._seen.add(m[0])
                if novas:
                    # fill the group sender name (PushName is empty in some
                    # exports) before the UI renders the page
                    self.batch.emit(_patch_group_senders(
                        self.nucleo, self.jid, novas))  # chronological
                    # advance the cursor to the OLDEST row of this page
                    # (batch[0] is the oldest, since the page is chronological)
                    cur = (batch[0][5], batch[0][0])
                else:
                    # no new unique rows: advance past this page's oldest
                    # raw row to avoid an infinite loop on a fully
                    # duplicated page (should not happen in practice)
                    cur = (batch[0][5], batch[0][0])
                read_count += len(novas)
                if read_count > 200000:  # safety cap
                    break
            if not self._stop_flag:
                log("Qt: %d msgs older em %.2fs", read_count, time.time() - t0)
                self.fim.emit()
        except Exception as exc:
            log_excecao("WorkerOlder", exc, traceback.format_exc())
            self.error.emit(str(exc))


class WorkerSearchGlobal(QThread):
    """Searches ALL conversations for a term (message text + media
    captions), newest first, paged (busca_todas). Emits each page
    (batch) and fim() when no older matches remain.

    Signals:
      total(int)  — total matching messages (first call, may be refined)
      batch(list)  — one page: (MessageID, KeyRemoteJid, Data, PushName,
                    TimestampLong, MediaWaType, MediaCaption), newest first
      fim()       — no older matches remain
      error(str)
    """
    total = Signal(int)
    batch = Signal(list)
    fim = Signal()
    error = Signal(str)

    PAGO = 300

    def __init__(self, nucleo, term, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.term = term
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            t0 = time.time()
            cur = None
            total = None
            while not self._stop_flag:
                res = self.nucleo.busca_todas(self.term, self.PAGO, cur)
                if total is None:
                    total = res["total"]
                    self.total.emit(total)
                msgs = res["msgs"]
                if not msgs:
                    break
                self.batch.emit(msgs)
                if res["fim"]:
                    break
                cur = (msgs[-1][4], msgs[-1][0])  # (TimestampLong, MessageID)
            if not self._stop_flag:
                log("Qt: search '%s' -> %d msgs em %.2fs",
                    self.term, total or 0, time.time() - t0)
                self.fim.emit()
        except Exception as exc:
            log_excecao("WorkerSearchGlobal", exc, traceback.format_exc())
            self.error.emit(str(exc))


class WorkerSearchConversation(QThread):
    """Searches ONE conversation's full history (busca_conversa).
    Emits a single ready(list) with the matches in chronological order.

    Signals:
      ready(list) — (MessageID, KeyRemoteJid, Data, PushName,
                     TimestampLong, MediaWaType, MediaCaption), chronological
      error(str)
    """
    ready = Signal(list)
    error = Signal(str)

    def __init__(self, nucleo, jid, term, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.jid = jid
        self.term = term
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            t0 = time.time()
            res = self.nucleo.busca_conversa(self.jid, self.term)
            if not self._stop_flag:
                log("Qt: search '%s' in %s -> %d msgs em %.2fs",
                    self.term, self.jid, len(res), time.time() - t0)
                self.ready.emit(res)
        except Exception as exc:
            log_excecao("WorkerSearchConversation", exc, traceback.format_exc())
            self.error.emit(str(exc))


class WorkerExport(QThread):
    """Exports the conversation to HTML (all messages, paginated)."""
    status = Signal(str)
    ready = Signal(str)
    error = Signal(str)

    def __init__(self, nucleo, conversation, formato, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.conversation = conversation
        self.formato = formato

    def _export_name(self, c):
        import re
        base = re.sub(r"[^A-Za-z0-9À-ÿ_\- ]+", "", c["nome"]).strip()
        base = re.sub(r"\s+", "_", base)
        return base or "conversation"

    def _lines_html(self, c, msgs):
        import html as _html
        parts = []
        last_day = None
        for msg in msgs:
            (mid, from_me, text, _bin, file_name, ts, sender_name, mime, type,
             size, duration, media_name, caption, lfu, _react, reactions) = msg
            day = hora(ts)
            if day:
                key = day[:10]
                if key != last_day:
                    last_day = key
                    parts.append(
                        f'<div class="day"><span>{_html.escape(day)}</span></div>')
            cls = "eu" if from_me else "outro"
            sender = ""
            if c["grupo"] and not from_me and sender_name:
                sender = f'<div class="sender">{_html.escape(sender_name)}</div>'
            # content (text / media / document / link / vcard / sticker)
            if type == 0 or type in (29, 1027):
                content = (_html.escape(text).replace("\n", "<br>")
                         if text else "")
            elif type == 1:
                content = (f'<div class="media">🖼 {_html.escape(media_name or "image")}'
                         f' · {_html.escape(tamanho_amigavel(size))}</div>')
            elif type in (2, 3):
                icon = "🎵" if type == 2 else "🎬"
                dur = f' · {_duration(duration)}' if duration else ""
                content = (f'<div class="media">{icon} '
                         f'{_html.escape(media_name or ("audio" if type == 2 else "video"))}'
                         f'{_html.escape(dur)} · '
                         f'{_html.escape(tamanho_amigavel(size))}</div>')
            elif type == 4:
                import re
                name = media_name or "Contact"
                content = f'<div class="media">👤 {_html.escape(name)}</div>'
                if text and "BEGIN:VCARD" in text:
                    fn = re.search(r"FN:(.*)", text)
                    tel = re.search(r"TEL[^:]*:(.*)", text)
                    if fn:
                        content += f'<div class="vcard">{_html.escape(fn.group(1).strip())}</div>'
                    if tel:
                        content += f'<div class="vcard">{_html.escape(tel.group(1).strip())}</div>'
            elif type in (8, 1006):
                name = media_name or file_name or "document"
                content = (f'<div class="media">📄 {_html.escape(name)} · '
                         f'{_html.escape(tamanho_amigavel(size))}</div>')
            elif type == 9:
                content = f'<div class="link">🔗 {_html.escape((text or "").strip())}</div>'
            elif type == 10:
                content = '<div class="media">✨ [sticker]</div>'
            else:
                content = (_html.escape(text).replace("\n", "<br>") if text
                         else _html.escape(_resumo_tipo(type)))
            if caption and type != 9:
                content += f'<div class="caption">{_html.escape(caption)}</div>'
            time_text = self._short_time(ts)
            reag_txt = ""
            if reactions:
                readers = {}
                order = []
                for jid_r, texto_r in reactions:
                    k = texto_r or "✅"
                    if k not in readers:
                        readers[k] = 0
                        order.append(k)
                    readers[k] += 1
                reag_txt = "  ".join(
                    f"{k} {readers[k]}" if readers[k] > 1 else k for k in order)
            footer = (f'<div class="footer">{_html.escape(reag_txt)} '
                      f'<span class="hora">{_html.escape(time_text)}'
                      f'{"  ✓✓" if from_me else ""}</span></div>')
            parts.append(
                f'<div class="row {cls}">'
                f'<div class="bubble">{sender}{content}{footer}</div></div>')
        return "".join(parts)

    @staticmethod
    def _short_time(data_ms):
        """HH:MM with unit detection (s/ms/us/ns), same as the core."""
        from ZAPiXDeskViewer import _ts_para_segundos
        seg = _ts_para_segundos(data_ms)
        if seg is None:
            return ""
        try:
            return datetime.datetime.fromtimestamp(seg).strftime("%H:%M")
        except Exception:
            return ""

    def _build_html(self, c, msgs):
        import html as _html
        dest_dir = os.path.join(tempfile.gettempdir(), "zapixdesk_export")
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"{self._export_name(c)}.html")
        total = self.nucleo.total_mensagens(c["jid"])
        content = self._lines_html(c, msgs)
        label = "👥 Group" if c["grupo"] else "💬 Direct message"
        css = """
        body { background:#efeae2; font-family:'DejaVu Sans',Arial,sans-serif;
               margin:0; padding:16px; }
        .header { background:#008069; color:#fff; padding:12px 16px;
                     border-radius:8px; margin-bottom:14px; }
        .header h1 { margin:0; font-size:18px; }
        .header small { color:#d6f5ef; }
        .row { display:flex; margin:2px 0; }
        .row.eu { justify-content:flex-end; }
        .row.outro { justify-content:flex-start; }
        .bubble { max-width:62%; background:#fff; padding:8px 12px;
                 border-radius:8px; font-size:14px; color:#111;
                 box-shadow:0 1px 1px rgba(0,0,0,.12); }
        .row.eu .bubble { background:#d9fdd3; }
        .sender { font-weight:bold; font-size:13px; color:#5268f5; }
        .footer { text-align:right; font-size:11px; color:#667781;
                  margin-top:2px; }
        .hora { white-space:nowrap; }
        .media { color:#667781; font-size:13px; }
        .link { color:#1a6fb5; font-size:14px; word-break:break-all; }
        .caption { font-size:13px; margin-top:3px; color:#333; }
        .vcard { font-size:12px; color:#667781; }
        .day { text-align:center; margin:12px 0 6px; }
        .day span { background:#fff; color:#667781; font-size:12px;
                    padding:3px 12px; border-radius:10px;
                    box-shadow:0 1px 1px rgba(0,0,0,.1); }
        """
        document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_html.escape(c['nome'])} — ZAPiXDesk</title>
<style>{css}</style>
</head>
<body>
<div class="header">
  <h1>{_html.escape(c['nome'])}</h1>
  <small>{_html.escape(label)} · {total} messages · exported by ZAPiXDeskViewerQt</small>
</div>
{content}
</body>
</html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(document)
        return path

    def _html_to_pdf(self, html_path, base):
        destination = os.path.splitext(html_path)[0] + ".pdf"
        candidates = []
        if os.name == "nt":
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
        else:
            candidates = ["google-chrome", "google-chrome-stable",
                          "chromium", "chromium-browser", "chrome", "msedge"]
        import shutil
        exe = None
        for cand in candidates:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                exe = cand
                break
            elif shutil.which(cand):
                exe = shutil.which(cand)
                break
        if not exe:
            raise RuntimeError(
                "No Chrome/Chromium/Edge was found to generate the PDF.\n"
                "Use the HTML format or install a headless browser.")
        cmd = [exe, "--headless", "--disable-gpu", "--no-sandbox",
               f"--print-to-pdf={destination}", "--no-pdf-header-footer",
               html_path]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if not os.path.isfile(destination) or os.path.getsize(destination) == 0:
            raise RuntimeError(
                "Failed to generate the PDF.\n"
                + proc.stderr.decode("utf-8", "replace")[:500])
        return destination

    def run(self):
        c = self.conversation
        try:
            self.status.emit(f"Exporting “{c['nome']}” (loading everything)…")
            msgs = []
            offset = 0
            total = self.nucleo.total_mensagens(c["jid"])
            while True:
                batch = self.nucleo.mensagens(c["jid"], 2000, offset)
                if not batch:
                    break
                msgs = batch + msgs
                offset += len(batch)
                if offset >= total or offset > 200000:
                    break
            if self.formato == "pdf":
                html_path = self._build_html(c, msgs)
                path = self._html_to_pdf(html_path, self._export_name(c))
            else:
                path = self._build_html(c, msgs)
            self.ready.emit(path)
        except Exception as exc:
            log_excecao("WorkerExport", exc, traceback.format_exc())
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Attachment worker (loads a file's bytes in the background)
# ---------------------------------------------------------------------------

class WorkerAttachment(QThread):
    ready = Signal(bytes)
    def __init__(self, nucleo, uri, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.uri = uri

    def run(self):
        data = self.nucleo.zip.bytes(self.uri) if self.nucleo else None
        self.ready.emit(data or b"")


class WorkerAttachmentRef(QThread):
    """Recovers the LocalFileUri matching a <hash16>|<name> ref from the
    current conversation (the lookup may touch SQLite)."""
    ready = Signal(str)   # lfu (or "" if not found)

    def __init__(self, window, ref, parent=None):
        super().__init__(parent)
        self.window = window
        self.ref = ref

    def run(self):
        lfu = self.window._lfu_ref(self.ref)
        self.ready.emit(lfu or "")


class WorkerBytes(QThread):
    """Loads an attachment's bytes from the zip (background) + BLOB fallback."""
    ready = Signal(bytes)
    error = Signal(str)

    def __init__(self, nucleo, uri, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.uri = uri

    def run(self):
        try:
            data = self.nucleo.zip.bytes(self.uri) if self.nucleo else None
            self.ready.emit(data or b"")
        except Exception as exc:
            log_excecao("WorkerBytes", exc, traceback.format_exc())
            self.error.emit(str(exc))


class WorkerDocument(QThread):
    """Resolves a document's path (extracted cache, or a one-off extraction
    from the zip) WITHOUT reading the bytes into memory — used to open PDFs
    and the like directly with the default app."""
    ready = Signal(str)                      # path ("" if not found)
    error = Signal(str)

    def __init__(self, nucleo, uri, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.uri = uri

    def run(self):
        try:
            path = self.nucleo.caminho_anexo(self.uri) \
                if self.nucleo else None
            self.ready.emit(path or "")
        except Exception as exc:
            log_excecao("WorkerDocument", exc, traceback.format_exc())
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Avatars (list / chat header / details window)
# ---------------------------------------------------------------------------
# The export has no profile photos, so avatars are "colored initials" —
# a circle with the first 1-2 letters of the name in a name-derived color
# (same idea as WhatsApp). If a future export ships ChatPictures bytes,
# _avatar_pixmap uses the real photo instead.

def _avatar_pixmap(nucleo, jid, name, size=40):
    """QPixmap avatar (square, full-bleed) for a jid/name, at `size` px."""
    # 1) real photo from the export, when present
    data = nucleo.avatar_dados(jid, name) if nucleo else None
    if data:
        pm = QPixmap()
        if pm.loadFromData(data):
            # center-crop to a square, then scale
            lado = min(pm.width(), pm.height())
            x = (pm.width() - lado) // 2
            y = (pm.height() - lado) // 2
            pm = pm.copy(x, y, lado, lado).scaled(
                size, size, Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation)
            # re-crop any residual to exactly size×size
            if pm.width() != size or pm.height() != size:
                lado2 = min(pm.width(), pm.height())
                x2 = (pm.width() - lado2) // 2
                y2 = (pm.height() - lado2) // 2
                pm = pm.copy(x2, y2, lado2, lado2)
            return pm
    # 2) colored initial
    cor = Nucleo._cor_avatar(jid or name)
    ini = Nucleo._iniciais(name, 2)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(cor)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    # the initial, centered, white, sized to ~46% of the box
    fonte = QFont("DejaVu Sans", int(size * 0.34), QFont.Bold)
    p.setFont(fonte)
    p.setPen(QColor("white"))
    p.drawText(pm.rect(), Qt.AlignCenter, ini)
    p.end()
    return pm


def _avatar_b64(nucleo, jid, name, size=40):
    """<img> data-URI avatar for rich-text contexts (details info panel)."""
    pm = _avatar_pixmap(nucleo, jid, name, size)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    if not pm.save(buf, "PNG"):
        return ""
    b64 = base64.b64encode(bytes(buf.data().data())).decode("ascii")
    buf.close()
    return (f'<img src="data:image/png;base64,{b64}" '
            f'width="{size}" height="{size}" style="border-radius:{size}px;'
            f'vertical-align:middle;">')


class WorkerDetails(QThread):
    """Loads the details-window data in the background (SQLite):
    the conversation info (dates, groups) + the categorized attachments
    (gallery). Keeps the UI responsive for large conversations."""
    ready = Signal(dict)          # {"info":..., "anexos":...}
    progress = Signal(int)        # scanned messages so far
    error = Signal(str)

    def __init__(self, nucleo, jid, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.jid = jid
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            info = self.nucleo.info_conversa(self.jid) \
                if self.nucleo else None
            if self._stop_flag:
                return
            anexos = self.nucleo.anexos_conversa(
                self.jid,
                progress=lambda n: self.progress.emit(n),
                parar=lambda: self._stop_flag) if self.nucleo else {}
            if self._stop_flag:
                return
            self.ready.emit({"info": info, "anexos": anexos})
        except Exception as exc:
            log_excecao("WorkerDetails", exc, traceback.format_exc())
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Original image viewer (QLabel in its own window, no PIL)
# ---------------------------------------------------------------------------

class ImageView(QMainWindow):
    """Shows the original image at full size (with scroll zoom)."""

    def __init__(self, pm, titulo, parent=None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.resize(min(pm.width() + 40, 1400), min(pm.height() + 80, 1000))
        central = QWidget()
        self.setCentralWidget(central)
        vb = QVBoxLayout(central)
        vb.setContentsMargins(0, 0, 0, 0)
        self.lbl = QLabel(central)
        self.lbl.setPixmap(pm)
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setMinimumSize(200, 200)
        vb.addWidget(self.lbl)


# ---------------------------------------------------------------------------
# Gallery grid (details window): thumbnail + caption, one widget per item
# ---------------------------------------------------------------------------

# The 6 gallery tabs, in order (WhatsApp-like)
ABAS_GALERIA = [
    ("imagem",    "🖼 Images",    "#0b843b"),
    ("video",     "🎬 Videos",    "#0b843b"),
    ("audio",     "🎵 Audios",    "#0b843b"),
    ("figurinha", "✨ Stickers", "#0b843b"),
    ("link",      "🔗 Links",     "#0b843b"),
    ("documento", "📄 Documents", "#0b843b"),
]


def _short_date_ts(ts):
    """'dd/mm/yyyy' for a raw TimestampLong ("" if unparseable)."""
    from ZAPiXDeskViewer import _ts_para_segundos
    seg = _ts_para_segundos(ts)
    if seg is None:
        return ""
    try:
        return datetime.datetime.fromtimestamp(seg).strftime("%d/%m/%Y")
    except Exception:
        return ""


class GalleryItem(QFrame):
    """One gallery item: thumbnail (or icon) + name + date. Clickable —
    the parent details window decides what the click does (image viewer,
    open with default app, open the link…)."""
    clicked = Signal(object)   # the item dict

    BOX = 132  # thumbnail box (px)

    def __init__(self, category, item, window, parent=None):
        super().__init__(parent)
        self.category = category
        self.item = item
        self.window = window
        self.setFixedSize(self.BOX + 12, self.BOX + 44)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QFrame { background:#f7f7f7; border:1px solid #e3e3e3;"
            " border-radius:6px; }")
        vb = QVBoxLayout(self)
        vb.setContentsMargins(6, 6, 6, 4)
        vb.setSpacing(3)
        self._build_top()
        # caption: name + date
        name = (item.get("nome") or "").strip()
        if not name and category == "link":
            name = (item.get("texto") or "link").strip()
        data = _short_date_ts(item.get("ts"))
        cap = name if name else "(no name)"
        if data:
            cap = f"{cap}<br><span style='color:#667781;font-size:10px;'>" \
                  f"{data}</span>"
        self.lbl_cap = QLabel(cap)
        self.lbl_cap.setTextFormat(Qt.RichText)
        self.lbl_cap.setWordWrap(True)
        self.lbl_cap.setStyleSheet(
            "font-size:11px; color:#111; background:transparent;")
        self.lbl_cap.setMaximumHeight(30)
        vb.addWidget(self.lbl_cap)
        vb.addStretch(0)

    # -- the thumbnail (or a big icon when there is no picture) ------------
    def _build_top(self):
        cat = self.category
        item = self.item
        if cat == "link":
            # no media: a clickable link pill
            txt = (item.get("texto") or "open link").strip()
            self.top_label = QLabel("🔗")
            self.top_label.setAlignment(Qt.AlignCenter)
            self.top_label.setStyleSheet(
                "font-size:44px; background:transparent;")
            self.top_label.setFixedSize(self.BOX, self.BOX)
        elif cat in ("imagem", "sticker"):
            pm = self._image_pm(item)
            if pm is None:
                self._top_icon("🖼" if cat == "imagem" else "✨")
            else:
                self._top_pm(pm, cat)
        elif cat == "video":
            pm = self._image_pm(item)
            if pm is None:
                self._top_icon("🎬")
            else:
                self._top_pm(pm, cat)
        elif cat == "audio":
            self._top_icon("🎵")
        else:  # document
            self._top_icon("📄")
        self.layout().addWidget(self.top_label, 0, Qt.AlignHCenter)

    def _image_pm(self, item):
        """QPixmap for a media item (extracted file, else the inline blob)."""
        try:
            lfu = item.get("lfu")
            if lfu and self.window.nucleo and \
                    self.window.nucleo.zip.caminho_extraido(lfu):
                pm = QPixmap(self.window.nucleo.zip.caminho_extraido(lfu))
                if not pm.isNull():
                    return pm
            _bin = item.get("bin")
            if _bin:
                pm = QPixmap()
                pm.loadFromData(bytes(_bin))
                if not pm.isNull():
                    return pm
        except Exception:
            pass
        return None

    def _top_pm(self, pm, cat):
        # fit inside the square, keep aspect, centered on a grey backdrop
        m = pm.scaled(self.BOX, self.BOX, Qt.KeepAspectRatio,
                      Qt.SmoothTransformation)
        self.top_label = QLabel()
        self.top_label.setAlignment(Qt.AlignCenter)
        self.top_label.setFixedSize(self.BOX, self.BOX)
        self.top_label.setStyleSheet(
            "background:#e9e9e9; border-radius:4px;")
        self.top_label.setPixmap(m)
        if cat == "video":
            # a small "play" badge over the thumbnail
            self.lbl_play = QLabel("▶")
            self.lbl_play.setStyleSheet(
                "font-size:30px; color:white; background:rgba(0,0,0,120);"
                " border-radius:18px;")
            self.lbl_play.setFixedSize(38, 38)
            self.lbl_play.setAlignment(Qt.AlignCenter)
            self.lbl_play.move((self.BOX - 38) // 2, (self.BOX - 38) // 2)
            self.lbl_play.show()
            self.lbl_play.raise_()

    def _top_icon(self, icon):
        self.top_label = QLabel(icon)
        self.top_label.setAlignment(Qt.AlignCenter)
        self.top_label.setFixedSize(self.BOX, self.BOX)
        self.top_label.setStyleSheet(
            "font-size:52px; background:#e9e9e9; border-radius:4px;")

    # -- click ------------------------------------------------------------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.item)
        super().mousePressEvent(ev)


class GradeGaleria(QScrollArea):
    """A tab's grid of GalleryItem (lazy: builds as the viewport grows)."""

    def __init__(self, category, window, parent=None):
        super().__init__(parent)
        self.category = category
        self.window = window
        self._itens = []
        self._construidos = 0
        self._stopped = False
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { border:none; background:white; }")
        self._central = QWidget()
        self._central.setStyleSheet("background:white;")
        self._grade = QGridLayout(self._central)
        self._grade.setContentsMargins(8, 8, 8, 8)
        self._grade.setHorizontalSpacing(8)
        self._grade.setVerticalSpacing(8)
        self.setWidget(self._central)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def set_items(self, itens):
        self._itens = list(itens)
        self._construidos = 0
        self._stopped = False
        self._central.deleteLater()
        self._central = QWidget()
        self._central.setStyleSheet("background:white;")
        self._grade = QGridLayout(self._central)
        self._grade.setContentsMargins(8, 8, 8, 8)
        self._grade.setHorizontalSpacing(8)
        self._grade.setVerticalSpacing(8)
        self.setWidget(self._central)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._fill()
        if not self._itens:
            lbl = QLabel("No items in this tab.")
            lbl.setStyleSheet("color:#667781; font-size:12px; padding:24px;")
            lbl.setAlignment(Qt.AlignCenter)
            self._grade.addWidget(lbl, 0, 0)

    def _columns(self):
        return max(1, self.viewport().width() // (GalleryItem.BOX + 20))

    def _fill(self):
        """Builds items until the grid is ~2 viewports tall (lazy)."""
        n_col = self._columns()
        alvo = len(self._itens)
        while self._construidos < alvo and not self._stopped:
            i = self._construidos
            item = self._itens[i]
            w = GalleryItem(self.category, item, self.window)
            w.clicked.connect(self.window._on_gallery_attachment_clicked)
            self._grade.addWidget(w, i // n_col, i % n_col)
            self._construidos += 1
            if (self._construidos - 1) % n_col == n_col - 1:
                lines = (self._construidos - 1) // n_col + 1
                if lines * (GalleryItem.BOX + 48) > 2 * self.viewport().height():
                    self._stopped = True

    def _on_scroll(self, valor):
        if self._stopped and self._construidos < len(self._itens):
            self._stopped = False
            self._fill()


# ---------------------------------------------------------------------------
# Details window (contact/group info + attachment gallery, WhatsApp-like)
# ---------------------------------------------------------------------------

class DetailsWindow(QMainWindow):
    """Opens when the chat title is clicked: the contact/group info (date
    range, groups the contact belongs to) + a gallery of the conversation's
    attachments, in 6 tabs (images / videos / audios / stickers / links /
    documents). Item clicks reuse the chat's attachment logic."""

    def __init__(self, nucleo, conversation, parent=None):
        super().__init__(parent)
        self.nucleo = nucleo
        self.conversation = conversation
        self.jid = conversation["jid"]
        self._workers = set()
        self._image_window = None
        self._info = None
        self._attachments = None
        name = conversation["nome"]
        self.setWindowTitle(
            f"Details — {name}" + (" (group)" if conversation["grupo"] else ""))
        self.resize(760, 560)
        self._build_ui(name)
        # load the data in the background (SQLite)
        self._worker = WorkerDetails(nucleo, self.jid, parent=self)
        self._worker.ready.connect(self._ready)
        self._worker.progress.connect(self._on_progress)
        self._worker.error.connect(self._error)
        self._worker.start()

    def _on_progress(self, n):
        """Gallery scan progress (keeps the 'Loading…' line informative)."""
        if not hasattr(self, "lbl_details"):
            return
        self.lbl_details.setText(f"Scanning messages… {n:,}")

    # ------------------------------------------------------------ UI -------
    def _build_ui(self, name):
        central = QWidget()
        self.setCentralWidget(central)
        vb = QVBoxLayout(central)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)

        # header: name + type + (dates added when data arrives)
        cab = QFrame()
        cab.setStyleSheet(f"background:{HEADER_BG_COLOR};")
        cab.setFixedHeight(56)
        hb = QHBoxLayout(cab)
        hb.setContentsMargins(14, 8, 14, 8)
        hb.setSpacing(10)
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(40, 40)
        self.avatar_label.setAlignment(Qt.AlignCenter)
        pm = _avatar_pixmap(self.nucleo, self.jid, name, 40)
        if not pm.isNull():
            self.avatar_label.setPixmap(
                pm.scaled(40, 40, Qt.KeepAspectRatio,
                          Qt.SmoothTransformation))
        else:
            self.avatar_label.setText("👥" if self.conversation["grupo"] else "👤")
        self.avatar_label.setStyleSheet("background:transparent;")
        hb.addWidget(self.avatar_label)
        vb2 = QVBoxLayout()
        vb2.setSpacing(1)
        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet("color:white; font-size:15px; font-weight:bold;")
        vb2.addWidget(self.lbl_name)
        self.lbl_details = QLabel("Loading…")
        self.lbl_details.setStyleSheet("color:#e6f5ef; font-size:11px;")
        vb2.addWidget(self.lbl_details)
        hb.addLayout(vb2, 1)
        vb.addWidget(cab)

        # body: splitter [info | gallery]
        split = QSplitter(Qt.Horizontal)
        vb.addWidget(split, 1)

        # left: info — a QScrollArea so long content (e.g. a group with many
        # members) scrolls INSTEAD of stretching the window
        info_scroll = QScrollArea()
        info_scroll.setWidgetResizable(True)
        info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        info_scroll.setStyleSheet("QScrollArea { border:none; background:#fff; }")
        info = QWidget()
        ivb = QVBoxLayout(info)
        ivb.setContentsMargins(10, 10, 4, 10)
        ivb.setSpacing(6)
        self.info_label = QLabel("Loading details…")
        self.info_label.setTextFormat(Qt.RichText)
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet(
            "QLabel { background:#f7f8fa; border:1px solid #e3e3e3;"
            " border-radius:6px; padding:8px; font-size:12px; }")
        self.info_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.info_label.linkActivated.connect(self._on_link_info)
        ivb.addWidget(self.info_label)
        # groups list (contact only)
        self.lbl_groups = QLabel("")
        self.lbl_groups.setTextFormat(Qt.RichText)
        self.lbl_groups.setWordWrap(True)
        self.lbl_groups.setStyleSheet(
            "QLabel { background:#f7f8fa; border:1px solid #e3e3e3;"
            " border-radius:6px; padding:8px; font-size:12px; }")
        self.lbl_groups.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.lbl_groups.linkActivated.connect(self._on_link_info)
        ivb.addWidget(self.lbl_groups)
        ivb.addStretch(1)
        info_scroll.setWidget(info)
        split.addWidget(info_scroll)

        # right: gallery tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabBar::tab { background:white; padding:6px 12px;"
            " font-size:11px; }"
            "QTabBar::tab:selected { background:#d9fdd3; }")
        split.addWidget(self.tabs)
        self._grades = {}
        for key, label, _cor in ABAS_GALERIA:
            g = GradeGaleria(key, self)
            self.tabs.addTab(g, label)
            self._grades[key] = g

        split.setSizes([240, 500])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

    # ------------------------------------------------------- data ---------
    def _ready(self, res):
        self._info = res.get("info")
        self._attachments = res.get("anexos") or {}
        if not self._info:
            self.info_label.setText("<b>Could not load the details.</b>")
            self.lbl_details.setText("")
            return
        info = self._info
        # header sub-line: date range + total + type
        d_min = _short_date_ts(info.get("ts_min"))
        d_max = _short_date_ts(info.get("ts_max"))
        parts = []
        if d_min and d_max and d_min != d_max:
            parts.append(f"📅 {d_min} → {d_max}")
        elif d_min:
            parts.append(f"📅 {d_min}")
        parts.append(f"{info.get('total', 0):,} messages")
        self.lbl_details.setText("   ·   ".join(parts))
        # info panel
        self._fill_info(info)
        # gallery
        for key, _r, _c in ABAS_GALERIA:
            self._grades[key].set_items(self._attachments.get(key, []))
        # open the first non-empty tab
        for key, _r, _c in ABAS_GALERIA:
            if self._attachments.get(key):
                self.tabs.setCurrentWidget(self._grades[key])
                break

    def _error(self, msg):
        self.info_label.setText(
            f"<b>Failed to load the details:</b><br>{_esc(msg)}")
        self.lbl_details.setText("")

    def _fill_info(self, info):
        is_group = info["grupo"]
        lines = []
        if is_group:
            if info.get("descricao"):
                lines.append(
                    f"<b>About</b><br>{_esc(info['descricao'])[:1200]}")
            # full participant list (GroupParticipants), with a count header
            membros = self._members_list(info["jid"])
            if membros:
                bloco = [f"<b>Members ({len(membros)})</b>"]
                for m in membros:
                    bloco.append(f"<br>• {_esc(m)}")
                lines.append("".join(bloco))
            elif info.get("membros"):
                lines.append(f"<b>Members</b><br>{info['membros']}")
            dono_nome = info.get("dono_nome")
            if dono_nome:
                lines.append(f"<b>Admin</b><br>{_esc(dono_nome)}")
            lines.append(
                f"<b>Created/last seen</b><br>"
                f"{_short_date_ts(info.get('ts_min')) or '—'} – "
                f"{_short_date_ts(info.get('ts_max')) or '—'}")
        else:
            c = info.get("contato") or {}
            lines.append(f"<b>Name</b><br>{_esc(c.get('nome') or '—')}")
            if c.get("numero"):
                lines.append(
                    f"<b>Number</b><br><a href='zapix:number|"
                    f"{_esc(c['numero'])}'>{_esc(c['numero'])}</a>")
            if c.get("status"):
                lines.append(f"<b>Status</b><br>{_esc(c['status'])}")
            self.info_label.setText("<br><br>".join(lines))
            # groups the contact belongs to
            grupos = info.get("grupos") or []
            if grupos:
                gl = [f"<b>Groups ({len(grupos)})</b>"]
                for g in grupos:
                    n = g.get("nome") or g.get("jid")
                    gl.append(f"<br>• {_esc(n)}")
                self.lbl_groups.setText("".join(gl))
            else:
                self.lbl_groups.setText("")
        if is_group:
            self.info_label.setText("<br><br>".join(lines) or "—")

    def _members_list(self, jid):
        """Full list of a group's member jids (GroupParticipants), with the
        best display name for each (UserStatuses -> conversation name -> jid).
        Returns a list of str names. Defensive: any DB problem yields []."""
        if not self.nucleo:
            return []
        try:
            rows = self.nucleo.msg_db.execute(
                "SELECT MemberJid FROM GroupParticipants "
                "WHERE GroupJid = ?", (jid,)).fetchall()
        except Exception:
            return []
        names = []
        for (member_jid,) in rows:
            if not member_jid:
                continue
            name = self.nucleo._nome_do(member_jid) or \
                str(member_jid).split("@")[0]
            names.append(str(name).strip() or str(member_jid).split("@")[0])
        names.sort(key=lambda s: s.lower())
        return names

    # --------------------------------------------------------- clicks ------
    def _on_link_info(self, url):
        """Handles <a href='zapix:...'> inside the info panel."""
        s = url.toString() if hasattr(url, "toString") else str(url)
        s = urllib.parse.unquote(s)
        if s.startswith("zapix:"):
            s = s[len("zapix:"):]
        if "|" in s:
            action, ref = s.split("|", 1)
        else:
            action, ref = s, ""
        if action == "number":
            QDesktopServices.openUrl(QUrl(f"https://wa.me/{ref}"))

    def _on_gallery_attachment_clicked(self, item):
        """Gallery item click → the SAME logic as the chat bubbles."""
        cat = self.active_category()
        if cat == "link":
            url = (item.get("texto") or "").strip()
            if url:
                QDesktopServices.openUrl(QUrl(url))
            return
        name = item.get("nome") or "attachment"
        if cat == "imagem":
            self._open_image(item, name)
        else:
            # video / audio / sticker / document: open with the default app
            self._open_with_app(item, name)

    def active_category(self):
        w = self.tabs.currentWidget()
        for key, g in self._grades.items():
            if g is w:
                return key
        return "imagem"

    def _open_image(self, item, name):
        """Shows the image in a full-size viewer (zip bytes, else the blob)."""
        lfu = item.get("lfu")
        data = self.nucleo.zip.bytes(lfu) if (self.nucleo and lfu) else None
        if data is None:
            data = item.get("bin")
        pm = QPixmap()
        if data:
            pm.loadFromData(data)
        if pm.isNull():
            QMessageBox.warning(self, APP_TITLE,
                                'Could not load the image.\n'
                                "The file may not be in the export.")
            return
        top = ImageView(pm, f"🖼 {name}", self)
        self._image_window = top
        top.show()

    def _open_with_app(self, item, name):
        """Resolves the file (cache/zip) and opens it with the default app."""
        lfu = item.get("lfu")
        if not (self.nucleo and lfu):
            # no file in the export — fall back to a one-off from the blob
            _bin = item.get("bin")
            if not _bin:
                QMessageBox.warning(self, APP_TITLE,
                                    "This file is not in the export.")
                return
            import time as _t
            cache = os.path.join(tempfile.gettempdir(), "zapixdesk_cache")
            os.makedirs(cache, exist_ok=True)
            destination = os.path.join(cache, f"{int(_t.time()*1000)}_{name}")
            with open(destination, "wb") as f:
                f.write(_bin)
            self._open_path(destination, name)
            return
        w = WorkerDocument(self.nucleo, lfu, parent=self)
        self._workers.add(w)
        w.ready.connect(
            lambda path, n=name: self._open_path(path, n))
        w.error.connect(
            lambda e, n=name: QMessageBox.critical(self, APP_TITLE, n + "\n" + e))
        w.finished.connect(lambda: self._workers.discard(w))
        w.start()

    def _open_path(self, path, name):
        if not path:
            QMessageBox.warning(self, APP_TITLE,
                                'Could not open the file.\n'
                                "It may not be in the export.")
            return
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE,
                                 f"Could not open automatically:\n{exc}\n\n"
                                 f"Extracted to: {path}")

    def closeEvent(self, ev):
        try:
            if self._worker and self._worker.isRunning():
                self._worker.stop()
                self._worker.wait(1200)
        except Exception:
            pass
        ev.accept()


# ---------------------------------------------------------------------------
# "Bubble" widget (QLabel with rich text) inside the QGraphicsScene
# ---------------------------------------------------------------------------

MAX_BUBBLE_WIDTH = 440  # px (same limit as the original viewer)


class Bubble(QLabel):
    """A message bubble: QLabel with HTML, limited width.

    The colored background (owner's green / other's white) is applied via
    the WIDGET's stylesheet (reliable), not via the rich text's <body> —
    QLabel ignores the HTML <body> background on many platforms."""

    def __init__(self, html_content, from_me, cor_fundo, cor_texto):
        super().__init__()
        # bubble background: the whole widget (reliable)
        self.setStyleSheet(
            f"QLabel {{ background:{cor_fundo}; border-radius:8px; }}")
        # text: colors in the rich text CSS (the body stays transparent)
        css = CSS_BALAO.format(
            cor_texto=cor_texto, cor_fundo="transparent",
            cor_rem=SENDER_COLOR_MINE if from_me else SENDER_COLOR)
        self.setText(f"<html><head><style>{css}</style></head>"
                     f"<body>{html_content}</body></html>")
        self.setTextFormat(Qt.RichText)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setWordWrap(True)
        self.from_me = bool(from_me)  # used by ChatView to right-align
        # content width is limited (sizeHint caps at MAX_BUBBLE_WIDTH);
        # the height adjusts. The horizontal position (left/right) is owned
        # by the NESTED HBox row that ChatView wraps the bubble in — not by
        # this widget's size policy.
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # enable text interaction (so links can be clicked)
        self.setTextInteractionFlags(Qt.TextBrowserInteraction)

    def mousePressEvent(self, event):
        print(f"[Bubble] mousePressEvent: pos={event.position()}, "
              f"contains 'zapix': {'zapix' in self.text()}")
        super().mousePressEvent(event)

    def sizeHint(self):
        # forces the maximum width (the original viewer's wraplength)
        s = super().sizeHint()
        return QSize(min(s.width(), MAX_BUBBLE_WIDTH), s.height())


# ---------------------------------------------------------------------------
# Chat area (QGraphicsView + scene) — resizing leaves NO ghosting
# ---------------------------------------------------------------------------


class ChatView(QScrollArea):
    """QScrollArea with a QVBoxLayout for the chat bubbles.

    Like WhatsApp: the most recent messages sit at the BOTTOM and the
    older ones are loaded ABOVE as you scroll up."""
    
    # emitted by MainWindow when an <a href="zapix:..."> in a bubble is clicked
    link_clicked = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # ALWAYS ON (not AsNeeded): with AsNeeded, Qt may REMOVE the
        # scrollbar widget entirely while the content is not scrollable
        # (empty hint state), and only recreate it once the content grows.
        # Recreated bar + stale sb.maximum() is exactly what defeated the
        # "jump to the newest messages" force: the final setValue landed on
        # a stale/rebuilt bar and the view opened a few messages too high.
        # A permanent bar (disabled while there is nothing to scroll) makes
        # sb.maximum() stable and the bottom jump reliable.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        # Container widget with a vertical layout.
        #
        # NO top stretch: the content is anchored at the TOP of the widget.
        # The most-recent messages sit at the BOTTOM of the layout. Keeping
        # the anchor at the top (instead of a stretch that grows/shrinks) is
        # what lets us insert older messages above WITHOUT the viewport
        # sliding — see _top_anchor / restore_top_anchor.
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(4)

        # Background
        self._container.setStyleSheet(f"background: {CHAT_BG_COLOR};")
        self._y_cursor = 0  # Not needed with QScrollArea
        self._largura = 0
        self._scroll_rigido = False
        # top-insertion anchor (keeps the viewport still while older
        # messages are inserted above)
        self._top_anchor = None  # (widget, y_of_widget_in_content, sb_value)
        # viewport pin: while older messages are being inserted above, the
        # scrollbar value is LOCKED — nothing the layout does can move the
        # viewport (no scroll jumps while the older pages render).
        # NOTE: the pin attributes must exist BEFORE setWidget() below,
        # because the scrollbar's event filter (installed right after)
        # already receives events during setWidget().
        self._pin_sb = None          # int: locked scrollbar value, or None
        self._pin_cold = False       # True = FROZEN mode (hold exact value)
        self._pin_pendente = False   # a force is queued for the idle tick
        # ONE long-lived timer drives the pin (started once, stopped on
        # release) — never loose singleShot()s, which can force a stale
        # value back after the render loop that created them has moved on
        self._pin_timer = QTimer(self)
        self._pin_timer.setInterval(16)  # ~60fps idle tick
        self._pin_timer.setTimerType(Qt.PreciseTimer)
        self._pin_timer.timeout.connect(self._pin_tick)

        # search highlight: MessageID -> (bubble widget, original background)
        self._bubbles_mid = {}
        self._marked_mids = set()

        self.setWidget(self._container)

    # ------------------------------------------------------- scrollbar pin --
    def pin_active(self):
        return self._pin_sb is not None

    def pin_enable(self, valor=None):
        """Locks the viewport: the scrollbar is held at `valor` (default:
        current value) for as long as the pin is active — inserts at the top
        cannot move the screen while older messages render. Also (re)starts
        the one long-lived enforcement timer. In FROZEN mode (_pin_cold)
        the pin holds the exact value; in BOTTOM mode it tracks the max."""
        if valor is None:
            valor = self.verticalScrollBar().value()
        self._pin_sb = int(valor)
        self._pin_pendente = True
        if self._pin_timer is not None and not self._pin_timer.isActive():
            self._pin_timer.start()

    def pin_update(self, fim=True):
        """Re-anchors the pin for the next batch. `fim=True` (the default):
        the pin tracks the BOTTOM of the content — as batches are appended
        the newest messages stay glued to the bottom. `fim=False` (top
        insertion, user reading older content): the pin freezes at the
        current value, so the viewport does not slide while items appear
        above."""
        if self._pin_sb is None:
            return
        sb = self.verticalScrollBar()
        self._pin_sb = sb.maximum() if fim else sb.value()
        self._pin_pendente = True

    def pin_disable(self):
        """Releases the pin: the viewport stays where it is (the bottom, if
        it was there; the user's reading spot if they scrolled up) and the
        scrollbar is clamped to the final content size. Also stops the
        enforcement timer so it cannot fire with a stale target."""
        self._pin_pendente = False
        self._pin_cold = False
        if self._pin_timer is not None:
            self._pin_timer.stop()
        if self._pin_sb is None:
            return
        alvo = self._pin_sb
        self._pin_sb = None
        sb = self.verticalScrollBar()
        alvo = max(sb.minimum(), min(alvo, sb.maximum()))
        sb.setValue(alvo)

    def _pin_force(self):
        """Marks the pin as needing enforcement. The ONE long-lived
        `_pin_timer` (created in __init__) picks it up on the next idle tick
        — never a `singleShot` here: loose one-shot timers can outlive the
        render loop that created them and force a STALE value back into the
        scrollbar, fighting the layout and making the view slide/jump.
        """
        if self._pin_sb is None:
            return
        self._pin_pendente = True

    def _pin_tick(self):
        """Idle tick while the pin is active.

        * BOTTOM mode (default, initial render): the pin chases the CURRENT
          maximum, so the newest messages stay glued to the bottom while the
          content grows.
        * FROZEN mode (_pin_cold, top-insert run): the pin holds the exact
          value it was given — the viewport does NOT slide while older items
          appear above.
        """
        if self._pin_sb is None:
            return
        sb = self.verticalScrollBar()
        if self._pin_cold:
            alvo = self._pin_sb  # frozen: hold exactly where we were told
        else:
            alvo = sb.maximum()  # bottom: follow the newest
        alvo = max(sb.minimum(), min(alvo, sb.maximum()))
        if not self._pin_cold and self._pin_sb != alvo:
            self._pin_sb = alvo
        if sb.value() != alvo:
            sb.setValue(alvo)

    def showEvent(self, ev):
        # never let the window's initial show scroll the chat away from the
        # newest messages
        super().showEvent(ev)
        if self._pin_sb is not None:
            self._pin_force()

    def wheelEvent(self, ev):
        # user scrolls while the pin is active: the viewport is now the
        # user's — release the pin (the enforcement loop stops on the next
        # tick because _pin_sb becomes None). If a top-insert run is in
        # progress, _render_top will re-arm the pin (in FROZEN mode, at the
        # user's new position) so the screen does not slide back.
        if self._pin_sb is not None and ev.angleDelta().y() != 0:
            self._pin_sb = None
            self._pin_pendente = False
        super().wheelEvent(ev)

    # ------------------------------------------------ bubble <a> click --
    def _on_link_activated(self, url):
        """QLabel native signal (rich text) when an <a> is clicked."""
        try:
            s = url.toString() if hasattr(url, "toString") else str(url)
            if "zapix" in s:
                self.link_clicked.emit(s)
        except Exception:
            pass
    
    # ------------------------------------------------------------------ --
    def _largura_util(self):
        return max(320, self.viewport().width() - 24)
    
    def _update_width(self):
        """Called on resizeEvent: adjusts the layout."""
        self._largura = self._largura_util()
    
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_width()
        # a resize can reflow the layout and move the scrollbar: if the pin
        # is alive, push the viewport back to where it belongs
        if self._pin_sb is not None:
            self._pin_force()
    
    # ----------------------------------------------------------- public --
    def _add_widget(self, widget, from_me):
        """Adds a widget at the BOTTOM of the layout (newest side), wrapped
        in a full-width row so it can be aligned left or right.

        `from_me` (== "align right"): True → the bubble sits on the right
        (stretch before it); False → on the left (stretch after it).

        A bare QVBoxLayout cannot push a widget to one side (a vertical
        spacer only affects the Y axis, and CSS `align-items` does not
        exist in Qt's stylesheet language — it was silently ignored, which
        is why every bubble used to land on the left). Wrapping the bubble
        in a nested QHBoxLayout with a QSpacerItem is the reliable way:
        the row spans the full width and the horizontal stretch pushes the
        bubble to the requested side."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        if from_me:
            h.addStretch(1)
            h.addWidget(widget)
        else:
            h.addWidget(widget)
            h.addStretch(1)
        self._layout.addWidget(row)
        return widget

    def add_bubble(self, html_content, from_me, cor_fundo):
        """Creates a bubble (QLabel rich text) at the BOTTOM of the chat."""
        lbl = Bubble(html_content, from_me, cor_fundo, TEXT_COLOR)
        lbl.linkActivated.connect(self._on_link_activated)
        self._add_widget(lbl, from_me)
        # register for search highlighting (cleared in clear())
        mid = getattr(lbl, "_mid", None)
        if mid is not None:
            self._bubbles_mid[mid] = (lbl, cor_fundo)
        return lbl

    def add_day_separator(self, day_text):
        """Date separator (centered pill, WhatsApp style)."""
        pil = QLabel(f'<span style="background:{DATE_BADGE_COLOR};'
                     f'color:{SOFT_COLOR};font-size:11px;'
                     f'padding:3px 12px;border-radius:10px;">'
                     f'{_esc(day_text)}</span>')
        pil.setAlignment(Qt.AlignCenter)
        pil.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._layout.addWidget(pil)
        return pil

    # --------------------------------------------------- top insertion --
    def _bubbles_widget(self):
        """The list of bubble/separator widgets in the layout (layout order
        = oldest → newest). Used to compute the top-insertion anchor."""
        out = []
        for i in range(self._layout.count()):
            w = self._layout.itemAt(i).widget()
            if w is not None:
                out.append(w)
        return out

    def prepare_top_anchor(self):
        """Arms the pin in FROZEN mode just before inserting older items
        above the current content: the viewport is held at its CURRENT
        position (wherever the user is — on the newest messages or reading
        older ones) so the screen does NOT slide while the older page
        renders. `restore_top_anchor()` is a no-op (the enforcement loop
        does the correction); the pin is released by `_render_top` when the
        queue empties.
        """
        self._pin_cold = True
        self.pin_enable()

    def insert_bubble_top(self, html_content, from_me, cor_fundo):
        """Creates a bubble and inserts it at the TOP (oldest side), wrapped
        in a full-width row (same left/right rule as `add_bubble`).
        The row is inserted at layout position 0 (above the previously-
        oldest item) so it does not disturb the top-insertion anchor."""
        lbl = Bubble(html_content, from_me, cor_fundo, TEXT_COLOR)
        lbl.linkActivated.connect(self._on_link_activated)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        if from_me:
            h.addStretch(1)
            h.addWidget(lbl)
        else:
            h.addWidget(lbl)
            h.addStretch(1)
        self._layout.insertWidget(0, row)
        # register for search highlighting (cleared in clear())
        mid = getattr(lbl, "_mid", None)
        if mid is not None:
            self._bubbles_mid[mid] = (lbl, cor_fundo)
        return lbl

    def insert_separator_top(self, day_text):
        """Date separator inserted at the TOP (oldest side)."""
        pil = QLabel(f'<span style="background:{DATE_BADGE_COLOR};'
                     f'color:{SOFT_COLOR};font-size:11px;'
                     f'padding:3px 12px;border-radius:10px;">'
                     f'{_esc(day_text)}</span>')
        pil.setAlignment(Qt.AlignCenter)
        pil.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._layout.insertWidget(0, pil)
        return pil

    def restore_top_anchor(self):
        """After inserting older items at the top, keeps the pin in the mode
        that `prepare_top_anchor()` armed:

        * mode BOTTOM (user was on the newest messages) → the enforcement
          loop re-anchors to the new bottom on the next tick automatically;
        * mode FROZEN (user was reading older content) → the pin value is
          already frozen at the pre-insert value; the enforcement loop will
          push the viewport back there after the layout settles (Qt shifts
          the offset when content is inserted above, which we correct).
        """
        # nothing to do here: the pin is already armed (in BOTTOM or
        # FROZEN mode) and the enforcement loop (_pin_tick) does the
        # actual correction on the next idle tick.
        return

    def clear(self):
        """Clear all bubbles."""
        self._top_anchor = None
        self.pin_disable()
        # drop any queued enforcement forces from a previous run, so a stale
        # pin target cannot fire AFTER this clear (e.g. between clear() and
        # the new conversation's pin_enable()).
        self._pin_pendente = False
        while self._layout.count() > 0:
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._bubbles_mid.clear()
        self._marked_mids.clear()
        # Scroll to the top (oldest messages)
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    # --------------------------------------------------------- search ------
    def mark_mid(self, mids, cor="FFF3BF"):
        """Highlights the bubbles whose MessageID is in `mids` (search
        results). The original background is remembered so clear_highlight()
        can restore it. Returns the number of bubbles actually highlighted
        (0 when the matches are not loaded/rendered yet)."""
        n = 0
        for mid in mids:
            if mid in self._marked_mids:
                continue
            ent = self._bubbles_mid.get(mid)
            if ent is None:
                continue
            w, original = ent
            w.setStyleSheet(
                f"QLabel {{ background:{cor}; border:2px solid #E8A33D;"
                f" border-radius:8px; }}")
            self._marked_mids.add(mid)
            n += 1
        return n

    def clear_highlight(self):
        """Restores the original background of every highlighted bubble."""
        for mid in list(self._marked_mids):
            ent = self._bubbles_mid.get(mid)
            if ent is None:
                self._marked_mids.discard(mid)
                continue
            w, original = ent
            try:
                w.setStyleSheet(
                    f"QLabel {{ background:{original}; border-radius:8px; }}")
            except RuntimeError:
                # widget already destroyed (clear() in flight)
                self._bubbles_mid.pop(mid, None)
            self._marked_mids.discard(mid)

    def scroll_to_bottom(self):
        """Bottom of the chat = the most recent messages (scrollbar max).
        When a pin is alive it already owns the viewport (it re-anchors to
        the bottom on its own) — so we just let it do its job and do NOT
        touch the scrollbar here (touching it would queue another force)."""
        if self._pin_sb is not None:
            return
        sb = self.verticalScrollBar()
        if self._scroll_rigido:
            sb.setPageStep(sb.maximum() or 1)
        sb.setValue(sb.maximum())

    def scroll_to_top(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.minimum())

    def set_rigid_scroll(self, enabled):
        """When True, scroll_to_bottom() jumps instantly (no smooth scroll)."""
        self._scroll_rigido = enabled

    def scroll_to_mid(self, mid, margem=120):
        """Scrolls the viewport so the bubble with `mid` is visible
        (roughly centered). Returns True when the bubble exists and was
        scrolled to; False when the message is not loaded/rendered yet."""
        ent = self._bubbles_mid.get(mid)
        if ent is None:
            return False
        w = ent[0]
        try:
            if not w.isVisible():
                w.ensurePolished()
            alvo = w.y() - max(40, self.viewport().height() // 2 - w.height() // 2)
            sb = self.verticalScrollBar()
            alvo = max(sb.minimum(), min(alvo, sb.maximum() - margem))
            sb.setValue(alvo)
            return True
        except RuntimeError:
            return False


# ---------------------------------------------------------------------------
# Conversation list delegate (avatar + name + summary, WhatsApp-like)
# ---------------------------------------------------------------------------

class ListItem(QStyledItemDelegate):
    """Draws a conversation row: circular avatar on the left, then the name
    (bold, with an unread pill) and the last-message summary (gray) below it.
    Replaces the plain '👥 name' text so each chat shows an avatar."""

    ALTO = 64          # row height
    AVATAR = 44        # avatar box (px)
    MARGEM = 12

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window

    def paint(self, p, op, index):
        # background (selection / hover) — the delegate owns the row
        selected = (op.state & QStyle.State_Selected)
        hover = (op.state & QStyle.State_Active) and \
            (op.state & QStyle.State_HasFocus)
        p.save()
        if selected:
            p.fillRect(op.rect, QColor(LIST_COLOR_SELECTED))
        p.restore()
        c = index.data(Qt.UserRole)
        if not c:
            super().paint(p, op, index)
            return
        # avatar
        pm = _avatar_pixmap(self.window.nucleo, c["jid"], c["nome"],
                            self.AVATAR)
        x = self.MARGEM
        y = op.rect.top() + (self.ALTO - self.AVATAR) // 2
        p.drawPixmap(x, y, pm)
        # text block to the right of the avatar
        tx = x + self.AVATAR + 12
        ty = op.rect.top() + 10
        tw = op.rect.right() - tx - 8
        if tw < 40:
            tw = 40
        # name (bold) — truncate with ellipsis
        name = c["nome"] or c["jid"]
        file_name = QFontMetrics(QFont("DejaVu Sans", 12, QFont.Bold))
        conv_name = file_name.elidedText(name, Qt.ElideRight, tw)
        p.setPen(QColor(TEXT_COLOR))
        file_name = QFont("DejaVu Sans", 12, QFont.Bold)
        p.setFont(file_name)
        p.drawText(QRectF(tx, ty, tw, 18), Qt.AlignVCenter, conv_name)
        # summary (gray) — the last message / type
        resumo = c.get("resumo", "")
        if not resumo:
            resumo = ("👥 " if c["grupo"] else "💬 ") + name
        fm_res = QFontMetrics(QFont("DejaVu Sans", 11))
        summary = fm_res.elidedText(resumo, Qt.ElideRight, tw)
        p.setPen(QColor(SOFT_COLOR))
        f_res = QFont("DejaVu Sans", 11)
        p.setFont(f_res)
        p.drawText(QRectF(tx, ty + 20, tw, 16), Qt.AlignVCenter, summary)
        # unread badge (top-right)
        nao_lidas = c.get("nao_lidas", 0)
        if nao_lidas:
            txt = str(nao_lidas) if nao_lidas <= 99 else "99+"
            fm_b = QFontMetrics(QFont("DejaVu Sans", 10, QFont.Bold))
            bw = fm_b.horizontalAdvance(txt) + 12
            bh = 18
            bx = op.rect.right() - bw - 8
            by = op.rect.top() + 8
            p.setBrush(QColor(SOFT_COLOR))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(bx, by, bw, bh, bh / 2, bh / 2)
            p.setPen(QColor("white"))
            f_b = QFont("DejaVu Sans", 10, QFont.Bold)
            p.setFont(f_b)
            p.drawText(QRectF(bx, by, bw, bh), Qt.AlignCenter, txt)
        p.restore()

    def sizeHint(self, op, index):
        # width: prefer the item's own size hint (set by the view layout);
        # QModelIndex has no sizeHint(), so fall back to the viewport width
        view = op.widget
        if view is not None:
            w = view.viewport().width()
        else:
            w = 320
        return QSize(max(w, 120), self.ALTO)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    # Bubbles rendered per render tick (keeps the UI responsive)
    _BATCH_RENDER = 25

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1150, 740)
        self.setMinimumSize(780, 500)
        self._image_window = None  # reference to the image viewer
        self._details_window = None  # reference to the details window

        # state
        self.nucleo = None
        self.zip_path = None
        self.owner_name = None      # account owner's name (never "You")
        self._current_conversation = None
        self._current_offset = 0
        self._current_total = 0
        self._ha_mais = False
        self._last_day = None
        self._render_ocupado = False
        self._worker_open = None
        self._worker_attachments = None
        self._worker_conversation = None
        self._worker_older = None     # WorkerOlder (older messages)
        self._worker_export = None
        self._attachment_workers = set()  # bytes QThreads (kept alive)
        self._pending_bubbles = []   # recent messages awaiting render (batches)
        self._pending_c = None
        self._msgs_loaded = []  # all loaded messages (attachments); grows
                                    # as older pages arrive
        self._pending_selection = None  # conversation picked while rendering
        self._conversations_cache = {}     # jid -> {"c", "total", "msgs", "fim"}
                                     # finished loads, for instant re-open
        # older-messages pipeline (recent-first loading)
        self._older_bubbles = []        # older messages awaiting top-insert render
        self._older_rendering = False  # True while a top-insert batch is running
        self._ts_min = None          # oldest loaded timestamp (header date)
        self._ts_max = None          # newest loaded timestamp (header date)
        self._top_last_day = None # last day separator placed at the top
        self._PRIMEIRAS = 300        # how many most-recent msgs to load first
        self._fast_mode = False    # False = "load all first" (default)
        self._loading_screen_label = None  # the "Loading…" overlay widget
        # message search (global or inside the open chat)
        self._search_term = None     # active search term (None = no search)
        self._search_scope = "chat"  # "chat" (open conversation) | "global"
        self._search_worker = None    # WorkerSearchGlobal / WorkerSearchConversation
        self._search_current_jid = None  # conversation the open-chat search ran in
        self._search_chat_mids = []   # MessageIDs matching in the open chat
                                    # (chronological) for Up/Down navigation
        self._search_chat_index = -1    # position in _search_chat_mids
        self._search_cursor = None    # (ts, mid) cursor for the global search
        self._search_end = False      # no older global matches remain
        self._search_total = 0        # total global matches (count(*))
        self._search_loaded = 0   # global matches loaded so far
        self._search_target_mid = None  # message to scroll to (global click)
        self._search_attempts = 0   # retries of _schedule_search_mid
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(350)
        self._search_debounce.timeout.connect(self._search)

        self._build_ui()
        self._set_icon()

    # ------------------------------------------------------------------ UI --
    def _set_icon(self):
        """Window icon: the logo mark (no text) — logo_icone.png, with
        zapixdesk.ico as the fallback. Resolves next to this script first
        (see _recurso), since the branding assets live there."""
        p = _recurso("logo_icone.png")
        if p and os.path.isfile(p):
            self.setWindowIcon(QIcon(p))
            return
        p = _recurso("zapixdesk.ico")
        if p and os.path.isfile(p):
            self.setWindowIcon(QIcon(p))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        raiz = QVBoxLayout(central)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        # ---- green header -------------------------------------------------
        header = QFrame()
        header.setStyleSheet(f"QFrame {{ background:{HEADER_BG_COLOR}; }}")
        header.setFixedHeight(52)
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(14, 8, 14, 8)
        hbox.setSpacing(8)
        icon = QLabel("💬")
        icon.setStyleSheet("color:white; font-size:20px;")
        hbox.addWidget(icon)
        self.lbl_app = QLabel(APP_TITLE)
        self.lbl_app.setStyleSheet("color:white; font-size:12px; font-weight:bold;")
        hbox.addWidget(self.lbl_app, 1)
        self.hash_bar = QProgressBar()
        self.hash_bar.setFixedWidth(140)
        self.hash_bar.setTextVisible(False)
        hbox.addWidget(self.hash_bar)
        btn_open = QPushButton("📂 Open…")
        btn_open.clicked.connect(self.open)
        hbox.addWidget(btn_open)
        raiz.addWidget(header)

        # ---- body: splitter [list | chat] --------------------------------
        split = QSplitter(Qt.Horizontal)
        raiz.addWidget(split, 1)

        # left panel: search + list
        panel_left = QFrame()
        vb = QVBoxLayout(panel_left)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)
        # message search: real text field + scope toggle (chat | all)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍  Search messages… (Enter)")
        self.search_edit.setStyleSheet(
            "QLineEdit { background:#f0f2f5; color:#111; border:none;"
            " border-bottom:1px solid #d5d5d5; font-size:12px; }")
        self.search_edit.setFixedHeight(34)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._search)
        self.search_edit.textEdited.connect(self._search_typed)
        vb.addWidget(self.search_edit)

        search_hbox = QHBoxLayout()
        search_hbox.setContentsMargins(6, 0, 6, 0)
        search_hbox.setSpacing(4)
        self.scope_btn = QPushButton("📄 Open chat")
        self.scope_btn.setObjectName("secundario")
        self.scope_btn.setStyleSheet(
            "QPushButton { background:#e4e6ea; color:#333; font-size:10px;"
            " padding:3px 8px; border-radius:4px; }"
            "QPushButton:hover { background:#d5d8dd; }")
        self.scope_btn.setToolTip(
            "Search scope: “Open chat” searches the conversation currently "
            "open (marks the matches in the chat, Up/Down walk between "
            "them); “All chats” searches the whole export.")
        self.scope_btn.setCheckable(True)
        self.scope_btn.toggled.connect(self._search_scope_changed)
        search_hbox.addWidget(self.scope_btn)
        self.search_result_label = QLabel("")
        self.search_result_label.setStyleSheet(
            "font-size:10px; color:#667781;")
        self.search_result_label.setToolTip(
            "Matches found (open-chat scope: how many are marked in the "
            "chat). Click a result to jump to it.")
        search_hbox.addWidget(self.search_result_label, 1)
        self.search_prev_btn = QPushButton("▲")
        self.search_prev_btn.setStyleSheet(
            "QPushButton { background:#e4e6ea; color:#333; font-size:10px;"
            " padding:3px 6px; border-radius:4px; }")
        self.search_prev_btn.setToolTip("Previous match (Up)")
        self.search_prev_btn.clicked.connect(lambda: self._search_navigate(-1))
        search_hbox.addWidget(self.search_prev_btn)
        self.search_next_btn = QPushButton("▼")
        self.search_next_btn.setStyleSheet(
            "QPushButton { background:#e4e6ea; color:#333; font-size:10px;"
            " padding:3px 6px; border-radius:4px; }")
        self.search_next_btn.setToolTip("Next match (Down)")
        self.search_next_btn.clicked.connect(lambda: self._search_navigate(1))
        search_hbox.addWidget(self.search_next_btn)
        vb.addLayout(search_hbox)

        # search results (global scope): conversation + snippet + date
        self.search_combo = QListWidget()
        self.search_combo.setSelectionMode(QAbstractItemView.SingleSelection)
        self.search_combo.setFocusPolicy(Qt.NoFocus)
        self.search_combo.itemClicked.connect(self._on_search_result_clicked)
        self.search_combo.hide()

        self.conversations_combo = QListWidget()
        self.conversations_combo.setSelectionMode(QAbstractItemView.SingleSelection)
        self.conversations_combo.setFocusPolicy(Qt.NoFocus)
        self.conversations_combo.itemClicked.connect(self._on_select)
        self.conversations_combo.setVerticalScrollMode(
            QAbstractItemView.ScrollPerPixel)
        self.conversations_combo.setItemDelegate(ListItem(self))
        vb.addWidget(self.search_combo, 1)
        vb.addWidget(self.conversations_combo, 1)
        split.addWidget(panel_left)

        # keyboard shortcuts: walk between open-chat search matches
        self.search_prev_scroll = QShortcut(QKeySequence("Up"), self)
        self.search_prev_scroll.activated.connect(lambda: self._search_navigate(-1))
        self.search_next_scroll = QShortcut(QKeySequence("Down"), self)
        self.search_next_scroll.activated.connect(lambda: self._search_navigate(1))

        # right panel: chat header + QGraphicsView
        panel_right = QFrame()
        vb2 = QVBoxLayout(panel_right)
        vb2.setContentsMargins(0, 0, 0, 0)
        vb2.setSpacing(0)
        self.header_chat = QFrame()
        self.header_chat.setStyleSheet("QFrame { background:white; }")
        self.header_chat.setFixedHeight(46)
        h2 = QHBoxLayout(self.header_chat)
        h2.setContentsMargins(12, 6, 12, 6)
        h2.setSpacing(8)
        self.avatar_header_label = QLabel()
        self.avatar_header_label.setFixedSize(36, 36)
        self.avatar_header_label.setAlignment(Qt.AlignCenter)
        self.avatar_header_label.setStyleSheet(
            "QLabel { background:transparent; font-size:18px; }")
        self.avatar_header_label.setText("💬")
        h2.addWidget(self.avatar_header_label)
        # title (clickable → details window) + date range, stacked
        vt_titulo = QVBoxLayout()
        vt_titulo.setContentsMargins(0, 0, 0, 0)
        vt_titulo.setSpacing(0)
        self.lbl_titulo = QLabel("Select a conversation on the left.")
        self.lbl_titulo.setObjectName("titulo")
        self.lbl_titulo.setStyleSheet(
            "font-size:13px; font-weight:bold; color:#0b843b;"
            " background:transparent;")
        self.lbl_titulo.setCursor(Qt.PointingHandCursor)
        self.lbl_titulo.mousePressEvent = self._on_title_clicked
        self.lbl_titulo.setToolTip(
            "Click to open the details (info + attachment gallery).")
        vt_titulo.addWidget(self.lbl_titulo)
        self.lbl_dates = QLabel("")
        self.lbl_dates.setStyleSheet(
            "font-size:10px; color:#667781; background:transparent;")
        vt_titulo.addWidget(self.lbl_dates)
        # group members line (clickable → details with the full list)
        self.lbl_members = QLabel("")
        self.lbl_members.setStyleSheet(
            "font-size:10px; color:#5268f5; background:transparent;")
        self.lbl_members.setCursor(Qt.PointingHandCursor)
        self.lbl_members.mousePressEvent = self._on_title_clicked
        self.lbl_members.setToolTip(
            "Click to open the details (participant list + gallery).")
        vt_titulo.addWidget(self.lbl_members)
        h2.addLayout(vt_titulo, 1)
        # account owner indicator (clickable to redefine)
        self.owner_btn = QPushButton("👤 Owner: —")
        self.owner_btn.setObjectName("secundario")
        self.owner_btn.setStyleSheet(
            "QPushButton { background:#d9fdd3; color:#111; font-size:11px;"
            " padding:5px 10px; }")
        self.owner_btn.setToolTip(
            "Shows the account owner (green bubble).\n"
            "Click to set the owner from the selected conversation.")
        self.owner_btn.clicked.connect(self._set_owner)
        h2.addWidget(self.owner_btn)
        # The progress bar lives in the STATUS BAR (created in
        # _build_ui's status section) — a header bar was too cramped and
        # invisible for most of the load (pulsing at start, full at end).
        self.btn_export = QPushButton("⬇ Export")
        self.btn_export.setObjectName("secundario")
        self.btn_export.setStyleSheet("QPushButton { background:#008069; }")
        self.btn_export.clicked.connect(self._export_conversation)
        h2.addWidget(self.btn_export)
        # load-mode toggle: "load all first" (default) <-> "fast" (recent
        # page immediately, older ones in the background)
        self.fast_mode_btn = QPushButton("🐢 Full")
        self.fast_mode_btn.setObjectName("secundario")
        self.fast_mode_btn.setStyleSheet(
            "QPushButton { background:#e4e6ea; color:#333; font-size:11px;"
            " padding:5px 10px; }"
            "QPushButton:hover { background:#d5d8dd; }")
        self.fast_mode_btn.setToolTip(
            "How messages are loaded.\n"
            "🐢 Full (default): loads and renders ALL messages in the "
            "background (progress bar in the bottom bar) and only then "
            "reveals the chat — no more dynamic inserting.\n"
            "⚡ Fast: shows the newest messages right away and keeps "
            "loading the rest in the background.")
        self.fast_mode_btn.setCheckable(True)
        self.fast_mode_btn.setChecked(False)
        self.fast_mode_btn.toggled.connect(self._fast_mode_changed)
        h2.addWidget(self.fast_mode_btn)
        vb2.addWidget(self.header_chat)

        self.chat_view = ChatView()
        self.chat_view.link_clicked.connect(self._on_attachment_clicked)
        vb2.addWidget(self.chat_view, 1)
        split.addWidget(panel_right)

        # hint shown until an export is opened
        self._show_empty(
            "💬 No conversation selected.<br>"
            "Open a <b>.zip</b> export (📂 Open…) and choose a "
            "conversation on the left.")

        split.setSizes([320, 830])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        # ---- status bar ---------------------------------------------------
        # The message-loading progress bar lives HERE (permanent widget),
        # not in the chat header: it is visible, determinate, and shows the
        # real "loaded/total messages" count the whole time a conversation
        # is being loaded (first page + the older pages in the background).
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("No export open.")
        self.loading_bar = QProgressBar()
        self.loading_bar.setTextVisible(True)
        self.loading_bar.setFormat("Loading… %p%")
        self.loading_bar.setFixedWidth(220)
        self.loading_bar.setRange(1, 1)
        self.status.addPermanentWidget(self.loading_bar)
        self.loading_bar.hide()

    def _filtrar(self, term):
        """Filters the CONVERSATION list by name/jid (kept for the
        conversation list; the message search below is separate)."""
        if not self.nucleo:
            return
        f = (term or "").strip().lower()
        self.conversations_combo.clear()
        self._filtros = []
        for c in self.nucleo.conversas:
            if f and f not in c["nome"].lower() and f not in c["jid"].lower():
                continue
            self._filtros.append(c)
            # the row is drawn by the delegate (avatar + name + summary);
            # the plain text is a fallback for accessibility/keyboard nav
            item = QListWidgetItem(c["nome"])
            item.setData(Qt.UserRole, c)
            self.conversations_combo.addItem(item)

    # ---------------------------------------------------- message search ----
    def _conversation_name_of(self, jid):
        """Conversation display name for a jid (from the loaded list)."""
        for c in self.nucleo.conversas:
            if c["jid"] == jid:
                return c["nome"]
        return str(jid).split("@")[0]

    def _search_snippet(self, row, term, larg=70):
        """A short snippet around the first occurrence of `term` in
        `row` (falls back to the line's beginning when no match)."""
        try:
            if not row:
                return ""
            t = (term or "").strip().lower()
            pos = row.lower().find(t) if t else 0
            if pos < 0:
                pos = 0
            ini = max(0, pos - 15)
            fim = min(len(row), ini + larg)
            excerpt = row[ini:fim].strip()
            if ini > 0:
                excerpt = "…" + excerpt
            if fim < len(row):
                excerpt = excerpt + "…"
            return excerpt or row[:larg]
        except Exception:
            return (row or "")[:larg]

    def _search_typed(self, text):
        """A keystroke: debounce the search so it only runs when the user
        pauses (or presses Enter, which calls _search directly)."""
        self._search_debounce.start()

    def _stop_search(self):
        """Stops the running search worker (conversation switch / new
        search). The old conversation's bubbles are gone, so no stale
        results can be applied."""
        w = self._search_worker
        if w is not None and w.isRunning():
            w.stop()
            w.wait(800)
        self._search_worker = None

    def _stop_search_global(self):
        """Stops a global search worker (used when switching to the
        open-chat scope mid-search)."""
        self._stop_search()

    def _clear_search_ui(self, keep_term=False):
        """Resets the search UI (list, labels, highlights)."""
        self.chat_view.clear_highlight()
        self.search_combo.clear()
        self._search_chat_mids = []
        self._search_chat_index = -1
        self._search_cursor = None
        self._search_end = False
        self._search_total = 0
        self._search_loaded = 0
        self._search_current_jid = None
        if not keep_term:
            self._search_term = None
        self.search_result_label.setText("")
        # show the conversation list again
        self.search_combo.hide()
        self.conversations_combo.show()

    def _toggle_search_panel(self, show_search):
        """Swaps the left panel: conversation list <-> search results."""
        if show_search:
            self.search_combo.show()
            self.conversations_combo.hide()
        else:
            self.search_combo.hide()
            self.conversations_combo.show()

    def _search(self):
        """Runs the message search (Enter or debounce)."""
        term = (self.search_edit.text() or "").strip()
        if not term:
            # empty field: cancel the search and restore the list
            self._stop_search()
            self._clear_search_ui()
            return
        if not self.nucleo:
            self.status.showMessage("Open a .zip export first.")
            return
        if self._search_term == term and self._search_scope == (
                "global" if self.scope_btn.isChecked() else "chat"):
            # same search already running/finished: ignore
            return
        self._search_term = term
        self._search_scope = (
            "global" if self.scope_btn.isChecked() else "chat")
        self._stop_search()
        self._clear_search_ui(keep_term=True)
        if self._search_scope == "chat":
            self._start_search_chat(term)
        else:
            self._start_search_global(term)

    def _search_scope_changed(self, enabled):
        """Scope toggle: chat <-> all chats. Re-runs the active search."""
        if self.search_edit.hasFocus():
            # the user is typing; let the debounce/Enter handle it
            return
        if not self._search_term:
            return
        self._search()

    def _start_search_chat(self, term):
        """Searches the OPEN conversation's full history (WorkerSearchConversation)."""
        c = self._current_conversation
        if not c:
            self.search_result_label.setText("Open a chat first (or use “All chats”).")
            self.status.showMessage(
                "Open-chat search needs an open conversation — "
                "or toggle “All chats”.")
            return
        self._search_current_jid = c["jid"]
        self.search_result_label.setText("Searching…")
        w = WorkerSearchConversation(self.nucleo, c["jid"], term, parent=self)
        w.ready.connect(self._search_chat_ready)
        w.error.connect(self._search_error)
        self._search_worker = w
        w.start()

    def _search_chat_ready(self, lines):
        """WorkerSearchConversation finished: mark the matches in the chat."""
        jid = self._search_current_jid
        if self._current_conversation and self._current_conversation["jid"] != jid:
            # the user already switched conversation: discard the results
            self._search_chat_mids = []
            self.search_result_label.setText("")
            return
        self._search_current_jid = jid
        self._search_chat_mids = [r[0] for r in lines]
        n = self.chat_view.mark_mid(self._search_chat_mids)
        total = len(self._search_chat_mids)
        self.search_result_label.setText(
            f"{n} match{'es' if total != 1 else ''} in chat "
            f"(of {total} loaded)")
        self.status.showMessage(
            f"{total} matches in “{self._conversation_name_of(jid)}”")
        if total:
            self._search_chat_index = 0
            self._search_focus_current()
        else:
            self.status.showMessage(
                f'No “{self._search_term}” in '
                f"“{self._conversation_name_of(jid)}”")

    def _search_focus_current(self):
        """Scrolls the open chat to the current match (index)."""
        if not self._search_chat_mids or self._search_chat_index < 0:
            return
        mid = self._search_chat_mids[self._search_chat_index]
        self.chat_view.scroll_to_mid(mid)

    def _search_navigate(self, delta):
        """Walks between the open-chat matches (Up/Down buttons/keys)."""
        # only active when an open-chat search has results (otherwise the
        # Up/Down keys keep their normal behavior)
        if self._search_scope != "chat" or not self._search_chat_mids:
            return
        n = len(self._search_chat_mids)
        self._search_chat_index = (
            (self._search_chat_index if self._search_chat_index >= 0 else 0)
            + delta) % n
        self._search_focus_current()

    def _mark_search_new(self, older_msgs):
        """Marks the search matches among an OLDER page (WorkerOlder)
        that just arrived, as it is rendered at the top. `older_msgs` are
        the 16-field message tuples of that page (chronological)."""
        if not self._search_chat_mids:
            return
        term = (self._search_term or "").strip().lower()
        if not term:
            return
        novas = []
        for m in older_msgs:
            text = (m[2] or "")
            caption = (m[12] or "")
            if term in text.lower() or term in caption.lower():
                novas.append(m[0])
        if novas:
            self._search_chat_mids = list(dict.fromkeys(novas + self._search_chat_mids))
            self.chat_view.mark_mid(novas)
            total = len(self._search_chat_mids)
            marked_count = len(self.chat_view._marked_mids)
            self.search_result_label.setText(
                f"{marked_count} match{'es' if total != 1 else ''} marked "
                f"(of {total} in chat)")

    def _start_search_global(self, term):
        """Searches ALL conversations (WorkerSearchGlobal, paged)."""
        self._toggle_search_panel(True)
        self.search_result_label.setText("Searching…")
        w = WorkerSearchGlobal(self.nucleo, term, parent=self)
        w.total.connect(self._search_global_total)
        w.batch.connect(self._search_global_batch)
        w.fim.connect(self._search_global_end)
        w.error.connect(self._search_error)
        self._search_worker = w
        w.start()

    def _search_global_total(self, total):
        self._search_total = total
        if self._search_loaded == 0:
            self.search_result_label.setText(f"Searching… {total} found")

    def _search_global_batch(self, lines):
        """One page of global matches (newest first)."""
        if self._search_worker is None:
            return
        self._search_loaded += len(lines)
        for (mid, jid, text, sender_name, ts, type, caption) in lines:
            # the snippet prefers the text; a media match falls back to
            # the caption
            row = (text or "") or (caption or "")
            if not row and type not in (0, 29, 1027):
                row = "[media]"
            name = self._conversation_name_of(jid)
            data = _short_date_ts(ts) or ""
            excerpt = self._search_snippet(row, self._search_term)
            label = f"{name}  ·  {data}\n{excerpt}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, (mid, jid, ts))
            # insert at the TOP: the worker emits newest-first, so the list
            # stays newest-first (top) as pages arrive
            self.search_combo.insertItem(0, item)
        self.search_result_label.setText(
            f"{self._search_loaded} of {self._search_total} "
            "loaded — click to open")

    def _search_global_end(self):
        if self._search_worker is None:
            return
        if self._search_loaded == 0:
            self.search_result_label.setText(f'No “{self._search_term}” found')
            self.status.showMessage(f'No “{self._search_term}” in the export')
        else:
            self.search_result_label.setText(
                f"{self._search_total} match"
                f"{'es' if self._search_total != 1 else ''} — click to open")
            self.status.showMessage(
                f"{self._search_total} matches for “{self._search_term}”")

    def _search_error(self, msg):
        self._clear_search_ui()
        self.status.showMessage("Search failed: " + msg)

    def _on_search_result_clicked(self, item):
        """A global-search result was clicked: open the conversation and
        scroll to the message."""
        data = item.data(Qt.UserRole)
        if not data:
            return
        mid, jid, ts = data
        # find the conversation in the list
        c = None
        for conv in self.nucleo.conversas:
            if conv["jid"] == jid:
                c = conv
                break
        if c is None:
            self.status.showMessage("Conversation not found.")
            return
        # keep the search term + results (the left panel stays on the
        # results); open the conversation (the search worker is stopped —
        # its pages are already loaded, the list keeps them)
        self._stop_search()
        self._pending_selection = c
        self._start_conversation()
        # scroll to the message as soon as it is rendered:
        # - if it is in the recent page (most likely), it renders soon;
        # - if it is older, the top-insert run will render it eventually.
        # We retry with a timer until the bubble exists (max ~3 s).
        self._search_target_mid = mid
        self._search_attempts = 0
        self._schedule_search_mid()

    def _schedule_search_mid(self):
        """Retries (every 150 ms, max 20 times ≈ 3 s) until the target
        bubble is rendered, then scrolls to it."""
        mid = getattr(self, "_search_target_mid", None)
        if mid is None:
            return
        if self.chat_view.scroll_to_mid(mid):
            self._search_target_mid = None
            # highlight it briefly (yellow)
            self.chat_view.mark_mid([mid])
            QTimer.singleShot(2500, lambda: self.chat_view.clear_highlight())
            return
        self._search_attempts = getattr(self, "_search_attempts", 0) + 1
        if self._search_attempts > 20:
            self._search_target_mid = None
            self.status.showMessage(
                "The message is not in the loaded part of the chat "
                "(scroll up / it loads as you go).")
            return
        QTimer.singleShot(150, self._schedule_search_mid)

    # ------------------------------------------------------------ opening --
    def open(self):
        zip_path, _ = QFileDialog.getOpenFileName(
            self, "Choose the exported .zip", "",
            "Zip file (*.zip);;All (*.*)")
        if not zip_path:
            return
        base, _ = os.path.splitext(zip_path)
        txt_path = base + ".md5.txt"
        if not os.path.exists(txt_path):
            txt_path, _ = QFileDialog.getOpenFileName(
                self, "Choose the .md5.txt", "", "MD5 (*.txt);;All (*.*)")
        if not txt_path:
            return
        self.status.showMessage("Opening…")
        self.hash_bar.setValue(0)
        self._worker_open = WorkerOpen(zip_path, txt_path, self)
        self._worker_open.md5_progress.connect(self._set_hash)
        self._worker_open.status.connect(self.status.showMessage)
        self._worker_open.ready.connect(self._ready)
        self._worker_open.error.connect(self._open_error)
        self._worker_open.start()

    def _set_hash(self, read, total):
        self.hash_bar.setMaximum(max(total or 1, 1))
        self.hash_bar.setValue(read)

    def _open_error(self, msg):
        QMessageBox.critical(self, APP_TITLE,
                             "Failed to open the export:\n\n" + msg)
        self.hash_bar.hide()
        self.status.showMessage("Error opening.")

    def _ready(self, nucleo, zip_path):
        self.nucleo = nucleo
        self.zip_path = zip_path
        # a new export: drop the previous one's cached loads
        self._conversations_cache.clear()
        self._current_conversation = None
        self._current_offset = 0
        # account owner: the core detected it from structural signals in
        # the data (auto-DM > volume > group presence) — the user can
        # correct it by clicking "👤 Owner".
        self._update_owner_ui()
        # the zip finished loading: hide the MD5/hash progress bar
        self.hash_bar.setValue(0)
        self.hash_bar.hide()
        self.status.showMessage(
            f"{len(nucleo.conversas)} conversations · "
            f"{os.path.basename(zip_path)}")
        self._filtrar("")
        self.lbl_titulo.setText("Select a conversation on the left.")
        self.avatar_header_label.clear()
        self.avatar_header_label.setText("💬")
        self.lbl_dates.setText("")
        self.lbl_members.setText("")
        # attachments in the background
        self._worker_attachments = WorkerAttachments(nucleo, self)
        self._worker_attachments.progress.connect(self._set_attachments)
        self._worker_attachments.start()

    def _set_attachments(self, extracted, total):
        self.status.showMessage(
            f"Extracting attachments… {extracted}/{total}")

    # ------------------------------------------------------------- owner --
    def _update_owner_ui(self):
        """Syncs the owner state (in the core) with the UI button."""
        if not self.nucleo:
            self.owner_name = None
            self.owner_btn.setText("👤 Owner: —")
            return
        dono_jid = getattr(self.nucleo, "dono_jid", None)
        dono_nome = getattr(self.nucleo, "dono_nome", None)
        confirmado = getattr(self.nucleo, "owner_confirmed", False)
        method = getattr(self.nucleo, "owner_method", None)
        self.owner_jid = dono_jid
        self.owner_name = (dono_nome or (str(dono_jid).split("@")[0]
                                          if dono_jid else None))
        if not dono_jid:
            self.owner_btn.setText("👤 Owner: —")
            return
        if confirmado:
            suffix = ""
        elif method:
            suffix = f" ({method})"
        else:
            suffix = " (guess)"
        text = f"👤 {self.owner_name}{suffix}"
        self.owner_btn.setText(text)
        self.owner_btn.setToolTip(
            f"Account owner: {self.owner_name}\n\n"
            f"How it was detected: {method or 'manual'}\n"
            "(green bubble in DMs and groups)\n"
            "Click to set the owner from the selected conversation.")

    def _set_owner(self):
        """Sets the account owner from the selected conversation.

        The export does not mark the owner, so the user indicates it: open
        the conversation with the owner and click here. Works for any export."""
        if not self.nucleo:
            return
        c = self._current_conversation
        if not c:
            QMessageBox.information(
                self, "Set owner",
                "First select the conversation WITH the account owner,\n"
                "then click “👤 Owner”.")
            return
        resp = QMessageBox.question(
            self, "Set the account owner",
            f"Mark “{c['nome']}” as the account owner?\n\n"
            "Their messages will get the green bubble and the name on top.\n"
            "(The export does not indicate the owner automatically — this is\n"
            "the moment to confirm who the owner is.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if resp != QMessageBox.Yes:
            return
        self.nucleo.definir_dono(c["jid"], c["nome"])
        self._update_owner_ui()
        self.status.showMessage(f"Owner set: {c['nome']}")
        # re-renders the current conversation with the new owner (if already loaded)
        if self._current_conversation and self._current_total:
            self._redraw_conversation()

    def _conversation_progress(self, read, total):
        """WorkerConversation pagination progress (loading the messages)."""
        # stale signal (the worker finished, or the conversation was already
        # switched): ignore it
        if not (self._worker_conversation and self._worker_conversation.isRunning()):
            return
        # determinate progress: the total is known, switch out of pulsing mode
        self._loading_bar_progress(max(total or 1, 1), read)
        # the "load all first" overlay shows the same numbers
        if self._loading_screen_label is not None:
            self._show_loading_progress(read, total)
        self.status.showMessage(
            f"Loading… {read}/{total} messages")

    def _redraw_conversation(self):
        """Re-renders the current conversation (after changing the owner)."""
        if not self._current_conversation or not self._msgs_loaded:
            return
        self.chat_view.clear()
        self._last_day = None
        self._render_ocupado = True
        self._pending_selection = None  # no queued selection during re-render
        self._pending_c = self._current_conversation
        # chronological order (oldest first): the scene builds from top to
        # bottom, so the recent messages are visible at the bottom
        self._pending_bubbles = list(self._msgs_loaded)
        self._current_offset = len(self._msgs_loaded)
        self._current_total = self._current_total or len(self._msgs_loaded)
        self._next_batch()

    # ------------------------------------------------------------ conversation --
    def _on_select(self, item):
        if not self._filtros:
            return
        idx = self.conversations_combo.row(item)
        if idx < 0 or idx >= len(self._filtros):
            return
        c = self._filtros[idx]
        # always switch: the previous conversation's load/render is
        # interrupted and finished work is kept in _conversations_cache, so the
        # user can navigate freely while the background loading continues
        # for the other chats
        self._pending_selection = c
        self._start_conversation()

    def _stop_worker_conversation(self):
        """Stops the message-loading worker (if any) — used on conversation
        switch so the old load is interrupted immediately."""
        w = getattr(self, "_worker_conversation", None)
        if w is not None and w.isRunning():
            w.stop()
            w.wait(800)
        self._worker_conversation = None

    def _start_conversation(self):
        """Opens a conversation.

        Default ("load all first"): ALL messages are loaded and rendered in
        the background while the chat area shows a "Loading…" overlay and
        the status-bar progress bar advances; only when everything is done
        does the chat reveal itself (already scrolled to the newest
        messages, ready to scroll up).

        "Quick mode" (toggle in the header): the newest `_PRIMEIRAS` are
        shown immediately and the older ones page in in the background
        (WorkerOlder).

        Switching conversation mid-load interrupts the previous load
        (its finished work is kept in `_conversations_cache`, so re-opening it
        later is instant)."""
        c = self._pending_selection
        if not c:
            return
        self._pending_selection = None
        # ---- interrupt the previous conversation ----------------------
        self._stop_worker_conversation()
        self._stop_worker_older()
        self._pending_bubbles = []   # the old render queue is stale
        self._older_bubbles = []       # the old top-insert queue is stale
        self._older_rendering = False
        self._current_conversation = c
        self._current_offset = 0
        self._current_total = 0
        self._ha_mais = False
        self._last_day = None
        self._render_ocupado = True
        self._ts_min = None
        self._ts_max = None
        self._top_last_day = None
        # a conversation switch invalidates the open-chat search (its
        # bubbles are being cleared): cancel the worker, drop the match
        # list, and clear the pending scroll-to-target
        self._stop_search()
        self._search_chat_mids = []
        self._search_chat_index = -1
        self._search_current_jid = None
        self._search_target_mid = None
        self.chat_view.clear_highlight()
        if self._search_term and self._search_scope == "chat":
            self.search_result_label.setText("")
        # release any pin left by the previous conversation (its render
        # timers are already dead: the pending/ant queues were cleared)
        self.chat_view.pin_disable()
        self.lbl_titulo.setText(
            ("👥 " if c["grupo"] else "💬 ") + c["nome"])
        self._update_header_avatar(c)
        self._update_header_members(c)
        self.lbl_dates.setText("📅 …")  # filled from the loaded messages
        self.chat_view.clear()
        # ---- already loaded in the background? open it instantly ------
        entry = self._conversations_cache.get(c["jid"])
        if entry and entry["c"] is c:
            self._open_from_cache(entry)
            return
        # ---- otherwise load (interruptible) ----------------------------
        if not self._fast_mode:
            # load-all-first: the chat reveals only when everything is done
            self._open_loading_screen()
            self._loading_bar_on(0, 0)
            self.status.showMessage(
                f"Loading “{c['nome']}” — all messages…")
            limit = 0
        else:
            # quick mode: newest page first, older ones in the background
            self._loading_bar_on(0, 0)
            self.status.showMessage(f"Loading “{c['nome']}”…")
            limit = self._PRIMEIRAS
        w = WorkerConversation(self.nucleo, c["jid"], limit, parent=self)
        w.ready.connect(self._conversation_loaded)
        w.progress.connect(self._conversation_progress)
        w.error.connect(self._chat_error)
        self._worker_conversation = w
        w.start()

    def _open_from_cache(self, entry):
        """Opens a conversation whose full load already finished (in the
        background): renders it instantly, no worker, no overlay."""
        msgs = entry["msgs"]
        self._current_total = entry["total"]
        self._current_offset = len(msgs)
        self._ha_mais = False
        self._msgs_loaded = list(msgs)
        self._remove_loading_screen()
        self._update_dates(msgs)
        self._pending_c = self._current_conversation
        self._pending_bubbles = list(msgs)
        self.chat_view.set_rigid_scroll(True)
        self.chat_view._pin_cold = True
        self.chat_view.pin_enable(0)
        self._loading_bar_off()
        self._next_batch()
        if self._current_offset > 0:
            self.status.showMessage(
                f"{self._current_offset}/{self._current_total} messages · "
                f"{os.path.basename(self.zip_path or '')}")

    def _stop_worker_older(self):
        """Stops the older-messages worker (if any) — used on conversation
        switch so a stale page is not inserted into the new chat."""
        w = getattr(self, "_worker_older", None)
        if w is not None and w.isRunning():
            w.stop()
            w.wait(800)
        self._worker_older = None

    def _loading_bar_on(self, maximum, value):
        """Show the progress bar. `maximum=0` means "total unknown yet"
        (first page loading): the bar pulses (indeterminate)."""
        if maximum and maximum > 0:
            self.loading_bar.setRange(1, max(maximum, 1))
            self.loading_bar.setValue(value)
        else:
            # indeterminate: range (0,0) makes Qt pulse the bar
            self.loading_bar.setRange(0, 0)
        if not self.loading_bar.isVisible():
            self.loading_bar.show()

    def _loading_bar_progress(self, read_count, total):
        """Determinate progress: `read_count` messages loaded of `total`."""
        self.loading_bar.setRange(1, max(total or 1, 1))
        self.loading_bar.setValue(max(0, min(read_count or 0, total or 1)))

    def _loading_bar_off(self):
        self.loading_bar.setRange(1, 1)
        self.loading_bar.setValue(0)
        self.loading_bar.hide()

    # ------------------------------------------- load-all-first mode ------
    def _open_loading_screen(self):
        """Shows the "Loading…" overlay in the (empty) chat area, used by the
        default "load all first" mode: the chat reveals only when everything
        is loaded/rendered. The status-bar progress bar advances meanwhile."""
        self._remove_loading_screen()
        self.chat_view.clear()
        lbl = QLabel('<div style="color:#667781;font-size:14px;'
                     'text-align:center;padding:40px 20px;">'
                     '⏳ Loading all messages…<br>'
                     '<span style="font-size:11px;">'
                     '(see the progress in the bottom bar)</span></div>')
        lbl.setTextFormat(Qt.RichText)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        larg = self.chat_view._largura_util()
        lbl.setFixedWidth(int(max(260, larg * 0.8)))
        self.chat_view._add_widget(lbl, False)
        self._loading_screen_label = lbl

    def _remove_loading_screen(self):
        """Removes the "Loading…" overlay (when present)."""
        lbl = self._loading_screen_label
        self._loading_screen_label = None
        if lbl is None:
            return
        try:
            if lbl.parent():
                self.chat_view._layout.removeWidget(lbl)
            lbl.setParent(None)
            lbl.deleteLater()
        except RuntimeError:
            pass

    def _show_loading_progress(self, read_count, total):
        """Updates the overlay's text with the current progress."""
        lbl = self._loading_screen_label
        if lbl is None:
            return
        if total and total > 0:
            txt = (f'⏳ Loading… {read_count:,} of {total:,} messages'
                   f'<br><span style="font-size:11px;">'
                   f'(see the progress in the bottom bar)</span>')
        else:
            txt = ('⏳ Loading all messages…'
                   '<br><span style="font-size:11px;">'
                   '(see the progress in the bottom bar)</span>')
        lbl.setText(
            f'<div style="color:#667781;font-size:14px;'
            f'text-align:center;padding:40px 20px;">{txt}</div>')
        lbl.adjustSize()

    def _fast_mode_changed(self, enabled):
        """Header toggle: switch between "load all first" (default) and
        "quick" (show the recent page immediately). Re-opens the current
        conversation in the new mode so the change is applied at once."""
        self._fast_mode = bool(enabled)
        self.fast_mode_btn.setText(
            "⚡ Fast" if not enabled else "🐢 Full")
        if self._current_conversation is not None and \
                self._current_offset > 0:
            # re-open in the new mode (the current render/loads are cancelled)
            self._pending_selection = self._current_conversation
            self._start_conversation()

    def _force_bottom(self):
        """Guarantees the chat viewport ends on the newest messages.

        Qt applies pending layout updates at the start of the next event-loop
        pass, so an immediate `sb.setValue(sb.maximum())` can be based on a
        STALE maximum (the last batch has not been laid out yet) and the view
        opens a few messages too high — the "not on the recent messages" bug.
        The jump is rigid (no smooth-scroll coalescing), the layout is forced
        to settle (ensurePolished), and a 250 ms retry catches the case where
        images/thumbnails reflow the content a moment later. Only called
        when no pin is alive (the pin owns the viewport otherwise)."""
        def _skip():
            if self._current_conversation is None:
                return
            cv = self.chat_view
            cv.set_rigid_scroll(True)
            cv.ensurePolished()
            sb = cv.verticalScrollBar()
            sb.setValue(sb.maximum())

        QTimer.singleShot(0, _skip)
        QTimer.singleShot(250, _skip)

    def _conversation_loaded(self, res):
        """QThread signal: the messages are ready to render.

        In "load all first" mode this is the FULL conversation (the overlay
        is removed and the chat reveals with everything rendered). In "fast"
        mode it is the newest page and the older ones page in afterwards."""
        msgs = res["msgs"]
        self._current_total = res["total"]
        self._current_offset = len(msgs)
        self._ha_mais = not res["fim"]
        self._msgs_loaded = list(msgs)
        # remember the full load so re-opening this conversation (after
        # switching away and back) is instant — the background loading is
        # "kept" for other conversations even while the user navigates
        if res["fim"]:
            self._conversations_cache[self._current_conversation["jid"]] = {
                "c": self._current_conversation,
                "total": self._current_total,
                "msgs": msgs,
                "fim": True,
            }
        # the "load all first" overlay is replaced by the real chat now
        self._remove_loading_screen()
        # header dates from what we have so far (the oldest of the recent
        # page is not the true oldest of the chat — WorkerOlder will
        # push _ts_min back down as older pages arrive)
        self._update_dates(msgs)
        self._pending_c = self._current_conversation
        # chronological order (oldest first): the scene builds from top to
        # bottom, so the recent messages are visible at the bottom
        self._pending_bubbles = list(msgs)
        # rigid scroll: the single final scroll_to_bottom() is an instant jump
        # (no smooth animation) — fast opening of long chats
        self.chat_view.set_rigid_scroll(True)
        # freeze the viewport (FROZEN mode) for the whole initial render:
        # the newest messages stay exactly where they are while the rest
        # appear, and one final scroll brings the view to the bottom
        self.chat_view._pin_cold = True
        self.chat_view.pin_enable(0)
        self._next_batch()
        # start pulling the older messages in the background (if any)
        if not res["fim"]:
            self._start_older()
        # an open-chat search is active for THIS conversation: re-run it
        # (the previous run was cancelled on the switch) and mark the
        # matches as the render progresses
        if (self._search_term and self._search_scope == "chat"
                and self._current_conversation
                and self._search_current_jid is None):
            self._start_search_chat(self._search_term)

    # ------------------------------------------- older messages (top) ----
    def _start_older(self):
        """Pages in the OLDER messages (the most recent `_PRIMEIRAS` are
        already loaded/rendered). Each page arrives chronological and is
        inserted at the TOP of the chat. The cursor starts at the OLDEST
        already-loaded row (msgs[-1] of the recent block)."""
        if not self._current_conversation or not self.nucleo:
            return
        if not self._msgs_loaded:
            return
        # oldest already-loaded row = (TimestampLong, MessageID)
        m_older = self._msgs_loaded[0]  # chronological: index 0 is oldest
        cursor = (m_older[5], m_older[0])
        self._worker_older = WorkerOlder(
            self.nucleo, self._current_conversation["jid"],
            self._current_total, cursor, parent=self)
        self._worker_older.batch.connect(self._batch_older)
        self._worker_older.fim.connect(self._end_older)
        self._worker_older.error.connect(self._chat_error)
        self._worker_older.start()

    def _batch_older(self, msgs):
        """One older page (chronological, oldest first). Queue it for
        top-insertion and remember it for attachment resolution. Also push
        the header's oldest date back."""
        self._msgs_loaded = list(msgs) + self._msgs_loaded
        self._current_offset = len(self._msgs_loaded)
        if msgs:
            t0 = msgs[0][5]
            if self._ts_min is None or t0 < self._ts_min:
                self._ts_min = t0
                self._update_top_dates()
        self._older_bubbles = list(msgs) + self._older_bubbles
        # an open-chat search is active: mark the matches that just arrived
        # (the older pages are being rendered at the top)
        if self._search_term and self._search_scope == "chat" and \
                self._search_current_jid == self._current_conversation["jid"]:
            self._mark_search_new(msgs)
        if not self._older_rendering:
            self._render_top()

    def _end_older(self):
        """All older messages have been loaded: finalize (dates, status)."""
        self._update_top_dates()
        # the older-messages pipeline is done: hide the progress bar now
        # (it stayed visible and determinate the whole time)
        self._loading_bar_off()
        # final safety net: make sure the view rests on the newest messages
        # (top insertions pin the viewport, but a stale layout can still
        # leave it a little short of the true bottom)
        self._force_bottom()
        # status: the chat is fully loaded now
        if self._current_offset > 0:
            self.status.showMessage(
                f"{self._current_offset}/{self._current_total} messages · "
                f"{os.path.basename(self.zip_path or '')}")

    def _update_top_dates(self):
        """Refresh the header date range using _ts_min/_ts_max (which now
        span the whole loaded chat, not just the recent page)."""
        try:
            d0 = _short_date_ts(self._ts_min) if self._ts_min else None
            d1 = _short_date_ts(self._ts_max) if self._ts_max else None
            if d0 and d1 and d0 != d1:
                self.lbl_dates.setText(f"📅 {d0} → {d1}")
            elif d1:
                self.lbl_dates.setText(f"📅 {d1}")
            elif d0:
                self.lbl_dates.setText(f"📅 {d0}")
            else:
                self.lbl_dates.setText("")
        except Exception:
            self.lbl_dates.setText("")

    def _render_top(self):
        """Inserts the queued older messages at the TOP of the chat, in
        batches of `_BATCH_RENDER`, oldest-first (so each batch's newest
        item sits right above what was already visible).

        The viewport is PINNED for the whole run: it never moves while the
        older pages render (it stays on the newest messages). When the queue
        empties the pin is released, leaving the chat exactly where it was.
        """
        if not self._older_bubbles:
            self._older_rendering = False
            # nothing more to insert: hand the viewport back to the user
            # (stays at the bottom if it was there; a user who scrolled up
            # keeps reading where they were)
            self.chat_view.pin_disable()
            return
        # the user broke the pin (wheel) and is reading the middle of the
        # chat: re-arm it in FROZEN mode at the user's current position, so
        # the screen does not slide back while the next page renders
        if not self.chat_view.pin_active():
            self.chat_view._pin_cold = True
            self.chat_view.pin_enable()
        self._older_rendering = True
        c = self._pending_c
        fim = min(len(self._older_bubbles), self._BATCH_RENDER)
        # take the NEWEST part of the queue first (it goes closest to the
        # already-visible content); the queue is ordered oldest-first, so
        # the last `fim` items are the newest of the batch
        batch = self._older_bubbles[-fim:]
        self._older_bubbles = self._older_bubbles[:-fim]
        # lock the viewport BEFORE the inserts (anchor + pin in one call)
        self.chat_view.prepare_top_anchor()
        for msg in batch:
            self._draw_msg_top(c, msg, msg[0])
        # process the layout (so the new widgets have their final geometry)
        self.chat_view.ensurePolished()
        # re-anchor: keeps the newest at the bottom (pin stays alive) or
        # restores the reading position (pin released)
        self.chat_view.restore_top_anchor()
        self._loading_bar_progress(
            self._current_total or 1, self._current_offset)
        QTimer.singleShot(0, self._render_top)

    def _draw_msg_top(self, c, msg, mid=None):
        """Renders one message at the TOP (oldest side). Mirrors
        _draw_msg but uses the top-insertion layout helpers."""
        (mid, from_me, text, _bin, file_name, ts, sender_name, mime, type,
         size, duration, media_name, caption, lfu, _react, reactions) = msg
        # date separator: show it when this message starts a new day AND the
        # day is different from the oldest already-rendered day. Because we
        # insert oldest-first, track the last day we've placed at the top.
        day = hora(ts)
        if day:
            key = day[:10]
            if key != self._top_last_day:
                self._top_last_day = key
                self.chat_view.insert_separator_top(separator_label(day))
        html_content = self._html_mensagem(c, msg, from_me)
        is_owner = self._is_owner(c, from_me, sender_name)
        cor = MY_COLOR if (from_me or is_owner) else OTHER_COLOR
        # alignment: owner's message → right (even in a group, where
        # from_me is False); others → left
        align_right = from_me or is_owner
        w = self.chat_view.insert_bubble_top(
            html_content, align_right, cor)
        w._mid = mid if mid is not None else mid
        return w

    # ------------------------------------------------------ header dates ---
    def _update_dates(self, msgs):
        """Fills the header date label with the oldest → newest message date.

        `msgs` is chronological (oldest first); index 0 is the oldest, the
        last is the newest. Shown as '📅 dd/mm/yyyy → dd/mm/yyyy'. Also
        records _ts_min/_ts_max (used later by _update_top_dates as the
        older pages widen the range)."""
        try:
            if not msgs:
                self.lbl_dates.setText("")
                return
            t0 = msgs[0][5]
            t1 = msgs[-1][5]
            self._ts_min = t0
            self._ts_max = t1
            d0 = _short_date_ts(t0)
            d1 = _short_date_ts(t1)
            if d0 and d1 and d0 != d1:
                self.lbl_dates.setText(f"📅 {d0} → {d1}")
            elif d0:
                self.lbl_dates.setText(f"📅 {d0}")
            else:
                self.lbl_dates.setText("")
        except Exception:
            self.lbl_dates.setText("")

    # ------------------------------------------------------- avatar --------
    def _update_header_avatar(self, c):
        """Sets the header avatar for conversation `c` (real photo when the
        export has one, colored initial otherwise)."""
        try:
            pm = _avatar_pixmap(self.nucleo, c["jid"], c["nome"], 36)
            if not pm.isNull():
                self.avatar_header_label.setPixmap(
                    pm.scaled(36, 36, Qt.KeepAspectRatio,
                              Qt.SmoothTransformation))
            else:
                self.avatar_header_label.setText("💬")
        except Exception:
            self.avatar_header_label.setText("💬")

    def _update_header_members(self, c):
        """Header member line: '👥 N members · click for the list' for a
        group (the full list lives in the details window), hidden otherwise."""
        if c["grupo"]:
            self.lbl_members.setText("👥 Members — click for the full list")
        else:
            self.lbl_members.setText("")

    # ------------------------------------------------------- details -------
    def _on_title_clicked(self, ev):
        """Clicking the chat title opens the details window."""
        if ev.button() == Qt.LeftButton and self._current_conversation:
            self._open_details()

    def _open_details(self):
        """Opens (or focuses) the details window for the current conversation."""
        if not self.nucleo or not self._current_conversation:
            return
        c = self._current_conversation
        if getattr(self, "_details_window", None) is not None:
            try:
                if self._details_window.jid == c["jid"] and \
                        self._details_window.isVisible():
                    self._details_window.raise_()
                    self._details_window.activateWindow()
                    return
            except Exception:
                pass
        self._details_window = DetailsWindow(self.nucleo, c, parent=self)
        self._details_window.show()

    def _show_empty(self, text):
        """Shows a centered hint when there is nothing to display
        (no export open / conversation without messages)."""
        self._remove_loading_screen()
        self.chat_view.clear()
        lbl = QLabel(f'<div style="color:#667781;font-size:13px;'
                     f'text-align:center;padding:24px;">{text}</div>')
        lbl.setTextFormat(Qt.RichText)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        larg = self.chat_view._largura_util()
        lbl.setFixedWidth(int(max(220, larg * 0.7)))
        self.chat_view._add_widget(lbl, False)

    def _next_batch(self):
        """Renders a batch of bubbles (via QTimer) and schedules the next one.
        While it runs the viewport is pinned to the BOTTOM, so the newest
        messages never slide out of view while the rest appear."""
        if not self._pending_bubbles:
            # batch finished: restore smooth scroll + final jump to bottom
            self.chat_view.set_rigid_scroll(False)
            self.chat_view.ensurePolished()
            self.chat_view.scroll_to_bottom()
            # the recent page is fully rendered: release the pin (the user
            # may now scroll to read)
            self.chat_view.pin_disable()
            # the pin's enforcement timer just stopped, so its last forced
            # position may be based on a stale content height: force a fresh
            # jump to the true bottom (the pin can no longer interfere).
            self._force_bottom()
            self._render_ocupado = False
            # the bar STAYS VISIBLE (determinate) while the older messages
            # keep loading in the background; it only hides when the whole
            # conversation is done (see _end_older) or on error/switch
            if self._ha_mais:
                self._loading_bar_progress(
                    self._current_total or 1, self._current_offset)
            else:
                self._loading_bar_off()
            if self._current_offset > 0:
                self.status.showMessage(
                    f"{self._current_offset}/{self._current_total} messages · "
                    f"{os.path.basename(self.zip_path or '')}")
            else:
                # conversation without messages: explain it in the chat area
                self.status.showMessage(
                    f'“{self._current_conversation["nome"]}” has no messages · '
                    f"{os.path.basename(self.zip_path or '')}")
                self._show_empty(
                    "💬 This conversation has no messages.")
            # a conversation picked while rendering? start it now
            if self._pending_selection is not None:
                self._start_conversation()
            # an open-chat search is active for THIS conversation: mark the
            # matches that just rendered (the recent page) now that their
            # bubbles exist
            if (self._search_term and self._search_scope == "chat"
                    and self._current_conversation
                    and self._search_current_jid == self._current_conversation["jid"]):
                self._mark_search_new(self._msgs_loaded)
            return
        c = self._pending_c
        fim = min(len(self._pending_bubbles), self._BATCH_RENDER)
        batch = self._pending_bubbles[:fim]
        self._pending_bubbles = self._pending_bubbles[fim:]
        for msg in batch:
            self._draw_msg(c, msg, msg[0])
        # progress: how many of the pending batch are already drawn
        self._loading_bar_progress(
            self._current_total or 1,
            self._current_offset - len(self._pending_bubbles))
        # the viewport is FROZEN for the whole run: the enforcement loop
        # (_pin_tick) holds it at the pinned value, so the newest
        # messages never slide away while the content grows (no per-batch
        # scroll calls, no fighting, no jumps)
        # schedules the next batch at the end of the event loop
        QTimer.singleShot(0, self._next_batch)

    def _draw_msg(self, c, msg, mid=None):
        (mid, from_me, text, _bin, file_name, ts, sender_name, mime, type,
         size, duration, media_name, caption, lfu, _react, reactions) = msg
        day = hora(ts)
        if day:
            key = day[:10]
            if key != self._last_day:
                self._last_day = key
                self.chat_view.add_day_separator(separator_label(day))
        html_content = self._html_mensagem(c, msg, from_me)
        # green bubble: messages from the account owner (DM or group)
        is_owner = self._is_owner(c, from_me, sender_name)
        cor = MY_COLOR if (from_me or is_owner) else OTHER_COLOR
        # alignment: owner's message → right (even in a group, where
        # from_me is False); others → left
        align_right = from_me or is_owner
        w = self.chat_view.add_bubble(html_content, align_right, cor)
        w._mid = mid if mid is not None else mid
        return w

    def _is_owner(self, c, from_me, sender_name):
        """True when the message is from the account owner (DM or group).

        DM: from_me (the owner is the user themselves).
        Group: resolves the sender's PushName to a jid (UserStatuses or
        the participant map) and compares it with dono_jid."""
        try:
            if not self.nucleo or not self.nucleo.dono_jid:
                return False
            if from_me:
                return True
            jid_rem = self.nucleo.enviador(c["jid"], sender_name, False)
            if jid_rem:
                return str(jid_rem).lower() == \
                    str(self.nucleo.dono_jid).lower()
        except Exception:
            pass
        return False

    # ----------------------------------------------------------- attachment links
    @staticmethod
    def _attachment_ref(lfu, media_name):
        """Compact attachment ref (no quotes/HTML) for the bubbles' <a>.
        Format: <hash16>|<type>|<name to save>"""
        key = hashlib.md5(str(lfu or "").encode("utf-8", "replace")).hexdigest()[:16]
        name = media_name or os.path.basename(str(lfu or "")) or "attachment"
        return f"{key}|{name}"

    def _html_mensagem(self, c, msg, from_me):
        (mid, from_me, text, _bin, file_name, ts, sender_name, mime, type,
         size, duration, media_name, caption, lfu, _react, reactions) = msg
        parts = []
        # sender: the account owner appears with their own name (green),
        # as in DMs — never "You"
        is_owner = self._is_owner(c, from_me, sender_name)
        if is_owner and (from_me or c["grupo"]):
            rem = self.owner_name or sender_name or "Account"
            parts.append(
                f'<div class="sender" '
                f'style="color:{SENDER_COLOR_MINE};">{_esc(rem)}</div>')
        elif c["grupo"]:
            rem = sender_name or (self.nucleo.nomes.get(str(c["jid"]).lower(), "")
                           if self.nucleo else "")
            if rem:
                parts.append(f'<div class="sender">{_esc(rem)}</div>')
        # content by type
        if type == 0 or type in (29, 1027):
            if text:
                parts.append(_esc(text).replace("\n", "<br>"))
        elif type == 1:
            parts.append(self._html_image(msg, from_me))
        elif type == 3:
            # video: thumbnail (if available) + name/duration + play links
            name = media_name or "video"
            dur = f" · {_duration(duration)}" if duration else ""
            parts.append(self._html_media(msg, "🎬", name,
                                           f'{_esc(dur)} · {_esc(tamanho_amigavel(size))}'))
        elif type == 2:
            # audio: no thumbnail, name/duration + play links
            name = media_name or "audio"
            dur = f" · {_duration(duration)}" if duration else ""
            parts.append(self._html_media(msg, "🎵", name,
                                           f'{_esc(dur)} · {_esc(tamanho_amigavel(size))}',
                                           sem_thumb=True))
        elif type == 4:
            import re
            name = media_name or "Contact"
            parts.append(f'<div class="media">👤 {_esc(name)}</div>')
            if text and "BEGIN:VCARD" in text:
                fn = re.search(r"FN:(.*)", text)
                tel = re.search(r"TEL[^:]*:(.*)", text)
                if fn:
                    parts.append(
                        f'<div class="vcard">{_esc(fn.group(1).strip())}</div>')
                if tel:
                    parts.append(
                        f'<div class="vcard">{_esc(tel.group(1).strip())}</div>')
        elif type in (8, 1006):
            name = media_name or file_name or "document"
            parts.append(
                f'<div class="media">📄 {_esc(name)} · '
                f'{_esc(tamanho_amigavel(size))}</div>')
            if not self._has_file(msg):
                parts.append(
                    '<div class="media" style="color:#c0392b;">'
                    '⚠ File does not exist in the export</div>')
            ref = self._attachment_ref(lfu, name)
            parts.append(
                f'<a href="zapix:open|{ref}">📂 Open</a> &nbsp; '
                f'<a href="zapix:save|{ref}">💾 Save…</a>')
        elif type == 9:
            parts.append(
                f'<div class="link">🔗 {_esc((text or "").strip())}</div>')
        elif type == 10:
            # sticker: thumbnail from the zip, inline preview fallback,
            # otherwise a missing-file warning
            html_fig = self._html_sticker(msg)
            if html_fig:
                parts.append(html_fig)
            else:
                parts.append('<div class="media">✨ [sticker]</div>')
                parts.append(
                    '<div class="media" style="color:#c0392b;">'
                    '⚠ File does not exist in the export</div>')
        else:
            if text:
                parts.append(_esc(text).replace("\n", "<br>"))
            else:
                parts.append(_esc(_resumo_tipo(type)))
        if caption and type != 9:
            parts.append(f'<div class="caption">{_esc(caption)}</div>')
        # footer: reactions + date + time + ✓✓
        reag_txt = ""
        if reactions:
            readers = {}
            order = []
            for jid_r, texto_r in reactions:
                k = texto_r or "✅"
                if k not in readers:
                    readers[k] = 0
                    order.append(k)
                readers[k] += 1
            reag_txt = "  ".join(
                f"{k} {readers[k]}" if readers[k] > 1 else k for k in order)
        date_text = _short_date(ts)
        time_text = self._short_time(ts)
        check = "  ✓✓" if from_me else ""
        when = (f'{date_text} {time_text}' if date_text and time_text
                  else (time_text or date_text))
        parts.append(
            f'<div class="footer">{_esc(reag_txt)} '
            f'<span>{_esc(when)}{_esc(check)}</span></div>')
        html_final = "".join(parts)
        return html_final

    @staticmethod
    def _short_time(data_ms):
        """HH:MM with unit detection (s/ms/us/ns), same as the core."""
        from ZAPiXDeskViewer import _ts_para_segundos
        seg = _ts_para_segundos(data_ms)
        if seg is None:
            return ""
        try:
            return datetime.datetime.fromtimestamp(seg).strftime("%H:%M")
        except Exception:
            return ""

    def _html_image(self, msg, from_me):
        """Inline thumbnail (cache) + working links (original / save)."""
        lfu = msg[13]
        media_name = msg[12]
        size = msg[9]
        ref = self._attachment_ref(lfu, media_name)
        lines = []
        path = (self.nucleo.zip.caminho_extraido(lfu)
                   if self.nucleo else None)
        print(f"[html_image] lfu={lfu}")
        print(f"[html_image] path={path}")
        if path and os.path.isfile(path):
            print(f"[html_image] file exists, trying QPixmap...")
            try:
                pm = QPixmap(path)
                if not pm.isNull():
                    print(f"[html_image] QPixmap OK: {pm.width()}x{pm.height()}")
                    img = self._image_b64(pm, max_height=280, max_width=380)
                    if img:
                        print(f"[html_image] thumbnail gerada: {len(img)} chars")
                        lines.append(img)
                    else:
                        print(f"[html_image] _image_b64 returned None")
                else:
                    print(f"[html_image] QPixmap is null")
            except Exception as e:
                print(f"[html_image] ERROR: {e}")
        else:
            print(f"[html_image] file does not exist, trying blob...")
        if not lines:
            # fallback: inline preview (BinaryData) when the file is not
            # in the zip (e.g. older media with only a preview blob)
            _bin = msg[3]
            if _bin:
                print(f"[html_image] trying blob ({len(_bin)} bytes)...")
                pm = QPixmap()
                pm.loadFromData(bytes(_bin))
                if not pm.isNull():
                    img = self._image_b64(pm, max_height=280, max_width=380)
                    if img:
                        lines.append(img)
        if not lines:
            print(f"[html_image] SEM thumbnail, usando fallback")
            lines.append(
                f'<div class="media">🖼 {_esc(media_name or "image")}</div>')
            lines.append(
                '<div class="media" style="color:#c0392b;">'
                '⚠ File does not exist in the export</div>')
        lines.append(
            f'<a href="zapix:image|{ref}">🔍 Original</a> &nbsp; '
            f'<a href="zapix:save|{ref}">💾 Save…</a>')
        return "".join(lines)

    def _html_sticker(self, msg):
        """Sticker thumbnail (zip → inline preview). None if absent."""
        lfu = msg[13]
        path = (self.nucleo.zip.caminho_extraido(lfu)
                   if self.nucleo else None)
        if path and os.path.isfile(path):
            pm = QPixmap(path)
            if not pm.isNull():
                img = self._image_b64(pm, max_height=220, max_width=280)
                if img:
                    return img
        _bin = msg[3]
        if _bin:
            pm = QPixmap()
            pm.loadFromData(bytes(_bin))
            if not pm.isNull():
                img = self._image_b64(pm, max_height=220, max_width=280)
                if img:
                    return img
        return None

    def _html_media(self, msg, icon, name, extra, sem_thumb=False):
        """Video/audio bubble: optional thumbnail + name line + play/save links.

        The thumbnail (first frame for video) is shown on top; the name +
        duration + size line and the links come AFTER it, so the footer date
        (added by _html_mensagem) is never covered."""
        lfu = msg[13]
        ref = self._attachment_ref(lfu, name)
        lines = []
        if not sem_thumb:
            thumb = self._media_thumb(msg)
            if thumb:
                lines.append(thumb)
        # name + extra (duration/size)
        lines.append(
            f'<div class="media">{icon} {_esc(name)} {_esc(extra)}</div>')
        if not self._has_file(msg):
            lines.append(
                '<div class="media" style="color:#c0392b;">'
                '⚠ File does not exist in the export</div>')
        lines.append(
            f'<a href="zapix:open|{ref}">▶ Open</a> &nbsp; '
            f'<a href="zapix:save|{ref}">💾 Save…</a>')
        return "".join(lines)

    def _media_thumb(self, msg):
        """First-frame thumbnail for a video (cached jpg/png/gif, else the
        inline preview blob). Returns an <img> data URI or None."""
        lfu = msg[13]
        path = (self.nucleo.zip.caminho_extraido(lfu)
                   if self.nucleo else None)
        if path and os.path.isfile(path):
            base = os.path.basename(path).lower()
            if base.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                pm = QPixmap(path)
                if not pm.isNull():
                    img = self._image_b64(pm, max_height=280, max_width=380)
                    if img:
                        return img
        _bin = msg[3]
        if _bin:
            pm = QPixmap()
            pm.loadFromData(bytes(_bin))
            if not pm.isNull():
                img = self._image_b64(pm, max_height=280, max_width=380)
                if img:
                    return img
        return None

    @staticmethod
    def _image_b64(pm, max_height=280, max_width=380):
        """QPixmap → <img data URI> (PNG, normalized to a fixed height).

        All thumbnails/fallbacks share the same display height so the chat
        looks uniform; width scales proportionally (aspect ratio preserved)."""
        if pm.isNull():
            return None
        # scale to fit within the box, preserving aspect ratio
        if pm.height() > max_height or pm.width() > max_width:
            pm = pm.scaled(max_width, max_height, Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
        # Encode a REAL PNG (raw pixels are not a valid PNG — the data URI
        # would be declared as PNG but contain unencoded RGBA and render as a
        # broken/blank image). QBuffer + QImage.save produces correct PNG
        # bytes with proper IHDR/IDAT/CRC chunks.
        qimg = pm.toImage()
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        if not qimg.save(buf, "PNG"):
            return None
        png_bytes = buf.data().data()  # bytes
        buf.close()
        b64 = base64.b64encode(bytes(png_bytes)).decode("ascii")
        return (f'<img src="data:image/png;base64,{b64}" '
                f'width="{pm.width()}" height="{pm.height()}">')

    def _has_file(self, msg):
        """True if the message's attachment exists (zip/cache) or has a preview."""
        try:
            lfu, _bin = msg[13], msg[3]
            if lfu and self.nucleo and self.nucleo.zip.tem(lfu):
                return True
            return bool(_bin)
        except Exception:
            return False

    # ------------------------------------------------------- attachments --
    def _lfu_ref(self, ref):
        """<hash16>|<name> → original LocalFileUri.

        Scans the already-loaded messages of the current conversation
        (kept in _msgs_loaded, which persists after the render).
        If not found — the conversation is very large and the attachment
        has not arrived yet — it falls back to a paginated database scan."""
        if not self.nucleo or not self._current_conversation:
            return None
        key = ref.split("|", 1)[0]
        # 1) in the already-in-memory messages (cheap)
        for m in self._msgs_loaded:
            lfu = m[13]
            if lfu and hashlib.md5(
                    str(lfu).encode("utf-8", "replace")).hexdigest()[:16] == key:
                return lfu
        # 2) paginated database scan (fallback)
        try:
            total = self.nucleo.total_mensagens(self._current_conversation["jid"])
            limit = min(total, 200000)
            for ini in range(0, limit, 400):
                batch = self.nucleo.mensagens(
                    self._current_conversation["jid"], 400, ini)
                if not batch:
                    break
                for m in batch:
                    lfu = m[13]
                    if lfu and hashlib.md5(
                            str(lfu).encode("utf-8", "replace")).hexdigest()[:16] == key:
                        return lfu
        except Exception:
            pass
        return None

    def _attachment_data(self, msg):
        """Attachment bytes: zip (via ZipIndex) → inline BLOB as fallback."""
        lfu = msg[13]
        data = self.nucleo.zip.bytes(lfu) if self.nucleo and lfu else None
        if data is None:
            data = msg[3] or None
        return data

    def _on_attachment_clicked(self, url):
        """Intercepts the bubbles' <a href='zapix:action|ref'>.

        The ChatView emits the raw QUrl (percent-encoded: '|' → %7C,
        '\\' → %5C). We URL-decode it, then split into action + ref.
        The ref is '<hash16>|<name>'; the name may contain spaces/unicode."""
        import urllib.parse
        print(f"\n[AUX] _on_attachment_clicked: url={url!r}")
        try:
            if hasattr(url, "toString"):
                s = url.toString()
            else:
                s = str(url).strip()
                if s.startswith("PySide6"):
                    s = s.split("QUrl('", 1)[-1].rstrip("')")
            print(f"[AUX] after toString/strip: s={s!r}")
            s = urllib.parse.unquote(s)
            print(f"[AUX] after unquote: s={s!r}")
            if s.startswith("zapix:"):
                s = s[len("zapix:"):]
            print(f"[AUX] after strip zapix:: s={s!r}")
            if "|" not in s:
                print("[AUX] ERROR: no '|' in string, returning")
                return
            action, ref = s.split("|", 1)
            print(f"[AUX] action={action!r}, ref={ref!r}")
            if not ref:
                print("[AUX] ERROR: ref is empty, returning")
                return
        except Exception as e:
            print(f"[AUX] ERROR in _on_attachment_clicked: {e}")
            import traceback
            traceback.print_exc()
            return
        # the lfu lookup may touch SQLite → run in the background to avoid blocking
        print("[AUX] starting WorkerAttachmentRef...")
        w = WorkerAttachmentRef(self, ref)
        self._attachment_workers.add(w)
        w.ready.connect(lambda lfu, a=action, r=ref: self._run_attachment(a, r, lfu))
        w.finished.connect(lambda: self._attachment_workers.discard(w))
        w.start()

    def _run_attachment(self, action, ref, lfu):
        name = ref.split("|", 1)[1] if "|" in ref else "attachment"
        print(f"\n[AUX] _run_attachment: action={action!r}, ref={ref!r}, lfu={lfu!r}")
        print(f"[AUX] name={name!r}")
        if action == "open":
            print("[AUX] calling _open_attachment")
            self._open_attachment(lfu, name)
        elif action == "save":
            print("[AUX] calling _save_attachment")
            self._save_attachment(lfu, name)
        elif action == "image":
            print("[AUX] calling _view_original_image")
            self._view_original_image(lfu, name)
        else:
            print(f"[AUX] ERROR: unknown action={action!r}")

    def _open_attachment(self, lfu, name):
        """Opens the attachment with the default app (audio/video/document).

        Uses the file's DIRECT path (extracted cache, or a one-off
        extraction from the zip) — the bytes are never read into memory,
        so even large documents (PDFs) open without a hitch."""
        print(f"\n[AUX] _open_attachment: lfu={lfu!r}, name={name!r}")
        if not self.nucleo or not lfu:
            print("[AUX] ERROR: nucleo or lfu is None/empty")
            QMessageBox.critical(
                self, APP_TITLE,
                f'The attachment "{name}" could not be resolved.\n\n'
                "It was not found in the already-loaded messages.")
            return
        self.status.showMessage("Opening attachment…")
        print("[AUX] starting WorkerDocument...")
        w = WorkerDocument(self.nucleo, lfu, self)
        self._attachment_workers.add(w)
        w.ready.connect(
            lambda path, n=name: self._open_attachment_end(path, n))
        w.error.connect(
            lambda e, n=name: self._open_attachment_error(n, e))
        w.finished.connect(lambda: self._attachment_workers.discard(w))
        w.start()

    def _open_attachment_error(self, name, error):
        """WorkerDocument failed (extraction/zip error): show a clear dialog."""
        QMessageBox.critical(
            self, APP_TITLE,
            f'Failed to open "{name}":\n\n{error}\n\n'
            "Try “💾 Save…” to extract the file and open it manually.")
        self.status.showMessage(f"Error opening: {name}")

    def _open_attachment_end(self, path, name):
        print(f"\n[AUX] _open_attachment_end: path={path!r}, name={name!r}")
        if not path:
            print("[AUX] ERROR: path is empty")
            QMessageBox.critical(
                self, APP_TITLE,
                f'The attachment "{name}" does not exist in the export.\n\n'
                "The file was not included in the .zip (common for older "
                "media).")
            return
        if not os.path.isfile(path):
            print(f"[AUX] ERROR: file does not exist at {path!r}")
            QMessageBox.critical(
                self, APP_TITLE,
                f'The attachment "{name}" does not exist in the export.\n\n'
                f"File path:\n{path}\n\n"
                "The file was not included in the .zip (common for older "
                "media).")
            return
        print(f"[AUX] file exists: {os.path.getsize(path)} bytes")
        ext = os.path.splitext(path)[1].lower()
        is_media = ext in (".mp3", ".ogg", ".opus", ".m4a", ".wav", ".amr",
                           ".mp4", ".mkv", ".mov", ".3gp", ".webm")
        feito = False
        # 1) media on Linux/macOS: try dedicated players first (more reliable
        #    than the default app for some containers), same order as the
        #    original viewer
        if is_media and os.name != "nt":
            print(f"[AUX] media file (ext={ext}), trying players...")
            for cmd in (["mpv", "--really-quiet", path],
                        ["ffplay", path], ["vlc", path]):
                if shutil.which(cmd[0]):
                    print(f"[AUX] found player: {cmd[0]}, trying to launch...")
                    try:
                        subprocess.Popen(cmd,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                        feito = True
                        print(f"[AUX] player launched successfully: {cmd[0]}")
                        break
                    except Exception as e:
                        print(f"[AUX] ERROR launching {cmd[0]}: {e}")
                        continue
            if not feito:
                print("[AUX] no player available or all failed")
        # 2) the system's default app (documents, and media when no player
        #    launched) — on Windows this is always the path taken
        if not feito:
            url = QUrl.fromLocalFile(path)
            print(f"[AUX] using QDesktopServices.openUrl with url={url!r}")
            if not QDesktopServices.openUrl(url):
                print("[AUX] ERROR: QDesktopServices.openUrl returned False")
                QMessageBox.warning(
                    self, APP_TITLE,
                    f'Could not open "{name}" automatically.\n\n'
                    f"File location:\n{path}\n\n"
                    "Open it manually or use “💾 Save…”.")
            else:
                print("[AUX] QDesktopServices.openUrl succeeded")
        self.status.showMessage(f"Opening: {name}")

    def _save_attachment(self, lfu, name):
        destination = QFileDialog.getSaveFileName(
            self, "Save attachment", name, "All (*.*)")[0]
        if not destination:
            return
        if not self.nucleo or not lfu:
            return
        self.status.showMessage("Saving attachment…")
        w = WorkerBytes(self.nucleo, lfu, self)
        self._attachment_workers.add(w)
        w.ready.connect(
            lambda data, d=destination, n=name: self._save_attachment_end(
                data, d, n))
        w.error.connect(self._attachment_error)
        w.finished.connect(lambda: self._attachment_workers.discard(w))
        w.start()

    def _save_attachment_end(self, data, destination, name):
        if not data:
            QMessageBox.critical(
                self, APP_TITLE,
                f'The attachment "{name}" does not exist in the export.\n\n'
                "The file was not included in the .zip (common for older "
                "media).")
            return
        try:
            with open(destination, "wb") as f:
                f.write(data)
            self.status.showMessage(f"Saved: {os.path.basename(destination)}")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE,
                                 f"Failed to save:\n{exc}")

    def _view_original_image(self, lfu, name):
        if not self.nucleo or not lfu:
            return
        self.status.showMessage("Loading original image…")
        w = WorkerBytes(self.nucleo, lfu, self)
        self._attachment_workers.add(w)
        w.ready.connect(
            lambda data, n=name: self._view_original_end(data, n))
        w.error.connect(self._attachment_error)
        w.finished.connect(lambda: self._attachment_workers.discard(w))
        w.start()

    def _view_original_end(self, data, name):
        pm = QPixmap()
        if data:
            pm.loadFromData(data)
        if pm.isNull():
            error_msg = (f'Could not open the image "{name}".\n\n'
                        if data else
                        f'The attachment "{name}" does not exist in the export.\n\n'
                        "The file was not included in the .zip (common for older media).")
            QMessageBox.critical(self, APP_TITLE, error_msg)
            return
        top = ImageView(pm, name, self)
        self._image_window = top  # keeps the reference alive
        top.show()

    def _attachment_error(self, msg):
        QMessageBox.critical(self, APP_TITLE, "Attachment failure:\n\n" + msg)

    def _chat_error(self, msg):
        QMessageBox.critical(self, APP_TITLE,
                             "Failed to load messages:\n\n" + msg)
        self._loading_bar_off()
        # the "load all first" overlay must not stay after a failure
        self._remove_loading_screen()
        # never leave a dead pin holding the viewport (it would freeze the
        # scrollbar at a stale position on a failed/empty conversation)
        self.chat_view.pin_disable()
        self._render_ocupado = False

    # ------------------------------------------------------------- export -
    def _export_conversation(self):
        c = self._current_conversation
        if not c:
            QMessageBox.information(self, APP_TITLE,
                                    "Open a conversation first.")
            return
        if not self.nucleo:
            return
        resp = QMessageBox.question(
            self, "Export conversation",
            f'Export “{c["nome"]}” to PDF?\n\n'
            "(HTML will be generated if PDF is not available.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if resp != QMessageBox.Yes:
            return
        formato = "pdf"
        self.status.showMessage("Exporting…")
        self._worker_export = WorkerExport(self.nucleo, c, formato, self)
        self._worker_export.status.connect(self.status.showMessage)
        self._worker_export.ready.connect(self._export_ready)
        self._worker_export.error.connect(self._export_error)
        self._worker_export.start()

    def _export_ready(self, path):
        self.status.showMessage(f"Exported: {os.path.basename(path)}")
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:
            pass
        QMessageBox.information(self, APP_TITLE,
                                f"Conversation exported to:\n{path}")

    def _export_error(self, msg):
        QMessageBox.critical(self, APP_TITLE, "Failed to export:\n\n" + msg)
        self.status.showMessage("Error exporting.")

    # -------------------------------------------------------------- cleanup --
    def closeEvent(self, ev):
        try:
            for w in (self._worker_open, self._worker_attachments,
                      self._worker_conversation, self._worker_older,
                      self._worker_export):
                if w is not None and w.isRunning():
                    if hasattr(w, "stop"):
                        w.stop()
                    w.wait(1500)
        except Exception:
            pass
        try:
            if self.nucleo:
                self.nucleo.fechar()
        except Exception:
            pass
        ev.accept()


# ---------------------------------------------------------------------------
# Splash (same style as the original: centered logo, simple fade)
# ---------------------------------------------------------------------------

class Splash(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZAPiXDesk")
        self.setFixedSize(480, 300)
        self.setWindowFlags(Qt.SplashScreen)
        p = _recurso("splash.png")
        if p and os.path.isfile(p):
            pm = QPixmap(p)
            pm = pm.scaled(480, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl = QLabel(self)
            self.lbl.setPixmap(pm)
            self.lbl.resize(480, 300)
        else:
            self.lbl = QLabel("ZAPiXDesk", self)
            self.lbl.setStyleSheet("font-size:26px; font-weight:bold; color:white;"
                                   " background:#0b0e0c;")
            self.lbl.setAlignment(Qt.AlignCenter)
            self.lbl.resize(480, 300)
        sw = QApplication.primaryScreen().availableGeometry().width()
        sh = QApplication.primaryScreen().availableGeometry().height()
        self.move((sw - 480) // 2, (sh - 300) // 2)
        QTimer.singleShot(1400, self.close)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ZAPiXDeskViewerQt")
    app.setStyleSheet(ESTILO)
    splash = Splash()
    splash.show()
    window = MainWindow()
    window.show()
    splash.close()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
