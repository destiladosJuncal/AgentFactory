"""
Proxy interceptor con historial navegable, estilo ZAP — parte de captura.

Envuelve mitmproxy (que resuelve lo difícil: MITM, generación de certificados
por host al vuelo, TLS, HTTP/2) y guarda cada flujo en SQLite para poder
mostrarlo como árbol de sitios y reenviarlo con o sin cambios.

Piezas:
  · Proxy         arranca/para mitmproxy en un hilo con su propio event loop.
  · Almacen       persiste y consulta los flujos (SQLite, una base por sesión).
  · reenviar()    dispara una request de nuevo, DIRECTO al servidor.

Dónde vive todo: $DATOS/_proxy/
  · mitm/         la CA y config de mitmproxy (confinada acá, no en ~/.mitmproxy)
  · <sesion>.db   una base por sesión de captura

Sobre la CA: mitmproxy la genera la primera vez. Para interceptar HTTPS hay que
instalarla en el navegador — en Firefox se hace navegando a mitm.it con el proxy
puesto y aceptándola una vez (Firefox tiene su propio almacén, no usa el llavero
de macOS). Sin eso, HTTPS da error de certificado, que es el comportamiento
correcto: no queremos romper TLS en silencio.

Seguridad: un proxy ve TODO en claro, incluidas cookies y tokens. La captura se
puede acotar a una lista de hosts objetivo; con la lista vacía captura todo, que
es lo cómodo para arrancar pero conviene acotar apenas sepas qué vas a probar.
"""

import asyncio
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from core import plataforma, secretos
from core.rutas import dir_datos

PUERTO_DEFAULT = 8899
MAX_BODY = 2 * 1024 * 1024          # 2 MB por cuerpo; más que eso se trunca
HEADERS_SENSIBLES = {"authorization", "cookie", "set-cookie", "proxy-authorization"}


def dir_proxy() -> Path:
    d = dir_datos() / "_proxy"
    (d / "mitm").mkdir(parents=True, exist_ok=True)
    return d


# --- Almacenamiento ---------------------------------------------------------

