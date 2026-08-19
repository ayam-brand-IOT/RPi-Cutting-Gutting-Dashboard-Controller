"""
simulator.py — Simulateur Modbus RTU pour tester le contrôleur sans matériel réel.

Simule tous les esclaves activés dans config.yaml (section devices:) sur un
seul bus RS485 virtuel, avec une interface graphique par appareil.

Pour tester en parallèle avec main.py, utilise un port série virtuel :

    sudo apt install socat
    socat -d -d pty,raw,echo=0 pty,raw,echo=0

Exemple : /dev/pts/3 dans CE simulateur, /dev/pts/4 comme modbus_port dans
config.yaml pour main.py.

Nécessite : pip install pymodbus (déjà dans requirements.txt)
"""

import asyncio
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusSerialServer

import sys
from pathlib import Path

# Compatibilité pymodbus selon version
try:
    from pymodbus.datastore import ModbusDeviceContext as _DeviceCtx
except ImportError:
    from pymodbus.datastore import ModbusSlaveContext as _DeviceCtx

from config_loader import load_config

# Appareils câblés en réel : exclus du simulateur (ni slave, ni onglet).
# main.py doit être branché sur le vrai port RS485 pour les atteindre.
# Exemple : REAL_DEVICES = {"gutting_left", "vision_left"}
REAL_DEVICES: set[str] = {""}

BLOCK_SIZE = 200

FX_COIL    = 1
FX_HOLDING = 3
FX_INPUT   = 4


def _addr(spec) -> int:
    """Adresse depuis un int direct ou un dict {address: …}."""
    return spec if isinstance(spec, int) else int(spec["address"])


def _min(spec) -> int:
    return int(spec.get("min", 0)) if isinstance(spec, dict) else 0


def _max(spec) -> int:
    return int(spec.get("max", 65535)) if isinstance(spec, dict) else 65535


def _make_slave_context():
    return _DeviceCtx(
        di=ModbusSequentialDataBlock(0, [0] * BLOCK_SIZE),
        co=ModbusSequentialDataBlock(0, [0] * BLOCK_SIZE),
        hr=ModbusSequentialDataBlock(0, [0] * BLOCK_SIZE),
        ir=ModbusSequentialDataBlock(0, [0] * BLOCK_SIZE),
    )
    
def get_config_path(filename: str = "config.yaml") -> str:
    if getattr(sys, "frozen", False):
        # Exécutable PyInstaller : dossier où se trouve le .exe
        base_dir = Path(sys.executable).resolve().parent
    else:
        # Exécution normale en script Python
        base_dir = Path(__file__).resolve().parent
    return str(base_dir / filename)


class ModbusSimulator:
    """Serveur Modbus RTU tournant dans un thread dédié."""

    def __init__(self, devices: dict):
        self._contexts = {
            int(dev["slave"]): _make_slave_context()
            for dev in devices.values()  # REAL_DEVICES already filtered out before
        }
        try:
            self.context = ModbusServerContext(devices=dict(self._contexts), single=False)
        except TypeError:
            self.context = ModbusServerContext(slaves=dict(self._contexts), single=False)
        self._thread = None
        self._lock = threading.Lock()  # protège l'accès concurrent GUI ↔ serveur
        self.last_error: str = ""

    def set_slave_active(self, slave: int, active: bool):
        """Branche/débranche un slave du bus (simule un appareil absent)."""
        try:
            if active:
                self.context[slave] = self._contexts[slave]
            else:
                del self.context[slave]
        except Exception as exc:
            print(f"[simulator] set_slave_active ERROR slave {slave}: {exc}")
        print(f"[simulator] slave {slave} {'ON' if active else 'OFF (muet)'}")

    def start(self, port: str, baudrate: int, parity: str, stopbits: int, bytesize: int):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            args=(port, baudrate, parity, stopbits, bytesize),
            daemon=True,
        )
        self._thread.start()

    async def _async_run(self, port, baudrate, parity, stopbits, bytesize):
        server = ModbusSerialServer(
            context=self.context,
            port=port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            bytesize=bytesize,
            ignore_missing_devices=True,  # ne répond pas aux slaves inconnus (vrais devices)
        )
        print(f"[simulator] serveur RTU sur {port} @ {baudrate} {parity}{bytesize}{stopbits}")
        await server.serve_forever()

    def _run(self, port, baudrate, parity, stopbits, bytesize):
        import traceback, time
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:  # redémarre automatiquement après une erreur (framing, bruit bus...)
            try:
                self.last_error = ""
                loop.run_until_complete(
                    self._async_run(port, baudrate, parity, stopbits, bytesize)
                )
            except Exception as exc:
                self.last_error = str(exc)
                print(f"[simulator] ERREUR — redémarrage dans 2s : {exc}")
                traceback.print_exc()
                time.sleep(2)

    # --- Accès datastore (toujours via _contexts, indépendant de l'état actif/muet) ---

    def set_input(self, slave: int, offset: int, value: int):
        with self._lock:
            self._contexts[slave].setValues(FX_INPUT, offset, [int(value)])

    def get_input(self, slave: int, offset: int) -> int:
        with self._lock:
            return self._contexts[slave].getValues(FX_INPUT, offset, count=1)[0]

    def get_holding(self, slave: int, offset: int) -> int:
        with self._lock:
            return self._contexts[slave].getValues(FX_HOLDING, offset, count=1)[0]

    def get_coil(self, slave: int, offset: int) -> bool:
        with self._lock:
            return bool(self._contexts[slave].getValues(FX_COIL, offset, count=1)[0])


