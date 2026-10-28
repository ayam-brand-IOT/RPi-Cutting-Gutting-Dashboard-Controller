#!/usr/bin/env python3
from gpiozero import DigitalInputDevice

PINS = range(2, 28)
dev = {p: DigitalInputDevice(p, pull_up=None, active_state=True) for p in PINS}

def etat():
    return {p for p, d in dev.items() if d.value}

repos = etat()
print(f"Au repos, GPIO déjà HIGH : {sorted(repos)}")
print("Toutes les entrées doivent être à 0 V. Ctrl+C pour quitter.\n")

try:
    while True:
        n = input("Numéro de l'entrée testée (ex. 8) puis maintiens 24 V, puis Entrée : ")
        input("  ...maintiens 24 V sur cette entrée, puis appuie sur Entrée : ")
        actifs = etat() - repos
        print(f"  Entrée {n} -> GPIO HIGH : {sorted(actifs)}\n")
        input("  Relâche l'entrée, puis appuie sur Entrée pour continuer : ")
except (KeyboardInterrupt, EOFError):
    pass