class Almacen:
    """Los flujos capturados de UNA sesión, en su propia base SQLite.

    Cada hilo que la use crea su propia conexión (el proxy escribe desde el
    hilo de mitmproxy, la UI lee desde el suyo). WAL permite leer mientras se
    escribe sin bloquear."""

    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self._local = threading.local()
        self._crear_esquema()
        self._cifrar_lo_viejo()
        # (host, ruta) que NO se capturan más — se cargan a memoria para que el
        # chequeo en el hilo del proxy sea instantáneo.
        self._ignorados = {(r["host"], r["ruta"]) for r in
                           self._con().execute("SELECT host, ruta FROM ignorados")}

    def _cifrar_lo_viejo(self):
        """Cifra los headers de las capturas hechas antes de que esto existiera.

        Sin esto, una base que ya venía de antes se seguiría leyendo bien (el
        descifrado deja pasar lo que está en claro) pero sus cookies quedarían
        expuestas para siempre a un SELECT crudo. Corre una sola vez: después
        no hay filas en claro que encontrar.
        """
        if not secretos.disponible():
            return
        con = self._con()
        try:
            pendientes = con.execute(
                "SELECT id, req_headers, resp_headers FROM flujos "
                "WHERE (req_headers IS NOT NULL AND req_headers <> '' "
                "       AND req_headers NOT LIKE ?) "
                "   OR (resp_headers IS NOT NULL AND resp_headers <> '' "
                "       AND resp_headers NOT LIKE ?)",
                (secretos.PREFIJO + "%", secretos.PREFIJO + "%")).fetchall()
        except sqlite3.Error:
            return
        if not pendientes:
            return
        for fila in pendientes:
            req, resp = fila["req_headers"], fila["resp_headers"]
            con.execute(
                "UPDATE flujos SET req_headers = ?, resp_headers = ? WHERE id = ?",
                (secretos.cifrar(req) if req and not secretos.esta_cifrado(req) else req,
                 secretos.cifrar(resp) if resp and not secretos.esta_cifrado(resp) else resp,
                 fila["id"]))
        con.commit()

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.ruta, timeout=30)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            self._local.con = con
        return con

    def _crear_esquema(self):
        self._con().executescript("""
            CREATE TABLE IF NOT EXISTS ignorados (
                host TEXT, ruta TEXT, PRIMARY KEY (host, ruta)
            );
            CREATE TABLE IF NOT EXISTS flujos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, origen TEXT,           -- 'captura' | 'reenvio'
                ref INTEGER,                    -- id del flujo original, si es reenvío
                metodo TEXT, esquema TEXT, host TEXT, puerto INTEGER,
                ruta TEXT, query TEXT,
                req_headers TEXT, req_body BLOB, req_trunc INTEGER,
                estado INTEGER, resp_headers TEXT, resp_body BLOB, resp_trunc INTEGER,
                resp_tipo TEXT, ms REAL
            )
        """)
        self._con().commit()

    def guardar(self, flujo: Dict[str, Any]) -> int:
        con = self._con()
        cur = con.execute("""
            INSERT INTO flujos (ts, origen, ref, metodo, esquema, host, puerto,
                ruta, query, req_headers, req_body, req_trunc,
                estado, resp_headers, resp_body, resp_trunc, resp_tipo, ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            flujo.get("ts", time.time()), flujo.get("origen", "captura"), flujo.get("ref"),
            flujo["metodo"], flujo["esquema"], flujo["host"], flujo["puerto"],
            flujo["ruta"], flujo.get("query", ""),
            # Los headers van CIFRADOS: son donde viven las cookies de sesion
            # y el Authorization, y sin esto un SELECT directo se los lleva.
            secretos.cifrar(json.dumps(flujo.get("req_headers", []))),
            flujo.get("req_body", b""),
            int(flujo.get("req_trunc", 0)),
            flujo.get("estado"),
            secretos.cifrar(json.dumps(flujo.get("resp_headers", []))),
            flujo.get("resp_body", b""), int(flujo.get("resp_trunc", 0)),
            flujo.get("resp_tipo", ""), flujo.get("ms", 0),
        ))
        con.commit()
        return cur.lastrowid

    def listar(self, origen: Optional[str] = None) -> List[sqlite3.Row]:
        """Resumen de cada flujo, sin los cuerpos (que pueden ser grandes)."""
        sql = ("SELECT id, ts, origen, ref, metodo, esquema, host, puerto, ruta, "
               "query, estado, resp_tipo, ms, length(resp_body) AS resp_len "
               "FROM flujos")
        params = ()
        if origen:
            sql += " WHERE origen = ?"
            params = (origen,)
        sql += " ORDER BY id"
        return self._con().execute(sql, params).fetchall()

    def listar_arbol(self) -> List[sqlite3.Row]:
        """Lo que va al árbol: TODO lo capturado, más los reenvíos que
        descubrieron un host+ruta que la captura no tenía (un path nuevo). Los
        reenvíos repetidos al mismo path no ensucian el árbol."""
        con = self._con()
        base = ("SELECT id, ts, origen, ref, metodo, esquema, host, puerto, ruta, "
                "query, estado, resp_tipo, req_headers, req_body "
                "FROM flujos")
        capturas = con.execute(base + " WHERE origen='captura' ORDER BY id").fetchall()
        conocidos = {(r["host"], r["ruta"]) for r in capturas}
        reenvios_nuevos = [r for r in con.execute(base + " WHERE origen='reenvio' ORDER BY id")
                           if (r["host"], r["ruta"]) not in conocidos]
        return list(capturas) + reenvios_nuevos

    def obtener(self, id_flujo: int) -> Optional[sqlite3.Row]:
        return self._con().execute("SELECT * FROM flujos WHERE id = ?", (id_flujo,)).fetchone()

    def borrar_todo(self):
        self._con().execute("DELETE FROM flujos")
        self._con().commit()

    def ignorar(self, pares):
        """Deja de capturar estas (host, ruta) — se agregan a la lista negra."""
        con = self._con()
        for host, ruta in pares:
            con.execute("INSERT OR IGNORE INTO ignorados (host, ruta) VALUES (?,?)",
                        (host, ruta))
            self._ignorados.add((host, ruta))
        con.commit()

    def esta_ignorado(self, host: str, ruta: str) -> bool:
        return (host, ruta) in self._ignorados

    def dejar_de_ignorar(self, pares=None):
        """Vuelve a capturar: si pares es None, limpia toda la lista negra."""
        con = self._con()
        if pares is None:
            con.execute("DELETE FROM ignorados")
            self._ignorados.clear()
        else:
            for host, ruta in pares:
                con.execute("DELETE FROM ignorados WHERE host=? AND ruta=?", (host, ruta))
                self._ignorados.discard((host, ruta))
        con.commit()

    def borrar_por_url(self, pares) -> int:
        """Borra TODOS los flujos de cada (host, ruta) dado — el árbol muestra
        una hoja por URL pero la base tiene N capturas; borrar por id dejaba las
        otras y la URL reaparecía. Se lleva también sus reenvíos (por ref)."""
        pares = [(h, r) for h, r in pares]
        if not pares:
            return 0
        con = self._con()
        borradas = 0
        for host, ruta in pares:
            ids = [x["id"] for x in con.execute(
                "SELECT id FROM flujos WHERE host=? AND ruta=?", (host, ruta))]
            if not ids:
                continue
            marcas = ",".join("?" * len(ids))
            con.execute(f"DELETE FROM flujos WHERE id IN ({marcas}) OR ref IN ({marcas})",
                        (*ids, *ids))
            borradas += len(ids)
        con.commit()
        return borradas

    def borrar(self, ids: List[int]):
        """Borra flujos por id (y sus reenvíos, que apuntan a ellos por ref)."""
        if not ids:
            return
        marcas = ",".join("?" * len(ids))
        con = self._con()
        con.execute(f"DELETE FROM flujos WHERE id IN ({marcas}) OR ref IN ({marcas})",
                    (*ids, *ids))
        con.commit()


# --- Addon de mitmproxy -----------------------------------------------------

def _headers_a_lista(headers) -> List[List[str]]:
    return [[k, v] for k, v in headers.items()]


def _acotar(cuerpo: bytes):
    if cuerpo and len(cuerpo) > MAX_BODY:
        return cuerpo[:MAX_BODY], True
    return cuerpo or b"", False


class _Capturador:
    """Addon que persiste cada respuesta. Corre en el hilo del proxy."""

    def __init__(self, almacen: Almacen, hosts: List[str],
                 al_flujo: Optional[Callable[[int], None]]):
        self.almacen = almacen
        self.hosts = [h.lower() for h in hosts if h.strip()]
        self.al_flujo = al_flujo

    def _interesa(self, host: str) -> bool:
        if not self.hosts:
            return True
        host = host.lower()
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def response(self, flow):
        req, resp = flow.request, flow.response
        if not self._interesa(req.pretty_host):
            return
        # Lo que la persona borró del árbol no se vuelve a capturar.
        if self.almacen.esta_ignorado(req.pretty_host, req.path.split("?")[0]):
            return
        req_body, req_trunc = _acotar(req.raw_content)
        resp_body, resp_trunc = _acotar(resp.raw_content)
        try:
            id_flujo = self.almacen.guardar({
                "origen": "captura",
                "metodo": req.method, "esquema": req.scheme,
                "host": req.pretty_host, "puerto": req.port,
                "ruta": req.path.split("?")[0], "query": urlsplit(req.path).query,
                "req_headers": _headers_a_lista(req.headers), "req_body": req_body,
                "req_trunc": req_trunc,
                "estado": resp.status_code, "resp_headers": _headers_a_lista(resp.headers),
                "resp_body": resp_body, "resp_trunc": resp_trunc,
                "resp_tipo": resp.headers.get("content-type", ""),
                "ms": (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
                       if flow.response.timestamp_end else 0,
            })
        except Exception as e:
            print(f"⚠️ proxy: no pude guardar un flujo: {e}")
            return
        if self.al_flujo:
            try:
                self.al_flujo(id_flujo)
            except Exception:
                pass


# --- Proxy ------------------------------------------------------------------

class Proxy:
    def __init__(self, almacen: Almacen, puerto: int = PUERTO_DEFAULT,
                 hosts: Optional[List[str]] = None,
                 al_flujo: Optional[Callable[[int], None]] = None):
        self.almacen = almacen
        self.puerto = puerto
        self.hosts = hosts or []
        self.al_flujo = al_flujo
        self._master = None
        self._loop = None
        self._hilo = None
        self._error = None
        self._corriendo = threading.Event()

    @property
    def corriendo(self) -> bool:
        return self._corriendo.is_set()

    @property
    def ca_pem(self) -> Path:
        return dir_proxy() / "mitm" / "mitmproxy-ca-cert.pem"

    def iniciar(self) -> Optional[str]:
        """Arranca el proxy. Devuelve un mensaje de error o None si arrancó."""
        if self.corriendo:
            return None
        self._error = None
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()

        # Esperamos a que confirme arranque (o falle) antes de devolver.
        for _ in range(60):
            if self._corriendo.is_set() or self._error:
                break
            time.sleep(0.1)
        return self._error

    def _correr(self):
        from mitmproxy import options
        from mitmproxy.tools.dump import DumpMaster

        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def main():
                opts = options.Options(
                    listen_host="127.0.0.1", listen_port=self.puerto,
                    confdir=str(dir_proxy() / "mitm"),
                )
                self._master = DumpMaster(opts, with_termlog=False, with_dumper=False)
                self._master.addons.add(_Capturador(self.almacen, self.hosts, self.al_flujo))
                self._corriendo.set()
                await self._master.run()

            self._loop.run_until_complete(main())
        except Exception as e:
            self._error = str(e)
        finally:
            self._corriendo.clear()

    def detener(self):
        if self._master and self._loop:
            self._loop.call_soon_threadsafe(self._master.shutdown)
        self._corriendo.clear()


# --- Reenvío ----------------------------------------------------------------

def reenviar(almacen: Almacen, id_flujo: int,
             cambios: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dispara una request de nuevo, DIRECTO al servidor (no por el proxy).

    `cambios` puede traer metodo, url, headers (lista [[k,v]]) y body ya
    editados; lo que no venga se toma del flujo original. El resultado se
    guarda como un flujo nuevo con origen='reenvio', para no mezclarlo con el
    tráfico real capturado.

    OJO: esto ejecuta una request REAL contra el servidor real. Un GET es
    inocuo; un POST/DELETE hace lo que dice, otra vez. Es el comportamiento
    buscado en un repeater, pero no está aislado.
    """
    import requests

    base = almacen.obtener(id_flujo)
    if base is None:
        return {"error": f"No existe el flujo {id_flujo}"}

    cambios = cambios or {}
    metodo = cambios.get("metodo", base["metodo"])
    if "url" in cambios:
        url = cambios["url"]
    else:
        q = f"?{base['query']}" if base["query"] else ""
        url = f"{base['esquema']}://{base['host']}:{base['puerto']}{base['ruta']}{q}"

    if "headers" in cambios:
        headers = {k: v for k, v in cambios["headers"]}
    else:
        # El repetidor SI necesita los valores reales para redisparar.
        headers = {k: v for k, v in
                   json.loads(secretos.descifrar(base["req_headers"]) or "[]")}
    # host/content-length los recalcula requests; dejarlos pisa el reenvío.
    for h in list(headers):
        if h.lower() in ("host", "content-length"):
            headers.pop(h)

    cuerpo = cambios.get("body", base["req_body"])
    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode("utf-8")

    inicio = time.time()
    try:
        r = requests.request(metodo, url, headers=headers,
                             data=cuerpo or None, timeout=30, allow_redirects=False,
                             verify=False)
    except Exception as e:
        return {"error": f"Falló el reenvío: {e}"}
    ms = (time.time() - inicio) * 1000

    partes = urlsplit(url)
    resp_body, resp_trunc = _acotar(r.content)
    nuevo = almacen.guardar({
        "origen": "reenvio", "ref": id_flujo,
        "metodo": metodo, "esquema": partes.scheme,
        "host": partes.hostname or base["host"],
        "puerto": partes.port or (443 if partes.scheme == "https" else 80),
        "ruta": partes.path, "query": partes.query,
        "req_headers": [[k, v] for k, v in headers.items()],
        "req_body": cuerpo or b"", "req_trunc": False,
        "estado": r.status_code, "resp_headers": [[k, v] for k, v in r.headers.items()],
        "resp_body": resp_body, "resp_trunc": resp_trunc,
        "resp_tipo": r.headers.get("content-type", ""), "ms": ms,
    })
    return {"id": nuevo, "estado": r.status_code, "ms": ms}


# --- Utilidades para la UI --------------------------------------------------

def texto_headers(headers_json: str) -> str:
    """Los headers como texto, para mostrarselos a la persona en la pestana del
    proxy. Descifra: la persona mirando su propia captura en su propia pantalla
    no es una fuga, y por eso este camino no pide confirmacion."""
    try:
        pares = json.loads(secretos.descifrar(headers_json) or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    return "\n".join(f"{k}: {v}" for k, v in pares)


def descomprimir(body: bytes, content_encoding: str) -> bytes:
    """Descomprime el body según Content-Encoding (br/gzip/deflate/zstd). El
    proxy guarda el cuerpo tal cual viene del servidor —comprimido—, así que un
    JS/HTML/JSON se veían como binario. Delegamos en el decoder de mitmproxy,
    que maneja bien los casos raros (zstd streaming sin content-size). Si algo
    falla o no está comprimido, devuelve el body tal cual."""
    enc = (content_encoding or "").lower().strip()
    if not body or not enc or enc == "identity":
        return body
    try:
        from mitmproxy.net import encoding as _enc
        d = _enc.decode(body, enc)
        if isinstance(d, (bytes, bytearray)) and d:
            return bytes(d)
    except Exception:
        pass
    # Fallback manual por si mitmproxy no estuviera.
    try:
        if "br" in enc:
            import brotli
            return brotli.decompress(body)
        if "gzip" in enc or "x-gzip" in enc:
            import gzip
            return gzip.decompress(body)
        if "deflate" in enc:
            import zlib
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
        if "zstd" in enc:
            import io
            import zstandard
            return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(body)).read()
    except Exception:
        return body
    return body


def _content_type_de(headers_json: str) -> str:
    try:
        for k, v in json.loads(secretos.descifrar(headers_json) or "[]"):
            if k.lower() == "content-type":
                return v
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return ""


def _content_encoding(headers_json: str) -> str:
    try:
        for k, v in json.loads(secretos.descifrar(headers_json) or "[]"):
            if k.lower() == "content-encoding":
                return v
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return ""


def cuerpo_legible(cuerpo: bytes, tipo: str = "") -> Dict[str, Any]:
    """Decide cómo mostrar un cuerpo: JSON con sangría, texto, o hex si es
    binario. Devuelve {'texto', 'binario', 'bytes'}."""
    if not cuerpo:
        return {"texto": "", "binario": False, "bytes": 0}

    if "json" in (tipo or "").lower():
        try:
            obj = json.loads(cuerpo.decode("utf-8"))
            return {"texto": json.dumps(obj, indent=2, ensure_ascii=False),
                    "binario": False, "bytes": len(cuerpo)}
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    try:
        texto = cuerpo.decode("utf-8")
        # Si tiene muchos caracteres de control, es binario disfrazado.
        if sum(1 for c in texto[:2000] if ord(c) < 9) < 5:
            return {"texto": texto, "binario": False, "bytes": len(cuerpo)}
    except UnicodeDecodeError:
        pass

    volcado = []
    for i in range(0, min(len(cuerpo), 4096), 16):
        trozo = cuerpo[i:i + 16]
        hexa = " ".join(f"{b:02x}" for b in trozo)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in trozo)
        volcado.append(f"{i:08x}  {hexa:<48}  {ascii_}")
    return {"texto": "\n".join(volcado), "binario": True, "bytes": len(cuerpo)}


