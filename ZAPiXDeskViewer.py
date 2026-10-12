#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZAPiXDeskViewer — viewer for ZAPiXDESK exports (PowerShell).


Author:    Alberto Magno (kraftdenker) <alberto.magno@gmail.com>
Co-author: DeepSeek Harness (AI assistant)

Single file, standard library only + Pillow (optional, for image
previews). Flow:

  1. Pick the exported .zip and the .txt with the MD5.
  2. Verify the hash (MD5, by blocks; ignores BOM and the " - path" suffix).
  3. Extract the decoded databases (messages/contacts) to a temp folder.
  4. List conversations (groups + DMs) with name, last message, and unread count.
  5. Show the chat with text, embedded media (read straight from the zip),
     documents, audios, videos, contacts (vCard), reactions, and links.

Usage:  python3 ZAPiXDeskViewer.py
"""

import hashlib
import html
import io
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.filedialog as fdlg
import tkinter.simpledialog as sdlg
import tkinter.font as tfont
import tkinter.messagebox as mbox
import tkinter.ttk as ttk
import traceback
import zipfile
import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
    import PIL.ImageTk as ImageTk
    HAS_PIL = True
except Exception:  # pragma: no cover
    HAS_PIL = False


def _fonte_pil(tamanho, negrito=False):
    """TrueType font for PIL (DejaVu Sans, with fallback to default)."""
    candidatos = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
        % ("-Bold" if negrito else ""),
        "/usr/share/fonts/dejavu/DejaVuSans%s.ttf"
        % ("-Bold" if negrito else ""),
        "DejaVuSans%s.ttf" % ("-Bold" if negrito else ""),
    ]
    for c in candidatos:
        try:
            return ImageFont.truetype(c, tamanho)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None

APP_TITLE = "ZAPiXDeskViewer — Conversation Viewer"
MB = 1024 * 1024

# ---------------------------------------------------------------------------
# Branding resources (splash screen + window icon)
# ---------------------------------------------------------------------------

# Resource location: next to the script, or inside the PyInstaller bundle (frozen).
def _base_dir():
    if getattr(sys, "frozen", False):
        # Bundled (PyInstaller --onefile): resources live in _MEIPASS.
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()
LOGO_SPLASH = os.path.join(BASE_DIR, "splash.png")        # 480x300 logo on dark bg
LOGO_ICONE = os.path.join(BASE_DIR, "logo_icone.png")     # logo mark (sem o texto)
ICONE_ICO = os.path.join(BASE_DIR, "zapixdesk.ico")       # .ico p/ Windows


def _caminho_recurso(nome):
    """Resolve a bundled resource next to this script; return None if missing."""
    p = os.path.join(BASE_DIR, nome)
    return p if os.path.isfile(p) else None


def definir_icone_janela(raiz):
    """Set the window icon: PNG via iconphoto (Linux/mac) or .ico via iconbitmap (Windows).

    Keeps a reference on the window so the PhotoImage isn't garbage-collected.
    Returns True on success, False if no icon could be set.
    """
    raiz._icone_ref = None
    try:
        if os.name == "nt":
            if _caminho_recurso("zapixdesk.ico"):
                raiz.iconbitmap(_caminho_recurso("zapixdesk.ico"))
                return True
        if HAS_PIL and _caminho_recurso("logo_icone.png"):
            img = Image.open(_caminho_recurso("logo_icone.png"))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            foto = ImageTk.PhotoImage(img)
            raiz._icone_ref = foto
            raiz.iconphoto(True, foto)
            return True
    except Exception:
        log_excecao("definir_icone_janela", tb=traceback.format_exc())
    return False


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zapixdeskviewer.log")
_log_lock = threading.Lock()


def log(msg, *args):
    """Writes `msg` (% format and args) to the log file with a timestamp."""
    texto = msg % args if args else msg
    linha = "%s | %s\n" % (
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], texto)
    try:
        with _log_lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(linha)
    except Exception:
        pass


def log_excecao(etiqueta, exc=None, tb=None):
    log("EXCEPTION in %s: %s", etiqueta, exc)
    if tb:
        for part in tb.splitlines():
            log("    %s", part)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def md5_arquivo(caminho, progresso=None, parar=None):
    """Calcula o MD5 de um arquivo por blocos. `progresso(bytes)` opcional."""
    h = hashlib.md5()
    total = os.path.getsize(caminho)
    lido = 0
    ultimo = 0.0
    with open(caminho, "rb") as f:
        while True:
            if parar is not None and parar():
                return None
            bloco = f.read(16 * MB)
            if not bloco:
                break
            h.update(bloco)
            lido += len(bloco)
            agora = time.time()
            if progresso and agora - ultimo > 0.15:
                progresso(lido, total)
                ultimo = agora
    return h.hexdigest().upper()


def ler_md5_esperado(caminho_txt):
    """Reads the MD5 .txt: `<32 HEX> - <path>` (BOM and suffix optional)."""
    with open(caminho_txt, "rb") as f:
        bruto = f.read()
    texto = bruto.decode("utf-8-sig", "replace")
    m = re.search(r"[0-9a-fA-F]{32}", texto)
    return m.group(0).upper() if m else None


def _ts_para_segundos(v):
    """Converte TimestampLong para SEGUNDOS, detectando a unidade.

    Os exports do ZAPiXDESK variam entre segundos e milissegundos
    (the original heuristic assumed ms and broke with exports in s,
    generating 1970 dates). Valid range: 2000-01-01..2100-01-01."""
    try:
        v = int(v)
    except Exception:
        return None
    if v <= 0:
        return None
    # tries s / ms / us / ns and accepts the only one that falls in 2000-2100
    for div in (1, 1000, 10 ** 6, 10 ** 9):
        s = v / div
        if 946684800 <= s <= 4102444800:  # 2000-01-01 .. 2100-01-01
            return s
    return None


def hora(data_ms):
    if not data_ms:
        return ""
    seg = _ts_para_segundos(data_ms)
    if seg is None:
        return ""
    import datetime
    try:
        dt = datetime.datetime.fromtimestamp(seg)
    except Exception:
        return ""
    return dt.strftime("%d/%m/%Y %H:%M")


def tamanho_amigavel(n):
    try:
        n = float(n)
    except Exception:
        return ""
    for un in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or un == "TB":
            return ("%.0f %s" if un in ("B",) else "%.1f %s") % (n, un)
        n /= 1024.0


def normalizar_uri(uri):
    """Backslash LocalFileUri -> lookup key in the zip index."""
    if not uri:
        return None
    u = uri.replace("\\", "/")
    return u.lower().lstrip("/")


class ZipIndex:
    """Zip entries index by normalized path and by base name."""

    def __init__(self, zpath, cache_dir=None):
        self.zpath = zpath
        self.cache_dir = cache_dir
        self._zf = None
        self._por_caminho = None
        self._por_base = None
        self._cache_caminho = None
        self._cache_base = None

    def _abrir(self):
        if self._zf is None:
            self._zf = zipfile.ZipFile(self.zpath, "r")
            self._por_caminho = {}
            self._por_base = {}
            for i in self._zf.infolist():
                if i.filename.endswith("/"):
                    continue
                chave = i.filename.replace("\\", "/").lower().lstrip("/")
                self._por_caminho.setdefault(chave, i.filename)
                base = os.path.basename(chave).lower()
                if base:
                    self._por_base.setdefault(base, i.filename)
        return self._zf

    def _indexar_cache(self):
        """Maps base name (lowercase) -> extracted file in the attachments folder.

        The cache file name is '<offset:012x>_<path_with_backslashes>'
        (e.g. 000022908264_shared\\transfers\\2025_14\\IMG-....jpg). The lookup
        key is the normalized BASENAME (last component, slash, lowercase)
        — so we extract only the basename, discarding the offset prefix and the
        directories (which bring backslashes and would break the comparison)."""
        if self._cache_caminho is not None:
            return
        self._cache_caminho = {}
        self._cache_base = {}
        d = self.cache_dir
        if not d or not os.path.isdir(d):
            return
        for f in os.listdir(d):
            if f == ".feito":
                continue
            caminho = os.path.join(d, f)
            if not os.path.isfile(caminho):
                continue
            # descarta o prefixo <offset:012x>_ (13 chars) e pega o basename
            corpo = f[13:] if len(f) > 13 and f[12] == "_" else f
            base = os.path.basename(corpo.replace("\\", "/"))
            if base:
                self._cache_base.setdefault(base.lower(), caminho)

    def achar(self, uri):
        """Returns the zip entry name for a LocalFileUri (or None)."""
        chave = normalizar_uri(uri)
        if not chave:
            return None
        self._abrir()
        e = self._por_caminho.get(chave)
        if e:
            return e
        base = os.path.basename(chave)
        return self._por_base.get(base)

    def caminho_extraido(self, uri):
        """Path of the already-extracted attachment in the cache, or None."""
        chave = normalizar_uri(uri)
        if not chave:
            return None
        self._indexar_cache()
        base = os.path.basename(chave).lower()
        return self._cache_base.get(base)

    def tem(self, uri):
        return self.achar(uri) is not None or self.caminho_extraido(uri) is not None

    def _ler_cache(self, caminho):
        with open(caminho, "rb") as f:
            return f.read()

    def bytes(self, uri, limite=150 * MB):
        """Reads an attachment's bytes (from the extracted cache, else the zip)."""
        caminho = self.caminho_extraido(uri)
        if caminho and os.path.getsize(caminho) <= limite:
            try:
                return self._ler_cache(caminho)
            except Exception:
                pass
        nome = self.achar(uri)
        if not nome:
            return None
        try:
            info = self._abrir().getinfo(nome)
            if info.file_size > limite:
                return None
            with self._abrir().open(nome) as f:
                return f.read()
        except Exception:
            return None

    def info(self, uri):
        caminho = self.caminho_extraido(uri)
        if caminho:
            return (os.path.basename(caminho), os.path.getsize(caminho))
        nome = self.achar(uri)
        if not nome:
            return None
        try:
            i = self._abrir().getinfo(nome)
            return (os.path.basename(nome), i.file_size)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Data core
# ---------------------------------------------------------------------------