def build_gui():
    cfg = load_config(get_config_path("config.yaml"))
    mcfg = cfg["machine"]

    # Seuls les devices activés et non-réels sont simulés
    devices = {
        name: dev
        for name, dev in cfg["devices"].items()
        if dev.get("enabled", True) and name not in REAL_DEVICES
    }

    root = tk.Tk()
    root.title("Simulateur Modbus RTU — Cutting-Gutting")

    sim = ModbusSimulator(devices)
    refreshers_by_tab: dict[tk.Widget, list] = {}

    # --- Barre de connexion ---
    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")

    ttk.Label(top, text="Port série :").grid(row=0, column=0, sticky="w")
    port_var = tk.StringVar(value=mcfg.get("modbus_port", "/dev/serial0"))
    ttk.Entry(top, textvariable=port_var, width=15).grid(row=0, column=1, padx=4)

    ttk.Label(top, text="Baud :").grid(row=0, column=2, sticky="w")
    baud_var = tk.StringVar(value=str(mcfg.get("modbus_baud", 9600)))
    ttk.Entry(top, textvariable=baud_var, width=8).grid(row=0, column=3, padx=4)

    ttk.Label(top, text="Parité :").grid(row=0, column=4, sticky="w")
    parity_var = tk.StringVar(value=str(mcfg.get("modbus_parity", "N")))
    ttk.Entry(top, textvariable=parity_var, width=3).grid(row=0, column=5, padx=4)

    status_var = tk.StringVar(value="Arrêté")
    status_label = ttk.Label(top, textvariable=status_var, foreground="red")

    def on_start():
        try:
            sim.start(
                port_var.get(),
                int(baud_var.get()),
                parity_var.get(),
                int(mcfg.get("modbus_stopbits", 1)),
                8,
            )
            status_var.set("En cours sur " + port_var.get())
            status_label.configure(foreground="green")
        except Exception as exc:
            messagebox.showerror("Erreur de démarrage", str(exc))

    ttk.Button(top, text="Démarrer serveur", command=on_start).grid(row=0, column=6, padx=8)
    status_label.grid(row=0, column=7, padx=12)

    # --- Onglets par appareil ---
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    def add_section_header(parent, row, text) -> int:
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=(8, 2)
        )
        ttk.Label(parent, text=text, font=("", 9, "bold")).grid(
            row=row + 1, column=0, columnspan=3, sticky="w", padx=4
        )
        return row + 2

    def add_int_field(parent, row, label, get_fn, set_fn=None,
                      minv=0, maxv=65535, readonly=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        var = tk.IntVar(value=get_fn())
        if readonly:
            ttk.Label(parent, textvariable=var, width=12,
                      relief="sunken", anchor="w").grid(row=row, column=1, padx=4, sticky="w")
            def refresh(v=var, g=get_fn):
                v.set(g())
        else:
            spin = ttk.Spinbox(parent, from_=minv, to=maxv, textvariable=var, width=10)
            spin.grid(row=row, column=1, padx=4)
            ttk.Button(parent, text="Appliquer",
                       command=lambda: set_fn(var.get())).grid(row=row, column=2, padx=4)
            def refresh(v=var, g=get_fn, s=spin):
                if root.focus_get() is not s:
                    v.set(g())
        refreshers_by_tab.setdefault(parent, []).append(refresh)

    def add_bool_field(parent, row, label, get_fn, readonly=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        var = tk.BooleanVar(value=bool(get_fn()))
        cb = ttk.Checkbutton(parent, variable=var, state="disabled" if readonly else "normal")
        cb.grid(row=row, column=1, sticky="w", padx=4)
        refreshers_by_tab.setdefault(parent, []).append(lambda v=var, g=get_fn: v.set(bool(g())))

    for name, dev in devices.items():
        slave = int(dev["slave"])
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text=dev.get("label", name))
        row = 0

        # Checkbox pour déconnecter l'esclave du bus sans fermer la fenêtre
        active_var = tk.BooleanVar(value=True)
        def _toggle(v=active_var, s=slave):
            sim.set_slave_active(s, v.get())
        ttk.Checkbutton(tab, text="Actif sur le bus", variable=active_var,
                        command=_toggle).grid(row=row, column=0, columnspan=3,
                                             sticky="w", padx=4, pady=(0, 4))
        row += 1

        # Input registers : simulateur les injecte, le maître les lit
        ir = dev.get("input_registers", {})
        if ir:
            row = add_section_header(tab, row, "Input Registers  (simulateur → maître)")
            for reg_name, spec in ir.items():
                # Ignore les registres composites lo/hi internes (uptime_lo/hi, ejector_count_lo/hi)
                if isinstance(spec, dict) and spec.get("data_type", "uint16") != "uint16":
                    continue
                offset = _addr(spec)
                add_int_field(
                    tab, row, reg_name,
                    get_fn=lambda a=slave, o=offset: sim.get_input(a, o),
                    set_fn=lambda v, a=slave, o=offset: sim.set_input(a, o, v),
                    minv=0, maxv=65535,
                )
                row += 1

        # Holding registers : le maître les écrit → affiché en lecture seule
        hr = dev.get("holding_registers", {})
        if hr:
            row = add_section_header(tab, row, "Holding Registers  (maître → slave, lecture seule)")
            for reg_name, spec in hr.items():
                offset = _addr(spec)
                add_int_field(
                    tab, row, reg_name,
                    get_fn=lambda a=slave, o=offset: sim.get_holding(a, o),
                    readonly=True,
                )
                row += 1

        # Coils : le maître les écrit → affiché en lecture seule
        coils = dev.get("coils", {})
        if coils:
            row = add_section_header(tab, row, "Coils  (maître → slave, lecture seule)")
            for coil_name, spec in coils.items():
                offset = _addr(spec)
                add_bool_field(
                    tab, row, coil_name,
                    get_fn=lambda a=slave, o=offset: sim.get_coil(a, o),
                    readonly=True,
                )
                row += 1

    # --- Rafraîchissement : seulement l'onglet visible, pour ne pas bloquer le RTU ---
    def periodic_refresh():
        # Surveille l'état du thread serveur et met à jour le bandeau
        if sim._thread and not sim._thread.is_alive():
            status_var.set("CRASH — redémarrage...")
            status_label.configure(foreground="orange")
        elif sim.last_error:
            status_var.set(f"Erreur : {sim.last_error[:60]}")
            status_label.configure(foreground="orange")

        try:
            tid = notebook.select()
            current_tab = notebook.nametowidget(tid) if tid else None
        except Exception:
            current_tab = None
        for fn in refreshers_by_tab.get(current_tab, []):
            try:
                fn()
            except Exception:
                pass
        root.after(1000, periodic_refresh)

    root.after(1000, periodic_refresh)
    root.mainloop()


if __name__ == "__main__":
    build_gui()