def params_de(fila) -> List[str]:
    """Nombres de parámetros de un flujo: query (GET) + cuerpo (POST form/json).
    Para mostrar 'index.php (id, p)' en el árbol."""
    from urllib.parse import parse_qsl
    nombres: List[str] = []
    q = fila["query"] if "query" in fila.keys() else ""
    for k, _ in parse_qsl(q or "", keep_blank_values=True):
        if k not in nombres:
            nombres.append(k)

    # cuerpo, solo si la fila lo trae (listar_arbol lo incluye)
    try:
        cuerpo = fila["req_body"]
        headers = fila["req_headers"]
    except (IndexError, KeyError):
        return nombres
    ct = ""
    for par in _headers_json(headers):
        if not (isinstance(par, (list, tuple)) and len(par) == 2):
            continue
        k, v = par
        if k.lower() == "content-type":
            ct = v.lower(); break
    texto = cuerpo.decode("utf-8", "replace") if isinstance(cuerpo, (bytes, bytearray)) else (cuerpo or "")
    if not texto:
        return nombres
    if "form-urlencoded" in ct or ("=" in texto and "{" not in texto[:2]):
        for k, _ in parse_qsl(texto, keep_blank_values=True):
            if k not in nombres:
                nombres.append(k)
    elif "json" in ct or texto.lstrip().startswith("{"):
        try:
            import json as _json
            obj = _json.loads(texto)
            if isinstance(obj, dict):
                for k in obj:
                    if k not in nombres:
                        nombres.append(k)
        except (ValueError, TypeError):
            pass
    return nombres