class Nucleo:
    """Extracts the databases from the zip and loads metadata (names, conversations)."""

    def __init__(self, zip_path):
        self.zip_path = zip_path
        self.tmp = tempfile.mkdtemp(prefix="zapixview_")
        self.anexos_dir = None
        self.zip = None
        self.msg_db = None
        self.con_db = None
        self.nomes = {}
        self.conversas = []  # lista de dicts ordenada
        self._participantes = {}  # jid_grupo -> {pushname -> jid} (remetentes vistos)
        self.dono_jid = None    # jid do dono da conta
        self.dono_nome = None   # owner's readable name
        self.dono_confirmado = False  # True when the user designated the owner
        self.dono_metodo = None  # how the owner was detected (shown in the UI)
        self._carregar()

    # -- extraction -----------------------------------------------------------
    def _extrair(self, entradas):
        z = zipfile.ZipFile(self.zip_path, "r")
        nomes = set(z.namelist())
        for nome in entradas:
            if nome not in nomes:
                continue  # missing entry (e.g. no -wal) is tolerated
            alvo = os.path.join(self.tmp, os.path.basename(nome))
            if os.path.exists(alvo):
                continue
            with z.open(nome) as src, open(alvo, "wb") as out:
                shutil.copyfileobj(src, out, 16 * MB)

    def _hash_zip(self):
        """MD5 (first 64 MB only) to version the attachments cache."""
        h = hashlib.md5()
        with open(self.zip_path, "rb") as f:
            while True:
                b = f.read(16 * MB)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()[:16]

    def _carregar(self):
        # 1) bancos + WAL
        self._extrair(["messages.dec.db", "messages.dec.db-wal",
                       "contacts.dec.db", "contacts.dec.db-wal"])
        # 2) connections (SQLite applies the WAL automatically)
        self.msg_db = self._con(
            "file:%s?mode=ro" % os.path.join(self.tmp, "messages.dec.db"),
            "file:%s?mode=ro" % os.path.join(self.tmp, "messages.dec.db-wal"))
        self.con_db = self._con(
            "file:%s?mode=ro" % os.path.join(self.tmp, "contacts.dec.db"),
            "file:%s?mode=ro" % os.path.join(self.tmp, "contacts.dec.db-wal"))
        # 3) nomes de contatos
        for jid, nome in self.con_db.execute(
                "SELECT Jid, COALESCE(ContactName, PushName, "
                "DisplayNameFromServer, UserName) FROM UserStatuses"):
            if nome and jid:
                self.nomes[jid.lower()] = str(nome).strip()
        # 4) local indexes (written to the -wal next to it; the zip is untouched)
        self._criar_indices()
        # 5) zip index — from the start, so media rendering
        #    works even before the background extraction finishes.
        #    Also points at the already-extracted attachments folder (a
        #    cache versioned by the zip's hash), if it exists: thumbnails
        #    then appear immediately, without waiting for the background
        #    extraction.
        cache_anexos = os.path.join(
            tempfile.gettempdir(), "zapixdesk_anexos", self._hash_zip())
        self.anexos_dir = cache_anexos
        self.zip = ZipIndex(self.zip_path, cache_dir=cache_anexos)
        # 6) conversations + last message
        self._montar_conversas()
        # 7) account owner — layered detection (see _detectar_dono)
        self._detectar_dono()
        log("Core ready: %d conversations, %d contacts, dono=%s (%s)",
            len(self.conversas), len(self.nomes), self.dono_jid,
            self.dono_metodo)

    def _detectar_dono(self):
        """Detects the account owner in layers, from strongest to weakest signal.

        The ZAPiXDESK export does NOT explicitly mark the owner, so we use
        structural signals from the data (nothing hardcoded):

        1. AUTO-DM ("notes to self"): the owner can send messages
           to their OWN number. That conversation is almost 100% de_mim
           (KeyFromMe=1) and the peer is a personal number (not @g.us /
           @c.us). No other contact shows that pattern — only the owner
           talks to themselves that much. In the reference export, the
           owner (Alberto Magno) has 959/962 de_mim messages in their own
           DM, while the most "frequent" contact (Idalina) is ~50/50.
           Threshold: >=85% de_mim and >=50 messages (avoids short DMs).
        2. Volume: DM with the highest total message volume (classic
           fallback; fails when the owner talks more to a third party
           than to themselves — e.g. Idalina).
        3. Presence: contact that appears as sender (PushName) in the
           MOST groups (the owner is usually in all of them).

        The user can (and should) confirm/correct by clicking
        "👤 Owner" (see Nucleo.definir_dono)."""
        jid, metodo = None, None
        # -- 1) auto-DM ------------------------------------------------
        try:
            rows = self.msg_db.execute(
                """SELECT KeyRemoteJid, count(*) AS total,
                          SUM(CASE WHEN KeyFromMe=1 THEN 1 ELSE 0 END) AS mim
                   FROM Messages
                   WHERE KeyRemoteJid NOT LIKE '%@g.us'
                     AND KeyRemoteJid NOT LIKE '%@c.us'
                     AND KeyRemoteJid IS NOT NULL
                   GROUP BY KeyRemoteJid
                   HAVING total >= 50
                   ORDER BY (mim * 1.0 / total) DESC, total DESC""").fetchall()
            for j, total, mim in rows:
                if total and (mim or 0) * 1.0 / total >= 0.85:
                    jid, metodo = j, "auto-DM (notes to self)"
                    break
        except sqlite3.Error as exc:
            log("dono: auto-DM unavailable (%s)", exc)
        # -- 2) volume total -------------------------------------------
        if not jid:
            try:
                r = self.msg_db.execute(
                    """SELECT KeyRemoteJid, count(*) FROM Messages
                       WHERE KeyRemoteJid NOT LIKE '%@g.us'
                         AND KeyRemoteJid NOT LIKE '%@c.us'
                       GROUP BY KeyRemoteJid ORDER BY count(*) DESC LIMIT 1""").fetchone()
                if r:
                    jid, metodo = r[0], "highest DM volume"
            except sqlite3.Error as exc:
                log("dono: volume unavailable (%s)", exc)
        # -- 3) group presence ------------------------------------------
        if not jid:
            try:
                r = self.msg_db.execute(
                    """SELECT PushName, count(DISTINCT KeyRemoteJid)
                       FROM Messages
                       WHERE KeyRemoteJid LIKE '%@g.us'
                         AND KeyFromMe=0 AND PushName IS NOT NULL AND PushName != ''
                       GROUP BY PushName ORDER BY 2 DESC LIMIT 1""").fetchone()
                if r:
                    nome = r[0]
                    jid = self._jid_pelo_nome(nome)
                    metodo = "present in the most groups" if jid else None
            except sqlite3.Error as exc:
                log("dono: group presence unavailable (%s)", exc)
        if jid:
            self.dono_jid = jid
            self.dono_nome = self.nomes.get(str(jid).lower()) \
                or str(jid).split("@")[0]
            self.dono_metodo = metodo

    def _jid_pelo_nome(self, nome):
        """Resolves a contact name to a jid (UserStatuses, several fields)."""
        alvo = (nome or "").strip().lower()
        if not alvo:
            return None
        try:
            for campo in ("ContactName", "PushName", "DisplayNameFromServer"):
                r = self.con_db.execute(
                    f"SELECT Jid FROM UserStatuses WHERE {campo} = ?",
                    (nome.strip(),)).fetchone()
                if r:
                    return r[0]
            r = self.con_db.execute(
                "SELECT Jid FROM UserStatuses WHERE lower(ContactName) = ? "
                "OR lower(PushName) = ?", (alvo, alvo)).fetchone()
            if r:
                return r[0]
        except sqlite3.Error:
            pass
        return None

    def definir_dono(self, jid, nome=None):
        """Sets the account owner (called by the UI when the user clicks
        the owner's conversation). Overrides the detection from _detectar_dono."""
        if not jid:
            return
        self.dono_jid = jid
        self.dono_nome = nome or self.nomes.get(str(jid).lower()) or str(jid).split("@")[0]
        self.dono_confirmado = True
        self.dono_metodo = "designated by the user"

    def _extraer_anexos(self, progresso=None, parar=None):
        """Extracts all attachments (media/documents) from the zip into a
        persistent cache (versioned by zip hash), once. So rendering
        conversations reads from local disk, not the zip.
        Returns the attachments folder and creates a ZipIndex pointing to it."""
        import re as _re
        exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".bmp",
                ".mp4", ".mkv", ".mov", ".3gp", ".webm", ".mp3", ".ogg",
                ".opus", ".m4a", ".wav", ".amr", ".pdf", ".doc", ".docx",
                ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".zip", ".vcf",
                ".apk", ".bin")
        base_cache = os.path.join(tempfile.gettempdir(), "zapixdesk_anexos")
        destino_dir = os.path.join(base_cache, self._hash_zip())
        os.makedirs(destino_dir, exist_ok=True)
        # extraction marker: if it exists and has files, do not redo
        marca = os.path.join(destino_dir, ".feito")
        ja_feito = os.path.exists(marca) and len(
            [f for f in os.listdir(destino_dir) if f != ".feito"]) > 0
        if not ja_feito:
            zf = zipfile.ZipFile(self.zip_path, "r")
            n_ok = n_pulados = 0
            for i in zf.infolist():
                if parar is not None and parar():
                    break
                if i.filename.endswith("/"):
                    continue
                nome_b = os.path.basename(i.filename)
                if not nome_b.lower().endswith(exts):
                    continue
                alvo = os.path.join(destino_dir,
                                    f"{i.header_offset:012x}_{nome_b}")
                if os.path.exists(alvo) and os.path.getsize(alvo) == i.file_size:
                    n_pulados += 1
                    continue
                try:
                    with zf.open(i) as src, open(alvo, "wb") as out:
                        shutil.copyfileobj(src, out, 16 * MB)
                    n_ok += 1
                except Exception as exc:
                    log("failed to extract %s: %s", i.filename, exc)
                if progresso and (n_ok % 20 == 0 or n_ok == 1):
                    progresso(n_ok, n_ok + n_pulados)
            zf.close()
            with open(marca, "w") as f:
                f.write("ok")
            log("Attachments: %d extracted, %d already cached (dir=%s)",
                n_ok, n_pulados, destino_dir)
        else:
            log("Attachments already cached (dir=%s)", destino_dir)
        self.anexos_dir = destino_dir
        # re-creates the ZipIndex pointing to the extracted attachments folder
        # (o anterior, criado no __init__, lia direto do zip)
        self.zip = ZipIndex(self.zip_path, cache_dir=destino_dir)
        return destino_dir

    def caminho_anexo(self, uri):
        """Direct path to the attachment (extracted cache or the zip itself,
        so no re-read of the bytes). Used to open documents with the default
        app without copying the file."""
        caminho = self.zip.caminho_extraido(uri) if self.zip else None
        if caminho and os.path.isfile(caminho):
            return caminho
        nome = self.zip.achar(uri) if self.zip else None
        if nome:
            # The attachment is inside the zip but has not been extracted
            # yet (extraction in progress or skipped): extract it now, just
            # this one, into the cache (same folder the background extraction
            # writes to).
            base_cache = os.path.dirname(self.zip.cache_dir or os.getcwd())
            os.makedirs(base_cache, exist_ok=True)
            destino = os.path.join(base_cache,
                                   f"ao_dados_{os.path.basename(nome)}")
            try:
                with zipfile.ZipFile(self.zip.zpath, "r") as zf, \
                        zf.open(nome) as src, open(destino, "wb") as out:
                    shutil.copyfileobj(src, out, 16 * MB)
                return destino
            except Exception:
                pass
        return None

    def _criar_indices(self):
        """Local WAL-mode indexes to speed up per-conversation lookups.
        Written to the -wal file in the temp folder; the zip stays intact."""
        try:
            self.msg_db.execute("PRAGMA journal_mode=WAL")
            self.msg_db.execute("PRAGMA synchronous=NORMAL")
            self.msg_db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_jid_ts "
                "ON Messages(KeyRemoteJid, TimestampLong)")
            self.msg_db.execute(
                "CREATE INDEX IF NOT EXISTS idx_reactions_parent "
                "ON Reactions(ParentMessageID)")
            # prefix index: makes the global search's
            # `WHERE Data LIKE 'term%' OR MediaCaption LIKE 'term%'` a
            # range scan instead of a full table scan (SQLite can use a
            # prefix index on a leading-anchored LIKE with no wildcards
            # before the prefix).
            self.msg_db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_data_prefix "
                "ON Messages(Data(40))")
            self.msg_db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_caption_prefix "
                "ON Messages(MediaCaption(40))")
            self.msg_db.commit()
            log("Local indexes created")
        except sqlite3.Error as exc:
            log("Warning: no local indexes (%s); slower lookups", exc)

    @staticmethod
    def _con(db_uri, wal_uri):
        # check_same_thread=False: the UI loads conversations in a separate
        # thread (each sqlite3.Connection may only be used on the thread that
        # created it, unless allowed; reads are sequential per conversation).
        con = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
        # ATTACH is not needed: with the -wal file next to it, the engine reads it alone.
        return con

    def _nome_do(self, jid):
        j = (jid or "").lower()
        if j.endswith("@g.us"):
            # group name comes from the conversation itself
            return None
        return self.nomes.get(j)

    def _montar_conversas(self):
        rows = self.msg_db.execute(
            """SELECT c.Jid, c.GroupSubject, c.Timestamp, c.UnreadMessageCount,
                      c.LastMessageID, m.KeyFromMe, m.Data, m.MediaWaType,
                      m.MediaCaption, m.TimestampLong
               FROM Conversations c
               LEFT JOIN Messages m ON m.MessageID = c.LastMessageID
               ORDER BY COALESCE(c.Timestamp, 0) DESC""").fetchall()
        for jid, grupo, ts, nao_lidas, _last_id, de_mim, texto, tipo, legenda, ts_m in rows:
            nome = grupo if jid.lower().endswith("@g.us") else self._nome_do(jid)
            nome = (nome or jid.split("@")[0])
            resumo = ""
            if tipo == 0 and texto:
                resumo = texto[:90]
            elif legenda:
                resumo = legenda[:90]
            else:
                resumo = self._resumo_tipo(tipo)
            self.conversas.append({
                "jid": jid, "nome": str(nome), "grupo": jid.lower().endswith("@g.us"),
                "ts": ts or 0, "nao_lidas": int(nao_lidas or 0), "resumo": resumo,
            })

    @staticmethod
    def _resumo_tipo(t):
        m = {1: "[Image]", 2: "[Audio]", 3: "[Video]", 4: "[Contact]",
             8: "[Document]", 9: "[Link]", 10: "[Sticker]"}
        return m.get(t, "[Message]") if t else ""

    # -- messages of a conversation ------------------------------------------
    # Returns 16-field tuples; the last one is the reactions list [(jid, emoji), ...].
    # Reactions already aggregated in a single JOIN (avoids a query per message).
    def total_mensagens(self, jid):
        try:
            r = self.msg_db.execute(
                "SELECT count(*) FROM Messages WHERE KeyRemoteJid = ?",
                (jid,)).fetchone()
            return int(r[0]) if r else 0
        except sqlite3.Error:
            return 0

    def mensagens(self, jid, limite=2000, offset=0):
        """Last `limite` messages (most recent), with `offset` regression
        to load earlier pages.

        Reactions: instead of a correlated subquery per message (which
        rescans the Reactions table on every row, with no index), a single
        bulk JOIN is done against a temp table holding the page's IDs:
        O(page) instead of O(page × reactions).

        Pagination with a composite cursor (TimestampLong, MessageID)
        instead of OFFSET: SQLite 3.45 (Debian) has a bug where
        `ORDER BY ... LIMIT N` queries with BLOB columns (BinaryData)
        fail with "unable to open database file" when the result
        accumulates ~1MB of BLOBs in a single query. With a cursor +
        small `limite` (400 rows ≈ 300KB of BLOBs), each query stays
        below the threshold and the bug does not trigger. The cursor
        uses (TimestampLong, MessageID) so rows with a duplicated
        TimestampLong are not lost."""
        # the effective per-query limit is 400 (below the bug's threshold)
        lim_efetivo = min(limite, 400)
        if offset > 0:
            try:
                r = self.msg_db.execute(
                    "SELECT TimestampLong, MessageID FROM Messages "
                    "WHERE KeyRemoteJid = ? "
                    "ORDER BY TimestampLong DESC, MessageID DESC "
                    "LIMIT 1 OFFSET ?", (jid, offset)).fetchone()
                cursor = (r[0], r[1]) if r else None
            except sqlite3.Error:
                cursor = None
        else:
            cursor = None
        return self._pagina_por_cursor(jid, lim_efetivo, cursor)

    _COLS_MSG = ("MessageID, KeyFromMe, Data, BinaryData, DataFileName, "
                "TimestampLong, PushName, MediaMimeType, MediaWaType, "
                "MediaSize, MediaDurationSeconds, MediaName, MediaCaption, "
                "LocalFileUri, Reactions")
    def _pagina_por_cursor(self, jid, lim, cursor):
        """One page of `lim` rows after `cursor` (None = most recent), with
        dedup, reactions JOIN and group-sender accumulation. Returns the page
        in CHRONOLOGICAL order (oldest first). Shared engine of mensagens()
        (offset path) and mensagens_apos_cursor() (true cursor path)."""
        cols = self._COLS_MSG
        if cursor is None:
            rows = self.msg_db.execute(
                f"SELECT {cols} FROM Messages "
                "WHERE KeyRemoteJid = ? "
                "ORDER BY TimestampLong DESC, MessageID DESC LIMIT ?",
                (jid, lim)).fetchall()
        else:
            ts_c, mid_c = cursor
            # uses < (strict) so the cursor is not duplicated; if more rows
            # share the same (TS, MID) — which should not happen — they are
            # lost, but pagination does not get stuck.
            rows = self.msg_db.execute(
                f"SELECT {cols} FROM Messages "
                "WHERE KeyRemoteJid = ? "
                "AND (TimestampLong < ? OR (TimestampLong = ? AND MessageID < ?)) "
                "ORDER BY TimestampLong DESC, MessageID DESC LIMIT ?",
                (jid, ts_c, ts_c, mid_c, lim)).fetchall()
        if not rows:
            return []
        # defensive dedup: if the (TS, MID) cursor is not unique in the
        # database, a MessageID can repeat. Remove duplicates by MessageID.
        if len(rows) > 1:
            vistos = set()
            unicas = []
            for r in rows:
                if r[0] not in vistos:
                    vistos.add(r[0])
                    unicas.append(r)
            rows = unicas
        # reactions for the page in ONE query (JOIN via temp table)
        reag_map = {}
        try:
            ids = [r[0] for r in rows]
            self.msg_db.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _pagina_ids (id TEXT PRIMARY KEY)")
            self.msg_db.execute("DELETE FROM _pagina_ids")
            self.msg_db.executemany(
                "INSERT INTO _pagina_ids (id) VALUES (?)", [(i,) for i in ids])
            reag_rows = self.msg_db.execute(
                """SELECT r.ParentMessageID, r.Text, r.RemoteResource
                   FROM Reactions r JOIN _pagina_ids p ON p.id = r.ParentMessageID""")
            for pid, texto_r, jid_r in reag_rows:
                reag_map.setdefault(pid, []).append((jid_r or "", texto_r or ""))
            self.msg_db.execute("DROP TABLE IF EXISTS _pagina_ids")
        except sqlite3.Error as exc:
            log("Warning: reactions via JOIN unavailable (%s)", exc)
        out = []
        eh_grupo = str(jid).lower().endswith("@g.us")
        for row in rows:
            row = list(row)
            row.append(reag_map.get(row[0], []))
            out.append(tuple(row))
        # accumulates group senders (PushName -> jid) for the green bubble
        if eh_grupo:
            for m in out:
                de_mim, push = m[1], m[6]
                if not de_mim and push:
                    self._registra_participante(jid, push)
        out.reverse()  # chronological (oldest first)
        return out

    def mensagens_apos_cursor(self, jid, limite, ts, mid):
        """The next page of `limite` messages OLDER than the row (ts, mid).

        True cursor pagination: the caller passes the (TimestampLong,
        MessageID) of the last row it already has; this returns the
        following `limite` rows (query DESC, returned chronological).
        Unlike the OFFSET path of mensagens(), this loses NO row when a
        (TS, MID) pair is duplicated - the cursor is the actual last row
        seen, so there is no raw/unique counting mismatch at the boundary."""
        lim = min(limite, 400)
        return self._pagina_por_cursor(jid, lim, (ts, mid))

    # -- message search --------------------------------------------------------
    def _termo_seguro(self, termo):
        """Sanitizes a search term for a leading-anchored LIKE.

        Escapes `%`/`_` (LIKE wildcards) and strips single quotes. The
        resulting pattern is always `termo + '%'` (leading-anchored), so
        the prefix indexes (Data(40), MediaCaption(40)) can be used."""
        t = (termo or "").strip().replace("\\", "").replace("%", "").replace("_", "").replace("'", "")
        return t

    def busca_todas(self, termo, limite=400, cursor=None):
        """Global search across ALL conversations: newest first, paginated
        with a true (TimestampLong, MessageID) cursor (same pattern as
        mensagens_apos_cursor — no row loss on duplicated (TS, MID)).

        Matches on message text (Data) and media captions (MediaCaption).
        Returns a dict:
          total   int  — total matching messages (count(*), no BLOBs)
          msgs    list — (MessageID, KeyRemoteJid, Data, PushName,
                          TimestampLong, MediaWaType, MediaCaption),
                         newest first (the cursor walk is DESC)
          fim     bool — True when no older matches remain
        """
        t = self._termo_seguro(termo)
        if not t:
            return {"total": 0, "msgs": [], "fim": True}
        pat = t + "%"
        where = ("KeyRemoteJid IS NOT NULL AND (Data LIKE ? OR "
                 "MediaCaption LIKE ?)")
        params = [pat, pat]
        try:
            total = int(self.msg_db.execute(
                f"SELECT count(*) FROM Messages WHERE {where}",
                params).fetchone()[0])
        except sqlite3.Error:
            return {"total": 0, "msgs": [], "fim": True}
        if cursor is None:
            rows = self.msg_db.execute(
                """SELECT MessageID, KeyRemoteJid, Data, PushName,
                          TimestampLong, MediaWaType, MediaCaption
                   FROM Messages WHERE {w}
                   ORDER BY TimestampLong DESC, MessageID DESC
                   LIMIT ?""".format(w=where),
                (*params, limite)).fetchall()
        else:
            ts_c, mid_c = cursor
            rows = self.msg_db.execute(
                """SELECT MessageID, KeyRemoteJid, Data, PushName,
                          TimestampLong, MediaWaType, MediaCaption
                   FROM Messages WHERE {w}
                     AND (TimestampLong < ? OR (TimestampLong = ? AND MessageID < ?))
                   ORDER BY TimestampLong DESC, MessageID DESC
                   LIMIT ?""".format(w=where),
                (*params, ts_c, ts_c, mid_c, limite)).fetchall()
        if not rows:
            return {"total": total, "msgs": [], "fim": True}
        # defensive dedup (a duplicated (TS, MID) could repeat a row)
        vistos = set()
        unicas = []
        for r in rows:
            if r[0] not in vistos:
                vistos.add(r[0])
                unicas.append(r)
        msgs = list(unicas)
        fim = len(msgs) < limite
        return {"total": total, "msgs": msgs, "fim": fim}

    def busca_conversa(self, jid, termo):
        """Search inside ONE conversation (its FULL history, no cap).

        Same matching as busca_todas (Data + MediaCaption). Returns the
        matching messages CHRONOLOGICAL (oldest first) — the same order
        the chat renders — so the UI can mark them by MessageID and walk
        between them with Up/Down.
        """
        t = self._termo_seguro(termo)
        if not t:
            return []
        pat = t + "%"
        try:
            rows = self.msg_db.execute(
                """SELECT MessageID, KeyRemoteJid, Data, PushName,
                          TimestampLong, MediaWaType, MediaCaption
                   FROM Messages
                   WHERE KeyRemoteJid = ? AND (Data LIKE ? OR MediaCaption LIKE ?)
                   ORDER BY TimestampLong ASC, MessageID ASC""",
                (jid, pat, pat)).fetchall()
        except sqlite3.Error:
            return []
        return [tuple(r) for r in rows]

    def primeiras_mensagens(self, jid, n):
        """The `n` MOST RECENT messages of the conversation, returned in
        chronological order (oldest of the n first).

        Used by the "recent-first" loading strategy: the first page is the
        newest messages (shown immediately), the older ones are paged in
        later (mensagens() with increasing offset). Reuses mensagens() —
        its offset=0 page IS the most recent batch — so the composite
        cursor / BLOB workaround / reactions JOIN all apply unchanged.
        """
        if n <= 0:
            return []
        # one page of at most 400 rows is what mensagens() returns; ask for
        # `n` (it caps at 400 internally, but the offset=0 page is the most
        # recent so n<=400 is one query; n>400 needs a few more pages)
        lote = self.mensagens(jid, n, 0)
        # mensagens() returns the page in chronological (oldest first) order
        # and, with offset=0, it is exactly the most recent `min(n,400)`
        # messages. If n > 400 we would need earlier pages too, but those
        # are OLDER than the first 400 — for the "show the newest" goal we
        # want the newest n, i.e. we must fetch pages 0.. until we have n
        # most-recent rows. mensagens(jid, n, offset) walks backwards from
        # the top, so accumulating offset=0,400,800... gives us the newest
        # rows in order. Keep it simple and cap to what one call gives when
        # n <= 400; otherwise loop.
        if len(lote) < n:
            acc = list(lote)
            off = len(lote)
            while off < n:
                prox = self.mensagens(jid, n, off)
                if not prox:
                    break
                # older page goes to the front (chronological order)
                acc = prox + acc
                off += len(prox)
            return acc[:n]
        return lote[:n]

    def reacoes(self, message_id):
        try:
            return self.msg_db.execute(
                "SELECT RemoteResource, Text FROM Reactions "
                "WHERE ParentMessageID = ? ORDER BY SenderTimestampMs",
                (message_id,)).fetchall()
        except sqlite3.Error:
            return []

    def participante(self, jid_conversa, jid_msg):
        """Sender name in a group: UserStatuses, else Messages.PushName."""
        nome = self._nome_do(jid_msg)
        return nome

    def enviador(self, jid_conversa, push, de_mim):
        """Resolves the real jid of the message's SENDER.

        Needed to paint the account owner's green bubble in groups as
        well (the export does not store KeyParticipantJid, only PushName).
        Resolution order:
          1. PushName -> UserStatuses (known contacts);
          2. PushName -> the conversation's participant map (senders
             already seen in groups, accumulated while loading);
          3. fallback: the PushName itself (treated as a pseudo-jid)."""
        nome = (push or "").strip()
        if not nome:
            return None
        chave = nome.lower()
        for campo in ("ContactName", "PushName", "DisplayNameFromServer"):
            try:
                r = self.con_db.execute(
                    f"SELECT Jid FROM UserStatuses WHERE lower({campo}) = ?",
                    (chave,)).fetchone()
                if r:
                    return r[0]
            except sqlite3.Error:
                break
        mapa = self._participantes.get(str(jid_conversa).lower())
        if mapa and chave in mapa:
            return mapa[chave]
        return chave  # pseudo-jid: matches dono_jid only if it coincides

    def _registra_participante(self, jid_conversa, push, jid=None):
        """Accumulates a group's sender (PushName -> known jid)."""
        nome = (push or "").strip()
        if not nome or not jid_conversa:
            return
        mapa = self._participantes.setdefault(str(jid_conversa).lower(), {})
        chave = nome.lower()
        if jid:
            mapa.setdefault(chave, str(jid))
        elif chave not in mapa:
            # tries to resolve in UserStatuses on the fly (cheap, 1 query)
            try:
                for campo in ("ContactName", "PushName"):
                    r = self.con_db.execute(
                        f"SELECT Jid FROM UserStatuses WHERE lower({campo}) = ?",
                        (chave,)).fetchone()
                    if r:
                        mapa[chave] = str(r[0])
                        break
            except sqlite3.Error:
                pass

    # -- conversation details (details window) ----------------------------
    # MediaWaType categories (same map as the bubble renderer):
    #   1 image · 3 video · 2 audio · 10 sticker · 9 link · 8/1006 document
    _TIPOS_ANEXO = {
        "imagem": (1,), "video": (3,), "audio": (2,),
        "figurinha": (10,), "link": (9,), "documento": (8, 1006),
    }

    def _nome_do_grupo(self, jid_grupo):
        """Group subject for a group jid (Conversations), or None."""
        try:
            r = self.msg_db.execute(
                "SELECT GroupSubject, GroupDescription, GroupOwner, "
                "ParticipantCount FROM Conversations WHERE Jid = ?",
                (jid_grupo,)).fetchone()
            if r:
                return {
                    "nome": r[0] or jid_grupo,
                    "descricao": r[1] or "",
                    "dono": r[2],
                    "membros": int(r[3] or 0),
                }
        except sqlite3.Error:
            pass
        return None

    def grupos_do_contato(self, jid):
        """Groups a contact belongs to (GroupParticipants ∩ Conversations).

        Returns a list of {jid, nome, dono, membros} sorted by name."""
        if not jid:
            return []
        try:
            rows = self.msg_db.execute(
                """SELECT DISTINCT gp.GroupJid, c.GroupSubject
                   FROM GroupParticipants gp
                   LEFT JOIN Conversations c ON c.Jid = gp.GroupJid
                   WHERE gp.MemberJid = ? AND gp.GroupJid LIKE '%@g.us'""",
                (jid,)).fetchall()
        except sqlite3.Error:
            return []
        out = []
        for gjid, _subj in rows:
            info = self._nome_do_grupo(gjid)
            out.append(info or {"jid": gjid, "nome": gjid,
                                "descricao": "", "dono": None, "membros": 0})
        out.sort(key=lambda g: (g["nome"] or "").lower())
        return out

    def info_contato(self, jid):
        """Contact info from UserStatuses (name, phone number, avatar blob)."""
        if not jid:
            return None
        try:
            r = self.con_db.execute(
                "SELECT Jid, ContactName, PushName, DisplayNameFromServer, "
                "UserName, Status FROM UserStatuses WHERE Jid = ?",
                (jid,)).fetchone()
        except sqlite3.Error:
            return None
        if not r:
            return None
        _, contato, push, disp, user, status = r
        nome = contato or push or disp or user or jid.split("@")[0]
        return {
            "jid": jid, "nome": str(nome).strip(),
            "numero": jid.split("@")[0],
            "status": str(status or "").strip(),
        }

    # -- avatars ------------------------------------------------------------
    # A small palette (WhatsApp-like) for the "colored initial" avatars.
    _AVATAR_CORES = [
        "#7f66ff", "#ff66b3", "#ff8a5b", "#e0a41e",
        "#3fa66a", "#2f9fd0", "#b04ad1", "#5b8c2a",
    ]

    @staticmethod
    def _iniciais(nome, maximo=2):
        """Up to `maximo` initials from a name (skips empties/all-caps noise)."""
        nome = (nome or "").strip()
        if not nome:
            return "?"
        partes = [p for p in nome.replace(",", " ").split() if p]
        if not partes:
            return nome[:maximo].upper() or "?"
        # if only one part, take the first chars
        if len(partes) == 1:
            return partes[0][:maximo].upper()
        iniciais = "".join(p[0] for p in partes[:maximo]).upper()
        return iniciais or nome[:maximo].upper()

    @staticmethod
    def _cor_avatar(chave):
        """Deterministic color for a name/jid (stable across calls)."""
        s = str(chave or "")
        h = 0
        for ch in s:
            h = (h * 31 + ord(ch)) & 0xffffffff
        return Nucleo._AVATAR_CORES[h % len(Nucleo._AVATAR_CORES)]

    def avatar_dados(self, jid, nome, tam=64):
        """Avatar image bytes for a jid/name (base64-ready), or None.

        Uses the real profile photo from ChatPictures when the export has
        it (PictureData / ThumbnailData). The ZAPiXDESK export in use leaves
        those empty, so this returns None and the UI falls back to a
        "colored initial" avatar (see Nucleo._iniciais/_cor_avatar)."""
        if not jid:
            return None
        try:
            r = self.con_db.execute(
                "SELECT PictureData, ThumbnailData FROM ChatPictures "
                "WHERE Jid = ?", (jid,)).fetchone()
        except sqlite3.Error:
            return None
        if not r:
            return None
        for blob in (r[0], r[1]):
            if blob and len(blob) > 0:
                return bytes(blob)
        return None

    def info_conversa(self, jid):
        """Full details of a conversation (contact or group) + date range.

        Used by the details window. All queries are defensive: a missing
        table/column degrades to an empty value instead of crashing."""
        eh_grupo = str(jid).lower().endswith("@g.us")
        info = {"jid": jid, "grupo": eh_grupo, "nome": "",
                "numero": "", "descricao": "", "dono": None,
                "membros": 0, "grupos": [], "contato": None,
                "ts_min": None, "ts_max": None, "total": 0}
        # message date range + total (fast: min/max + count, 1 query)
        try:
            r = self.msg_db.execute(
                "SELECT count(*), min(TimestampLong), max(TimestampLong) "
                "FROM Messages WHERE KeyRemoteJid = ?", (jid,)).fetchone()
            if r:
                info["total"] = int(r[0] or 0)
                info["ts_min"] = r[1]
                info["ts_max"] = r[2]
        except sqlite3.Error:
            pass
        if eh_grupo:
            g = self._nome_do_grupo(jid)
            if g:
                info["nome"] = g["nome"]
                info["descricao"] = g["descricao"]
                info["dono"] = g["dono"]
                info["membros"] = g["membros"]
                info["dono_nome"] = self._nome_do(g["dono"])
        else:
            info["contato"] = self.info_contato(jid)
            if info["contato"]:
                info["nome"] = info["contato"]["nome"]
                info["numero"] = info["contato"]["numero"]
            info["grupos"] = self.grupos_do_contato(jid)
        return info

    def anexos_conversa(self, jid, limite_por_tipo=None, progress=None,
                        parar=None):
        """Categorized attachments of a conversation (gallery).

        Scans Messages and groups by MediaWaType into the 6 gallery
        categories. Returns a dict: categoria -> [ {mid, ts, tipo, nome,
        lfu, bin, tamanho, duracao, texto} ] sorted by timestamp (newest
        first). `limite_por_tipo` caps each category (None = no cap);
        `progresso`/`parar` behave like Nucleo.mensagens (called per page,
        return True to stop).

        Uses cursor pagination (LIMIT 400, no OFFSET) instead of one big
        query: SQLite 3.45 fails with "unable to open database file" when a
        single query accumulates ~1MB of BLOBs (BinaryData). The page size
        stays below that threshold, exactly like mensagens()."""
        cols = ("MessageID, TimestampLong, MediaWaType, MediaName, "
                "DataFileName, LocalFileUri, BinaryData, MediaSize, "
                "MediaDurationSeconds, Data")
        out = {k: [] for k in self._TIPOS_ANEXO}
        total = 0
        cursor = None
        while True:
            if parar is not None and parar():
                break
            if cursor is None:
                q = (f"SELECT {cols} FROM Messages WHERE KeyRemoteJid = ? "
                     "ORDER BY TimestampLong DESC, MessageID DESC LIMIT 400")
                p = (jid,)
            else:
                ts_c, mid_c = cursor
                q = (f"SELECT {cols} FROM Messages WHERE KeyRemoteJid = ? "
                     "AND (TimestampLong < ? OR "
                     "(TimestampLong = ? AND MessageID < ?)) "
                     "ORDER BY TimestampLong DESC, MessageID DESC LIMIT 400")
                p = (jid, ts_c, ts_c, mid_c)
            try:
                rows = self.msg_db.execute(q, p).fetchall()
            except sqlite3.Error:
                break
            if not rows:
                break
            for mid, ts, tipo, nome, nome_arq, lfu, _bin, tam, dur, texto in rows:
                for cat, tipos in self._TIPOS_ANEXO.items():
                    if tipo in tipos:
                        out[cat].append({
                            "mid": mid, "ts": ts, "tipo": tipo,
                            "nome": nome or nome_arq or "",
                            "lfu": lfu, "bin": _bin or None,
                            "tamanho": int(tam or 0),
                            "duracao": int(dur or 0),
                            "texto": texto or "",
                        })
                        break
            cursor = (rows[-1][1], rows[-1][0])
            total += len(rows)
            if progress is not None:
                progress(total)
            if total > 300000:  # safety cap
                break
        if limite_por_tipo:
            out = {k: v[:limite_por_tipo] for k, v in out.items()}
        return out

    def fechar(self):
        for con in (self.msg_db, self.con_db):
            try:
                con.close()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

