#!/usr/bin/env python3
# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause
"""Live status page for the Versa PCIe bring-up design (LiteX bridge registers over UARTBone).

  python3 scripts/versa_monitor.py --csr-csv build/bringup_l75_s1/csr.csv --uart-port auto
  then open http://localhost:8765   (--uart-port auto = the Versa's FT2232H channel B, found again after a re-enumeration)

The same server renders the project's notes (docs/*.md: runbook, hardware log, design...) under /docs and /doc/<name>.

Starts litex_server on the UART (or use --server-host/--server-port for one already running), polls the debug CSRs
(DCU, receiver detection, LTSSM, counters, DLL) about 5 times a second, drains the LTSSM tracer into a timestamped event
log and serves a small page. The page is read-only except for the buttons (LTSSM enable/disable, Link Retrain), which only
act when pressed; the server listens on localhost only. Standard library only.
"""
import argparse, glob, html, json, pathlib, re, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import markdown as _markdown
except ImportError:
    _markdown = None
from litex import RemoteClient
from litepcie.phy.soft.ltssm import LTSSM_STATES

STATE_NAMES = {code: name for name, code in LTSSM_STATES.items()}
INIT_STATES = ["RESET-ALL", "WAIT-TX-PLL-LOCK", "RESET-TX-PCS", "WAIT-RX-SIGNAL", "RESET-RX-CDR", "WAIT-RX-CDR-LOCK", "RESET-RX-PCS", "READY"]

def find_uart():
    """UART of the Versa's FT2232H (channel B = interface 1): the /dev/ttyUSBn numbers change when the board re-enumerates."""
    for pattern in ["/dev/serial/by-id/*Versa*if01-port0", "/dev/serial/by-id/*if01-port0"]:
        ports = sorted(glob.glob(pattern))
        if ports:
            return ports[0]
    return None