def _headers_json(j):
    import json as _json
    if isinstance(j, str):
        j = secretos.descifrar(j)
    try:
        return _json.loads(j) if isinstance(j, (str, bytes)) else (j or [])
    except (ValueError, TypeError):
        return []


def arbol_de(filas: List[sqlite3.Row]) -> Dict[str, Any]:
    """Agrupa los flujos en un árbol host -> segmentos de ruta -> hoja.

    Cada nodo hoja apunta al id del último flujo con esa ruta (si se pidió N
    veces, gana el más reciente para el detalle; el árbol muestra una entrada)."""
    raiz: Dict[str, Any] = {}
    for f in filas:
        host = f"{f['host']}:{f['puerto']}" if f["puerto"] not in (80, 443) else f["host"]
        nodo = raiz.setdefault(host, {"_hijos": {}, "_flujo": None, "_host": True})
        segmentos = [s for s in f["ruta"].split("/") if s] or ["/"]
        for seg in segmentos:
            nodo = nodo["_hijos"].setdefault(seg, {"_hijos": {}, "_flujo": None})
        nodo["_flujo"] = dict(f)          # la hoja lleva los datos del flujo
        nodo["_params"] = params_de(f)
    return raiz


# --- Firefox con perfil dedicado --------------------------------------------

# --- Certificado en Firefox (automático, una sola vez) ----------------------
# El objetivo: instalar la CA en el perfil UNA vez y no volver a pedirla nunca.
# El cert de mitmproxy es persistente (vive en _proxy/mitm/, vence en ~10 años)
# y el almacén de Firefox (cert9.db) tampoco se borra entre reinicios. Así que
# solo hay que instalarlo si todavía no está.