# WhatsApp palette (light theme)
COR_FUNDO_CHAT = "#efeae2"   # chat background (beige)
COR_EU = "#d9fdd3"           # owner bubble (light green)
COR_OUTRO = "#ffffff"        # peer bubble (white)
COR_LISTA = "#ffffff"        # conversation list background
COR_LISTA_SEL = "#d9fdd3"    # selected list item
COR_SEPARADOR = "#d5d5d5"    # date separator line
COR_BOLSA_DATA = "#ffffff"   # date "pill" background
COR_TEXTO = "#111111"
COR_SUAVE = "#667781"        # WhatsApp gray
COR_HORA = "#667781"
COR_REMETENTE = "#5268f5"    # sender name in group (blue)
COR_REMETENTE_EU = "#0b843b" # "You" in group (green)
COR_URL = "#1a6fb5"
COR_CHECK = "#53bdeb"        # read receipts (✓✓)
COR_TITULO = "#008069"       # WhatsApp green (header)
COR_HEADER_BG = "#008069"


class App:
    def __init__(self, raiz):
        self.raiz = raiz
        raiz.title(APP_TITLE)
        raiz.geometry("1100x720")
        raiz.minsize(760, 480)
        definir_icone_janela(raiz)  # window icon (logo without the text)

        self.nucleo = None
        self.zip_path = None
        self.imagens_foto = []  # keeps refs to the PhotoImages
        self._thumbs = {}       # hash(arquivo+ext) -> (PIL.Image, caminho)
        self._fonte = None
        self._fonte_negrito = None
        self._fonte_suave = None
        self._fonte_titulo = None

        self._montar_ui()
        self._abrir_estado()

    # -- UI construction -------------------------------------------------------
    def _montar_ui(self):
        self.raiz.configure(bg=COR_LISTA)

        # Green header (WhatsApp style)
        header = tk.Frame(self.raiz, bg=COR_HEADER_BG, height=52)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="💬", bg=COR_HEADER_BG, fg="white",
                 font=("DejaVu Sans", 16)).pack(side="left", padx=(12, 4))
        self.lbl_app = tk.Label(header, text=APP_TITLE, bg=COR_HEADER_BG,
                                fg="white", font=("DejaVu Sans", 11, "bold"),
                                anchor="w")
        self.lbl_app.pack(side="left", fill="x", expand=True)
        tk.Button(header, text="📂 Open…", command=self.abrir,
                  bg="#128c7e", fg="white", relief="flat",
                  activebackground="#0b6e63", activeforeground="white",
                  padx=10, font=("DejaVu Sans", 9)).pack(side="right", padx=10, pady=10)
        self.lbl_status = tk.Label(header, text="No export opened.",
                                   bg=COR_HEADER_BG, fg="#d6f5ef",
                                   font=("DejaVu Sans", 8))
        self.lbl_status.pack(side="right", padx=6)
        self.barra_hash = ttk.Progressbar(header, length=140, mode="determinate")
        self.barra_hash.pack(side="right", padx=4, pady=10)

        corpo = tk.Frame(self.raiz, bg=COR_FUNDO_CHAT)
        corpo.pack(fill="both", expand=True)
        # Column 0 (list): width controlled by the draggable separator.
        # Column 1: fixed separator (6px).
        # Column 2 (chat): takes the remaining space (weight=1).
        corpo.columnconfigure(0, weight=0, minsize=200)
        corpo.columnconfigure(1, weight=0)
        corpo.columnconfigure(2, weight=1, minsize=320)
        corpo.rowconfigure(0, weight=1)

        # Conversation panel (WhatsApp-style list: white background)
        # Initial width 320px; resizable by dragging the separator.
        self.painel_lista = tk.Frame(corpo, width=320, bg=COR_LISTA)
        self.painel_lista.grid(row=0, column=0, sticky="nsew")
        self.painel_lista.grid_propagate(False)
        painel = self.painel_lista
        # filter bar
        barra_busca = tk.Frame(painel, bg="#f0f2f5")
        barra_busca.pack(fill="x")
        self.ed_busca = tk.Entry(barra_busca, bg="#f0f2f5",
                                 relief="flat", font=("DejaVu Sans", 9),
                                 highlightthickness=1, highlightbackground=COR_SEPARADOR)
        self.ed_busca.insert(0, "🔍  Search conversations…")
        self.ed_busca.configure(fg=COR_SUAVE)
        self.ed_busca.pack(fill="x", padx=8, pady=6)
        self.ed_busca.bind("<KeyRelease>", self._filtrar)
        self.ed_busca.bind("<FocusIn>", self._busca_foco_in)
        self.ed_busca.bind("<FocusOut>", self._busca_foco_out)
        self.ed_busca._placeholder = True

        self.cb_conversas = tk.Listbox(
            painel, activestyle="none", exportselection=False,
            bg=COR_LISTA, fg=COR_TEXTO, selectbackground=COR_LISTA_SEL,
            selectforeground=COR_TEXTO, font=("DejaVu Sans", 10),
            highlightthickness=0, bd=0, relief="flat")
        self.cb_conversas.pack(fill="both", expand=True)
        self.cb_conversas.bind("<<ListboxSelect>>", self._ao_selecionar)

        # Draggable vertical separator between the list and the chat.
        # Uses a tk.Frame (which accepts mouse binds) instead of
        # ttk.Separator (which does not propagate mouse events well on Tk 8.6).
        _cursor_sep = "sb_left_right"   # cursor for a vertical separator (X11)
        try:
            self.sep_lista_chat = tk.Frame(corpo, bg=COR_SEPARADOR, width=6,
                                           cursor=_cursor_sep)
        except Exception:
            # Fallback without a special cursor (e.g.: cursor unavailable).
            self.sep_lista_chat = tk.Frame(corpo, bg=COR_SEPARADOR, width=6)
        self.sep_lista_chat.grid(row=0, column=1, sticky="ns")
        self.sep_lista_chat.grid_propagate(False)
        self.sep_lista_chat.bind("<Button-1>", self._sep_iniciar)
        self.sep_lista_chat.bind("<B1-Motion>", self._sep_mover)
        self.sep_lista_chat.bind("<ButtonRelease-1>", self._sep_soltar)
        # Invisible, wider drag region (makes it easier to grab the separator).
        self._sep_largura_atual = 320
        self._arrastando_sep = False

        # Chat area (WhatsApp beige background)
        chat_frame = tk.Frame(corpo, bg=COR_FUNDO_CHAT)
        chat_frame.grid(row=0, column=2, sticky="nsew")
        self.cb_chat = tk.Canvas(chat_frame, bg=COR_FUNDO_CHAT, highlightthickness=0)
        barra_role = tk.Scrollbar(chat_frame, orient="vertical",
                                  command=self.cb_chat.yview)
        self.cb_chat.configure(yscrollcommand=barra_role.set)
        self.cb_chat.pack(side="left", fill="both", expand=True)
        barra_role.pack(side="right", fill="y")
        self.fundo_chat = tk.Frame(self.cb_chat, bg=COR_FUNDO_CHAT)
        self.fundo_chat.pack(fill="both", expand=True)
        self.cb_chat.create_window((0, 0), window=self.fundo_chat, anchor="nw")
        self.fundo_chat.bind("<Configure>", self._ao_redimensionar)
        # Mouse-wheel scrolling on the chat canvas (and, via _rebindar_scroll,
        # on messages created afterwards — see _reflow).
        self._vincular_scroll_mouse(self.cb_chat)
        self._vincular_scroll_mouse(self.fundo_chat)
        self._chat_largura = None
        # Text-wrap cache: wraplength already computed per conversation.
        self._wrap_cache = 440
        # conversation header (white, like WhatsApp)
        self.header_chat = tk.Frame(chat_frame, bg="white", height=44)
        self.header_chat.pack(side="top", fill="x")
        self.header_chat.pack_propagate(False)
        # Chat bubble icon (ZAPiXDesk logo) to the left of the title.
        self.icone_titulo = tk.Label(self.header_chat, text="", bg="white")
        self._montar_icone_titulo(self.icone_titulo)
        self.icone_titulo.pack(side="left", padx=(12, 6), pady=6)
        self.lbl_titulo = tk.Label(self.header_chat, text="Select a conversation",
                                   bg="white", fg=COR_TEXTO, anchor="w",
                                   font=("DejaVu Sans", 11, "bold"))
        self.lbl_titulo.pack(side="left", padx=(0, 8), fill="x", expand=True)
        # Loading progress bar (hidden until needed)
        self.barra_carregando = ttk.Progressbar(self.header_chat, length=120,
                                                mode="determinate")
        self.barra_carregando.pack_forget()
        # Export button (HTML/PDF)
        self.bt_exportar = tk.Button(self.header_chat, text="⬇ Export",
                                     command=self._exportar_conversa,
                                     relief="flat", bg="#008069", fg="white",
                                     padx=8, font=("DejaVu Sans", 9))
        self.bt_exportar.pack(side="right", padx=8)

    def _montar_icone_titulo(self, widget):
        """Creates the chat bubble icon (Z in the bubble, ZAPiXDesk style)."""
        if not HAS_PIL:
            return
        try:
            tam = 32
            balao = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
            d = ImageDraw.Draw(balao)
            # rounded green bubble
            d.rounded_rectangle([2, 2, tam - 2, tam - 6], radius=8,
                                fill=(0, 128, 105, 255))
            # bubble tail (bottom-left triangle)
            d.polygon([(10, tam - 7), (16, tam - 7), (8, tam - 1)],
                      fill=(0, 128, 105, 255))
            # white letter Z (PIL TrueType font, not tkinter's)
            f = _fonte_pil(15, negrito=True)
            if f is not None:
                d.text((tam / 2, (tam - 4) / 2), "Z", fill="white",
                       font=f, anchor="mm")
            else:
                d.text((tam / 2 - 5, (tam - 4) / 2 - 7), "Z", fill="white")
            foto = ImageTk.PhotoImage(balao)
            widget._foto_icone = foto
            widget.config(image=foto)
        except Exception:
            log_excecao("_montar_icone_titulo", tb=traceback.format_exc())

    def _atualizar_titulo_janela(self, nome=None):
        """Shows in the window title which conversation is open."""
        if nome:
            self.raiz.title(f"💬 {nome} — ZAPiXDeskViewer")
        else:
            self.raiz.title(APP_TITLE)

    def _barra_carregando_on(self, maximum=None, value=None):
        """Shows the progress bar (always deterministic) in the header."""
        self.barra_carregando.configure(mode="determinate",
                                        maximum=maximum or 100)
        if value is not None:
            self.barra_carregando.configure(value=value)
        if not self.barra_carregando.winfo_ismapped():
            self.barra_carregando.pack(side="left", padx=(4, 4), pady=8)

    def _barra_carregando_progresso(self, maximum, value):
        self.barra_carregando.configure(mode="determinate",
                                        maximum=maximum or 1, value=value)

    def _barra_carregando_off(self):
        """Oculta a barra de progresso."""
        self.barra_carregando.pack_forget()
        self.barra_carregando.configure(value=0)

    def _fontes(self):
        if self._fonte is None:
            self._fonte = tfont.Font(family="DejaVu Sans", size=10)
            self._fonte_negrito = tfont.Font(family="DejaVu Sans", size=10, weight="bold")
            self._fonte_suave = tfont.Font(family="DejaVu Sans", size=9)
            self._fonte_titulo = tfont.Font(family="DejaVu Sans", size=11, weight="bold")
        return (self._fonte, self._fonte_negrito, self._fonte_suave, self._fonte_titulo)

    # -- redimensionamento -----------------------------------------------------
    # Batch of widgets per reflow tick (keeps the UI responsive).
    _LOTE_REFLOW = 60

    def _ao_redimensionar(self, _evento=None):
        """Adjusts the chat when the window is resized: scroll region,
        text wrapping (wraplength), and image scaling. The reflow is done in
        batches (via after) so large chats do not freeze the UI."""
        try:
            largura = self.fundo_chat.winfo_width()
        except Exception:
            return
        if largura < 120:
            return
        # scroll region follows the content
        try:
            self.cb_chat.configure(scrollregion=self.cb_chat.bbox("all"))
        except Exception:
            pass
        # usable width for text (subtracts bubble margins)
        nova = max(180, min(largura - 90, 560))
        if nova == self._chat_largura:
            return
        self._chat_largura = nova
        self._wrap_cache = nova
        alvo = max(80, min(nova, 460))
        # Incremental reflow: process the widgets in batches.
        if getattr(self, "_reflow_andamento", False):
            return  # a reflow is already running
        self._reflow_andamento = True
        self._reflow_largura = nova
        self._reflow_alvo = alvo
        # Collect the root children only once
        self._reflow_fila = list(self.fundo_chat.winfo_children())
        self._reflow_idx = 0
        self.raiz.after(0, self._reflow_proximo_lote)

    def _reflow_proximo_lote(self):
        """Processes the next batch of reflow widgets and schedules the following one."""
        try:
            largura = self._reflow_largura
            alvo = self._reflow_alvo
            fila = self._reflow_fila
            fim = min(self._reflow_idx + self._LOTE_REFLOW, len(fila))
            for i in range(self._reflow_idx, fim):
                self._reflow(fila[i], largura, alvo)
            self._reflow_idx = fim
            if fim < len(fila):
                self.raiz.after(0, self._reflow_proximo_lote)
            else:
                self._reflow_andamento = False
                # Adjust the final scroll region
                try:
                    self.cb_chat.configure(
                        scrollregion=self.cb_chat.bbox("all"))
                except Exception:
                    pass
        except Exception:
            self._reflow_andamento = False

    def _reflow(self, widget, largura, alvo):
        """Applies wraplength (via cache) and rescales photos only when the
        target size actually changed. Also makes sure every widget allows
        mouse-wheel scrolling (via bindtags) — see _rebindar_scroll."""
        try:
            self._rebindar_scroll(widget)
            if isinstance(widget, tk.Label):
                foto = getattr(widget, "_foto", None)
                if foto is not None:
                    if getattr(foto, "_alvo", 0) != alvo:
                        self._reescalar(widget, alvo)
                elif getattr(widget, "_txt_cache", None) is not None:
                    widget.configure(wraplength=largura)
            else:
                for filho in widget.winfo_children():
                    self._reflow(filho, largura, alvo)
        except Exception:
            pass

    # -- draggable separator (width of the conversation list) -------------------
    def _sep_iniciar(self, evento):
        """Starts dragging the separator; records the initial mouse position."""
        self._arrastando_sep = True
        self._sep_x_inicial = evento.x_root
        self._sep_largura_inicial = self.painel_lista.winfo_width()
        self.sep_lista_chat.configure(bg="#8fd3c7")  # highlight while dragging

    def _sep_mover(self, evento):
        """Adjusts the list column width as the mouse moves."""
        if not self._arrastando_sep:
            return
        dx = evento.x_root - self._sep_x_inicial
        nova = self._sep_largura_inicial + dx
        # Limits: minimum 200px, maximum = 60% of the window.
        max_larg = int(self.raiz.winfo_width() * 0.6)
        nova = max(200, min(nova, max_larg))
        self.painel_lista.configure(width=nova)
        self._sep_largura_atual = nova

    def _sep_soltar(self, _evento):
        """Ends the drag; restores the separator color."""
        self._arrastando_sep = False
        self.sep_lista_chat.configure(bg=COR_SEPARADOR)
        # Recalculates the chat wraplength with the new width.
        self._ao_redimensionar()

    # -- mouse-wheel scrolling in the chat --------------------------------------
    def _vincular_scroll_mouse(self, widget):
        """Binds <MouseWheel> (Windows/macOS) and <Button-4/5> (Linux) to
        scroll the chat canvas."""
        widget.bind("<MouseWheel>", self._ao_rolar)   # Windows/macOS
        widget.bind("<Button-4>", self._ao_rolar)     # Linux: scroll up
        widget.bind("<Button-5>", self._ao_rolar)     # Linux: scroll down

    def _rebindar_scroll(self, widget):
        """Ensures chat children allow mouse-wheel scrolling.

        The Tk 8.6 on this system does not expose bind(all=), so we add
        fundo_chat to each widget's bindtags: scroll events that happen over
        a message fall through to fundo_chat's handlers. Called from _reflow
        (which walks the tree on every reflow)."""
        try:
            tags = widget.bindtags()
            tags = tuple(t for t in tags if str(t) != str(self.fundo_chat))
            # fundo_chat first (after the widget itself) => its handler runs
            widget.bindtags((widget, self.fundo_chat) + tags)
        except Exception:
            pass
        for filho in widget.winfo_children():
            self._rebindar_scroll(filho)

    def _ao_rolar(self, evento):
        """Scrolls the chat canvas according to the mouse wheel."""
        # positive delta = scroll up; negative = scroll down.
        if evento.num == 4:      # Linux: button 4 (up)
            delta = -1
        elif evento.num == 5:    # Linux: button 5 (down)
            delta = 1
        else:                    # MouseWheel: delta already has the right sign
            delta = -1 if (getattr(evento, "delta", 0) > 0) else 1
        # yview_scroll with units=1 scrolls one "line" per tick.
        try:
            self.cb_chat.yview_scroll(delta, "units")
        except Exception:
            pass

    def _rolar_para_fim(self):
        """Scrolls the chat to the most recent message (used after the reflow)."""
        try:
            self.cb_chat.yview_moveto(1.0)
        except Exception:
            pass

    def _reescalar(self, widget, alvo):
        """Rescales a label's photo to fit the target (px)."""
        if not HAS_PIL:
            return
        foto = getattr(widget, "_foto", None)
        if foto is None:
            return
        im = getattr(foto, "_im_orig", None)
        if im is None:
            return
        # thumbnail on a temporary copy so as not to corrupt the original
        novo = im.copy()
        if novo.width > alvo:
            novo.thumbnail((alvo, alvo))
        novo_foto = ImageTk.PhotoImage(novo)
        novo_foto._im_orig = novo
        novo_foto._alvo = alvo
        widget.configure(image=novo_foto)
        widget._foto = novo_foto
        self.imagens_foto.append(novo_foto)

    # -- opening flow -----------------------------------------------------------
    def abrir(self):
        zip_path = fdlg.askopenfilename(
            title="Choose the exported .zip",
            filetypes=[("Zip file", "*.zip"), ("All", "*.*")])
        if not zip_path:
            return
        base, _ = os.path.splitext(zip_path)
        txt_path = base + ".md5.txt"
        if not os.path.exists(txt_path):
            txt_path = fdlg.askopenfilename(
                title="Choose the .md5.txt file",
                filetypes=[("MD5", "*.txt"), ("All", "*.*")])
        if not txt_path:
            return
        threading.Thread(target=self._fluxo_abertura, args=(zip_path, txt_path),
                         daemon=True).start()

    def _chamar(self, fn, *args):
        """Schedules a callback on Tk's mainloop (main thread).

        Uses after(0) so the callback goes straight into the event loop,
        with no intermediate queue — this keeps the UI responsive even
        with several conversations loaded in sequence."""
        self.raiz.after(0, lambda: fn(*args))

    def _fluxo_abertura(self, zip_path, txt_path):
        t0 = time.time()
        log("Open started: %s", zip_path)
        try:
            esperado = ler_md5_esperado(txt_path)
            if not esperado:
                raise ValueError("MD5 not found in the .txt file.")
            log("Expected MD5: %s", esperado)
            parar = {"v": False}
            self._chamar(self._set_status, "Verifying MD5…")
            real = md5_arquivo(
                zip_path,
                progresso=lambda l, t: self._chamar(self._set_hash, l, t),
                parar=lambda: parar["v"])
            if real is None:
                log("Open cancelled (stop)")
                return
            if real != esperado:
                log("MD5 MISMATCH: actual=%s", real)
                self._chamar(self._hash_erro, esperado, real)
                return
            log("MD5 OK in %.1fs; extracting databases…", time.time() - t0)
            self._chamar(self._set_status, "Extracting databases…")
            nucleo = Nucleo(zip_path)
            log("Open finished in %.1fs", time.time() - t0)
            # attachments extracted in background (does not block the list)
            threading.Thread(target=self._extrair_anexos_bg, args=(nucleo,),
                             daemon=True).start()
            self._chamar(self._pronto, nucleo, zip_path)
        except Exception as exc:
            log_excecao("fluxo_abertura", exc, traceback.format_exc())
            self._chamar(self._erro_abertura, str(exc))

    def _set_status(self, texto):
        self.lbl_status.config(text=texto)

    def _set_hash(self, lido, total):
        self.barra_hash.config(maximum=total or 1, value=lido)

    def _set_anexos(self, extraidos, total):
        self.lbl_status.config(text=f"Extracting attachments… {extraidos}/{total}")

    def _hash_erro(self, esperado, real):
        self.barra_hash.config(value=0)
        self.lbl_status.config(text="⚠ MD5 mismatch")
        mbox.showerror(
            APP_TITLE,
            "The file's MD5 does not match.\n\n"
            f"Expected: {esperado}\nFound: {real}\n\n"
            "The file may be corrupted or incomplete.")

    def _erro_abertura(self, msg):
        self.lbl_status.config(text="Error opening.")
        mbox.showerror(APP_TITLE, "Failed to open the export:\n\n" + msg)

    def _extrair_anexos_bg(self, nucleo):
        """Extracts attachments to the cache in the background (after listing)."""
        try:
            nucleo._extraer_anexos(
                progresso=lambda o, t: self._chamar(self._set_anexos, o, t),
                parar=lambda: False)
        except Exception as exc:
            log_excecao("extrair_anexos_bg", exc, traceback.format_exc())

    def _pronto(self, nucleo, zip_path):
        self.nucleo = nucleo
        self.zip_path = zip_path
        self.barra_hash.config(value=0)
        self.lbl_status.config(
            text=f"{len(nucleo.conversas)} conversas · "
            f"{os.path.basename(zip_path)}")
        self._preencher_conversas()
        self.lbl_titulo.config(text="Select a conversation on the left.")
        self._atualizar_titulo_janela()

    # -- conversation list ------------------------------------------------------
    def _preencher_conversas(self, filtro=""):
        self.cb_conversas.delete(0, "end")
        f = filtro.strip().lower()
        for c in self.nucleo.conversas:
            if f and f not in c["nome"].lower() and f not in c["jid"].lower():
                continue
            rotulo = ("👥 " if c["grupo"] else "💬 ") + c["nome"]
            if c["nao_lidas"]:
                rotulo += f"  ({c['nao_lidas']})"
            self.cb_conversas.insert("end", rotulo)
        self._filtros = [c for c in self.nucleo.conversas
                         if not f or f in c["nome"].lower() or f in c["jid"].lower()]

    def _filtrar(self, _evento=None):
        self._preencher_conversas(self._texto_busca())

    def _texto_busca(self):
        if getattr(self.ed_busca, "_placeholder", False):
            return ""
        return self.ed_busca.get()

    def _busca_foco_in(self, _e=None):
        if getattr(self.ed_busca, "_placeholder", False):
            self.ed_busca.delete(0, "end")
            self.ed_busca.configure(fg=COR_TEXTO)
            self.ed_busca._placeholder = False

    def _busca_foco_out(self, _e=None):
        if not self.ed_busca.get().strip():
            self.ed_busca.insert(0, "🔍  Search conversation…")
            self.ed_busca.configure(fg=COR_SUAVE)
            self.ed_busca._placeholder = True

    def _ao_selecionar(self, _evento=None):
        sel = self.cb_conversas.curselection()
        if not sel or not hasattr(self, "_filtros"):
            return
        c = self._filtros[sel[0]]
        threading.Thread(target=self._carregar_conversa, args=(c,),
                         daemon=True).start()

    _LIMITE_PAGINA = 2000

    def _carregar_conversa(self, c, offset=0, append=False):
        t0 = time.time()
        log("Opening conversation %r (%s) offset=%d append=%s",
            c["nome"], c["jid"], offset, append)
        try:
            total = self.nucleo.total_mensagens(c["jid"])
            msgs = self.nucleo.mensagens(c["jid"], self._LIMITE_PAGINA, offset)
            self._conversa_atual = c
            self._offset_atual = offset + len(msgs)
            self._total_atual = total
            self._ha_mais = (offset + len(msgs)) < total
            log("  %d messages (of %d) in %.2fs",
                len(msgs), total, time.time() - t0)
            self._chamar(self._renderizar_chat, c, msgs, t0,
                         self._ha_mais, offset, append)
        except Exception as exc:
            log_excecao("carregar_conversa", exc, traceback.format_exc())
            self._chamar(self._barra_carregando_off)
            self._chamar(self._erro_chat, traceback.format_exc(limit=3))

    def _carregar_mais(self):
        """Loads the next page (earlier messages) and APPENDS to the top,
        without destroying what is already visible."""
        c = getattr(self, "_conversa_atual", None)
        if not c or getattr(self, "_render_ocupado", False):
            return
        self._render_ocupado = True
        self.lbl_status.config(text="Loading earlier messages…")
        threading.Thread(target=self._carregar_conversa,
                         args=(c, self._offset_atual, True), daemon=True).start()

    def _erro_chat(self, tb):
        self.lbl_titulo.config(text="Error loading conversation")
        mbox.showerror(APP_TITLE, "Failed to load messages:\n\n" + tb)

    # -- chat rendering -----------------------------------------------------------
    # Batch of messages per UI "tick" (keeps the interface responsive).
    _LOTE_RENDER = 40

    def _renderizar_chat(self, c, msgs, t0=None, ha_mais=False, offset=0,
                         append=False):
        """Prepares the area and renders messages in batches (via self.after),
        so large conversations do not freeze the UI. With
        `append=True`, the older page is inserted above, without destroying
        what is already visible ("Load earlier messages" button)."""
        t1 = time.time()
        fonte, negrito, suave, titulo = self._fontes()
        if not append:
            for w in self.fundo_chat.winfo_children():
                w.destroy()
            self.imagens_foto.clear()
            self._ultimo_dia = None
            self.lbl_titulo.config(
                text=("👥 " if c["grupo"] else "💬 ") + c["nome"], font=titulo,
                fg=COR_TEXTO)
            self._atualizar_titulo_janela(c["nome"])
        else:
            # remove the "load more" button from the top
            for w in self.fundo_chat.winfo_children():
                if isinstance(w, tk.Button) and "anteriores" in str(w.cget("text")):
                    w.destroy()
        if self._chat_largura:
            self._wrap_cache = self._chat_largura
        self._render_ocupado = True
        # Deterministic bar: each batch of 40 messages advances the bar.
        self._barra_carregando_on(maximum=len(msgs) or 1, value=0)
        self._render_idx = 0
        self._render_msgs = msgs
        self._render_n = len(msgs)
        self._render_c = c
        self._render_fontes = (fonte, negrito, suave)
        self._render_t0 = t1
        self._render_t1 = t0
        self._render_ha_mais = ha_mais
        self._render_offset = offset
        self._render_append = append
        if not msgs:
            if not append:
                tk.Label(self.fundo_chat, text="No messages.",
                         bg=COR_FUNDO_CHAT, fg=COR_SUAVE).pack(pady=30)
            self.lbl_status.config(text="No messages.")
            self._barra_carregando_off()
            log("  empty render")
            return
        self.raiz.after(0, self._render_proximo_lote)

    def _render_proximo_lote(self):
        """Renders the next batch of messages and schedules the following one.

        Does not call update_idletasks() here: items with side="top"
        stack on their own, and processing the event loop mid-batch fired
        recursive <Configure> from _ao_redimensionar (the app froze)."""
        try:
            fonte, negrito, suave = self._render_fontes
            c = self._render_c
            msgs = self._render_msgs
            append = getattr(self, "_render_append", False)
            fim = min(self._render_idx + self._LOTE_RENDER, len(msgs))
            for i in range(self._render_idx, fim):
                msg = msgs[i]
                de_mim = bool(msg[1])
                ts = msg[5]
                dia = hora(ts)
                if dia:
                    dia_chave = dia[:10]
                    if dia_chave != self._ultimo_dia:
                        self._ultimo_dia = dia_chave
                        cont = tk.Frame(self.fundo_chat, bg=COR_FUNDO_CHAT)
                        cont.pack(side="top", fill="x", pady=7)
                        tk.Label(cont, text=dia, bg=COR_BOLSA_DATA,
                                 fg=COR_SUAVE, font=suave,
                                 padx=10, pady=2, relief="flat").pack()
                self._bloco_msg(self.fundo_chat, fonte, negrito, suave,
                                de_mim, c, msg, append=append)
            self._render_idx = fim
            self.lbl_status.config(
                text=f"Loading messages… {fim}/{len(msgs)}")
            self._barra_carregando_progresso(len(msgs) or 1, fim)
            if fim < len(msgs):
                self.raiz.after(0, self._render_proximo_lote)
            else:
                # button to load earlier messages, if any
                if self._render_ha_mais:
                    bt = tk.Button(self.fundo_chat,
                                   text="⬆  Load earlier messages",
                                   command=self._carregar_mais,
                                   relief="flat", bg=COR_FUNDO_CHAT,
                                   fg=COR_SUAVE, font=suave)
                    bt.pack(side="top", pady=8)
                # keeps the scroll position where the user was
                if append:
                    self.cb_chat.yview_moveto(0.0)
                else:
                    # Scrolls to the most recent message. Uses after_idle to
                    # ensure the reflow (wraplength/photos) has been processed
                    # before moving the scroll — otherwise the content may
                    # still be measured and "1.0" would land in the middle.
                    self.cb_chat.yview_moveto(1.0)
                    self.cb_chat.after_idle(self._rolar_para_fim)
                dt = time.time() - self._render_t0
                log("  rendered %d msgs in %.2fs (total %.2fs)",
                    len(msgs), dt,
                    (time.time() - self._render_t1) if self._render_t1 else -1)
                self.lbl_status.config(
                    text=f"{len(msgs)} messages · "
                         f"{os.path.basename(self.zip_path or '')}")
                self._barra_carregando_off()
            self._render_ocupado = False
        except Exception as exc:
            log_excecao("render_proximo_lote", exc, traceback.format_exc())
            self.lbl_status.config(text="Error rendering.")
            self._barra_carregando_off()
            self._render_ocupado = False
            self._render_idx = len(msgs)  # to stop the batch loop


    def _bloco_msg(self, pai, fonte, negrito, suave, de_mim, conversa, msg,
                   append=False):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, reacoes) = msg
        # Width already sized for this conversation (wraplength cache):
        # avoids the _reflow sweep on every render batch.
        wrap = self._wrap_cache if self._wrap_cache else self.LARGURA_MAX_BALAO
        # green bubble: messages from the account owner (DM or group)
        cor = COR_EU if (de_mim or self._eh_dono(conversa, de_mim, push)) \
            else COR_OUTRO
        # Single bubble (WhatsApp style): sender + content + footer.
        # Full row (fill="x") to stack in a column; the bubble is
        # left-aligned (others) or right-aligned (me) inside the row.
        # In append mode (earlier page), insert at the TOP of the visible area.
        linha = tk.Frame(pai, bg=COR_FUNDO_CHAT)
        if append:
            linha.pack(side="top", fill="x", pady=(1, 1), after=None)
        else:
            linha.pack(side="top", fill="x", pady=(1, 1))
        balao = tk.Frame(linha, bg=cor, padx=8, pady=3,
                          highlightbackground="#c9c9c9",
                          highlightthickness=0)
        balao.pack(side="left" if de_mim else "right", anchor="n",
                   padx=(14, 6) if de_mim else (6, 14), pady=(1, 1))

        # sender (groups only): the account owner appears with their own
        # name (green), like in DMs — never "You"
        eh_dono = self._eh_dono(conversa, de_mim, push)
        if conversa["grupo"]:
            if eh_dono:
                remetente = self.nucleo.dono_nome or push or ""
            else:
                remetente = push or ""
            if remetente:
                tk.Label(balao, text=remetente,
                         fg=COR_REMETENTE_EU if eh_dono else COR_REMETENTE,
                         font=negrito, bg=cor, anchor="w").pack(
                    side="top", fill="x")

        # content by type (inside the bubble) — the color already handles the owner
        if tipo == 0 or tipo in (29, 1027):
            if texto:
                self._balao_texto(balao, texto, de_mim, fonte, wrap, cor)
        elif tipo == 1:
            self._balao_imagem(balao, msg, de_mim, fonte, suave, cor)
        elif tipo in (2, 3):
            self._balao_midia(balao, msg, de_mim, fonte, suave,
                              audio=(tipo == 2), cor=cor)
        elif tipo == 4:
            self._balao_vcard(balao, msg, fonte, suave)
        elif tipo == 8:
            self._balao_documento(balao, msg, de_mim, fonte, suave, cor)
        elif tipo == 9:
            self._balao_link(balao, msg, de_mim, fonte, suave, cor)
        elif tipo == 10:
            self._balao_figurinha(balao, msg, de_mim, fonte, suave, cor)
        elif tipo == 1006:
            self._balao_documento(balao, msg, de_mim, fonte, suave, cor)
        else:
            if texto:
                self._balao_texto(balao, texto, de_mim, fonte, wrap, cor)
            else:
                self._balao_texto(balao, self.nucleo._resumo_tipo(tipo),
                                  de_mim, fonte, wrap, cor)
        # caption inside the bubble
        if legenda and tipo not in (9,):
            self._balao_texto(balao, legenda, de_mim, suave, wrap, cor)
        # footer: reactions + time + read receipts
        reag_txt = ""
        if reacoes:
            vistos = {}
            ordem = []
            for jid_r, texto_r in reacoes:
                k = texto_r or "✅"
                if k not in vistos:
                    vistos[k] = 0
                    ordem.append(k)
                vistos[k] += 1
            reag_txt = "  ".join(
                f"{k} {vistos[k]}" if vistos[k] > 1 else k for k in ordem)
        hora_txt = self._hora_curta(ts)
        check = "  ✓✓" if de_mim else ""
        rodape = tk.Frame(balao, bg=cor)
        rodape.pack(side="top", fill="x", anchor="e")
        if reag_txt:
            tk.Label(rodape, text=reag_txt, fg=COR_SUAVE, font=suave,
                     bg=cor).pack(side="left")
        tk.Label(rodape, text=hora_txt + check, fg=COR_HORA, font=suave,
                 bg=cor).pack(side="right")

    @staticmethod
    def _hora_curta(data_ms):
        """HH:MM only (WhatsApp style)."""
        if not data_ms:
            return ""
        try:
            data_ms = int(data_ms)
        except Exception:
            return ""
        if data_ms > 10 ** 17:
            data_ms //= 1000
        elif data_ms > 10 ** 15:
            data_ms //= 1000000
        if data_ms > 10 ** 14:
            data_ms //= 1000
        import datetime
        try:
            return datetime.datetime.fromtimestamp(data_ms / 1000.0).strftime("%H:%M")
        except Exception:
            return ""

    LARGURA_MAX_BALAO = 440  # px; o wraplength se ajusta a menos que isso

    def _eh_dono(self, conversa, de_mim, push):
        """True when the message is from the account owner.

        DM: the owner is the "de_mim" side itself (KeyFromMe).
        Group: resolves the sender's PushName to a jid (UserStatuses
        or the conversation's participant map) and compares it with dono_jid."""
        try:
            if not self.nucleo or not self.nucleo.dono_jid:
                return False
            if de_mim:
                return True
            jid_rem = self.nucleo.enviador(
                conversa["jid"], push, False)
            if jid_rem:
                return str(jid_rem).lower() == \
                    str(self.nucleo.dono_jid).lower()
        except Exception:
            pass
        return False

    def _balao_texto(self, pai, texto, de_mim, fonte, wrap=None, cor=None):
        """Creates the text label with wraplength already sized (cache)."""
        if cor is None:
            cor = COR_EU if de_mim else COR_OUTRO
        if wrap is None:
            wrap = self._wrap_cache or self.LARGURA_MAX_BALAO
        t = tk.Label(pai, text=texto, bg=cor, fg=COR_TEXTO, font=fonte,
                     anchor="w", justify="left", wraplength=wrap)
        t._txt_cache = wrap
        t.pack(side="top", fill="x")

    def _balao_imagem(self, pai, msg, de_mim, fonte, suave, cor=None):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, _reac) = msg
        if cor is None:
            cor = COR_EU if de_mim else COR_OUTRO
        f = tk.Frame(pai, bg=cor)
        f.pack(side="top", fill="x")
        dados = self.nucleo.zip.bytes(lfu)
        if dados is None and _bin:
            dados = _bin  # preview inline (BinaryData)
        if HAS_PIL and dados:
            try:
                im = Image.open(io.BytesIO(dados))
                im.thumbnail((420, 420))
                foto = ImageTk.PhotoImage(im)
                foto._im_orig = im
                self.imagens_foto.append(foto)
                lbl = tk.Label(f, image=foto, bg=cor, cursor="hand2")
                lbl._foto = foto
                lbl.pack()
                lbl.bind("<Button-1>",
                         lambda e, m=msg: self._visualizar_imagem(
                             self._dados_anexo(m),
                             f"🖼 {self._nome_base(m)}"))
                self._balao_texto(pai, f"🖼 {midia_nome or 'image'}"
                                   f" · {tamanho_amigavel(tamanho)}", de_mim, suave)
                bar = tk.Frame(f, bg=cor)
                bar.pack(side="top", anchor="w", padx=2, pady=(0, 2))
                tk.Button(bar, text="🔍 View original", relief="flat",
                          bg=cor, font=suave, padx=6,
                          command=lambda m=msg: self._abrir_anexo(m)
                          if not self._visualizar_imagem(
                              self._dados_anexo(m),
                              self._nome_base(m)) else None
                          ).pack(side="left", padx=(0, 4))
                tk.Button(bar, text="💾 Save…", relief="flat",
                          bg=cor, font=suave, padx=6,
                          command=lambda m=msg: self._salvar(m)).pack(side="left")
                return
            except Exception:
                pass
        if dados is None:
            tk.Label(f, text="🖼 [image] · ⚠ The file does not exist in the export",
                     fg=COR_SUAVE, font=suave, bg=cor).pack(padx=6, pady=6)
        else:
            tk.Label(f, text=f"🖼 {midia_nome or 'image'} (no preview)",
                     fg=COR_SUAVE, font=suave, bg=cor).pack(padx=6, pady=6)
            tk.Button(f, text="🔍 Open", relief="flat", bg=cor, font=suave,
                      padx=6, command=lambda m=msg: self._abrir_anexo(m)
                      ).pack(side="top", anchor="w", padx=2, pady=(0, 2))

    def _salvar(self, msg):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, _reac) = msg
        nome_base = midia_nome or nome_arq or os.path.basename(lfu or "") or "attachment"
        destino = fdlg.asksaveasfilename(
            title="Save attachment", initialfile=nome_base,
            defaultextension=os.path.splitext(nome_base)[1])
        if not destino:
            return
        dados = self.nucleo.zip.bytes(lfu)
        if dados is None:
            mbox.showerror(APP_TITLE, "File not found in the zip.")
            return
        with open(destino, "wb") as f:
            f.write(dados)
        self._chamar(self._set_status, f"Saved: {destino}")

    def _dados_anexo(self, msg):
        """Returns the attachment's bytes (from the zip or the inline preview)."""
        _bin = msg[3]
        lfu = msg[13]
        dados = self.nucleo.zip.bytes(lfu) if self.nucleo else None
        if dados is None:
            dados = _bin or None
        return dados

    def _tem_arquivo(self, msg):
        """True se o anexo da mensagem existe (zip/cache) ou tem preview inline."""
        try:
            lfu, _bin = msg[13], msg[3]
            if lfu and self.nucleo and self.nucleo.zip.tem(lfu):
                return True
            return bool(_bin)
        except Exception:
            return False

    def _caminho_video(self, msg):
        """Path of the already-extracted video (cache), or None."""
        return self.nucleo.zip.caminho_extraido(msg[13]) if self.nucleo else None

    def _thumbnail_video(self, msg, largura=320):
        """Generates (or fetches from cache) a video thumbnail -> PhotoImage.
        ffmpeg runs once per file (disk + memory cache)."""
        if not HAS_PIL:
            return None
        caminho = self._caminho_video(msg)
        if not caminho:
            return None
        nome = os.path.basename(caminho)
        ext = os.path.splitext(caminho)[1] or ".mp4"
        # cache key: hash of the name (stable per extracted file)
        chave = hashlib.md5(caminho.encode("utf-8", "replace")).hexdigest()[:16]
        th_dir = os.path.join(tempfile.gettempdir(), "zapixdesk_thumbs")
        os.makedirs(th_dir, exist_ok=True)
        th = os.path.join(th_dir, f"{chave}{ext}.png")
        if chave in self._thumbs and os.path.exists(th):
            try:
                im = Image.open(th); im.load()
            except Exception:
                im = None
        else:
            if shutil.which("ffmpeg") and not os.path.exists(th):
                subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error",
                     "-i", caminho, "-vf", f"scale={largura}:-2",
                     "-frames:v", "1", "-y", th],
                    timeout=30, capture_output=True)
            try:
                im = Image.open(th); im.load()
            except Exception:
                return None
            self._thumbs[chave] = im
        im.thumbnail((largura, largura))
        foto = ImageTk.PhotoImage(im)
        foto._im_orig = im
        return foto

    def _placeholder_video(self, cor):
        """Placeholder image (play) for videos without a thumbnail."""
        if not HAS_PIL:
            return None
        try:
            im = Image.new("RGB", (320, 180), (28, 28, 30))
            return ImageTk.PhotoImage(im)
        except Exception:
            return None

    def _nome_base(self, msg):
        midia_nome, nome_arq, lfu = msg[12], msg[4], msg[13]
        return (midia_nome or nome_arq
                or os.path.basename(lfu or "") or "attachment")

    def _abrir_anexo(self, msg):
        """Extracts the attachment to the cache and opens it with the default app."""
        dados = self._dados_anexo(msg)
        if not dados:
            mbox.showerror(APP_TITLE,
                           "File not found in the zip "
                           "(only the inline preview is available).")
            return
        nome = self._nome_base(msg)
        cache = os.path.join(tempfile.gettempdir(), "zapixdesk_cache")
        os.makedirs(cache, exist_ok=True)
        destino = os.path.join(cache, f"{int(time.time()*1000)}_{nome}")
        with open(destino, "wb") as f:
            f.write(dados)
        try:
            if os.name == "nt":
                os.startfile(destino)
            else:
                subprocess.Popen(["xdg-open", destino])
        except FileNotFoundError:
            mbox.showerror(APP_TITLE,
                           "Could not open automatically.\n"
                           f"File extracted to:\n{destino}")
        self._chamar(self._set_status, f"Opened: {nome}")

    def _visualizar_imagem(self, dados, titulo):
        """Popup with the image at original size (Pillow)."""
        if not HAS_PIL or not dados:
            return False
        try:
            im = Image.open(io.BytesIO(dados))
        except Exception:
            return False
        topo = tk.Toplevel(self.raiz)
        topo.title(titulo)
        topo.configure(bg="black")
        im.thumbnail((1280, 900))
        foto = ImageTk.PhotoImage(im)
        # do not set _im_orig: the popup should not be rescaled
        self.imagens_foto.append(foto)
        lbl = tk.Label(topo, image=foto, bg="black")
        lbl.pack(padx=8, pady=8)
        return True

    def _reproduzir(self, msg, audio=False):
        """Plays audio/video with a system player."""
        dados = self._dados_anexo(msg)
        if not dados:
            mbox.showerror(APP_TITLE,
                           "Media not found in the zip. "
                           "Use Save and open it manually.")
            return
        nome = self._nome_base(msg)
        ext = os.path.splitext(nome)[1]
        cache = os.path.join(tempfile.gettempdir(), "zapixdesk_cache")
        os.makedirs(cache, exist_ok=True)
        destino = os.path.join(cache, f"midia_{int(time.time()*1000)}{ext}")
        with open(destino, "wb") as f:
            f.write(dados)
        if audio:
            cmds = [["mpv", "--really-quiet", "--no-video", destino],
                    ["ffplay", "-nodisp", "-autoexit", destino],
                    ["mpv", destino],
                    ["vlc", "--play-and-exit", destino],
                    ["xdg-open", destino]]
        else:
            cmds = [["mpv", "--really-quiet", destino],
                    ["ffplay", destino],
                    ["mpv", destino],
                    ["vlc", destino], ["xdg-open", destino]]
        feito = False
        for c in cmds:
            if shutil.which(c[0]):
                try:
                    subprocess.Popen(c)
                    feito = True
                    break
                except Exception:
                    continue
        if not feito:
            mbox.showerror(APP_TITLE,
                           "No player found (mpv/ffplay/vlc).\n"
                           f"File extracted to:\n{destino}")
        self._chamar(self._set_status, f"Playing: {nome}")

    def _balao_midia(self, pai, msg, de_mim, fonte, suave, audio=False, cor=None):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, _reac) = msg
        if cor is None:
            cor = COR_EU if de_mim else COR_OUTRO
        f = tk.Frame(pai, bg=cor)
        f.pack(side="top", fill="x")
        icone = "🎵" if audio else "🎬"
        dur = f" · {self._duracao(duracao)}" if duracao else ""
        # video thumbnail
        if not audio:
            foto = self._thumbnail_video(msg)
            if foto is None:
                foto = self._placeholder_video(cor)
            if foto is not None:
                self.imagens_foto.append(foto)
                cont = tk.Frame(f, bg=cor)
                cont.pack(side="top", anchor="w", padx=2, pady=(2, 2))
                lbl = tk.Label(cont, image=foto, bg="#1c1c1e", cursor="hand2")
                lbl._foto = foto
                lbl.pack()
                # overlay: play icon + duration
                ov = tk.Label(cont, text="▶", bg="#1c1c1e", fg="white",
                              font=("DejaVu Sans", 22), anchor="center")
                ov.place(relx=0.5, rely=0.5, anchor="center")
                if duracao:
                    dur_l = tk.Label(cont, text=self._duracao(duracao),
                                     bg="#000000", fg="white",
                                     font=suave, padx=4, pady=1)
                    dur_l.place(relx=1.0, rely=1.0, anchor="se", x=4, y=4)
                def _tocar(_e=None, m=msg):
                    self._reproduzir(m, audio=False)
                lbl.bind("<Button-1>", _tocar)
                ov.bind("<Button-1>", _tocar)
        # title (+ a warning when the file is not in the export)
        tem_arquivo = self._tem_arquivo(msg)
        titulo = f"{icone} {midia_nome or ('audio' if audio else 'video')}" \
                 f"{dur} · {tamanho_amigavel(tamanho)}"
        tk.Label(f, text=titulo, fg=COR_TEXTO, font=fonte,
                 bg=cor, anchor="w", wraplength=self.LARGURA_MAX_BALAO).pack(
            side="top", anchor="w", padx=4, pady=2)
        if not tem_arquivo:
            tk.Label(f, text="⚠ File does not exist in export",
                     fg=COR_SUAVE, font=suave, bg=cor, anchor="w").pack(
            side="top", anchor="w", padx=4)
        bar = tk.Frame(f, bg=cor)
        bar.pack(side="top", anchor="w", padx=2, pady=(0, 2))
        tk.Button(bar, text="▶ Play", relief="flat", bg=cor,
                  font=suave, padx=6,
                  command=lambda m=msg, a=audio: self._reproduzir(m, a)
                  ).pack(side="left", padx=(0, 4))
        tk.Button(bar, text="💾 Save…", relief="flat", bg=cor,
                  font=suave, padx=6,
                  command=lambda m=msg: self._salvar(m)).pack(side="left")

    @staticmethod
    def _duracao(s):
        try:
            s = int(s)
        except Exception:
            return ""
        m, ss = divmod(s, 60)
        return f"{m}:{ss:02d}"

    def _balao_documento(self, pai, msg, de_mim, fonte, suave, cor=None):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, _reac) = msg
        if cor is None:
            cor = COR_EU if de_mim else COR_OUTRO
        f = tk.Frame(pai, bg=cor)
        f.pack(side="top", fill="x")
        nome = midia_nome or nome_arq or os.path.basename(lfu or "") or "document"
        tk.Label(f, text=f"📄 {nome} · {tamanho_amigavel(tamanho)}",
                 fg=COR_TEXTO, font=fonte,
                 bg=cor, anchor="w").pack(
            side="top", anchor="w", padx=4, pady=2)
        if not self._tem_arquivo(msg):
            tk.Label(f, text="⚠ File does not exist in export",
                     fg=COR_SUAVE, font=suave, bg=cor, anchor="w").pack(
            side="top", anchor="w", padx=4)
        bar = tk.Frame(f, bg=cor)
        bar.pack(side="top", anchor="w", padx=2, pady=(0, 2))
        tk.Button(bar, text="📂 Open", relief="flat", bg=cor,
                  font=suave, padx=6,
                  command=lambda m=msg: self._abrir_anexo(m)
                  ).pack(side="left", padx=(0, 4))
        tk.Button(bar, text="💾 Save…", relief="flat", bg=cor,
                  font=suave, padx=6,
                  command=lambda m=msg: self._salvar(m)).pack(side="left")

    def _balao_vcard(self, pai, msg, fonte, suave):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, _reac) = msg
        f = tk.Frame(pai, bg=COR_OUTRO)
        f.pack(side="top", fill="x")
        nome = midia_nome or "Contact"
        tk.Label(f, text=f"👤 {nome}", fg=COR_TEXTO, font=fonte,
                 bg=COR_OUTRO, anchor="w").pack(side="top", anchor="w", padx=4, pady=2)
        if texto and "BEGIN:VCARD" in (texto or ""):
            nome_c = re.search(r"FN:(.*)", texto)
            tel = re.search(r"TEL[^:]*:(.*)", texto)
            if nome_c:
                tk.Label(f, text=nome_c.group(1).strip(), fg=COR_SUAVE,
                         font=suave, bg=COR_OUTRO).pack(side="top", anchor="w", padx=4)
            if tel:
                tk.Label(f, text=tel.group(1).strip(), fg=COR_SUAVE,
                         font=suave, bg=COR_OUTRO).pack(side="top", anchor="w", padx=4)

    def _balao_link(self, pai, msg, de_mim, fonte, suave, cor=None):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, _reac) = msg
        if cor is None:
            cor = COR_EU if de_mim else COR_OUTRO
        url = (texto or "").strip()
        f = tk.Frame(pai, bg=cor)
        f.pack(side="top", fill="x")
        if url:
            tk.Label(f, text=f"🔗 {url}", fg=COR_URL, font=fonte,
                     anchor="w", wraplength=380, justify="left",
                     bg=cor).pack(
                side="top", anchor="w", padx=4, pady=2)
        if legenda:
            tk.Label(f, text=legenda, fg=COR_TEXTO, font=suave,
                     anchor="w", bg=cor).pack(
                side="top", anchor="w", padx=4)
        dados = self.nucleo.zip.bytes(lfu)
        if HAS_PIL and dados:
            try:
                im = Image.open(io.BytesIO(dados))
                im.thumbnail((360, 180))
                foto = ImageTk.PhotoImage(im)
                foto._im_orig = im
                self.imagens_foto.append(foto)
                lb = tk.Label(f, image=foto, bg=cor)
                lb._foto = foto
                lb.pack(side="top", anchor="w", padx=4, pady=2)
            except Exception:
                pass

    def _balao_figurinha(self, pai, msg, de_mim, fonte, suave, cor=None):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, _reac) = msg
        if cor is None:
            cor = COR_EU if de_mim else COR_OUTRO
        f = tk.Frame(pai, bg=cor)
        f.pack(side="top", fill="x")
        dados = self.nucleo.zip.bytes(lfu)
        if dados is None and _bin:
            dados = _bin
        if HAS_PIL and dados:
            try:
                im = Image.open(io.BytesIO(dados))
                im.thumbnail((220, 220))
                foto = ImageTk.PhotoImage(im)
                foto._im_orig = im
                self.imagens_foto.append(foto)
                lb = tk.Label(f, image=foto, bg=cor)
                lb._foto = foto
                lb.pack()
                return
            except Exception:
                pass
        tk.Label(f, text="✨ [sticker]", fg=COR_SUAVE, font=suave,
                 bg=cor).pack(padx=6, pady=6)

    # -- conversation export -----------------------------------------------------
    def _nome_export(self, c):
        """File-name-safe name derived from the conversation's name."""
        base = re.sub(r"[^A-Za-z0-9À-ÿ_\- ]+", "", c["nome"]).strip()
        base = re.sub(r"\s+", "_", base)
        return (base or "conversation")

    def _exportar_conversa(self):
        """Asks for the format (HTML/PDF) and exports the current conversation."""
        c = getattr(self, "_conversa_atual", None)
        if not c:
            mbox.showinfo(APP_TITLE, "Open a conversation first.")
            return
        if not self.nucleo:
            return
        opcoes = tk.Toplevel(self.raiz)
        opcoes.title("Export conversation")
        opcoes.configure(bg=COR_LISTA)
        opcoes.geometry("360x180")
        opcoes.resizable(False, False)
        opcoes.transient(self.raiz)
        opcoes.grab_set()
        tk.Label(opcoes, text=f"Export “{c['nome']}” to:",
                 bg=COR_LISTA, font=("DejaVu Sans", 11),
                 anchor="center").pack(pady=(16, 8), padx=12, fill="x")
        var = tk.StringVar(value="html")
        tk.Radiobutton(opcoes, text="HTML  (self-contained, opens in the browser)",
                       variable=var, value="html", bg=COR_LISTA,
                       anchor="w", font=("DejaVu Sans", 10)).pack(
            padx=16, anchor="w", fill="x")
        tk.Radiobutton(opcoes, text="PDF  (via headless Chrome, if available)",
                       variable=var, value="pdf", bg=COR_LISTA,
                       anchor="w", font=("DejaVu Sans", 10)).pack(
            padx=16, anchor="w", fill="x")
        def _ok():
            opcoes.destroy()
            self._exportar_bg(c, var.get())
        def _cancel():
            opcoes.destroy()
        bt_frame = tk.Frame(opcoes, bg=COR_LISTA)
        bt_frame.pack(pady=12)
        tk.Button(bt_frame, text="Export", command=_ok, bg=COR_TITULO,
                  fg="white", padx=14, pady=4,
                  font=("DejaVu Sans", 10)).pack(side="left", padx=6)
        tk.Button(bt_frame, text="Cancel", command=_cancel, bg="#888888",
                  fg="white", padx=14, pady=4,
                  font=("DejaVu Sans", 10)).pack(side="left", padx=6)

    def _exportar_bg(self, c, formato):
        """Loads ALL messages (in a thread) and generates the file."""
        threading.Thread(target=self._exportar_trabalho,
                         args=(c, formato), daemon=True).start()

    def _exportar_trabalho(self, c, formato):
        t0 = time.time()
        log("Export starting: %r (%s) format=%s", c["nome"], c["jid"], formato)
        try:
            # loads all messages in batches (without freezing the UI).
            # mensagens() returns in chronological order (oldest first)
            # and the offset advances to older pages.
            msgs = []
            offset = 0
            total = self.nucleo.total_mensagens(c["jid"])
            while True:
                lote = self.nucleo.mensagens(c["jid"], self._LIMITE_PAGINA, offset)
                if not lote:
                    break
                msgs = lote + msgs  # the older page goes to the beginning
                offset += len(lote)
                if offset >= total:
                    break
                if offset > 200000:  # safety cap
                    break
            log("  %d messages loaded in %.2fs", len(msgs), time.time() - t0)
            base = self._nome_export(c)
            if formato == "pdf":
                caminho_html = self._gerar_html(c, msgs, base)
                caminho = self._html_para_pdf(caminho_html, base)
            else:
                caminho = self._gerar_html(c, msgs, base, abrir=False)
            log("Export finished: %s in %.2fs", caminho, time.time() - t0)
            self._chamar(self._set_status, f"Exported: {os.path.basename(caminho)}")
            # opens the file with the default app
            try:
                if os.name == "nt":
                    os.startfile(caminho)
                else:
                    subprocess.Popen(["xdg-open", caminho])
            except Exception:
                pass
            mbox.showinfo(APP_TITLE, f"Conversation exported to:\n{caminho}")
        except Exception as exc:
            log_excecao("exportar_trabalho", exc, traceback.format_exc())
            self._chamar(self._erro_export, str(exc))

    def _erro_export(self, msg):
        mbox.showerror(APP_TITLE, "Failed to export:\n\n" + msg)

    @staticmethod
    def _esc(t):
        """Escapes HTML for message text."""
        return html.escape(str(t or ""), quote=True)

    def _linhas_html(self, c, msgs):
        """Generates the HTML body of the messages (WhatsApp-style bubbles)."""
        partes = []
        ultimo_dia = None
        for msg in msgs:
            (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
             tamanho, duracao, midia_nome, legenda, lfu, _reag, reacoes) = msg
            dia = hora(ts)
            if dia:
                dia_chave = dia[:10]
                if dia_chave != ultimo_dia:
                    ultimo_dia = dia_chave
                    partes.append(
                        f'<div class="dia"><span>{html.escape(dia)}</span></div>')
            classe = "eu" if de_mim else "outro"
            remetente = ""
            if c["grupo"] and not de_mim and push:
                remetente = (f'<div class="remetente">{html.escape(push)}</div>')
            conteudo = self._conteudo_html(msg, de_mim)
            hora_txt = self._hora_curta(ts)
            reag_txt = ""
            if reacoes:
                vistos = {}
                ordem = []
                for jid_r, texto_r in reacoes:
                    k = texto_r or "✅"
                    if k not in vistos:
                        vistos[k] = 0
                        ordem.append(k)
                    vistos[k] += 1
                reag_txt = "  ".join(
                    f"{k} {vistos[k]}" if vistos[k] > 1 else k for k in ordem)
            rodape = f'<div class="rodape">{html.escape(reag_txt)} ' \
                     f'<span class="hora">{html.escape(hora_txt)}' \
                     f'{"  ✓✓" if de_mim else ""}</span></div>'
            partes.append(
                f'<div class="linha {classe}">'
                f'<div class="balao">{remetente}{conteudo}{rodape}</div></div>')
        return "".join(partes)

    def _conteudo_html(self, msg, de_mim):
        (rid, de_mim, texto, _bin, nome_arq, ts, push, mime, tipo,
         tamanho, duracao, midia_nome, legenda, lfu, _reag, reacoes) = msg
        if tipo == 0 or tipo in (29, 1027):
            corpo = self._esc(texto).replace("\n", "<br>") if texto else ""
        elif tipo == 1:
            corpo = (f'<div class="midia">🖼 {html.escape(midia_nome or "image")}'
                     f' · {html.escape(tamanho_amigavel(tamanho))}</div>')
        elif tipo in (2, 3):
            icone = "🎵" if tipo == 2 else "🎬"
            dur = f' · {self._duracao(duracao)}' if duracao else ""
            corpo = (f'<div class="midia">{icone} '
                     f'{html.escape(midia_nome or ("audio" if tipo == 2 else "video"))}'
                     f'{html.escape(dur)} · '
                     f'{html.escape(tamanho_amigavel(tamanho))}</div>')
        elif tipo == 4:
            nome = midia_nome or "Contact"
            corpo = f'<div class="midia">👤 {html.escape(nome)}</div>'
            if texto and "BEGIN:VCARD" in (texto or ""):
                nome_c = re.search(r"FN:(.*)", texto)
                tel = re.search(r"TEL[^:]*:(.*)", texto)
                if nome_c:
                    corpo += f'<div class="vcard">{html.escape(nome_c.group(1).strip())}</div>'
                if tel:
                    corpo += f'<div class="vcard">{html.escape(tel.group(1).strip())}</div>'
        elif tipo == 8:
            nome = midia_nome or nome_arq or "document"
            corpo = (f'<div class="midia">📄 {html.escape(nome)} · '
                     f'{html.escape(tamanho_amigavel(tamanho))}</div>')
        elif tipo == 9:
            corpo = f'<div class="link">🔗 {html.escape((texto or "").strip())}</div>'
        elif tipo == 10:
            corpo = '<div class="midia">✨ [sticker]</div>'
        elif tipo == 1006:
            nome = midia_nome or nome_arq or "document"
            corpo = (f'<div class="midia">📄 {html.escape(nome)} · '
                     f'{html.escape(tamanho_amigavel(tamanho))}</div>')
        else:
            if texto:
                corpo = self._esc(texto).replace("\n", "<br>")
            else:
                corpo = html.escape(self.nucleo._resumo_tipo(tipo))
        if legenda and tipo not in (9,):
            corpo += f'<div class="legenda">{self._esc(legenda)}</div>'
        return corpo

    _HTML_CSS = """
    body { background:#efeae2; font-family:'DejaVu Sans',Arial,sans-serif;
           margin:0; padding:16px; }
    .cabecalho { background:#008069; color:#fff; padding:12px 16px;
                 border-radius:8px; margin-bottom:14px; }
    .cabecalho h1 { margin:0; font-size:18px; }
    .cabecalho small { color:#d6f5ef; }
    .linha { display:flex; margin:2px 0; }
    .linha.eu { justify-content:flex-end; }
    .linha.outro { justify-content:flex-start; }
    .balao { max-width:62%; background:#fff; padding:6px 10px;
             border-radius:8px; font-size:14px; color:#111;
             box-shadow:0 1px 1px rgba(0,0,0,.12); }
    .linha.eu .balao { background:#d9fdd3; }
    .remetente { font-weight:bold; font-size:13px; color:#5268f5; }
    .rodape { text-align:right; font-size:11px; color:#667781;
              margin-top:2px; }
    .hora { white-space:nowrap; }
    .midia { color:#667781; font-size:13px; }
    .link { color:#1a6fb5; font-size:14px; word-break:break-all; }
    .legenda { font-size:13px; margin-top:3px; color:#333; }
    .vcard { font-size:12px; color:#667781; }
    .dia { text-align:center; margin:12px 0 6px; }
    .dia span { background:#fff; color:#667781; font-size:12px;
                padding:3px 12px; border-radius:10px;
                box-shadow:0 1px 1px rgba(0,0,0,.1); }
    """

    def _gerar_html(self, c, msgs, base, caminho=None):
        """Generates the self-contained .html file of the conversation."""
        if caminho is None:
            destino_dir = os.path.join(tempfile.gettempdir(), "zapixdesk_export")
            os.makedirs(destino_dir, exist_ok=True)
            caminho = os.path.join(destino_dir, f"{base}.html")
        total = self.nucleo.total_mensagens(c["jid"])
        corpo = self._linhas_html(c, msgs)
        rotulo = "👥 Group" if c["grupo"] else "💬 Direct message"
        documento = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(c['nome'])} — ZAPiXDesk</title>