class ServerProcess:
    """litex_server on the UART, restarted when it dies or the board re-enumerated (port 'auto' is searched again)."""
    def __init__(self, port, baudrate):
        self.port, self.baudrate, self.proc = port, baudrate, None
    def uart(self):
        return find_uart() if self.port == "auto" else self.port
    def start(self):
        uart = self.uart()
        if uart is None:
            raise RuntimeError("no Versa UART found (/dev/serial/by-id/*if01-port0)")
        self.proc = subprocess.Popen(["litex_server", "--uart", "--uart-port", uart, "--uart-baudrate", str(self.baudrate)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.5)
    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
            try: self.proc.wait(timeout=3)
            except Exception: self.proc.kill()
            self.proc = None
    def restart(self):
        self.stop()
        self.start()

class Monitor:
    def __init__(self, csr_csv, host, port, poll, server=None):
        self.csr_csv, self.host, self.port, self.poll = csr_csv, host, port, poll
        self.server = server
        self.lock    = threading.Lock()
        self.bus     = None
        self.state   = {"connected": False, "error": "starting"}
        self.events  = []   # (time, text)
        self.history = []   # detect rate bookkeeping
        self.t0      = time.time()
        self.pending = []   # commands from the page
        self.last_ltssm = None
        self.msi_triggers_seen = None  # last trigger count read
        self.msi_watch = None          # (trigger count, TX TLPs at the trigger, polls left)
        self.msi_sent = 0              # triggers followed by a TX TLP
        self.msi_tx_before = 0
        self.msi_missing = 0           # triggers without any TX TLP
        self.dma_last = None           # (time, TX TLPs, RX TLPs) at the last rate sample
        self.dma_rates = None          # (TX, RX) TLPs per second over the last sample

    # Register access ------------------------------------------------------------------------------
    def connect(self):
        self.bus = RemoteClient(csr_csv=self.csr_csv, host=self.host, port=self.port)
        self.bus.open()
        regs = self.bus.regs
        # The soft PHY register group has had three spellings in LitePCIe: pcie_endpoint_phy_*, pcie_phy_phy_* and pcie_phy_*.
        self.gprefix = next((g for g in ("pcie_endpoint_phy_", "pcie_phy_phy_", "pcie_phy_") if hasattr(regs, g + "link_status")), "pcie_phy_")
        self.prefix  = self.gprefix + "debug_"
        self.sprefix = self.gprefix + "serdes_"
        self.tracer  = getattr(regs, self.gprefix + "ltssm_tracer_history", None)
        # Transitions recorded before we connected are stale: drop them.
        dropped = 0
        while self.tracer is not None and (self.tracer.read() >> 31) & 1 and dropped < 4096:
            dropped += 1
        if dropped:
            self.event(f"{dropped} older LTSSM transitions discarded")

    def reg(self, name):
        return getattr(self.bus.regs, name, None)

    def event(self, text):
        self.events.append((time.time() - self.t0, text))
        self.events = self.events[-300:]

    # One polling round -----------------------------------------------------------------------------
    def read_state(self):
        r, p, sp = self.reg, self.prefix, self.sprefix
        s = {"connected": True, "time": time.time() - self.t0}
        s["scratch"] = r("ctrl_scratch").read()
        s["alive"]   = (s["scratch"] == 0x12345678)
        d = r(sp + "dcu_status").read()
        s["dcu"] = dict(tx_lol=d & 1, rx_lol=(d >> 1) & 1, rx_los=(d >> 2) & 1, tx_ready=(d >> 3) & 1, rx_ready=(d >> 4) & 1,
            init_state=INIT_STATES[(d >> 5) & 7])
        s["tx_idle_delay"] = r(sp + "tx_idle_delay").read()
        t = r(sp + "detect_status").read()
        s["detect"] = dict(requests=t & 0xff, done=(t >> 8) & 0xff, timeouts=(t >> 16) & 0xff, connected=(t >> 24) & 1)
        st = r(p + "status").read()
        s["phy"] = dict(ltssm=STATE_NAMES.get(st & 0x3f, hex(st & 0x3f)), l0=(st >> 8) & 1, link_up=(st >> 9) & 1, serdes_ready=(st >> 10) & 1,
            rx_elecidle=(st >> 11) & 1, rx_polarity=(st >> 12) & 1, enabled=(st >> 13) & 1, perst=(st >> 14) & 1, link=(st >> 16) & 0xff, lane=(st >> 24) & 0x1f)
        s["counters"] = {n: r(p + n).read() for n in ["rx_ts", "rx_ts_errors", "rx_errors", "rx_overflows", "rx_dllp", "rx_tlp", "rx_rate"]}
        if r("pcie_msi_enable") is not None:
            host_en = r(self.gprefix + "msi_enable")
            s["msi"] = dict(host_enabled=host_en.read() if host_en else None, enable=r("pcie_msi_enable").read(),
                vector=r("pcie_msi_vector").read(),
                triggers=r("msi_trigger_count").read() if r("msi_trigger_count") is not None else None)
        if r(p + "dll_status") is not None:
            dl = r(p + "dll_status").read()
            s["dll"] = dict(state=dl & 3, fc_ok=(dl >> 3) & 1, replays=r(p + "dll_replays").read(), rx_errors=r(p + "dll_rx_errors").read(),
                link_errors=r(p + "dll_link_errors").read(), tx_tlps=r(p + "dll_tx_tlps").read(), rx_tlps=r(p + "dll_rx_tlps").read())
        if r(self.gprefix + "link_status") is not None:
            s["dl_active"] = r(self.gprefix + "link_status").read() & 1
        if r("pcie_dma0_writer_enable") is not None:
            # DMA (LitePCIe): engines (writer: card to host, reader: host to card), loopback, descriptor tables, and what the host
            # granted (bus mastering, payload/request sizes). The rate is the DLL's TLP counters over about one second.
            def first(*names):
                for n in names:
                    if r(n) is not None:
                        return r(n).read()
                return None
            s["dma"] = dict(writer_enable=first("pcie_dma0_writer_enable"), reader_enable=first("pcie_dma0_reader_enable"),
                loopback=first("pcie_dma0_loopback_enable"), writer_table_level=first("pcie_dma0_writer_table_level"),
                reader_table_level=first("pcie_dma0_reader_table_level"),
                bus_master=first(self.gprefix + "bus_master_enable"),
                max_request_size=first(self.gprefix + "max_request_size"),
                max_payload_size=first(self.gprefix + "max_payload_size"))
            d = s.get("dll")
            if d:
                now = time.time()
                if self.dma_last is None or now - self.dma_last[0] >= 1.0:
                    if self.dma_last is not None:
                        dt = now - self.dma_last[0]
                        self.dma_rates = (((d["tx_tlps"] - self.dma_last[1]) & 0xffffffff) / dt,
                                          ((d["rx_tlps"] - self.dma_last[2]) & 0xffffffff) / dt)
                    self.dma_last = (now, d["tx_tlps"], d["rx_tlps"])
                s["dma"]["tlps_per_s"] = self.dma_rates
        # Drain the LTSSM tracer (each read pops one entry).
        if self.tracer is not None:
            for _ in range(64):
                e = self.tracer.read()
                if not (e >> 31) & 1:
                    break
                new, old = e & 0x3f, (e >> 6) & 0x3f
                self.event(f"LTSSM {STATE_NAMES.get(old, hex(old))} -> {STATE_NAMES.get(new, hex(new))}" + ("  (tracer overflowed: transitions missed)" if (e >> 30) & 1 else ""))
        return s

    def watch_msi(self, s):
        """An MSI is a Memory Write TLP: after each trigger the DLL's TX TLP counter must move."""
        m, d = s.get("msi"), s.get("dll")
        if not m or m.get("triggers") is None or not d:
            return
        tx, n = d["tx_tlps"], m["triggers"]
        if self.msi_triggers_seen is not None and n != self.msi_triggers_seen:
            self.event(f"MSI trigger #{n} (host MSI enabled: {m['host_enabled']}, vector 0 enabled in core: {m['enable'] & 1})")
            self.msi_watch = (n, self.msi_tx_before, 12)
        self.msi_triggers_seen = n
        if self.msi_watch is not None:
            trig, tx0, left = self.msi_watch
            if tx > tx0:
                self.msi_sent += 1
                self.event(f"MSI TLP left the card: TX TLPs {tx0} -> {tx} after trigger #{trig}")
                self.msi_watch = None
            elif left <= 0:
                self.msi_missing += 1
                why = []
                if not m["host_enabled"]: why.append("the host has not enabled MSI (config space)")
                if not (m["enable"] & 1): why.append("vector 0 is not enabled in the core (pcie_msi_enable)")
                self.event(f"trigger #{trig}: NO TX TLP seen" + (": " + "; ".join(why) if why else " (bus mastering off?)"))
                self.msi_watch = None
            else:
                self.msi_watch = (trig, tx0, left - 1)
        self.msi_tx_before = tx
        m["sent_seen"], m["missing"] = self.msi_sent, self.msi_missing

    def hints(self, s):
        h = []
        if not s.get("alive"):
            return [("bad", "No valid answer from the SoC (ctrl_scratch): UARTBone/clock/bitstream problem.")]
        d, t, p, c = s["dcu"], s["detect"], s["phy"], s["counters"]
        if d["tx_lol"]:
            h.append(("bad", "DCU TX PLL not locked: no reference clock, or the bitstream's refclk frequency does not match the board (bench build: 156.25 MHz on-board oscillator, host build: 100 MHz from the slot)."))
        elif not d["tx_ready"]:
            h.append(("warn", f"DCU TX not ready, init FSM in {d['init_state']}."))
        else:
            h.append(("ok", "DCU TX PLL locked on the reference clock, transmitter initialized."))
        if not p["enabled"]:
            h.append(("info", "LTSSM disabled (by design at reset). Press Enable when the host is up."))
        else:
            if t["timeouts"] and not t["done"]:
                h.append(("bad", "Receiver detection never completes in the DCU (timeouts only): PCIE_MODE/detect ports not working."))
            if t["connected"] and p["rx_elecidle"]:
                h.append(("warn", "DCU reports a receiver at the far end but the host is not transmitting (RX idle): host powered and root port enabled? PRSNT jumper J4 set? Root port may sit in Polling.Compliance."))
            if not t["connected"] and t["requests"]:
                h.append(("warn", "DCU reports no receiver at the far end (host RX termination not seen)."))
            if p["ltssm"].startswith("Polling.Active") and not p["rx_elecidle"] and c["rx_ts"] == 0:
                h.append(("warn", "RX signal present but no valid TS1 received: polarity, loss-of-signal level, analog/CDR settings, refclk mismatch."))
            if c["rx_ts_errors"]:
                h.append(("warn", f"{c['rx_ts_errors']} corrupted Training Sequences received."))
            if p["l0"]:
                h.append(("ok", "L0 reached: link trained."))
            if s.get("dl_active"):
                h.append(("ok", "Data Link Layer active (flow control initialized): the host should enumerate the card."))
        if not p["rx_elecidle"] and c["rx_overflows"]:
            h.append(("warn", "Elastic buffer overflows with a receive signal present: clock tolerance/SKP issue (counters also count noise while the receiver is idle, so read them with a live signal only)."))
        return h

    # Polling thread ----------------------------------------------------------------------------------
    def run(self):
        while True:
            try:
                if self.bus is None:
                    self.connect()
                    self.event("connected to litex_server")
                for cmd in self.take_commands():
                    self.exec_command(cmd)
                s = self.read_state()
                self.watch_msi(s)
                s["hints"] = self.hints(s)
                s["events"] = self.events[-80:]
                with self.lock:
                    self.state = s
                ltssm = s["phy"]["ltssm"]
                if self.last_ltssm is not None and ltssm != self.last_ltssm and self.tracer is None:
                    self.event(f"LTSSM {self.last_ltssm} -> {ltssm}")
                self.last_ltssm = ltssm
            except Exception as ex:
                with self.lock:
                    self.state = {"connected": False, "error": f"{type(ex).__name__}: {ex}", "events": self.events[-80:]}
                try:
                    if self.bus: self.bus.close()
                except Exception:
                    pass
                self.bus = None
                time.sleep(1)
                if self.server is not None:
                    try:
                        self.server.restart()
                        self.event("litex_server restarted on " + str(self.server.uart()))
                    except Exception as ex2:
                        self.event(f"litex_server restart failed: {ex2}")
                        time.sleep(2)
            time.sleep(self.poll)

    def take_commands(self):
        with self.lock:
            c, self.pending = self.pending, []
        return c

    def exec_command(self, cmd):
        control = self.reg(self.prefix + "control")
        if cmd == "enable":
            control.write(1);  self.event("button: LTSSM enable")
        elif cmd == "disable":
            control.write(0);  self.event("button: LTSSM disable")
        elif cmd == "retrain":
            control.write(control.read() | 2); self.event("button: Link Retrain")
        elif cmd == "msi_enable":
            self.reg("pcie_msi_enable").write(1); self.event("button: MSI vector 0 enabled in the core (pcie_msi_enable = 1)")
        elif cmd == "msi_send":
            if self.reg("msi_trigger_trigger") is None:
                self.event("MSI trigger: this bitstream has no msi_trigger CSR")
            else:
                self.reg("msi_trigger_trigger").write(1); self.event("button: MSI vector 0 triggered")
        elif cmd == "msi_clear":
            self.reg("pcie_msi_clear").write(1); self.event("button: MSI vector 0 cleared")
        elif cmd.startswith("tx_idle_delay="):
            v = int(cmd.split("=")[1]); self.reg(self.sprefix + "tx_idle_delay").write(v); self.event(f"button: tx_idle_delay = {v}")

    def post(self, cmd):
        with self.lock:
            self.pending.append(cmd)


# Documentation pages (docs/*.md rendered on the fly) ---------------------------------------------------------
DOCS_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs"
DOC_ORDER = [
    ("runbook",                 "Runbook"),
    ("hardware-log",            "Hardware log"),
    ("serdes-adapter-design",   "SERDES adapter"),
    ("timing-check",            "Timing"),
    ("toolchain-notes",         "Toolchain"),
    ("prior-art",               "Prior art"),
]
DOC_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--mut:#667085;--line:#e3e6ec;--link:#2b5fb4;--code:#eef0f4}
@media (prefers-color-scheme:dark){:root{--bg:#12151c;--card:#1b2030;--fg:#e6e9f0;--mut:#98a2b3;--line:#2b3347;--link:#8db4ff;--code:#252b3d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,sans-serif}
a{color:var(--link)}header{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:2}
header b{margin-right:6px}header a{text-decoration:none;padding:3px 9px;border-radius:7px}header a.cur{background:var(--code);font-weight:600}
.wrap{display:flex;gap:22px;max-width:1280px;margin:0 auto;padding:16px}.toc{flex:0 0 250px;position:sticky;top:64px;align-self:flex-start;max-height:calc(100vh - 80px);overflow:auto;font-size:13px;border-right:1px solid var(--line);padding-right:12px}
.toc ul{list-style:none;padding-left:12px;margin:2px 0}.toc>div>ul{padding-left:0}.toc a{text-decoration:none;color:var(--mut)}.toc a:hover{color:var(--link)}
main{flex:1;min-width:0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 22px 22px}
h1{font-size:24px}h2{font-size:19px;margin-top:28px;border-bottom:1px solid var(--line);padding-bottom:4px}h3{font-size:16px}
table{border-collapse:collapse;display:block;overflow-x:auto;margin:10px 0}th,td{border:1px solid var(--line);padding:5px 9px;vertical-align:top;text-align:left}th{background:var(--code)}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:90%}pre{background:var(--code);padding:10px 12px;border-radius:8px;overflow-x:auto}pre code{background:none;padding:0}
@media (max-width:860px){.toc{display:none}.wrap{padding:8px}main{padding:4px 14px 16px}}
"""

def doc_names():
    avail = {p.stem for p in DOCS_DIR.glob("*.md")}
    ordered = [(n, t) for n, t in DOC_ORDER if n in avail]
    others  = [(n, n.replace("-", " ")) for n in sorted(avail) if n not in dict(DOC_ORDER)]
    return ordered + others

def doc_shell(title, body, toc="", current=None):
    nav = '<a href="/">&larr; Monitor</a>' + "".join(
        f'<a class="{"cur" if n == current else ""}" href="/doc/{n}">{html.escape(t)}</a>' for n, t in doc_names())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} - Versa notes</title><style>{DOC_CSS}</style></head><body>
<header><b>Versa notes</b>{nav}</header><div class="wrap"><div class="toc">{toc}</div><main>{body}</main></div></body></html>""".encode()

def render_doc(name):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    path = DOCS_DIR / (name + ".md")
    if not path.is_file():
        return None
    text = path.read_text(errors="replace")
    if _markdown is None:
        return doc_shell(name, "<pre>" + html.escape(text) + "</pre><p>(install the python package <code>markdown</code> for formatting)</p>", current=name)
    md   = _markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"], extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(text)
    body = re.sub(r'href="([A-Za-z0-9_.-]+)\.md(#[^"]*)?"', lambda m: f'href="/doc/{m.group(1)}{m.group(2) or ""}"', body)
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return doc_shell(m.group(1) if m else name, body, toc=md.toc, current=name)

def render_docs_index():
    items = "".join(f'<li><a href="/doc/{n}">{html.escape(t)}</a> <span style="color:var(--mut)">({n}.md)</span></li>' for n, t in doc_names())
    return doc_shell("Notes", f"<h1>Notes</h1><ul>{items}</ul>")

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Versa PCIe Monitor</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--mut:#667085;--line:#e3e6ec;--ok:#16794c;--okbg:#e3f5ea;--warn:#8a5a00;--warnbg:#fff1d6;--bad:#b3261e;--badbg:#fde4e2;--info:#2b5fb4;--infobg:#e4edfb;--on:#1b9e5a;--off:#c4c9d4}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#12151c;--card:#1b2030;--fg:#e6e9f0;--mut:#98a2b3;--line:#2b3347;--ok:#6ad49b;--okbg:#17382a;--warn:#f0c060;--warnbg:#3d3115;--bad:#ff8f87;--badbg:#42201e;--info:#8db4ff;--infobg:#1b2c4d;--on:#43d17f;--off:#3a4256}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--card)}
h1{font-size:16px;margin:0}nav a{color:var(--info);text-decoration:none;margin:0 4px;font-weight:600}nav a:hover{text-decoration:underline}main{padding:16px;display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));max-width:1300px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:0 0 8px}
.wide{grid-column:1/-1}.row{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px dashed var(--line)}.row:last-child{border:0}.row span:first-child{color:var(--mut)}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-weight:600;font-size:12px}.ok{background:var(--okbg);color:var(--ok)}.warn{background:var(--warnbg);color:var(--warn)}.bad{background:var(--badbg);color:var(--bad)}.info{background:var(--infobg);color:var(--info)}.off{background:var(--line);color:var(--mut)}
.state{font-size:26px;font-weight:700}.path{display:flex;gap:6px;margin:10px 0 4px;flex-wrap:wrap}.path div{flex:1;min-width:90px;text-align:center;padding:6px 4px;border-radius:8px;border:1px solid var(--line);color:var(--mut);font-size:12px}.path .cur{background:var(--infobg);color:var(--info);border-color:var(--info);font-weight:700}.path .done{background:var(--okbg);color:var(--ok)}
.leds{display:flex;gap:8px;flex-wrap:wrap}.led{width:74px;text-align:center;font-size:11px;color:var(--mut)}.led i{display:block;width:16px;height:16px;border-radius:50%;margin:0 auto 3px;background:var(--off)}.led.on i{background:var(--on);box-shadow:0 0 8px var(--on)}
.hint{padding:7px 10px;border-radius:8px;margin:5px 0}button{font:inherit;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}button:hover{border-color:var(--info)}
#events{max-height:300px;overflow:auto;font:12px ui-monospace,monospace}#events div{padding:2px 0;border-bottom:1px dashed var(--line)}.t{color:var(--mut);margin-right:8px}
</style></head><body>
<header><h1>Versa PCIe bring-up monitor</h1><div id="conn"></div><nav><a href="/doc/bringup-runbook">Runbook</a> <a href="/doc/hardware-log">Hardware log</a> <a href="/doc/design">Design</a> <a href="/docs">All notes</a></nav><div>
<button onclick="cmd('enable')">LTSSM enable</button> <button onclick="cmd('disable')">disable</button> <button onclick="cmd('retrain')">Link retrain</button></div></header>
<main id="main"><div class="card wide">Waiting for data…</div></main>
<script>
const PATH=["Detect","Polling","Configuration","L0"];
function stage(s){if(!s)return -1;if(s.startsWith("Detect"))return 0;if(s.startsWith("Polling"))return 1;if(s.startsWith("Config"))return 2;if(s=="L0"||s.startsWith("Recovery")||s.startsWith("L0"))return 3;return -1}
function pill(v,good,labels){const t=labels?labels[v?1:0]:(v?"yes":"no");return `<span class="pill ${v==good?'ok':'warn'}">${t}</span>`}
function row(a,b){return `<div class="row"><span>${a}</span><span>${b}</span></div>`}
async function cmd(c){if(c=="enable"&&!confirm("Enable the LTSSM? The card starts link training with the host."))return;if(c=="retrain"&&!confirm("Request a Link Retrain from the card?"))return;
await fetch("/api/cmd",{method:"POST",body:JSON.stringify({cmd:c})})}
function led(n,name,on){return `<div class="led ${on?'on':''}"><i></i>LED${n}<br>${name}</div>`}
async function tick(){
 let s;try{s=await (await fetch("/api/state")).json()}catch(e){document.getElementById("conn").innerHTML='<span class="pill bad">monitor server not reachable</span>';return}
 const c=document.getElementById("conn");
 if(!s.connected){c.innerHTML=`<span class="pill bad">no link to the board: ${s.error||""}</span>`;}else{c.innerHTML=`<span class="pill ok">connected</span> <span class="pill ${s.alive?'ok':'bad'}">SoC ${s.alive?'alive':'no answer'}</span> <span style="color:var(--mut)">${s.time.toFixed(0)} s</span>`}
 if(!s.connected){return}
 const p=s.phy,d=s.dcu,t=s.detect,k=s.counters,st=stage(p.ltssm);
 const path=PATH.map((n,i)=>`<div class="${i==st?'cur':(i<st?'done':'')}">${n}</div>`).join("");
 const leds=[led(0,"TX ready",d.tx_ready&&!d.tx_lol),led(1,"RX locked",d.rx_ready),led(2,"PERST#",p.perst),led(3,"detected",t.connected),led(4,"RX idle",p.rx_elecidle),led(5,"link up",p.link_up),led(6,"L0",p.l0),led(7,"heartbeat",1)].join("");
 const hints=(s.hints||[]).map(h=>`<div class="hint ${h[0]}">${h[1]}</div>`).join("");
 const ev=(s.events||[]).slice().reverse().map(e=>`<div><span class="t">${e[0].toFixed(1)} s</span>${e[1]}</div>`).join("");
 document.getElementById("main").innerHTML=`
 <div class="card wide"><h2>Link</h2><div class="state">${p.ltssm}</div><div class="path">${path}</div>
  ${row("LTSSM",p.enabled?'<span class="pill info">enabled</span>':'<span class="pill off">disabled</span>')}${row("Link / lane",p.link+" / "+p.lane)}${row("Link up (configuration done)",pill(p.link_up,1))}${row("L0",pill(p.l0,1))}${row("Data Link Layer active",pill(s.dl_active||0,1))}</div>
 <div class="card"><h2>DCU (transceiver)</h2>${row("TX PLL locked on refclk",pill(!d.tx_lol,1))}${row("TX ready",pill(d.tx_ready,1))}${row("RX loss of signal",pill(d.rx_los,0,["no","yes"]))}${row("RX CDR lock lost",pill(d.rx_lol,0,["no","yes"]))}${row("RX ready",pill(d.rx_ready,1))}${row("Init FSM",d.init_state)}${row("PERST# asserted",pill(p.perst,0,["no","yes"]))}${row("RX electrical idle",pill(p.rx_elecidle,0,["no","yes"]))}${row("RX polarity inverted",p.rx_polarity?'yes':'no')}</div>
 <div class="card"><h2>Receiver detection (our TX side)</h2>${row("Requests",t.requests)}${row("Completed by DCU",t.done)}${row("Timeouts",pill(t.timeouts==0,1,["yes: "+t.timeouts,"none"]))}${row("Last result",t.connected?'<span class="pill info">receiver present</span>':'<span class="pill off">no receiver</span>')}${row("tx_idle_delay (lane cycles)",s.tx_idle_delay)}</div>
 <div class="card"><h2>Receive counters</h2>${row("Training Sequences",k.rx_ts)}${row("Corrupted TS",k.rx_ts_errors)}${row("8b/10b errors",k.rx_errors)}${row("Elastic buffer overflows",k.rx_overflows)}${row("DLLPs (L0)",k.rx_dllp)}${row("TLPs (L0)",k.rx_tlp)}${row("Partner rate id",k.rx_rate)}${s.dll?row("DLL replays / rx errors",s.dll.replays+" / "+s.dll.rx_errors)+row("DLL TLPs tx / rx",s.dll.tx_tlps+" / "+s.dll.rx_tlps):""}</div>
 ${s.msi?`<div class="card"><h2>MSI</h2>${row("Host enabled MSI (config space)",s.msi.host_enabled==null?'n/a':pill(s.msi.host_enabled,1))}${row("Vector 0 enabled in the core",pill(s.msi.enable&1,1))}${row("Vector pending (not cleared)",pill(s.msi.vector&1,0,["no","yes"]))}${row("Triggers written",s.msi.triggers==null?'n/a':s.msi.triggers)}${row("MSI TLPs seen leaving the card",'<span class="pill '+(s.msi.sent_seen?'ok':'off')+'">'+(s.msi.sent_seen||0)+'</span>')}${row("Triggers without a TX TLP",'<span class="pill '+(s.msi.missing?'bad':'off')+'">'+(s.msi.missing||0)+'</span>')}<div style="margin-top:8px"><button onclick="cmd('msi_enable')">Enable vector 0</button> <button onclick="cmd('msi_send')">Send MSI</button> <button onclick="cmd('msi_clear')">Clear</button></div></div>`:""}
 ${s.dma?`<div class="card"><h2>DMA</h2>${row("Host bus mastering",pill(s.dma.bus_master,1))}${row("Max payload / request",s.dma.max_payload_size+" / "+s.dma.max_request_size+" B")}${row("Writer enabled (card to host)",pill(s.dma.writer_enable&1,1,["no","yes"])+" "+(s.dma.writer_enable&2?"idle":"busy"))}${row("Reader enabled (host to card)",pill(s.dma.reader_enable&1,1,["no","yes"])+" "+(s.dma.reader_enable&2?"idle":"busy"))}${row("Loopback",pill(s.dma.loopback&1,1,["off","on"]))}${row("Descriptors queued (writer / reader)",s.dma.writer_table_level+" / "+s.dma.reader_table_level)}${row("TLPs per second (TX / RX)",s.dma.tlps_per_s?s.dma.tlps_per_s.map(v=>Math.round(v).toLocaleString()).join(" / "):"…")}</div>`:''}
 <div class="card"><h2>What the Versa's LEDs should show</h2><div class="leds">${leds}</div><div style="color:var(--mut);margin-top:8px">LEDs are active low on the board: lit = condition true.</div></div>
 <div class="card wide"><h2>Reading</h2>${hints}</div>
 <div class="card wide"><h2>Event log (LTSSM transitions, buttons)</h2><div id="events">${ev}</div></div>`;
}
setInterval(tick,500);tick();
</script></body></html>"""

def make_handler(mon):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, ctype, body):
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path == "/api/state":
                with mon.lock: s = mon.state
                self._send(200, "application/json", json.dumps(s).encode())
            elif self.path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif self.path in ("/docs", "/docs/"):
                self._send(200, "text/html; charset=utf-8", render_docs_index())
            elif self.path.startswith("/doc/"):
                page = render_doc(self.path[5:].split("#")[0].split("?")[0])
                if page is None:
                    self._send(404, "text/plain", b"no such note")
                else:
                    self._send(200, "text/html; charset=utf-8", page)
            else:
                self._send(404, "text/plain", b"not found")
        def do_POST(self):
            if self.path == "/api/cmd":
                n = int(self.headers.get("Content-Length", 0)); cmd = json.loads(self.rfile.read(n))["cmd"]
                if cmd in ("enable", "disable", "retrain", "msi_enable", "msi_send", "msi_clear") or cmd.startswith("tx_idle_delay="):
                    mon.post(cmd)
                self._send(200, "application/json", b"{}")
            else:
                self._send(404, "text/plain", b"not found")
    return H

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csr-csv", required=True)
    ap.add_argument("--uart-port", help="start litex_server on this serial port (UARTBone), or 'auto' for the Versa's FT2232H channel B (searched again after a re-enumeration)")
    ap.add_argument("--uart-baudrate", default=115200, type=int)
    ap.add_argument("--server-host", default="localhost")
    ap.add_argument("--server-port", default=1234, type=int)
    ap.add_argument("--http-port", default=8765, type=int)
    ap.add_argument("--poll", default=0.1, type=float, help="pause between polling rounds (s)")
    ap.add_argument("--once", action="store_true", help="print one JSON snapshot and exit")
    args = ap.parse_args()

    server = None
    if args.uart_port:
        server = ServerProcess(args.uart_port, args.uart_baudrate)
        server.start()
    mon = Monitor(args.csr_csv, args.server_host, args.server_port, args.poll, server=server)
    try:
        if args.once:
            mon.connect(); s = mon.read_state(); s["hints"] = mon.hints(s); s["events"] = mon.events
            print(json.dumps(s, indent=1)); return
        threading.Thread(target=mon.run, daemon=True).start()
        httpd = ThreadingHTTPServer(("127.0.0.1", args.http_port), make_handler(mon))
        print(f"Versa monitor on http://localhost:{args.http_port}  (Ctrl-C to stop)", flush=True)
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server: server.stop()

if __name__ == "__main__":
    main()