def _certutil() -> Optional[str]:
    """Ruta al certutil de NSS (el que maneja el almacén de Firefox), o None.
    Ojo: hay otro certutil (Kerberos) con el mismo nombre; se distinguen porque
    el de NSS acepta -L -d."""
    import shutil
    import subprocess
    for cand in ("certutil", "/opt/homebrew/bin/certutil", "/usr/local/bin/certutil"):
        ruta = shutil.which(cand) if "/" not in cand else (cand if Path(cand).exists() else None)
        if not ruta:
            continue
        try:
            r = subprocess.run([ruta, "-H"], capture_output=True, text=True, timeout=10)
            if "NSS" in (r.stdout + r.stderr) or "-d " in (r.stdout + r.stderr):
                return ruta
        except Exception:
            continue
    return None


def ca_instalada_en_firefox(perfil: Path) -> bool:
    """¿El perfil ya confía en la CA de mitmproxy? Mira el cert9.db directo, así
    funciona la haya instalado la app o vos a mano por mitm.it."""
    db = Path(perfil) / "cert9.db"
    if not db.exists():
        return False
    try:
        return b"mitmproxy" in db.read_bytes()
    except OSError:
        return False


def instalar_ca_firefox(perfil: Path) -> Dict[str, Any]:
    """Instala la CA en el perfil si falta. Idempotente: si ya está, no hace
    nada. Devuelve {'estado': 'ya'|'instalada'|'manual', ...}."""
    perfil = Path(perfil)
    if ca_instalada_en_firefox(perfil):
        return {"estado": "ya"}

    cu = _certutil()
    ca = ca_pem_global()
    if not cu or not ca.exists():
        return {"estado": "manual",
                "motivo": "sin certutil" if not cu else "sin CA generada todavía"}

    import subprocess
    try:
        r = subprocess.run(
            [cu, "-A", "-n", "mitmproxy AgenteDeepSeek", "-t", "CT,c,c",
             "-i", str(ca), "-d", f"sql:{perfil}"],
            capture_output=True, text=True, timeout=20)
    except Exception as e:
        return {"estado": "manual", "motivo": str(e)}
    if r.returncode != 0:
        # Suele fallar solo si Firefox tiene el perfil abierto (cert9.db lockeado).
        return {"estado": "manual", "motivo": (r.stderr or "").strip()[:200]}
    return {"estado": "instalada"}