<style>{self._HTML_CSS}</style>
</head>
<body>
<div class="cabecalho">
  <h1>{html.escape(c['nome'])}</h1>
  <small>{html.escape(rotulo)} · {total} messages · exported by ZAPiXDeskViewer</small>
</div>
{corpo}
</body>
</html>"""
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(documento)
        return caminho

    def _html_para_pdf(self, caminho_html, base):
        """Converts the HTML to PDF via headless Chrome, if available."""
        destino = os.path.splitext(caminho_html)[0] + ".pdf"
        candidatos = []
        if os.name == "nt":
            candidatos = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
        else:
            candidatos = ["google-chrome", "google-chrome-stable",
                          "chromium", "chromium-browser", "chrome", "msedge"]
        exe = None
        for cand in candidatos:
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
               f"--print-to-pdf={destino}", "--no-pdf-header-footer",
               caminho_html]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if not os.path.isfile(destino) or os.path.getsize(destino) == 0:
            raise RuntimeError(
                "Failed to generate the PDF.\n" + proc.stderr.decode("utf-8", "replace")[:500])
        return destino

    # -- shutdown -------------------------------------------------------------
    def _abrir_estado(self):
        self.raiz.protocol("WM_DELETE_WINDOW", self._sair)

    def _sair(self):
        """Full cleanup: closes the database, destroys windows and releases images.
        Prevents the X server from being left hanging with allocated resources."""
        try:
            if self.nucleo:
                self.nucleo.fechar()
        except Exception:
            pass
        # Releases the image references (PhotoImage) so Tk does not
        # hold display resources.
        self.imagens_foto.clear()
        self._thumbs.clear()
        # Destroys the main window (also destroys the children, including
        # Toplevels such as the Splash and export popups).
        try:
            self.raiz.destroy()
        except Exception:
            pass


class Splash:
    """Splash screen shown while the main window is being built.

    Displays the ZAPiXDesk logo (splash.png) in a small centered window that
    fades out after a short delay. Uses a normal decorated window so it
    composites reliably on GNOME/mutter (borderless overrideredirect windows
    can be hidden by the compositor).
    """

    DURACAO_MS = 1600   # time shown before the fade-out
    PASSOS_FADE = 12    # steps of the fade-out animation
    PASSO_FADE_MS = 40  # interval between fade steps

    def __init__(self, raiz):
        self.raiz = raiz
        self._foto = None
        self._alpha = 1.0
        self._fechando = False

        self.win = tk.Toplevel(raiz)
        self.win.title("ZAPiXDesk")
        # Regular window (with decorations) — reliable on mutter/GNOME.
        self.win.resizable(False, False)

        # Loads the logo; if it does not exist, creates a simple placeholder.
        if HAS_PIL and _caminho_recurso("splash.png"):
            try:
                img = Image.open(_caminho_recurso("splash.png"))
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                self._foto = ImageTk.PhotoImage(img)
            except Exception:
                log_excecao("Splash.__init__ (logo)", tb=traceback.format_exc())
                self._foto = None

        if self._foto is not None:
            w, h = self._foto.width(), self._foto.height()
        else:
            w, h = 480, 120

        # Centers on the screen
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        # Dark background consistent with the logo
        self.win.config(bg="#0b0e0c")

        if self._foto is not None:
            self.lbl = tk.Label(self.win, image=self._foto, bg="#0b0e0c")
            self.lbl.pack(expand=True, fill="both")
        else:
            self.lbl = tk.Label(self.win, text="ZAPiXDesk",
                                bg="#0b0e0c", fg="white",
                                font=("DejaVu Sans", 22, "bold"))
            self.lbl.pack(expand=True, fill="both")

        self.win.lift()
        self.win.attributes("-alpha", 1.0)
        self.win.focus_force()
        # Process events so the splash content is rendered before the
        # main window appears.
        self.win.update()

        # Closes if the user clicks the splash's X.
        self.win.protocol("WM_DELETE_WINDOW", self.fechar)

        # Schedules the automatic fade-out.
        self.win.after(self.DURACAO_MS, self.fechar)

    def fechar(self):
        """Starts the fade-out; the window is destroyed when it finishes."""
        if self._fechando:
            return
        self._fechando = True
        self._fade_out()

    def _fade_out(self):
        if self._alpha <= 0.0:
            try:
                self.win.destroy()
            except Exception:
                pass
            return
        self._alpha = max(0.0, self._alpha - 1.0 / self.PASSOS_FADE)
        try:
            self.win.attributes("-alpha", self._alpha)
        except Exception:
            # On some platforms alpha is not supported: close directly.
            try:
                self.win.destroy()
            except Exception:
                pass
            return
        self.win.after(self.PASSO_FADE_MS, self._fade_out)


def main():
    raiz = tk.Tk()
    # Splash first: creates the window and shows the logo before the main UI.
    splash = Splash(raiz)
    raiz.update_idletasks()
    definir_icone_janela(raiz)
    app = App(raiz)

    # SIGINT (Ctrl+C) handler: does the proper cleanup before exiting,
    # so the X server is not left hanging with allocated resources.
    def _sair_sigint(_sig, _frm):
        try:
            app._sair()
        except Exception:
            raiz.destroy()
        os._exit(0)
    signal.signal(signal.SIGINT, _sair_sigint)

    # The UI is ready — the splash continues for its minimum duration and fades.
    raiz.mainloop()
    # Final cleanup (in case the mainloop ends normally).
    try:
        splash.fechar()
    except Exception:
        pass


if __name__ == "__main__":
    main()