def ca_pem_global() -> Path:
    return dir_proxy() / "mitm" / "mitmproxy-ca-cert.pem"


def ca_cer_global() -> Path:
    """La misma CA en formato DER, que es el que come el almacén de Windows."""
    return dir_proxy() / "mitm" / "mitmproxy-ca-cert.cer"


# --- Confiar la CA en el almacén de Windows (opcional, reversible) -----------
#
# En Windows no se puede usar el camino de macOS: el certutil de NSS —el que
# sabe escribir en el cert9.db de Firefox— no viene con Firefox (solo trae
# nss3.dll), y el certutil.exe que SÍ está en el PATH es el de Microsoft, con
# otra sintaxis. La función _certutil() de arriba ya lo detecta y lo descarta
# bien, así que el camino por defecto en Windows es la instalación manual por
# mitm.it: dos clics, una sola vez, y su alcance es el perfil descartable.
#
# Esto de acá es la alternativa: meter la CA en el almacén de confianza DEL
# USUARIO (no del equipo: no hace falta ser administrador) y decirle a Firefox
# que lea ese almacén con security.enterprise_roots.
#
# Es deliberadamente un botón aparte y no algo que pase solo al abrir Firefox.
# La diferencia importa: instalarla en el perfil afecta solo a ese Firefox
# descartable, mientras que meterla en el almacén de Windows hace que TODAS las
# aplicaciones de esta cuenta confíen en la CA de mitmproxy. Es un radio de
# acción bastante más grande que el del camino de macOS, y merece un clic
# consciente. Por eso también existe quitar_ca_de_windows().

def _marca_ca_windows() -> Path:
    return dir_proxy() / "ca-en-windows.json"


def _huella_ca() -> Optional[str]:
    """Huella SHA-1 de la CA, que es como certutil identifica un certificado
    para borrarlo después."""
    pem = ca_pem_global()
    if not pem.exists():
        return None
    try:
        import hashlib
        import ssl
        der = ssl.PEM_cert_to_DER_cert(pem.read_text(encoding="utf-8"))
        return hashlib.sha1(der).hexdigest()
    except Exception:
        return None


def ca_confiada(perfil: Path) -> bool:
    """¿El perfil va a confiar en la CA, por cualquiera de los dos caminos?

    Hace falta mirar los dos: por enterprise roots el certificado NO queda en
    el cert9.db, así que preguntarle solo a ca_instalada_en_firefox() daría
    False para siempre y la app reabriría mitm.it en cada arranque."""
    return ca_instalada_en_firefox(perfil) or _marca_ca_windows().exists()


def confiar_ca_en_windows(perfil: Path) -> Dict[str, Any]:
    """Agrega la CA al almacén del usuario y habilita enterprise roots.

    Windows muestra su propio cuadro de confirmación al agregar una raíz de
    confianza: eso es correcto y no se intenta evitar."""
    if not plataforma.ES_WINDOWS:
        return {"error": "Esto solo aplica en Windows."}

    pem = ca_pem_global()
    if not pem.exists():
        return {"error": "Todavía no hay CA generada: encendé la captura una vez."}

    cer = ca_cer_global()
    if not cer.exists():
        # mitmproxy suele dejar el .cer al lado, pero si no está se deriva del
        # .pem, que siempre existe.
        try:
            import ssl
            cer.write_bytes(ssl.PEM_cert_to_DER_cert(pem.read_text(encoding="utf-8")))
        except Exception as e:
            return {"error": f"No pude preparar el certificado: {e}"}

    import subprocess
    try:
        r = subprocess.run(
            ["certutil.exe", "-user", "-addstore", "Root", str(cer)],
            capture_output=True, text=True, timeout=60,
            encoding=plataforma.codificacion_consola(), errors="replace")
    except Exception as e:
        return {"error": f"No pude ejecutar certutil: {e}"}

    if r.returncode != 0:
        detalle = (r.stderr or r.stdout or "").strip()[:300]
        return {"error": f"Windows no aceptó el certificado: {detalle}"}

    # Firefox no mira el almacén de Windows salvo que se le pida. Este pref va
    # SOLO en el perfil de captura: ponerlo en general haría que ese perfil
    # confiara además en cualquier raíz corporativa del equipo, que es un
    # cambio de comportamiento que nadie pidió.
    try:
        user_js = Path(perfil) / "user.js"
        actual = user_js.read_text(encoding="utf-8") if user_js.exists() else ""
        if "security.enterprise_roots.enabled" not in actual:
            with open(user_js, "a", encoding="utf-8") as f:
                f.write('user_pref("security.enterprise_roots.enabled", true);\n')
    except OSError as e:
        return {"error": f"Agregué el certificado pero no pude configurar el perfil: {e}"}

    _marca_ca_windows().write_text(
        json.dumps({"huella": _huella_ca(), "cuando": time.strftime("%Y-%m-%d %H:%M:%S")},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True,
            "aviso": ("Listo: Windows confía en la CA de la captura para tu usuario, "
                      "y el Firefox del proxy la va a usar. Cerrá y reabrí Firefox "
                      "si estaba abierto. Podés revertirlo cuando quieras.")}


def quitar_ca_de_windows() -> Dict[str, Any]:
    """Deshace confiar_ca_en_windows(). Existe porque una CA de intercepción
    no puede ser una decisión de una sola dirección."""
    if not plataforma.ES_WINDOWS:
        return {"error": "Esto solo aplica en Windows."}

    marca = _marca_ca_windows()
    huella = None
    if marca.exists():
        try:
            huella = json.loads(marca.read_text(encoding="utf-8")).get("huella")
        except Exception:
            pass
    huella = huella or _huella_ca()
    if not huella:
        return {"error": "No sé qué certificado sacar: no encuentro la huella."}

    import subprocess
    try:
        r = subprocess.run(
            ["certutil.exe", "-user", "-delstore", "Root", huella],
            capture_output=True, text=True, timeout=60,
            encoding=plataforma.codificacion_consola(), errors="replace")
    except Exception as e:
        return {"error": f"No pude ejecutar certutil: {e}"}

    if marca.exists():
        try:
            marca.unlink()
        except OSError:
            pass

    if r.returncode != 0:
        detalle = (r.stderr or r.stdout or "").strip()[:300]
        return {"error": f"certutil devolvió un error (quizá ya no estaba): {detalle}"}
    return {"ok": True, "aviso": "Windows ya no confía en la CA de la captura."}


def _perfil_en_uso(perfil: Path) -> bool:
    """¿Hay un Firefox vivo usando NUESTRO perfil? (no el default de la persona).

    Antes esto era `pgrep -fl firefox`, que no existe en Windows y hacía que la
    función devolviera siempre False. psutil ya es dependencia y sirve en los
    tres sistemas."""
    try:
        import psutil
    except ImportError:
        return _perfil_lockeado(perfil)

    objetivo = str(perfil).lower()
    try:
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                nombre = (p.info.get("name") or "").lower()
                if "firefox" not in nombre:
                    continue
                linea = " ".join(p.info.get("cmdline") or []).lower()
                if objetivo in linea:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        return _perfil_lockeado(perfil)
    return False


def _perfil_lockeado(perfil: Path) -> bool:
    """Respaldo de _perfil_en_uso cuando no se pueden leer las líneas de comando.

    Firefox mantiene el lock del perfil abierto en exclusiva mientras corre, así
    que en Windows intentar renombrarlo falla si está en uso. En POSIX el lock es
    un symlink y esta prueba no dice nada, así que ahí se contesta que no."""
    if not plataforma.ES_WINDOWS:
        return False
    lock = Path(perfil) / "parent.lock"
    if not lock.exists():
        return False
    try:
        os.rename(lock, lock)
        return False
    except OSError:
        return True


def _limpiar_locks(perfil: Path):
    """Saca los locks que deja un arranque fallido. Solo se llama cuando ya
    verificamos que ningún Firefox está usando el perfil."""
    # En Windows el archivo se llama 'parent.lock'; en macOS/Linux, '.parentlock'.
    for nombre in (".parentlock", "parent.lock", "lock", ".lock"):
        try:
            (Path(perfil) / nombre).unlink()
        except OSError:
            pass


def lanzar_firefox(puerto: int) -> Dict[str, Any]:
    """Abre Firefox con un perfil aparte, ya configurado para usar el proxy.

    El perfil vive en $DATOS/_proxy/firefox y es descartable: tu Firefox de
    todos los días queda intacto — sin proxy, sin la CA instalada, sin riesgo
    de quedarte sin internet por olvidarte el proxy puesto.

    Abre mitm.it de entrada: ahí se instala el certificado una sola vez (dos
    clics), que es lo que hace falta para que HTTPS no dé error."""
    import subprocess

    firefox = plataforma.ruta_firefox()
    if firefox is None:
        return {"error": plataforma.como_instalar_firefox()}

    perfil = dir_proxy() / "firefox"
    perfil.mkdir(parents=True, exist_ok=True)
    (perfil / "user.js").write_text("\n".join([
        'user_pref("network.proxy.type", 1);',
        f'user_pref("network.proxy.http", "127.0.0.1");',
        f'user_pref("network.proxy.http_port", {puerto});',
        f'user_pref("network.proxy.ssl", "127.0.0.1");',
        f'user_pref("network.proxy.ssl_port", {puerto});',
        'user_pref("network.proxy.share_proxy_settings", true);',
        'user_pref("network.proxy.allow_hijacking_localhost", true);',
        'user_pref("network.proxy.no_proxies_on", "");',
        'user_pref("browser.shell.checkDefaultBrowser", false);',
        'user_pref("datareporting.policy.dataSubmissionEnabled", false);',
        'user_pref("browser.aboutConfig.showWarning", false);',
    ]) + "\n", encoding="utf-8")

    # Instalar la CA si falta. Si ya está (lo normal salvo la primera vez), no
    # se toca nada y Firefox abre directo al sitio, sin pasar por mitm.it.
    if ca_confiada(perfil):
        # Puede estar en el cert9.db (macOS) o en el almacén de Windows.
        cert = {"estado": "ya"}
    else:
        cert = instalar_ca_firefox(perfil)
    manual = cert["estado"] == "manual"
    inicio = "http://mitm.it" if manual else "about:blank"

    if _perfil_en_uso(perfil):
        return {"perfil": str(perfil),
                "aviso": "Firefox del proxy ya está abierto. Buscá su ventana "
                         "(es un perfil aparte del Firefox normal).",
                "estado": cert["estado"], "ya_abierto": True}

    _limpiar_locks(perfil)   # el perfil no está en uso: si hay lock, es viejo

    try:
        if plataforma.ES_MAC:
            # 'open -n -a' es la forma correcta en macOS: usa LaunchServices, se
            # detacha bien (launchd adopta el proceso) y convive con tu Firefox
            # normal. Lanzar el binario interno directo dejaba un zombie y salía al
            # toque cuando ya había otra instancia. subprocess.run espera a que
            # 'open' termine (es instantáneo) y lo cosecha: sin defunct.
            subprocess.run(
                ["open", "-n", "-a", str(firefox), "--args",
                 "-no-remote", "-profile", str(perfil), inicio],
                check=True, timeout=20,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # Fuera de macOS se lanza el binario directo. '-no-remote -profile'
            # es el mecanismo portable que 'open -n' envolvía: sin él, Firefox
            # le pasa la URL a la instancia que ya estuviera abierta y el perfil
            # de captura nunca se usa.
            #
            # Popen y no run: run esperaría a que Firefox se cierre, y esto lo
            # llama el hilo de la interfaz — la app quedaría congelada mientras
            # la persona navega.
            banderas = 0
            if plataforma.ES_WINDOWS:
                banderas = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_GROUP
            subprocess.Popen(
                [str(firefox), "-no-remote", "-profile", str(perfil), inicio],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, close_fds=True, creationflags=banderas)
    except subprocess.CalledProcessError as e:
        return {"error": f"Firefox no pudo abrir (open devolvió {e.returncode})."}
    except Exception as e:
        return {"error": f"No pude abrir Firefox: {e}"}

    if cert["estado"] == "ya":
        aviso = "El certificado ya estaba instalado. Navegá tranquilo: HTTPS se intercepta."
    elif cert["estado"] == "instalada":
        aviso = "Instalé el certificado en el perfil (una sola vez, queda para siempre). Ya podés navegar."
    elif plataforma.ES_WINDOWS:
        aviso = ("Falta el certificado. En la pestaña de mitm.it que se acaba de "
                 "abrir: clic en 'Other', se baja un archivo, y en Firefox andá a "
                 "Configuración → Privacidad y seguridad → Certificados → Ver "
                 "certificados → Importar, elegí ese archivo y marcá «Confiar en "
                 "esta CA para identificar sitios web». Es una sola vez.\n\n"
                 "Alternativa: el botón «Confiar el certificado en Windows» hace "
                 "esto solo, pero la CA pasa a valer para todas las aplicaciones "
                 "de tu usuario, no solo para este Firefox.")
    else:
        aviso = ("No pude instalar el certificado solo (" + cert.get("motivo", "") + ").\n\n"
                 "Abrí mitm.it, elegí 'Other' e instalalo a mano — es una sola vez. "
                 "Si Firefox del proxy estaba abierto, cerralo y reintentá: el "
                 "almacén de certificados no se puede tocar con Firefox abierto.")
    return {"perfil": str(perfil), "aviso": aviso, "estado": cert["estado"]}
